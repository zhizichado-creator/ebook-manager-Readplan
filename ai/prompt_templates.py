"""Prompt builders for structured ebook analysis."""
from __future__ import annotations
import json

SYSTEM_PROMPT = (
    "你是本地电子书书库的分类助手。只返回一个 JSON 对象，不要 Markdown。"
    "格式：{\"category_path\":[\"一级\",\"二级\"],\"tags\":[\"标签\"],\"summary\":\"简介\"}。"
    "分类建议应简洁；标签最多 8 个；不要编造作者、出版社或书中事实。"
)


def classification_messages(book: dict, *, include_text: bool, text: str = "") -> list[dict[str, str]]:
    payload = {"title": book.get("title", ""), "author": book.get("author", ""),
               "publisher": book.get("publisher", ""), "format": book.get("format", ""),
               "metadata": book.get("metadata", {})}
    if include_text:
        payload["excerpt"] = text[:16_000]
        payload["local_suggestion"] = {"category_path": book.get("category_path", []),
                                        "tags": book.get("tags", []),
                                        "summary": book.get("summary", "")}
    return [{"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]
