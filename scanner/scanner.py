"""Recursive ebook indexing with incremental updates and error isolation.

The scanner never moves or modifies source files. ``scan_directory_stats`` is
the detailed API; ``scan_directory`` retains the older ``(new, updated)`` return
shape consumed by the current UI.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from typing import Any
import unicodedata

from database.db import DB_PATH, Database, utc_now
from utils.cover_utils import extract_cover

logger = logging.getLogger(__name__)

# Temporary default for the first desktop build. The UI can pass another folder.
DEFAULT_LIBRARY_PATH = Path(r"D:\LIBS")
SUPPORTED_FORMATS = {".pdf", ".epub", ".mobi", ".azw", ".azw3", ".txt"}

# Rules are intentionally lightweight and local: use folder names and filenames
# now; embedded PDF/EPUB metadata can feed the same classifier in a later phase.
CATEGORY_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("物理", ("physics", "quantum", "mechanics", "物理", "量子", "力学")),
    ("数学", ("mathematics", "mathematical", "math", "calculus", "algebra", "geometry", "statistics", "数学", "高等数学")),
    ("计算机", ("computer science", "computer", "programming", "python", "algorithm", "software", "coding", "data structure", "编程", "算法", "计算机", "人工智能", "机器学习")),
    ("编程语言", ("programming language", "programming", "python", "coding", "编程")),
    ("算法", ("algorithm", "算法")),
    ("化学", ("chemistry", "chemical", "化学")),
    ("生物", ("biology", "biological", "生物", "生命科学")),
    ("医学", ("medicine", "medical", "医学", "临床")),
    ("历史", ("history", "historical", "历史")),
    ("哲学", ("philosophy", "philosophical", "哲学")),
    ("文学", ("literature", "novel", "poetry", "fiction", "文学", "小说", "诗歌")),
    ("工程", ("engineering", "engineer", "工程")),
    ("经济", ("economics", "economy", "经济")),
    ("管理", ("management", "business", "管理")),
    ("心理学", ("psychology", "心理学")),
    ("法律", ("law", "legal", "法律")),
    ("教育", ("education", "teaching", "教育")),
    ("艺术", ("art", "design", "艺术", "设计")),
    ("地理", ("geography", "geographic", "地理")),
    ("天文", ("astronomy", "astronomical", "天文")),
)

TAG_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("量子", ("quantum", "量子")),
    ("量子力学", ("quantum mechanics", "量子力学")),
    ("理论物理", ("theoretical physics", "理论物理")),
    ("薛定谔方程", ("schrodinger equation", "薛定谔方程", "薛定谔")),
    ("线性代数", ("linear algebra", "线性代数")),
    ("群论", ("group theory", "群论")),
    ("抽象代数", ("abstract algebra", "抽象代数")),
    ("PyTorch", ("pytorch",)),
    ("Python", ("python",)),
    ("算法", ("algorithm", "算法")),
    ("编程", ("programming", "coding", "编程")),
    ("人工智能", ("artificial intelligence", "machine learning", "deep learning", "ai", "人工智能", "机器学习", "深度学习")),
    ("数学", ("math", "mathematics", "calculus", "algebra", "数学")),
    ("物理", ("physics", "物理")),
    ("历史", ("history", "历史")),
    ("哲学", ("philosophy", "哲学")),
    ("文学", ("literature", "novel", "poetry", "文学", "小说", "诗歌")),
    ("计算机", ("computer science", "computer", "计算机")),
    ("数据分析", ("data analysis", "数据分析")),
)


def _matches(text: str, keyword: str) -> bool:
    """Match Chinese phrases by substring and English terms by word boundary."""
    if any(ord(character) > 127 for character in keyword):
        return keyword.casefold() in text
    return re.search(rf"(?<![a-z0-9]){re.escape(keyword.casefold())}(?![a-z0-9])", text) is not None


def classify_book(path: str | Path, title: str | None = None) -> tuple[list[str], list[str]]:
    """Infer ordered categories and canonical tags from a book path and title."""
    source = Path(path)
    text = unicodedata.normalize("NFKC", f"{source.parent} {source.stem} {title or ''}").casefold()
    text = re.sub(r"[_\\./-]+", " ", text)
    categories = [name for name, keywords in CATEGORY_RULES
                  if any(_matches(text, keyword) for keyword in keywords)]
    tags = [name for name, keywords in TAG_RULES
            if any(_matches(text, keyword) for keyword in keywords)]
    if not categories:
        categories = ["其他"]
    return categories, tags


def _apply_classification(
    connection: sqlite3.Connection,
    columns: set[str],
    book_id: int,
    path: Path,
    existing_category_id: int | None,
) -> bool:
    """Attach inferred categories/tags without overwriting a chosen main category."""
    category_names, tag_names = classify_book(path)
    changed = False
    category_ids: list[int] = []
    now = utc_now()

    for name in category_names:
        row = connection.execute(
            "SELECT id FROM categories WHERE normalized_name=? OR name=? LIMIT 1",
            (name.casefold(), name),
        ).fetchone()
        if row is None:
            category_values: dict[str, Any] = {"name": name, "normalized_name": name.casefold()}
            if "is_default" in {info[1] for info in connection.execute("PRAGMA table_info(categories)")}:
                category_values["is_default"] = 0
            if "created_time" in {info[1] for info in connection.execute("PRAGMA table_info(categories)")}:
                category_values["created_time"] = now
            category_columns = list(category_values)
            cursor = connection.execute(
                f"INSERT OR IGNORE INTO categories ({', '.join(category_columns)}) "
                f"VALUES ({', '.join('?' for _ in category_columns)})",
                [category_values[key] for key in category_columns],
            )
            changed |= cursor.rowcount == 1
            row = connection.execute(
                "SELECT id FROM categories WHERE normalized_name=? OR name=? LIMIT 1",
                (name.casefold(), name),
            ).fetchone()
        if row is not None:
            category_ids.append(int(row[0]))

    if existing_category_id is None and category_ids and "category_id" in columns:
        connection.execute("UPDATE books SET category_id=? WHERE id=?", (category_ids[0], book_id))
        changed = True
        # Related categories capture useful subcategories such as 编程语言 and 算法.
        for category_id in category_ids:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO book_categories(book_id,category_id) VALUES (?,?)",
                (book_id, category_id),
            )
            changed |= cursor.rowcount == 1

    tag_columns = {info[1] for info in connection.execute("PRAGMA table_info(tags)")}
    for name in tag_names:
        tag = connection.execute(
            "SELECT id FROM tags WHERE normalized_name=? OR name=? LIMIT 1",
            (name.casefold(), name),
        ).fetchone()
        if tag is None:
            values: dict[str, Any] = {"name": name, "normalized_name": name.casefold(), "source": "system"}
            if "color" in tag_columns:
                values["color"] = "#888888"
            if "created_time" in tag_columns:
                values["created_time"] = now
            names = list(values)
            cursor = connection.execute(
                f"INSERT OR IGNORE INTO tags ({', '.join(names)}) VALUES ({', '.join('?' for _ in names)})",
                [values[key] for key in names],
            )
            changed |= cursor.rowcount == 1
            tag = connection.execute(
                "SELECT id FROM tags WHERE normalized_name=? OR name=? LIMIT 1",
                (name.casefold(), name),
            ).fetchone()
        if tag is not None:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO book_tags(book_id,tag_id,created_time) VALUES (?,?,?)",
                (book_id, int(tag[0]), now),
            )
            changed |= cursor.rowcount == 1
    return changed


def _file_time(timestamp: float) -> str:
    """Convert a filesystem timestamp to a consistent UTC ISO8601 string."""
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat(timespec="seconds")


def _connection(database: Database | sqlite3.Connection) -> sqlite3.Connection:
    """Accept the project's Database wrapper or a direct SQLite connection."""
    return getattr(database, "connection", database)


