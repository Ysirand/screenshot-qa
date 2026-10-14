"""本地智能体后端:后台调用 WorkBuddy / CodeBuddy 这类本地 agent 的 CLI。

和官方 API 的区别:不用 API Key,直接用你本机已经登录的智能体(带它自己的模型、
技能、插件、记忆),在后台跑完再把答案吐回桌面窗口。

调用方式是 CLI 官方的非交互模式:
    <cli> -p --input-format stream-json --output-format stream-json --include-partial-messages
题目截图以 base64 图片块的形式从 stdin 送进去,回答从 stdout 流式读回来。
"""

from __future__ import annotations

import base64
import json
import os
import sys
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable, Iterator

from .errors import BackendError
from .logging_setup import log

CREATE_NO_WINDOW = 0x08000000  # Windows:别弹出黑框

# 常见安装位置(找不到时再兜底搜索)
_CLI_RELATIVE = Path("resources") / "app.asar.unpacked" / "cli" / "dist" / "codebuddy-lite-wb.mjs"
_CLI_RELATIVE_BIN = Path("resources") / "app.asar.unpacked" / "cli" / "bin" / "codebuddy"

_UNINSTALL_KEYS = [
    r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
    r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
]


def _looks_like_auth(text: str) -> bool:
    """判断一段输出是不是"没登录"。"""
    low = (text or "").lower()
    return (
        "authentication required" in low
        or "please use /login" in low
        or "not logged in" in low
        or "未登录" in (text or "")
    )


def _find_workbuddy_dirs() -> list[Path]:
    """从注册表和常见目录里找 WorkBuddy 的安装位置。"""
    found: list[Path] = []

    try:
        import winreg
    except ImportError:
        winreg = None

    if winreg is not None:
        for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            for key_path in _UNINSTALL_KEYS:
                try:
                    with winreg.OpenKey(root, key_path) as parent:
                        count = winreg.QueryInfoKey(parent)[0]
                        for index in range(count):
                            try:
                                name = winreg.EnumKey(parent, index)
                                with winreg.OpenKey(parent, name) as sub:
                                    display = str(winreg.QueryValueEx(sub, "DisplayName")[0])
                                    if "workbuddy" not in display.lower():
                                        continue
                                    for value_name in ("InstallLocation", "DisplayIcon", "UninstallString"):
                                        try:
                                            raw = str(winreg.QueryValueEx(sub, value_name)[0])
                                        except OSError:
                                            continue
                                        path = raw.strip("\"'").split(",")[0]
                                        candidate = Path(path)
                                        directory = candidate if candidate.is_dir() else candidate.parent
                                        if directory.is_dir():
                                            found.append(directory)
                                index += 1
                            except OSError:
                                continue
                except OSError:
                    continue

    if sys.platform == "darwin":
        # macOS:WorkBuddy / CodeBuddy 一般装在 /Applications 下
        for app in ("WorkBuddy", "CodeBuddy", "CodeBuddy Code"):
            found.append(
                Path("/Applications") / (app + ".app") / "Contents" / "Resources"
            )
            found.append(
                Path.home() / "Applications" / (app + ".app") / "Contents" / "Resources"
            )
    else:
        for drive in ("C:", "D:", "E:", "F:"):
            found.append(Path(drive + "\\") / "workbuddy")
        local_appdata = os.environ.get("LOCALAPPDATA")
        if local_appdata:
            found.append(Path(local_appdata) / "Programs" / "workbuddy")
    for env_name in ("WORKBUDDY_RESOURCES_PATH", "WORKBUDDY_APP_PATH"):
        value = os.environ.get(env_name)
        if value:
            found.append(Path(value))

    unique: list[Path] = []
    seen: set[str] = set()
    for path in found:
        text = str(path).lower()
        if text not in seen:
            seen.add(text)
            unique.append(path)
    return unique


