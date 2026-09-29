"""Library navigation sidebar backed by the local categories and tags tables."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QInputDialog, QLabel, QListWidget, QListWidgetItem, QMenu,
    QMessageBox, QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)


class CategoryTreeWidget(QTreeWidget):
    """Tree accepts book-card drags so dropping assigns a category."""
    booksDropped = Signal(int, list)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasFormat("application/x-ebook-book-ids"):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasFormat("application/x-ebook-book-ids"):
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:
        if not event.mimeData().hasFormat("application/x-ebook-book-ids"):
            super().dropEvent(event)
            return
        item = self.itemAt(event.position().toPoint())
        if not item:
            event.ignore()
            return
        try:
            book_ids = [int(value) for value in bytes(event.mimeData().data(
                "application/x-ebook-book-ids")).decode("ascii").split(",") if value]
        except (UnicodeDecodeError, ValueError):
            event.ignore()
            return
        category_id = int(item.data(0, Qt.ItemDataRole.UserRole))
        self.booksDropped.emit(category_id, book_ids)
        event.acceptProposedAction()


class LibrarySidebar(QWidget):
    filterRequested = Signal(str, object, str)
    tagsRequested = Signal(list, str)
    booksDropped = Signal(int, list)
    tagManagementRequested = Signal()

    SMART_GROUPS = [
        ("📚", "全部", "all"), ("⭐", "收藏", "favorite"), ("◷", "最近打开", "recent"),
        ("＋", "最近添加", "added"), ("📖", "在读", "reading"),
        ("✓", "已读", "finished"), ("○", "未读", "unread"),
    ]
    CATEGORY_ICONS = {
        "数学": "∑", "物理": "⚛", "计算机": "💻", "文学": "📖", "历史": "🏛",
        "哲学": "🧠", "其他": "📦", "化学": "⚗", "生物": "🧬", "医学": "🩺",
        "经济": "💰", "管理": "📊", "艺术": "🎨", "音乐": "🎵", "摄影": "📷",
        "天文": "🔭", "地理": "🌍", "工程": "⚙", "农业": "🌾", "语言": "🗣",
        "旅游": "✈", "美食": "🍜", "体育": "⚽", "小说": "📕", "诗歌": "✒",
        "漫画": "💬", "教材": "📘", "古籍": "📜", "职场": "💼", "互联网": "🌐",
        "设计": "🖌", "编程": "⌨", "算法": "🧮", "运维": "🛠", "安全": "🔒",
        "人工智能": "🤖",
    }

    def __init__(self, connection: sqlite3.Connection, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.connection = connection
        self.setObjectName("Sidebar")
        self._build()
        self.refresh()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 18, 10, 12)
        layout.setSpacing(8)
        brand = QLabel("READPLAN")
        brand.setObjectName("AppBrand")
        layout.addWidget(brand)

        self.smart_list = QListWidget()
        self.smart_list.setMaximumHeight(220)
        for icon, name, key in self.SMART_GROUPS:
            item = QListWidgetItem(f"{icon}   {name}")
            item.setData(Qt.ItemDataRole.UserRole, key)
            self.smart_list.addItem(item)
        self.smart_list.itemClicked.connect(self._on_smart_clicked)
        self.smart_list.setCurrentRow(0)
        layout.addWidget(self.smart_list)

        self.cat_header = QLabel("分类")
        self.cat_header.setObjectName("SectionTitle")
        layout.addWidget(self.cat_header)
        self.category_tree = CategoryTreeWidget()
        self.category_tree.setAcceptDrops(True)
        self.category_tree.setDragDropMode(QTreeWidget.DragDropMode.DropOnly)
        self.category_tree.booksDropped.connect(self.booksDropped)
        self.category_tree.setHeaderHidden(True)
        self.category_tree.setIndentation(18)
        self.category_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.category_tree.customContextMenuRequested.connect(self._category_menu)
        self.category_tree.itemClicked.connect(self._on_category_clicked)
        layout.addWidget(self.category_tree, 2)
        self.add_category_button = QPushButton("＋  新建分类")
        self.add_category_button.setObjectName("Quiet")
        self.add_category_button.clicked.connect(lambda: self._create_category(None))
        layout.addWidget(self.add_category_button)

        self.tag_header = QLabel("标签")
        self.tag_header.setObjectName("SectionTitle")
        tag_header_row = QHBoxLayout()
        tag_header_row.addWidget(self.tag_header, 1)
        self.manage_tags_button = QPushButton("管理")
        self.manage_tags_button.setObjectName("Quiet")
        self.manage_tags_button.clicked.connect(self.tagManagementRequested)
        tag_header_row.addWidget(self.manage_tags_button)
        layout.addLayout(tag_header_row)
        self.tag_logic = QComboBox()
        self.tag_logic.addItem("满足任一标签", "OR")
        self.tag_logic.addItem("满足全部标签", "AND")
        self.tag_logic.currentIndexChanged.connect(self._emit_tags)
        layout.addWidget(self.tag_logic)
        self.tag_list = QListWidget()
        self.tag_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.tag_list.itemSelectionChanged.connect(self._emit_tags)
        layout.addWidget(self.tag_list, 1)

    def refresh(self) -> None:
        """Reload category/tag names and their current book counts."""
        self.category_tree.clear()
        categories = [dict(row) for row in self.connection.execute(
            "SELECT c.*, (SELECT COUNT(DISTINCT b.id) FROM books b "
            "LEFT JOIN book_categories bc ON bc.book_id=b.id "
            "WHERE b.category_id=c.id OR bc.category_id=c.id) AS book_count "
            "FROM categories c ORDER BY c.sort_order, c.name COLLATE NOCASE"
        )]
        by_id = {int(category["id"]): category for category in categories}
        children: dict[int, list[dict]] = {}
        roots: list[dict] = []
        for category in categories:
            parent_id = category.get("parent_id")
            if parent_id is not None and int(parent_id) in by_id:
                children.setdefault(int(parent_id), []).append(category)
            else:
                roots.append(category)
        order_key = lambda category: (category.get("sort_order", 0), category["name"].casefold())
        roots.sort(key=order_key)
        for values in children.values():
            values.sort(key=order_key)
        visited: set[int] = set()

        def add_category(category: dict, parent_item: QTreeWidgetItem | None = None) -> None:
            category_id = int(category["id"])
            if category_id in visited:
                return
            visited.add(category_id)
            icon = category.get("icon") or self.CATEGORY_ICONS.get(category["name"], "•")
            label = f"{icon}  {category['name']}   ({category['book_count']})"
            item = QTreeWidgetItem([label])
            item.setData(0, Qt.ItemDataRole.UserRole, category_id)
            item.setData(0, Qt.ItemDataRole.UserRole + 1, category["name"])
            if parent_item is not None:
                parent_item.addChild(item)
            else:
                self.category_tree.addTopLevelItem(item)
            for child in children.get(category_id, []):
                add_category(child, item)

        for category in roots:
            add_category(category)
        # Recover orphaned/cyclic legacy rows as roots rather than hiding them.
        for category in sorted(categories, key=order_key):
            if int(category["id"]) not in visited:
                add_category(category)

        self.tag_list.clear()
        for row in self.connection.execute(
            "SELECT t.id, t.name, COUNT(DISTINCT bt.book_id) AS book_count "
            "FROM tags t LEFT JOIN book_tags bt ON bt.tag_id=t.id "
            "GROUP BY t.id ORDER BY t.name COLLATE NOCASE"
        ):
            item = QListWidgetItem(f"# {row['name']}   ({row['book_count']})")
            item.setData(Qt.ItemDataRole.UserRole, row["id"])
            self.tag_list.addItem(item)

    def _on_smart_clicked(self, item: QListWidgetItem) -> None:
        key = str(item.data(Qt.ItemDataRole.UserRole))
        title = next(name for _icon, name, value in self.SMART_GROUPS if value == key)
        self.category_tree.clearSelection()
        self.tag_list.clearSelection()
        self.filterRequested.emit("group", key, title)

    def _on_category_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        self.smart_list.clearSelection()
        self.tag_list.clearSelection()
        self.filterRequested.emit("category", item.data(0, Qt.ItemDataRole.UserRole),
                                  str(item.data(0, Qt.ItemDataRole.UserRole + 1)))

    def _emit_tags(self) -> None:
        selected = [item.data(Qt.ItemDataRole.UserRole) for item in self.tag_list.selectedItems()]
        if selected:
            self.smart_list.clearSelection()
            self.category_tree.clearSelection()
        self.tagsRequested.emit(selected, str(self.tag_logic.currentData()))

    def _category_menu(self, point) -> None:
        item = self.category_tree.itemAt(point)
        menu = QMenu(self)
        create = menu.addAction("新建分类" if item is None else "新建子分类")
        rename = menu.addAction("重命名") if item else None
        move = menu.addAction("移动到层级…") if item else None
        change_icon = menu.addAction("修改图标") if item else None
        delete = menu.addAction("删除") if item else None
        action = menu.exec(self.category_tree.viewport().mapToGlobal(point))
        if action is create:
            self._create_category(item.data(0, Qt.ItemDataRole.UserRole) if item else None)
        elif action is rename and item:
            self._rename_category(item)
        elif action is move and item:
            self._move_category(item)
        elif action is change_icon and item:
            self._edit_icon(item)
        elif action is delete and item:
            self._delete_category(item)

    def _create_category(self, parent_id: int | None) -> None:
        name, accepted = QInputDialog.getText(self, "新建分类", "分类名称")
        if not accepted or not name.strip():
            return
        try:
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            self.connection.execute(
                "INSERT INTO categories(name, normalized_name, parent_id, created_time) VALUES (?, ?, ?, ?)",
                (name.strip(), name.strip().casefold(), parent_id, now),
            )
            self.connection.commit()
            self.refresh()
        except sqlite3.IntegrityError as error:
            QMessageBox.warning(self, "无法创建分类", f"分类名称可能已存在。\n{error}")

    def _rename_category(self, item: QTreeWidgetItem) -> None:
        category_id = item.data(0, Qt.ItemDataRole.UserRole)
        current_name = str(item.data(0, Qt.ItemDataRole.UserRole + 1))
        name, accepted = QInputDialog.getText(self, "重命名分类", "分类名称", text=current_name)
        if not accepted or not name.strip():
            return
        try:
            self.connection.execute(
                "UPDATE categories SET name=?, normalized_name=? WHERE id=?",
                (name.strip(), name.strip().casefold(), category_id),
            )
            self.connection.commit()
            self.refresh()
        except sqlite3.IntegrityError as error:
            QMessageBox.warning(self, "无法重命名", f"分类名称可能已存在。\n{error}")

    def _edit_icon(self, item: QTreeWidgetItem) -> None:
        category_id = item.data(0, Qt.ItemDataRole.UserRole)
        icon, accepted = QInputDialog.getText(self, "修改分类图标", "输入一个符号或 Emoji")
        if accepted:
            self.connection.execute("UPDATE categories SET icon=? WHERE id=?", (icon.strip() or None, category_id))
            self.connection.commit()
            self.refresh()

    def _move_category(self, item: QTreeWidgetItem) -> None:
        category_id = int(item.data(0, Qt.ItemDataRole.UserRole))
        rows = [dict(row) for row in self.connection.execute("SELECT id,name,parent_id FROM categories")]
        children: dict[int, set[int]] = {}
        for row in rows:
            if row["parent_id"] is not None:
                children.setdefault(int(row["parent_id"]), set()).add(int(row["id"]))
        descendants: set[int] = set()
        stack = list(children.get(category_id, set()))
        while stack:
            current = stack.pop()
            if current in descendants:
                continue
            descendants.add(current)
            stack.extend(children.get(current, set()))
        parents = [(None, "顶级分类")] + [
            (int(row["id"]), str(row["name"])) for row in rows
            if int(row["id"]) != category_id and int(row["id"]) not in descendants
        ]
        choice, accepted = QInputDialog.getItem(self, "移动分类层级", "新的父分类", [name for _, name in parents], 0, False)
        if not accepted:
            return
        parent_id = next((value for value, name in parents if name == choice), None)
        self.connection.execute("UPDATE categories SET parent_id=? WHERE id=?", (parent_id, category_id))
        self.connection.commit()
        self.refresh()

    def _delete_category(self, item: QTreeWidgetItem) -> None:
        category_id = item.data(0, Qt.ItemDataRole.UserRole)
        name = item.data(0, Qt.ItemDataRole.UserRole + 1)
        answer = QMessageBox.question(self, "删除分类", f"删除分类“{name}”？书籍文件不会被删除。")
        if answer == QMessageBox.StandardButton.Yes:
            self.connection.execute("DELETE FROM categories WHERE id=?", (category_id,))
            self.connection.commit()
            self.refresh()

    def set_compact(self, compact: bool) -> None:
        """Show a narrow icon-only rail when compact mode is requested."""
        brand = self.findChild(QLabel, "AppBrand")
        if brand:
            brand.setVisible(not compact)
        self.category_tree.setVisible(not compact)
        self.cat_header.setVisible(not compact)
        self.add_category_button.setVisible(not compact)
        self.tag_header.setVisible(not compact)
        self.manage_tags_button.setVisible(not compact)
        self.tag_list.setVisible(not compact)
        self.tag_logic.setVisible(not compact)
        self.smart_list.setMaximumHeight(260 if compact else 220)
        for index in range(self.smart_list.count()):
            item = self.smart_list.item(index)
            key = item.data(Qt.ItemDataRole.UserRole)
            icon, name, _ = next(row for row in self.SMART_GROUPS if row[2] == key)
            item.setText(icon if compact else f"{icon}   {name}")
