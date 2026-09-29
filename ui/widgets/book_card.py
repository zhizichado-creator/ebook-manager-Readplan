"""Compact book tile for the cover grid view."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal, QPoint, QRect, QParallelAnimationGroup, QPropertyAnimation, QEasingCurve, QTimer
from PySide6.QtGui import QDrag, QColor, QCursor, QPainter, QPen, QFontMetrics
from PySide6.QtWidgets import (
    QApplication, QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel,
    QProgressBar, QPushButton, QVBoxLayout, QWidget, QStyledItemDelegate, QStyle,
)
from PySide6.QtCore import QMimeData
from ui.style import get_colors
from ui.cover_cache import cover_loader, request_cover_pixmap


class BookCard(QFrame):
    clicked = Signal(int)
    doubleClicked = Signal(int)
    favoriteToggled = Signal(int, bool)
    openRequested = Signal(int)
    editRequested = Signal(int)

    def __init__(self, book: dict, category_name: str = "未分类", tags: list[str] | None = None,
                 category_icon: str = "•", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.book = book
        self.book_id = int(book["id"])
        self._press_position: QPoint | None = None
        self.setObjectName("BookCard")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setFixedWidth(178)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(5)
        shadow.setOffset(0, 1)
        self.setGraphicsEffect(shadow)
        self.apply_theme(str(QApplication.instance().property("readplanTheme") or "light"))
        self._shadow_animation: QParallelAnimationGroup | None = None
        self._hovered = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(9, 9, 9, 10)
        layout.setSpacing(6)

        self.cover = QLabel()
        self.cover.setObjectName("BookCover")
        self.cover.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cover.setFixedSize(158, 237)
        placeholder = QVBoxLayout(self.cover)
        placeholder.setContentsMargins(8, 18, 8, 12)
        self.placeholder_title = QLabel(str(self.book.get("title") or "书").strip()[:1] or "书")
        self.placeholder_title.setObjectName("CoverInitial")
        self.placeholder_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.placeholder_title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        placeholder.addWidget(self.placeholder_title, 1)
        self.placeholder_category = QLabel(category_icon or "•")
        self.placeholder_category.setObjectName("CoverCategoryIcon")
        self.placeholder_category.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.placeholder_category.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        placeholder.addWidget(self.placeholder_category)
        self._set_cover(str(book.get("cover") or book.get("cover_path") or ""))
        layout.addWidget(self.cover, alignment=Qt.AlignmentFlag.AlignHCenter)
        self.favorite = QPushButton("★" if book.get("is_favorite") else "☆", self.cover)
        self.favorite.setObjectName("FavoriteButton")
        self.favorite.setFixedSize(30, 30)
        self.favorite.setGeometry(122, 7, 30, 30)
        self.favorite.setToolTip("取消收藏" if book.get("is_favorite") else "加入收藏")
        self.favorite.clicked.connect(self._toggle_favorite)
        self.favorite.hide()
        self.open_button = QPushButton("▶ 打开", self.cover)
        self.open_button.setObjectName("CardOpen")
        self.open_button.setGeometry(14, 198, 64, 30)
        self.open_button.hide()
        self.open_button.clicked.connect(lambda: self.openRequested.emit(self.book_id))
        self.edit_button = QPushButton("✎ 编辑", self.cover)
        self.edit_button.setObjectName("CardEdit")
        self.edit_button.setGeometry(80, 198, 64, 30)
        self.edit_button.hide()
        self.edit_button.clicked.connect(lambda: self.editRequested.emit(self.book_id))

        title_row = QHBoxLayout()
        self.title_label = QLabel(str(book.get("title") or "未命名书籍"))
        self.title_label.setObjectName("CardTitle")
        self.title_label.setWordWrap(False)
        self.title_label.setToolTip(self.title_label.text())
        self.title_label.setText(self.title_label.fontMetrics().elidedText(
            self.title_label.text(), Qt.TextElideMode.ElideRight, 116))
        title_row.addWidget(self.title_label, 1)
        layout.addLayout(title_row)

        author = QLabel(str(book.get("author") or "未知作者"))
        author.setObjectName("Muted")
        author.setText(author.fontMetrics().elidedText(author.text(), Qt.TextElideMode.ElideRight, 150))
        layout.addWidget(author)

        chip_text = f"{category_name}"
        if tags:
            chip_text += "  ·  " + " #".join(tags[:2])
        chips = QLabel(chip_text)
        chips.setObjectName("CardChips")
        chips.setText(chips.fontMetrics().elidedText(chips.text(), Qt.TextElideMode.ElideRight, 152))
        layout.addWidget(chips)

        progress = max(0, min(100, int(float(book.get("read_progress") or 0) * 100)))
        progress_row = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(progress)
        self.progress.setTextVisible(False)
        progress_row.addWidget(self.progress, 1)
        progress_row.addWidget(QLabel(f"{progress}%"))
        layout.addLayout(progress_row)

    def apply_theme(self, theme: str) -> None:
        """Update shadow contrast to match the active application palette."""
        effect = self.graphicsEffect()
        if isinstance(effect, QGraphicsDropShadowEffect):
            effect.setColor(QColor(0, 0, 0, get_colors(theme)["shadow_alpha"]))

    def _set_cover(self, cover_path: str) -> None:
        self._cover_source = cover_path
        self._cover_cache_key = None
        if cover_path:
            pixmap, cache_key = request_cover_pixmap(
                cover_path, self.cover.size(), rounded_radius=9, on_ready=self._on_cover_ready
            )
            self._cover_cache_key = cache_key
            if pixmap is not None and not pixmap.isNull():
                self._show_cover(pixmap)
                return
        initial = str(self.book.get("title") or "书").strip()[:1] or "书"
        self.placeholder_title.setText(initial)
        self.placeholder_title.show()
        self.placeholder_category.show()
        self.cover.setToolTip("暂无封面")

    def _on_cover_ready(self, cache_key: str) -> None:
        if not cache_key or cache_key != self._cover_cache_key:
            return
        pixmap, _key = request_cover_pixmap(
            self._cover_source, self.cover.size(), rounded_radius=9
        )
        if pixmap is not None and not pixmap.isNull():
            self._show_cover(pixmap)

    def _show_cover(self, pixmap) -> None:
        self.cover.setPixmap(pixmap)
        self.placeholder_title.hide()
        self.placeholder_category.hide()
        self.cover.setToolTip("")

    def _toggle_favorite(self) -> None:
        value = not bool(self.book.get("is_favorite"))
        self.favorite.setText("★" if value else "☆")
        self.book["is_favorite"] = int(value)
        self.favorite.setToolTip("取消收藏" if value else "加入收藏")
        self.favoriteToggled.emit(self.book_id, value)

    def _animate_hover(self, hovering: bool) -> None:
        effect = self.graphicsEffect()
        if not isinstance(effect, QGraphicsDropShadowEffect):
            return
        if self._shadow_animation:
            self._shadow_animation.stop()
        animation = QParallelAnimationGroup(self)
        blur = QPropertyAnimation(effect, b"blurRadius", animation)
        blur.setDuration(220)
        blur.setEasingCurve(QEasingCurve.Type.OutCubic)
        blur.setStartValue(effect.blurRadius())
        blur.setEndValue(7 if hovering else 5)
        offset = QPropertyAnimation(effect, b"yOffset", animation)
        offset.setDuration(220)
        offset.setEasingCurve(QEasingCurve.Type.OutCubic)
        offset.setStartValue(effect.yOffset())
        offset.setEndValue(1.5 if hovering else 1)
        animation.addAnimation(blur)
        animation.addAnimation(offset)
        self._shadow_animation = animation
        animation.start()

    def _sync_hover(self) -> None:
        # Child buttons and the cover should count as part of the same card.
        # This prevents enter/leave events between child widgets from restarting
        # the hover animation and making the card appear to flicker.
        hovering = self.rect().contains(self.mapFromGlobal(QCursor.pos()))
        if hovering == self._hovered:
            return
        self._hovered = hovering
        self._animate_hover(hovering)
        self.favorite.setVisible(hovering)
        self.open_button.setVisible(hovering)
        self.edit_button.setVisible(hovering)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_position = event.position().toPoint()
            self.clicked.emit(self.book_id)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if (self._press_position is not None
                and event.buttons() & Qt.MouseButton.LeftButton
                and (event.position().toPoint() - self._press_position).manhattanLength() >= 10):
            drag = QDrag(self)
            data = QMimeData()
            data.setData("application/x-ebook-book-ids", str(self.book_id).encode("ascii"))
            drag.setMimeData(data)
            drag.exec(Qt.DropAction.MoveAction)
            self._press_position = None
            return
        super().mouseMoveEvent(event)

    def enterEvent(self, event) -> None:
        QTimer.singleShot(0, self._sync_hover)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        QTimer.singleShot(0, self._sync_hover)
        super().leaveEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.doubleClicked.emit(self.book_id)
        super().mouseDoubleClickEvent(event)


class BookCardDelegate(QStyledItemDelegate):
    """Paint non-widget book cards so large libraries do not retain 10k widgets."""

    DATA_ROLE = Qt.ItemDataRole.UserRole + 1

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        loader = cover_loader()
        if loader is not None:
            loader.ready.connect(self._cover_ready)

    def _cover_ready(self, _cache_key: str) -> None:
        view = self.parent()
        if view is not None and hasattr(view, "viewport"):
            view.viewport().update()

    def paint(self, painter: QPainter, option, index) -> None:
        # Real BookCard widgets are layered above item painting by Qt. Do not
        # draw a second delegate card underneath those visible widgets.
        view = self.parent()
        if view is not None and hasattr(view, "indexWidget") and view.indexWidget(index) is not None:
            return
        data = index.data(self.DATA_ROLE)
        if not isinstance(data, dict):
            return super().paint(painter, option, index)

        theme = str(QApplication.instance().property("readplanTheme") or "light")
        colors = get_colors(theme)
        rect = option.rect.adjusted(2, 2, -2, -2)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        border = colors["accent"] if hovered or selected else colors["border"]

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(QColor(border), 2 if selected else 1))
        painter.setBrush(QColor(colors["hover"] if hovered else colors["surface"]))
        painter.drawRoundedRect(rect, 11, 11)

        cover_rect = QRect(rect.center().x() - 79, rect.top() + 9, 158, 237)
        book = data.get("book") or {}
        cover_path = str(book.get("cover") or book.get("cover_path") or "")
        pixmap = request_cover_pixmap(
            cover_path, cover_rect.size(), rounded_radius=9
        )[0] if cover_path else None
        if pixmap is not None and not pixmap.isNull():
            painter.drawPixmap(cover_rect, pixmap)
        else:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(colors["selected"]))
            painter.drawRoundedRect(cover_rect, 9, 9)
            initial_font = option.font
            initial_font.setPixelSize(max(36, initial_font.pixelSize() * 3))
            initial_font.setBold(True)
            painter.setPen(QColor(colors["accent"]))
            painter.setFont(initial_font)
            painter.drawText(cover_rect, Qt.AlignmentFlag.AlignCenter,
                             str(book.get("title") or "书").strip()[:1] or "书")
            painter.setPen(QColor(colors["text_sub"]))
            icon_font = option.font
            icon_font.setPixelSize(max(16, icon_font.pixelSize()))
            painter.setFont(icon_font)
            painter.drawText(cover_rect.adjusted(0, 35, 0, 0), Qt.AlignmentFlag.AlignHCenter
                             | Qt.AlignmentFlag.AlignBottom, str(data.get("category_icon") or "•"))

        text_left = rect.left() + 10
        text_width = rect.width() - 20
        title = str(book.get("title") or "未命名书籍")
        author = str(book.get("author") or "未知作者")
        chip = str(data.get("category") or "未分类")
        tags = data.get("tags") or []
        if tags:
            chip += "  ·  " + " #".join(tags[:2])
        metrics = QFontMetrics(option.font)
        title_font = option.font
        title_font.setBold(True)
        title_y = cover_rect.bottom() + metrics.height() + 7
        author_y = title_y + metrics.height() + 5
        chips_y = author_y + metrics.height() + 5
        painter.setPen(QColor(colors["text"]))
        painter.setFont(title_font)
        painter.drawText(text_left, title_y,
                         QFontMetrics(title_font).elidedText(title, Qt.TextElideMode.ElideRight, text_width))
        painter.setFont(option.font)
        painter.setPen(QColor(colors["text_sub"]))
        painter.drawText(text_left, author_y,
                         metrics.elidedText(author, Qt.TextElideMode.ElideRight, text_width))
        painter.setPen(QColor(colors["accent"]))
        painter.drawText(text_left, chips_y,
                         metrics.elidedText(chip, Qt.TextElideMode.ElideRight, text_width))

        progress = max(0, min(100, int(float(book.get("read_progress") or 0) * 100)))
        bar_rect = rect.adjusted(10, 0, -34, 0)
        bar_rect.setTop(chips_y + metrics.height() + 4)
        bar_rect.setHeight(5)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(colors["border"]))
        painter.drawRoundedRect(bar_rect, 2, 2)
        progress_rect = bar_rect.adjusted(0, 0, -bar_rect.width() * (100 - progress) // 100, 0)
        painter.setBrush(QColor(colors["accent"]))
        painter.drawRoundedRect(progress_rect, 2, 2)
        painter.setPen(QColor(colors["text_sub"]))
        painter.drawText(rect.right() - 30, bar_rect.top() + metrics.height(), f"{progress}%")
        painter.restore()
