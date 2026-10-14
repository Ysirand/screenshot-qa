"""轻量提示条:在屏幕角落闪一下,不打断操作。"""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter
from PySide6.QtWidgets import QWidget

_DEFAULT_MS = 2600


class Toast(QWidget):
    def __init__(self, text: str, msec: int = _DEFAULT_MS, accent: str = "#4C9BFF") -> None:
        super().__init__(None)
        self._text = text
        self._accent = QColor(accent)
        self._msec = msec

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

        font = QFont()
        font.setPointSize(11)
        self.setFont(font)
        metrics = self.fontMetrics()
        width = min(max(metrics.horizontalAdvance(text) + 40, 220), 720)
        self.resize(width, metrics.height() + 26)

    def show_bottom_center(self, msec: int | None = None) -> None:
        screen = QGuiApplication.screenAt(self.cursor().pos()) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        self.move(QPoint(area.center().x() - self.width() // 2, area.bottom() - self.height() - 90))
        self.show()
        self.raise_()
        QTimer.singleShot(msec or self._msec, self.close)

    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = self.rect().adjusted(1, 1, -1, -1)

        painter.setPen(QColor(255, 255, 255, 30))
        painter.setBrush(QColor(24, 26, 33, 242))
        painter.drawRoundedRect(rect, 10, 10)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._accent)
        painter.drawRoundedRect(rect.adjusted(0, 0, 0, 0).x(), rect.y() + 8, 3, rect.height() - 16, 1.5, 1.5)

        painter.setPen(QColor(232, 236, 244))
        painter.drawText(rect.adjusted(18, 0, -14, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self._text)


_ACTIVE: list[Toast] = []


def toast(text: str, msec: int = _DEFAULT_MS, accent: str = "#4C9BFF") -> Toast:
    """弹一条提示。必须留一个 Python 引用,否则窗口会被 GC 掉立刻消失。"""
    widget = Toast(text, msec, accent)
    _ACTIVE.append(widget)
    widget.destroyed.connect(lambda *_: _ACTIVE.remove(widget) if widget in _ACTIVE else None)
    widget.show_bottom_center()
    return widget
