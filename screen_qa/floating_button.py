"""桌面上的悬浮小按钮:左键截图,右键出菜单,可以拖着走。

防误触做了这几件事:
- 按下后移动超过阈值(默认 6px)就当成"拖动",松手不会触发截图;
- 可以设成"双击才截图";
- 平时半透明,鼠标移上去才变清楚;
- 靠边自动吸附,不挡路;
- 截图时会先把自己藏起来,免得被拍进去。
"""

from __future__ import annotations

import time

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QWidget

from .config import assets_dir, get

DRAG_THRESHOLD = 6      # 超过这个像素就算拖动
DOUBLE_CLICK_MS = 400   # 双击判定的间隔


class FloatingButton(QWidget):
    """常驻桌面的小圆钮。"""

    capture_requested = Signal()
    menu_requested = Signal(QPoint)
    position_changed = Signal(int, int)

    def __init__(self, cfg: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._cfg = cfg
        self._press_pos: QPoint | None = None
        self._window_pos: QPoint | None = None
        self._dragged = False
        self._last_click_at = 0.0
        self._icon: QPixmap | None = None

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("左键截图搜题 · 右键更多功能 · 拖动可移动")

        self.apply_config(cfg)
        self._restore_position()

    # ---------- 配置 ----------

    def apply_config(self, cfg: dict) -> None:
        self._cfg = cfg
        size = int(get(cfg, "floating_button.size", 46) or 46)
        self.setFixedSize(max(32, min(size, 96)), max(32, min(size, 96)))
        self._set_idle_opacity()
        self.update()

    def _idle_opacity(self) -> float:
        return float(get(self._cfg, "floating_button.opacity", 0.62) or 0.62)

    def _set_idle_opacity(self) -> None:
        self.setWindowOpacity(max(0.2, min(1.0, self._idle_opacity())))

    def _double_click_mode(self) -> bool:
        return str(get(self._cfg, "floating_button.trigger", "single")) == "double"

    # ---------- 位置 ----------

    def _restore_position(self) -> None:
        pos = get(self._cfg, "floating_button.pos", None)
        screen = QApplication.primaryScreen()
        area = screen.availableGeometry() if screen else QRect(0, 0, 1280, 720)
        if isinstance(pos, list) and len(pos) == 2:
            point = QPoint(int(pos[0]), int(pos[1]))
        else:
            point = QPoint(area.right() - self.width() - 40, area.top() + area.height() // 3)
        self.move(self._clamp_and_snap(point))

    def _screen_for(self, point: QPoint) -> QRect:
        screen = QApplication.screenAt(point) or QApplication.primaryScreen()
        return screen.availableGeometry() if screen else QRect(0, 0, 1280, 720)

    def _clamp_and_snap(self, point: QPoint) -> QPoint:
        """限制在屏幕内,并吸附到最近的水平边缘。"""
        area = self._screen_for(point)
        margin = 8
        x = max(area.left() + margin, min(point.x(), area.right() - self.width() - margin))
        y = max(area.top() + margin, min(point.y(), area.bottom() - self.height() - margin))
        if bool(get(self._cfg, "floating_button.snap_edges", True)):
            if x + self.width() // 2 < area.center().x():
                x = area.left() + margin
            else:
                x = area.right() - self.width() - margin
        return QPoint(x, y)

    # ---------- 事件 ----------

    def mousePressEvent(self, event):  # noqa: N802
        # 右键不要在这里处理:Windows 会先发 mousePress 再发 WM_CONTEXTMENU,
        # 两条路都弹菜单的话,第二个事件会立刻把刚弹出来的菜单关掉(看起来就是"右键无效")。
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self._press_pos = event.globalPosition().toPoint()
        self._window_pos = self.pos()
        self._dragged = False

    def mouseMoveEvent(self, event):  # noqa: N802
        if self._press_pos is None or self._window_pos is None:
            return
        delta = event.globalPosition().toPoint() - self._press_pos
        if not self._dragged and delta.manhattanLength() <= DRAG_THRESHOLD:
            return
        self._dragged = True
        self.move(self._window_pos + delta)

    def mouseReleaseEvent(self, event):  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton or self._press_pos is None:
            return
        self._press_pos = None
        if self._dragged:
            # 拖动结束:吸边并记住位置
            self.move(self._clamp_and_snap(self.pos()))
            self.position_changed.emit(self.x(), self.y())
            return

        if self._double_click_mode():
            now = time.monotonic()
            if now - self._last_click_at <= DOUBLE_CLICK_MS / 1000.0:
                self._last_click_at = 0.0
                self.capture_requested.emit()
            else:
                self._last_click_at = now
        else:
            self.capture_requested.emit()

    def contextMenuEvent(self, event):  # noqa: N802
        self.menu_requested.emit(event.globalPos())

    def enterEvent(self, event):  # noqa: N802
        self.setWindowOpacity(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802
        self._set_idle_opacity()
        super().leaveEvent(event)

    # ---------- 绘制 ----------

    def _load_icon(self) -> QPixmap | None:
        if self._icon is not None:
            return self._icon
        for name in ("icon.png", "icon.ico"):
            path = assets_dir() / name
            if path.exists():
                pixmap = QPixmap(str(path))
                if not pixmap.isNull():
                    self._icon = pixmap.scaled(
                        self.width() - 12, self.height() - 12,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                    return self._icon
        return None

    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = self.rect().adjusted(2, 2, -2, -2)

        # 圆形底
        path = QPainterPath()
        path.addEllipse(rect)
        painter.fillPath(path, QColor(28, 30, 38, 235))
        painter.setPen(QPen(QColor(76, 155, 255, 210), 2))
        painter.drawPath(path)

        icon = self._load_icon()
        if icon is not None:
            painter.drawPixmap(
                (self.width() - icon.width()) // 2,
                (self.height() - icon.height()) // 2,
                icon,
            )
        else:
            painter.setPen(QPen(QColor(235, 240, 248), 3))
            painter.drawEllipse(rect.adjusted(12, 12, -12, -12))