"""主程序:托盘图标 + 全局热键 + 截图 + 请求豆包 + 悬浮窗显示。"""

from __future__ import annotations

import threading
import time
from datetime import datetime

from PySide6.QtCore import QBuffer, QIODevice, QObject, QRect, QThread, Signal
from PySide6.QtNetwork import QLocalServer
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import QApplication, QDialog, QMenu, QMessageBox, QSystemTrayIcon

from . import autostart
from .answer_window import AnswerWindow
from .collection import Collection, extract_title
from .collection_window import show_collection_window
from .floating_button import FloatingButton
from .backend import backend_label, backend_short_label, build_backend
from .config import (
    APP_NAME,
    APP_VERSION,
    app_dir,
    assets_dir,
    history_dir,
    get,
    load_config,
    save_config,
)
from .errors import BackendError
from .hotkey import GlobalHotkey
from .logging_setup import log
from .overlay import SelectionOverlay
from .settings_dialog import SettingsDialog
from .toast import toast

CONTROL_SERVER = "screenqa-control-9f3a"


def _icon() -> QIcon:
    for name in ("icon.ico", "icon.png"):
        path = assets_dir() / name
        if path.exists():
            icon = QIcon(str(path))
            if not icon.isNull():
                return icon
    pixmap = QPixmap(64, 64)
    pixmap.fill(QColor(76, 155, 255))
    return QIcon(pixmap)


def pixmap_to_png(pixmap: QPixmap) -> bytes:
    """QPixmap -> PNG bytes(发给接口用)。"""
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not pixmap.save(buffer, "PNG"):
        return b""
    return bytes(buffer.data())


