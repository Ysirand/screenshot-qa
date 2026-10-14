"""全局热键:Win32 RegisterHotKey + Qt 原生事件过滤器,不需要额外依赖。

RegisterHotKey(NULL, ...) 注册到当前线程,WM_HOTKEY 会投递进线程消息队列,
Qt 的事件分发器会把它交给已安装的 nativeEventFilter。
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter, QObject, Signal

from .logging_setup import log

WM_HOTKEY = 0x0312

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.RegisterHotKey.restype = wintypes.BOOL
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
user32.UnregisterHotKey.restype = wintypes.BOOL


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", ctypes.c_size_t),
        ("lParam", ctypes.c_ssize_t),
        ("time", wintypes.DWORD),
        ("pt_x", wintypes.LONG),
        ("pt_y", wintypes.LONG),
    ]


_MODIFIERS = {
    "ctrl": MOD_CONTROL,
    "control": MOD_CONTROL,
    "alt": MOD_ALT,
    "shift": MOD_SHIFT,
    "win": MOD_WIN,
    "super": MOD_WIN,
    "meta": MOD_WIN,
    "cmd": MOD_WIN,
}

_NAMED_KEYS = {
    "space": 0x20,
    "tab": 0x09,
    "esc": 0x1B,
    "escape": 0x1B,
    "enter": 0x0D,
    "return": 0x0D,
    "backspace": 0x08,
    "delete": 0x2E,
    "del": 0x2E,
    "insert": 0x2D,
    "ins": 0x2D,
    "home": 0x24,
    "end": 0x23,
    "pageup": 0x21,
    "pagedown": 0x22,
    "up": 0x26,
    "down": 0x28,
    "left": 0x25,
    "right": 0x27,
    "`": 0xC0,
    "~": 0xC0,
    "-": 0xBD,
    "=": 0xBB,
    "[": 0xDB,
    "]": 0xDD,
    "\\": 0xDC,
    ";": 0xBA,
    "'": 0xDE,
    ",": 0xBC,
    ".": 0xBE,
    "/": 0xBF,
}


def parse_hotkey(spec: str) -> tuple[int, int]:
    """把 "Alt+Q" / "Ctrl+Alt+S" 解析成 (修饰键掩码, 虚拟键码)。"""
    text = (spec or "").strip()
    if not text:
        raise ValueError("快捷键不能为空")
    parts = [p.strip() for p in text.split("+") if p.strip()]
    if len(parts) < 2:
        raise ValueError("快捷键至少要有一个修饰键,例如 Alt+Q")

    mods = 0
    vk = 0
    for part in parts:
        low = part.lower()
        if low in _MODIFIERS:
            mods |= _MODIFIERS[low]
            continue
        if vk:
            raise ValueError("快捷键里只能有一个主键,例如 Alt+Q")
        if low in _NAMED_KEYS:
            vk = _NAMED_KEYS[low]
        elif len(low) == 1 and low.isalpha():
            vk = ord(low.upper())
        elif len(low) == 1 and low.isdigit():
            vk = ord(low)
        elif low.startswith("f") and low[1:].isdigit() and 1 <= int(low[1:]) <= 24:
            vk = 0x70 + int(low[1:]) - 1
        else:
            raise ValueError("无法识别的按键:" + part)

    if not vk:
        raise ValueError("快捷键缺少主键,例如 Alt+Q")
    return mods, vk


class GlobalHotkey(QObject):
    """注册一个全局热键,按下时发出 activated 信号。"""

    activated = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._hotkey_id = 0xB001
        self._filter: _HotkeyFilter | None = None
        self._registered = False
        self.spec = ""

    @property
    def registered(self) -> bool:
        return self._registered

    def register(self, spec: str) -> tuple[bool, str]:
        """注册热键。返回 (是否成功, 错误说明)。"""
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is None:
            return False, "程序尚未初始化"

        self.unregister()
        try:
            mods, vk = parse_hotkey(spec)
        except ValueError as exc:
            return False, str(exc)

        if self._filter is None:
            self._filter = _HotkeyFilter(self._hotkey_id, self)
            app.installNativeEventFilter(self._filter)

        if not user32.RegisterHotKey(None, self._hotkey_id, mods | MOD_NOREPEAT, vk):
            err = ctypes.get_last_error()
            if err == 1409:  # ERROR_HOTKEY_ALREADY_REGISTERED
                return False, "快捷键 " + spec + " 已被其他程序占用,换一个试试。"
            return False, "注册快捷键失败(错误码 " + str(err) + ")。"

        self.spec = spec
        self._registered = True
        log.info("全局热键已注册: %s", spec)
        return True, ""

    def unregister(self) -> None:
        if self._registered:
            user32.UnregisterHotKey(None, self._hotkey_id)
            self._registered = False
            self.spec = ""

    def trigger(self) -> None:
        """内部/测试用:等同于按下了热键。"""
        self.activated.emit()


class _HotkeyFilter(QAbstractNativeEventFilter):
    def __init__(self, hotkey_id: int, owner: GlobalHotkey) -> None:
        super().__init__()
        self._hotkey_id = hotkey_id
        self._owner = owner

    def nativeEventFilter(self, eventType, message):  # noqa: N802 (Qt 命名)
        try:
            if bytes(eventType) == b"windows_generic_MSG":
                msg = ctypes.cast(int(message), ctypes.POINTER(MSG)).contents
                if msg.message == WM_HOTKEY and msg.wParam == self._hotkey_id:
                    self._owner.trigger()
        except Exception as exc:  # 事件过滤器里绝不能抛异常
            log.debug("热键过滤器异常: %s", exc)
        return False, 0