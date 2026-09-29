"""Read EPUB package metadata and introductory text without unpacking to disk."""
from __future__ import annotations

import re
import posixpath
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote
from xml.etree import ElementTree


def parse_epub(path: str | Path, max_chapters: int = 5, max_chars: int = 24_000) -> dict[str, Any]:
    source = Path(path)
    try:
        with zipfile.ZipFile(source) as archive:
            names = set(archive.namelist())
            container = ElementTree.fromstring(archive.read("META-INF/container.xml"))
            rootfile = next((node.attrib.get("full-path", "") for node in container.iter()
                             if node.tag.rsplit("}", 1)[-1] == "rootfile"), "")
            package = ElementTree.fromstring(archive.read(rootfile))
            manifest: dict[str, str] = {}
            spine: list[str] = []
            metadata: dict[str, Any] = {"title": "", "author": "", "publisher": "", "subject": ""}
            for node in package.iter():
                tag = node.tag.rsplit("}", 1)[-1].casefold()
                if tag == "item":
                    manifest[node.attrib.get("id", "")] = node.attrib.get("href", "")
                elif tag == "itemref":
                    spine.append(node.attrib.get("idref", ""))
                elif tag in {"title", "creator", "publisher", "subject"} and node.text:
                    key = {"creator": "author"}.get(tag, tag)
                    metadata[key] = (metadata.get(key, "") + "; " + node.text.strip()).strip("; ")
            base = str(PurePosixPath(rootfile).parent)
            pieces: list[str] = []
            for item_id in spine[:max_chapters]:
                member = posixpath.normpath(posixpath.join(base, unquote(manifest.get(item_id, ""))))
                if member not in names:
                    continue
                if archive.getinfo(member).file_size > 2_000_000:
                    continue
                root = ElementTree.fromstring(archive.read(member))
                words = [node.text.strip() for node in root.iter()
                         if node.tag.rsplit("}", 1)[-1].casefold() in {"p", "h1", "h2", "h3"} and node.text]
                if words:
                    pieces.append("\n".join(words))
                if sum(map(len, pieces)) >= max_chars:
                    break
            text = re.sub(r"\s+", " ", "\n".join(pieces))[:max_chars]
            from collections import Counter
            tokens = re.findall(r"[A-Za-z][A-Za-z0-9+#.-]{2,}|[\u4e00-\u9fff]{2,8}", text.casefold())
            return {"metadata": metadata, "text": text,
                    "first_page": pieces[0][:6000] if pieces else "",
                    "abstract": metadata.get("subject", "") or text[:1800], "toc": "",
                    "keywords": [term for term, _ in Counter(tokens).most_common(20)]}
    except (OSError, KeyError, zipfile.BadZipFile, ElementTree.ParseError, ValueError) as error:
        return {"metadata": {}, "text": "", "keywords": [], "error": str(error)}
