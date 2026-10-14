"""开机自启:Windows 写注册表,macOS 写 LaunchAgent,其它平台不支持。"""

from __future__ import annotations

import sys
from pathlib import Path

from . import plat

VALUE_NAME = "截图自动搜题"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

# macOS 的登录项
LAUNCH_AGENT = "com.screenshotqa.plist"


def _windows_command() -> str:
    exe = Path(sys.executable)
    if getattr(sys, "frozen", False):
        return '"' + str(exe) + '"'
    if exe.name.lower() == "python.exe":
        pythonw = exe.with_name("pythonw.exe")  # 无控制台窗口
        if pythonw.exists():
            exe = pythonw
    main_py = Path(__file__).resolve().parent.parent / "main.py"
    return '"' + str(exe) + '" "' + str(main_py) + '"'


def _launch_agent_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / LAUNCH_AGENT


def _plist_text() -> str:
    args = "".join(
        "        <string>%s</string>\n" % part.replace("&", "&amp;").replace("<", "&lt;")
        for part in plat.launch_command()
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n'
        "<dict>\n"
        "    <key>Label</key>\n"
        "    <string>com.screenshotqa</string>\n"
        "    <key>ProgramArguments</key>\n"
        "    <array>\n"
        + args +
        "    </array>\n"
        "    <key>RunAtLoad</key>\n"
        "    <true/>\n"
        "    <key>ProcessType</key>\n"
        "    <string>Interactive</string>\n"
        "</dict>\n"
        "</plist>\n"
    )


def is_enabled() -> bool:
    if plat.IS_WINDOWS:
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
    if plat.IS_MACOS:
        return _launch_agent_path().exists()
    return False


def set_enabled(enabled: bool) -> tuple[bool, str]:
    if plat.IS_WINDOWS:
        return _set_windows(enabled)
    if plat.IS_MACOS:
        return _set_macos(enabled)
    return False, "当前系统不支持设置开机自启"


def _set_windows(enabled: bool) -> tuple[bool, str]:
    try:
        import winreg
    except ImportError:
        return False, "当前系统不支持注册表自启"
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            if enabled:
                winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, _windows_command())
            else:
                try:
                    winreg.DeleteValue(key, VALUE_NAME)
                except FileNotFoundError:
                    pass
        return True, ""
    except OSError as exc:
        return False, "写注册表失败:" + str(exc)


def _set_macos(enabled: bool) -> tuple[bool, str]:
    path = _launch_agent_path()
    try:
        if enabled:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(_plist_text(), encoding="utf-8")
            # 下次登录自动生效;这里顺手 load 一下,现在就起作用
            import subprocess

            subprocess.run(["launchctl", "load", "-w", str(path)],  # noqa: S603,S607
                           capture_output=True, check=False)
        else:
            import subprocess

            if path.exists():
                subprocess.run(["launchctl", "unload", "-w", str(path)],  # noqa: S603,S607
                               capture_output=True, check=False)
                path.unlink(missing_ok=True)
        return True, ""
    except OSError as exc:
        return False, "写 LaunchAgent 失败:" + str(exc)