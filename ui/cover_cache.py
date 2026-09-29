"""Shared in-memory and asynchronous cache for book-cover thumbnails."""
from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QSize, Qt, QThreadPool, Signal
from PySide6.QtGui import QImage, QPainter, QPainterPath, QPixmap, QPixmapCache
from PySide6.QtWidgets import QApplication


_version_cache: dict[str, tuple[float, str | None]] = {}
_VERSION_TTL = 3.0
_failed_cache_keys: set[str] = set()


def _source_key(path: str | Path) -> str | None:
    source = Path(path)
    path_key = str(source.absolute())
    now = time.monotonic()
    cached = _version_cache.get(path_key)
    if cached and now - cached[0] < _VERSION_TTL:
        return cached[1]
    try:
        stat = source.stat()
        if not source.is_file():
            raise OSError("not a file")
        version = f"{source.resolve()}:{stat.st_mtime_ns}:{stat.st_size}"
    except OSError:
        version = None
    _version_cache[path_key] = (now, version)
    return version


def _render_key(source_key: str, size: QSize, rounded_radius: int) -> str:
    return f"readplan-cover-render:{source_key}:{size.width()}x{size.height()}:{rounded_radius}"


class _CoverDecodeTask(QRunnable):
    def __init__(self, path: str, cache_key: str, size: QSize, radius: int, signal_owner) -> None:
        super().__init__()
        self.path = path
        self.cache_key = cache_key
        self.size = QSize(size)
        self.radius = radius
        self.signal_owner = signal_owner

    def run(self) -> None:
        image = QImage(self.path)
        if image.isNull():
            self.signal_owner.decoded.emit(self.cache_key, QImage())
            return
        image = image.scaled(
            self.size, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        if self.radius:
            rounded = QImage(image.size(), QImage.Format.Format_ARGB32_Premultiplied)
            rounded.fill(Qt.GlobalColor.transparent)
            painter = QPainter(rounded)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            path = QPainterPath()
            path.addRoundedRect(rounded.rect(), self.radius, self.radius)
            painter.setClipPath(path)
            painter.drawImage(0, 0, image)
            painter.end()
            image = rounded
        self.signal_owner.decoded.emit(self.cache_key, image)


class _CoverLoader(QObject):
    decoded = Signal(str, QImage)
    ready = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        pool = QThreadPool.globalInstance()
        pool.setMaxThreadCount(max(1, min(4, pool.maxThreadCount())))
        self._pending: set[str] = set()
        self.decoded.connect(self._cache_decoded)

    def request(self, path: str, cache_key: str, size: QSize, radius: int) -> None:
        if cache_key in self._pending:
            return
        self._pending.add(cache_key)
        QThreadPool.globalInstance().start(
            _CoverDecodeTask(path, cache_key, size, radius, self)
        )

    def _cache_decoded(self, cache_key: str, image: QImage) -> None:
        self._pending.discard(cache_key)
        if not image.isNull():
            QPixmapCache.insert(cache_key, QPixmap.fromImage(image))
        else:
            _failed_cache_keys.add(cache_key)
        self.ready.emit(cache_key)


_loader: _CoverLoader | None = None


def cover_loader() -> _CoverLoader | None:
    """Return the shared GUI-thread cache loader, if a QApplication exists."""
    global _loader
    app = QApplication.instance()
    if app is None:
        return None
    if _loader is None:
        _loader = _CoverLoader(app)
    return _loader


def load_cover_pixmap(
    path: str | Path,
    size: QSize | None = None,
    *,
    rounded_radius: int = 0,
) -> QPixmap | None:
    """Synchronously load a cover; repeated paths and sizes use QPixmapCache."""
    source = Path(path)
    source_key = _source_key(source)
    if source_key is None:
        return None
    if size is not None:
        cache_key = _render_key(source_key, size, rounded_radius)
        rendered = QPixmapCache.find(cache_key)
        if rendered is not None:
            return rendered

    base_key = f"readplan-cover-source:{source_key}"
    pixmap = QPixmapCache.find(base_key)
    if pixmap is None:
        pixmap = QPixmap(str(source))
        if pixmap.isNull():
            return None
        QPixmapCache.insert(base_key, pixmap)
    if size is None:
        return pixmap

    rendered = pixmap.scaled(
        size, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation,
    )
    if rounded_radius:
        rounded = QPixmap(rendered.size())
        rounded.fill(Qt.GlobalColor.transparent)
        painter = QPainter(rounded)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path_shape = QPainterPath()
        path_shape.addRoundedRect(rounded.rect(), rounded_radius, rounded_radius)
        painter.setClipPath(path_shape)
        painter.drawPixmap(0, 0, rendered)
        painter.end()
        rendered = rounded
    QPixmapCache.insert(_render_key(source_key, size, rounded_radius), rendered)
    return rendered


def request_cover_pixmap(
    path: str | Path,
    size: QSize,
    *,
    rounded_radius: int = 0,
    on_ready=None,
) -> tuple[QPixmap | None, str | None]:
    """Return a cached cover immediately or decode it off the UI thread."""
    source = Path(path)
    source_key = _source_key(source)
    if source_key is None:
        return None, None
    cache_key = _render_key(source_key, size, rounded_radius)
    cached = QPixmapCache.find(cache_key)
    if cached is not None:
        return cached, cache_key
    if cache_key in _failed_cache_keys:
        return None, cache_key
    loader = cover_loader()
    if loader is None:
        return load_cover_pixmap(source, size, rounded_radius=rounded_radius), cache_key
    if on_ready is not None:
        try:
            loader.ready.connect(on_ready)
        except (RuntimeError, TypeError):
            pass
    loader.request(str(source), cache_key, size, rounded_radius)
    return None, cache_key