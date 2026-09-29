"""SQLite connection, schema creation, migrations, and compatibility wrapper."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


DB_PATH = Path(__file__).resolve().with_name("ebook.db")


def iso_now() -> str:
    """Return a UTC ISO8601 timestamp with second precision."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# Kept as an alias for the scanner and UI while they are gradually migrated.
utc_now = iso_now


def get_connection(path: str | Path | None = None) -> sqlite3.Connection:
    """Open a configured SQLite connection. Schema creation is handled by init_db."""
    db_path = Path(path) if path is not None else DB_PATH
    db_path = db_path.expanduser()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    return connection


@contextmanager
def get_db(path: str | Path | None = None) -> Iterator[sqlite3.Connection]:
    """Yield a connection, committing on success and rolling back on exceptions."""
    connection = get_connection(path)
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}


def _add_missing_columns(connection: sqlite3.Connection, table: str, declarations: dict[str, str]) -> None:
    existing = _columns(connection, table)
    for name, declaration in declarations.items():
        if name not in existing:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")


def _initialize_connection(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            normalized_name TEXT UNIQUE,
            parent_id INTEGER REFERENCES categories(id) ON DELETE SET NULL,
            icon TEXT,
            sort_order INTEGER NOT NULL DEFAULT 0,
            is_default INTEGER NOT NULL DEFAULT 0,
            created_time TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
        );
        CREATE TABLE IF NOT EXISTS books (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            author TEXT,
            path TEXT NOT NULL UNIQUE,
            format TEXT,
            size INTEGER,
            file_size INTEGER NOT NULL DEFAULT 0,
            cover TEXT,
            cover_path TEXT,
            category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL,
            created_time TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
            last_open_time TEXT,
            description TEXT,
            publisher TEXT,
            publish_date TEXT,
            isbn TEXT,
            language TEXT NOT NULL DEFAULT 'zh',
            pages INTEGER,
            rating REAL NOT NULL DEFAULT 0 CHECK (rating BETWEEN 0 AND 5),
            is_favorite INTEGER NOT NULL DEFAULT 0 CHECK (is_favorite IN (0, 1)),
            read_status TEXT NOT NULL DEFAULT 'unread' CHECK (read_status IN ('unread','reading','finished')),
            read_progress REAL NOT NULL DEFAULT 0 CHECK (read_progress BETWEEN 0 AND 1),
            last_position TEXT,
            hash TEXT,
            file_created_time TEXT,
            file_modified_time TEXT,
            notes TEXT NOT NULL DEFAULT '',
            reading_status TEXT NOT NULL DEFAULT 'unread' CHECK (reading_status IN ('unread','reading','completed')),
            indexed_mtime REAL NOT NULL DEFAULT 0,
            indexed_size INTEGER NOT NULL DEFAULT 0,
            missing INTEGER NOT NULL DEFAULT 0 CHECK (missing IN (0,1))
        );
        CREATE TABLE IF NOT EXISTS tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            normalized_name TEXT UNIQUE,
            source TEXT NOT NULL DEFAULT 'system' CHECK (source IN ('manual','ai','system')),
            color TEXT NOT NULL DEFAULT '#888888',
            created_time TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
        );
        CREATE TABLE IF NOT EXISTS book_tags (
            book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
            tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
            created_time TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
            PRIMARY KEY (book_id, tag_id)
        );
        CREATE TABLE IF NOT EXISTS book_categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
            category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
            UNIQUE (book_id, category_id)
        );
        CREATE TABLE IF NOT EXISTS scan_roots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            path TEXT NOT NULL UNIQUE,
            enabled INTEGER NOT NULL DEFAULT 1,
            last_scanned_time TEXT,
            created_time TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
        );
        CREATE TABLE IF NOT EXISTS reading_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
            opened_time TEXT NOT NULL,
            open_result TEXT NOT NULL DEFAULT 'success'
        );
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS classification_feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            book_id INTEGER REFERENCES books(id) ON DELETE SET NULL,
            suggested_category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL,
            chosen_category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL,
            suggested_tags TEXT NOT NULL DEFAULT '[]',
            chosen_tags TEXT NOT NULL DEFAULT '[]',
            analyzer TEXT NOT NULL DEFAULT 'local',
            created_time TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
        );
        CREATE TABLE IF NOT EXISTS shelves (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            description TEXT,
            cover TEXT,
            created_time TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
        );
        CREATE TABLE IF NOT EXISTS book_shelves (
            book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
            shelf_id INTEGER NOT NULL REFERENCES shelves(id) ON DELETE CASCADE,
            sort_order INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (book_id, shelf_id)
        );
        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
            type TEXT NOT NULL DEFAULT 'note' CHECK (type IN ('note','bookmark','highlight')),
            content TEXT NOT NULL DEFAULT '',
            position TEXT,
            color TEXT,
            created_time TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
            updated_time TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
        );
        CREATE TABLE IF NOT EXISTS reading_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
            start_time TEXT NOT NULL,
            end_time TEXT,
            duration INTEGER NOT NULL DEFAULT 0,
            pages_read INTEGER NOT NULL DEFAULT 0
        );
        """
    )

    # Upgrade the earlier composite-key form without discarding any book links.
    if "id" not in _columns(connection, "book_categories"):
        connection.execute("SAVEPOINT migrate_book_categories_id")
        try:
            connection.execute("""
                CREATE TABLE book_categories_migration (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
                    category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
                    UNIQUE (book_id, category_id)
                )
            """)
            connection.execute(
                "INSERT OR IGNORE INTO book_categories_migration(book_id,category_id) "
                "SELECT book_id,category_id FROM book_categories"
            )
            connection.execute("DROP TABLE book_categories")
            connection.execute("ALTER TABLE book_categories_migration RENAME TO book_categories")
            connection.execute("RELEASE SAVEPOINT migrate_book_categories_id")
        except sqlite3.Error:
            connection.execute("ROLLBACK TO SAVEPOINT migrate_book_categories_id")
            connection.execute("RELEASE SAVEPOINT migrate_book_categories_id")
            raise

    # Add fields introduced after an older library was created; keep all existing rows.
    _add_missing_columns(connection, "books", {
        "author": "TEXT", "format": "TEXT", "size": "INTEGER", "file_size": "INTEGER NOT NULL DEFAULT 0",
        "cover": "TEXT", "cover_path": "TEXT", "category_id": "INTEGER REFERENCES categories(id) ON DELETE SET NULL",
        "last_open_time": "TEXT", "description": "TEXT", "publisher": "TEXT", "publish_date": "TEXT",
        "isbn": "TEXT", "language": "TEXT NOT NULL DEFAULT 'zh'", "pages": "INTEGER",
        "rating": "REAL NOT NULL DEFAULT 0", "is_favorite": "INTEGER NOT NULL DEFAULT 0",
        "read_status": "TEXT NOT NULL DEFAULT 'unread'", "read_progress": "REAL NOT NULL DEFAULT 0",
        "last_position": "TEXT", "hash": "TEXT", "file_created_time": "TEXT",
        "file_modified_time": "TEXT", "notes": "TEXT NOT NULL DEFAULT ''",
        "reading_status": "TEXT NOT NULL DEFAULT 'unread'", "indexed_mtime": "REAL NOT NULL DEFAULT 0",
        "indexed_size": "INTEGER NOT NULL DEFAULT 0", "missing": "INTEGER NOT NULL DEFAULT 0",
    })
    _add_missing_columns(connection, "categories", {
        "normalized_name": "TEXT", "parent_id": "INTEGER REFERENCES categories(id) ON DELETE SET NULL",
        "icon": "TEXT", "sort_order": "INTEGER NOT NULL DEFAULT 0", "is_default": "INTEGER NOT NULL DEFAULT 0",
        "created_time": "TEXT",
    })
    _add_missing_columns(connection, "tags", {
        "normalized_name": "TEXT", "source": "TEXT NOT NULL DEFAULT 'system'",
        "color": "TEXT NOT NULL DEFAULT '#888888'", "created_time": "TEXT",
    })
    _add_missing_columns(connection, "book_tags", {"created_time": "TEXT"})

    # Build indexes only after legacy tables have received their new columns.
    connection.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_books_category ON books(category_id);
        CREATE INDEX IF NOT EXISTS idx_books_title ON books(title COLLATE NOCASE);
        CREATE INDEX IF NOT EXISTS idx_books_author ON books(author COLLATE NOCASE);
        CREATE INDEX IF NOT EXISTS idx_books_format ON books(format);
        CREATE INDEX IF NOT EXISTS idx_books_last_open ON books(last_open_time DESC);
        CREATE INDEX IF NOT EXISTS idx_books_hash ON books(hash);
        CREATE INDEX IF NOT EXISTS idx_book_tags_tag ON book_tags(tag_id);
        CREATE INDEX IF NOT EXISTS idx_book_categories_category ON book_categories(category_id);
        CREATE INDEX IF NOT EXISTS idx_classification_feedback_book ON classification_feedback(book_id, created_time DESC);
        CREATE INDEX IF NOT EXISTS idx_reading_history_opened ON reading_history(opened_time DESC);
        CREATE INDEX IF NOT EXISTS idx_notes_book ON notes(book_id);
        CREATE INDEX IF NOT EXISTS idx_reading_log_book ON reading_log(book_id, start_time DESC);
        """
    )

    # Backfill fields added during migration and preserve old reading-state values.
    connection.execute("UPDATE books SET size=file_size WHERE size IS NULL")
    connection.execute("UPDATE books SET file_size=indexed_size WHERE file_size=0 AND indexed_size>0")
    connection.execute("UPDATE books SET cover=cover_path WHERE cover IS NULL")
    connection.execute("UPDATE books SET cover_path=cover WHERE cover_path IS NULL")
    connection.execute("UPDATE books SET read_status=CASE reading_status WHEN 'completed' THEN 'finished' ELSE reading_status END")
    connection.execute("UPDATE books SET reading_status=CASE read_status WHEN 'finished' THEN 'completed' ELSE read_status END")
    connection.execute("UPDATE categories SET normalized_name=lower(name) WHERE normalized_name IS NULL")
    connection.execute("UPDATE tags SET normalized_name=lower(name) WHERE normalized_name IS NULL")
    connection.execute("UPDATE book_tags SET created_time=? WHERE created_time IS NULL", (iso_now(),))

