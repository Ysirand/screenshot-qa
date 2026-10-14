"""开机自启:写注册表 HKCU\...\Run。"""

from __future__ import annotations

import sys
from pathlib import Path

VALUE_NAME = "截图自动搜题"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _command() -> str:
    exe = Path(sys.executable)
    if getattr(sys, "frozen", False):
        return '"' + str(exe) + '"'
    if exe.name.lower() == "python.exe":
        pythonw = exe.with_name("pythonw.exe")  # 无控制台窗口
        if pythonw.exists():
            exe = pythonw
    main_py = Path(__file__).resolve().parent.parent / "main.py"
    return '"' + str(exe) + '" "' + str(main_py) + '"'


def is_enabled() -> bool:
    try:
        import winreg
    except ImportError:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, VALUE_NAME)
            return bool(value)
    except OSError:
        return False


def set_enabled(enabled: bool) -> tuple[bool, str]:
    try:
        import winreg
    except ImportError:
        return False, "当前系统不支持注册表自启"
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            if enabled:
                winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, _command())
            else:
                try:
                    winreg.DeleteValue(key, VALUE_NAME)
                except FileNotFoundError:
                    pass
        return True, ""
    except OSError as exc:
        return False, "写注册表失败:" + str(exc)