def _book_columns(connection: sqlite3.Connection) -> set[str]:
    """Read the current book schema so this module does not assume new columns."""
    return {row["name"] if isinstance(row, sqlite3.Row) else row[1]
            for row in connection.execute("PRAGMA table_info(books)")}


def _cover_directory(database: Database | sqlite3.Connection) -> Path:
    """Keep extracted covers beside the active library database, never source books."""
    database_path = getattr(database, "path", DB_PATH)
    return Path(database_path).expanduser().resolve().parent / "covers"


def _ensure_cover(
    connection: sqlite3.Connection,
    columns: set[str],
    book_id: int,
    path: Path,
    cache_dir: Path,
    force: bool = False,
) -> bool:
    """Extract a cover only when missing or when the source file changed."""
    if "cover_path" not in columns or path.suffix.casefold() == ".txt":
        return False
    if not force:
        row = connection.execute("SELECT cover_path FROM books WHERE id=?", (book_id,)).fetchone()
        current = str(row[0] or "") if row else ""
        if current and Path(current).is_file():
            return False
    try:
        cover_path = extract_cover(path, cache_dir)
    except Exception:
        logger.exception("封面提取失败：%s", path)
        return False
    if not cover_path:
        return False
    updates = {column: cover_path for column in ("cover_path", "cover") if column in columns}
    assignments = ", ".join(f"{column}=?" for column in updates)
    connection.execute(f"UPDATE books SET {assignments} WHERE id=?", (*updates.values(), book_id))
    return True