def init_db(path: str | Path | None = None) -> Path:
    """Create or extend the database schema. Categories and tags are created on demand."""
    db_path = Path(path) if path is not None else DB_PATH
    connection = get_connection(db_path)
    try:
        with connection:
            _initialize_connection(connection)
    finally:
        connection.close()
    return db_path


def reset_db(path: str | Path | None = None) -> Path:
    """Debug-only destructive reset: drop all application tables and recreate them."""
    connection = get_connection(path)
    try:
        with connection:
            connection.execute("PRAGMA defer_foreign_keys = ON")
            for table in ("reading_log", "notes", "book_shelves", "shelves", "reading_history",
                          "classification_feedback",
                          "book_categories", "book_tags", "books", "tags", "categories",
                          "scan_roots", "settings"):
                connection.execute(f"DROP TABLE IF EXISTS {table}")
            _initialize_connection(connection)
    finally:
        connection.close()
    return Path(path) if path is not None else DB_PATH


class Database:
    """Connection-owning adapter retained for the existing UI and scanner."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else DB_PATH
        self.path = self.path.expanduser()
        init_db(self.path)
        self.connection = get_connection(self.path)

    def initialize(self) -> None:
        with self.connection:
            _initialize_connection(self.connection)

    def list_books(self, query: str = "") -> list[dict[str, Any]]:
        sql = "SELECT * FROM books"
        params: tuple[str, ...] = ()
        if query.strip():
            sql += " WHERE title LIKE ? OR author LIKE ?"
            term = f"%{query.strip()}%"
            params = (term, term)
        sql += " ORDER BY title COLLATE NOCASE LIMIT 500"
        return [dict(row) for row in self.connection.execute(sql, params)]

    def close(self) -> None:
        self.connection.close()
