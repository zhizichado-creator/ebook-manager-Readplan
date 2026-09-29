"""Extract ebook covers into a small, reusable local image cache."""
from __future__ import annotations

import hashlib
import logging
import posixpath
import struct
import zipfile
from pathlib import Path
from urllib.parse import unquote
from xml.etree import ElementTree

from PySide6.QtCore import QSize
from PySide6.QtGui import QImage
from PySide6.QtPdf import QPdfDocument

logger = logging.getLogger(__name__)
MAX_COVER_BYTES = 24 * 1024 * 1024


def extract_cover(source: str | Path, cache_dir: str | Path) -> str | None:
    """Return a cached PNG path, or ``None`` when the file has no readable cover.

    Cache filenames include the canonical source path and file stat, so a book is
    only decoded again after the source changes or the cache is cleared.
    """
    path = Path(source)
    try:
        stat = path.stat()
        suffix = path.suffix.casefold()
        if suffix not in {".pdf", ".epub", ".mobi", ".azw", ".azw3"}:
            return None
        cache = Path(cache_dir)
        key = f"{path.resolve()}\0{stat.st_size}\0{stat.st_mtime_ns}"
        destination = cache / f"{hashlib.sha256(key.encode('utf-8')).hexdigest()}.png"
        if destination.is_file() and destination.stat().st_size:
            return str(destination)
        if destination.with_suffix(".none").exists():
            return None

        if suffix == ".pdf":
            image = _pdf_cover(path)
        elif suffix == ".epub":
            image = _image_from_bytes(_epub_cover(path))
        elif suffix in {".mobi", ".azw", ".azw3"}:
            image = _image_from_bytes(_palm_cover(path))
        else:
            return None
        if image is None or image.isNull():
            cache.mkdir(parents=True, exist_ok=True)
            destination.with_suffix(".none").touch()
            return None

        cache.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(".tmp.png")
        if not image.save(str(temporary), "PNG"):
            return None
        temporary.replace(destination)
        return str(destination)
    except (OSError, RuntimeError, ValueError, zipfile.BadZipFile, ElementTree.ParseError) as error:
        logger.debug("无法提取封面 %s：%s", path, error)
        return None


def _pdf_cover(path: Path) -> QImage | None:
    document = QPdfDocument()
    try:
        if document.load(str(path)) != QPdfDocument.Error.None_ or document.pageCount() < 1:
            return None
        page_size = document.pagePointSize(0)
        if page_size.width() <= 0 or page_size.height() <= 0:
            target = QSize(600, 900)
        else:
            scale = min(900 / page_size.height(), 600 / page_size.width())
            target = QSize(max(1, round(page_size.width() * scale)),
                           max(1, round(page_size.height() * scale)))
        image = document.render(0, target)
        return image if not image.isNull() else None
    finally:
        document.close()


def _epub_cover(path: Path) -> bytes | None:
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        package_path = ""
        if "META-INF/container.xml" in names:
            container = ElementTree.fromstring(archive.read("META-INF/container.xml"))
            rootfile = next((node for node in container.iter()
                             if node.tag.rsplit("}", 1)[-1] == "rootfile"), None)
            if rootfile is not None:
                package_path = rootfile.attrib.get("full-path", "")
        if not package_path:
            package_path = next((name for name in names if name.casefold().endswith(".opf")), "")
        if not package_path or package_path not in names:
            return None

        package = ElementTree.fromstring(archive.read(package_path))
        manifest: dict[str, str] = {}
        cover_ids: list[str] = []
        for node in package.iter():
            local = node.tag.rsplit("}", 1)[-1]
            if local == "item" and node.attrib.get("id") and node.attrib.get("href"):
                manifest[node.attrib["id"]] = node.attrib["href"]
                properties = node.attrib.get("properties", "").split()
                if "cover-image" in properties:
                    cover_ids.insert(0, node.attrib["id"])
            elif local == "meta" and node.attrib.get("name", "").casefold() == "cover":
                cover_id = node.attrib.get("content", "")
                if cover_id:
                    cover_ids.append(cover_id)

        package_dir = posixpath.dirname(package_path)
        candidates = [manifest[item_id] for item_id in cover_ids if item_id in manifest]
        candidates.extend(href for href in manifest.values()
                          if "cover" in posixpath.basename(href).casefold()
                          and Path(href).suffix.casefold() in {".jpg", ".jpeg", ".png", ".gif", ".webp"})
        for href in candidates:
            member = posixpath.normpath(posixpath.join(package_dir, unquote(href.split("#", 1)[0])))
            if member in names:
                info = archive.getinfo(member)
                if 0 < info.file_size <= MAX_COVER_BYTES:
                    data = archive.read(member)
                    if _image_from_bytes(data) is not None:
                        return data
    return None