def _upsert_file(
    connection: sqlite3.Connection,
    columns: set[str],
    path: Path,
    stat: os.stat_result,
    scanned_at: str,
    cover_dir: Path,
) -> str:
    """Insert/update a single path; return ``new``, ``updated`` or ``skipped``."""
    canonical = str(path.resolve())
    file_format = path.suffix[1:].lower()
    file_created = _file_time(stat.st_ctime)
    file_modified = _file_time(stat.st_mtime)
    row = connection.execute(
        "SELECT * FROM books WHERE path=?", (canonical,)
    ).fetchone()

    if row is not None:
        # Classification runs after scanning in the asynchronous local parser.
        # Keeping scan-time path rules here would create unused categories/tags
        # before content analysis has produced the final suggestions.
        classification_changed = False
        cover_changed = _ensure_cover(connection, columns, int(row["id"]), path, cover_dir)
        previous_size = row["indexed_size"] if "indexed_size" in columns else row["size"] if "size" in columns else row["file_size"]
        previous_mtime = row["indexed_mtime"] if "indexed_mtime" in columns else None
        previous_modified = row["modified_time"] if "modified_time" in columns else row["file_modified_time"] if "file_modified_time" in columns else None
        unchanged = (
            previous_size == stat.st_size
            and ((previous_mtime is not None and previous_mtime == stat.st_mtime)
                 or (previous_mtime is None and previous_modified == file_modified))
        )
        if unchanged:
            if "last_scan_time" in columns:
                connection.execute("UPDATE books SET last_scan_time=? WHERE id=?", (scanned_at, row["id"]))
            if "missing" in columns and row["missing"]:
                connection.execute("UPDATE books SET missing=0 WHERE id=?", (row["id"],))
                return "updated"
            return "updated" if classification_changed or cover_changed else "skipped"

        values: dict[str, Any] = {
            "title": path.stem,
            "format": file_format,
            "size": stat.st_size,
            "file_size": stat.st_size,
            "file_created_time": file_created,
            "created_time": scanned_at,
            "modified_time": file_modified,
            "file_modified_time": file_modified,
            "indexed_mtime": stat.st_mtime,
            "indexed_size": stat.st_size,
            "last_scan_time": scanned_at,
            "missing": 0,
            "cover_path": None,
            "cover": None,
        }
        updates = {key: value for key, value in values.items() if key in columns}
        assignments = ", ".join(f"{key}=?" for key in updates)
        connection.execute(
            f"UPDATE books SET {assignments} WHERE id=?", (*updates.values(), row["id"])
        )
        _ensure_cover(connection, columns, int(row["id"]), path, cover_dir, force=True)
        return "updated"

    values = {
        "title": path.stem,
        "author": "",
        "path": canonical,
        "format": file_format,
        "size": stat.st_size,
        "file_size": stat.st_size,
        "file_created_time": file_created,
        "created_time": scanned_at,
        "modified_time": file_modified,
        "file_modified_time": file_modified,
        "indexed_mtime": stat.st_mtime,
        "indexed_size": stat.st_size,
        "last_scan_time": scanned_at,
        "missing": 0,
    }
    insert_values = {key: value for key, value in values.items() if key in columns}
    names = list(insert_values)
    cursor = connection.execute(
        f"INSERT OR IGNORE INTO books ({', '.join(names)}) "
        f"VALUES ({', '.join('?' for _ in names)})",
        tuple(insert_values[name] for name in names),
    )
    if cursor.rowcount == 1:
        _ensure_cover(connection, columns, int(cursor.lastrowid), path, cover_dir)
        return "new"
    return "skipped"


