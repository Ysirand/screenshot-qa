"""启动入口。"""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon

from .config import APP_NAME, APP_VERSION
from .logging_setup import log
from .winutil import acquire_single_instance


def _signal_running_instance(command: str) -> int:
    """告诉已经在运行的实例做点什么(没有就提示一下)。"""
    from PySide6.QtNetwork import QLocalSocket

    QApplication(sys.argv)   # QLocalSocket 需要一个 QApplication 实例
    for _ in range(8):
        socket = QLocalSocket()
        socket.connectToServer("screenqa-control-9f3a")
        if socket.waitForConnected(400):
            socket.write(command.encode("utf-8"))
            socket.flush()
            socket.waitForBytesWritten(500)
            socket.disconnectFromServer()
            return 0
        socket.abort()
    QMessageBox.information(
        None, APP_NAME,
        "程序已经在运行了 —— 右下角托盘图标(可能收在 ^ 里)右键就有「收藏夹」。",
    )
    return 0


def main() -> int:
    if not acquire_single_instance():
        # 已经有一个实例在跑:把命令发过去(默认打开收藏夹),然后自己退出
        command = "collection"
        for arg in sys.argv[1:]:
            if arg in ("--collection", "--open-collection"):
                command = "collection"
            elif arg == "--quit":
                command = "quit"
        _signal_running_instance(command)
        return 0

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setQuitOnLastWindowClosed(False)

    from .app import ScreenQaApp, _icon

    app.setWindowIcon(_icon())

    if not QSystemTrayIcon.isSystemTrayAvailable():
        QMessageBox.critical(None, APP_NAME, "系统没有可用的托盘区域,程序无法运行。")
        return 1

    log.info("启动 %s v%s", APP_NAME, APP_VERSION)
    controller = ScreenQaApp(app)
    if any(a in ("--collection", "--open-collection") for a in sys.argv[1:]):
        controller.open_collection()
    controller.startup_message()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())