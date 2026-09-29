"""Layered local and optional AI classification for individual books."""
from __future__ import annotations
import asyncio
import json
import re
import sqlite3
import unicodedata
from pathlib import Path
from typing import Any

from parser.metadata import extract_book_metadata
from scanner.scanner import CATEGORY_RULES, TAG_RULES, _matches, classify_book


def analyze_local(book: dict, *, mode: str = "fast") -> dict[str, Any]:
    path = Path(str(book.get("path", "")))
    parsed = extract_book_metadata(path, include_text=mode in {"standard", "ai"})
    metadata = parsed.get("metadata", {})
    title = str(metadata.get("title") or book.get("title") or path.stem)
    author = str(metadata.get("author") or book.get("author") or "")
    categories, tags = classify_book(path, title)
    context = unicodedata.normalize("NFKC", " ".join(str(value) for value in (
        title, author, metadata.get("publisher"), metadata.get("subject"),
        parsed.get("text", ""), *parsed.get("keywords", []))).casefold())
    extra_categories = [name for name, keywords in CATEGORY_RULES
                        if any(_matches(context, keyword) for keyword in keywords)]
    extra_tags = [name for name, keywords in TAG_RULES
                  if any(_matches(context, keyword) for keyword in keywords)]
    matched_categories = list(dict.fromkeys(categories + extra_categories))
    category_path = matched_categories[:1]
    if category_path[0] == "计算机":
        child = next((value for value in matched_categories[1:]
                      if value in {"编程语言", "操作系统", "网络", "数据库", "前端", "后端", "移动开发",
                                   "云计算", "大数据", "区块链", "人工智能", "算法"}), None)
        if child:
            category_path.append(child)
    tags = list(dict.fromkeys(tags + extra_tags + matched_categories[1:] + parsed.get("keywords", [])[:8]))
    confidence = 0.82 if matched_categories != ["其他"] else 0.2
    suggestion = {"book_id": book.get("id"), "title": title, "author": author,
            "category_path": category_path, "tags": tags[:12],
            "format": book.get("format", ""),
            "summary": str(parsed.get("abstract") or metadata.get("subject") or parsed.get("text", "")[:600])[:2000],
            "confidence": confidence,
            "metadata": metadata, "text": parsed.get("text", ""), "analyzer": "local",
            "parse_error": parsed.get("error", "")}
    return _apply_feedback(book, suggestion, context)


def _similarity_tokens(text: str) -> set[str]:
    text = unicodedata.normalize("NFKC", text).casefold()
    words = set(re.findall(r"[a-z0-9+#.-]{2,}", text))
    for segment in re.findall(r"[\u4e00-\u9fff]+", text):
        words.update(segment[index:index + 2] for index in range(max(0, len(segment) - 1)))
        words.update(segment[index:index + 3] for index in range(max(0, len(segment) - 2)))
    return words


def _apply_feedback(book: dict, suggestion: dict, context: str) -> dict:
    """Prefer a user's corrected category for lexically similar future books."""
    try:
        from database.db import get_connection
        connection = get_connection()
        try:
            rows = connection.execute(
                "SELECT f.chosen_category_id,f.chosen_tags,b.title,b.path "
                "FROM classification_feedback f JOIN books b ON b.id=f.book_id "
                "WHERE (f.chosen_category_id IS NOT NULL AND f.chosen_category_id IS NOT f.suggested_category_id) "
                "OR f.chosen_tags<>f.suggested_tags "
                "ORDER BY f.created_time DESC LIMIT 300"
            ).fetchall()
            metadata = suggestion.get("metadata", {})
            target = _similarity_tokens(" ".join(str(value) for value in (
                suggestion.get("title", ""), suggestion.get("author", ""),
                metadata.get("publisher", ""), metadata.get("subject", ""), book.get("path", ""))))
            best = None
            best_score = 0.0
            for row in rows:
                prior = _similarity_tokens(str(row["title"] or "") + " " + str(row["path"] or ""))
                if not prior or not target:
                    continue
                score = len(target & prior) / max(1, len(target | prior))
                if score > best_score:
                    best, best_score = row, score
            if best is None or best_score < 0.22:
                return suggestion
            if best["chosen_category_id"] is not None:
                current_category = connection.execute(
                    "SELECT id FROM categories WHERE normalized_name=?",
                    (suggestion.get("category_path", [""])[-1].casefold(),),
                ).fetchone()
                if current_category is None or int(current_category[0]) != int(best["chosen_category_id"]):
                    category_id = int(best["chosen_category_id"])
                    names: list[str] = []
                    seen: set[int] = set()
                    while category_id not in seen:
                        seen.add(category_id)
                        row = connection.execute("SELECT name,parent_id FROM categories WHERE id=?", (category_id,)).fetchone()
                        if row is None:
                            break
                        names.append(str(row["name"]))
                        if row["parent_id"] is None:
                            break
                        category_id = int(row["parent_id"])
                    if names:
                        suggestion["category_path"] = list(reversed(names))
            try:
                learned_tags = json.loads(best["chosen_tags"] or "[]")
            except (TypeError, json.JSONDecodeError):
                learned_tags = []
            suggestion["tags"] = list(dict.fromkeys(str(tag) for tag in learned_tags))[:12]
            suggestion["confidence"] = max(float(suggestion["confidence"]), min(0.86, 0.5 + best_score))
            suggestion["feedback_match"] = round(best_score, 3)
            return suggestion
        finally:
            connection.close()
    except (OSError, sqlite3.Error):
        return suggestion


async def analyze_book(book: dict, *, mode: str, ai_client=None, allow_text_upload: bool = False) -> dict[str, Any]:
    suggestion = analyze_local(book, mode=mode)
    if mode != "ai" or ai_client is None:
        return suggestion
    from ai.prompt_templates import classification_messages
    messages = classification_messages(suggestion, include_text=allow_text_upload,
                                       text=suggestion.get("text", ""))
    raw = await ai_client.complete(messages)
    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", raw)
        if not match:
            raise RuntimeError("AI 未返回有效 JSON")
        result = json.loads(match.group(0))
    path = result.get("category_path", [])
    if isinstance(path, str):
        path = [item.strip() for item in path.split(">") if item.strip()]
    if not isinstance(path, list):
        path = []
    tags = result.get("tags", [])
    if isinstance(tags, str):
        tags = [part.strip() for part in tags.replace("，", ",").split(",") if part.strip()]
    if not isinstance(tags, list):
        tags = []
    return {**suggestion, "category_path": [str(item) for item in path[:4]],
            "tags": [str(item).lstrip("# ") for item in tags[:12]],
            "summary": str(result.get("summary", ""))[:2000], "confidence": 0.9,
            "analyzer": "ai"}


def analyze_sync(book: dict, *, mode: str, ai_client=None, allow_text_upload: bool = False) -> dict[str, Any]:
    return asyncio.run(analyze_book(book, mode=mode, ai_client=ai_client,
                                    allow_text_upload=allow_text_upload))
