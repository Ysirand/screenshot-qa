"""本地假的火山方舟接口(OpenAI 兼容),用来离线验证整条链路。

它会把收到的请求原样记下来,测试可以据此检查:
- 有没有带 Authorization
- 有没有真的把图片作为 base64 data URL 塞进 content
- 流式分片能不能被正确解析(reasoning_content 要被忽略)
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ANSWER = """【答案】B

【解析】
- 选项 A:把速度和路程混为一谈,单位对不上。
- 选项 B:由 v = s / t 得 36 km/h = 10 m/s,正确。
- 选项 C、D:忽略了单位换算。

如果换成公式写法:`s = v * t`,代入即可。

```python
v = 36 * 1000 / 3600   # 10 m/s
print(v)
```
"""

VALID_KEY = "test-key-123456"

SIMPLE_ANSWER = "【答案】C\n\n【解析】这是自定义接口返回的解答。"

REASONING = "我先读题……这是选择题,先算单位换算……"


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args) -> None:  # 静音
        return

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            payload = {}

        server = self.server
        server.requests.append(
            {"path": self.path, "headers": dict(self.headers), "payload": payload}
        )

        if self.path.startswith("/simple"):
            return self._simple(payload)

        auth = self.headers.get("Authorization") or ""
        if auth != "Bearer " + VALID_KEY:
            return self._json(
                401, {"error": {"message": "invalid api key", "code": "AuthenticationError"}}
            )

        if payload.get("model") == "unknown-model":
            return self._json(
                404, {"error": {"message": "model not found", "code": "ModelNotFound"}}
            )

        if payload.get("stream"):
            return self._stream(payload)

        return self._json(
            200,
            {
                "choices": [
                    {"message": {"role": "assistant", "content": ANSWER}, "finish_reason": "stop"}
                ]
            },
        )

    def _simple(self, payload: dict) -> None:
        """/simple/json 返回 JSON,/simple/sse 返回流式,/simple/text 返回纯文本。"""
        if self.path.endswith("/sse"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                for i in range(0, len(SIMPLE_ANSWER), 8):
                    obj = {"delta": {"text": SIMPLE_ANSWER[i : i + 8]}}
                    self.wfile.write(("data: " + json.dumps(obj, ensure_ascii=False) + "\n\n").encode("utf-8"))
                    self.wfile.flush()
                    time.sleep(0.005)
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                pass
            return

        if self.path.endswith("/text"):
            body = SIMPLE_ANSWER.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        return self._json(200, {"data": {"answer": SIMPLE_ANSWER, "has_image": bool(payload.get("image_base64"))}})

    def _stream(self, payload: dict) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()

        def send(obj: dict) -> None:
            self.wfile.write(("data: " + json.dumps(obj, ensure_ascii=False) + "\n\n").encode("utf-8"))
            self.wfile.flush()

        try:
            for piece in _chunks(REASONING, 0):
                send({"choices": [{"delta": {"reasoning_content": piece}}]})
                time.sleep(0.005)
            for piece in _chunks(ANSWER, 7):
                send({"choices": [{"delta": {"content": piece}}]})
                time.sleep(0.01)
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass


def _chunks(text: str, size: int) -> list[str]:
    if size <= 0:
        return [text]
    return [text[i : i + size] for i in range(0, len(text), size)]


def _has_image(payload: dict) -> bool:
    for message in payload.get("messages") or []:
        content = message.get("content")
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "image_url":
                    url = (part.get("image_url") or {}).get("url") or ""
                    if url.startswith("data:image/") and ";base64," in url:
                        return True
    return False


class MockArk:
    def __init__(self) -> None:
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.server.requests = []
        self.server.requests_ok_auth = False
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        host, port = self.server.server_address[:2]
        return "http://127.0.0.1:{0}/v1".format(port)

    @property
    def requests(self) -> list:
        return self.server.requests

    def __enter__(self) -> "MockArk":
        self.thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self.server.shutdown()
        self.server.server_close()