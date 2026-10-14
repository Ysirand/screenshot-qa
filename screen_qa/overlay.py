"""截图遮罩:先给每个屏幕拍一张快照铺满全屏,再让用户在上面拖框/点窗口。

- 拖动 = 框选任意区域
- 单击 = 选中光标所在的那个窗口(自动高亮提示)
- Esc / 右键 = 取消
- 回车 = 直接选用当前高亮区域
"""

from __future__ import annotations

import ctypes

from PySide6.QtCore import QObject, QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QWidget

from . import capture, winutil
MIN_SELECTION = 8  # 小于这个尺寸(逻辑像素)就当成"单击"


class _Point(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def _cursor_physical_pos() -> tuple[int, int]:
    pt = _Point()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    return int(pt.x), int(pt.y)


class ScreenOverlay(QWidget):
    """单个屏幕上的全屏截图遮罩。"""

    picked = Signal(QPixmap, QRect)   # (裁好的图, 屏幕全局逻辑坐标)
    closed = Signal()

    def __init__(self, screen, pixmap: QPixmap, show_highlight: bool) -> None:
        super().__init__(None)
        self._screen = screen
        self._pixmap = pixmap
        self._show_highlight = show_highlight
        self._dragging = False
        self._start = QPoint()
        self._current = QRect()
        self._hover = QRect()
        self._finished = False
        self._phys_rect: tuple[int, int, int, int] | None = None

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setScreen(screen)
        self.setGeometry(screen.geometry())

    # ---------- 坐标换算 ----------

    def _scale(self) -> tuple[float, float]:
        """屏幕逻辑像素 -> 快照像素 的缩放比。"""
        width = max(1, self.width())
        height = max(1, self.height())
        return self._pixmap.width() / width, self._pixmap.height() / height

    def physical_rect(self) -> tuple[int, int, int, int] | None:
        """本遮罩窗口在物理桌面坐标里的矩形(用来把全局鼠标坐标换算到本地)。"""
        if self._phys_rect is None:
            try:
                self._phys_rect = winutil.get_window_rect(int(self.winId()))
            except Exception:
                self._phys_rect = None
        return self._phys_rect

    def _global_phys_to_local(self, x: int, y: int) -> QPoint | None:
        rect = self.physical_rect()
        if rect is None:
            return None
        left, top, width, height = rect
        if width <= 0 or height <= 0:
            return None
        sx = self.width() / width
        sy = self.height() / height
        return QPoint(int(round((x - left) * sx)), int(round((y - top) * sy)))

    def crop_pixmap(self, local_rect: QRect) -> QPixmap:
        sx, sy = self._scale()
        src = QRect(
            int(round(local_rect.x() * sx)),
            int(round(local_rect.y() * sy)),
            max(1, int(round(local_rect.width() * sx))),
            max(1, int(round(local_rect.height() * sy))),
        )
        src = src.intersected(self._pixmap.rect())
        return self._pixmap.copy(src)

    # ---------- 选区状态 ----------

    def _selection(self) -> QRect:
        if self._dragging and not self._current.isNull():
            return self._current
        if not self._hover.isNull():
            return self._hover
        return QRect()

    def _set_current(self, end: QPoint) -> None:
        self._current = QRect(self._start, end).normalized()
        self.update()

    def _finish(self, rect: QRect) -> None:
        if self._finished:
            return
        self._finished = True
        rect = rect.intersected(self.rect())
        if rect.width() < 1 or rect.height() < 1:
            self.picked.emit(QPixmap(), QRect())
            return
        self.picked.emit(self.crop_pixmap(rect), rect)

    # ---------- 事件 ----------

    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.RightButton:
            self.closed.emit()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self._dragging = True
        self._start = event.position().toPoint()
        self._current = QRect(self._start, self._start)
        self.update()

    def mouseMoveEvent(self, event):  # noqa: N802
        if self._dragging:
            self._set_current(event.position().toPoint())
            return
        if not self._show_highlight:
            return
        x, y = _cursor_physical_pos()
        found = winutil.window_under_point(x, y)
        rect = QRect()
        if found is not None:
            _, (left, top, width, height) = found
            top_left = self._global_phys_to_local(left, top)
            bottom_right = self._global_phys_to_local(left + width, top + height)
            if top_left is not None and bottom_right is not None:
                rect = QRect(top_left, bottom_right).normalized().intersected(self.rect())
                if rect.width() < MIN_SELECTION or rect.height() < MIN_SELECTION:
                    rect = QRect()
        if rect != self._hover:
            self._hover = rect
            self.update()

    def mouseReleaseEvent(self, event):  # noqa: N802
        if not self._dragging:
            return
        self._dragging = False
        self._set_current(event.position().toPoint())
        if (
            self._current.width() >= MIN_SELECTION
            and self._current.height() >= MIN_SELECTION
        ):
            self._finish(self._current)
            return
        # 拖动距离太小 —— 当成单击:优先选光标下的窗口,否则整屏
        if not self._hover.isNull():
            self._finish(self._hover)
        else:
            self._finish(self.rect())
        self.update()

    def keyPressEvent(self, event):  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.closed.emit()
        elif event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            selection = self._selection()
            self._finish(selection if not selection.isNull() else self.rect())
        else:
            super().keyPressEvent(event)

    # ---------- 绘制 ----------

    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.drawPixmap(self.rect(), self._pixmap)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 110))

        selection = self._selection()
        if not selection.isNull() and selection.isValid():
            sx, sy = self._scale()
            source = QRect(
                int(selection.x() * sx),
                int(selection.y() * sy),
                max(1, int(selection.width() * sx)),
                max(1, int(selection.height() * sy)),
            )
            painter.drawPixmap(selection, self._pixmap, source)

            accent = QColor(0, 174, 255)
            painter.setPen(QPen(accent, 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(selection.adjusted(0, 0, -1, -1))

            if self._dragging:
                self._draw_size_label(painter, selection, accent)

        self._draw_hint(painter)

    def _draw_size_label(self, painter: QPainter, rect: QRect, accent: QColor) -> None:
        sx, sy = self._scale()
        text = "{0} x {1}".format(
            max(1, int(round(rect.width() * sx))), max(1, int(round(rect.height() * sy)))
        )
        font = QFont()
        font.setPointSize(10)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        box = QRect(0, 0, metrics.horizontalAdvance(text) + 14, metrics.height() + 8)
        box.moveTopLeft(QPoint(rect.left(), rect.top() - box.height() - 4))
        if box.top() < 4:
            box.moveTop(rect.top() + 4)
        if box.right() > self.width() - 4:
            box.moveRight(self.width() - 4)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(accent)
        painter.drawRoundedRect(box, 4, 4)
        painter.setPen(QColor(255, 255, 255))
        painter.drawText(box, Qt.AlignmentFlag.AlignCenter, text)

    def _draw_hint(self, painter: QPainter) -> None:
        if self._dragging:
            return
        text = "拖动框选题目 · 单击选中整个窗口 · Enter 确认 · Esc 取消"
        font = QFont()
        font.setPointSize(11)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        width = metrics.horizontalAdvance(text) + 32
        height = metrics.height() + 16
        box = QRect(0, 0, width, height)
        box.moveCenter(QPoint(self.width() // 2, 40 + height // 2))

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 170))
        painter.drawRoundedRect(box, 8, 8)
        painter.setPen(QColor(235, 240, 248))
        painter.drawText(box, Qt.AlignmentFlag.AlignCenter, text)


class SelectionOverlay(QObject):
    """管理所有屏幕上的遮罩,合并成一次选择。"""

    selected = Signal(QPixmap, QRect)
    cancelled = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._overlays: list[ScreenOverlay] = []
        self._done = False

    @property
    def active(self) -> bool:
        return bool(self._overlays)

    def start(self, show_highlight: bool = True) -> None:
        app = QApplication.instance()
        if app is None:
            return
        self.close_all()
        self._done = False

        screens = app.screens()
        if not screens:
            self.cancelled.emit()
            return

        cursor_screen = QApplication.screenAt(QCursor.pos())
        # 先拍照,再显示遮罩,避免把遮罩自身拍进去
        grabbed: list[tuple[object, QPixmap]] = []
        for screen in screens:
            grabbed.append((screen, capture.grab_screen(screen)))

        for screen, pixmap in grabbed:
            if pixmap.isNull():
                continue
            overlay = ScreenOverlay(screen, pixmap, show_highlight)
            overlay.picked.connect(self._on_picked)
            overlay.closed.connect(self._on_closed)
            self._overlays.append(overlay)

        if not self._overlays:
            self.cancelled.emit()
            return

        for overlay in self._overlays:
            overlay.showFullScreen()
            overlay.raise_()

        focus = self._overlays[0]
        if cursor_screen is not None:
            for overlay in self._overlays:
                if overlay._screen is cursor_screen:
                    focus = overlay
                    break
        focus.activateWindow()
        focus.setFocus(Qt.FocusReason.OtherFocusReason)

    def _on_picked(self, pixmap: QPixmap, rect: QRect) -> None:
        if self._done:
            return
        self._done = True

        sender = self.sender()
        origin = QPoint(0, 0)
        if isinstance(sender, ScreenOverlay):
            origin = sender._screen.geometry().topLeft()

        self.close_all()
        if pixmap.isNull():
            self.cancelled.emit()
            return
        self.selected.emit(pixmap, rect.translated(origin))

    def _on_closed(self) -> None:
        if self._done:
            return
        self._done = True
        self.close_all()
        self.cancelled.emit()

    def close_all(self) -> None:
        overlays, self._overlays = self._overlays, []
        for overlay in overlays:
            try:
                overlay.picked.disconnect()
                overlay.closed.disconnect()
            except (RuntimeError, TypeError):
                pass
            overlay.hide()
            overlay.close()
            overlay.deleteLater()