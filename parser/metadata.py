"""Single entry point for local format metadata and text parsing."""
from __future__ import annotations
from pathlib import Path
from typing import Any


def extract_book_metadata(path: str | Path, *, include_text: bool = True) -> dict[str, Any]:
    source = Path(path)
    extension = source.suffix.casefold()
    if extension == ".pdf":
        from parser.pdf_parser import parse_pdf
        result = parse_pdf(source) if include_text else parse_pdf(source, max_pages=0)
    elif extension == ".epub":
        from parser.epub_parser import parse_epub
        result = parse_epub(source) if include_text else parse_epub(source, max_chapters=0)
    else:
        result = {"metadata": {}, "text": "", "keywords": []}
    metadata = result.setdefault("metadata", {})
    metadata.setdefault("title", source.stem)
    metadata.setdefault("author", "")
    result["metadata"] = metadata
    return result
