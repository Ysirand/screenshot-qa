"""配置管理:读取/写入 config.json,并与默认值深度合并。"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any

APP_NAME = "截图自动搜题"
APP_VERSION = "1.0.0"

# 提示词版本:以后改了默认提示词就 +1,
# 老用户的 config.json 里存着旧提示词,靠这个自动升级(不然新要求永远不生效)
PROMPT_VERSION = 2

DEFAULT_CONFIG: dict[str, Any] = {
    # 谁来回答:workbuddy_acp(本地 WorkBuddy,推荐) / openai(官方 API)
    #            / cli(本地智能体命令行) / simple(自定义 HTTP 接口)
    "backend": "workbuddy_acp",
    # 主后端失败时自动切到哪个(留空 = 不切换)
    "fallback_backend": "",
    "workbuddy_acp": {
        "reuse_session": True,     # 复用同一个隔离会话,这样追问能记得上下文
        "cwd": "",                 # 会话挂在哪个目录,留空用临时目录
        "timeout_seconds": 300,
        "thought_level": "low",    # 思考强度:low 最快 / enabled 默认 / high / max
        "model": "",               # 留空 = 跟随 WorkBuddy 里选的模型;也可填 fast-model 等
        "warm_up": True,           # 启动时预热会话(实测能省掉第一次约 11 秒)
        "gateway_url": "",         # 手动指定网关地址,如 127.0.0.1:57464;留空自动探测
        "gateway_password": "",    # 网关开了密码鉴权时填(一般不用)
    },
    "api": {
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "api_key": "",
        "model": "doubao-seed-1-6-250615",
        "temperature": 0.2,
        "max_tokens": 4096,
        "timeout": 180,
        "thinking": "disabled",
    },
    "floating_button": {
        "enabled": True,
        "size": 46,
        "opacity": 0.62,
        "snap_edges": True,
        "trigger": "single",     # single(单击截图) / double(双击,防误触更强)
        "pos": None,
    },
    "cli": {
        "command": "",                      # 留空自动探测 WorkBuddy 自带的 CLI
        "tools": "Read",                    # 允许的工具,留空字符串表示全部禁用
        "permission_mode": "",
        "extra_args": [],
        "env": {
            "CODEBUDDY_AUTH_TOKEN": "",     # 可选:有 token 就不用交互式登录
            "CODEBUDDY_BASE_URL": "",
        },
        "timeout_seconds": 300,
    },
    "simple": {
        "url": "",
        "headers": {},
        "prompt_field": "prompt",
        "image_field": "image_base64",
        "response_path": "",                # 例如 "data.answer"
        "timeout_seconds": 180,
    },
    "hotkey": "Alt+Q",
    "prompt_version": PROMPT_VERSION,
    "system_prompt": (
        "你是一个答题助手。用户会发来一道题目(可能是选择题、填空题、判断题或解答题)的截图。\n"
        "请严格按照下面的格式回答:\n"
        "1. 第一行用【答案】开头,直接给出最终答案。选择题给出选项字母及选项内容;"
        "填空题/解答题给出关键结果。\n"
        "2. 空一行后,用【解析】开头,给出简明、分步骤的讲解,说明为什么选它、其他选项错在哪里。\n"
        "3. 若图片模糊、信息不全或含多道题,请指出,并按题号分别作答。\n"
        "4. 最后单独起一行,用【题目】开头,给出这道题的简短概括(不超过 20 字,方便归档检索)。\n"
        "请用简体中文回答,不要重复题目原文,不要输出与题目无关的内容。"
    ),
    "followup_prompt": "针对上面这道题,继续解答用户的追问:{question}",
    "appearance": {
        "opacity": 0.94,
        "font_size": 15,
        "width": 580,
        "height": 480,
        "auto_hide_seconds": 0,
        "last_pos": None,
    },
    "behavior": {
        "save_history": True,
        "copy_answer_to_clipboard": False,
        "show_window_highlight": True,
        "notify_on_done": False,
    },
    "autostart": False,
}


# 历史上出现过的默认提示词(用来判断用户是否自己改过)
_LEGACY_PROMPTS: set[str] = {
    (
        "你是一个答题助手。用户会发来一道题目(可能是选择题、填空题、判断题或解答题)的截图。\n"
        "请严格按照下面的格式回答:\n"
        "1. 第一行用【答案】开头,直接给出最终答案。选择题给出选项字母及选项内容;"
        "填空题/解答题给出关键结果。\n"
        "2. 空一行后,用【解析】开头,给出简明、分步骤的讲解,说明为什么选它、其他选项错在哪里。\n"
        "3. 若图片模糊、信息不全或含多道题,请指出,并按题号分别作答。\n"
        "请用简体中文回答,不要重复题目原文,不要输出与题目无关的内容。"
    ),
}

def app_dir() -> Path:
    """程序根目录(打包后为 exe 所在目录)。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def config_path() -> Path:
    return app_dir() / "config.json"


def history_dir() -> Path:
    d = app_dir() / "history"
    d.mkdir(parents=True, exist_ok=True)
    return d


def log_dir() -> Path:
    d = app_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def assets_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", app_dir())) / "assets"
    return Path(__file__).resolve().parent.parent / "assets"


def deep_merge(base: dict, override: dict) -> dict:
    """把 override 合并进 base 的副本(递归合并字典)。"""
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config() -> dict:
    """读取配置;文件不存在或损坏时回落到默认值。

    如果配置文件里存的是**旧版默认提示词**,自动升级成新版
    (否则我们改了默认提示词,老用户永远用不上)。
    """
    path = config_path()
    data: dict = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                data = {}
        except Exception:
            data = {}

    cfg = deep_merge(DEFAULT_CONFIG, data)
    _migrate_prompt(cfg, data)
    return cfg


def _migrate_prompt(cfg: dict, raw: dict) -> None:
    """把老配置里的旧默认提示词升级成新版(用户自己改过的提示词不动)。"""
    saved_version = int(raw.get("prompt_version") or 0)
    saved_prompt = str(raw.get("system_prompt") or "")
    if saved_version >= PROMPT_VERSION:
        return
    if saved_prompt and saved_prompt not in _LEGACY_PROMPTS:
        # 用户自己改过提示词 —— 尊重他的,只把版本号跟上
        cfg["prompt_version"] = PROMPT_VERSION
        return
    cfg["system_prompt"] = DEFAULT_CONFIG["system_prompt"]
    cfg["prompt_version"] = PROMPT_VERSION


def save_config(cfg: dict) -> None:
    path = config_path()
    text = json.dumps(cfg, ensure_ascii=False, indent=2)
    try:
        path.write_text(text, encoding="utf-8")
    except OSError:
        pass


def get(cfg: dict, dotted: str, default: Any = None) -> Any:
    """按 "api.model" 这样的路径取值。"""
    node: Any = cfg
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def set_value(cfg: dict, dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    node = cfg
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value