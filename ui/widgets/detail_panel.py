"""Selected-book information panel used beside the library view."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QProgressBar, QScrollArea,
    QVBoxLayout, QWidget,
)

from ui.cover_cache import load_cover_pixmap
from utils.file_utils import format_file_size


class DetailPanel(QWidget):
    openClicked = Signal()
    editClicked = Signal()
    favoriteClicked = Signal()
    deleteClicked = Signal()
    copyPathClicked = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("DetailPanel")
        self._build()
        self.set_book(None)

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea()
        self.scroll.setObjectName("DetailScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        content.setObjectName("DetailContent")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(18, 20, 18, 18)
        layout.setSpacing(10)
        title = QLabel("详情")
        title.setObjectName("SectionTitle")
        layout.addWidget(title)

        self.cover = QLabel("📚")
        self.cover.setObjectName("DetailCover")
        self.cover.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cover.setFixedSize(170, 255)
        self.cover.setScaledContents(False)
        layout.addWidget(self.cover, alignment=Qt.AlignmentFlag.AlignHCenter)

        self.title = QLabel("选择一本书查看详情")
        self.title.setObjectName("PageTitle")
        self.title.setWordWrap(True)
        layout.addWidget(self.title)
        self.author = QLabel("")
        self.author.setObjectName("Muted")
        layout.addWidget(self.author)

        self.rating = QLabel("")
        self.rating.setObjectName("Muted")
        layout.addWidget(self.rating)

        self.info = QLabel("")
        self.info.setWordWrap(True)
        self.info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.info)
        self.copy_path_button = QPushButton("复制文件路径")
        self.copy_path_button.setObjectName("Quiet")
        layout.addWidget(self.copy_path_button)

        self.progress_label = QLabel("")
        layout.addWidget(self.progress_label)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        layout.addWidget(self.progress)

        actions = QHBoxLayout()
        self.open_button = QPushButton("▶  打开")
        self.open_button.setObjectName("Primary")
        self.edit_button = QPushButton("✎ 编辑")
        actions.addWidget(self.open_button)
        actions.addWidget(self.edit_button)
        layout.addLayout(actions)

        secondary = QHBoxLayout()
        self.favorite_button = QPushButton("☆ 收藏")
        self.delete_button = QPushButton("删除记录")
        self.delete_button.setObjectName("Quiet")
        secondary.addWidget(self.favorite_button)
        secondary.addWidget(self.delete_button)
        layout.addLayout(secondary)

        self.description_title = QLabel("简介")
        self.description_title.setObjectName("SectionTitle")
        layout.addWidget(self.description_title)
        self.description = QLabel("选择书籍后显示简介。")
        self.description.setWordWrap(True)
        layout.addWidget(self.description)
        layout.addStretch(1)
        self.scroll.setWidget(content)
        outer.addWidget(self.scroll)

        self.open_button.clicked.connect(self.openClicked)
        self.edit_button.clicked.connect(self.editClicked)
        self.favorite_button.clicked.connect(self.favoriteClicked)
        self.delete_button.clicked.connect(self.deleteClicked)
        self.copy_path_button.clicked.connect(lambda: self.copyPathClicked.emit(self.current_path))

    def set_book(self, book: dict | None, category_name: str = "未分类", tags: list[str] | None = None) -> None:
        """Render book metadata; passing ``None`` shows the empty selection state."""
        enabled = book is not None
        self.current_path = str(book.get("path") or "") if book else ""
        for control in (self.open_button, self.edit_button, self.favorite_button, self.delete_button):
            control.setEnabled(enabled)
        self.copy_path_button.setEnabled(enabled)
        if not book:
            self.title.setText("选择一本书查看详情")
            self.author.setText("")
            self.rating.setText("")
            self.cover.setPixmap(QPixmap())
            self.cover.setText("📚")
            self.info.setText("")
            self.progress_label.setText("")
            self.progress.setValue(0)
            self.description.setText("从书库中选择一本书。")
            return

        self.cover.setText("")
        cover_path = str(book.get("cover") or book.get("cover_path") or "")
        pixmap = load_cover_pixmap(cover_path, self.cover.size(), rounded_radius=10) if cover_path else None
        if pixmap is not None:
            self.cover.setPixmap(pixmap)
        else:
            self.cover.setPixmap(QPixmap())
            self.cover.setText((str(book.get("title") or "書")[:1]))
        self.title.setText(str(book.get("title") or "未命名书籍"))
        self.author.setText(str(book.get("author") or "未知作者"))
        rating = float(book.get("rating") or 0)
        self.rating.setText(f"{'★' * round(rating)}{'☆' * (5 - round(rating))}  {rating:.1f}")
        size = book.get("size") if book.get("size") is not None else book.get("file_size", 0)
        status_map = {"unread": "未读", "reading": "在读", "finished": "已读", "completed": "已读"}
        status = status_map.get(book.get("read_status") or book.get("reading_status"), "未读")
        tag_text = "  ".join(f"#{tag}" for tag in (tags or [])) or "无"
        info_rows = [
            f"分类    {category_name}", f"标签    {tag_text}",
            f"格式    {(book.get('format') or '').upper() or '未知'}",
            f"大小    {format_file_size(int(size or 0))}",
            f"页数    {book.get('pages') or '—'}", f"语言    {book.get('language') or '—'}",
            f"出版    {book.get('publisher') or '—'} {book.get('publish_date') or ''}".strip(),
            f"ISBN    {book.get('isbn') or '—'}",
            f"添加    {book.get('created_time') or '—'}",
            f"打开    {book.get('last_open_time') or '—'}",
            f"状态    {status}", f"路径    {book.get('path') or '—'}",
        ]
        self.info.setText("\n".join(info_rows))
        progress = int(float(book.get("read_progress") or 0) * 100)
        self.progress.setValue(max(0, min(100, progress)))
        self.progress_label.setText(f"阅读进度    {progress}%")
        self.description.setText(str(book.get("description") or book.get("notes") or "暂无简介"))
        self.favorite_button.setText("★ 已收藏" if book.get("is_favorite") else "☆ 收藏")