def _palm_cover(path: Path) -> bytes | None:
    """Read the EXTH cover image record from an unencrypted Palm/MOBI file."""
    with path.open("rb") as stream:
        pdb_header = stream.read(78)
        if len(pdb_header) < 78:
            return None
        record_count = struct.unpack_from(">H", pdb_header, 76)[0]
        if not 0 < record_count <= 65535:
            return None
        record_table = stream.read(record_count * 8)
        if len(record_table) != record_count * 8:
            return None
        offsets = [struct.unpack_from(">I", record_table, index * 8)[0]
                   for index in range(record_count)]
        if not offsets or offsets[0] >= path.stat().st_size:
            return None
        record0_end = offsets[1] if len(offsets) > 1 else path.stat().st_size
        stream.seek(offsets[0])
        record0 = stream.read(max(0, record0_end - offsets[0]))
        mobi_start = record0.find(b"MOBI", 0, 64)
        if mobi_start < 0 or len(record0) < mobi_start + 132:
            return None
        header_length = struct.unpack_from(">I", record0, mobi_start + 4)[0]
        first_image = struct.unpack_from(">I", record0, mobi_start + 108)[0]
        exth_flags = struct.unpack_from(">I", record0, mobi_start + 112)[0]
        if first_image == 0xFFFFFFFF:
            return None

        cover_offset: int | None = None
        exth_start = mobi_start + header_length
        if exth_flags & 0x40 and record0[exth_start:exth_start + 4] == b"EXTH":
            exth_length, count = struct.unpack_from(">II", record0, exth_start + 4)
            cursor = exth_start + 12
            exth_end = min(len(record0), exth_start + exth_length)
            for _ in range(count):
                if cursor + 8 > exth_end:
                    break
                record_type, record_length = struct.unpack_from(">II", record0, cursor)
                if record_length < 8 or cursor + record_length > exth_end:
                    break
                if record_type == 201 and record_length >= 12:
                    cover_offset = struct.unpack_from(">I", record0, cursor + 8)[0]
                    break
                cursor += record_length

        indexes: list[int] = []
        if cover_offset is not None:
            indexes.append(first_image + cover_offset)
        # Some MOBI files omit EXTH cover metadata; inspect image records in order.
        last_image = struct.unpack_from(">H", record0, mobi_start + 186)[0] if len(record0) >= mobi_start + 188 else 0xFFFF
        if last_image != 0xFFFF:
            indexes.extend(index for index in range(first_image, min(last_image + 1, record_count))
                           if index not in indexes)
        for index in indexes:
            if index < 0 or index >= record_count:
                continue
            start = offsets[index]
            end = offsets[index + 1] if index + 1 < record_count else path.stat().st_size
            if end <= start or end - start > MAX_COVER_BYTES:
                continue
            stream.seek(start)
            image_data = stream.read(end - start)
            if _image_from_bytes(image_data) is not None:
                return image_data
    return None


def _image_from_bytes(data: bytes | None) -> QImage | None:
    if not data or len(data) > MAX_COVER_BYTES:
        return None
    image = QImage.fromData(data)
    return image if not image.isNull() else None
