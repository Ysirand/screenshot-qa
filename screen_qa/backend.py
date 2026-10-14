"""后端工厂:根据配置挑一个"谁来回答"。

- openai : 豆包官方 API,或任何 OpenAI 兼容的多模态接口
- cli    : 本机已登录的智能体 CLI(WorkBuddy / CodeBuddy)
- simple : 你自己的 HTTP 接口
"""

from __future__ import annotations

from typing import Any, Callable, Iterator

from .api import DoubaoClient
from .cli_backend import CliBackend
from .workbuddy_acp_backend import WorkBuddyAcpBackend
from .config import get
from .errors import BackendError
from .logging_setup import log
from .simple_http_backend import SimpleHttpBackend

BACKEND_CHOICES: list[tuple[str, str]] = [
    ("workbuddy_acp", "本地 WorkBuddy(ACP · 免 Key,免打扰,独立会话)"),
    ("openai", "豆包官方 API / 任意 OpenAI 兼容接口"),
    ("cli", "本地智能体命令行(WorkBuddy · CodeBuddy)"),
    ("simple", "自定义 HTTP 接口(自己的本地平台)"),
]

BACKEND_LABELS = {key: label for key, label in BACKEND_CHOICES}
DEFAULT_BACKEND = "workbuddy_acp"


class OpenAiBackend:
    """豆包官方 API(以及任何 OpenAI 兼容接口)。"""

    label = "豆包官方 API"

    def __init__(self, cfg: dict, system_prompt: str, followup_prompt: str) -> None:
        self.client = DoubaoClient(cfg, system_prompt, followup_prompt)

    def stream_answer(
        self,
        image_png: bytes | None,
        user_text: str,
        previous_answer: str | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> Iterator[str]:
        return self.client.stream_answer(image_png, user_text, previous_answer, should_cancel)

    def test(self) -> str:
        return self.client.check()


def build_backend(cfg: dict) -> Any:
    """按配置创建后端对象。"""
    name = str(get(cfg, "backend", DEFAULT_BACKEND) or DEFAULT_BACKEND).strip().lower()
    system_prompt = str(cfg.get("system_prompt", "") or "")
    followup_prompt = str(cfg.get("followup_prompt", "") or "")

    if name == "workbuddy_acp":
        return WorkBuddyAcpBackend(cfg.get("workbuddy_acp") or {}, system_prompt)
    if name == "cli":
        return CliBackend(cfg.get("cli") or {}, system_prompt)
    if name == "simple":
        return SimpleHttpBackend(cfg.get("simple") or {})
    if name != "openai":
        log.warning("未知后端 %r,回退到 openai", name)
    return OpenAiBackend(cfg.get("api") or {}, system_prompt, followup_prompt)


def backend_label(cfg: dict) -> str:
    return BACKEND_LABELS.get(str(get(cfg, "backend", DEFAULT_BACKEND)), "未知后端")


__all__ = [
    "BACKEND_CHOICES",
    "BACKEND_LABELS",
    "BackendError",
    "DEFAULT_BACKEND",
    "OpenAiBackend",
    "backend_label",
    "build_backend",
]