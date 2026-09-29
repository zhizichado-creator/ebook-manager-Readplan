"""Application entry point for ebook_manager."""
from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from database.db import Database
from ui.main_window import MainWindow


def main() -> int:
    """Initialize the UI and local database, then start the Qt event loop."""
    # Use Qt-rendered dialogs so the application theme also applies to pickers.
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_DontUseNativeDialogs, True)
    app = QApplication(sys.argv)
    app.setApplicationName("ebook_manager")
    app.setOrganizationName("Readplan")

    # Database() initializes database/ebook.db and safely adds missing schema fields.
    database = Database()
    app.aboutToQuit.connect(database.close)
    print(f"ebook_manager database: {database.path}", flush=True)

    window = MainWindow(database)
    window.show()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
