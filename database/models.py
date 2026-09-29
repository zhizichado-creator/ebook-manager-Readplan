"""SQLite data models and CRUD functions for books, categories, and tags."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterator


@dataclass(frozen=True, slots=True)
class Book:
    id: int
    title: str
    author: str | None
    path: str
    format: str | None
    size: int | None = None
    cover: str | None = None
    category_id: int | None = None
    created_time: str | None = None
    last_open_time: str | None = None
    description: str | None = None
    publisher: str | None = None
    publish_date: str | None = None
    isbn: str | None = None
    language: str = "zh"
    pages: int | None = None
    rating: float = 0
    is_favorite: bool = False
    read_status: str = "unread"
    read_progress: float = 0
    last_position: str | None = None
    hash: str | None = None


@dataclass(frozen=True, slots=True)
class Category:
    id: int
    name: str
    parent_id: int | None = None
    icon: str | None = None
    sort_order: int = 0
    created_time: str | None = None


@dataclass(frozen=True, slots=True)
class Tag:
    id: int
    name: str
    source: str = "system"
    color: str = "#888888"
    created_time: str | None = None


@dataclass(frozen=True, slots=True)
class BookTag:
    book_id: int
    tag_id: int
    created_time: str | None = None


@dataclass(frozen=True, slots=True)
class Shelf:
    id: int
    name: str
    description: str | None = None
    cover: str | None = None
    created_time: str | None = None


@dataclass(frozen=True, slots=True)
class Note:
    id: int
    book_id: int
    type: str
    content: str
    position: str | None = None
    color: str | None = None
    created_time: str | None = None
    updated_time: str | None = None


@dataclass(frozen=True, slots=True)
class ReadingLog:
    id: int
    book_id: int
    start_time: str
    end_time: str | None = None
    duration: int = 0
    pages_read: int = 0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def _connection(connection: sqlite3.Connection | None = None) -> Iterator[sqlite3.Connection]:
    """Use a caller connection or lazily open/close the configured default DB."""
    owned = connection is None
    if connection is None:
        from database.db import get_connection
        connection = get_connection()
    try:
        if owned:
            with connection:
                yield connection
        else:
            yield connection
    finally:
        if owned:
            connection.close()


def _dicts(cursor: sqlite3.Cursor) -> list[dict[str, Any]]:
    return [dict(row) for row in cursor.fetchall()]


def add_book(
    title: str,
    path: str,
    author: str = "",
    format: str = "",
    size: int | None = None,
    *,
    connection: sqlite3.Connection | None = None,
    **fields: Any,
) -> int:
    """Insert a book and return its new id. Extra supported columns may be keywords."""
    values: dict[str, Any] = {
        "title": title, "path": path, "author": author, "format": format,
        "size": size, "file_size": size or 0, "created_time": _now(),
    }
    allowed = {
        "cover", "category_id", "last_open_time", "description", "publisher", "publish_date",
        "isbn", "language", "pages", "rating", "is_favorite", "read_status", "read_progress",
        "last_position", "hash", "file_created_time", "file_modified_time", "notes",
        "indexed_mtime", "indexed_size", "missing",
    }
    values.update({key: value for key, value in fields.items() if key in allowed})
    columns = list(values)
    sql = f"INSERT INTO books ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})"
    with _connection(connection) as conn:
        cursor = conn.execute(sql, [values[column] for column in columns])
        return int(cursor.lastrowid)


def get_book(book_id: int, *, connection: sqlite3.Connection | None = None) -> dict[str, Any] | None:
    with _connection(connection) as conn:
        row = conn.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()
        return dict(row) if row else None


def list_books(
    *, connection: sqlite3.Connection | None = None, limit: int = 500, offset: int = 0,
) -> list[dict[str, Any]]:
    with _connection(connection) as conn:
        return _dicts(conn.execute(
            "SELECT * FROM books ORDER BY title COLLATE NOCASE LIMIT ? OFFSET ?", (limit, offset)
        ))


def update_book(book_id: int, *, connection: sqlite3.Connection | None = None, **fields: Any) -> int:
    """Update allowed book fields and return the affected row count."""
    allowed = {
        "title", "author", "path", "format", "size", "file_size", "cover", "cover_path", "category_id",
        "last_open_time", "description", "publisher", "publish_date", "isbn", "language", "pages",
        "rating", "is_favorite", "read_status", "read_progress", "last_position", "hash", "notes",
        "reading_status", "file_created_time", "file_modified_time", "indexed_mtime", "indexed_size", "missing",
    }
    updates = {key: value for key, value in fields.items() if key in allowed}
    if not updates:
        return 0
    if "size" in updates and "file_size" not in updates:
        updates["file_size"] = updates["size"] or 0
    if "cover" in updates and "cover_path" not in updates:
        updates["cover_path"] = updates["cover"]
    if "read_status" in updates and "reading_status" not in updates:
        updates["reading_status"] = "completed" if updates["read_status"] == "finished" else updates["read_status"]
    assignments = ", ".join(f"{key}=?" for key in updates)
    with _connection(connection) as conn:
        return conn.execute(f"UPDATE books SET {assignments} WHERE id=?", (*updates.values(), book_id)).rowcount


def delete_book(book_id: int, *, connection: sqlite3.Connection | None = None) -> int:
    with _connection(connection) as conn:
        return conn.execute("DELETE FROM books WHERE id=?", (book_id,)).rowcount


def search_books(query: str, *, connection: sqlite3.Connection | None = None, limit: int = 500) -> list[dict[str, Any]]:
    term = f"%{query.strip()}%"
    with _connection(connection) as conn:
        return _dicts(conn.execute(
            "SELECT DISTINCT b.* FROM books b "
            "LEFT JOIN categories c ON c.id=b.category_id "
            "LEFT JOIN book_categories bc ON bc.book_id=b.id "
            "LEFT JOIN categories c2 ON c2.id=bc.category_id "
            "LEFT JOIN book_tags bt ON bt.book_id=b.id LEFT JOIN tags t ON t.id=bt.tag_id "
            "WHERE b.title LIKE ? OR b.author LIKE ? OR b.format LIKE ? OR c.name LIKE ? OR c2.name LIKE ? OR t.name LIKE ? "
            "ORDER BY b.title COLLATE NOCASE LIMIT ?",
            (term, term, term, term, term, term, limit),
        ))


def update_last_open(book_id: int, *, connection: sqlite3.Connection | None = None) -> int:
    now = _now()
    with _connection(connection) as conn:
        return conn.execute(
            "UPDATE books SET last_open_time=?, read_status='reading', reading_status='reading' WHERE id=?",
            (now, book_id),
        ).rowcount


def update_progress(
    book_id: int, progress: float, position: str | None = None, *,
    connection: sqlite3.Connection | None = None,
) -> int:
    progress = min(max(float(progress), 0.0), 1.0)
    with _connection(connection) as conn:
        return conn.execute(
            "UPDATE books SET read_progress=?, last_position=? WHERE id=?", (progress, position, book_id)
        ).rowcount


def add_category(
    name: str, parent_id: int | None = None, icon: str | None = None, sort_order: int = 0,
    *, connection: sqlite3.Connection | None = None,
) -> int:
    with _connection(connection) as conn:
        cursor = conn.execute(
            "INSERT INTO categories(name, normalized_name, parent_id, icon, sort_order, created_time) VALUES (?, ?, ?, ?, ?, ?)",
            (name.strip(), name.strip().casefold(), parent_id, icon, sort_order, _now()),
        )
        return int(cursor.lastrowid)


def list_categories(*, connection: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
    with _connection(connection) as conn:
        return _dicts(conn.execute("SELECT * FROM categories ORDER BY sort_order, name COLLATE NOCASE"))


def get_category(category_id: int, *, connection: sqlite3.Connection | None = None) -> dict[str, Any] | None:
    with _connection(connection) as conn:
        row = conn.execute("SELECT * FROM categories WHERE id=?", (category_id,)).fetchone()
        return dict(row) if row else None


def delete_category(category_id: int, *, connection: sqlite3.Connection | None = None) -> int:
    with _connection(connection) as conn:
        return conn.execute("DELETE FROM categories WHERE id=?", (category_id,)).rowcount


def add_category_to_book(book_id: int, category_id: int, *, connection: sqlite3.Connection | None = None) -> int:
    """Associate a book with an additional category."""
    with _connection(connection) as conn:
        cursor = conn.execute(
            "INSERT OR IGNORE INTO book_categories(book_id, category_id) VALUES (?, ?)",
            (book_id, category_id),
        )
        return cursor.rowcount


def remove_category_from_book(book_id: int, category_id: int, *, connection: sqlite3.Connection | None = None) -> int:
    with _connection(connection) as conn:
        return conn.execute(
            "DELETE FROM book_categories WHERE book_id=? AND category_id=?", (book_id, category_id)
        ).rowcount


def get_book_categories(book_id: int, *, connection: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
    with _connection(connection) as conn:
        return _dicts(conn.execute(
            "SELECT c.* FROM categories c JOIN book_categories bc ON bc.category_id=c.id "
            "WHERE bc.book_id=? ORDER BY c.sort_order, c.name COLLATE NOCASE", (book_id,)
        ))


def add_tag(name: str, color: str = "#888888", *, connection: sqlite3.Connection | None = None) -> int:
    with _connection(connection) as conn:
        cursor = conn.execute(
            "INSERT INTO tags(name, normalized_name, color, created_time) VALUES (?, ?, ?, ?)",
            (name.strip(), name.strip().casefold(), color, _now()),
        )
        return int(cursor.lastrowid)


def list_tags(*, connection: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
    with _connection(connection) as conn:
        return _dicts(conn.execute("SELECT * FROM tags ORDER BY name COLLATE NOCASE"))


def get_or_create_tag(name: str, color: str = "#888888", *, connection: sqlite3.Connection | None = None) -> int:
    normalized = name.strip().casefold()
    with _connection(connection) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO tags(name, normalized_name, color, created_time) VALUES (?, ?, ?, ?)",
            (name.strip(), normalized, color, _now()),
        )
        row = conn.execute("SELECT id FROM tags WHERE normalized_name=?", (normalized,)).fetchone()
        return int(row[0])


def delete_tag(tag_id: int, *, connection: sqlite3.Connection | None = None) -> int:
    with _connection(connection) as conn:
        return conn.execute("DELETE FROM tags WHERE id=?", (tag_id,)).rowcount


def add_tag_to_book(book_id: int, tag_id: int, *, connection: sqlite3.Connection | None = None) -> int:
    with _connection(connection) as conn:
        cursor = conn.execute(
            "INSERT OR IGNORE INTO book_tags(book_id, tag_id, created_time) VALUES (?, ?, ?)",
            (book_id, tag_id, _now()),
        )
        return cursor.rowcount


def remove_tag_from_book(book_id: int, tag_id: int, *, connection: sqlite3.Connection | None = None) -> int:
    with _connection(connection) as conn:
        return conn.execute("DELETE FROM book_tags WHERE book_id=? AND tag_id=?", (book_id, tag_id)).rowcount


def get_book_tags(book_id: int, *, connection: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
    with _connection(connection) as conn:
        return _dicts(conn.execute(
            "SELECT t.* FROM tags t JOIN book_tags bt ON bt.tag_id=t.id WHERE bt.book_id=? ORDER BY t.name",
            (book_id,),
        ))


def add_shelf(name: str, description: str = "", *, connection: sqlite3.Connection | None = None) -> int:
    with _connection(connection) as conn:
        cursor = conn.execute(
            "INSERT INTO shelves(name, description, created_time) VALUES (?, ?, ?)",
            (name.strip(), description, _now()),
        )
        return int(cursor.lastrowid)


def list_shelves(*, connection: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
    with _connection(connection) as conn:
        return _dicts(conn.execute("SELECT * FROM shelves ORDER BY name COLLATE NOCASE"))


def add_note(book_id: int, content: str, note_type: str = "note", position: str | None = None,
             color: str | None = None, *, connection: sqlite3.Connection | None = None) -> int:
    now = _now()
    with _connection(connection) as conn:
        cursor = conn.execute(
            "INSERT INTO notes(book_id, type, content, position, color, created_time, updated_time) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (book_id, note_type, content, position, color, now, now),
        )
        return int(cursor.lastrowid)


def add_reading_log(book_id: int, start_time: str | None = None, end_time: str | None = None,
                    duration: int = 0, pages_read: int = 0, *, connection: sqlite3.Connection | None = None) -> int:
    with _connection(connection) as conn:
        cursor = conn.execute(
            "INSERT INTO reading_log(book_id, start_time, end_time, duration, pages_read) VALUES (?, ?, ?, ?, ?)",
            (book_id, start_time or _now(), end_time, duration, pages_read),
        )
        return int(cursor.lastrowid)