def scan_directory_stats(
    database: Database | sqlite3.Connection,
    root: str | Path = DEFAULT_LIBRARY_PATH,
) -> dict[str, int]:
    """Recursively scan ``root`` and return total/new/updated/skipped/errors.

    Files are identified by normalized full path. Unchanged files are skipped;
    a changed file at the same path updates its existing database record. Schema
    differences are handled by selecting only columns already present in books.
    """
    stats = {"total": 0, "new": 0, "updated": 0, "skipped": 0, "errors": 0}
    folder = Path(root).expanduser()
    try:
        folder = folder.resolve(strict=True)
        if not folder.is_dir():
            raise NotADirectoryError(str(folder))
    except (OSError, RuntimeError) as error:
        logger.error("无法访问扫描目录 %s：%s", folder, error)
        stats["errors"] += 1
        return stats

    connection = _connection(database)
    try:
        columns = _book_columns(connection)
    except sqlite3.Error:
        logger.exception("读取 books 表结构失败")
        stats["errors"] += 1
        return stats
    scanned_at = utc_now()
    cover_dir = _cover_directory(database)

    def on_walk_error(error: OSError) -> None:
        stats["errors"] += 1
        logger.error("无法遍历目录 %s：%s", getattr(error, "filename", folder), error)

    try:
        with connection:
            for current, directories, filenames in os.walk(folder, onerror=on_walk_error):
                # Ignore hidden folders as a practical guard against application caches.
                directories[:] = [name for name in directories if not name.startswith(".")]
                for filename in filenames:
                    path = Path(current) / filename
                    stats["total"] += 1
                    if path.suffix.lower() not in SUPPORTED_FORMATS:
                        stats["skipped"] += 1
                        logger.debug("跳过不支持的格式：%s", path)
                        continue
                    try:
                        # Opening one byte catches unreadable files without loading large books.
                        with path.open("rb") as stream:
                            stream.read(1)
                        file_stat = path.stat()
                        result = _upsert_file(connection, columns, path, file_stat, scanned_at, cover_dir)
                        stats[result] += 1
                    except (OSError, sqlite3.Error, ValueError) as error:
                        stats["errors"] += 1
                        logger.exception("扫描文件失败 %s：%s", path, error)
    except sqlite3.Error:
        # A database failure is reported instead of escaping into the Qt event loop.
        stats["errors"] += 1
        logger.exception("扫描时数据库写入失败，目录：%s", folder)
    return stats


def backfill_missing_covers(database: Database | sqlite3.Connection) -> dict[str, int]:
    """Generate covers for already indexed books without rescanning their folders."""
    connection = _connection(database)
    stats = {"total": 0, "generated": 0, "missing_files": 0, "errors": 0}
    try:
        columns = _book_columns(connection)
        if "cover_path" not in columns:
            return stats
        cover_dir = _cover_directory(database)
        rows = connection.execute(
            "SELECT id, path, cover_path FROM books "
            "WHERE cover_path IS NULL OR cover_path='' ORDER BY id"
        ).fetchall()
        stats["total"] = len(rows)
        for row in rows:
            source = Path(str(row["path"] or ""))
            if source.suffix.casefold() not in {".pdf", ".epub", ".mobi", ".azw", ".azw3"}:
                continue
            if not source.is_file():
                stats["missing_files"] += 1
                continue
            try:
                # Commit one book at a time so a large library does not hold a
                # SQLite write lock while the next document is being decoded.
                with connection:
                    if _ensure_cover(connection, columns, int(row["id"]), source, cover_dir):
                        stats["generated"] += 1
            except (OSError, sqlite3.Error, ValueError, RuntimeError):
                stats["errors"] += 1
                logger.exception("补全封面失败：%s", source)
    except sqlite3.Error:
        stats["errors"] += 1
        logger.exception("读取待补全封面列表失败")
    return stats


def scan_files_stats(
    database: Database | sqlite3.Connection,
    files: list[str | Path],
) -> dict[str, int]:
    """Index explicitly selected ebook files without scanning sibling files."""
    stats = {"total": 0, "new": 0, "updated": 0, "skipped": 0, "errors": 0}
    connection = _connection(database)
    cover_dir = _cover_directory(database)
    try:
        columns = _book_columns(connection)
        scanned_at = utc_now()
        with connection:
            for item in files:
                path = Path(item).expanduser()
                stats["total"] += 1
                try:
                    if path.suffix.lower() not in SUPPORTED_FORMATS:
                        stats["skipped"] += 1
                        continue
                    path = path.resolve(strict=True)
                    if not path.is_file():
                        stats["errors"] += 1
                        continue
                    file_stat = path.stat()
                    with path.open("rb") as stream:
                        stream.read(1)
                    stats[_upsert_file(connection, columns, path, file_stat, scanned_at, cover_dir)] += 1
                except (OSError, RuntimeError, sqlite3.Error, ValueError):
                    stats["errors"] += 1
                    logger.exception("导入文件失败：%s", path)
    except sqlite3.Error:
        stats["errors"] += 1
        logger.exception("导入所选文件时数据库写入失败")
    return stats


def scan_directory(
    database: Database | sqlite3.Connection,
    root: str | Path = DEFAULT_LIBRARY_PATH,
) -> tuple[int, int]:
    """Compatibility wrapper used by the current UI: return ``(new, updated)``."""
    stats = scan_directory_stats(database, root)
    return stats["new"], stats["updated"]


async def scan_directory_async(
    database: Database | sqlite3.Connection,
    root: str | Path = DEFAULT_LIBRARY_PATH,
) -> dict[str, int]:
    """Async entry point for future UI use; perform filesystem work off the UI thread."""
    return await asyncio.to_thread(scan_directory_stats, database, root)
