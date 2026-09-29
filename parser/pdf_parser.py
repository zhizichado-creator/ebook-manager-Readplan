"""Extract a bounded amount of text and metadata from PDF files locally."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any


def parse_pdf(path: str | Path, max_pages: int = 8, max_chars: int = 24_000) -> dict[str, Any]:
    """Read document metadata and initial pages; never uploads document data."""
    source = Path(path)
    try:
        from pypdf import PdfReader
    except ImportError:
        return {"metadata": {}, "text": "", "keywords": [], "error": "缺少 pypdf 依赖"}
    try:
        reader = PdfReader(str(source), strict=False)
        info = reader.metadata
        metadata = {
            "title": str(getattr(info, "title", "") or "").strip(),
            "author": str(getattr(info, "author", "") or "").strip(),
            "publisher": str(getattr(info, "producer", "") or "").strip(),
            "subject": str(getattr(info, "subject", "") or "").strip(),
            "embedded_keywords": str(getattr(info, "keywords", "") or "").strip(),
            "pages": len(reader.pages),
        }
        pieces: list[str] = []
        for page in reader.pages[:max(0, max_pages)]:
            try:
                text = page.extract_text() or ""
            except Exception:
                text = ""
            if text:
                pieces.append(text)
            if sum(map(len, pieces)) >= max_chars:
                break
        text = "\n".join(pieces)[:max_chars]
        abstract = _section(text, ("abstract", "摘要", "内容简介"), 1800)
        toc = _section(text, ("contents", "table of contents", "目录"), 1800)
        embedded = [value.strip() for value in re.split(r"[,;，；]", metadata["embedded_keywords"]) if value.strip()]
        return {"metadata": metadata, "text": text, "first_page": pieces[0][:6000] if pieces else "",
                "abstract": abstract, "toc": toc,
                "keywords": list(dict.fromkeys(embedded + _keywords(text)))[:30]}
    except Exception as error:
        return {"metadata": {}, "text": "", "keywords": [], "error": str(error)}


def _keywords(text: str, limit: int = 20) -> list[str]:
    """Extract lightweight candidate terms for display and local matching."""
    import re
    from collections import Counter
    terms = re.findall(r"[A-Za-z][A-Za-z0-9+#.-]{2,}|[\u4e00-\u9fff]{2,8}", text.casefold())
    stop = {"the", "and", "for", "with", "this", "that", "from", "are", "was", "本书", "作者", "我们"}
    return [term for term, _ in Counter(t for t in terms if t not in stop).most_common(limit)]


def _section(text: str, headings: tuple[str, ...], limit: int) -> str:
    import re
    for heading in headings:
        match = re.search(rf"{re.escape(heading)}\s*[:：]?\s*", text, flags=re.IGNORECASE)
        if match:
            value = text[match.end():match.end() + limit]
            next_heading = re.search(r"\n\s*(?:目录|contents|关键词|keywords|abstract|摘要)\b", value, re.IGNORECASE)
            return (value[:next_heading.start()] if next_heading else value).strip()
    return ""