class AskWorker(QThread):
    """后台线程里向后端要答案,避免界面卡住。"""

    chunk = Signal(str)
    failed = Signal(str)
    succeeded = Signal(float)

    def __init__(
        self,
        backend,
        image_png: bytes | None,
        question: str,
        previous_answer: str | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._backend = backend
        self._image = image_png
        self._question = question
        self._previous = previous_answer
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:  # noqa: D102
        started = time.time()
        got_anything = False
        try:
            for piece in self._backend.stream_answer(
                self._image,
                self._question,
                self._previous,
                should_cancel=lambda: self._cancelled,
            ):
                got_anything = True
                self.chunk.emit(piece)
        except BackendError as exc:
            self.failed.emit(str(exc))
            return
        except Exception as exc:  # pragma: no cover
            log.exception("请求后端时出错")
            self.failed.emit("发生未预期的错误:" + str(exc))
            return

        if self._cancelled and not got_anything:
            self.failed.emit("已取消。")
            return
        self.succeeded.emit(time.time() - started)


class ScreenQaApp(QObject):
    """把各个部件串起来。"""

    def __init__(self, qapp: QApplication) -> None:
        super().__init__()
        self.qapp = qapp
        self.cfg = load_config()
        self._worker: AskWorker | None = None
        self._last_image: bytes | None = None
        self._ball_was_visible = False
        self._last_question = ""
        self._last_answer = ""
        self._window_was_visible = False

        self.backend = build_backend(self.cfg)

        self.window = AnswerWindow(self.cfg)
        self.window.followup_requested.connect(self.ask_followup)
        self.window.recapture_requested.connect(self.start_capture)
        self.window.save_requested.connect(self.save_current_question)
        self.window.open_collection_requested.connect(self.open_collection)

        # 桌面上的悬浮小按钮
        self.ball: FloatingButton | None = None
        if bool(get(self.cfg, "floating_button.enabled", True)):
            self.ball = FloatingButton(self.cfg)
            self.ball.capture_requested.connect(self.start_capture)
            self.ball.menu_requested.connect(self.show_menu_at)
            self.ball.position_changed.connect(self.save_ball_position)
            self.ball.show()

        # 启动时预热会话(实测能省掉 WorkBuddy 侧冷启动那一次的开销)
        self._start_warmup()

        self.overlay = SelectionOverlay(self)
        self.overlay.selected.connect(self.on_region_selected)
        self.overlay.cancelled.connect(self.on_capture_cancelled)

        self.hotkey = GlobalHotkey(self)
        self.hotkey.activated.connect(self.start_capture)

        self._build_tray()
        self._start_control_server()
        self._hotkey_error = ""
        ok, message = self.hotkey.register(str(self.cfg.get("hotkey", "Alt+Q")))
        if not ok:
            self._hotkey_error = message
            log.warning("热键注册失败:%s", message)

    # ---------- 托盘 ----------

    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(_icon(), self)
        self.tray.setToolTip(APP_NAME + " v" + APP_VERSION)

        menu = QMenu()
        self._act_capture = menu.addAction("截图搜题")
        self._act_capture.triggered.connect(self.start_capture)
        hotkey = str(self.cfg.get("hotkey", "Alt+Q"))
        self._act_capture.setText("截图搜题  (" + hotkey + ")")
        self._act_capture_hotkey = hotkey

        menu.addSeparator()
        act_show = menu.addAction("显示/隐藏答案窗")
        act_show.triggered.connect(self.toggle_window)

        act_settings = menu.addAction("设置…")
        act_settings.triggered.connect(self.open_settings)

        act_collection = menu.addAction("收藏夹…")
        act_collection.triggered.connect(self.open_collection)

        self._act_ball = menu.addAction("桌面悬浮按钮")
        self._act_ball.setCheckable(True)
        self._act_ball.setChecked(bool(get(self.cfg, "floating_button.enabled", True)))
        self._act_ball.toggled.connect(self.toggle_ball)

        menu.addSeparator()
        act_history = menu.addAction("打开历史记录文件夹")
        act_history.triggered.connect(self.open_history)

        act_logs = menu.addAction("打开日志")
        act_logs.triggered.connect(self.open_logs)

        menu.addSeparator()
        self._act_autostart = menu.addAction("开机自动启动")
        self._act_autostart.setCheckable(True)
        self._act_autostart.setChecked(autostart.is_enabled())
        self._act_autostart.toggled.connect(self.toggle_autostart)

        menu.addSeparator()
        act_quit = menu.addAction("退出")
        act_quit.triggered.connect(self.quit)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _on_tray_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.start_capture()
        elif reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.start_capture()  # 遮罩已经开着的话 start_capture 会自己忽略

    def show_menu_at(self, point) -> None:
        """悬浮按钮右键:弹一个专门给按钮用的菜单,收藏夹放最前面。

        注意:菜单必须留一个引用(self._popup_menu)。QMenu 没有父对象时,
        函数一返回就会被 Python 回收,菜单会瞬间消失 —— 之前"右键没反应"就是这个原因。
        """
        from PySide6.QtWidgets import QMenu

        menu = getattr(self, "_popup_menu", None)
        if menu is None:
            # 挂在窗口下,保证对象活得够久
            menu = QMenu(self.window)
            menu.addAction("截图搜题").triggered.connect(self.start_capture)
            menu.addSeparator()
            menu.addAction("📁 收藏夹").triggered.connect(self.open_collection)
            menu.addAction("设置…").triggered.connect(self.open_settings)
            menu.addAction("显示/隐藏答案窗").triggered.connect(self.toggle_window)
            menu.addSeparator()
            menu.addAction("退出").triggered.connect(self.quit)
            self._popup_menu = menu
        menu.popup(point)

    def _start_control_server(self) -> None:
        """起一个本地控制端口:再次双击 run.bat 时,把收藏夹叫出来。"""
        QLocalServer.removeServer(CONTROL_SERVER)
        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._on_control_connection)
        if not self._server.listen(CONTROL_SERVER):
            log.warning("控制端口没起来: %s", self._server.errorString())
        else:
            log.info("控制端口已就绪: %s", CONTROL_SERVER)

    def _on_control_connection(self) -> None:
        socket = self._server.nextPendingConnection()
        if socket is None:
            return
        socket.readyRead.connect(lambda: self._handle_control(socket))
        socket.disconnected.connect(socket.deleteLater)

    def _handle_control(self, socket) -> None:
        command = bytes(socket.readAll()).decode("utf-8", "replace").strip()
        log.info("收到控制指令: %r", command)
        if command == "quit":
            self.quit()
        else:
            self.open_collection()

    def save_ball_position(self, x: int, y: int) -> None:
        from .config import set_value

        set_value(self.cfg, "floating_button.pos", [int(x), int(y)])
        save_config(self.cfg)

    def open_collection(self) -> None:
        show_collection_window(None)

    def save_current_question(self, folder: str) -> None:
        """把当前题目收进收藏夹(纯本地写入,毫秒级)。"""
        if not self._last_answer.strip():
            toast("还没有答案可以收藏", 1800, "#FFB020")
            return
        try:
            collection = Collection()
            collection.ensure_folder(folder)
            # 标题优先取模型给的【题目】那一行,收藏列表才看得懂
            question = extract_title(self._last_answer, self._last_question or "")
            collection.add_item(
                question=question,
                answer=self._last_answer,
                folder=folder,
                source=backend_label(self.cfg),
                image_png=self._last_image,
            )
        except Exception as exc:
            log.warning("收藏失败: %s", exc)
            toast("收藏失败:" + str(exc), 2600, "#FF6B6B")
            return
        self.window.mark_saved(folder)
        toast("已收藏到「" + (folder or "未分类") + "」。点答案窗上的「📁 收藏夹」就能看", 3000)

    def toggle_ball(self, enabled: bool) -> None:
        """显示/隐藏悬浮按钮。"""
        self.cfg.setdefault("floating_button", {})["enabled"] = bool(enabled)
        save_config(self.cfg)
        if enabled and self.ball is None:
            self.ball = FloatingButton(self.cfg)
            self.ball.capture_requested.connect(self.start_capture)
            self.ball.menu_requested.connect(self.show_menu_at)
            self.ball.position_changed.connect(self.save_ball_position)
            self.ball.show()
        elif not enabled and self.ball is not None:
            self.ball.hide()
            self.ball.deleteLater()
            self.ball = None
        toast("悬浮按钮已开启" if enabled else "悬浮按钮已关闭", 1800)

    def _start_warmup(self) -> None:
        """后台预热 ACP 会话,别拖慢启动。"""
        if not bool(get(self.cfg, "workbuddy_acp.warm_up", True)):
            return
        backend = self.backend
        if not hasattr(backend, "warm_up"):
            return

        def run() -> None:
            try:
                backend.warm_up()
            except Exception as exc:  # pragma: no cover
                log.debug("预热异常: %s", exc)

        threading.Thread(target=run, name="screenqa-warmup", daemon=True).start()

    def toggle_window(self) -> None:
        if self.window.isVisible():
            self.window.hide_by_user()
        else:
            self.window.show_window()

    def open_history(self) -> None:
        import os

        os.startfile(str(history_dir()))  # noqa: S606

    def open_logs(self) -> None:
        import os

        path = app_dir() / "logs"
        path.mkdir(parents=True, exist_ok=True)
        os.startfile(str(path))  # noqa: S606

    def toggle_autostart(self, enabled: bool) -> None:
        ok, message = autostart.set_enabled(enabled)
        if ok:
            self.cfg["autostart"] = enabled
            save_config(self.cfg)
            toast("已开启开机自启" if enabled else "已关闭开机自启")
        else:
            self._act_autostart.setChecked(not enabled)
            QMessageBox.warning(None, "设置失败", message)

    # ---------- 截图 ----------

    def start_capture(self) -> None:
        if self.overlay.active:
            return
        if self._worker is not None and self._worker.isRunning():
            # 上一题还在跑,直接取消,开始新的一题
            self._cancel_worker()

        self._window_was_visible = self.window.isVisible()
        if self._window_was_visible:
            # 先藏起来,免得旧答案被拍进新题目的截图里
            self.window.hide()
        if self.ball is not None and self.ball.isVisible():
            # 悬浮按钮也要藏,否则会被拍进截图
            self.ball.hide()
            self._ball_was_visible = True

        self.overlay.start(show_highlight=bool(self.cfg["behavior"].get("show_window_highlight", True)))

    def _restore_ball(self) -> None:
        if self.ball is not None and self._ball_was_visible:
            self.ball.show()
        self._ball_was_visible = False

    def on_capture_cancelled(self) -> None:
        self._restore_ball()
        if self._window_was_visible:
            self.window.show_window()

    def on_region_selected(self, pixmap: QPixmap, rect: QRect) -> None:
        if pixmap.isNull():
            return
        png = pixmap_to_png(pixmap)
        if not png:
            toast("截图失败,请重试", accent="#FF6B6B")
            return
        self._last_image = png
        self._last_answer = ""
        self._last_question = "请解答截图中的这道题。"
        self._restore_ball()
        self.window.begin(
            rect, (pixmap.width(), pixmap.height()), source_label=backend_short_label(self.cfg)
        )
        self._start_worker(self._last_image, self._last_question, previous=None)

    # ---------- 追问 ----------

    def ask_followup(self, question: str) -> None:
        if not self._last_image:
            toast("还没有题目,先按快捷键截个图吧")
            return
        self._cancel_worker()
        self.window.begin_followup(question)
        context = "题目:" + self._last_question + "\n\n上一次的解答:\n" + self._last_answer
        self._start_worker(self._last_image, question, previous=context)

    # ---------- 请求 ----------

    def _start_worker(self, image: bytes, question: str, previous: str | None) -> None:
        self._cancel_worker()
        # 注意:后端对象要复用(ACP 后端的会话上下文挂在它身上),只有改设置时才重建
        worker = AskWorker(self.backend, image, question, previous, self)
        worker.chunk.connect(self.window.append)
        worker.failed.connect(self.on_failed)
        worker.succeeded.connect(self.on_succeeded)
        worker.finished.connect(lambda: self._on_worker_finished(worker))
        self._worker = worker
        worker.start()

    def _on_worker_finished(self, worker: AskWorker) -> None:
        if self._worker is worker:
            self._worker = None
        worker.deleteLater()

    def _cancel_worker(self) -> None:
        worker = self._worker
        if worker is None:
            return
        worker.cancel()
        if worker.isRunning():
            worker.wait(1500)
        # 断开旧信号:旧线程万一还在吐字,也不能污染新的答案
        for signal in (worker.chunk, worker.failed, worker.succeeded):
            try:
                signal.disconnect()
            except (RuntimeError, TypeError):
                pass

    def on_succeeded(self, elapsed: float) -> None:
        self.window.finish(elapsed)
        self._last_answer = self.window.answer_text
        self._save_history()
        if bool(self.cfg["behavior"].get("notify_on_done", False)) and not self.window.isVisible():
            self.tray.showMessage(
                APP_NAME,
                backend_short_label(self.cfg) + " 已经给出解答",
                QSystemTrayIcon.MessageIcon.Information,
                2500,
            )

    def on_failed(self, message: str) -> None:
        self.window.fail(message)
        log.warning("请求失败: %s", message)

    # ---------- 历史 ----------

    def _save_history(self) -> None:
        if not bool(self.cfg["behavior"].get("save_history", True)):
            return
        if not self._last_image or not self._last_answer.strip():
            return
        try:
            folder = history_dir() / datetime.now().strftime("%Y-%m-%d")
            folder.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%H%M%S")
            (folder / (stamp + ".png")).write_bytes(self._last_image)
            (folder / (stamp + ".md")).write_text(
                "# 题目截图: " + stamp + ".png\n\n## 解答\n\n" + self._last_answer + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            log.warning("保存历史失败: %s", exc)

    # ---------- 设置 ----------

    def open_settings(self) -> None:
        dialog = SettingsDialog(self.cfg, None)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.cfg = dialog.cfg
        save_config(self.cfg)

        self.window.apply_config(self.cfg)
        self.backend = build_backend(self.cfg)
        self._start_warmup()

        # 悬浮按钮的开关/大小/触发方式都可能在设置里改过
        want_ball = bool(get(self.cfg, "floating_button.enabled", True))
        if want_ball and self.ball is None:
            self.toggle_ball(True)
        elif not want_ball and self.ball is not None:
            self.toggle_ball(False)
        elif self.ball is not None:
            self.ball.apply_config(self.cfg)
        if hasattr(self, "_act_ball"):
            self._act_ball.setChecked(want_ball)

        hotkey = str(self.cfg.get("hotkey", "Alt+Q"))
        ok, message = self.hotkey.register(hotkey)
        if ok:
            self._act_capture.setText("截图搜题  (" + hotkey + ")")
            toast("设置已保存")
        else:
            toast("设置已保存,但快捷键注册失败:" + message, 4200, "#FF6B6B")
        self._act_autostart.setChecked(autostart.is_enabled())

    # ---------- 退出 ----------

    def quit(self) -> None:
        try:
            if getattr(self, "_server", None) is not None:
                self._server.close()
        except Exception:
            pass
        self._cancel_worker()
        self.overlay.close_all()
        self.hotkey.unregister()
        self.tray.hide()
        self.qapp.quit()

    def startup_message(self) -> None:
        spec = self.hotkey.spec or str(self.cfg.get("hotkey", "Alt+Q"))
        if self._hotkey_error:
            toast(self._hotkey_error + "(可在设置里换一个)", 6000, "#FF6B6B")
            return
        name = str(self.cfg.get("backend", "openai") or "openai")

        if name == "openai" and not str(self.cfg["api"].get("api_key") or "").strip():
            toast("还没有配置 API Key,右键托盘图标 -> 设置", 5200, "#FFB020")
            return
        if name == "workbuddy_acp":
            from .workbuddy_acp_backend import workbuddy_running

            if not workbuddy_running():
                toast("没检测到 WorkBuddy 在运行,先把它打开(可最小化)", 5200, "#FFB020")
                return
            toast("已就绪。左键点桌面悬浮按钮截图;右键它(或托盘图标)里有「收藏夹」", 5200)
            return
        if name == "cli":
            from .cli_backend import find_cli

            if find_cli() is None:
                toast("没有找到本地智能体 CLI,右键托盘图标 -> 设置", 5200, "#FFB020")
                return
            toast("已就绪(本地智能体),按 " + spec + " 框选题目", 3600)
            return
        if name == "simple" and not str(self.cfg["simple"].get("url") or "").strip():
            toast("还没有配置 HTTP 接口地址,右键托盘图标 -> 设置", 5200, "#FFB020")
            return
        toast("已就绪,按 " + spec + " 框选题目", 3200)