def find_cli() -> Path | None:
    """自动定位本地智能体 CLI。"""
    for name in ("codebuddy", "cbc", "codebuddy-code"):
        which = shutil.which(name)
        if which:
            return Path(which)

    for directory in _find_workbuddy_dirs():
        for relative in (_CLI_RELATIVE, _CLI_RELATIVE_BIN):
            candidate = directory / relative
            if candidate.is_file():
                return candidate
        # 安装目录往上两级也可能是根目录(比如 .../WorkBuddy/resources/...)
        parent = directory.parent
        for relative in (_CLI_RELATIVE, _CLI_RELATIVE_BIN):
            candidate = parent / relative
            if candidate.is_file():
                return candidate
    return None


def _find_node() -> str | None:
    which = shutil.which("node")
    if which:
        return which
    if sys.platform == "darwin":
        for candidate in ("/opt/homebrew/bin/node", "/usr/local/bin/node"):
            if Path(candidate).is_file():
                return candidate
    home = Path.home() / ".workbuddy" / "binaries"
    for pattern in ("node/node.exe", "node/*/node.exe", "node/bin/node.exe"):
        for candidate in home.glob(pattern):
            if candidate.is_file():
                return str(candidate)
    return None


class CliBackend:
    """把本地 CLI 智能体包装成和 API 后端一样的接口。"""

    label = "本地智能体(CLI)"

    def __init__(self, cfg: dict, system_prompt: str = "") -> None:
        self.cfg = cfg or {}
        self.system_prompt = system_prompt or ""
        self._argv_cache: list[str] | None = None

    # ---------- 进程参数 ----------

    def resolve_argv(self) -> list[str]:
        """算出启动命令,找不到就抛带说明的错误。"""
        if self._argv_cache is not None:
            return self._argv_cache

        configured = str(self.cfg.get("command") or "").strip()
        path: Path | None
        if configured:
            candidate = Path(configured)
            if not candidate.exists():
                raise BackendError(
                    "找不到配置里的 CLI:\n" + configured + "\n请在设置里改成正确路径,或清空让它自动探测。"
                )
            path = candidate
        else:
            path = find_cli()
            if path is None:
                raise BackendError(
                    "没有找到本地智能体 CLI。\n\n"
                    "如果你装了 WorkBuddy,它一般在这个位置:\n"
                    "<WorkBuddy安装目录>\\resources\\app.asar.unpacked\\cli\\dist\\codebuddy-lite-wb.mjs\n"
                    "请在设置里手动填上这个路径。"
                )

        suffix = path.suffix.lower()
        if suffix in (".mjs", ".js", ".cjs"):
            node = _find_node()
            if node is None:
                raise BackendError(
                    "这个 CLI 是 JS 脚本,但没有找到 Node.js。\n"
                    "请安装 Node.js 18 以上版本,或在设置里指定 node.exe 的路径。"
                )
            argv = [node, str(path)]
        else:
            argv = [str(path)]

        argv += [
            "-p",
            "--verbose",
            "--input-format",
            "stream-json",
            "--output-format",
            "stream-json",
            "--include-partial-messages",
        ]

        tools = self.cfg.get("tools", "Read")
        if tools is not None:
            argv += ["--tools", str(tools)]

        permission_mode = str(self.cfg.get("permission_mode") or "").strip()
        if permission_mode:
            argv += ["--permission-mode", permission_mode]

        extra = self.cfg.get("extra_args") or []
        if isinstance(extra, list):
            argv += [str(item) for item in extra if str(item).strip()]

        self._argv_cache = argv
        return argv

    def describe(self) -> str:
        try:
            argv = self.resolve_argv()
        except BackendError as exc:
            return "未找到 CLI:" + str(exc).splitlines()[0]
        target = argv[1] if len(argv) > 1 and argv[0].lower().endswith("node.exe") else argv[0]
        return Path(target).name

    def _env(self) -> dict:
        env = os.environ.copy()
        env["CODEBUDDY_DISABLE_BROWSER_MCP"] = "1"
        env.setdefault("WORKBUDDY_NODE_ENV", "production")
        for key, value in (self.cfg.get("env") or {}).items():
            text = str(value or "").strip()
            if text:
                env[str(key).strip()] = text
        return env

    # ---------- 协议 ----------

    def _message_payload(
        self, image_path: Path | None, question: str, previous_answer: str | None
    ) -> dict:
        content: list[dict] = []
        if image_path is not None:
            try:
                raw = image_path.read_bytes()
                content.append(
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "data": base64.b64encode(raw).decode("ascii"),
                            "media_type": "image/png",
                        },
                    }
                )
            except OSError as exc:
                raise BackendError("读取截图失败:" + str(exc)) from exc

        parts = [question]
        if self.system_prompt:
            parts.append(self.system_prompt)
        if previous_answer:
            parts.append("上一轮的解答是:\n" + previous_answer[:3000])
        if image_path is not None:
            # 同时给出文件路径:万一模型更习惯用 Read 工具读图,也能兜住
            parts.append("(题目截图文件:" + str(image_path) + ")")
            if self.cfg.get("tools", "Read"):
                parts.append("如果需要,可以用 Read 工具读取上面的图片文件。")
        content.append({"type": "text", "text": "\n\n".join(part for part in parts if part)})

        return {
            "type": "user",
            "message": {"role": "user", "content": content},
            "parent_tool_use_id": None,
            "session_id": "default",
        }

    @staticmethod
    def _text_from_event(obj: dict) -> str | None:
        """从一行 stream-json 里抠出正文增量(对多种形状都兼容)。"""
        kind = obj.get("type")

        if kind == "stream_event":
            event = obj.get("event") or {}
            if not isinstance(event, dict):
                return None
            if event.get("type") == "content_block_delta":
                delta = event.get("delta") or {}
                if isinstance(delta, dict):
                    text = delta.get("text")
                    if isinstance(text, str):
                        return text
            return None

        if kind == "content_block_delta":
            delta = obj.get("delta") or {}
            if isinstance(delta, dict) and isinstance(delta.get("text"), str):
                return delta["text"]
            return None

        return None

    @staticmethod
    def _error_text(obj: dict) -> str:
        """result 事件里的错误细节(errors 数组优先)。"""
        errors = obj.get("errors")
        if isinstance(errors, list):
            parts = [str(item).strip() for item in errors if str(item).strip()]
            if parts:
                return "\n".join(parts)
        for key in ("result", "error", "message"):
            value = obj.get(key)
            if isinstance(value, str) and value.strip():
                return value
        return ""

    @staticmethod
    def _text_from_message(obj: dict) -> str:
        """assistant / result 这类整块消息里的正文。"""
        if obj.get("type") == "result":
            for key in ("result", "text"):
                value = obj.get(key)
                if isinstance(value, str) and value.strip():
                    return value
            return ""
        message = obj.get("message")
        if not isinstance(message, dict):
            return ""
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            chunks = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    text = part.get("text")
                    if isinstance(text, str):
                        chunks.append(text)
            return "".join(chunks)
        return ""

    # ---------- 主流程 ----------

    def stream_answer(
        self,
        image_png: bytes | None,
        user_text: str,
        previous_answer: str | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> Iterator[str]:
        argv = self.resolve_argv()
        timeout = float(self.cfg.get("timeout_seconds", 300) or 300)

        workdir = Path(tempfile.mkdtemp(prefix="screenqa_cli_"))
        image_path = None
        if image_png:
            image_path = workdir / "question.png"
            image_path.write_bytes(image_png)
        payload = self._message_payload(image_path, user_text, previous_answer)

        log.info("调用本地智能体: %s (cwd=%s)", " ".join(argv[:3]), workdir)

        try:
            process = subprocess.Popen(  # noqa: S603
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(workdir),
                env=self._env(),
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=CREATE_NO_WINDOW,
            )
        except OSError as exc:
            shutil.rmtree(workdir, ignore_errors=True)
            raise BackendError("启动本地智能体失败:" + str(exc)) from exc

        stderr_lines: list[str] = []

        def drain_stderr() -> None:
            try:
                for line in process.stderr:  # type: ignore[union-attr]
                    stderr_lines.append(line.rstrip())
            except Exception:
                pass

        threading.Thread(target=drain_stderr, daemon=True).start()

        try:
            try:
                process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")  # type: ignore[union-attr]
                process.stdin.flush()  # type: ignore[union-attr]
                process.stdin.close()  # type: ignore[union-attr]
            except (BrokenPipeError, OSError):
                pass

            emitted: list[str] = []
            fallback = ""
            plain_lines: list[str] = []
            started = time.time()
            auth_error = False

            for line in process.stdout:  # type: ignore[union-attr]
                if should_cancel is not None and should_cancel():
                    break
                if time.time() - started > timeout:
                    process.kill()
                    raise BackendError(
                        "本地智能体超时(超过 " + str(int(timeout)) + " 秒还没答完)。"
                    )

                line = line.strip()
                if not line:
                    continue
                if not line.startswith("{"):
                    if "Authentication required" in line or "/login" in line:
                        auth_error = True
                    plain_lines.append(line)
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(obj, dict):
                    continue

                kind = obj.get("type")

                if kind == "result" and obj.get("is_error"):
                    detail = self._error_text(obj) or fallback
                    if _looks_like_auth(detail):
                        auth_error = True
                    elif emitted:
                        # 已经吐了一部分答案,就别把提示盖掉,收尾即可
                        log.warning("本地智能体中途报错: %s", detail[:200])
                        break
                    else:
                        raise BackendError(
                            "本地智能体执行出错("
                            + str(obj.get("subtype") or "error")
                            + "):\n"
                            + (detail[:500] or "(没有细节)")
                        )

                piece = self._text_from_event(obj)
                if piece:
                    emitted.append(piece)
                    yield piece
                    continue

                if kind in ("assistant", "result"):
                    text = self._text_from_message(obj)
                    if text.strip():
                        if _looks_like_auth(text):
                            auth_error = True
                        else:
                            fallback = text

            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()

            stderr_text = "\n".join(stderr_lines).strip()
            if auth_error or "Authentication required" in stderr_text:
                raise BackendError(
                    "本地智能体没有可用的登录凭据。\n\n"
                    "已实测:这个 WorkBuddy 版本的凭据是加密存在本地的(带 keyId/nonce/AEAD),"
                    "解密密钥不在磁盘上 —— 只有 WorkBuddy 桌面端启动它时才会注入,"
                    "所以单独运行(包括 cli_login.bat)没法完成登录。\n\n"
                    "可行的做法:\n"
                    "1) 设置里把「回答来源」改成「豆包官方 API」,最省事,推荐;\n"
                    "2) 如果你有 WorkBuddy / CodeBuddy 的 AUTH TOKEN,填到设置里的「AUTH TOKEN」;\n"
                    "3) 如果你的平台有 HTTP 接口,用「自定义 HTTP 接口」那个后端。"
                )

            detail = stderr_text[-800:] if stderr_text else "(没有输出)"

            if not emitted:
                if fallback.strip():
                    yield fallback
                elif plain_lines and process.returncode in (0, None):
                    text = "\n".join(plain_lines).strip()
                    if text:
                        yield text
                    else:
                        raise BackendError("本地智能体没有返回内容。\n\n原始输出:\n" + detail)
                elif process.returncode not in (0, None):
                    raise BackendError(
                        "本地智能体启动失败(退出码 " + str(process.returncode) + "):\n" + detail
                    )
                else:
                    raise BackendError("本地智能体没有返回内容。\n\n原始输出:\n" + detail)
        finally:
            if process.poll() is None:
                process.kill()
            shutil.rmtree(workdir, ignore_errors=True)

    # ---------- 诊断 ----------

    def test(self) -> str:
        """纯文本探一次,确认已经登录且能正常出话。"""
        argv = self.resolve_argv()  # 找不到会抛 BackendError
        try:
            text = "".join(self.stream_answer(None, "只回复两个字:正常")).strip()
        except BackendError:
            raise
        except Exception as exc:  # pragma: no cover
            raise BackendError("测试失败:" + str(exc)) from exc
        if not text:
            raise BackendError("本地智能体没有返回内容,请先用 cli_login.bat 登录一次。")
        return "本地智能体可用,回复:" + text[:60] + "\n命令:" + " ".join(argv[:4]) + " …"