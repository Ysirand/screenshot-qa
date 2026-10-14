"""WorkBuddy 窗口后端(实验性,当前**没有**接进程序)。

【为什么不启用】
这条路实测只能"发"不能可靠"读":WorkBuddy 是 Electron 应用,聊天列表做了虚拟化,
UIA 只能看到当前渲染进视口的内容,而且键盘模拟(Ctrl+V / 打字 / 回车)不生效。
能用的部分(ValuePattern 写输入框、InvokePattern 点发送、UiaClientsAreListening
激活无障碍树)都在下面,留作参考。

**真正可用的是 workbuddy_acp_backend.py**(走 ACP 协议),默认后端就是它。

----
原始说明:直接操作 WorkBuddy 桌面端的聊天窗口。

流程(和真人手点一样):
    恢复窗口 -> 把截图放进剪贴板 -> 在输入框里 Ctrl+V -> 输入题目文字
    -> 回车发送 -> 轮询对话内容,把新出现的回答流式吐出来 -> 收尾时把窗口还原

前置条件:
- WorkBuddy 桌面端在运行且已登录(不需要任何 API Key / token);
- 需要短暂接管前台窗口和剪贴板(会提前备份剪贴板内容,结束后还原)。

之所以能读到界面内容:Chromium 只有在 UIA 客户端声明"我在监听"之后才会构建
无障碍树,所以先调一次 UiaClientsAreListening() 再找控件。
"""

from __future__ import annotations

import ctypes
import io
import time
from ctypes import wintypes
from typing import Callable, Iterator

from .errors import BackendError
from .logging_setup import log

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# 必须显式声明,否则 64 位句柄会被 ctypes 按 c_int 截断(GlobalLock 会返回空指针)
kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = ctypes.c_void_p
kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
kernel32.GlobalUnlock.restype = wintypes.BOOL
kernel32.GlobalSize.argtypes = [ctypes.c_void_p]
kernel32.GlobalSize.restype = ctypes.c_size_t

user32.OpenClipboard.argtypes = [wintypes.HWND]
user32.OpenClipboard.restype = wintypes.BOOL
user32.EmptyClipboard.restype = wintypes.BOOL
user32.CloseClipboard.restype = wintypes.BOOL
user32.SetClipboardData.argtypes = [wintypes.UINT, ctypes.c_void_p]
user32.SetClipboardData.restype = ctypes.c_void_p
user32.GetClipboardData.argtypes = [wintypes.UINT]
user32.GetClipboardData.restype = ctypes.c_void_p
user32.EnumClipboardFormats.argtypes = [wintypes.UINT]
user32.EnumClipboardFormats.restype = wintypes.UINT
user32.EnumWindows.argtypes = [ctypes.c_void_p, wintypes.LPARAM]
user32.EnumWindows.restype = wintypes.BOOL
user32.GetForegroundWindow.restype = wintypes.HWND
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsIconic.restype = wintypes.BOOL
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.SetCursorPos.restype = wintypes.BOOL

SW_RESTORE, SW_MINIMIZE, SW_SHOW = 9, 6, 5
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
DWMWA_CLOAKED = 14

VK_CONTROL, VK_V, VK_RETURN, VK_A, VK_ESCAPE, VK_DELETE = 0x11, 0x56, 0x0D, 0x41, 0x1B, 0x2E

# 对话正文后面会跟一堆界面文字,收尾时按这些标记剪掉
_TRAILING_NOISE = (
    "请选择一个产物查看详情",
    "引用来源 (",
    "点击或长按 ALT 语音输入",
    "允许完全访问",
    "内容由 AI 生成",
    "停止生成",
    "正在生成",
)

# 发送后这么久之内不认为"回答已结束"(agent 还在思考/调工具)
_MIN_SETTLE_SECONDS = 6.0


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_void_p)]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_void_p)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", _KEYBDINPUT), ("mi", _MOUSEINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


def _key(vk: int, up: bool = False) -> _INPUT:
    item = _INPUT()
    item.type = INPUT_KEYBOARD
    item.u.ki = _KEYBDINPUT(vk, 0, KEYEVENTF_KEYUP if up else 0, 0, None)
    return item


def _unicode(ch: str, up: bool = False) -> _INPUT:
    item = _INPUT()
    item.type = INPUT_KEYBOARD
    flags = KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if up else 0)
    item.u.ki = _KEYBDINPUT(0, ord(ch), flags, 0, None)
    return item


