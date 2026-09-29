"""Book tag editing and global tag-library management dialogs."""
from __future__ import annotations

import sqlite3

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout,
)

from scanner.scanner import classify_book
from tag.service import delete_tag, ensure_tag, merge_tags, normalize_tag_name, rename_tag, set_book_tags


class BookTagDialog(QDialog):
    """Edit a book or a selection of books and see local tag suggestions."""

    def __init__(self, connection: sqlite3.Connection, book_ids: list[int], books: list[dict], parent=None):
        super().__init__(parent)
        self.connection = connection
        self.book_ids = list(dict.fromkeys(int(value) for value in book_ids))
        self.setWindowTitle("标签管理")
        self.setMinimumSize(460, 520)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("当前标签（选择后可移除）"))
        self.current = QListWidget()
        self.current.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        layout.addWidget(self.current, 1)
        remove = QPushButton("移除选中标签")
        remove.clicked.connect(self._remove_selected)
        layout.addWidget(remove)

        add_row = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText("输入标签，多个标签用逗号分隔")
        add = QPushButton("添加")
        add.clicked.connect(self._add_input)
        self.input.returnPressed.connect(self._add_input)
        add_row.addWidget(self.input, 1)
        add_row.addWidget(add)
        layout.addLayout(add_row)

        layout.addWidget(QLabel("本地规则推荐（点击添加）"))
        self.recommended = QListWidget()
        self.recommended.setMaximumHeight(130)
        layout.addWidget(self.recommended)
        self.pending_names = self._current_names()
        self.recommendation_names: list[str] = []
        for book in books:
            _categories, names = classify_book(book.get("path", ""), book.get("title", ""))
            self.recommendation_names.extend(names)
        self.recommendation_names = list(dict.fromkeys(self.recommendation_names))
        self.recommended.itemClicked.connect(self._add_recommended)
        self._refresh()

        manage = QPushButton("管理全部标签…")
        manage.clicked.connect(self._manage_library)
        layout.addWidget(manage, alignment=Qt.AlignmentFlag.AlignLeft)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _current_names(self) -> list[str]:
        if not self.book_ids:
            return []
        marks = ",".join("?" for _ in self.book_ids)
        return [row[0] for row in self.connection.execute(
            f"SELECT DISTINCT t.name FROM tags t JOIN book_tags bt ON bt.tag_id=t.id "
            f"WHERE bt.book_id IN ({marks}) ORDER BY t.name COLLATE NOCASE", self.book_ids)]

    def _refresh(self) -> None:
        self.current.clear()
        for name in self.pending_names:
            self.current.addItem(QListWidgetItem(f"#{name}   ×"))
        self.recommended.clear()
        existing = {name.casefold() for name in self.pending_names}
        suggestions = list(self.recommendation_names)
        suggestions.extend(str(row[0]) for row in self.connection.execute(
            "SELECT name FROM tags ORDER BY name COLLATE NOCASE LIMIT 80"))
        for name in dict.fromkeys(suggestions):
            if name.casefold() not in existing:
                item = QListWidgetItem(f"＋ #{name}")
                item.setData(Qt.ItemDataRole.UserRole, name)
                self.recommended.addItem(item)

    def _add_recommended(self, item: QListWidgetItem) -> None:
        name = str(item.data(Qt.ItemDataRole.UserRole) or "")
        if name and name.casefold() not in {value.casefold() for value in self.pending_names}:
            self.pending_names.append(name)
        self._refresh()

    def _add_input(self) -> None:
        values = [normalize_tag_name(value) for value in self.input.text().replace("，", ",").split(",")]
        for value in values:
            if value and value.casefold() not in {item.casefold() for item in self.pending_names}:
                self.pending_names.append(value)
        self.input.clear()
        self._refresh()

    def _remove_selected(self) -> None:
        names = {self.current.item(row).text().split("#", 1)[-1].rsplit("×", 1)[0].strip().casefold()
                 for row in sorted({index.row() for index in self.current.selectedIndexes()}, reverse=True)}
        self.pending_names = [name for name in self.pending_names if name.casefold() not in names]
        self._refresh()

    def _manage_library(self) -> None:
        TagLibraryDialog(self.connection, self).exec()
        self._refresh()

    def save(self) -> None:
        set_book_tags(self.connection, self.book_ids, self.pending_names, source="manual")

    def accept(self) -> None:
        self.save()
        super().accept()


