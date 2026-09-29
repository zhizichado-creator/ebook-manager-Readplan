"""Transactional tag CRUD, assignment, and merge operations."""
from __future__ import annotations
import sqlite3
from typing import Iterable


def normalize_tag_name(name: str) -> str:
    return " ".join(name.strip().lstrip("#＃").split())


def ensure_tag(connection: sqlite3.Connection, name: str, source: str = "manual") -> int:
    clean = normalize_tag_name(name)
    if not clean:
        raise ValueError("标签名称不能为空")
    if source not in {"manual", "ai", "system"}:
        raise ValueError("无效的标签来源")
    connection.execute(
        "INSERT OR IGNORE INTO tags(name,normalized_name,source,created_time) "
        "VALUES(?,?,?,strftime('%Y-%m-%dT%H:%M:%SZ','now'))",
        (clean, clean.casefold(), source),
    )
    row = connection.execute("SELECT id FROM tags WHERE normalized_name=?", (clean.casefold(),)).fetchone()
    return int(row[0])


def set_book_tags(connection: sqlite3.Connection, book_ids: Iterable[int], names: Iterable[str], *, source: str = "manual") -> None:
    ids = sorted({int(value) for value in book_ids})
    clean_names = list(dict.fromkeys(normalize_tag_name(name) for name in names if normalize_tag_name(name)))
    with connection:
        tag_ids = [ensure_tag(connection, name, source) for name in clean_names]
        for book_id in ids:
            connection.execute("DELETE FROM book_tags WHERE book_id=?", (book_id,))
            connection.executemany(
                "INSERT OR IGNORE INTO book_tags(book_id,tag_id,created_time) "
                "VALUES(?,?,strftime('%Y-%m-%dT%H:%M:%SZ','now'))",
                [(book_id, tag_id) for tag_id in tag_ids],
            )


def rename_tag(connection: sqlite3.Connection, tag_id: int, name: str) -> None:
    clean = normalize_tag_name(name)
    if not clean:
        raise ValueError("标签名称不能为空")
    with connection:
        connection.execute("UPDATE tags SET name=?,normalized_name=?,source='manual' WHERE id=?",
                           (clean, clean.casefold(), int(tag_id)))


def delete_tag(connection: sqlite3.Connection, tag_id: int) -> None:
    with connection:
        connection.execute("DELETE FROM tags WHERE id=?", (int(tag_id),))


def merge_tags(connection: sqlite3.Connection, source_ids: Iterable[int], target_id: int) -> None:
    sources = {int(value) for value in source_ids} - {int(target_id)}
    with connection:
        for source_id in sources:
            connection.execute(
                "INSERT OR IGNORE INTO book_tags(book_id,tag_id,created_time) "
                "SELECT book_id,?,strftime('%Y-%m-%dT%H:%M:%SZ','now') FROM book_tags WHERE tag_id=?",
                (int(target_id), source_id),
            )
            connection.execute("DELETE FROM tags WHERE id=?", (source_id,))
