"""Placeholder page for a future built-in reading experience."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget


class ReaderPage(QWidget):
    """Reserved view; books continue to open in the operating system reader."""

    backRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.addStretch(1)
        title = QLabel("阅读器页面预留")
        title.setStyleSheet("font-size: 22px; font-weight: 700;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        detail = QLabel("当前版本通过系统默认阅读器打开电子书。")
        detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        back = QPushButton("返回书库")
        back.clicked.connect(self.backRequested)
        layout.addWidget(title)
        layout.addWidget(detail)
        layout.addWidget(back, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addStretch(1)
