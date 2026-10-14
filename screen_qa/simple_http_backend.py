"""自定义 HTTP 后端:把截图 POST 给你自己的本地/远程接口。

适配"我这个平台有 HTTP 接口但契约我说了算"的场景(自建 agent 平台、Dify、扣子、
本地网关等都行)。请求体默认长这样:

    { "prompt": "...", "image_base64": "...", "image_mime": "image/png" }

响应支持两种:
- 普通 JSON:按 response_path(点号路径)取值,如 "text" / "data.answer"
- SSE(text/event-stream):逐行读 data:,边到边显示
"""

from __future__ import annotations

import base64
import json
from typing import Any, Callable, Iterator

import requests

from .errors import BackendError
from .logging_setup import log

# 没配 response_path 时,按这些常见字段名依次尝试
_FALLBACK_KEYS = ("text", "content", "answer", "result", "output", "message", "data")


def _dig(obj: Any, path: str) -> Any:
    """按 "data.answer" 这样的路径取值;path 为空时返回原对象。"""
    node = obj
    for part in str(path).split("."):
        part = part.strip()
        if not part:
            continue
        if isinstance(node, dict):
            if part not in node:
                return None
            node = node[part]
        elif isinstance(node, list):
            try:
                node = node[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return node


def _pick_text(obj: Any, preferred: str) -> str | None:
    """从响应里挑出正文。"""
    if isinstance(obj, str):
        return obj
    if not isinstance(obj, (dict, list)):
        return None

    if preferred:
        value = _dig(obj, preferred)
        if isinstance(value, str) and value:
            return value

    if isinstance(obj, dict):
        for key in _FALLBACK_KEYS:
            value = obj.get(key)
            if isinstance(value, str) and value:
                return value
            if isinstance(value, (dict, list)):
                nested = _pick_text(value, "")
                if nested:
                    return nested
    elif obj:
        nested = _pick_text(obj[0], "")
        if nested:
            return nested
    return None


class SimpleHttpBackend:
    """把你自己的 HTTP 接口包成后端。"""

    label = "自定义 HTTP 接口"

    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg or {}
        self.session = requests.Session()

    @property
    def url(self) -> str:
        return str(self.cfg.get("url") or "").strip()

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        extra = self.cfg.get("headers") or {}
        if isinstance(extra, dict):
            for key, value in extra.items():
                if str(value or "").strip():
                    headers[str(key)] = str(value)
        return headers

    def _timeout(self) -> tuple[int, int]:
        seconds = int(self.cfg.get("timeout_seconds", 180) or 180)
        return (15, seconds)

    def _body(self, image_png: bytes | None, user_text: str) -> dict:
        body: dict[str, Any] = {}
        prompt_field = str(self.cfg.get("prompt_field") or "prompt")
        body[prompt_field] = user_text
        if image_png:
            image_field = str(self.cfg.get("image_field") or "image_base64")
            body[image_field] = base64.b64encode(image_png).decode("ascii")
            body.setdefault("image_mime", "image/png")
        return body

    # ---------- 主流程 ----------

    def stream_answer(
        self,
        image_png: bytes | None,
        user_text: str,
        previous_answer: str | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> Iterator[str]:
        if not self.url:
            raise BackendError("还没有填写接口地址。请在设置里填上你本地平台的 HTTP 接口 URL。")

        prompt = user_text
        if previous_answer:
            prompt = prompt + "\n\n(上一轮的解答供参考:" + previous_answer[:2000] + ")"

        try:
            response = self.session.post(
                self.url,
                headers=self._headers(),
                json=self._body(image_png, prompt),
                stream=True,
                timeout=self._timeout(),
            )
        except requests.RequestException as exc:
            raise BackendError(
                "连不上接口:" + str(exc) + "\n请确认平台在运行,且地址填写正确。"
            ) from exc

        with response:
            if response.status_code != 200:
                raise BackendError(
                    "接口返回 HTTP "
                    + str(response.status_code)
                    + ":\n"
                    + (response.text or "")[:400]
                )

            ctype = (response.headers.get("Content-Type") or "").lower()
            preferred = str(self.cfg.get("response_path") or "").strip()

            if "text/event-stream" in ctype:
                for raw in response.iter_lines(decode_unicode=False):
                    if should_cancel is not None and should_cancel():
                        break
                    if not raw:
                        continue
                    line = raw.decode("utf-8", "replace").strip()
                    if not line.startswith("data:"):
                        continue
                    chunk = line[5:].strip()
                    if chunk in ("[DONE]", ""):
                        continue
                    try:
                        obj = json.loads(chunk)
                    except ValueError:
                        yield chunk
                        continue
                    text = _pick_text(obj, preferred)
                    if text:
                        yield text
                return

            try:
                obj = response.json()
            except ValueError:
                text = (response.text or "").strip()
                if not text:
                    raise BackendError("接口没有返回内容。")
                yield text
                return

            text = _pick_text(obj, preferred)
            if not text:
                snippet = json.dumps(obj, ensure_ascii=False)[:400]
                raise BackendError(
                    "接口返回了内容,但没找到文本字段。\n可以在设置里指定取值路径(如 data.answer)。\n原始返回:"
                    + snippet
                )
            yield text

    def test(self) -> str:
        if not self.url:
            raise BackendError("还没有填写接口地址。")
        text = "".join(self.stream_answer(None, "ping"))[:80]
        return "接口可用,返回:" + (text or "(空)") + "\n地址:" + self.url


def _unused(_: Any) -> None:  # pragma: no cover
    log.debug("keep import")