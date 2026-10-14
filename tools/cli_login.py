"""打开本地智能体(WorkBuddy / CodeBuddy)的登录界面,并复查登录结果。

跑一次就行:进去之后输入 /login,浏览器里授权完成,再回到这个窗口按提示退出。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from screen_qa.cli_backend import CliBackend, find_cli  # noqa: E402
from screen_qa.errors import BackendError  # noqa: E402
from screen_qa.logging_setup import log  # noqa: E402

# 登录过程不需要刷日志
log.setLevel(40)

BAR = "=" * 68


def banner() -> None:
    print(BAR)
    print(" 本地智能体登录")
    print(BAR)
    print()
    print("先说结论(已实测):")
    print("  这个 WorkBuddy 版本的凭据是加密存在本地的,解密密钥只有 WorkBuddy 桌面端")
    print("  启动这个 CLI 时才会注入。所以单独运行(包括本脚本)没法完成登录。")
    print()
    print("  如果你装的是完整版 CodeBuddy Code,下面的窗口里是能登录的,那就:")
    print("    - 不要在这个窗口里输 python(看到 >>> 就是进 Python 了,按 Ctrl+Z 回车退出)")
    print("    - 等 CodeBuddy 自己的界面出来,输入 /login,它会打开浏览器让你授权")
    print()
    print("  如果窗口一闪就退回,说明这个版本不行 —— 请改用「豆包官方 API」,")
    print("  或者拿到 AUTH TOKEN 后填进设置里的「AUTH TOKEN」。")
    print()


def verify(backend: CliBackend) -> tuple[bool, str]:
    """登录完再探一次,把结果明确告诉用户。"""
    try:
        return True, backend.test()
    except BackendError as exc:
        return False, str(exc)
    except Exception as exc:  # pragma: no cover
        return False, "检查失败:" + str(exc)


def main() -> int:
    banner()

    cli = find_cli()
    if cli is None:
        print("没找到本地智能体的 CLI。")
        print("如果你装了 WorkBuddy,它一般在这个位置:")
        print(r"  <WorkBuddy安装目录>\resources\app.asar.unpacked\cli\dist\codebuddy-lite-wb.mjs")
        print("找到后把路径填进程序的「设置 -> CLI 路径」。")
        return 1

    print("找到 CLI:", cli)
    backend = CliBackend({"command": str(cli), "tools": "", "timeout_seconds": 120}, "")
    try:
        argv = [str(item) for item in backend.resolve_argv()[:2]]
    except BackendError as exc:
        print("无法启动:", exc)
        return 1

    print("正在打开…(第一次加载要几秒)")
    print()
    try:
        code = subprocess.call(argv)
    except OSError as exc:
        print("启动失败:", exc)
        return 1
    print()
    print("窗口已关闭(退出码 %s),正在检查登录状态…" % code)
    print()

    ok, info = verify(backend)
    print(BAR)
    if ok:
        print(" 登录成功!")
        print(BAR)
        print()
        print("现在可以直接用了:")
        print("  1. 启动 run.bat(如果已经在跑,托盘右键 -> 退出后重开)")
        print("  2. 托盘右键 -> 设置 -> 回答来源选「本地智能体」-> 测试连接 -> 保存")
        print("  3. 按 Alt+Q 框选题目")
        print()
        print("探测结果:", info.splitlines()[0] if info else "")
        return 0

    print(" 没有登录成功")
    print(BAR)
    print()
    print(info)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())