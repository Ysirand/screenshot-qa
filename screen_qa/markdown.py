"""把大模型返回的 Markdown 转成 QTextBrowser 能显示的 HTML(够用就好)。"""

from __future__ import annotations

import html
import re

_CODE_BLOCK = re.compile(r"```[a-zA-Z0-9_+-]*\n(.*?)```", re.DOTALL)
_HEADING = re.compile(r"^(#{1,4})\s+(.*)$")
_BULLET = re.compile(r"^[-*+]\s+(.*)$")
_NUMBERED = re.compile(r"^(\d+)[.)]\s+(.*)$")
_QUOTE = re.compile(r"^>\s?(.*)$")
_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")

PLACEHOLDER = "\u0000BLOCK{}\u0000"


def to_html(text: str) -> str:
    """转成 HTML 片段。代码块先摘出来,避免里面的符号被当成 Markdown。"""
    if not text:
        return ""

    blocks: list[str] = []

    def stash(match: re.Match) -> str:
        code = html.escape(match.group(1).rstrip())
        blocks.append("<pre>" + code + "</pre>")
        return PLACEHOLDER.format(len(blocks) - 1)

    text = _CODE_BLOCK.sub(stash, text)

    out: list[str] = []
    list_mode: str | None = None

    def close_list() -> None:
        nonlocal list_mode
        if list_mode:
            out.append("</" + list_mode + ">")
            list_mode = None

    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if not line:
            close_list()
            continue

        heading = _HEADING.match(line)
        if heading:
            close_list()
            level = min(len(heading.group(1)) + 2, 6)
            out.append("<h{0}>{1}</h{0}>".format(level, _inline(heading.group(2))))
            continue

        bullet = _BULLET.match(line)
        if bullet:
            if list_mode != "ul":
                close_list()
                out.append("<ul>")
                list_mode = "ul"
            out.append("<li>" + _inline(bullet.group(1)) + "</li>")
            continue

        numbered = _NUMBERED.match(line)
        if numbered:
            if list_mode != "ol":
                close_list()
                out.append("<ol>")
                list_mode = "ol"
            out.append("<li>" + _inline(numbered.group(2)) + "</li>")
            continue

        quote = _QUOTE.match(line)
        if quote:
            close_list()
            out.append("<blockquote>" + _inline(quote.group(1)) + "</blockquote>")
            continue

        close_list()
        out.append("<p>" + _inline(line) + "</p>")

    close_list()
    body = "\n".join(out)

    for index, block in enumerate(blocks):
        body = body.replace(PLACEHOLDER.format(index), block)
    return body


def _inline(text: str) -> str:
    """行内元素:粗体、斜体、行内代码、链接、删除线。"""
    out = html.escape(text)
    out = re.sub(r"`([^`]+)`", r"<code>\1</code>", out)
    out = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", out)
    out = re.sub(r"~~(.+?)~~", r"<s>\1</s>", out)
    out = re.sub(r"(?<![A-Za-z0-9_*])\*([^*]+)\*(?![A-Za-z0-9_*])", r"<i>\1</i>", out)
    out = _LINK.sub(r'<a href="\2">\1</a>', out)
    return out


def plain_text(markdown_text: str) -> str:
    """把 Markdown 粗体等标记去掉,用于复制到剪贴板。"""
    text = _CODE_BLOCK.sub(lambda m: m.group(1).rstrip(), markdown_text)
    text = text.replace("**", "").replace("~~", "")
    text = _LINK.sub(r"\1 (\2)", text)
    return re.sub(r"^#{1,4}\s+", "", text, flags=re.MULTILINE)