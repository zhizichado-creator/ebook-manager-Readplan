"""Three-pane ebook library window with search, filters, views, and detail editing."""
from __future__ import annotations

import json
import csv
import re
import sqlite3
import shutil
from contextlib import closing
from pathlib import Path
from typing import Any

from PySide6.QtCore import QEvent, QPoint, Qt, QSettings, QThread, Signal, QUrl, QSize, QTimer
from PySide6.QtGui import QAction, QDesktopServices, QIcon, QPixmap, QColor, QPainter, QStandardItem, QStandardItemModel, QGuiApplication, QPixmapCache
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QInputDialog,
    QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QFrame,
    QCompleter, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
    QMessageBox, QMenu, QProgressBar, QPushButton, QSlider, QSpinBox, QSplitter, QStackedWidget, QGroupBox,
    QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget, QRadioButton,
)

from database.db import Database, utc_now
from scanner.scanner import DEFAULT_LIBRARY_PATH, backfill_missing_covers, scan_directory_stats, scan_files_stats
from ai.ai_client import AIClient
from tag.service import ensure_tag, set_book_tags
from ui.widgets.classification_dialog import (
    AnalysisWorker, BatchAnalysisWorker, ClassificationReviewDialog,
)
from ui.widgets.tag_dialog import BookTagDialog, TagLibraryDialog
from utils.secret_store import get_secret, set_secret
from utils.font_manager import DEFAULT_FONT_SIZE, FONT_OPTIONS, SYSTEM_FONT, FontManager
from utils.smooth_scroll import install_smooth_scrolling
from ui.style import get_colors, get_stylesheet
from ui.cover_cache import load_cover_pixmap
from ui.widgets.book_card import BookCard, BookCardDelegate
from ui.widgets.detail_panel import DetailPanel
from ui.widgets.sidebar import LibrarySidebar
from utils.file_utils import format_file_size


class ScanWorker(QThread):
    """Run recursive scans on a separate SQLite connection and worker thread."""
    completed = Signal(dict)

    def __init__(self, database_path: str, target: str | list[str]) -> None:
        super().__init__()
        self.database_path = database_path
        self.target = target

    def run(self) -> None:
        database = None
        try:
            database = Database(self.database_path)
            connection = database.connection
            if isinstance(self.target, list):
                candidate_paths = set()
                for value in self.target:
                    try:
                        candidate_paths.add(str(Path(value).expanduser().resolve()))
                    except (OSError, RuntimeError):
                        pass
                before: set[str] = set()
                candidates = list(candidate_paths)
                for offset in range(0, len(candidates), 500):
                    part = candidates[offset:offset + 500]
                    marks = ",".join("?" for _ in part)
                    before.update(str(row[0]) for row in connection.execute(
                        f"SELECT path FROM books WHERE path IN ({marks})", part))
            else:
                root = Path(self.target).expanduser().resolve()
                before = set()
                for row in connection.execute("SELECT path FROM books"):
                    try:
                        Path(str(row[0])).resolve().relative_to(root)
                        before.add(str(row[0]))
                    except (OSError, RuntimeError, ValueError):
                        continue
            if isinstance(self.target, list):
                result = scan_files_stats(database, self.target)
            else:
                result = scan_directory_stats(database, self.target)
            if isinstance(self.target, list):
                query_paths = candidate_paths
            else:
                query_paths = set()
                for row in connection.execute("SELECT path FROM books"):
                    try:
                        Path(str(row[0])).resolve().relative_to(root)
                        query_paths.add(str(row[0]))
                    except (OSError, RuntimeError, ValueError):
                        continue
            imported_ids: list[int] = []
            values = list(query_paths - before)
            for offset in range(0, len(values), 500):
                part = values[offset:offset + 500]
                if not part:
                    continue
                marks = ",".join("?" for _ in part)
                imported_ids.extend(int(row[0]) for row in connection.execute(
                    f"SELECT id FROM books WHERE path IN ({marks})", part))
            result["imported_ids"] = imported_ids
        except Exception as error:
            result = {"fatal_error": str(error)}
        finally:
            if database is not None:
                database.close()
        self.completed.emit(result)


class CoverBackfillWorker(QThread):
    """Fill missing cover thumbnails for indexed books without blocking the UI."""
    completed = Signal(dict)

    def __init__(self, database_path: str) -> None:
        super().__init__()
        self.database_path = database_path

    def run(self) -> None:
        database = None
        try:
            database = Database(self.database_path)
            result = backfill_missing_covers(database)
        except Exception as error:
            result = {"error": str(error), "generated": 0, "errors": 1}
        finally:
            if database is not None:
                database.close()
        self.completed.emit(result)


class AIConnectionWorker(QThread):
    completed = Signal(bool, str)

    def __init__(self, base_url: str, model: str, api_key: str):
        super().__init__()
        self.client = AIClient(base_url, model, api_key)

    def run(self) -> None:
        import asyncio
        try:
            response = asyncio.run(self.client.test_connection())
            self.completed.emit(True, response[:300])
        except Exception as error:
            self.completed.emit(False, str(error))


class BookEditDialog(QDialog):
    """Small metadata editor for the fields available in the current schema."""
    def __init__(self, book: dict, categories: list[dict], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("编辑书籍信息")
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.title_edit = QLineEdit(str(book.get("title") or ""))
        self.author_edit = QLineEdit(str(book.get("author") or ""))
        self.category = QComboBox()
        self.category.addItem("未分类", None)
        for category in categories:
            self.category.addItem(category["name"], category["id"])
        index = self.category.findData(book.get("category_id"))
        self.category.setCurrentIndex(max(index, 0))
        self.status = QComboBox()
        for label, value in (("未读", "unread"), ("在读", "reading"), ("已读", "finished")):
            self.status.addItem(label, value)
        current_status = book.get("read_status") or book.get("reading_status") or "unread"
        if current_status == "completed":
            current_status = "finished"
        self.status.setCurrentIndex(max(self.status.findData(current_status), 0))
        self.rating = QSpinBox()
        self.rating.setRange(0, 5)
        self.rating.setValue(round(float(book.get("rating") or 0)))
        self.tags_edit = QLineEdit()
        self.tags_edit.setPlaceholderText("用逗号分隔多个标签")
        self.notes = QTextEdit()
        self.notes.setPlainText(str(book.get("notes") or book.get("description") or ""))
        self.notes.setMaximumHeight(100)
        form.addRow("书名", self.title_edit)
        form.addRow("作者", self.author_edit)
        form.addRow("分类", self.category)
        form.addRow("阅读状态", self.status)
        form.addRow("评分", self.rating)
        form.addRow("标签", self.tags_edit)
        form.addRow("备注 / 简介", self.notes)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self) -> dict[str, Any]:
        return {
            "title": self.title_edit.text().strip(), "author": self.author_edit.text().strip(),
            "category_id": self.category.currentData(), "read_status": self.status.currentData(),
            "rating": float(self.rating.value()), "notes": self.notes.toPlainText().strip(),
            "tags": [tag.strip() for tag in self.tags_edit.text().split(",") if tag.strip()],
        }


