"""Win32 小工具:取光标下的窗口、单实例锁等。全程 ctypes,不依赖第三方库。"""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
try:
    dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
except OSError:  # pragma: no cover
    dwmapi = None

GA_ROOT = 2
GW_HWNDNEXT = 2
GW_HWNDPREV = 3
DWMWA_CLOAKED = 14
GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TOOLWINDOW = 0x00000080
LWA_ALPHA = 0x00000002


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


user32.WindowFromPoint.argtypes = [POINT]
user32.WindowFromPoint.restype = wintypes.HWND
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetAncestor.restype = wintypes.HWND
user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetWindow.restype = wintypes.HWND
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
user32.GetWindowRect.restype = wintypes.BOOL
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsIconic.restype = wintypes.BOOL
user32.PtInRect.argtypes = [ctypes.POINTER(RECT), POINT]
user32.PtInRect.restype = wintypes.BOOL
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongW.restype = wintypes.LONG
user32.GetLayeredWindowAttributes.argtypes = [
    wintypes.HWND,
    ctypes.POINTER(wintypes.DWORD),
    ctypes.POINTER(ctypes.c_ubyte),
    ctypes.POINTER(wintypes.DWORD),
]
user32.GetLayeredWindowAttributes.restype = wintypes.BOOL


def get_window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    """物理像素下的窗口矩形 (left, top, width, height)。"""
    rect = RECT()
    if not user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
        return None
    width = rect.right - rect.left
    height = rect.bottom - rect.top
    if width <= 0 or height <= 0:
        return None
    return (rect.left, rect.top, width, height)


def _is_cloaked(hwnd: int) -> bool:
    if dwmapi is None:
        return False
    value = ctypes.c_int(0)
    try:
        dwmapi.DwmGetWindowAttribute(
            wintypes.HWND(hwnd), DWMWA_CLOAKED, ctypes.byref(value), ctypes.sizeof(value)
        )
    except Exception:
        return False
    return value.value != 0


def _is_invisible_layered(hwnd: int) -> bool:
    ex_style = user32.GetWindowLongW(wintypes.HWND(hwnd), GWL_EXSTYLE)
    if not (ex_style & WS_EX_LAYERED):
        return False
    color_key = wintypes.DWORD(0)
    alpha = ctypes.c_ubyte(255)
    flags = wintypes.DWORD(0)
    ok = user32.GetLayeredWindowAttributes(
        wintypes.HWND(hwnd), ctypes.byref(color_key), ctypes.byref(alpha), ctypes.byref(flags)
    )
    if not ok:
        return False
    if (flags.value & LWA_ALPHA) and alpha.value == 0:
        return True
    return False


def _pid_of(hwnd: int) -> int:
    pid = wintypes.DWORD(0)
    user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
    return int(pid.value)


def _acceptable(hwnd: int, point: POINT, skip_pids: set[int]) -> bool:
    if not hwnd or not user32.IsWindowVisible(wintypes.HWND(hwnd)):
        return False
    if user32.IsIconic(wintypes.HWND(hwnd)):
        return False
    if _pid_of(hwnd) in skip_pids:
        return False
    shell = user32.GetShellWindow()
    desktop = user32.GetDesktopWindow()
    if hwnd in (shell, desktop):
        return False
    if _is_cloaked(hwnd) or _is_invisible_layered(hwnd):
        return False
    rect = RECT()
    if not user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
        return False
    if rect.right - rect.left < 8 or rect.bottom - rect.top < 8:
        return False
    if not user32.PtInRect(ctypes.byref(rect), point):
        return False
    return True


def window_under_point(
    x: int, y: int, skip_pids: set[int] | None = None, prefer_child: bool = True
) -> tuple[int, tuple[int, int, int, int]] | None:
    """返回物理坐标点 (x, y) 下方的窗口:(hwnd, (left, top, w, h))。

    会跳过本进程自己的窗口(全屏截图遮罩就属于这一类)。
    """
    if skip_pids is None:
        skip_pids = {os.getpid()}
    point = POINT(int(x), int(y))

    hwnd = user32.WindowFromPoint(point)
    if not hwnd:
        return None

    if prefer_child:
        candidate = hwnd
    else:
        candidate = user32.GetAncestor(wintypes.HWND(hwnd), GA_ROOT) or hwnd

    # 从光标下的窗口开始,沿 Z 序往下找第一个"合格"的窗口
    current = user32.GetAncestor(wintypes.HWND(candidate), GA_ROOT) or candidate
    for _ in range(200):
        if _acceptable(current, point, skip_pids):
            rect = get_window_rect(current)
            if rect is not None:
                return current, rect
        nxt = user32.GetWindow(wintypes.HWND(current), GW_HWNDNEXT)
        if not nxt:
            break
        current = nxt
    return None


_single_instance_handle = None


def acquire_single_instance(name: str = "Global\\ScreenQaDoubao_SingleInstance") -> bool:
    """已有一个实例在跑时返回 False。"""
    global _single_instance_handle
    ERROR_ALREADY_EXISTS = 183
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    handle = kernel32.CreateMutexW(None, wintypes.BOOL(False), name)
    if not handle:
        return True
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        return False
    _single_instance_handle = handle
    return True