"""桌面悬浮答案窗:置顶显示豆包的解答,可拖动、可缩放、可追问。"""

from __future__ import annotations

import time

from PySide6.QtCore import QEvent, QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizeGrip,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from . import markdown
from .config import get
from .toast import toast

_ACCENT = "#4C9BFF"
_ERROR = "#FF6B6B"
_OK = "#4ED18B"

_STYLE = """
#card {
    background-color: rgba(28, 30, 38, 250);
    border: 1px solid rgba(255, 255, 255, 28);
    border-radius: 14px;
}
#headerDot { font-size: 15px; }
#title { color: #E8ECF4; font-size: 13px; font-weight: 600; }
#status { color: #93A0B4; font-size: 12px; }
QTextBrowser {
    background: transparent;
    border: none;
    color: #E8ECF4;
    selection-background-color: #4C9BFF;
    selection-color: #FFFFFF;
}
QLineEdit {
    background: rgba(255, 255, 255, 20);
    border: 1px solid rgba(255, 255, 255, 34);
    border-radius: 9px;
    padding: 6px 10px;
    color: #E8ECF4;
    selection-background-color: #4C9BFF;
}
QLineEdit:focus { border: 1px solid #4C9BFF; }
QLineEdit:disabled { color: #6C7688; }
QPushButton {
    background: rgba(255, 255, 255, 20);
    border: 1px solid rgba(255, 255, 255, 30);
    border-radius: 9px;
    padding: 5px 12px;
    color: #DCE3EE;
}
QPushButton:hover { background: rgba(76, 155, 255, 70); border-color: rgba(76, 155, 255, 160); }
QPushButton:pressed { background: rgba(76, 155, 255, 110); }
QPushButton:disabled { color: #6C7688; background: rgba(255, 255, 255, 10); }
#closeButton { padding: 5px 11px; }
"""

_BODY_STYLE = """
p { margin: 7px 0; }
h3 { margin: 12px 0 4px 0; color: #8CC4FF; }
h4 { margin: 10px 0 4px 0; color: #8CC4FF; }
h5, h6 { margin: 8px 0 4px 0; color: #8CC4FF; }
ul, ol { margin: 4px 0 4px 18px; }
li { margin: 3px 0; }
pre {
    background-color: rgba(255, 255, 255, 22);
    border-radius: 8px;
    padding: 8px;
    margin: 6px 0;
    font-family: "Cascadia Mono", "Consolas", monospace;
}
code { background-color: rgba(255, 255, 255, 22); font-family: "Cascadia Mono", "Consolas", monospace; }
blockquote { color: #A9B6C9; margin-left: 10px; }
a { color: #7FBBFF; }
"""


