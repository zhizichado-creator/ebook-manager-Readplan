"""Unit tests for recursive ebook scanning and incremental indexing."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from database.db import Database
from scanner.scanner import scan_directory_stats


class ScannerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.library = self.root / "library"
        self.library.mkdir()
        self.database = Database(self.root / "test-library.db")

    def tearDown(self) -> None:
        self.database.close()
        self.temp_dir.cleanup()

    def test_normal_recursive_scan(self) -> None:
        (self.library / "高等数学.pdf").write_bytes(b"%PDF-1.7 sample")
        nested = self.library / "Computer Science"
        nested.mkdir()
        (nested / "Algorithm.epub").write_bytes(b"epub sample")

        result = scan_directory_stats(self.database, self.library)

        self.assertEqual(result, {"total": 2, "new": 2, "updated": 0, "skipped": 0, "errors": 0})
        books = self.database.list_books()
        self.assertEqual({book["title"] for book in books}, {"高等数学", "Algorithm"})
        pdf = next(book for book in books if book["title"] == "高等数学")
        self.assertEqual(pdf["format"], "pdf")
        self.assertEqual(pdf["size"], len(b"%PDF-1.7 sample"))
        self.assertTrue(pdf["file_modified_time"].startswith("20"))

    def test_repeated_scan_does_not_duplicate_records(self) -> None:
        (self.library / "book.txt").write_text("text", encoding="utf-8")

        first = scan_directory_stats(self.database, self.library)
        second = scan_directory_stats(self.database, self.library)

        self.assertEqual(first["new"], 1)
        self.assertEqual(second["new"], 0)
        self.assertEqual(second["skipped"], 1)
        self.assertEqual(len(self.database.list_books()), 1)

    def test_new_file_is_found_on_next_scan(self) -> None:
        first_file = self.library / "first.mobi"
        first_file.write_bytes(b"first")
        scan_directory_stats(self.database, self.library)
        (self.library / "second.azw3").write_bytes(b"second")

        result = scan_directory_stats(self.database, self.library)

        self.assertEqual(result["new"], 1)
        self.assertEqual(len(self.database.list_books()), 2)
        self.assertIn("second", {book["title"] for book in self.database.list_books()})

    def test_unsupported_format_is_skipped(self) -> None:
        (self.library / "notes.docx").write_bytes(b"not an ebook")
        (self.library / "book.azw").write_bytes(b"ebook")

        result = scan_directory_stats(self.database, self.library)

        self.assertEqual(result["total"], 2)
        self.assertEqual(result["new"], 1)
        self.assertEqual(result["skipped"], 1)
        self.assertEqual(len(self.database.list_books()), 1)

    def test_folder_and_filename_keywords_assign_categories_and_tags(self) -> None:
        computer_folder = self.library / "Computer Science"
        computer_folder.mkdir()
        (computer_folder / "Algorithm.pdf").write_bytes(b"sample")
        (self.library / "Quantum Physics.epub").write_bytes(b"sample")

        result = scan_directory_stats(self.database, self.library)

        self.assertEqual(result["new"], 2)
        books = {row["title"]: row for row in self.database.list_books()}
        algorithm_category = self.database.connection.execute(
            "SELECT name FROM categories WHERE id=?", (books["Algorithm"]["category_id"],)
        ).fetchone()[0]
        quantum_category = self.database.connection.execute(
            "SELECT name FROM categories WHERE id=?", (books["Quantum Physics"]["category_id"],)
        ).fetchone()[0]
        self.assertEqual(algorithm_category, "计算机")
        self.assertEqual(quantum_category, "物理")
        quantum_tags = {row["name"] for row in self.database.connection.execute(
            "SELECT t.name FROM tags t JOIN book_tags bt ON bt.tag_id=t.id WHERE bt.book_id=?",
            (books["Quantum Physics"]["id"],),
        )}
        self.assertIn("量子", quantum_tags)
        self.assertIn("物理", quantum_tags)
        self.assertEqual(scan_directory_stats(self.database, self.library)["new"], 0)
        self.assertEqual(len(self.database.list_books()), 2)


if __name__ == "__main__":
    unittest.main()