class BookCategoriesDialog(QDialog):
    """Choose multiple categories for one or more books and manage categories."""

    def __init__(self, connection: sqlite3.Connection, book_ids: list[int], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.connection = connection
        self.book_ids = book_ids
        self.setWindowTitle("添加 / 修改分类")
        self.setMinimumSize(380, 440)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("勾选要应用到所选书籍的分类："))
        self.categories = QListWidget()
        self.categories.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        layout.addWidget(self.categories, 1)
        actions = QHBoxLayout()
        create = QPushButton("＋ 新建分类")
        delete = QPushButton("删除选中分类")
        actions.addWidget(create)
        actions.addWidget(delete)
        layout.addLayout(actions)
        create.clicked.connect(self.create_category)
        delete.clicked.connect(self.delete_categories)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.refresh_categories()

    def refresh_categories(self) -> None:
        selected: set[int] = set()
        if self.book_ids:
            placeholders = ",".join("?" for _ in self.book_ids)
            selected.update(int(row[0]) for row in self.connection.execute(
                f"SELECT category_id FROM books WHERE id IN ({placeholders}) AND category_id IS NOT NULL",
                self.book_ids,
            ))
            selected.update(int(row[0]) for row in self.connection.execute(
                f"SELECT DISTINCT category_id FROM book_categories WHERE book_id IN ({placeholders})",
                self.book_ids,
            ))
        self.categories.clear()
        for row in self.connection.execute("SELECT id,name FROM categories ORDER BY sort_order,name COLLATE NOCASE"):
            item = QListWidgetItem(row["name"])
            item.setData(Qt.ItemDataRole.UserRole, int(row["id"]))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsSelectable)
            item.setCheckState(Qt.CheckState.Checked if int(row["id"]) in selected else Qt.CheckState.Unchecked)
            self.categories.addItem(item)

    def create_category(self) -> None:
        name, accepted = QInputDialog.getText(self, "新建分类", "分类名称")
        name = name.strip()
        if not accepted or not name:
            return
        try:
            self.connection.execute(
                "INSERT INTO categories(name,normalized_name) VALUES (?,?)", (name, name.casefold())
            )
            self.connection.commit()
            self.refresh_categories()
        except sqlite3.IntegrityError:
            QMessageBox.warning(self, "分类已存在", f"“{name}”已经存在。")

    def delete_categories(self) -> None:
        ids = [int(item.data(Qt.ItemDataRole.UserRole)) for item in self.categories.selectedItems()]
        if not ids:
            return
        names = [self.categories.item(self.categories.row(item)).text() for item in self.categories.selectedItems()]
        answer = QMessageBox.question(
            self, "删除分类", f"删除分类“{'、'.join(names)}”？书籍文件不会被删除。"
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        with self.connection:
            self.connection.executemany("DELETE FROM categories WHERE id=?", [(value,) for value in ids])
        self.refresh_categories()

    def selected_category_ids(self) -> list[int]:
        return [int(self.categories.item(index).data(Qt.ItemDataRole.UserRole))
                for index in range(self.categories.count())
                if self.categories.item(index).checkState() == Qt.CheckState.Checked]


class MainWindow(QMainWindow):
    PAGE_SIZE = 50

    def __init__(self, database: Database) -> None:
        super().__init__()
        # Keep rendered cover thumbnails warm while placing a bounded limit on memory.
        QPixmapCache.setCacheLimit(64 * 1024)
        self.database = database
        self.connection = database.connection
        self.font_manager = FontManager(self.connection)
        self.font_manager.load_font_setting()
        self.font_manager.apply_font(QApplication.instance())
        self.qsettings = QSettings("Readplan", "ebook_manager")
        self.current_filter_kind = "group"
        self.current_filter_value: Any = "all"
        self.current_filter_title = "全部书籍"
        self.selected_tags: list[int] = []
        self.tag_logic = "OR"
        self.selected_id: int | None = None
        self.pending_import_ids: list[int] = []
        self.current_page = 0
        self.total_filtered_books = 0
        self.loaded_count = 0
        self._loading_more = False
        self.books: list[dict[str, Any]] = []
        self.book_by_id: dict[int, dict[str, Any]] = {}
        self._grid_cards: dict[int, BookCard] = {}
        self._grid_row_by_id: dict[int, int] = {}
        self._last_virtual_center_row: int | None = None
        self._last_virtual_columns: int | None = None
        self.theme = self._get_setting("theme", "light")
        self.view_mode = self._get_setting("view_mode", "grid")
        self.collapsed_sidebar = self._get_setting("sidebar_collapsed", "0") == "1"
        self._loading = False

        self.setWindowTitle("Readplan · 电子书书库")
        self.setMinimumSize(960, 620)
        self.resize(1440, 900)
        self.setAcceptDrops(True)
        self._build_ui()
        self.smooth_scroll_manager = install_smooth_scrolling(QApplication.instance(), self)
        self._build_actions()
        self._restore_window()
        self._connect_signals()
        QGuiApplication.styleHints().colorSchemeChanged.connect(self._on_system_theme_changed)
        self._apply_theme()
        self.refresh_books()
        # Existing libraries may predate cover extraction. Backfill them after
        # the first paint, while keeping the UI responsive on large libraries.
        QTimer.singleShot(350, self.start_cover_backfill)
        if self._get_setting("auto_scan", "0") == "1":
            default_folder = self._get_setting("default_library_path", str(DEFAULT_LIBRARY_PATH)).strip()
            if default_folder:
                QTimer.singleShot(500, lambda: self.start_scan(default_folder))

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("AppRoot")
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        toolbar = QFrame()
        toolbar.setObjectName("Toolbar")
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(18, 12, 18, 12)
        toolbar_layout.setSpacing(10)
        self.search = QLineEdit()
        self.search.setObjectName("SearchBox")
        self.search.setPlaceholderText("搜索书名、作者、标签、ISBN…   Ctrl+K")
        self.search.setMinimumWidth(320)
        self.search.setMaximumWidth(650)
        self.completer_model = QStandardItemModel(self)
        self.completer = QCompleter(self.completer_model, self.search)
        self.completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.search.setCompleter(self.completer)
        self.completer.activated[str].connect(self._completion_activated)
        toolbar_layout.addWidget(self.search, 1)
        self.sort_combo = QComboBox()
        for label, key in (("最近添加", "created"), ("书名", "title"), ("作者", "author"),
                           ("最近打开", "last_open"), ("评分", "rating")):
            self.sort_combo.addItem(label, key)
        toolbar_layout.addWidget(self.sort_combo)
        self.view_button = QPushButton("▦ 网格")
        self.view_button.setObjectName("Quiet")
        toolbar_layout.addWidget(self.view_button)
        self.import_button = QPushButton("＋ 导入")
        self.import_button.setObjectName("Primary")
        toolbar_layout.addWidget(self.import_button)
        self.theme_button = QPushButton("☾ 深色")
        self.theme_button.setObjectName("Quiet")
        toolbar_layout.addWidget(self.theme_button)
        self.settings_button = QPushButton("⚙ 设置")
        self.settings_button.setObjectName("Quiet")
        toolbar_layout.addWidget(self.settings_button)
        self.sidebar_button = QPushButton("☰")
        self.sidebar_button.setObjectName("Quiet")
        self.sidebar_button.setToolTip("折叠侧边栏  Ctrl+B")
        toolbar_layout.addWidget(self.sidebar_button)
        self.detail_button = QPushButton("详情")
        self.detail_button.setObjectName("DetailToggle")
        self.detail_button.setCheckable(True)
        # Restore the details column after prior sessions that saved it hidden.
        self.detail_button.setChecked(True)
        self.detail_button.setToolTip("隐藏详情面板")
        toolbar_layout.addWidget(self.detail_button)
        root_layout.addWidget(toolbar)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.sidebar = LibrarySidebar(self.connection)
        self.sidebar.setMinimumWidth(56 if self.collapsed_sidebar else 190)
        self.sidebar.setMaximumWidth(64 if self.collapsed_sidebar else 250)
        center = QWidget()
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(20, 17, 20, 8)
        center_layout.setSpacing(10)
        title_row = QHBoxLayout()
        self.page_title = QLabel("全部书籍")
        self.page_title.setObjectName("PageTitle")
        title_row.addWidget(self.page_title)
        title_row.addStretch()
        self.result_label = QLabel("")
        self.result_label.setObjectName("Muted")
        title_row.addWidget(self.result_label)
        center_layout.addLayout(title_row)

        self.views = QStackedWidget()
        self.grid = QListWidget()
        self.grid.setViewMode(QListWidget.ViewMode.IconMode)
        self.grid.setFlow(QListWidget.Flow.LeftToRight)
        self.grid.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.grid.setMovement(QListWidget.Movement.Static)
        self.grid.setWrapping(True)
        self.grid.setSpacing(14)
        self.grid.setGridSize(QSize(194, 366))
        self.grid.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.grid.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.grid.setItemDelegate(BookCardDelegate(self.grid))
        self._grid_viewport = self.grid.viewport()
        self._grid_viewport.installEventFilter(self)
        self.views.addWidget(self.grid)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["书名", "作者", "分类", "格式", "大小", "添加时间"])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.views.addWidget(self.table)

        self.empty_label = QLabel("📚\n还没有书，点击“导入”添加你的第一本书")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setObjectName("Muted")
        self.views.addWidget(self.empty_label)
        center_layout.addWidget(self.views, 1)

        pagination = QHBoxLayout()
        pagination.addStretch()
        self.previous_page_button = QPushButton("回到顶部")
        self.previous_page_button.setObjectName("Quiet")
        pagination.addWidget(self.previous_page_button)
        self.pagination_label = QLabel("第 1 / 1 页")
        self.pagination_label.setObjectName("Muted")
        pagination.addWidget(self.pagination_label)
        self.next_page_button = QPushButton("加载更多")
        self.next_page_button.setObjectName("Quiet")
        pagination.addWidget(self.next_page_button)
        center_layout.addLayout(pagination)

        self.selection_bar = QFrame()
        selection_layout = QHBoxLayout(self.selection_bar)
        selection_layout.setContentsMargins(8, 2, 8, 2)
        self.selection_text = QLabel("")
        selection_layout.addWidget(self.selection_text)
        self.batch_category = QComboBox()
        self.batch_category.addItem("移动到分类…", None)
        selection_layout.addWidget(self.batch_category)
        self.batch_move_button = QPushButton("应用")
        selection_layout.addWidget(self.batch_move_button)
        self.batch_tag_button = QPushButton("添加标签")
        selection_layout.addWidget(self.batch_tag_button)
        self.batch_export_button = QPushButton("导出清单")
        selection_layout.addWidget(self.batch_export_button)
        self.batch_delete_button = QPushButton("删除所选")
        selection_layout.addWidget(self.batch_delete_button)
        center_layout.addWidget(self.selection_bar)
        self.selection_bar.hide()

        self.detail = DetailPanel()
        self.detail.setMinimumWidth(270)
        self.detail.setMaximumWidth(360)
        self.splitter.addWidget(self.sidebar)
        self.splitter.addWidget(center)
        self.splitter.addWidget(self.detail)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([220, 900, 310])
        root_layout.addWidget(self.splitter, 1)

        footer = QFrame()
        footer.setObjectName("Footer")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(20, 8, 20, 8)
        self.status = QLabel("准备就绪")
        self.status.setObjectName("Muted")
        footer_layout.addWidget(self.status)
        footer_layout.addStretch()
        self.scan_progress = QProgressBar()
        self.scan_progress.setRange(0, 0)
        self.scan_progress.setMaximumWidth(140)
        self.scan_progress.hide()
        footer_layout.addWidget(self.scan_progress)
        root_layout.addWidget(footer)
        self.setCentralWidget(root)
        self.sidebar.set_compact(self.collapsed_sidebar)
        if self.collapsed_sidebar:
            self.sidebar.setMaximumWidth(64)
            self.sidebar.setMinimumWidth(56)

    def _build_actions(self) -> None:
        actions = [
            ("搜索", "Ctrl+K", self.search.setFocus),
            ("导入", "Ctrl+I", self.choose_directory),
            ("折叠侧边栏", "Ctrl+B", self.toggle_sidebar),
            ("切换主题", "Ctrl+D", self.toggle_theme),
            ("删除书籍记录", "Delete", self.delete_selected),
        ]
        for text, shortcut, callback in actions:
            action = QAction(text, self)
            action.setShortcut(shortcut)
            action.triggered.connect(callback)
            self.addAction(action)

    def _connect_signals(self) -> None:
        self.search.textChanged.connect(self._search_changed)
        self.sort_combo.currentIndexChanged.connect(self._sort_changed)
        self.previous_page_button.clicked.connect(lambda: self._change_page(-1))
        self.next_page_button.clicked.connect(lambda: self._change_page(1))
        self.view_button.clicked.connect(self.toggle_view)
        self.theme_button.clicked.connect(self.toggle_theme)
        self.sidebar_button.clicked.connect(self.toggle_sidebar)
        self.settings_button.clicked.connect(self.show_settings)
        self.detail_button.toggled.connect(self.toggle_detail)
        self.import_button.clicked.connect(self.choose_import_source)
        self.sidebar.filterRequested.connect(self._set_filter)
        self.sidebar.tagsRequested.connect(self._set_tags)
        self.sidebar.booksDropped.connect(self._assign_category)
        self.sidebar.tagManagementRequested.connect(self._manage_tag_library)
        self.grid.verticalScrollBar().valueChanged.connect(
            lambda value: self._maybe_load_more(self.grid, value))
        self.grid.verticalScrollBar().valueChanged.connect(self._virtualize_grid_cards)
        self.table.verticalScrollBar().valueChanged.connect(
            lambda value: self._maybe_load_more(self.table, value))
        self.grid.itemSelectionChanged.connect(self._grid_selection_changed)
        self.grid.itemDoubleClicked.connect(self._grid_open)
        self.grid.customContextMenuRequested.connect(self._context_menu)
        self.table.itemSelectionChanged.connect(self._table_selection_changed)
        self.table.itemDoubleClicked.connect(self._table_open)
        self.table.customContextMenuRequested.connect(self._context_menu)
        self.detail.openClicked.connect(self.open_selected)
        self.detail.editClicked.connect(self.edit_selected)
        self.detail.favoriteClicked.connect(self.toggle_favorite)
        self.detail.deleteClicked.connect(self.delete_selected)
        self.detail.copyPathClicked.connect(self._copy_path)
        self.batch_move_button.clicked.connect(self.move_selected_to_category)
        self.batch_tag_button.clicked.connect(self.tag_selected)
        self.batch_export_button.clicked.connect(self.export_selected)
        self.batch_delete_button.clicked.connect(self.delete_selected)

    def _setting(self, key: str, default: str = "") -> str:
        row = self.connection.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return str(row[0]) if row else default

    def _get_setting(self, key: str, default: str = "") -> str:
        try:
            row = self.database.connection.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            return str(row[0]) if row else default
        except sqlite3.Error:
            return default

    def _set_setting(self, key: str, value: str) -> None:
        self.connection.execute(
            "INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self.connection.commit()

    def _restore_window(self) -> None:
        geometry = self.qsettings.value("geometry")
        if geometry:
            self.restoreGeometry(geometry)
        sizes = self._setting("splitter_sizes")
        if sizes:
            try:
                restored_sizes = json.loads(sizes)
                # A previously hidden panel can leave a saved zero width.
                # Reclaim 310 px from the center view so details are visible.
                if (self.detail_button.isChecked() and isinstance(restored_sizes, list)
                        and len(restored_sizes) == 3 and restored_sizes[2] < self.detail.minimumWidth()):
                    restored_sizes = [int(value) for value in restored_sizes]
                    restored_sizes[1] = max(
                        420, restored_sizes[1] - (310 - restored_sizes[2])
                    )
                    restored_sizes[2] = 310
                self.splitter.setSizes(restored_sizes)
            except (ValueError, TypeError):
                pass
        filter_kind = self._get_setting("filter_kind", "group")
        filter_value = self._get_setting("filter_value", "all")
        if filter_kind == "category":
            try:
                self.current_filter_value = int(filter_value)
            except ValueError:
                self.current_filter_value = "all"
            row = self.connection.execute("SELECT name FROM categories WHERE id=?", (self.current_filter_value,)).fetchone()
            if row:
                self.current_filter_kind = "category"
                self.current_filter_title = str(row[0])
        elif filter_kind == "group" and filter_value in {key for _, _, key in self.sidebar.SMART_GROUPS}:
            self.current_filter_kind = "group"
            self.current_filter_value = filter_value
            self.current_filter_title = next(name for _, name, key in self.sidebar.SMART_GROUPS if key == filter_value)
        saved_view = self._get_setting("view_mode", self.view_mode)
        self.view_mode = saved_view if saved_view in {"grid", "list"} else "grid"
        self.views.setCurrentIndex(0 if self.view_mode == "grid" else 1)
        self._update_view_button()

    def _apply_theme(self) -> None:
        instance = QApplication.instance()
        if self.theme == "system":
            scheme = QGuiApplication.styleHints().colorScheme()
            self._active_theme = "dark" if scheme == Qt.ColorScheme.Dark else "light"
        else:
            self._active_theme = self.theme
        if instance:
            instance.setProperty("readplanTheme", self._active_theme)
            self.font_manager.apply_font(instance)
            instance.setStyleSheet(get_stylesheet(
                self._active_theme, self.font_manager.resolved_family, self.font_manager.current_size
            ))
        for card in self.findChildren(BookCard):
            card.apply_theme(self._active_theme)
        self.theme_button.setText("☀ 浅色" if self._active_theme == "dark" else "☾ 深色")

    def _on_system_theme_changed(self, _scheme=None) -> None:
        if self.theme == "system":
            self._apply_theme()

    def toggle_theme(self) -> None:
        self.theme = "dark" if self._active_theme == "light" else "light"
        self._apply_theme()
        self._set_setting("theme", self.theme)

    def toggle_view(self) -> None:
        self.view_mode = "list" if self.view_mode == "grid" else "grid"
        self._switch_view(0 if self.view_mode == "grid" else 1)
        self._update_view_button()
        self._update_pagination()
        self._set_setting("view_mode", self.view_mode)

    def _update_view_button(self) -> None:
        self.view_button.setText("☷ 列表" if self.view_mode == "grid" else "▦ 网格")

    def _switch_view(self, index: int) -> None:
        """Switch library pages without a parent graphics effect.

        Book cards already use their own shadow effects. Applying an opacity
        effect to the containing list makes Qt nest graphics effects and can
        break child painting when switching between empty and populated views.
        """
        target = self.views.widget(index)
        if self.views.currentWidget() is target:
            return
        self.views.setCurrentIndex(index)
        if target is self.grid:
            QTimer.singleShot(0, self._virtualize_grid_cards)
        else:
            self._virtualize_grid_cards()

    def toggle_sidebar(self) -> None:
        self.collapsed_sidebar = not self.collapsed_sidebar
        self.sidebar.set_compact(self.collapsed_sidebar)
        self.sidebar.setMinimumWidth(56 if self.collapsed_sidebar else 190)
        self.sidebar.setMaximumWidth(64 if self.collapsed_sidebar else 280)
        self._set_setting("sidebar_collapsed", "1" if self.collapsed_sidebar else "0")

    def toggle_detail(self, visible: bool) -> None:
        self.detail.setVisible(visible)
        self.detail_button.setToolTip("隐藏详情面板" if visible else "显示详情面板")
        if visible:
            sizes = self.splitter.sizes()
            if len(sizes) == 3 and sizes[2] < self.detail.minimumWidth():
                sizes[1] = max(420, sizes[1] - (310 - sizes[2]))
                sizes[2] = 310
                self.splitter.setSizes(sizes)
        self._set_setting("detail_visible", "1" if visible else "0")

    def _search_changed(self, *_: object) -> None:
        self.current_page = 0
        self.refresh_books()

    def _sort_changed(self, *_: object) -> None:
        self.current_page = 0
        self.refresh_books()

    def _change_page(self, delta: int) -> None:
        if delta > 0:
            self._load_more_books()
            return
        active = self.views.currentWidget()
        if active is self.table:
            active.verticalScrollBar().setValue(0)
        else:
            self.grid.verticalScrollBar().setValue(0)

    def _maybe_load_more(self, view: QWidget, value: int) -> None:
        if (self._loading_more or self.views.currentWidget() is not view
                or self.loaded_count >= self.total_filtered_books):
            return
        bar = view.verticalScrollBar()
        if bar.maximum() > 0 and value >= bar.maximum() - max(48, bar.pageStep() // 12):
            QTimer.singleShot(0, self._load_more_books)

    def _load_more_books(self) -> None:
        if self._loading_more or self.loaded_count >= self.total_filtered_books:
            return
        self._loading_more = True
        try:
            sql, params = self._build_query()
            new_books = [dict(row) for row in self.connection.execute(
                f"{sql} LIMIT ? OFFSET ?", (*params, self.PAGE_SIZE, self.loaded_count))]
        except sqlite3.Error as error:
            self.status.setText(f"加载更多书籍失败：{error}")
            self._loading_more = False
            return
        if not new_books:
            self.total_filtered_books = self.loaded_count
            self._update_pagination()
            self._loading_more = False
            return
        self.books.extend(new_books)
        self.book_by_id.update({int(book["id"]): book for book in new_books})
        self.loaded_count = len(self.books)
        self.current_page = max(0, (self.loaded_count - 1) // self.PAGE_SIZE)
        self._render_grid(new_books, append=True)
        self._render_table(new_books, append=True)
        self._update_pagination()
        self._update_footer()
        self._loading_more = False

    def _update_pagination(self) -> None:
        self.pagination_label.setText(f"已加载 {self.loaded_count} / {self.total_filtered_books} 本")
        active = self.views.currentWidget() if hasattr(self, "views") else None
        bar = active.verticalScrollBar() if active in (self.grid, self.table) else None
        self.previous_page_button.setEnabled(bool(bar and bar.value() > 0))
        self.next_page_button.setEnabled(self.loaded_count < self.total_filtered_books)

    def _set_filter(self, kind: str, value: Any, title: str) -> None:
        self.current_page = 0
        self.current_filter_kind = kind
        self.current_filter_value = value
        self.current_filter_title = title
        self.page_title.setText(title)
        self._set_setting("filter_kind", kind)
        self._set_setting("filter_value", str(value))
        self.refresh_books()

    def _set_tags(self, tags: list[int], logic: str) -> None:
        self.current_page = 0
        self.selected_tags = [int(tag) for tag in tags]
        self.tag_logic = logic
        if self.selected_tags:
            self.current_filter_kind = "tags"
            names = [row[0] for row in self.connection.execute(
                f"SELECT name FROM tags WHERE id IN ({','.join('?' for _ in self.selected_tags)})",
                self.selected_tags,
            )]
            self.current_filter_title = "标签 · " + "、".join(names)
            self.page_title.setText(self.current_filter_title)
        elif self.current_filter_kind == "tags":
            self.current_filter_kind, self.current_filter_value, self.current_filter_title = "group", "all", "全部书籍"
            self.page_title.setText(self.current_filter_title)
        self.refresh_books()

    def _search_clauses(self, query: str) -> tuple[list[str], list[Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        tokens = re.findall(r"(title|author|tag|format|is):([^\s]+)|([^\s]+)", query, flags=re.I)
        for field, value, plain in tokens:
            if plain:
                like = f"%{plain}%"
                clauses.append("(b.title LIKE ? OR b.author LIKE ? OR b.isbn LIKE ? OR EXISTS "
                               "(SELECT 1 FROM book_tags bt JOIN tags t ON t.id=bt.tag_id "
                               "WHERE bt.book_id=b.id AND t.name LIKE ?))")
                params.extend([like, like, like, like])
                continue
            field, value = field.lower(), value.strip()
            if field == "title":
                clauses.append("b.title LIKE ?")
                params.append(f"%{value}%")
            elif field == "author":
                clauses.append("b.author LIKE ?")
                params.append(f"%{value}%")
            elif field == "tag":
                clauses.append("EXISTS (SELECT 1 FROM book_tags bt JOIN tags t ON t.id=bt.tag_id "
                               "WHERE bt.book_id=b.id AND t.name LIKE ?)")
                params.append(f"%{value}%")
            elif field == "format":
                clauses.append("lower(b.format)=lower(?)")
                params.append(value.lstrip("."))
            elif field == "is":
                status = {"favorite": "favorite", "reading": "reading", "finished": "finished", "unread": "unread"}.get(value.lower())
                if status == "favorite":
                    clauses.append("b.is_favorite=1")
                elif status:
                    clauses.append("(b.read_status=? OR (?='finished' AND b.reading_status='completed'))")
                    params.extend([status, status])
        return clauses, params

    def _build_query(self) -> tuple[str, list[Any]]:
        clauses = ["COALESCE(b.missing,0)=0"]
        params: list[Any] = []
        kind, value = self.current_filter_kind, self.current_filter_value
        if kind == "group":
            if value == "favorite":
                clauses.append("b.is_favorite=1")
            elif value == "recent":
                clauses.append("b.last_open_time IS NOT NULL")
            elif value == "added":
                pass
            elif value in {"reading", "finished", "unread"}:
                if value == "finished":
                    clauses.append("(b.read_status='finished' OR b.reading_status='completed')")
                else:
                    clauses.append("b.read_status=?")
                    params.append(value)
        elif kind == "category":
            clauses.append("(b.category_id=? OR EXISTS (SELECT 1 FROM book_categories bc "
                           "WHERE bc.book_id=b.id AND bc.category_id=?))")
            params.extend([value, value])

        if self.selected_tags:
            placeholders = ",".join("?" for _ in self.selected_tags)
            if self.tag_logic == "AND":
                clauses.append(
                    f"b.id IN (SELECT book_id FROM book_tags WHERE tag_id IN ({placeholders}) "
                    "GROUP BY book_id HAVING COUNT(DISTINCT tag_id)=?)"
                )
                params.extend(self.selected_tags)
                params.append(len(set(self.selected_tags)))
            else:
                clauses.append(f"EXISTS (SELECT 1 FROM book_tags bt WHERE bt.book_id=b.id AND bt.tag_id IN ({placeholders}))")
                params.extend(self.selected_tags)

        search_clauses, search_params = self._search_clauses(self.search.text())
        clauses.extend(search_clauses)
        params.extend(search_params)
        sort_sql = {
            "created": "b.created_time DESC", "title": "b.title COLLATE NOCASE",
            "author": "b.author COLLATE NOCASE, b.title COLLATE NOCASE",
            "last_open": "b.last_open_time DESC NULLS LAST", "rating": "b.rating DESC, b.title COLLATE NOCASE",
        }.get(str(self.sort_combo.currentData()), "b.created_time DESC")
        if kind == "group" and value == "recent":
            sort_sql = "b.last_open_time DESC NULLS LAST"
        sql = "SELECT b.* FROM books b WHERE " + " AND ".join(clauses) + f" ORDER BY {sort_sql}, b.id DESC"
        return sql, params

    def refresh_books(self, *_: object) -> None:
        if not hasattr(self, "views"):
            return
        try:
            sql, params = self._build_query()
            count_sql = sql.split(" ORDER BY ", 1)[0].replace("SELECT b.*", "SELECT COUNT(*)", 1)
            self.total_filtered_books = int(self.connection.execute(count_sql, params).fetchone()[0])
            self.current_page = 0
            self.books = [dict(row) for row in self.connection.execute(
                f"{sql} LIMIT ? OFFSET ?", (*params, self.PAGE_SIZE, 0))]
            self.loaded_count = len(self.books)
        except sqlite3.Error as error:
            self.status.setText(f"读取书库失败：{error}")
            return
        self.book_by_id = {int(book["id"]): book for book in self.books}
        self._update_completions()
        self._refresh_batch_categories()
        self._render_grid()
        self._render_table()
        self.page_title.setText(self.current_filter_title)
        if self.books:
            self._switch_view(0 if self.view_mode == "grid" else 1)
            if self.selected_id not in self.book_by_id:
                self.selected_id = int(self.books[0]["id"])
            self._select_book(self.selected_id)
        else:
            self.selected_id = None
            self.detail.set_book(None)
            has_any = self.connection.execute("SELECT EXISTS(SELECT 1 FROM books WHERE missing=0)").fetchone()[0]
            self.empty_label.setText("🔍\n未找到匹配的书籍" if has_any else "📚\n还没有书，点击“导入”添加你的第一本书")
            self._switch_view(2)
        self.result_label.setText(f"共 {self.total_filtered_books} 本")
        self._update_pagination()
        self._update_footer()
        self._update_selection_bar()

    def _update_completions(self) -> None:
        self.completer_model.clear()
        for book in self.books[:30]:
            title = str(book.get("title") or "未命名书籍")
            author = str(book.get("author") or "未知作者")
            item = QStandardItem(f"{title} — {author}")
            item.setData(int(book["id"]), Qt.ItemDataRole.UserRole)
            cover = str(book.get("cover") or book.get("cover_path") or "")
            cover_pixmap = load_cover_pixmap(cover, QSize(24, 32)) if cover else None
            if cover_pixmap is not None:
                item.setIcon(QIcon(cover_pixmap))
            else:
                pixmap = QPixmap(24, 32)
                pixmap.fill(QColor(get_colors(self._active_theme)["accent"]))
                painter = QPainter(pixmap)
                painter.setPen(QColor(get_colors(self._active_theme)["text"]))
                painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, title[:1])
                painter.end()
                item.setIcon(QIcon(pixmap))
            self.completer_model.appendRow(item)
        prefix = self.search.text().split()[-1].split(":")[-1] if self.search.text().strip() else ""
        self.completer.setCompletionPrefix(prefix)

    def _completion_activated(self, text: str) -> None:
        title = text.split(" — ", 1)[0]
        self.search.setText(title)

    def _render_grid(self, books_to_render: list[dict[str, Any]] | None = None,
                     append: bool = False) -> None:
        self._loading = True
        if not append:
            for row in range(self.grid.count()):
                item = self.grid.item(row)
                card = self.grid.itemWidget(item)
                if card is not None:
                    self.grid.removeItemWidget(item)
                    card.deleteLater()
            self.grid.clear()
            self._grid_cards.clear()
            self._grid_row_by_id.clear()
            self._last_virtual_center_row = None
            self._last_virtual_columns = None
        books_to_render = self.books if books_to_render is None else books_to_render
        categories, tags = self._metadata_for_books(books_to_render)
        card_height = 366 + max(0, self.font_manager.current_size - DEFAULT_FONT_SIZE) * 6
        self.grid.setGridSize(QSize(194, card_height))
        for book in books_to_render:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, int(book["id"]))
            item.setSizeHint(QSize(194, card_height))
            category_name = categories.get(book["id"], "未分类")
            item.setData(BookCardDelegate.DATA_ROLE, {
                "book": book,
                "category": category_name,
                "tags": tags.get(book["id"], []),
                "category_icon": self.category_icons.get(
                    book["id"], LibrarySidebar.CATEGORY_ICONS.get(category_name, "•")
                ),
            })
            row = self.grid.count()
            self.grid.addItem(item)
            self._grid_row_by_id[int(book["id"])] = row
        self._loading = False
        self._virtualize_grid_cards()

    def _create_book_card(self, book: dict[str, Any], data: dict[str, Any]) -> BookCard:
        book_id = int(book["id"])
        card = BookCard(
            book, str(data.get("category") or "未分类"), list(data.get("tags") or []),
            category_icon=str(data.get("category_icon") or "•"),
        )
        card.clicked.connect(self._select_book)
        card.doubleClicked.connect(self._open_book_id)
        card.openRequested.connect(self._open_book_id)
        card.favoriteToggled.connect(self._save_favorite)
        card.editRequested.connect(self._edit_book_id)
        card.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        card.customContextMenuRequested.connect(
            lambda _point, target_id=book_id: self._card_context_menu(target_id))
        card.cover.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        card.cover.customContextMenuRequested.connect(
            lambda _point, target_id=book_id: self._card_context_menu(target_id))
        card.setToolTip(str(book.get("description") or book.get("notes") or "双击打开"))
        return card

    def _virtualize_grid_cards(self, *_args) -> None:
        force = not _args
        if not hasattr(self, "grid"):
            return
        grid = self.grid
        if self.views.currentWidget() is not grid or grid.count() == 0:
            for book_id, card in list(self._grid_cards.items()):
                row = self._grid_row_by_id.get(book_id, -1)
                if row >= 0 and row < grid.count():
                    grid.removeItemWidget(grid.item(row))
                card.deleteLater()
            self._grid_cards.clear()
            self._last_virtual_center_row = None
            self._last_virtual_columns = None
            return

        viewport = grid.viewport()
        width, height = viewport.width(), viewport.height()
        if width <= 0 or height <= 0:
            return
        columns = max(1, width // max(1, grid.gridSize().width() + grid.spacing()))
        middle = grid.indexAt(QPoint(width // 2, height // 2))
        if not middle.isValid():
            for x in (width // 3, (width * 2) // 3, 12, width - 12):
                middle = grid.indexAt(QPoint(x, height // 2))
                if middle.isValid():
                    break
        center_row = middle.row() if middle.isValid() else 0
        if (not force and self._last_virtual_columns == columns
                and self._last_virtual_center_row is not None
                and abs(center_row - self._last_virtual_center_row) < columns * 2):
            return
        self._last_virtual_center_row = center_row
        self._last_virtual_columns = columns
        first_row = max(0, center_row - columns * 3)
        last_row = min(grid.count(), center_row + columns * 4 + 1)
        nearby_rect = viewport.rect().adjusted(0, -height, 0, height)
        wanted: set[int] = set()
        for row in range(first_row, last_row):
            item = grid.item(row)
            if not nearby_rect.intersects(grid.visualItemRect(item)):
                continue
            book_id = int(item.data(Qt.ItemDataRole.UserRole))
            wanted.add(book_id)
            if grid.itemWidget(item) is None:
                data = item.data(BookCardDelegate.DATA_ROLE) or {}
                book = data.get("book") or self.book_by_id.get(book_id)
                if book:
                    card = self._create_book_card(book, data)
                    grid.setItemWidget(item, card)
                    self._grid_cards[book_id] = card

        for book_id, card in list(self._grid_cards.items()):
            if book_id in wanted:
                continue
            row = self._grid_row_by_id.get(book_id, -1)
            if row >= 0 and row < grid.count():
                grid.removeItemWidget(grid.item(row))
            card.deleteLater()
            self._grid_cards.pop(book_id, None)

    def _render_table(self, books_to_render: list[dict[str, Any]] | None = None,
                      append: bool = False) -> None:
        books_to_render = self.books if books_to_render is None else books_to_render
        categories, _tags = self._metadata_for_books(books_to_render)
        self._loading = True
        start_row = self.table.rowCount() if append else 0
        self.table.setRowCount(start_row + len(books_to_render) if append else len(books_to_render))
        for row, book in enumerate(books_to_render, start_row):
            size = book.get("size") if book.get("size") is not None else book.get("file_size", 0)
            values = [book.get("title", ""), book.get("author", ""), categories.get(book["id"], "未分类"),
                      str(book.get("format") or "").upper(), format_file_size(int(size or 0)),
                      str(book.get("created_time") or "")[:10]]
            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value or ""))
                if col == 0:
                    item.setData(Qt.ItemDataRole.UserRole, int(book["id"]))
                self.table.setItem(row, col, item)
            self.table.setRowHeight(row, 44)
        self._loading = False

    def _metadata_for_books(self, books: list[dict]) -> tuple[dict, dict]:
        if not books:
            self.category_icons = {}
            return {}, {}
        ids = [int(book["id"]) for book in books]
        placeholders = ",".join("?" for _ in ids)
        categories: dict[int, str] = {}
        self.category_icons: dict[int, str] = {}
        for row in self.connection.execute(
            f"SELECT b.id, COALESCE(c.name, c2.name, '未分类') name, COALESCE(c.icon,c2.icon) icon FROM books b "
            "LEFT JOIN categories c ON c.id=b.category_id "
            "LEFT JOIN book_categories bc ON bc.book_id=b.id "
            "LEFT JOIN categories c2 ON c2.id=bc.category_id "
            f"WHERE b.id IN ({placeholders}) GROUP BY b.id", ids,
        ):
            categories[row["id"]] = row["name"]
            self.category_icons[row["id"]] = str(
                row["icon"] or LibrarySidebar.CATEGORY_ICONS.get(row["name"], "•")
            )
        tags: dict[int, list[str]] = {book_id: [] for book_id in ids}
        for row in self.connection.execute(
            f"SELECT bt.book_id, t.name FROM book_tags bt JOIN tags t ON t.id=bt.tag_id "
            f"WHERE bt.book_id IN ({placeholders}) ORDER BY t.name", ids,
        ):
            tags[row["book_id"]].append(row["name"])
        return categories, tags

    def _select_book(self, book_id: int) -> None:
        self.selected_id = int(book_id)
        book = self.book_by_id.get(self.selected_id)
        if not book:
            return
        self._loading = True
        for row in range(self.grid.count()):
            if self.grid.item(row).data(Qt.ItemDataRole.UserRole) == self.selected_id:
                self.grid.setCurrentRow(row)
                break
        for row in range(self.table.rowCount()):
            if self.table.item(row, 0).data(Qt.ItemDataRole.UserRole) == self.selected_id:
                self.table.selectRow(row)
                break
        self._loading = False
        categories, tags = self._metadata_for_books([book])
        self.detail.set_book(book, categories.get(self.selected_id, "未分类"), tags.get(self.selected_id, []))
        self._update_selection_bar()

    def _grid_selection_changed(self) -> None:
        if self._loading:
            return
        item = self.grid.currentItem()
        if item:
            self._select_book(int(item.data(Qt.ItemDataRole.UserRole)))

    def _table_selection_changed(self) -> None:
        if self._loading:
            return
        selected = self.table.selectedItems()
        if selected:
            book_id = selected[0].data(Qt.ItemDataRole.UserRole)
            if book_id is None:
                book_id = self.table.item(selected[0].row(), 0).data(Qt.ItemDataRole.UserRole)
            if book_id is not None:
                self._select_book(int(book_id))

    def _grid_open(self, item: QListWidgetItem) -> None:
        self._open_book_id(int(item.data(Qt.ItemDataRole.UserRole)))

    def _table_open(self, item: QTableWidgetItem) -> None:
        book_id = item.data(Qt.ItemDataRole.UserRole)
        if book_id is None:
            book_id = self.table.item(item.row(), 0).data(Qt.ItemDataRole.UserRole)
        if book_id is not None:
            self._open_book_id(int(book_id))

    def _open_book_id(self, book_id: int) -> None:
        self.selected_id = book_id
        self.open_selected()

    def open_selected(self) -> None:
        book = self.book_by_id.get(self.selected_id)
        if not book:
            return
        path = Path(book["path"])
        if not path.is_file():
            QMessageBox.warning(self, "文件不存在", f"找不到电子书文件：\n{path}")
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            QMessageBox.warning(self, "无法打开", f"系统未能打开文件：\n{path}")
            return
        now = utc_now()
        with self.connection:
            self.connection.execute(
                "UPDATE books SET last_open_time=?, read_status='reading', reading_status='reading' WHERE id=?",
                (now, self.selected_id),
            )
            self.connection.execute(
                "INSERT INTO reading_history(book_id, opened_time) VALUES (?, ?)", (self.selected_id, now)
            )
        self.refresh_books()

    def toggle_favorite(self) -> None:
        if self.selected_id is not None:
            book = self.book_by_id[self.selected_id]
            self._save_favorite(self.selected_id, not bool(book.get("is_favorite")))

    def _copy_path(self, path: str) -> None:
        QApplication.clipboard().setText(path)
        self.status.setText("文件路径已复制")

    def _save_favorite(self, book_id: int, favorite: bool) -> None:
        self.connection.execute("UPDATE books SET is_favorite=? WHERE id=?", (int(favorite), book_id))
        self.connection.commit()
        self.refresh_books()

    def edit_selected(self) -> None:
        if self.selected_id is None:
            return
        book = self.book_by_id.get(self.selected_id)
        if not book:
            return
        categories = [dict(row) for row in self.connection.execute(
            "SELECT id,name FROM categories ORDER BY sort_order,name COLLATE NOCASE"
        )]
        dialog = BookEditDialog(book, categories, self)
        old_tags = [str(row[0]) for row in self.connection.execute(
            "SELECT t.name FROM tags t JOIN book_tags bt ON bt.tag_id=t.id WHERE bt.book_id=? ORDER BY t.name",
            (self.selected_id,),
        )]
        dialog.tags_edit.setText(", ".join(old_tags))
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        if not values["title"]:
            QMessageBox.warning(self, "保存失败", "书名不能为空。")
            return
        now = utc_now()
        with self.connection:
            self.connection.execute(
                "UPDATE books SET title=?,author=?,category_id=?,read_status=?,reading_status=?,rating=?,notes=? WHERE id=?",
                (values["title"], values["author"], values["category_id"], values["read_status"],
                 "completed" if values["read_status"] == "finished" else values["read_status"],
                 values["rating"], values["notes"], self.selected_id),
            )
            self.connection.execute("DELETE FROM book_categories WHERE book_id=?", (self.selected_id,))
            if values["category_id"] is not None:
                self.connection.execute(
                    "INSERT OR IGNORE INTO book_categories(book_id,category_id) VALUES (?,?)",
                    (self.selected_id, values["category_id"]),
                )
            self.connection.execute("DELETE FROM book_tags WHERE book_id=?", (self.selected_id,))
            for tag_name in values["tags"]:
                tag_id = ensure_tag(self.connection, tag_name, source="manual")
                self.connection.execute(
                    "INSERT OR IGNORE INTO book_tags(book_id,tag_id,created_time) VALUES (?,?,?)",
                    (self.selected_id, tag_id, now),
                )
        self.sidebar.refresh()
        self.refresh_books()
        if book.get("category_id") != values["category_id"] or set(old_tags) != set(values["tags"]):
            self._record_classification_feedback(
                int(self.selected_id), book.get("category_id"), values["category_id"], old_tags, values["tags"])

    def _record_classification_feedback(self, book_id: int, suggested_category_id, chosen_category_id,
                                        suggested_tags: list[str], chosen_tags: list[str],
                                        analyzer: str = "manual") -> None:
        if suggested_category_id == chosen_category_id and set(suggested_tags) == set(chosen_tags):
            return
        self.connection.execute(
            "INSERT INTO classification_feedback(book_id,suggested_category_id,chosen_category_id,"
            "suggested_tags,chosen_tags,analyzer,created_time) VALUES(?,?,?,?,?,?,?)",
            (int(book_id), suggested_category_id, chosen_category_id,
             json.dumps(suggested_tags, ensure_ascii=False), json.dumps(chosen_tags, ensure_ascii=False),
             analyzer, utc_now()),
        )
        self.connection.commit()

    def delete_selected(self) -> None:
        ids = self._selected_ids()
        if not ids:
            return
        answer = QMessageBox.question(
            self, "删除书籍记录", f"从书库删除选中的 {len(ids)} 条记录？\n原始电子书文件不会被删除。"
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        with self.connection:
            self.connection.executemany("DELETE FROM books WHERE id=?", [(book_id,) for book_id in ids])
        self.selected_id = None
        self.sidebar.refresh()
        self.refresh_books()

    def _selected_ids(self) -> list[int]:
        if self.view_mode == "list":
            return sorted({int(self.table.item(item.row(), 0).data(Qt.ItemDataRole.UserRole))
                           for item in self.table.selectedItems() if self.table.item(item.row(), 0)})
        return sorted({int(item.data(Qt.ItemDataRole.UserRole)) for item in self.grid.selectedItems()})

    def _edit_book_id(self, book_id: int) -> None:
        self.selected_id = int(book_id)
        self.edit_selected()

    def analyze_selected_book(self) -> None:
        """Run local parsing or optional AI classification without blocking Qt."""
        if self.selected_id is None:
            return
        if hasattr(self, "analysis_worker") and self.analysis_worker.isRunning():
            return
        book = dict(self.book_by_id.get(self.selected_id, {}))
        if not book:
            return
        mode = self._get_setting("smart_mode", "fast")
        use_ai = mode == "ai" and self._get_setting("ai_enabled", "0") == "1"
        client = None
        allow_text = False
        if use_ai:
            base_url = self._get_setting("ai_base_url", "")
            model = self._get_setting("ai_model", "")
            key = get_secret("Readplan", "ai_api_key")
            if not base_url or not model:
                use_ai = False
                self.status.setText("AI 服务未配置，改用本地规则分析")
            else:
                client = AIClient(base_url, model, key)
                allow_text = self._get_setting("ai_allow_text", "0") == "1"
                disclosure = "书名、作者和元数据将发送至已配置的 AI 服务。"
                if allow_text:
                    disclosure += "部分书籍文本将发送至 AI 服务进行分析。"
                answer = QMessageBox.warning(
                    self, "AI 隐私确认", disclosure + "是否继续？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    use_ai = False
                    client = None
                    allow_text = False
        if not use_ai and mode == "ai":
            mode = "standard"
        self.status.setText("正在本地解析并分析分类…" if not use_ai else "正在请求 AI 分类建议…")
        self.analysis_worker = AnalysisWorker(book, mode, client, allow_text)
        self.analysis_worker.completed.connect(self._analysis_finished)
        self.analysis_worker.start()

    def _analysis_finished(self, result: dict) -> None:
        suggestion = result.get("suggestion")
        if not suggestion:
            self.status.setText(f"智能分析失败：{result.get('error', '未知错误')}")
            QMessageBox.warning(self, "智能分析失败", self.status.text())
            return
        if suggestion.get("ai_error"):
            self.status.setText(f"AI 不可用，已回退本地建议：{suggestion['ai_error']}")
        dialog = ClassificationReviewDialog(suggestion, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            self.status.setText("已跳过分类建议")
            return
        self._apply_classification_feedback(int(suggestion["book_id"]), suggestion, dialog.values())
        self.sidebar.refresh()
        self.refresh_books()
        self.status.setText("已接受分类建议；本次修正已记录用于后续规则改进")

    def _apply_classification_feedback(self, book_id: int, suggestion: dict, values: dict) -> None:
        book_id = int(suggestion["book_id"])
        old_book = self.connection.execute(
            "SELECT category_id,title,author,path,publisher,pages FROM books WHERE id=?", (book_id,)
        ).fetchone()
        old_category_id = int(old_book["category_id"]) if old_book and old_book["category_id"] else None
        metadata = suggestion.get("metadata", {})
        current_title = str(old_book["title"] or "") if old_book else ""
        filename_title = Path(str(old_book["path"] or "")).stem if old_book else ""
        title = str(suggestion.get("title") or current_title)
        if current_title.casefold() != filename_title.casefold():
            title = current_title
        author = str(old_book["author"] or suggestion.get("author") or "") if old_book else str(suggestion.get("author") or "")
        publisher = str(old_book["publisher"] or metadata.get("publisher") or "") if old_book else str(metadata.get("publisher") or "")
        pages = old_book["pages"] if old_book and old_book["pages"] else metadata.get("pages")
        chosen_ids: list[int] = []
        parent_id = None
        with self.connection:
            for order, name in enumerate(values["category_path"]):
                row = self.connection.execute(
                    "SELECT id FROM categories WHERE normalized_name=?", (name.casefold(),)
                ).fetchone()
                if row:
                    category_id = int(row[0])
                    self.connection.execute("UPDATE categories SET parent_id=? WHERE id=?", (parent_id, category_id))
                else:
                    cursor = self.connection.execute(
                        "INSERT INTO categories(name,normalized_name,parent_id,sort_order,created_time) "
                        "VALUES(?,?,?,?,?)", (name, name.casefold(), parent_id, order, utc_now()),
                    )
                    category_id = int(cursor.lastrowid)
                chosen_ids.append(category_id)
                parent_id = category_id
            chosen_category = chosen_ids[-1] if chosen_ids else old_category_id
            self.connection.execute(
                "UPDATE books SET title=?,author=?,publisher=?,pages=?,category_id=?,"
                "description=CASE WHEN ?='' THEN description ELSE ? END WHERE id=?",
                (title, author, publisher, pages, chosen_category, values["summary"], values["summary"], book_id),
            )
            self.connection.execute("DELETE FROM book_categories WHERE book_id=?", (book_id,))
            self.connection.executemany("INSERT OR IGNORE INTO book_categories(book_id,category_id) VALUES(?,?)",
                                         [(book_id, cid) for cid in chosen_ids])
            suggested_path = suggestion.get("category_path", [])
            suggested_leaf = self.connection.execute(
                "SELECT id FROM categories WHERE normalized_name=?", (suggested_path[-1].casefold(),)
            ).fetchone() if suggested_path else None
            same_tags = {str(value).casefold() for value in values["tags"]} == {
                str(value).casefold() for value in suggestion.get("tags", [])}
            tag_source = ("ai" if suggestion.get("analyzer") == "ai" else "system") if same_tags else "manual"
            set_book_tags(self.connection, [book_id], values["tags"],
                          source=tag_source)
            self.connection.execute(
                "INSERT INTO classification_feedback(book_id,suggested_category_id,chosen_category_id,"
                "suggested_tags,chosen_tags,analyzer,created_time) VALUES(?,?,?,?,?,?,?)",
                (book_id, int(suggested_leaf[0]) if suggested_leaf else None, chosen_category,
                 json.dumps(suggestion.get("tags", []), ensure_ascii=False),
                 json.dumps(values["tags"], ensure_ascii=False), str(suggestion.get("analyzer", "local")), utc_now()),
            )

    def manage_book_categories(self, book_ids: list[int] | None = None) -> None:
        ids = book_ids or self._selected_ids()
        if not ids:
            return
        dialog = BookCategoriesDialog(self.connection, ids, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        category_ids = dialog.selected_category_ids()
        previous_categories = {book_id: self.connection.execute(
            "SELECT category_id FROM books WHERE id=?", (book_id,)).fetchone()[0] for book_id in ids}
        with self.connection:
            for book_id in ids:
                self.connection.execute("DELETE FROM book_categories WHERE book_id=?", (book_id,))
                self.connection.executemany(
                    "INSERT OR IGNORE INTO book_categories(book_id,category_id) VALUES (?,?)",
                    [(book_id, category_id) for category_id in category_ids],
                )
                self.connection.execute(
                    "UPDATE books SET category_id=? WHERE id=?",
                    (category_ids[0] if category_ids else None, book_id),
                )
        chosen_category = category_ids[0] if category_ids else None
        for book_id, previous_category in previous_categories.items():
            if previous_category != chosen_category:
                tags = [str(row[0]) for row in self.connection.execute(
                    "SELECT t.name FROM tags t JOIN book_tags bt ON bt.tag_id=t.id WHERE bt.book_id=?", (book_id,))]
                self._record_classification_feedback(book_id, previous_category, chosen_category, tags, tags)
        self.sidebar.refresh()
        self.refresh_books()

    def _update_selection_bar(self) -> None:
        ids = self._selected_ids()
        visible = len(ids) > 1
        self.selection_bar.setVisible(visible)
        if visible:
            self.selection_text.setText(f"已选 {len(ids)} 本")

    def _refresh_batch_categories(self) -> None:
        current = self.batch_category.currentData() if hasattr(self, "batch_category") else None
        self.batch_category.clear()
        self.batch_category.addItem("移动到分类…", None)
        for row in self.connection.execute("SELECT id,name FROM categories ORDER BY sort_order,name"):
            self.batch_category.addItem(row["name"], row["id"])
        index = self.batch_category.findData(current)
        if index >= 0:
            self.batch_category.setCurrentIndex(index)

    def move_selected_to_category(self) -> None:
        category_id = self.batch_category.currentData()
        ids = self._selected_ids()
        if category_id is None or not ids:
            return
        with self.connection:
            self.connection.executemany("UPDATE books SET category_id=? WHERE id=?",
                                        [(category_id, book_id) for book_id in ids])
            self.connection.executemany("INSERT OR IGNORE INTO book_categories(book_id,category_id) VALUES (?,?)",
                                        [(book_id, category_id) for book_id in ids])
        self.sidebar.refresh()
        self.refresh_books()

    def _assign_category(self, category_id: int, book_ids: list[int]) -> None:
        """Apply a category after a book card is dropped onto the sidebar tree."""
        if not book_ids:
            return
        previous_categories = {book_id: self.connection.execute(
            "SELECT category_id FROM books WHERE id=?", (book_id,)).fetchone()[0] for book_id in book_ids}
        with self.connection:
            self.connection.executemany(
                "UPDATE books SET category_id=? WHERE id=?",
                [(category_id, book_id) for book_id in book_ids],
            )
            self.connection.executemany(
                "INSERT OR IGNORE INTO book_categories(book_id,category_id) VALUES (?,?)",
                [(book_id, category_id) for book_id in book_ids],
            )
        for book_id, previous_category in previous_categories.items():
            if previous_category != category_id:
                tags = [str(row[0]) for row in self.connection.execute(
                    "SELECT t.name FROM tags t JOIN book_tags bt ON bt.tag_id=t.id WHERE bt.book_id=?", (book_id,))]
                self._record_classification_feedback(book_id, previous_category, category_id, tags, tags)
        self.sidebar.refresh()
        self.refresh_books()

    def tag_selected(self) -> None:
        ids = self._selected_ids()
        if not ids:
            return
        books = [self.book_by_id[value] for value in ids if value in self.book_by_id]
        previous_tags = {
            book_id: [str(row[0]) for row in self.connection.execute(
                "SELECT t.name FROM tags t JOIN book_tags bt ON bt.tag_id=t.id WHERE bt.book_id=?", (book_id,))]
            for book_id in ids
        }
        dialog = BookTagDialog(self.connection, ids, books, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        for book_id in ids:
            chosen_tags = [str(row[0]) for row in self.connection.execute(
                "SELECT t.name FROM tags t JOIN book_tags bt ON bt.tag_id=t.id WHERE bt.book_id=?", (book_id,))]
            category_id = self.book_by_id.get(book_id, {}).get("category_id")
            self._record_classification_feedback(
                book_id, category_id, category_id, previous_tags.get(book_id, []), chosen_tags)
        self.sidebar.refresh()
        self.refresh_books()

    def _manage_tag_library(self) -> None:
        TagLibraryDialog(self.connection, self).exec()
        self.sidebar.refresh()
        self.refresh_books()

    def export_selected(self) -> None:
        ids = self._selected_ids()
        if not ids:
            return
        filename, _ = QFileDialog.getSaveFileName(self, "导出书目清单", "books.csv", "CSV 文件 (*.csv)")
        if not filename:
            return
        placeholders = ",".join("?" for _ in ids)
        rows = self.connection.execute(
            f"SELECT title,author,path,format,size,file_size,read_status,rating FROM books WHERE id IN ({placeholders})",
            ids,
        )
        with Path(filename).open("w", encoding="utf-8-sig", newline="") as output:
            writer = csv.writer(output)
            writer.writerow(["书名", "作者", "路径", "格式", "大小（字节）", "阅读状态", "评分"])
            for row in rows:
                writer.writerow([row["title"], row["author"], row["path"], row["format"],
                                 row["size"] if row["size"] is not None else row["file_size"],
                                 row["read_status"], row["rating"]])
        self.status.setText(f"已导出 {len(ids)} 条书目")

    def _context_menu(self, point) -> None:
        if self.view_mode == "grid":
            target = self.grid.itemAt(point)
            if target and not target.isSelected():
                self.grid.clearSelection()
                target.setSelected(True)
                self.grid.setCurrentItem(target)
        else:
            target = self.table.itemAt(point)
            if target and not target.isSelected():
                self.table.clearSelection()
                self.table.selectRow(target.row())
        ids = self._selected_ids()
        if not ids:
            return
        menu = QMenu(self)
        open_action = menu.addAction("打开")
        edit_action = menu.addAction("编辑信息")
        add_category_action = menu.addAction("添加分类…")
        change_category_action = menu.addAction("修改分类…")
        add_tag_action = menu.addAction("添加标签…")
        manage_tags_action = menu.addAction("标签管理…")
        analyze_action = menu.addAction("智能分析分类…")
        favorite_action = menu.addAction("加入/移出收藏")
        menu.addSeparator()
        delete_action = menu.addAction("删除")
        action = menu.exec(self.cursor().pos())
        self.selected_id = ids[0]
        if action is open_action:
            self.open_selected()
        elif action is edit_action:
            self.edit_selected()
        elif action is add_category_action or action is change_category_action:
            self.manage_book_categories(ids)
        elif action is add_tag_action:
            self.tag_selected()
        elif action is manage_tags_action:
            self._manage_tag_library()
        elif action is analyze_action:
            self.analyze_selected_book()
        elif action is favorite_action:
            self.toggle_favorite()
        elif action is delete_action:
            self.delete_selected()

    def _card_context_menu(self, book_id: int) -> None:
        for index in range(self.grid.count()):
            item = self.grid.item(index)
            if int(item.data(Qt.ItemDataRole.UserRole)) == int(book_id):
                self.grid.clearSelection()
                item.setSelected(True)
                self.grid.setCurrentItem(item)
                self._context_menu(self.grid.visualItemRect(item).center())
                return

    def choose_directory(self) -> None:
        default_path = Path(self._get_setting("default_library_path", str(DEFAULT_LIBRARY_PATH)))
        suggested = str(default_path) if default_path.is_dir() else str(Path.home())
        folder = QFileDialog.getExistingDirectory(self, "选择电子书目录", suggested)
        if folder:
            self.start_scan(folder)

    def choose_import_source(self) -> None:
        """Offer both explicit file import and recursive folder scanning."""
        menu = QMenu(self)
        choose_files = menu.addAction("选择电子书文件…")
        scan_folder = menu.addAction("扫描整个文件夹…")
        action = menu.exec(self.import_button.mapToGlobal(self.import_button.rect().bottomLeft()))
        if action is choose_files:
            files, _ = QFileDialog.getOpenFileNames(
                self, "选择电子书文件", str(Path.home()),
                "电子书 (*.pdf *.epub *.mobi *.azw *.azw3 *.txt);;所有文件 (*.*)",
            )
            if files:
                self.start_scan(files)
        elif action is scan_folder:
            self.choose_directory()

    def start_cover_backfill(self) -> None:
        """Create thumbnails for older indexed books that have no cached cover."""
        if hasattr(self, "cover_worker") and self.cover_worker.isRunning():
            return
        count = self.connection.execute(
            "SELECT COUNT(*) FROM books WHERE cover_path IS NULL OR cover_path=''"
        ).fetchone()[0]
        if not count:
            return
        self.status.setText(f"正在补全 {count} 本书的封面…")
        self.cover_worker = CoverBackfillWorker(str(self.database.path))
        self.cover_worker.completed.connect(self._cover_backfill_finished)
        self.cover_worker.start()

    def _cover_backfill_finished(self, result: dict) -> None:
        generated = int(result.get("generated", 0))
        self.refresh_books()
        if result.get("error"):
            self.status.setText(f"封面补全失败：{result['error']}")
        elif generated:
            self.status.setText(f"已补全 {generated} 本书的封面")
        else:
            self.status.setText("封面补全完成；没有可提取的内嵌封面")

    def start_scan(self, folder: str | list[str]) -> None:
        if hasattr(self, "cover_worker") and self.cover_worker.isRunning():
            QTimer.singleShot(400, lambda: self.start_scan(folder))
            return
        if hasattr(self, "scan_worker") and self.scan_worker.isRunning():
            return
        description = f"{len(folder)} 个所选文件" if isinstance(folder, list) else folder
        self.status.setText(f"正在导入：{description}")
        self.scan_progress.show()
        self.import_button.setEnabled(False)
        self.scan_worker = ScanWorker(str(self.database.path), folder)
        self.scan_worker.completed.connect(self._scan_finished)
        self.scan_worker.finished.connect(self._scan_thread_finished)
        self.scan_worker.start()

    def _scan_finished(self, result: dict) -> None:
        self.scan_progress.hide()
        if "fatal_error" in result:
            self.status.setText(f"导入失败：{result['fatal_error']}")
            QMessageBox.critical(self, "导入失败", result["fatal_error"])
            return
        message = (
            f"扫描完成 · 新增 {result['new']} · 更新 {result['updated']} · "
            f"跳过 {result['skipped']} · 错误 {result['errors']}"
        )
        self.sidebar.refresh()
        imported_ids = [int(value) for value in result.get("imported_ids", [])]
        if imported_ids:
            self._show_new_imports(imported_ids)
        else:
            self.refresh_books()
        self.status.setText(message)
        known_ids = set(self.pending_import_ids)
        self.pending_import_ids.extend(
            value for value in imported_ids if value not in known_ids)

    def _show_new_imports(self, book_ids: list[int]) -> None:
        """Clear stale filters and show the newest newly indexed book on page one."""
        self.current_filter_kind = "group"
        self.current_filter_value = "all"
        self.current_filter_title = "全部书籍"
        self.selected_tags = []
        self.current_page = 0
        self.selected_id = max(book_ids)

        created_sort = self.sort_combo.findData("created")
        if created_sort >= 0:
            previous = self.sort_combo.blockSignals(True)
            self.sort_combo.setCurrentIndex(created_sort)
            self.sort_combo.blockSignals(previous)
        previous = self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(previous)

        for widget in (self.sidebar.smart_list, self.sidebar.category_tree, self.sidebar.tag_list):
            widget.blockSignals(True)
        self.sidebar.category_tree.clearSelection()
        self.sidebar.tag_list.clearSelection()
        for index in range(self.sidebar.smart_list.count()):
            item = self.sidebar.smart_list.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == "all":
                self.sidebar.smart_list.setCurrentItem(item)
                break
        self.sidebar.smart_list.blockSignals(False)
        self.sidebar.category_tree.blockSignals(False)
        self.sidebar.tag_list.blockSignals(False)

        self._set_setting("filter_kind", "group")
        self._set_setting("filter_value", "all")
        self.refresh_books()

    def _scan_thread_finished(self) -> None:
        queue = getattr(self, "scan_queue", [])
        if queue:
            next_folder = queue.pop(0)
            QTimer.singleShot(0, lambda: self.start_scan(next_folder))
        else:
            self.import_button.setEnabled(True)
            if self.pending_import_ids:
                imported = self.pending_import_ids
                self.pending_import_ids = []
                QTimer.singleShot(120, lambda ids=imported: self._analyze_imported_books(ids))

    def _analyze_imported_books(self, book_ids: list[int]) -> None:
        """Analyze newly indexed books locally, using PDF/EPUB text where available."""
        if not book_ids:
            return
        books = []
        for offset in range(0, len(book_ids), 500):
            chunk = book_ids[offset:offset + 500]
            marks = ",".join("?" for _ in chunk)
            books.extend(dict(row) for row in self.connection.execute(
                f"SELECT * FROM books WHERE id IN ({marks})", chunk))
        if not books:
            return
        books.sort(key=lambda book: str(book.get("title") or "").casefold())
        # Standard mode extracts bounded local text from PDF/EPUB. This never
        # sends titles, metadata, or document text to an AI service.
        self.status.setText(f"正在本地分析新导入的 {len(books)} 本书…")
        self.batch_analysis_worker = BatchAnalysisWorker(books, "standard")
        self.batch_analysis_worker.progress.connect(
            lambda current, total: self.status.setText(f"正在本地分析新书 {current}/{total}…"))
        self.batch_analysis_worker.completed.connect(self._batch_analysis_finished)
        self.batch_analysis_worker.start()

    def _batch_analysis_finished(self, suggestions: list) -> None:
        if not suggestions:
            self.status.setText("没有生成分类和标签")
            return
        self._classification_save_queue = list(suggestions)
        self._classification_save_total = len(suggestions)
        self._classification_save_done = 0
        self._save_next_classification_batch()

    def _save_next_classification_batch(self) -> None:
        """Persist generated metadata in short UI batches for large imports."""
        queue = getattr(self, "_classification_save_queue", [])
        if not queue:
            self.sidebar.refresh()
            self.refresh_books()
            self.status.setText(
                f"已自动生成 {self._classification_save_done} 本新书的分类、标签和简介（本地分析）"
            )
            return

        batch = queue[:50]
        del queue[:50]
        for suggestion in batch:
            try:
                book_id = int(suggestion.get("book_id"))
            except (TypeError, ValueError):
                continue
            if self.connection.execute("SELECT 1 FROM books WHERE id=?", (book_id,)).fetchone() is None:
                continue
            values = {
                "category_path": suggestion.get("category_path") or ["其他"],
                "tags": suggestion.get("tags") or [],
                "summary": suggestion.get("summary") or "",
            }
            try:
                self._apply_classification_feedback(book_id, suggestion, values)
                self._classification_save_done += 1
            except (sqlite3.Error, KeyError, TypeError, ValueError) as error:
                self.status.setText(f"部分分类标签生成失败：{error}")

        self.status.setText(
            f"正在保存自动分类和标签 {self._classification_save_done}/{self._classification_save_total}…"
        )
        QTimer.singleShot(0, self._save_next_classification_batch)

    def _update_footer(self) -> None:
        totals = self.connection.execute(
            "SELECT COUNT(*) total, SUM(CASE WHEN read_status IN ('finished') OR reading_status='completed' THEN 1 ELSE 0 END) finished, "
            "SUM(CASE WHEN read_status='reading' THEN 1 ELSE 0 END) reading, "
            "SUM(CASE WHEN is_favorite=1 THEN 1 ELSE 0 END) favorites FROM books WHERE missing=0"
        ).fetchone()
        self.status.setText(
            f"共 {totals['total'] or 0} 本 · 已读 {totals['finished'] or 0} · "
            f"在读 {totals['reading'] or 0} · 收藏 {totals['favorites'] or 0}"
        )

    def show_settings(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("设置")
        dialog.setMinimumWidth(540)
        layout = QVBoxLayout(dialog)
        theme_group = QGroupBox("主题")
        theme_layout = QHBoxLayout(theme_group)
        theme_buttons: dict[str, QRadioButton] = {}
        for value, label in (("light", "浅色"), ("dark", "深色"), ("system", "跟随系统")):
            button = QRadioButton(label)
            button.setChecked(self.theme == value)
            theme_buttons[value] = button
            theme_layout.addWidget(button)
        layout.addWidget(theme_group)

        appearance_group = QGroupBox("外观")
        appearance_form = QFormLayout(appearance_group)
        font_family = QComboBox()
        for label, value, _candidates in FONT_OPTIONS:
            available = self.font_manager.is_available(value)
            display = label if available else f"{label}（未安装）"
            font_family.addItem(display, value)
            if not available:
                font_family.setItemData(
                    font_family.count() - 1,
                    "此字体当前不可用；选择后将自动回退到系统默认字体。",
                    Qt.ItemDataRole.ToolTipRole,
                )
        family_index = font_family.findData(self.font_manager.current_family)
        font_family.setCurrentIndex(max(family_index, 0))
        appearance_form.addRow("界面字体", font_family)

        size_row = QHBoxLayout()
        size_row.addWidget(QLabel("小"))
        font_size_slider = QSlider(Qt.Orientation.Horizontal)
        font_size_slider.setRange(10, 24)
        font_size_slider.setSingleStep(1)
        font_size_slider.setPageStep(2)
        font_size_slider.setValue(self.font_manager.current_size)
        font_size_slider.setToolTip("界面字号：10–24 px")
        size_row.addWidget(font_size_slider, 1)
        size_row.addWidget(QLabel("大"))
        font_size_value = QLabel(f"{self.font_manager.current_size} px")
        font_size_value.setMinimumWidth(46)
        size_row.addWidget(font_size_value)
        size_container = QWidget()
        size_container.setLayout(size_row)
        appearance_form.addRow("字体大小", size_container)

        font_preview = QLabel("Aa 电子书管理器 · Readplan")
        font_preview.setObjectName("FontPreview")
        font_preview.setMinimumHeight(38)
        appearance_form.addRow("预览", font_preview)
        font_status = QLabel(f"当前字体：{self.font_manager.resolved_family}")
        font_status.setObjectName("Muted")
        appearance_form.addRow("", font_status)

        restore_font_button = QPushButton("恢复系统字体")
        restore_font_button.setObjectName("Quiet")
        appearance_form.addRow("", restore_font_button)
        layout.addWidget(appearance_group)

        def apply_font_preferences(*_args: object) -> None:
            requested_family = str(font_family.currentData() or SYSTEM_FONT)
            available = self.font_manager.set_font(
                requested_family, font_size_slider.value(), QApplication.instance()
            )
            if not available:
                previous = font_family.blockSignals(True)
                fallback_index = font_family.findData(SYSTEM_FONT)
                font_family.setCurrentIndex(max(fallback_index, 0))
                font_family.blockSignals(previous)
                font_status.setText("所选字体未安装，已自动改用系统默认字体。")
            else:
                font_status.setText(f"当前字体：{self.font_manager.resolved_family}")
            font_size_value.setText(f"{self.font_manager.current_size} px")
            self._apply_theme()

        def restore_system_font() -> None:
            previous_combo = font_family.blockSignals(True)
            previous_slider = font_size_slider.blockSignals(True)
            font_family.setCurrentIndex(max(font_family.findData(SYSTEM_FONT), 0))
            font_size_slider.setValue(DEFAULT_FONT_SIZE)
            font_family.blockSignals(previous_combo)
            font_size_slider.blockSignals(previous_slider)
            apply_font_preferences()

        font_family.currentIndexChanged.connect(apply_font_preferences)
        font_size_slider.valueChanged.connect(apply_font_preferences)
        restore_font_button.clicked.connect(restore_system_font)

        library_group = QGroupBox("电子书库")
        library_form = QFormLayout(library_group)
        path_row = QHBoxLayout()
        path_edit = QLineEdit(self._get_setting("default_library_path", r"D:\LIBS"))
        browse = QPushButton("选择…")
        browse.clicked.connect(lambda: path_edit.setText(
            QFileDialog.getExistingDirectory(dialog, "选择默认书库目录", path_edit.text()) or path_edit.text()
        ))
        path_row.addWidget(path_edit, 1)
        path_row.addWidget(browse)
        path_container = QWidget()
        path_container.setLayout(path_row)
        library_form.addRow("书库路径", path_container)
        layout.addWidget(library_group)

        scan_group = QGroupBox("扫描设置")
        scan_form = QFormLayout(scan_group)
        auto_scan = QCheckBox("启动时扫描默认书库目录")
        auto_scan.setChecked(self._get_setting("auto_scan", "0") == "1")
        scan_form.addRow("自动扫描", auto_scan)
        clear_cache = QPushButton("清理封面缓存")
        clear_cache.clicked.connect(self.clear_cover_cache)
        scan_form.addRow("缓存", clear_cache)
        layout.addWidget(scan_group)

        smart_group = QGroupBox("智能功能与 AI 服务")
        smart_form = QFormLayout(smart_group)
        smart_enabled = QCheckBox("开启 AI 分类（关闭时只使用本地规则）")
        smart_enabled.setChecked(self._get_setting("ai_enabled", "0") == "1")
        smart_form.addRow("AI 分类", smart_enabled)
        mode_combo = QComboBox()
        for label, value in (("快速：文件名 + 元数据", "fast"),
                             ("标准：增加 PDF / EPUB 文本摘要", "standard"),
                             ("AI：发送信息获取语义建议", "ai")):
            mode_combo.addItem(label, value)
        mode_index = mode_combo.findData(self._get_setting("smart_mode", "fast"))
        mode_combo.setCurrentIndex(max(mode_index, 0))
        smart_form.addRow("分析等级", mode_combo)
        ai_url = QLineEdit(self._get_setting("ai_base_url", "https://api.openai.com/v1"))
        ai_url.setPlaceholderText("https://api.openai.com/v1 或 http://localhost:11434/v1")
        smart_form.addRow("API 地址", ai_url)
        ai_model = QLineEdit(self._get_setting("ai_model", ""))
        ai_model.setPlaceholderText("模型名称，例如 gpt-4o-mini")
        smart_form.addRow("模型", ai_model)
        ai_key = QLineEdit()
        ai_key.setEchoMode(QLineEdit.EchoMode.Password)
        ai_key.setPlaceholderText("留空表示保留已保存的密钥")
        smart_form.addRow("API Key", ai_key)
        upload_text = QCheckBox("允许发送 PDF / EPUB 文本片段（每次分析前还会再次确认）")
        upload_text.setChecked(self._get_setting("ai_allow_text", "0") == "1")
        smart_form.addRow("隐私", upload_text)
        test_ai = QPushButton("测试连接")
        test_ai.clicked.connect(lambda: self._test_ai_connection(
            ai_url.text(), ai_model.text(), ai_key.text(), dialog))
        smart_form.addRow("连接", test_ai)
        layout.addWidget(smart_group)

        database_group = QGroupBox("数据库")
        database_layout = QVBoxLayout(database_group)
        database_layout.addWidget(QLabel(str(self.database.path)))
        database_actions = QHBoxLayout()
        backup_button = QPushButton("备份数据库…")
        restore_button = QPushButton("恢复数据库…")
        backup_button.clicked.connect(self.backup_database)
        restore_button.clicked.connect(self.restore_database)
        database_actions.addWidget(backup_button)
        database_actions.addWidget(restore_button)
        database_layout.addLayout(database_actions)
        clear_library_button = QPushButton("清空所有书籍和扫描路径…")
        clear_library_button.setObjectName("Danger")

        def clear_library_from_settings() -> None:
            if self.clear_library_index():
                # Close without saving stale values from the now-cleared settings form.
                dialog.reject()

        clear_library_button.clicked.connect(clear_library_from_settings)
        database_layout.addWidget(clear_library_button)
        layout.addWidget(database_group)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._set_setting("default_library_path", path_edit.text().strip())
        self._set_setting("auto_scan", "1" if auto_scan.isChecked() else "0")
        self._set_setting("ai_enabled", "1" if smart_enabled.isChecked() else "0")
        self._set_setting("smart_mode", str(mode_combo.currentData()))
        self._set_setting("ai_base_url", ai_url.text().strip())
        self._set_setting("ai_model", ai_model.text().strip())
        self._set_setting("ai_allow_text", "1" if upload_text.isChecked() else "0")
        if ai_key.text():
            try:
                set_secret("Readplan", "ai_api_key", ai_key.text())
            except RuntimeError as error:
                QMessageBox.warning(self, "无法安全保存 API Key", str(error))
        selected_theme = next((name for name, button in theme_buttons.items() if button.isChecked()), "light")
        if selected_theme != self.theme:
            self.theme = selected_theme
            self._apply_theme()
            self._set_setting("theme", self.theme)

    def clear_library_index(self) -> bool:
        """Clear indexed books and scan-root settings without touching source files."""
        busy_workers = ("scan_worker", "cover_worker", "analysis_worker", "batch_analysis_worker")
        if getattr(self, "scan_queue", []) or getattr(self, "_classification_save_queue", []) or any(
            (worker := getattr(self, name, None)) is not None and worker.isRunning()
            for name in busy_workers
        ):
            QMessageBox.warning(self, "暂时无法清空", "请等待扫描、封面处理和智能分析结束后再清空书库。")
            return False

        try:
            book_count = int(self.connection.execute("SELECT COUNT(*) FROM books").fetchone()[0])
            path_count = int(self.connection.execute("SELECT COUNT(*) FROM scan_roots").fetchone()[0])
            category_count = int(self.connection.execute("SELECT COUNT(*) FROM categories").fetchone()[0])
            tag_count = int(self.connection.execute("SELECT COUNT(*) FROM tags").fetchone()[0])
        except sqlite3.Error as error:
            QMessageBox.critical(self, "读取书库失败", str(error))
            return False

        answer = QMessageBox.question(
            self,
            "确认清空书库",
            f"即将从 Readplan 中移除 {book_count} 本书的索引、{path_count} 个扫描目录、"
            f"{category_count} 个分类和 {tag_count} 个标签，并清除阅读记录及默认书库路径。\n\n"
            "磁盘上的电子书原文件不会被删除。此操作无法撤销，建议先备份数据库。\n\n确定继续吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return False

        try:
            with self.connection:
                self.connection.execute("DELETE FROM books")
                self.connection.execute("DELETE FROM scan_roots")
                self.connection.execute("DELETE FROM classification_feedback")
                self.connection.execute("DELETE FROM tags")
                self.connection.execute("DELETE FROM categories")
                self.connection.execute(
                    "INSERT INTO settings(key, value) VALUES ('default_library_path', '') "
                    "ON CONFLICT(key) DO UPDATE SET value=''"
                )
                self.connection.execute(
                    "INSERT INTO settings(key, value) VALUES ('auto_scan', '0') "
                    "ON CONFLICT(key) DO UPDATE SET value='0'"
                )
        except sqlite3.Error as error:
            QMessageBox.critical(self, "清空失败", f"书库未能完整清空：\n{error}")
            return False

        self.scan_queue = []
        self.pending_import_ids = []
        self.selected_id = None
        self.selected_tags = []
        self.current_filter_kind = "group"
        self.current_filter_value = "all"
        self.current_filter_title = "全部书籍"
        self._set_setting("filter_kind", "group")
        self._set_setting("filter_value", "all")
        self.sidebar.refresh()
        self.refresh_books()
        self.status.setText("书籍、扫描路径、分类和标签已清空；原始电子书文件保留")
        QMessageBox.information(
            self, "清空完成", "书籍、扫描路径、分类和标签已清空。下次扫描会在本地分析后重新生成。\n\n"
            "磁盘上的原始电子书文件均已保留。"
        )
        return True

    def _test_ai_connection(self, base_url: str, model: str, entered_key: str, parent: QWidget | None = None) -> None:
        if hasattr(self, "ai_connection_worker") and self.ai_connection_worker.isRunning():
            return
        if not base_url.strip() or not model.strip():
            QMessageBox.warning(parent or self, "连接测试", "请先填写 API 地址和模型名称。")
            return
        key = entered_key or get_secret("Readplan", "ai_api_key")
        self.status.setText("正在测试 AI 服务连接…")
        self.ai_connection_worker = AIConnectionWorker(base_url.strip(), model.strip(), key)
        self._ai_settings_dialog = parent
        self.ai_connection_worker.completed.connect(self._ai_connection_finished)
        self.ai_connection_worker.start()

    def _ai_connection_finished(self, succeeded: bool, detail: str) -> None:
        self.status.setText("AI 服务连接成功" if succeeded else "AI 服务连接失败")
        parent = getattr(self, "_ai_settings_dialog", None) or self
        try:
            if not parent.isVisible():
                parent = self
        except RuntimeError:
            parent = self
        if succeeded:
            QMessageBox.information(parent, "连接成功", f"AI 服务已响应：{detail}")
        else:
            QMessageBox.warning(parent, "连接失败", detail)

    def backup_database(self) -> None:
        """Create a consistent SQLite backup, including committed WAL data."""
        filename, _ = QFileDialog.getSaveFileName(
            self, "备份数据库", "readplan-backup.db", "SQLite 数据库 (*.db *.sqlite);;所有文件 (*.*)"
        )
        if not filename:
            return
        if Path(filename).resolve() == self.database.path.resolve():
            QMessageBox.warning(self, "无法备份", "请选择新的备份文件路径，不要覆盖当前数据库。")
            return
        try:
            self.connection.commit()
            with closing(sqlite3.connect(filename)) as destination:
                self.connection.backup(destination)
            QMessageBox.information(self, "备份完成", f"数据库已备份到：\n{filename}")
        except (OSError, sqlite3.Error) as error:
            QMessageBox.critical(self, "备份失败", str(error))

    def restore_database(self) -> None:
        """Validate a backup before replacing the current database contents."""
        filename, _ = QFileDialog.getOpenFileName(
            self, "恢复数据库", str(Path.home()), "SQLite 数据库 (*.db *.sqlite);;所有文件 (*.*)"
        )
        if not filename:
            return
        if Path(filename).resolve() == self.database.path.resolve():
            QMessageBox.warning(self, "无法恢复", "当前正在使用该数据库文件，请选择一个备份文件。")
            return
        answer = QMessageBox.question(
            self, "确认恢复", "恢复将替换当前书库数据。建议先备份当前数据库，是否继续？"
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            source_uri = Path(filename).resolve().as_uri() + "?mode=ro"
            with closing(sqlite3.connect(source_uri, uri=True)) as source:
                integrity = source.execute("PRAGMA integrity_check").fetchone()
                tables = {row[0] for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if not integrity or integrity[0] != "ok" or not {"books", "categories", "tags"}.issubset(tables):
                    raise sqlite3.DatabaseError("所选文件不是有效的 Readplan 数据库备份。")
                source.backup(self.connection)
            self.connection.commit()
            self.selected_id = None
            self.sidebar.refresh()
            self.refresh_books()
            QMessageBox.information(self, "恢复完成", "数据库已恢复。")
        except (OSError, sqlite3.Error) as error:
            QMessageBox.critical(self, "恢复失败", str(error))

    def clear_cover_cache(self) -> None:
        """Remove only the app-owned cover cache directory."""
        cover_cache = self.database.path.expanduser().resolve().parent / "covers"
        try:
            if not cover_cache.exists():
                QMessageBox.information(self, "缓存清理", "当前没有封面缓存。")
                return
            shutil.rmtree(cover_cache)
            QPixmapCache.clear()
            QMessageBox.information(self, "缓存清理", "封面缓存已清理。")
        except OSError as error:
            QMessageBox.warning(self, "清理失败", str(error))

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:
        paths = [Path(url.toLocalFile()) for url in event.mimeData().urls() if url.isLocalFile()]
        folders = {str(path if path.is_dir() else path.parent) for path in paths}
        if folders:
            self.scan_queue = sorted(folders)
            self.start_scan(self.scan_queue.pop(0))
            event.acceptProposedAction()

    def eventFilter(self, watched, event) -> bool:
        if (watched is getattr(self, "_grid_viewport", None)
                and event.type() == QEvent.Type.Resize):
            QTimer.singleShot(0, self._virtualize_grid_cards)
        return super().eventFilter(watched, event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "grid"):
            QTimer.singleShot(0, self._virtualize_grid_cards)

    def closeEvent(self, event) -> None:
        if hasattr(self, "cover_worker") and self.cover_worker.isRunning():
            self.setEnabled(False)
            self.status.setText("封面补全完成后关闭窗口…")
            self.cover_worker.finished.connect(self.close)
            event.ignore()
            return
        if hasattr(self, "scan_worker") and self.scan_worker.isRunning():
            self.scan_queue = []
            self.setEnabled(False)
            self.status.setText("扫描完成后关闭窗口…")
            self.scan_worker.finished.connect(self.close)
            event.ignore()
            return
        self.qsettings.setValue("geometry", self.saveGeometry())
        self._set_setting("splitter_sizes", json.dumps(self.splitter.sizes()))
        super().closeEvent(event)
