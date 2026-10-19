"""WorkBuddy ACP 后端:通过桌面端自带的 ACP(Agent Client Protocol)网关答题。

这是最干净的一条路:
- 不需要 API Key,用的是你已经登录的 WorkBuddy;
- 不操作窗口、不抢焦点、不动剪贴板;
- 图片以 base64 原生发送,不需要本地 OCR;
- 每次答题都在一个**新建的隔离会话**里,不会污染你正在用的对话和项目。

协议(来自 CLI 内置的 OpenAPI 文档):
    POST /api/v1/acp/connect                 -> { connectionId, sessionToken }
    POST /api/v1/acp  method=initialize      Header: acp-connection-id
    POST /api/v1/acp  method=session/new     -> { sessionId, models, modes, ... }
    POST /api/v1/acp  method=session/prompt  -> SSE: session/update / agent_message_chunk

注意:网关端口是动态的,每次要现找;连接只认本机回环地址(免鉴权)。
"""

from __future__ import annotations

import base64
import json
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Iterator

from .errors import BackendError
from .logging_setup import log

CONNECT_PATH = "/api/v1/acp/connect"
ACP_PATH = "/api/v1/acp"
AUTH_STATUS_PATH = "/api/v1/auth/status"

# 单张图别超过这个大小,超了先缩放再发
_MAX_IMAGE_BYTES = 3 * 1024 * 1024
_MAX_IMAGE_WIDTH = 1600

_port_cache: int | None = None


def _local_listen_ports() -> list[int]:
    try:
        import psutil
    except ImportError:  # pragma: no cover
        return []
    ports: set[int] = set()
    try:
        for conn in psutil.net_connections(kind="tcp"):
            if conn.status != psutil.CONN_LISTEN or not conn.laddr:
                continue
            if conn.laddr.ip in ("127.0.0.1", "0.0.0.0", "::1", "::"):
                ports.add(int(conn.laddr.port))
    except Exception as exc:  # pragma: no cover
        log.debug("枚举端口失败: %s", exc)
    return sorted(ports)


# 新版本可能给网关加了密码,401/403 也说明"接口在这里"
_GATEWAY_PATHS = (AUTH_STATUS_PATH, "/api/v1/info")


def _probe_gateway(port: int) -> bool:
    for path in _GATEWAY_PATHS:
        try:
            with urllib.request.urlopen(
                "http://127.0.0.1:%d%s" % (port, path), timeout=1.2
            ) as resp:
                body = resp.read(300).decode("utf-8", "replace")
                if "authEnabled" in body or "protocolVersion" in body or "accessToken" in body:
                    return True
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                return True
        except Exception:
            continue
    return False


def _workbuddy_ports() -> list[int]:
    """先找 WorkBuddy 自己进程占用的端口,比全盘扫快得多。"""
    try:
        import psutil
    except ImportError:  # pragma: no cover
        return []
    pids: set[int] = set()
    for proc in psutil.process_iter(["name"]):
        try:
            if "workbuddy" in (proc.info.get("name") or "").lower():
                pids.add(proc.pid)
        except Exception:
            continue
    ports: list[int] = []
    try:
        for conn in psutil.net_connections(kind="tcp"):
            if conn.status == psutil.CONN_LISTEN and conn.laddr and conn.pid in pids:
                ports.append(int(conn.laddr.port))
    except Exception:
        pass
    return sorted(set(ports))


def _parse_manual(value: str) -> tuple[str, int] | None:
    """把 "127.0.0.1:57464" / "http://127.0.0.1:57464" 解析成 (host, port)。"""
    text = (value or "").strip()
    if not text:
        return None
    if "://" not in text:
        text = "http://" + text
    try:
        parsed = urllib.parse.urlparse(text)
        port = parsed.port
        if not port:
            return None
        return (parsed.hostname or "127.0.0.1", int(port))
    except Exception:
        return None


def workbuddy_running() -> bool:
    """快速判断 WorkBuddy 桌面端在不在跑(不扫端口,只查进程)。"""
    try:
        import psutil
    except ImportError:  # pragma: no cover
        return True
    for proc in psutil.process_iter(["name"]):
        try:
            if (proc.info.get("name") or "").lower().startswith("workbuddy"):
                return True
        except Exception:
            continue
    return False