class AnswerWindow(QWidget):
    """豆包解答的悬浮展示窗。"""

    followup_requested = Signal(str)
    recapture_requested = Signal()
    hidden_by_user = Signal()
    save_requested = Signal(str)          # 参数:分类名(空字符串 = 未分类)
    open_collection_requested = Signal()

    def __init__(self, cfg: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._cfg = cfg
        self._answer_md = ""
        self._busy = False
        self._drag_offset: QPoint | None = None
        self._user_moved = False
        self._started_at = 0.0
        self._dots = 1
        self._saved = False
        self._phase = "idle"   # idle / answer / followup
        self._question_rect = QRect()

        self.setWindowTitle("解答")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        if not bool(get(cfg, "appearance.activate_on_show", False)):
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

        self._build_ui()
        self._apply_appearance()

        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(1000)
        self._tick_timer.timeout.connect(self._on_tick)

        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(70)
        self._render_timer.timeout.connect(self._render_now)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._auto_hide)

    # ---------- 构建界面 ----------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 14, 14, 14)

        card = QFrame(self)
        card.setObjectName("card")
        outer.addWidget(card)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 6)
        shadow.setColor(QColor(0, 0, 0, 150))
        card.setGraphicsEffect(shadow)

        root = QVBoxLayout(card)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(8)

        header = QWidget(card)
        header.setFixedHeight(30)
        self._header = header
        header.installEventFilter(self)

        head_row = QHBoxLayout(header)
        head_row.setContentsMargins(0, 0, 0, 0)
        head_row.setSpacing(8)

        self._dot = QLabel("●", header)
        self._dot.setObjectName("headerDot")
        self._dot.setStyleSheet("color: " + _ACCENT + ";")
        self._title = QLabel("解答", header)
        self._title.setObjectName("title")
        header.setToolTip("按住这里(或窗口空白处)可以拖动这个窗口")
        self._status = QLabel("待命", header)
        self._status.setObjectName("status")
        self._status.setMinimumWidth(104)
        for widget in (self._dot, self._title, self._status):
            widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        head_row.addWidget(self._dot)
        head_row.addWidget(self._title)
        head_row.addWidget(self._status)
        head_row.addStretch(1)

        self._save_button = QPushButton("收藏", header)
        self._save_button.setToolTip("把这道题收进收藏夹(可以选分类)")
        self._save_button.clicked.connect(self._on_save_clicked)
        self._save_button.setEnabled(False)

        self._copy_button = QPushButton("复制", header)
        self._copy_button.setToolTip("复制答案(Ctrl+C)")
        self._copy_button.clicked.connect(self.copy_answer)
        self._copy_button.setEnabled(False)

        self._again_button = QPushButton("重新截图", header)
        self._again_button.setToolTip("重新框选题目")
        self._again_button.clicked.connect(self.recapture_requested.emit)

        self._close_button = QPushButton("✕", header)
        self._close_button.setObjectName("closeButton")
        self._close_button.setToolTip("隐藏(Esc)")
        self._close_button.clicked.connect(self.hide_by_user)

        head_row.addWidget(self._save_button)
        head_row.addWidget(self._copy_button)
        head_row.addWidget(self._again_button)
        head_row.addWidget(self._close_button)
        root.addWidget(header)

        self._body = QTextBrowser(card)
        self._body.setOpenExternalLinks(True)
        self._body.setFrameShape(QFrame.Shape.NoFrame)
        self._body.document().setDocumentMargin(2)
        root.addWidget(self._body, 1)

        footer = QWidget(card)
        foot_row = QHBoxLayout(footer)
        foot_row.setContentsMargins(0, 0, 0, 0)
        foot_row.setSpacing(8)

        self._followup = QLineEdit(footer)
        self._followup.setPlaceholderText("对这道题继续追问…(回车发送)")
        self._followup.setEnabled(False)
        self._followup.returnPressed.connect(self._send_followup)

        self._send_button = QPushButton("追问", footer)
        self._send_button.clicked.connect(self._send_followup)
        self._send_button.setEnabled(False)

        grip = QSizeGrip(footer)
        grip.setFixedSize(16, 16)

        foot_row.addWidget(self._followup, 1)
        foot_row.addWidget(self._send_button)
        foot_row.addWidget(grip)
        root.addWidget(footer)

        self.setStyleSheet(_STYLE)

    def _apply_appearance(self) -> None:
        width = int(get(self._cfg, "appearance.width", 580))
        height = int(get(self._cfg, "appearance.height", 480))
        self.resize(max(340, width), max(240, height))
        self.setMinimumSize(340, 220)

        opacity = float(get(self._cfg, "appearance.opacity", 0.94))
        self.setWindowOpacity(min(1.0, max(0.35, opacity)))

        font_size = int(get(self._cfg, "appearance.font_size", 15))
        family = '"Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI"'
        self._body.setStyleSheet(
            'QTextBrowser { font-family: ' + family + "; font-size: " + str(font_size) + "px; }"
        )
        sheet = "body { font-family: " + family + "; font-size: " + str(font_size) + "px; }"
        self._body.document().setDefaultStyleSheet(sheet + _BODY_STYLE)

    def apply_config(self, cfg: dict) -> None:
        self._cfg = cfg
        self._apply_appearance()

    # ---------- 对外状态接口 ----------

    def set_source_label(self, text: str) -> None:
        """标题栏显示这次是谁在回答。"""
        self._title.setText(text or "解答")

    def begin(self, question_rect: QRect, image_size: tuple[int, int], source_label: str = "") -> None:
        """开始一次新的提问。"""
        self._answer_md = ""
        self._busy = True
        self._started_at = time.time()
        self._phase = "answer"
        self._question_rect = question_rect
        self._body.clear()
        self._body.setHtml(
            '<div style="color:#93A0B4">已截取题目 ' + str(image_size[0]) + " x " + str(image_size[1])
            + ",正在交给「" + (source_label or "AI") + "」解答…</div>"
        )
        self._copy_button.setEnabled(False)
        self._followup.setEnabled(False)
        self._send_button.setEnabled(False)
        self._saved = False
        self._save_button.setText("收藏")
        self._save_button.setEnabled(False)
        self._save_button.setToolTip("把这道题收进收藏夹(可以选分类)")
        self._dot.setStyleSheet("color: " + _ACCENT + ";")
        self._title.setText(source_label or "解答")
        self._status.setText("思考中…")
        self._tick_timer.start()
        self._hide_timer.stop()
        self.show_window()

    def append(self, chunk: str) -> None:
        if not chunk:
            return
        self._answer_md += chunk
        if not self._render_timer.isActive():
            self._render_timer.start()

    def finish(self, elapsed: float | None = None) -> None:
        self._busy = False
        self._phase = "idle"
        self._tick_timer.stop()
        self._render_now()
        seconds = elapsed if elapsed is not None else (time.time() - self._started_at)
        self._status.setText("完成 · 用时 " + ("%.1f" % seconds) + " 秒")
        self._dot.setStyleSheet("color: " + _OK + ";")
        self._copy_button.setEnabled(bool(self._answer_md.strip()))
        self._save_button.setEnabled(bool(self._answer_md.strip()))
        self._followup.setEnabled(True)
        self._send_button.setEnabled(True)
        if bool(get(self._cfg, "behavior.copy_answer_to_clipboard", False)):
            self.copy_answer(quiet=True)
        self._schedule_auto_hide()

    def fail(self, message: str) -> None:
        self._busy = False
        self._phase = "idle"
        self._tick_timer.stop()
        self._dot.setStyleSheet("color: " + _ERROR + ";")
        self._status.setText("出错了")
        body = (
            '<div style="color:' + _ERROR + ';font-weight:600">请求失败</div>'
            '<div style="color:#E8ECF4;margin-top:8px">'
            + markdown.to_html(message)
            + "</div>"
            '<div style="color:#93A0B4;margin-top:10px">点右上角"重新截图"再试一次,或右键托盘图标打开设置检查 API Key / 模型名。</div>'
        )
        self._body.setHtml(body)
        self._copy_button.setEnabled(True)
        self._followup.setEnabled(False)
        self._send_button.setEnabled(False)

    def begin_followup(self, question: str) -> None:
        self._busy = True
        self._phase = "followup"
        self._started_at = time.time()
        self._answer_md = ""
        self._body.clear()
        self._body.setHtml('<div style="color:#93A0B4">追问中…</div>')
        self._followup.setEnabled(False)
        self._send_button.setEnabled(False)
        self._dot.setStyleSheet("color: " + _ACCENT + ";")
        self._status.setText("追问中…")
        self._tick_timer.start()
        self._hide_timer.stop()

    @property
    def answer_text(self) -> str:
        return self._answer_md

    @property
    def busy(self) -> bool:
        return self._busy

    # ---------- 展示 ----------

    def show_window(self) -> None:
        remember = bool(get(self._cfg, "appearance.remember_position", False))
        saved = get(self._cfg, "appearance.last_pos", None)
        if remember and isinstance(saved, list) and len(saved) == 2:
            self.move(int(saved[0]), int(saved[1]))
        elif not self._user_moved:
            self.place_near(self._question_rect)
        self.show()
        self.raise_()
        if bool(get(self._cfg, "appearance.activate_on_show", False)):
            self.activateWindow()

    def place_near(self, rect: QRect) -> None:
        screen = None
        if rect.isValid() and not rect.isNull():
            screen = QGuiApplication.screenAt(rect.center())
        if screen is None:
            screen = QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        width = self.width()
        height = self.height()

        x = rect.right() + 16 if rect.isValid() and not rect.isNull() else area.right() - width - 24
        if x + width > area.right():
            x = rect.left() - 16 - width if rect.isValid() and not rect.isNull() else area.right() - width - 24
        x = max(area.left() + 8, min(x, area.right() - width - 8))

        y = rect.top() if rect.isValid() and not rect.isNull() else area.top() + 60
        y = max(area.top() + 8, min(y, area.bottom() - height - 8))
        self.move(x, y)

    def hide_by_user(self) -> None:
        self._remember_position()
        self.hide()
        self.hidden_by_user.emit()

    def _remember_position(self) -> None:
        if bool(get(self._cfg, "appearance.remember_position", False)):
            from .config import set_value

            set_value(self._cfg, "appearance.last_pos", [self.x(), self.y()])

    def _schedule_auto_hide(self) -> None:
        seconds = int(get(self._cfg, "appearance.auto_hide_seconds", 0) or 0)
        if seconds > 0:
            self._hide_timer.start(seconds * 1000)

    def _auto_hide(self) -> None:
        if self.underMouse() or self._busy:
            return
        self.hide()

    # ---------- 交互 ----------

    def _send_followup(self) -> None:
        text = self._followup.text().strip()
        if not text or self._busy:
            return
        self._followup.clear()
        self.followup_requested.emit(text)

    def _on_save_clicked(self) -> None:
        """点「收藏」:弹个小菜单选分类,一步到位。

        收藏完这个按钮会变成「📁 收藏夹」,再点就是打开收藏夹 —— 刚收藏完最想看它在哪。
        """
        if self._saved:
            self.open_collection_requested.emit()
            return

        from PySide6.QtWidgets import QMenu

        menu = QMenu(self)
        menu.addAction("收藏到「未分类」").triggered.connect(lambda: self.save_requested.emit(""))
        try:
            from .collection import Collection

            folders = Collection().folders()
        except Exception:
            folders = []
        if folders:
            menu.addSeparator()
            for name in folders:
                menu.addAction("收藏到「" + name + "」").triggered.connect(
                    lambda _=False, value=name: self.save_requested.emit(value)
                )
        menu.addSeparator()
        menu.addAction("新建分类并收藏…").triggered.connect(self._save_with_new_folder)
        menu.addAction("打开收藏夹").triggered.connect(self.open_collection_requested.emit)
        menu.exec(self._save_button.mapToGlobal(QPoint(0, self._save_button.height())))

    def _save_with_new_folder(self) -> None:
        from PySide6.QtWidgets import QInputDialog

        name, ok = QInputDialog.getText(self, "新建分类", "分类名字:")
        if ok and name.strip():
            self.save_requested.emit(name.strip())

    def mark_saved(self, folder: str) -> None:
        """收藏成功后,按钮变成「打开收藏夹」—— 刚收藏完最想看它在哪。"""
        self._saved = True
        self._save_button.setText("📁 收藏夹")
        self._save_button.setEnabled(True)
        self._save_button.setToolTip("已收藏到「" + (folder or "未分类") + "」,点这里打开收藏夹")

    def copy_answer(self, quiet: bool = False) -> None:
        text = markdown.plain_text(self._answer_md).strip()
        if not text:
            return
        QApplication.clipboard().setText(text)
        if not quiet:
            toast("答案已复制到剪贴板", 1800, _OK)

    def keyPressEvent(self, event):  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.hide_by_user()
            return
        if event.matches(QKeySequence.StandardKey.Copy):
            self.copy_answer()
            return
        super().keyPressEvent(event)

    def _on_tick(self) -> None:
        if not self._busy:
            return
        elapsed = time.time() - self._started_at
        label = "追问中" if self._phase == "followup" else "思考中"
        if elapsed >= 2:
            self._status.setText(label + " " + ("%.0f" % elapsed) + " 秒")
        else:
            self._dots = self._dots % 3 + 1
            self._status.setText(label + "…")

    def _render_now(self) -> None:
        text = self._answer_md
        if not text.strip():
            return
        self._body.setHtml(markdown.to_html(text))
        bar = self._body.verticalScrollBar()
        bar.setValue(bar.maximum())

    def mousePressEvent(self, event):  # noqa: N802
        """在窗口的空白区域(不是按钮/文本框)按下也能拖。

        子控件会自己吃掉鼠标事件,所以能走到这里的,就只有边框、间隙这些"非交互"区域。
        """
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self.setCursor(Qt.CursorShape.SizeAllCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):  # noqa: N802
        if self._drag_offset is not None:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):  # noqa: N802
        if self._drag_offset is not None:
            self._drag_offset = None
            self._user_moved = True
            self.unsetCursor()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def eventFilter(self, obj, event):  # noqa: N802
        # 这里的异常会被 Qt 吞掉,并且会污染后续所有 Qt 调用的状态(很难查),
        # 所以整段都兜住,出问题只记日志。
        try:
            if obj is getattr(self, "_header", None):
                kind = event.type()
                if kind == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                    self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                    self.setCursor(Qt.CursorShape.SizeAllCursor)
                    return True
                if kind == QEvent.Type.Enter:
                    self._header.setCursor(Qt.CursorShape.SizeAllCursor)
                elif kind == QEvent.Type.Leave:
                    self._header.unsetCursor()
                elif kind == QEvent.Type.MouseMove and self._drag_offset is not None:
                    self.move(event.globalPosition().toPoint() - self._drag_offset)
                    return True
                elif kind == QEvent.Type.MouseButtonRelease:
                    if self._drag_offset is not None:
                        self._drag_offset = None
                        self._user_moved = True
                        self._header.unsetCursor()
                    return True
        except Exception as exc:  # pragma: no cover
            from .logging_setup import log

            log.warning("答案窗事件过滤器异常(已忽略): %s", exc)
        return super().eventFilter(obj, event)

    def enterEvent(self, event):  # noqa: N802
        self._hide_timer.stop()
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802
        if not self._busy:
            self._schedule_auto_hide()
        super().leaveEvent(event)

    def closeEvent(self, event):  # noqa: N802
        # 关闭按钮 / Alt+F4 只隐藏,程序继续在托盘里待命
        event.ignore()
        self.hide_by_user()

    def mouseDoubleClickEvent(self, event):  # noqa: N802
        self._user_moved = False
        self.place_near(self._question_rect)