class TagLibraryDialog(QDialog):
    """Rename, delete, and merge tags without losing book associations."""

    def __init__(self, connection: sqlite3.Connection, parent=None):
        super().__init__(parent)
        self.connection = connection
        self.setWindowTitle("标签库")
        self.setMinimumSize(420, 420)
        layout = QVBoxLayout(self)
        self.tags = QListWidget()
        self.tags.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        layout.addWidget(self.tags, 1)
        controls = QHBoxLayout()
        create = QPushButton("新建")
        rename = QPushButton("改名")
        delete = QPushButton("删除")
        merge_button = QPushButton("合并到…")
        controls.addWidget(create)
        controls.addWidget(rename)
        controls.addWidget(delete)
        controls.addWidget(merge_button)
        layout.addLayout(controls)
        create.clicked.connect(self._create)
        rename.clicked.connect(self._rename)
        delete.clicked.connect(self._delete)
        merge_button.clicked.connect(self._merge)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)
        self.refresh()

    def refresh(self) -> None:
        self.tags.clear()
        for row in self.connection.execute(
            "SELECT t.id,t.name,t.source,COUNT(bt.book_id) count FROM tags t "
            "LEFT JOIN book_tags bt ON bt.tag_id=t.id GROUP BY t.id ORDER BY t.name COLLATE NOCASE"):
            item = QListWidgetItem(f"#{row['name']}   · {row['source']}   · {row['count']} 本")
            item.setData(Qt.ItemDataRole.UserRole, int(row["id"]))
            item.setData(Qt.ItemDataRole.UserRole + 1, row["name"])
            self.tags.addItem(item)

    def _selected(self) -> list[QListWidgetItem]:
        return self.tags.selectedItems()

    def _create(self) -> None:
        name, accepted = QInputDialog.getText(self, "新建标签", "标签名称")
        if not accepted or not normalize_tag_name(name):
            return
        try:
            with self.connection:
                ensure_tag(self.connection, name, source="manual")
            self.refresh()
        except sqlite3.IntegrityError:
            QMessageBox.information(self, "标签已存在", "相同名称的标签已经在标签库中。")

    def _rename(self) -> None:
        items = self._selected()
        if len(items) != 1:
            return
        name, ok = QInputDialog.getText(self, "修改标签名称", "标签名称", text=items[0].data(Qt.ItemDataRole.UserRole + 1))
        if ok and name.strip():
            try:
                rename_tag(self.connection, int(items[0].data(Qt.ItemDataRole.UserRole)), name)
                self.refresh()
            except sqlite3.IntegrityError:
                QMessageBox.warning(self, "标签已存在", "该标签名称已经存在。")

    def _delete(self) -> None:
        ids = [int(item.data(Qt.ItemDataRole.UserRole)) for item in self._selected()]
        if ids and QMessageBox.question(self, "删除标签", f"删除所选 {len(ids)} 个标签及关联？") == QMessageBox.StandardButton.Yes:
            for tag_id in ids:
                delete_tag(self.connection, tag_id)
            self.refresh()

    def _merge(self) -> None:
        items = self._selected()
        if len(items) < 2:
            QMessageBox.information(self, "合并标签", "请至少选择两个标签；名称最前的标签将保留。")
            return
        target_id = int(items[0].data(Qt.ItemDataRole.UserRole))
        merge_tags(self.connection, [int(item.data(Qt.ItemDataRole.UserRole)) for item in items], target_id)
        self.refresh()
