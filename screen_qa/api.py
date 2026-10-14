"""豆包(火山方舟)客户端:把题目截图直接发给视觉大模型,流式拿回解答。

默认走火山方舟(豆包官方 API),接口与 OpenAI Chat Completions 完全兼容,
所以把 base_url / model 换成任何支持图片输入的 OpenAI 兼容服务也能用
(DeepSeek、通义千问 VL、智谱 GLM-4V 等)。
"""

from __future__ import annotations

import base64
import json
from typing import Any, Callable, Iterator

import requests

from .errors import BackendError



# 统一用 BackendError,原来的名字保留成别名,老代码/测试不用改
ApiError = BackendError


def _explain_http_error(status: int, body: str) -> str:
    detail = ""
    try:
        payload = json.loads(body)
        err = payload.get("error") or payload
        if isinstance(err, dict):
            detail = str(err.get("message") or err.get("code") or "")
        elif isinstance(err, str):
            detail = err
    except Exception:
        detail = (body or "")[:300]

    table = {
        400: "请求被拒绝(400)。多半是模型名写错,或该模型不支持图片输入。",
        401: "API Key 无效或已失效(401)。请在设置里检查 API Key。",
        403: "没有访问权限(403)。请确认已在火山方舟开通该模型的调用权限。",
        404: "接口或模型不存在(404)。请检查 Base URL 与模型名。",
        429: "请求过于频繁或额度不足(429)。稍后再试或检查账户余额。",
    }
    head = table.get(status, f"请求失败(HTTP {status})。")
    return f"{head}\n\n接口返回:{detail}" if detail else head


class DoubaoClient:
    """截图 -> 豆包 -> 解答。"""

    def __init__(self, api_cfg: dict, system_prompt: str, followup_prompt: str) -> None:
        self.cfg = api_cfg or {}
        self.system_prompt = system_prompt or ""
        self.followup_prompt = followup_prompt or ""
        self.session = requests.Session()

    # ---------- 内部工具 ----------

    @property
    def base_url(self) -> str:
        return str(self.cfg.get("base_url") or "").rstrip("/")

    def _headers(self) -> dict:
        key = str(self.cfg.get("api_key") or "").strip()
        if not key:
            raise ApiError("还没有配置 API Key。请右键任务栏托盘图标 -> 设置,填入豆包(火山方舟)API Key。")
        return {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }

    def _payload(self, messages: list, stream: bool) -> dict:
        payload: dict[str, Any] = {
            "model": self.cfg.get("model"),
            "messages": messages,
            "stream": stream,
            "temperature": float(self.cfg.get("temperature", 0.2)),
            "max_tokens": int(self.cfg.get("max_tokens", 4096)),
        }
        thinking = str(self.cfg.get("thinking") or "").strip().lower()
        if thinking in ("disabled", "enabled", "auto"):
            # 豆包 Seed 系列支持 thinking 开关;关掉可以直接出答案,速度更快
            payload["thinking"] = {"type": thinking}
        return payload

    def _content(self, text: str, image_png: bytes | None) -> list:
        parts: list[dict] = []
        if image_png:
            b64 = base64.b64encode(image_png).decode("ascii")
            parts.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}})
        parts.append({"type": "text", "text": text})
        return parts

    def build_messages(self, image_png: bytes | None, user_text: str, previous_answer: str | None) -> list:
        messages: list[dict] = []
        if self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})
        if previous_answer:
            # 追问:带上上一轮的题目和答案,让模型知道"这题"指哪道题
            messages.append({"role": "assistant", "content": previous_answer[:4000]})
        messages.append({"role": "user", "content": self._content(user_text, image_png)})
        return messages

    # ---------- 对外接口 ----------

    def stream_answer(
        self,
        image_png: bytes | None,
        user_text: str,
        previous_answer: str | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> Iterator[str]:
        """流式返回解答片段。出错时抛 ApiError。"""
        url = f"{self.base_url}/chat/completions"
        payload = self._payload(self.build_messages(image_png, user_text, previous_answer), stream=True)
        timeout = int(self.cfg.get("timeout", 180))

        try:
            resp = self.session.post(
                url, headers=self._headers(), json=payload, stream=True, timeout=(15, timeout)
            )
        except requests.RequestException as exc:
            raise ApiError(f"无法连接接口:{exc}\n请检查网络,或确认 Base URL 是否正确。") from exc

        with resp:
            if resp.status_code != 200:
                raise ApiError(_explain_http_error(resp.status_code, resp.text))

            ctype = (resp.headers.get("Content-Type") or "").lower()
            if "text/event-stream" not in ctype:
                # 服务端忽略了 stream=true,直接返回整包 JSON
                try:
                    data = resp.json()
                except ValueError as exc:
                    raise ApiError("接口返回了无法解析的内容,请检查 Base URL / 模型名。") from exc
                text = _extract_message(data)
                if text:
                    yield text
                return

            for raw in resp.iter_lines(decode_unicode=False):
                if should_cancel is not None and should_cancel():
                    break
                if not raw:
                    continue
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                chunk = line[5:].strip()
                if chunk == "[DONE]":
                    break
                try:
                    data = json.loads(chunk)
                except ValueError:
                    continue
                if isinstance(data, dict) and data.get("error"):
                    raise ApiError(_explain_http_error(200, json.dumps(data, ensure_ascii=False)))
                piece = _extract_delta(data)
                if piece:
                    yield piece

    def check(self) -> str:
        """设置界面的"测试连接"用:发一条最小的文本请求。"""
        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.cfg.get("model"),
            "messages": [{"role": "user", "content": "回复两个字:正常"}],
            "max_tokens": 16,
            "stream": False,
        }
        try:
            resp = self.session.post(url, headers=self._headers(), json=payload, timeout=30)
        except requests.RequestException as exc:
            raise ApiError(f"无法连接接口:{exc}") from exc
        if resp.status_code != 200:
            raise ApiError(_explain_http_error(resp.status_code, resp.text))
        try:
            data = resp.json()
        except ValueError as exc:
            raise ApiError("接口返回了无法解析的内容,请检查 Base URL。") from exc
        reply = _extract_message(data) or "(空回复)"
        return f"连接成功,模型回复:{reply.strip()[:80]}"


def _extract_delta(data: dict) -> str:
    """从流式分片里取出正文(忽略思维链 reasoning_content)。"""
    try:
        choices = data.get("choices") or []
        if not choices:
            return ""
        delta = choices[0].get("delta") or {}
        if not isinstance(delta, dict):
            return ""
        content = delta.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):  # 少数实现会把 content 拆成数组
            return "".join(
                part.get("text", "") for part in content if isinstance(part, dict)
            )
    except Exception:
        return ""
    return ""


def _extract_message(data: dict) -> str:
    """从非流式响应里取出正文。"""
    try:
        choices = data.get("choices") or []
        if not choices:
            return ""
        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(part.get("text", "") for part in content if isinstance(part, dict))
    except Exception:
        return ""
    return ""