def find_gateway_port(force: bool = False, manual: str = "") -> int:
    """找到 WorkBuddy ACP 网关的端口。

    顺序:手动填的地址 -> 缓存 -> WorkBuddy 进程占用的端口 -> 全部本地端口。
    """
    global _port_cache

    parsed = _parse_manual(manual)
    if parsed is not None:
        host, port = parsed
        if _probe_gateway(port):
            _port_cache = port
            return port
        raise BackendError(
            "连不上你手动填的网关地址:" + manual + "\n\n"
            "请确认 WorkBuddy 正在运行、端口没写错(端口每次启动都会变)。"
        )

    if _port_cache is not None and not force and _probe_gateway(_port_cache):
        return _port_cache

    own = _workbuddy_ports()
    if own:
        with ThreadPoolExecutor(max_workers=8) as pool:
            for port, ok in zip(own, pool.map(_probe_gateway, own)):
                if ok:
                    _port_cache = port
                    log.info("WorkBuddy ACP 网关端口(自家进程): %d", port)
                    return port

    ports = [p for p in _local_listen_ports() if p not in own]
    if ports:
        with ThreadPoolExecutor(max_workers=24) as pool:
            for port, ok in zip(ports, pool.map(_probe_gateway, ports)):
                if ok:
                    _port_cache = port
                    log.info("WorkBuddy ACP 网关端口: %d", port)
                    return port

    raise BackendError(
        "没有找到 WorkBuddy 的 ACP 网关。\n\n"
        "实测 WorkBuddy 5.8.3 起,这个本地接口不一定会随开机自启就打开 —— "
        "有时要真正进入一个对话它才监听端口。\n\n"
        "可以这样试:\n"
        "1) 打开 WorkBuddy,点进任意一个对话(让它处于活跃状态),再点「重试」;\n"
        "2) 或者在设置里手动填「网关地址」(格式 127.0.0.1:端口);\n"
        "3) 实在不行,把回答来源换成「豆包官方 API」。"
    )


def parse_sse_line(line: str) -> dict | None:
    """解析一行 SSE,返回里面的 JSON-RPC 对象。"""
    text = line.strip()
    if not text.startswith("data:"):
        return None
    payload = text[5:].strip()
    if not payload or payload == "[DONE]":
        return None
    try:
        obj = json.loads(payload)
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


def text_from_event(obj: dict, session_id: str | None = None) -> str | None:
    """从一条 JSON-RPC 消息里取出回答增量。

    注意:ACP 的流里会混入「当前会话」的通知(实测过,WorkBuddy 自己也被坑过),
    所以必须用 sessionId 严格过滤,否则会把别人会话的内容当成答案。
    """
    params = obj.get("params")
    if not isinstance(params, dict):
        return None
    if session_id is not None and params.get("sessionId") != session_id:
        return None
    update = params.get("update")
    if not isinstance(update, dict):
        return None
    if update.get("sessionUpdate") != "agent_message_chunk":
        return None
    content = update.get("content")
    if not isinstance(content, dict):
        return None
    if content.get("type") != "text":
        return None
    text = content.get("text")
    return text if isinstance(text, str) else None


def shrink_image(png: bytes) -> bytes:
    """太大就等比缩一下,避免 base64 之后过大被服务端拒绝。"""
    if len(png) <= _MAX_IMAGE_BYTES:
        return png
    try:
        import io

        from PIL import Image
    except ImportError:  # pragma: no cover
        return png
    try:
        image = Image.open(io.BytesIO(png))
        if image.width <= _MAX_IMAGE_WIDTH:
            return png
        ratio = _MAX_IMAGE_WIDTH / float(image.width)
        image = image.resize((_MAX_IMAGE_WIDTH, max(1, int(image.height * ratio))))
        buffer = io.BytesIO()
        image.save(buffer, "PNG", optimize=True)
        shrunk = buffer.getvalue()
        log.info("截图过大,已从 %d 缩到 %d 字节", len(png), len(shrunk))
        return shrunk if len(shrunk) < len(png) else png
    except Exception as exc:  # pragma: no cover
        log.warning("缩放截图失败: %s", exc)
        return png


_WARMUP_PNG: str | None = None


