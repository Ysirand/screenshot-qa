"""创建桌面快捷方式。

中文名通过一个带 BOM 的 .ps1 交给 PowerShell,避免 .bat 文件被 cmd 按 GBK 解析而乱码。

    python tools/make_shortcut.py                # 建到桌面
    python tools/make_shortcut.py --into <目录>   # 建到指定目录(测试用)
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAME = "截图搜题"


def build_script(name: str, target: Path, workdir: Path, icon: Path, into: str | None) -> str:
    def q(value: object) -> str:
        return str(value).replace("'", "''")

    lines = [
        "$ws = New-Object -ComObject WScript.Shell",
    ]
    if into:
        lines.append("$dir = '" + q(into) + "'")
    else:
        lines.append("$dir = [Environment]::GetFolderPath('Desktop')")
    lines += [
        "$lnk = $ws.CreateShortcut((Join-Path $dir ('" + q(name) + ".lnk')))",
        "$lnk.TargetPath = '" + q(target) + "'",
        "$lnk.WorkingDirectory = '" + q(workdir) + "'",
    ]
    if icon.exists():
        lines.append("$lnk.IconLocation = '" + q(icon) + "'")
    lines += [
        "$lnk.Description = '" + q(name) + "'",
        "$lnk.Save()",
        "Write-Host ('created: ' + $lnk.FullName)",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--into", default="", help="放快捷方式的目录(默认桌面)")
    args = parser.parse_args()

    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass

    target = ROOT / "run.bat"
    if not target.exists():
        print("找不到 run.bat:", target)
        return 1

    script = build_script(NAME, target, ROOT, ROOT / "assets" / "icon.ico", args.into or None)

    with tempfile.TemporaryDirectory() as tmp:
        ps1 = Path(tmp) / "make_shortcut.ps1"
        ps1.write_text(script, encoding="utf-8-sig")  # BOM:PowerShell 5.1 靠它认中文
        try:
            result = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(ps1),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
            )
        except OSError as exc:
            print("调用 PowerShell 失败:", exc)
            return 1

    out = (result.stdout or "").strip()
    err = (result.stderr or "").strip()
    if result.returncode != 0:
        print("创建快捷方式失败:")
        print(err or out or "(没有输出)")
        return 1

    print(out or "已创建")
    print()
    print("桌面上的「" + NAME + "」就是它,双击即可启动。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())