def send_inputs(items: list[_INPUT]) -> None:
    if not items:
        return
    array = (_INPUT * len(items))(*items)
    user32.SendInput(len(items), array, ctypes.sizeof(_INPUT))


def type_text(text: str, delay: float = 0.002) -> None:
    """用 SendInput 逐字打进去(支持中文)。"""
    for ch in text:
        if ch == "\n":
            send_inputs([_key(VK_RETURN), _key(VK_RETURN, True)])
        else:
            send_inputs([_unicode(ch), _unicode(ch, True)])
        if delay:
            time.sleep(delay)


def press(vk: int) -> None:
    send_inputs([_key(vk), _key(vk, True)])


def ctrl_key(vk: int) -> None:
    send_inputs([_key(VK_CONTROL), _key(vk), _key(vk, True), _key(VK_CONTROL, True)])


def click_at(x: int, y: int) -> None:
    """在物理坐标点一下(先移动再按下抬起)。"""
    user32.SetCursorPos(int(x), int(y))
    time.sleep(0.05)
    for flags in (0x0002, 0x0004):  # LEFTDOWN, LEFTUP
        item = _INPUT()
        item.type = 0  # MOUSE
        item.u.mi = _MOUSEINPUT(0, 0, 0, flags, 0, None)
        send_inputs([item])
        time.sleep(0.03)


