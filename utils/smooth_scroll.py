"""Shared smooth and inertial scrolling for Qt scroll areas and item views."""
from __future__ import annotations

import math
import time

from PySide6.QtCore import QAbstractAnimation, QEasingCurve, QEvent, QObject, QPropertyAnimation, Qt, QTimer
from PySide6.QtWidgets import QAbstractScrollArea, QListWidget, QScrollArea, QScrollBar


class AutoHideScrollBar(QScrollBar):
    """A thin scrollbar whose thumb fades away while the area is idle."""

    def __init__(self, orientation: Qt.Orientation, parent=None) -> None:
        super().__init__(orientation, parent)
        self.setProperty("scrolling", False)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.setInterval(850)
        self._hide_timer.timeout.connect(self._deactivate)
        self.valueChanged.connect(self.show_temporarily)
        self.rangeChanged.connect(self._range_changed)

    def _set_scrolling(self, active: bool) -> None:
        if bool(self.property("scrolling")) == active:
            return
        self.setProperty("scrolling", active)
        style = self.style()
        style.unpolish(self)
        style.polish(self)
        self.update()

    def _range_changed(self, minimum: int, maximum: int) -> None:
        if maximum <= minimum:
            self._hide_timer.stop()
            self._set_scrolling(False)

    def show_temporarily(self, *_args) -> None:
        if self.maximum() <= self.minimum():
            self._set_scrolling(False)
            return
        self._set_scrolling(True)
        self._hide_timer.start()

    def _deactivate(self) -> None:
        if self.underMouse():
            self._hide_timer.start(500)
            return
        self._set_scrolling(False)

    def enterEvent(self, event) -> None:
        self.show_temporarily()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hide_timer.start(500)
        super().leaveEvent(event)


class SmoothScrollController(QObject):
    """Smooth wheel, trackpad, and short-decay inertial scrolling."""

    def __init__(self, area: QAbstractScrollArea) -> None:
        super().__init__(area)
        self.area = area
        self.viewport = area.viewport()
        self.viewport.setMouseTracking(True)
        self.viewport.installEventFilter(self)
        self._scrollbars = (area.verticalScrollBar(), area.horizontalScrollBar())
        for scrollbar in self._scrollbars:
            scrollbar.installEventFilter(self)
        self._animation: QPropertyAnimation | None = None
        self._active_bar: QScrollBar | None = None
        self._target = 0.0
        self._velocity = 0.0
        self._last_wheel_at = 0.0
        self._last_frame_at = 0.0
        self._idle_timer = QTimer(self)
        self._idle_timer.setSingleShot(True)
        self._idle_timer.setInterval(220)
        self._idle_timer.timeout.connect(self._start_inertia)
        self._inertia_timer = QTimer(self)
        self._inertia_timer.setInterval(16)
        self._inertia_timer.timeout.connect(self._inertia_step)

    def eventFilter(self, watched, event) -> bool:
        if event.type() == QEvent.Type.Wheel and watched in self._scrollbars:
            return self._handle_wheel(event, watched)
        if watched is not self.viewport:
            if event.type() == QEvent.Type.Enter and watched in self._scrollbars:
                watched.show_temporarily()
            return False
        if event.type() == QEvent.Type.Wheel:
            return self._handle_wheel(event)
        if event.type() == QEvent.Type.MouseMove:
            pos = event.position().toPoint()
            margin = 12
            if pos.x() >= self.viewport.width() - margin:
                self.area.verticalScrollBar().show_temporarily()
            if pos.y() >= self.viewport.height() - margin:
                self.area.horizontalScrollBar().show_temporarily()
        return False

    @staticmethod
    def _axis_delta(event, horizontal: bool) -> tuple[int, int]:
        pixel = event.pixelDelta().x() if horizontal else event.pixelDelta().y()
        angle = event.angleDelta().x() if horizontal else event.angleDelta().y()
        if horizontal and not pixel and not angle:
            pixel, angle = event.pixelDelta().y(), event.angleDelta().y()
        return pixel, angle

    def _wheel_scrollbar(self, event, forced_bar=None):
        if forced_bar is not None:
            horizontal = forced_bar.orientation() == Qt.Orientation.Horizontal
            pixel, angle = self._axis_delta(event, horizontal)
            return forced_bar, pixel, angle
        horizontal = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        vertical_bar = self.area.verticalScrollBar()
        horizontal_bar = self.area.horizontalScrollBar()
        if horizontal and horizontal_bar.maximum() > horizontal_bar.minimum():
            pixel, angle = self._axis_delta(event, True)
            return horizontal_bar, pixel, angle
        if vertical_bar.maximum() > vertical_bar.minimum():
            pixel, angle = self._axis_delta(event, False)
            return vertical_bar, pixel, angle
        if horizontal_bar.maximum() > horizontal_bar.minimum():
            pixel, angle = self._axis_delta(event, True)
            return horizontal_bar, pixel, angle
        return None, 0, 0

    def _handle_wheel(self, event, forced_bar=None) -> bool:
        bar, pixel_delta, angle_delta = self._wheel_scrollbar(event, forced_bar)
        if bar is None:
            return False
        if pixel_delta:
            delta = -float(pixel_delta) * 0.78
        else:
            steps = float(angle_delta) / 120.0
            delta = -steps * max(22.0, self.area.fontMetrics().height() * 1.75)
        if not delta:
            return False

        now = time.perf_counter()
        interval = max(0.025, min(now - self._last_wheel_at, 0.18)) if self._last_wheel_at else 0.08
        instantaneous = delta / interval * 0.20
        if self._active_bar is bar:
            self._velocity = max(-440.0, min(440.0, self._velocity * 0.58 + instantaneous * 0.42))
        else:
            self._velocity = max(-440.0, min(440.0, instantaneous))
        self._last_wheel_at = now
        previous_bar = self._active_bar
        previous_animation = self._animation
        same_animation = previous_animation is not None and previous_bar is bar
        if previous_animation is not None:
            previous_animation.stop()
        if same_animation:
            target = self._target
            animation = previous_animation
        else:
            if previous_animation is not None:
                previous_animation.deleteLater()
            target = float(bar.value())
            animation = QPropertyAnimation(bar, b"value", self)
            self._animation = animation
        self._active_bar = bar
        self._idle_timer.start()
        self._inertia_timer.stop()

        target = max(float(bar.minimum()), min(float(bar.maximum()), target + delta))
        self._target = target
        animation.setDuration(210)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        animation.setStartValue(bar.value())
        animation.setEndValue(round(target))
        self._animation = animation
        animation.start()
        if isinstance(bar, AutoHideScrollBar):
            bar.show_temporarily()
        event.accept()
        return True

    def _start_inertia(self) -> None:
        if abs(self._velocity) < 45 or self._active_bar is None:
            self._velocity = 0.0
            return
        if self._animation and self._animation.state() == QAbstractAnimation.State.Running:
            self._animation.stop()
        self._last_frame_at = time.perf_counter()
        self._inertia_timer.start()

    def _inertia_step(self) -> None:
        bar = self._active_bar
        if bar is None:
            self._inertia_timer.stop()
            return
        now = time.perf_counter()
        elapsed = max(0.008, min(now - self._last_frame_at, 0.04))
        self._last_frame_at = now
        current = bar.value()
        next_value = max(bar.minimum(), min(bar.maximum(), round(current + self._velocity * elapsed)))
        bar.setValue(next_value)
        self._velocity *= math.exp(-6.5 * elapsed)
        if isinstance(bar, AutoHideScrollBar):
            bar.show_temporarily()
        if next_value in (bar.minimum(), bar.maximum()) or abs(self._velocity) < 24:
            self._velocity = 0.0
            self._inertia_timer.stop()


