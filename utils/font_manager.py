"""Application-wide font preferences backed by the SQLite settings table."""
from __future__ import annotations

import sqlite3

from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication


DEFAULT_FONT_SIZE = 13
MIN_FONT_SIZE = 10
MAX_FONT_SIZE = 24
SYSTEM_FONT = "system"

FONT_OPTIONS = (
    ("系统默认字体", SYSTEM_FONT, ()),
    ("微软雅黑", "Microsoft YaHei", ("Microsoft YaHei", "Microsoft YaHei UI")),
    ("Microsoft YaHei", "Microsoft YaHei", ("Microsoft YaHei", "Microsoft YaHei UI")),
    ("苹方 PingFang SC", "PingFang SC", ("PingFang SC",)),
    ("思源黑体", "Source Han Sans SC", ("Source Han Sans SC", "Source Han Sans CN", "Source Han Sans")),
    ("Noto Sans CJK", "Noto Sans CJK SC", ("Noto Sans CJK SC", "Noto Sans CJK", "Noto Sans CJK JP")),
    ("Arial", "Arial", ("Arial",)),
    ("Segoe UI", "Segoe UI", ("Segoe UI",)),
)


class FontManager:
    """Load, persist, resolve, and apply the application's global font."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.current_family = SYSTEM_FONT
        self.current_size = DEFAULT_FONT_SIZE
        self._available = {family.casefold(): family for family in QFontDatabase().families()}

    @staticmethod
    def system_family() -> str:
        return QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont).family()

    def available_family(self, family_id: str) -> str | None:
        if family_id == SYSTEM_FONT:
            return self.system_family()
        candidates = next(
            (candidate_names for _label, value, candidate_names in FONT_OPTIONS if value == family_id),
            (family_id,),
        )
        for candidate in candidates:
            actual = self._available.get(candidate.casefold())
            if actual:
                return actual
        return None

    def is_available(self, family_id: str) -> bool:
        return self.available_family(family_id) is not None

    @property
    def resolved_family(self) -> str:
        return self.available_family(self.current_family) or self.system_family()

    def load_font_setting(self) -> None:
        rows = {
            str(row["key"]): str(row["value"])
            for row in self.connection.execute(
                "SELECT key,value FROM settings WHERE key IN ('font_family','font_size')"
            )
        }
        family = rows.get("font_family", SYSTEM_FONT)
        if family != SYSTEM_FONT and not self.is_available(family):
            family = SYSTEM_FONT
        try:
            size = int(rows.get("font_size", DEFAULT_FONT_SIZE))
        except (TypeError, ValueError):
            size = DEFAULT_FONT_SIZE
        self.current_family = family
        self.current_size = max(MIN_FONT_SIZE, min(MAX_FONT_SIZE, size))
        if family != rows.get("font_family", SYSTEM_FONT):
            self._persist()

    def set_font(self, family_id: str, size: int, app: QApplication | None = None) -> bool:
        """Update and persist font preferences; return False when fallback was needed."""
        available = self.is_available(family_id)
        self.current_family = family_id if available else SYSTEM_FONT
        self.current_size = max(MIN_FONT_SIZE, min(MAX_FONT_SIZE, int(size)))
        self._persist()
        self.apply_font(app)
        return available

    def apply_font(self, app: QApplication | None = None) -> QFont:
        font = QFont(self.resolved_family)
        font.setPixelSize(self.current_size)
        target = app or QApplication.instance()
        if target is not None:
            target.setFont(font)
        return font

    def _persist(self) -> None:
        with self.connection:
            self.connection.executemany(
                "INSERT INTO settings(key,value) VALUES (?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (("font_family", self.current_family), ("font_size", str(self.current_size))),
            )