class WorkBuddyWindowBackend:
    label = "WorkBuddy 窗口"

    def __init__(self, cfg: dict, system_prompt: str = "") -> None:
        self.cfg = cfg or {}
        self.system_prompt = system_prompt or ""

    # ---------- 窗口 ----------

    def find_window(self) -> int:
        """找到 WorkBuddy 主窗口。"""
        wanted = str(self.cfg.get("window_title") or "WorkBuddy").strip().lower()
        found: list[int] = []

        def callback(hwnd, _):
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 2)
            user32.GetWindowTextW(hwnd, buf, length + 2)
            cls = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, cls, 256)
            if cls.value == "Chrome_WidgetWin_1" and wanted in buf.value.strip().lower():
                found.append(int(hwnd))
            return True

        user32.EnumWindows(ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(callback), 0)
        if not found:
            raise BackendError(
                "没有找到 WorkBuddy 的窗口。\n\n"
                "请先打开 WorkBuddy 桌面端(可以最小化,但别退出),并确认它已经登录。"
            )
        return found[0]

    def _enable_accessibility(self) -> None:
        """告诉系统有 UIA 客户端在监听,Chromium 才会构建无障碍树。"""
        try:
            ctypes.WinDLL("UIAutomationCore.dll").UiaClientsAreListening()
        except Exception as exc:  # pragma: no cover
            log.debug("UiaClientsAreListening 失败: %s", exc)

    def _activate(self, hwnd: int) -> bool:
        was_minimized = bool(user32.IsIconic(hwnd))
        user32.ShowWindow(hwnd, SW_RESTORE if was_minimized else SW_SHOW)
        try:
            user32.AllowSetForegroundWindow(-1)  # ASFW_ANY
        except Exception:
            pass
        try:
            user32.SetForegroundWindow(hwnd)
        except Exception:
            pass
        time.sleep(0.4)
        if user32.GetForegroundWindow() != hwnd:
            # 前台锁:先轻轻敲一下 ALT 再试
            send_inputs([_key(0x12), _key(0x12, True)])
            try:
                user32.SetForegroundWindow(hwnd)
            except Exception:
                pass
            time.sleep(0.3)
        if user32.GetForegroundWindow() != hwnd:
            raise BackendError(
                "没能把 WorkBuddy 窗口切到前台(Windows 前台锁挡住了)。\n"
                "请手动点一下 WorkBuddy 窗口再重试。"
            )
        return was_minimized

    # ---------- 控件 ----------

    def find_controls(self, hwnd: int, timeout: float = 12.0):
        """定位输入框 / 发送按钮 / 对话内容区。"""
        import uiautomation as auto

        auto.SetGlobalSearchTimeout(2)
        self._enable_accessibility()

        deadline = time.time() + timeout
        last_error = ""
        while time.time() < deadline:
            root = auto.ControlFromHandle(hwnd)
            edit = send_button = document = None
            stack = [root]
            guard = 0
            while stack and guard < 4000:
                guard += 1
                ctl = stack.pop()
                try:
                    children = ctl.GetChildren()
                except Exception:
                    children = []
                stack.extend(children)
                try:
                    kind = ctl.ControlTypeName
                except Exception:
                    continue
                if kind == "EditControl" and edit is None:
                    edit = ctl
                elif kind == "ButtonControl" and send_button is None:
                    if (ctl.Name or "").strip() in ("发送", "Send"):
                        send_button = ctl
                elif kind == "DocumentControl" and document is None:
                    document = ctl

            if edit is not None and document is not None:
                return edit, send_button, document
            last_error = "控件还没就绪(edit=%s document=%s)" % (edit is not None, document is not None)
            time.sleep(1.0)

        raise BackendError("没能识别 WorkBuddy 的聊天界面:" + last_error)

    @staticmethod
    def _document_text(document) -> str:
        try:
            return document.GetTextPattern().DocumentRange.GetText(60000)
        except Exception as exc:
            log.debug("读取对话文本失败: %s", exc)
            return ""

    def _clear_input(self, edit) -> None:
        """清空输入框(优先用 ValuePattern,不行再 Ctrl+A 删)。"""
        try:
            pattern = edit.GetValuePattern()
            if (pattern.Value or "").strip():
                pattern.SetValue("")
                time.sleep(0.25)
                if not (self._draft_of(edit) or "").strip():
                    return
        except Exception:
            pass
        rect = edit.BoundingRectangle
        if rect.right > rect.left:
            click_at((rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2)
            time.sleep(0.2)
        ctrl_key(VK_A)
        time.sleep(0.15)
        press(VK_DELETE)
        time.sleep(0.3)

    @staticmethod
    def _search_key(prompt: str) -> str:
        """取提示词里最有辨识度的一小段,用来在界面文本里定位这条消息。"""
        first_line = prompt.strip().splitlines()[0] if prompt.strip() else ""
        return first_line.strip()[:40]

    def _message_in_document(self, document, prompt: str) -> bool:
        """这条消息是不是已经出现在对话里了(真正判断发送成功与否)。"""
        key = self._search_key(prompt)
        if not key:
            return False
        return key in self._document_text(document)

    def _do_send(self, edit, send_button, document, prompt: str) -> bool:
        """把消息发出去。判定标准是「对话里出现了这条消息」,不是输入框空不空。"""
        attempts = []

        if send_button is not None:
            attempts.append(("invoke", lambda: send_button.GetInvokePattern().Invoke()))
            rect = None
            try:
                rect = send_button.BoundingRectangle
            except Exception:
                rect = None
            if rect is not None and rect.right > rect.left and rect.bottom > rect.top:
                cx, cy = (rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2
                attempts.append(("click", lambda: click_at(cx, cy)))
        attempts.append(("enter", lambda: press(VK_RETURN)))

        for name, action in attempts:
            try:
                action()
            except Exception as exc:
                log.debug("发送方式 %s 失败: %s", name, exc)
                continue
            time.sleep(1.6)
            if self._message_in_document(document, prompt):
                log.info("发送成功(方式:%s)", name)
                return True
            if not (self._draft_of(edit) or "").strip():
                # 输入框空了但消息还没出现,再等一会儿
                time.sleep(1.5)
                if self._message_in_document(document, prompt):
                    log.info("发送成功(方式:%s)", name)
                    return True
            log.info("发送方式 %s 似乎没生效,换下一种", name)
        return False

    def _restore_draft(self, edit, draft: str) -> None:
        """把用户原来的草稿放回去。"""
        if not draft.strip():
            return
        try:
            self._clear_input(edit)
            rect = edit.BoundingRectangle
            if rect.right > rect.left:
                click_at((rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2)
                time.sleep(0.2)
            type_text(draft, delay=0.001)
            time.sleep(0.3)
            log.info("已把原来的草稿放回输入框")
        except Exception as exc:
            log.warning("还原草稿失败: %s", exc)

    @staticmethod
    def _draft_of(edit) -> str:
        try:
            return edit.GetValuePattern().Value or ""
        except Exception:
            return ""

    def _extract_answer(self, text: str, baseline: str, prompt: str) -> str:
        """从整窗口文本里切出回答正文。

        优先:找到提示词最后一次出现的位置,它后面的就是回答(新建会话、换页面都不影响)。
        兜底:和发送前的基线做差集。
        """
        key = self._search_key(prompt)
        if key:
            index = text.rfind(key)
            if index >= 0:
                return text[index + len(key):]
        if text.startswith(baseline):
            tail = text[len(baseline):]
            if tail.startswith(prompt):
                tail = tail[len(prompt):]
            return tail
        return ""

    def _clean(self, raw: str) -> str:
        """把界面文字剪掉,只留回答正文。"""
        text = raw
        for noise in _TRAILING_NOISE:
            index = text.rfind(noise)
            if index > 0:
                text = text[:index]
        return text.strip()

    # ---------- 剪贴板 ----------

    def _snapshot_clipboard(self) -> list[tuple[int, bytes]]:
        """备份剪贴板(尽力而为)。"""
        saved: list[tuple[int, bytes]] = []
        if not user32.OpenClipboard(None):
            return saved
        try:
            fmt = 0
            while True:
                fmt = user32.EnumClipboardFormats(fmt)
                if not fmt:
                    break
                if fmt in (2, 8, 17):  # CF_BITMAP / CF_DIB / CF_DIBV5 这类句柄型,跳过备份
                    continue
                handle = user32.GetClipboardData(fmt)
                if not handle:
                    continue
                ptr = kernel32.GlobalLock(handle)
                if not ptr:
                    continue
                try:
                    size = kernel32.GlobalSize(handle)
                    if 0 < size <= 4 * 1024 * 1024:
                        saved.append((int(fmt), ctypes.string_at(ptr, size)))
                finally:
                    kernel32.GlobalUnlock(handle)
        finally:
            user32.CloseClipboard()
        return saved

    def _restore_clipboard(self, saved: list[tuple[int, bytes]]) -> None:
        if not saved:
            return
        CF_DIB = 8
        text_formats = {1: "utf-16-le", 13: "utf-16-le", 7: "utf-16-le", 16: "latin-1"}
        if not user32.OpenClipboard(None):
            return
        try:
            user32.EmptyClipboard()
            for fmt, data in saved:
                if fmt == CF_DIB:
                    continue
                encoding = text_formats.get(fmt)
                if encoding is None:
                    continue
                if fmt in (1, 13, 7):
                    try:
                        payload = data.decode(encoding, errors="ignore").rstrip("\x00").encode(encoding)
                    except Exception:
                        continue
                else:
                    payload = data.rstrip(b"\x00")
                handle = kernel32.GlobalAlloc(0x0042, len(payload) + 2)
                if not handle:
                    continue
                ptr = kernel32.GlobalLock(handle)
                ctypes.memmove(ptr, payload, len(payload))
                ctypes.memset(ptr + len(payload), 0, 2)
                kernel32.GlobalUnlock(handle)
                user32.SetClipboardData(int(fmt), handle)
        finally:
            user32.CloseClipboard()

    def _set_clipboard_image(self, png: bytes) -> None:
        """把 PNG 作为 CF_DIB 放进剪贴板(Chromium 认得这个格式)。"""
        try:
            from PIL import Image
        except ImportError as exc:  # pragma: no cover
            raise BackendError("缺少 Pillow,无法把截图放进剪贴板。请先运行 install.bat。") from exc

        image = Image.open(io.BytesIO(png)).convert("RGB")
        buffer = io.BytesIO()
        image.save(buffer, "BMP")
        dib = buffer.getvalue()[14:]  # 去掉 14 字节 BMP 文件头,剩下的就是 CF_DIB

        if not user32.OpenClipboard(None):
            raise BackendError("无法打开剪贴板(可能被别的程序占用),请稍后重试。")
        try:
            user32.EmptyClipboard()
            GMEM_MOVEABLE = 0x0002
            handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(dib))
            if not handle:
                raise BackendError("剪贴板内存分配失败。")
            ptr = kernel32.GlobalLock(handle)
            ctypes.memmove(ptr, dib, len(dib))
            kernel32.GlobalUnlock(handle)
            if not user32.SetClipboardData(8, handle):  # 8 = CF_DIB
                raise BackendError("写入剪贴板失败。")
        finally:
            user32.CloseClipboard()

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

        prompt = user_text
        if self.system_prompt and not previous_answer:
            prompt = prompt + "\n" + self.system_prompt
        if previous_answer:
            prompt = prompt + "\n(上一轮的解答供参考:" + previous_answer[:1500] + ")"

        hwnd = self.find_window()
        log.info("WorkBuddy 窗口: hwnd=%s", hwnd)

        was_minimized = self._activate(hwnd)
        edit, send_button, document = self.find_controls(hwnd)

        draft = self._draft_of(edit)
        baseline = self._document_text(document)
        log.info("输入框原有内容 %d 字,对话基线 %d 字", len(draft), len(baseline))

        saved_clipboard = self._snapshot_clipboard()
        try:
            self._set_clipboard_image(image_png)

            # 点一下输入框确保焦点在它身上
            rect = edit.BoundingRectangle
            if rect.right > rect.left and rect.bottom > rect.top:
                click_at((rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2)
                time.sleep(0.3)

            # 先把输入框清空,别把用户没发出去的草稿一起发出去
            if draft.strip():
                self._clear_input(edit)
                time.sleep(0.2)

            ctrl_key(VK_V)          # 粘贴图片
            time.sleep(1.0)
            if prompt:
                type_text(prompt)
                time.sleep(0.4)

            sent = self._do_send(edit, send_button, document, prompt)
            if not sent:
                log.warning("三种发送方式都没让消息出现在对话里")
            if draft.strip():
                time.sleep(0.5)
                self._restore_draft(edit, draft)
            if not sent:
                raise BackendError(
                    "消息没能发出去。\n\n"
                    "常见原因:窗口没被切到前台,或者输入框不在聊天页。\n"
                    "请手动点一下 WorkBuddy 的聊天输入框后重试。"
                )

            # 轮询新出现的回答
            timeout = float(self.cfg.get("answer_timeout_seconds", 240) or 240)
            stable_ms = float(self.cfg.get("stable_ms", 3500) or 3500)
            min_settle = float(self.cfg.get("min_settle_seconds", _MIN_SETTLE_SECONDS) or 0)
            deadline = time.time() + timeout
            emitted = 0
            last_raw = ""
            stable_since = time.time()
            started = time.time()

            while time.time() < deadline:
                if should_cancel is not None and should_cancel():
                    break
                text = self._document_text(document)
                raw = self._extract_answer(text, baseline, prompt)
                cleaned = self._clean(raw)

                elapsed = time.time() - started
                if cleaned != last_raw:
                    last_raw = cleaned
                    stable_since = time.time()
                    if len(cleaned) > emitted:
                        yield cleaned[emitted:]
                        emitted = len(cleaned)
                elif (
                    emitted > 0
                    and elapsed >= min_settle
                    and (time.time() - stable_since) * 1000 >= stable_ms
                ):
                    log.info("回答已稳定(%d 字,用时 %.1fs)", emitted, elapsed)
                    break
                time.sleep(0.5)

            if emitted == 0:
                raise BackendError(
                    "没能从 WorkBuddy 窗口读到回答(等了 " + str(int(timeout)) + " 秒)。\n\n"
                    "请确认:1) WorkBuddy 已登录;2) 当前停在一个对话界面;"
                    "3) 刚才那条消息确实发出去了。"
                )
        finally:
            self._restore_clipboard(saved_clipboard)
            if was_minimized and bool(self.cfg.get("restore_minimized_after", True)):
                time.sleep(0.5)
                user32.ShowWindow(hwnd, SW_MINIMIZE)

    # ---------- 诊断 ----------

    def test(self) -> str:
        hwnd = self.find_window()
        was_minimized = self._activate(hwnd)
        try:
            edit, send_button, document = self.find_controls(hwnd)
        finally:
            if was_minimized:
                user32.ShowWindow(hwnd, SW_MINIMIZE)

        rect = edit.BoundingRectangle
        lines = [
            "找到 WorkBuddy 窗口,聊天界面识别成功。",
            "输入框:(%d,%d) %dx%d" % (rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top),
            "输入框里现有 %d 个字(发送前会自动避开你的草稿所在位置)" % len(self._draft_of(edit)),
            "发送按钮:" + (("找到「" + (send_button.Name or "") + "」") if send_button else "没找到,将用回车发送"),
            "对话内容区可读:%s" % ("是" if self._document_text(document) else "否"),
        ]
        return "\n".join(lines)