def _warmup_png_b64() -> str:
    """一张极小的热身图(200x80 白底黑字),第一次用完就缓存。"""
    global _WARMUP_PNG
    if _WARMUP_PNG is None:
        png = b""
        try:
            from PySide6.QtCore import QBuffer, QIODevice
            from PySide6.QtGui import QColor, QImage, QPainter

            image = QImage(200, 80, QImage.Format.Format_ARGB32)
            image.fill(QColor(255, 255, 255))
            painter = QPainter(image)
            painter.setPen(QColor(0, 0, 0))
            painter.drawText(20, 50, "1 + 1 = ?")
            painter.end()
            buffer = QBuffer()
            buffer.open(QIODevice.OpenModeFlag.WriteOnly)
            image.save(buffer, "PNG")
            png = bytes(buffer.data())
        except Exception:
            # 拿不到 Qt 就退化成一张 1x1 的图,仍然能触发视觉路径
            png = base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
            )
        _WARMUP_PNG = base64.b64encode(png).decode("ascii")
    return _WARMUP_PNG


class WorkBuddyAcpBackend:
    label = "WorkBuddy(ACP)"

    def __init__(self, cfg: dict, system_prompt: str = "") -> None:
        self.cfg = cfg or {}
        self.system_prompt = system_prompt or ""
        self._connection_id: str | None = None
        self._session_id: str | None = None
        self._port: int | None = None
        self._host = "127.0.0.1"
        self._initialized = False
        self._gateway_token: str | None = None
        self._lock = threading.RLock()
        self._warmed = False

    # ---------- 传输 ----------

    def _url(self, path: str) -> str:
        if self._port is None:
            manual = str(self.cfg.get("gateway_url") or "")
            parsed = _parse_manual(manual)
            if parsed is not None:
                self._host, self._port = parsed
            else:
                self._port = find_gateway_port(manual=manual)
                self._host = "127.0.0.1"
        return "http://%s:%d%s" % (self._host, self._port, path)

    def _post(self, path: str, body: dict | None, *, stream: bool = False,
              timeout: int = 30, headers: dict | None = None):
        data = json.dumps(body or {}).encode("utf-8")
        request = urllib.request.Request(self._url(path), data=data, method="POST")
        request.add_header("Content-Type", "application/json")
        request.add_header("Accept", "text/event-stream, application/json")
        token = self._token()
        if token:
            request.add_header("Authorization", "Bearer " + token)
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        try:
            return urllib.request.urlopen(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            detail = exc.read(400).decode("utf-8", "replace")
            if exc.code == 401:
                raise BackendError(
                    "WorkBuddy 网关要求鉴权(401)。请确认 WorkBuddy 桌面端处于登录状态。"
                ) from exc
            raise BackendError("请求 WorkBuddy 网关失败(HTTP %d):%s" % (exc.code, detail[:300])) from exc
        except Exception as exc:
            self._port = None
            self._connection_id = None
            self._initialized = False
            raise BackendError(
                "连不上 WorkBuddy 网关:" + str(exc) + "\n请确认 WorkBuddy 桌面端正在运行。"
            ) from exc

    def _token(self) -> str | None:
        """网关开了密码鉴权时,先用密码换一个 token(失败就算了,老版本不需要)。"""
        if self._gateway_token:
            return self._gateway_token
        password = str(self.cfg.get("gateway_password") or "").strip()
        if not password:
            return None
        try:
            payload = json.dumps({"password": password}).encode("utf-8")
            request = urllib.request.Request(
                "http://%s:%d/api/v1/auth/login" % (self._host, self._port or 0),
                data=payload, method="POST")
            request.add_header("Content-Type", "application/json")
            with urllib.request.urlopen(request, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8", "replace"))
            token = data.get("token") or data.get("accessToken")
            if token:
                self._gateway_token = str(token)
                log.info("网关密码登录成功")
            return self._gateway_token
        except Exception as exc:
            log.debug("网关密码登录失败(可能不需要): %s", exc)
            return None

    def _acp_headers(self) -> dict:
        if not self._connection_id:
            raise BackendError("内部错误:还没有建立 ACP 连接。")
        return {"acp-connection-id": self._connection_id}

    # ---------- 握手 ----------

    def connect(self) -> str:
        with self._post(CONNECT_PATH, {}) as resp:
            payload = json.loads(resp.read().decode("utf-8", "replace"))
        connection_id = payload.get("connectionId")
        if not connection_id:
            raise BackendError("WorkBuddy 网关没有返回 connectionId。")
        self._connection_id = connection_id
        self._initialized = False
        return connection_id

    def _ensure_initialized(self) -> None:
        """一条连接只 initialize 一次(重复调用会被网关拒绝)。"""
        with self._lock:
            if self._initialized:
                return
            self._rpc("initialize", {"protocolVersion": 1, "clientCapabilities": {}}, 1, seconds=20)
            self._initialized = True

    def _rpc(self, method: str, params: dict, rid: int, *, seconds: float = 30,
             on_event: Callable[[dict], None] | None = None) -> list[dict]:
        """发一个 JSON-RPC 请求,读 SSE 直到看到它的响应。"""
        payload = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params}
        events: list[dict] = []
        response = self._post(ACP_PATH, payload, stream=True, timeout=max(30, int(seconds) + 10),
                              headers=self._acp_headers())
        with response:
            started = time.time()
            while time.time() - started < seconds:
                line = response.readline()
                if not line:
                    break
                obj = parse_sse_line(line.decode("utf-8", "replace"))
                if obj is None:
                    continue
                events.append(obj)
                if on_event is not None:
                    on_event(obj)
                if obj.get("id") == rid and ("result" in obj or "error" in obj):
                    if "error" in obj:
                        message = json.dumps(obj["error"], ensure_ascii=False)
                        raise BackendError("WorkBuddy 返回错误:" + message[:400])
                    break
        return events

    def _ensure_session(self) -> str:
        """拿到一个**隔离的新会话**。

        实测:一条连接上第一次 session/new 会贴到"当前正在用的会话",
        第二次才真正新建。所以这里故意调两次,只用第二次的结果。
        """
        with self._lock:
            return self._ensure_session_locked()

    def _ensure_session_locked(self) -> str:
        if self._session_id and bool(self.cfg.get("reuse_session", True)):
            return self._session_id

        cwd = str(self.cfg.get("cwd") or "").strip()
        if not cwd:
            # 专用目录:会话挂在这里,不会混进你的项目
            cwd = str(Path(tempfile.gettempdir()) / "screenqa_acp")
        Path(cwd).mkdir(parents=True, exist_ok=True)

        params_base = {"cwd": cwd, "mcpServers": []}
        self._rpc("session/new", dict(params_base), 20, seconds=20)          # 丢弃:可能是当前会话
        events = self._rpc("session/new", dict(params_base), 21, seconds=25)  # 这个才是新的

        session_id = None
        for obj in events:
            result = obj.get("result")
            if isinstance(result, dict) and result.get("sessionId"):
                session_id = result["sessionId"]
            params = obj.get("params") or {}
            if not session_id and params.get("sessionId"):
                session_id = params["sessionId"]
        if not session_id:
            raise BackendError("WorkBuddy 没有返回 sessionId,无法开始答题。")

        self._session_id = session_id
        log.info("WorkBuddy 隔离会话: %s (cwd=%s)", session_id, cwd)
        self._apply_options(session_id)
        return session_id

    def _apply_options(self, session_id: str) -> None:
        """按配置调整这次会话的思考强度和模型(真正影响速度的就是这两个)。"""
        thought = str(self.cfg.get("thought_level") or "").strip()
        model = str(self.cfg.get("model") or "").strip()
        if not thought and not model:
            return
        try:
            if thought:
                self._rpc(
                    "session/set_config_option",
                    {"sessionId": session_id, "configId": "thought_level", "value": thought},
                    22, seconds=15,
                )
                log.info("思考强度设为 %s", thought)
            if model:
                self._rpc(
                    "session/set_config_option",
                    {"sessionId": session_id, "configId": "model", "value": model},
                    23, seconds=15,
                )
                log.info("模型设为 %s", model)
        except BackendError as exc:
            # 设置失败不影响答题,只是没那么快
            log.warning("调整会话参数失败(继续用默认值): %s", exc)

    def reconnect(self) -> None:
        """丢弃连接/会话/端口缓存,下次提问重新找一遍。"""
        global _port_cache
        _port_cache = None
        self._connection_id = None
        self._session_id = None
        self._initialized = False
        self._port = None
        self._gateway_token = None

    def start_fresh_session(self) -> None:
        """下次提问时开一个全新会话。"""
        self._session_id = None

    # ---------- 主流程 ----------

    def stream_answer(
        self,
        image_png: bytes | None,
        user_text: str,
        previous_answer: str | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> Iterator[str]:
        if not image_png:
            raise BackendError("没有截图,无法提问。")

        if self._connection_id is None:
            self.connect()
        self._ensure_initialized()
        session_id = self._ensure_session()

        content: list[dict] = []
        text = user_text
        if self.system_prompt and not previous_answer:
            text = text + "\n" + self.system_prompt
        if previous_answer:
            text = text + "\n(上一轮的解答供参考:" + previous_answer[:1500] + ")"
        content.append({"type": "text", "text": text})
        content.append({
            "type": "image",
            "data": base64.b64encode(shrink_image(image_png)).decode("ascii"),
            "mimeType": "image/png",
        })

        timeout = float(self.cfg.get("timeout_seconds", 300) or 300)
        payload = {"jsonrpc": "2.0", "id": 30, "method": "session/prompt",
                   "params": {"sessionId": session_id, "prompt": content}}

        response = self._post(ACP_PATH, payload, stream=True,
                              timeout=max(30, int(timeout) + 20), headers=self._acp_headers())
        emitted = 0
        got_done = False
        started = time.time()
        with response:
            while time.time() - started < timeout:
                if should_cancel is not None and should_cancel():
                    log.info("用户取消了这次提问")
                    break
                line = response.readline()
                if not line:
                    break
                obj = parse_sse_line(line.decode("utf-8", "replace"))
                if obj is None:
                    continue
                piece = text_from_event(obj, session_id)
                if piece:
                    emitted += len(piece)
                    yield piece
                    continue
                if obj.get("id") == 30 and ("result" in obj or "error" in obj):
                    got_done = True
                    if "error" in obj:
                        raise BackendError(
                            "WorkBuddy 返回错误:" + json.dumps(obj["error"], ensure_ascii=False)[:400]
                        )
                    break

        if emitted == 0:
            raise BackendError(
                "WorkBuddy 没有给出回答。\n\n"
                "可能是这次提问被中断,或者会话状态异常;再试一次通常就好了。"
            )
        if not got_done:
            log.info("回答流结束(用时 %.1fs)", time.time() - started)

    def warm_up(self) -> bool:
        """预热会话。

        实测:一个新建会话的**第一次**调用要多花约 11 秒(agent 初始化),
        之后每道题只要 4~6 秒。所以在程序启动时先偷偷跑一句话把它烧掉。
        """
        if self._warmed:
            return True
        try:
            with self._lock:
                if self._connection_id is None:
                    self.connect()
                self._ensure_initialized()
                session_id = self._ensure_session_locked()
                started = time.time()
                # 必须带一张图:视觉那条冷启动路径和纯文字是分开的,只发文字烧不掉
                payload = {"jsonrpc": "2.0", "id": 90, "method": "session/prompt",
                           "params": {"sessionId": session_id, "prompt": [
                               {"type": "text", "text": "热身:请只回复两个字 就绪。"},
                               {"type": "image", "data": _warmup_png_b64(), "mimeType": "image/png"}]}}
                response = self._post(ACP_PATH, payload, stream=True, timeout=120,
                                      headers=self._acp_headers())
                with response:
                    deadline = time.time() + 90
                    while time.time() < deadline:
                        line = response.readline()
                        if not line:
                            break
                        obj = parse_sse_line(line.decode("utf-8", "replace"))
                        if obj and obj.get("id") == 90 and ("result" in obj or "error" in obj):
                            break
                self._warmed = True
                log.info("WorkBuddy 会话预热完成(%.1fs)", time.time() - started)
                return True
        except Exception as exc:
            log.warning("预热失败(不影响使用): %s", exc)
            return False

    # ---------- 诊断 ----------

    def test(self) -> str:
        self.reconnect()
        port = find_gateway_port(force=True, manual=str(self.cfg.get("gateway_url") or ""))
        self._port = port
        if self._connection_id is None:
            self.connect()
        self._ensure_initialized()
        session_id = self._ensure_session()
        return (
            "WorkBuddy ACP 可用。\n"
            "网关端口:%d\n隔离会话:%s\n"
            "图片能力:已确认支持(promptCapabilities.image = true)\n"
            "事件已按 sessionId 过滤,不会串到你的其它会话"
            % (port, session_id)
        )