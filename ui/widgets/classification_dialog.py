"""Background analysis worker and a review dialog for classification suggestions."""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import QThread, Signal, Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit,
    QPlainTextEdit, QTableWidget, QTableWidgetItem, QVBoxLayout,
)


class AnalysisWorker(QThread):
    completed = Signal(dict)

    def __init__(self, book: dict, mode: str, ai_client=None, allow_text_upload: bool = False):
        super().__init__()
        self.book = book
        self.mode = mode
        self.ai_client = ai_client
        self.allow_text_upload = allow_text_upload

    def run(self) -> None:
        from ai.classifier import analyze_local, analyze_sync
        try:
            result = analyze_sync(self.book, mode=self.mode, ai_client=self.ai_client,
                                  allow_text_upload=self.allow_text_upload)
            self.completed.emit({"suggestion": result})
        except Exception as error:
            if self.ai_client is not None:
                try:
                    result = analyze_local(self.book, mode="standard")
                    result["ai_error"] = str(error)
                    self.completed.emit({"suggestion": result})
                except Exception as fallback_error:
                    self.completed.emit({"error": f"AI 失败：{error}；本地回退也失败：{fallback_error}"})
            else:
                self.completed.emit({"error": str(error)})


class BatchAnalysisWorker(QThread):
    progress = Signal(int, int)
    completed = Signal(list)

    def __init__(self, books: list[dict], mode: str, ai_client=None, allow_text_upload: bool = False):
        super().__init__()
        self.books = books
        self.mode = mode
        self.ai_client = ai_client
        self.allow_text_upload = allow_text_upload

    def run(self) -> None:
        from ai.classifier import analyze_local, analyze_sync
        suggestions = []
        for index, book in enumerate(self.books, 1):
            try:
                result = analyze_sync(book, mode=self.mode, ai_client=self.ai_client,
                                      allow_text_upload=self.allow_text_upload)
            except Exception as error:
                try:
                    result = analyze_local(book, mode="standard")
                    result["ai_error"] = str(error)
                except Exception as fallback_error:
                    result = {"book_id": book.get("id"), "title": book.get("title", "未命名书籍"),
                              "category_path": ["其他"], "tags": [], "summary": "",
                              "confidence": 0.0, "analyzer": "local",
                              "parse_error": str(fallback_error), "ai_error": str(error)}
            # Parsed excerpts are only needed while making the request; avoid
            # retaining document text for every imported row in the review UI.
            result.pop("text", None)
            suggestions.append(result)
            self.progress.emit(index, len(self.books))
        self.completed.emit(suggestions)


class ClassificationReviewDialog(QDialog):
    """Show local/AI output before changing the user's library metadata."""

    def __init__(self, suggestion: dict[str, Any], parent=None):
        super().__init__(parent)
        self.suggestion = suggestion
        self.setWindowTitle("确认分类建议")
        self.setMinimumWidth(480)
        layout = QVBoxLayout(self)
        source = "AI 建议" if suggestion.get("analyzer") == "ai" else "本地分析建议"
        layout.addWidget(QLabel(f"{source} · 置信度 {suggestion.get('confidence', 0):.0%}"))
        form = QFormLayout()
        self.category = QLineEdit(" > ".join(suggestion.get("category_path", [])))
        self.category.setPlaceholderText("例如：计算机 > 人工智能")
        self.tags = QLineEdit(", ".join(suggestion.get("tags", [])))
        self.tags.setPlaceholderText("逗号分隔标签")
        self.summary = QPlainTextEdit(str(suggestion.get("summary", "")))
        self.summary.setMaximumHeight(110)
        form.addRow("分类路径", self.category)
        form.addRow("标签", self.tags)
        form.addRow("简介", self.summary)
        layout.addLayout(form)
        if suggestion.get("parse_error"):
            layout.addWidget(QLabel(f"文本解析提示：{suggestion['parse_error']}"))
        buttons = QDialogButtonBox()
        self.accept_button = buttons.addButton("接受建议", QDialogButtonBox.ButtonRole.AcceptRole)
        self.skip_button = buttons.addButton("跳过", QDialogButtonBox.ButtonRole.DestructiveRole)
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        self.accept_button.clicked.connect(self.accept)
        self.skip_button.clicked.connect(self.reject)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self) -> dict[str, Any]:
        return {
            "category_path": [part.strip() for part in self.category.text().replace("/", ">").split(">") if part.strip()],
            "tags": [part.strip().lstrip("#＃") for part in self.tags.text().replace("，", ",").split(",") if part.strip()],
            "summary": self.summary.toPlainText().strip(),
        }


class BatchClassificationReviewDialog(QDialog):
    """Editable import-review table for local and optional AI recommendations."""

    def __init__(self, suggestions: list[dict[str, Any]], parent=None):
        super().__init__(parent)
        self.suggestions = suggestions
        self.setWindowTitle("确认导入分类与标签建议")
        self.resize(960, 560)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("逐行检查并修改分类、标签和简介。保存后才会写入书库。"))
        self.table = QTableWidget(len(suggestions), 4)
        self.table.setHorizontalHeaderLabels(["书名", "建议分类路径", "建议标签（逗号分隔）", "简介"])
        self.table.setWordWrap(False)
        self.table.setColumnWidth(0, 210)
        self.table.setColumnWidth(1, 220)
        self.table.setColumnWidth(2, 260)
        for row, suggestion in enumerate(suggestions):
            title = QTableWidgetItem(str(suggestion.get("title", "")))
            title.setFlags(title.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 0, title)
            self.table.setItem(row, 1, QTableWidgetItem(" > ".join(suggestion.get("category_path", []))))
            self.table.setItem(row, 2, QTableWidgetItem(", ".join(suggestion.get("tags", []))))
            self.table.setItem(row, 3, QTableWidgetItem(str(suggestion.get("summary", ""))))
        layout.addWidget(self.table, 1)
        buttons = QDialogButtonBox()
        accept_all = buttons.addButton("接受全部 / 保存修改", QDialogButtonBox.ButtonRole.AcceptRole)
        skip = buttons.addButton("跳过分析", QDialogButtonBox.ButtonRole.DestructiveRole)
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        accept_all.clicked.connect(self.accept)
        skip.clicked.connect(self.reject)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self) -> list[dict[str, Any]]:
        result = []
        for row, suggestion in enumerate(self.suggestions):
            category_text = self.table.item(row, 1).text() if self.table.item(row, 1) else ""
            tags_text = self.table.item(row, 2).text() if self.table.item(row, 2) else ""
            summary = self.table.item(row, 3).text() if self.table.item(row, 3) else ""
            result.append({
                **suggestion,
                "review_values": {
                    "category_path": [part.strip() for part in category_text.replace("/", ">").split(">") if part.strip()],
                    "tags": [part.strip().lstrip("#＃") for part in tags_text.replace("，", ",").split(",") if part.strip()],
                    "summary": summary.strip(),
                },
            })
        return result
