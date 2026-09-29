"""Filesystem helpers for supported ebook files."""
from __future__ import annotations

from pathlib import Path

SUPPORTED_FORMATS = {".pdf", ".epub", ".mobi", ".txt", ".azw", ".azw3"}


def is_supported_book(path: str | Path) -> bool:
    """Return whether the file extension is a supported ebook format."""
    return Path(path).suffix.lower() in SUPPORTED_FORMATS


def format_file_size(size: int) -> str:
    """Format bytes as a compact human-readable size."""
    value = float(max(size, 0))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"