def _replace_scrollbar(area: QAbstractScrollArea, orientation: Qt.Orientation) -> None:
    getter = area.verticalScrollBar if orientation == Qt.Orientation.Vertical else area.horizontalScrollBar
    setter = area.setVerticalScrollBar if orientation == Qt.Orientation.Vertical else area.setHorizontalScrollBar
    current = getter()
    if isinstance(current, AutoHideScrollBar):
        return
    replacement = AutoHideScrollBar(orientation, area)
    replacement.setRange(current.minimum(), current.maximum())
    replacement.setPageStep(current.pageStep())
    replacement.setSingleStep(current.singleStep())
    replacement.setValue(current.value())
    setter(replacement)


def enable_smooth_scrolling(area: QAbstractScrollArea) -> SmoothScrollController:
    """Install the shared controller on one existing Qt scroll area."""
    controller = getattr(area, "_smooth_scroll_controller", None)
    if controller is not None:
        return controller
    _replace_scrollbar(area, Qt.Orientation.Vertical)
    _replace_scrollbar(area, Qt.Orientation.Horizontal)
    controller = SmoothScrollController(area)
    area._smooth_scroll_controller = controller
    return controller


class SmoothScrollManager(QObject):
    """Install smooth scrolling for current and future dialogs in the app."""

    def __init__(self, application, root: QObject | None = None) -> None:
        super().__init__(application)
        self.application = application
        application.installEventFilter(self)
        if root is not None:
            self.install_tree(root)

    def install_tree(self, root: QObject) -> None:
        if isinstance(root, QAbstractScrollArea):
            enable_smooth_scrolling(root)
        for area in root.findChildren(QAbstractScrollArea):
            enable_smooth_scrolling(area)

    def eventFilter(self, watched, event) -> bool:
        if event.type() == QEvent.Type.Show and isinstance(watched, QAbstractScrollArea):
            enable_smooth_scrolling(watched)
        return False


class SmoothScrollArea(QScrollArea):
    """Drop-in QScrollArea with smooth scrolling enabled."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        enable_smooth_scrolling(self)


class SmoothScrollList(QListWidget):
    """Drop-in QListWidget with smooth scrolling enabled."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        enable_smooth_scrolling(self)


def install_smooth_scrolling(application, root: QObject | None = None) -> SmoothScrollManager:
    """Create or reuse the application's shared scroll manager."""
    manager = getattr(application, "_smooth_scroll_manager", None)
    if manager is None:
        manager = SmoothScrollManager(application)
        application._smooth_scroll_manager = manager
    if root is not None:
        manager.install_tree(root)
    return manager