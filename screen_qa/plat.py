"""平台差异集中在这里,其它模块尽量不直接判断系统。

Windows 和 macOS 各走各的实现;没有 Mac 的地方就优雅降级(功能少一点,但不会崩)。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"
IS_MACOS = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")


def open_path(path: str | Path) -> None:
    """用系统默认程序打开文件或文件夹。"""
    target = str(path)
    if IS_WINDOWS:
        os.startfile(target)  # type: ignore[attr-defined]  # noqa: S606
    elif IS_MACOS:
        subprocess.Popen(["open", target])  # noqa: S603,S607
    else:
        subprocess.Popen(["xdg-open", target])  # noqa: S603,S607


def reveal_in_file_manager(path: str | Path) -> None:
    """在文件管理器里定位到某个文件(macOS 用 open -R)。"""
    target = str(path)
    if IS_MACOS:
        subprocess.Popen(["open", "-R", target])  # noqa: S603,S607
    elif IS_WINDOWS:
        subprocess.Popen(["explorer", "/select,", target])  # noqa: S603,S607
    else:
        open_path(Path(target).parent)


def macos_has_screen_permission() -> bool:
    """macOS 有没有授予「屏幕录制」权限。

    macOS 10.15 之后没有这个权限,截屏只能拿到桌面壁纸(或者一片黑)。
    老系统 / 非 macOS 一律返回 True(交给后面的"是不是全黑"兜底判断)。
    """
    if not IS_MACOS:
        return True
    try:
        import ctypes

        core = ctypes.CDLL(
            "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
        )
        core.CGPreflightScreenCaptureAccess.restype = ctypes.c_bool
        return bool(core.CGPreflightScreenCaptureAccess())
    except Exception:
        return True


def macos_request_screen_permission() -> None:
    """弹一次系统授权窗口(macOS 只会在第一次调用时弹)。"""
    if not IS_MACOS:
        return
    try:
        import ctypes

        core = ctypes.CDLL(
            "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
        )
        core.CGRequestScreenCaptureAccess.restype = ctypes.c_bool
        core.CGRequestScreenCaptureAccess()
    except Exception:
        pass


def macos_accessibility_hint() -> str:
    """全局热键在 macOS 上需要「辅助功能」权限。"""
    return (
        "macOS 需要在「系统设置 → 隐私与安全性 → 辅助功能」里勾上本程序(或终端),\\n"
        "全局快捷键才能生效。勾完重启一次程序即可。"
    )


def app_bundle_path() -> Path | None:
    """如果是以 .app 形式打包运行的,返回那个 .app 的路径。"""
    if not IS_MACOS:
        return None
    exe = Path(sys.executable).resolve()
    for parent in [exe, *exe.parents]:
        if parent.suffix == ".app":
            return parent
    return None


def launch_command() -> list[str]:
    """开机自启用:怎么把本程序拉起来。"""
    if getattr(sys, "frozen", False):
        return [sys.executable]
    if IS_MACOS:
        python = Path(sys.executable)
        # 打包成 .app 时用 bundle 里的解释器;否则用当前解释器
        main_py = Path(__file__).resolve().parent.parent / "main.py"
        return [str(python), str(main_py)]
    main_py = Path(__file__).resolve().parent.parent / "main.py"
    return [str(sys.executable), str(main_py)]