"""macOS 全局热键:用 pynput 监听,不需要编译原生代码。

注意:macOS 要在「系统设置 → 隐私与安全性 → 辅助功能」里给本程序(或终端)授权,
否则监听不到按键。没装 pynput 时会返回明确的提示,而不是静默失效。
"""

from __future__ import annotations

import sys

from PySide6.QtCore import QObject, Signal

from .logging_setup import log

# 把我们的写法映射成 pynput 的写法
_MODIFIERS = {
    "ctrl": "<ctrl>",
    "control": "<ctrl>",
    "alt": "<alt>",
    "option": "<alt>",
    "opt": "<alt>",
    "shift": "<shift>",
    "win": "<cmd>",
    "cmd": "<cmd>",
    "command": "<cmd>",
    "super": "<cmd>",
    "meta": "<cmd>",
}

_NAMED_KEYS = {
    "space": "<space>",
    "tab": "<tab>",
    "esc": "<esc>",
    "escape": "<esc>",
    "enter": "<enter>",
    "return": "<enter>",
    "backspace": "<backspace>",
    "delete": "<delete>",
    "del": "<delete>",
    "home": "<home>",
    "end": "<end>",
    "pageup": "<page_up>",
    "pagedown": "<page_down>",
    "up": "<up>",
    "down": "<down>",
    "left": "<left>",
    "right": "<right>",
    "-": "-",
    "=": "=",
    "[": "[",
    "]": "]",
    ";": ";",
    "'": "'",
    ",": ",",
    ".": ".",
    "/": "/",
}


def to_pynput(spec: str) -> str:
    """把 "Alt+Q" 这样的写法转成 pynput 的 "<alt>+q"。"""
    text = (spec or "").strip()
    if not text:
        raise ValueError("快捷键不能为空")
    parts = [p.strip() for p in text.split("+") if p.strip()]
    if len(parts) < 2:
        raise ValueError("快捷键至少要有一个修饰键,例如 Alt+Q")

    out: list[str] = []
    key: str | None = None
    for part in parts:
        low = part.lower()
        if low in _MODIFIERS:
            out.append(_MODIFIERS[low])
            continue
        if key is not None:
            raise ValueError("快捷键里只能有一个主键,例如 Alt+Q")
        if low in _NAMED_KEYS:
            key = _NAMED_KEYS[low]
        elif len(low) == 1:
            key = low
        elif low.startswith("f") and low[1:].isdigit() and 1 <= int(low[1:]) <= 24:
            key = "<f%d>" % int(low[1:])
        else:
            raise ValueError("无法识别的按键:" + part)

    if key is None:
        raise ValueError("快捷键缺少主键,例如 Alt+Q")
    out.append(key)
    return "+".join(out)


class MacHotkey(QObject):
    """和 Windows 版 GlobalHotkey 同样的接口,保证上层不用改。"""

    activated = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.spec = ""
        self._listener = None
        self._error = ""

    @property
    def registered(self) -> bool:
        return self._listener is not None

    def register(self, spec: str) -> tuple[bool, str]:
        self.unregister()
        try:
            combo = to_pynput(spec)
        except ValueError as exc:
            return False, str(exc)

        try:
            from pynput import keyboard
        except ImportError:
            return False, (
                "macOS 上需要 pynput 才能用全局快捷键。\\n"
                "请在终端里执行: .venv/bin/pip install pynput"
            )

        try:
            listener = keyboard.GlobalHotKeys({combo: self._fire})
            listener.daemon = True
            listener.start()
        except Exception as exc:  # 权限不足时会抛
            log.warning("macOS 注册热键失败: %s", exc)
            return False, (
                "注册全局快捷键失败:" + str(exc) + "\\n\\n"
                "多半是没给「辅助功能」权限:系统设置 → 隐私与安全性 → 辅助功能,"
                "把本程序(或终端)勾上,然后重启程序。"
            )

        self._listener = listener
        self.spec = spec
        log.info("macOS 全局热键已注册: %s", spec)
        return True, ""

    def _fire(self) -> None:
        # 这个回调在 pynput 的监听线程里,发 Qt 信号会自动排队到主线程,是安全的
        self.activated.emit()

    def unregister(self) -> None:
        listener, self._listener = self._listener, None
        if listener is not None:
            try:
                listener.stop()
            except Exception:
                pass
        self.spec = ""

    def trigger(self) -> None:
        """内部/测试用。"""
        self.activated.emit()


def create_hotkey(parent: QObject | None = None):
    """按平台选一个热键实现。"""
    if sys.platform == "darwin":
        return MacHotkey(parent)
    from .hotkey import GlobalHotkey

    return GlobalHotkey(parent)