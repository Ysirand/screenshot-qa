"""冒烟测试:把整条链路跑一遍,不碰真实屏幕、不碰真实豆包接口。

    .venv\\Scripts\\python.exe tests\\smoke.py
"""

from __future__ import annotations

import os
import sys
import uuid
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from screen_qa import markdown  # noqa: E402
from screen_qa.api import ApiError, DoubaoClient  # noqa: E402
from screen_qa.hotkey import MOD_ALT, MOD_CONTROL, MOD_SHIFT, parse_hotkey  # noqa: E402
from tests.mock_ark import MockArk, _has_image  # noqa: E402

IS_WINDOWS = sys.platform == "win32"
IS_MACOS = sys.platform == "darwin"

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print("  [PASS] " + name)
    else:
        FAILED.append(name)
        print("  [FAIL] " + name + ("  -> " + detail if detail else ""))


def section(title: str) -> None:
    print("\n== " + title + " ==")


# ---------------------------------------------------------------- 纯逻辑

def test_markdown() -> None:
    section("markdown")
    html = markdown.to_html(
        "【答案】**B**\n\n- 甲\n- 乙\n\n1. 一\n2. 二\n\n```python\nx = 1\n```\n\n> 引用\n\n# 标题"
    )
    check("粗体", "<b>B</b>" in html)
    check("无序列表", "<ul>" in html and "<li>甲</li>" in html)
    check("有序列表", "<ol>" in html and "<li>一</li>" in html)
    check("代码块", "<pre>x = 1</pre>" in html, html)
    check("引用", "<blockquote>引用</blockquote>" in html)
    check("标题", "<h3>标题</h3>" in html)
    check("HTML 转义", "<script>" not in markdown.to_html("<script>bad</script>"))
    check("纯文本去标记", "**" not in markdown.plain_text("**B**"))


def test_hotkey_parse() -> None:
    section("快捷键解析")
    check("Alt+Q", parse_hotkey("Alt+Q") == (MOD_ALT, ord("Q")))
    check("Ctrl+Alt+S", parse_hotkey("Ctrl+Alt+S") == (MOD_CONTROL | MOD_ALT, ord("S")))
    check("Ctrl+Shift+F2", parse_hotkey("Ctrl+Shift+F2") == (MOD_CONTROL | MOD_SHIFT, 0x71))
    for spec in ("", "Q", "Alt+", "Alt+Q+W", "Alt+Foo"):
        try:
            parse_hotkey(spec)
            check("拒绝非法快捷键 " + repr(spec), False, "竟然通过了")
        except ValueError:
            check("拒绝非法快捷键 " + repr(spec), True)


def test_api(mock: MockArk) -> None:
    section("豆包 API 客户端")
    cfg = {
        "base_url": mock.base_url,
        "api_key": "test-key-123456",
        "model": "doubao-seed-1-6-250615",
        "temperature": 0.2,
        "max_tokens": 2048,
        "timeout": 30,
        "thinking": "disabled",
    }
    client = DoubaoClient(cfg, "系统提示", "追问提示")
    png = _fake_png()

    pieces = list(client.stream_answer(png, "请解答截图中的这道题。"))
    text = "".join(pieces)
    check("流式返回内容", "【答案】B" in text and "【解析】" in text, text[:80])
    check("忽略思维链", "我先读题" not in text)
    check("分片多于 1 个", len(pieces) > 5, str(len(pieces)))

    request = mock.requests[-1]
    check("带上 Authorization", request["headers"].get("Authorization") == "Bearer test-key-123456")
    check("模型名正确", request["payload"].get("model") == "doubao-seed-1-6-250615")
    check("真的把图片发出去了", _has_image(request["payload"]))
    check("关闭思考模式", request["payload"].get("thinking") == {"type": "disabled"})
    image_url = _image_url(request["payload"])
    check("图片是 PNG base64", image_url.startswith("data:image/png;base64,") and len(image_url) > 200)
    check("流式开关打开", request["payload"].get("stream") is True)

    # 追问:要带上上一轮内容
    list(client.stream_answer(png, "为什么不是 C?", previous_answer="【答案】B"))
    messages = mock.requests[-1]["payload"]["messages"]
    check("追问带上下文", any(m.get("role") == "assistant" for m in messages), str(messages)[:120])

    # 测试连接
    check("测试连接可用", "连接成功" in client.check())

    # 错误映射
    bad = DoubaoClient({**cfg, "api_key": ""}, "s", "f")
    try:
        list(bad.stream_answer(png, "x"))
        check("空 Key 报错", False, "没报错")
    except ApiError as exc:
        check("空 Key 报错", "API Key" in str(exc), str(exc))

    bad_key = DoubaoClient({**cfg, "api_key": "wrong-key-xx"}, "s", "f")
    try:
        list(bad_key.stream_answer(png, "x"))
        check("401 中文提示", False, "没报错")
    except ApiError as exc:
        check("401 中文提示", "API Key" in str(exc), str(exc))

    no_model = DoubaoClient({**cfg, "model": "unknown-model"}, "s", "f")
    try:
        list(no_model.stream_answer(png, "x"))
        check("404 中文提示", False, "没报错")
    except ApiError as exc:
        check("404 中文提示", "不存在" in str(exc), str(exc))


def test_cli_backend(out_dir) -> None:
    section("本地智能体后端(CLI)")
    import json as _json

    from screen_qa.cli_backend import CliBackend
    from screen_qa.backend import DEFAULT_BACKEND, build_backend
    from screen_qa.errors import BackendError

    fake = ROOT / "tests" / "fake_agent_cli.mjs"
    dump = out_dir / "cli_dump.json"
    if dump.exists():
        dump.unlink()

    cfg = {
        "command": str(fake),
        "tools": "Read",
        "timeout_seconds": 60,
        "env": {"FAKE_MODE": "ok", "FAKE_DUMP": str(dump)},
    }
    backend = CliBackend(cfg, "系统提示词")

    png = _fake_png()
    text = "".join(backend.stream_answer(png, "请解答截图中的这道题。"))
    check("CLI 流式拿到答案", "【答案】B" in text, text[:80])
    check("增量与整块消息没有重复", text.count("【答案】B") == 1, str(text.count("【答案】B")))
    check("CLI 路径可解析", str(fake) in " ".join(backend.resolve_argv()))

    check("CLI 记录了调用现场", dump.exists())
    if dump.exists():
        data = _json.loads(dump.read_text(encoding="utf-8"))
        argv = data.get("argv") or []
        check("走的是官方非交互参数", "-p" in argv and "stream-json" in argv)
        check("工具被限制为 Read", "--tools" in argv and argv[argv.index("--tools") + 1] == "Read")
        check("子进程工作在临时目录", "screenqa_cli_" in str(data.get("cwd", "")), str(data.get("cwd")))
        check("消息里带了图片", data.get("hasImage") is True)
        content = ((data.get("payload") or {}).get("message") or {}).get("content") or []
        image = next((part for part in content if part.get("type") == "image"), None)
        check("图片是 base64 PNG", bool(image) and image["source"]["media_type"] == "image/png")
        check("图片数据完整", bool(image) and len(image["source"]["data"]) > 200)
        text_part = next((part for part in content if part.get("type") == "text"), None)
        check("题目文本一起发出", bool(text_part) and "请解答截图中的这道题。" in text_part["text"])

    # 未登录
    backend.cfg["env"] = {"FAKE_MODE": "auth"}
    try:
        "".join(backend.stream_answer(png, "x"))
        check("未登录时给出中文提示", False, "没报错")
    except BackendError as exc:
        check("未登录时给出中文提示", "登录" in str(exc), str(exc)[:80])

    # 真实 CLI 那种 result(is_error) 形态
    backend.cfg["env"] = {"FAKE_MODE": "err"}
    try:
        "".join(backend.stream_answer(png, "x"))
        check("识别 result.is_error 形态", False, "没报错")
    except BackendError as exc:
        check("识别 result.is_error 形态", "登录" in str(exc), str(exc)[:60])

    # 没有任何输出
    backend.cfg["env"] = {"FAKE_MODE": "empty"}
    try:
        "".join(backend.stream_answer(png, "x"))
        check("空输出时报错", False, "没报错")
    except BackendError as exc:
        check("空输出时报错", "没有返回内容" in str(exc), str(exc)[:80])

    # 进程启动失败
    backend.cfg["env"] = {"FAKE_MODE": "fail"}
    try:
        "".join(backend.stream_answer(png, "x"))
        check("启动失败时报错", False, "没报错")
    except BackendError as exc:
        check("启动失败时报错", "启动失败" in str(exc), str(exc)[:80])

    # 纯文本输出的 CLI 也能兜住
    backend.cfg["env"] = {"FAKE_MODE": "text"}
    plain = "".join(backend.stream_answer(png, "x"))
    check("兼容纯文本输出的 CLI", "纯文本答案" in plain, plain[:60])

    # 工厂
    check("工厂默认后端", DEFAULT_BACKEND == "workbuddy_acp", DEFAULT_BACKEND)
    check("工厂能建 CLI 后端", build_backend({"backend": "cli", "cli": cfg}).__class__ is CliBackend)
    check("未知后端回退", build_backend({"backend": "???"}).__class__.__name__ == "OpenAiBackend")
    backend.cfg["env"] = {"FAKE_MODE": "ok"}
    check("纯文本测试连接可用", "本地智能体可用" in backend.test(), str(backend.cfg["env"]))

    leftovers = list((Path(tempfile.gettempdir())).glob("screenqa_cli_*"))
    check("临时目录已清理", not leftovers, str(leftovers[:3]))


def test_simple_backend(mock: MockArk) -> None:
    section("自定义 HTTP 后端")
    from screen_qa.simple_http_backend import SimpleHttpBackend
    from screen_qa.errors import BackendError

    png = _fake_png()

    # JSON 返回,显式指定取值路径
    backend = SimpleHttpBackend(
        {"url": mock.base_url.replace("/v1", "") + "/simple/json", "response_path": "data.answer"}
    )
    text = "".join(backend.stream_answer(png, "请解答"))
    check("JSON 接口按路径取值", "【答案】C" in text, text[:60])

    request = mock.requests[-1]
    check("默认字段名正确", "prompt" in request["payload"] and "image_base64" in request["payload"])
    check("图片确实是 base64", len(request["payload"]["image_base64"]) > 200)
    check("mock 确认收到图片", bool(request["payload"].get("image_base64")))

    # 自动猜字段
    backend = SimpleHttpBackend({"url": mock.base_url.replace("/v1", "") + "/simple/json"})
    check("不配路径也能猜出文本", "【答案】C" in "".join(backend.stream_answer(png, "x")))

    # SSE 流式
    backend = SimpleHttpBackend(
        {
            "url": mock.base_url.replace("/v1", "") + "/simple/sse",
            "response_path": "delta.text",
            "image_field": "image_data",
            "prompt_field": "q",
        }
    )
    pieces = list(backend.stream_answer(png, "x"))
    check("SSE 能逐段解析", len(pieces) > 1 and "【答案】C" in "".join(pieces), str(len(pieces)))
    check("自定义字段名生效", "q" in mock.requests[-1]["payload"] and "image_data" in mock.requests[-1]["payload"])

    # 纯文本返回
    backend = SimpleHttpBackend({"url": mock.base_url.replace("/v1", "") + "/simple/text"})
    check("纯文本返回也能用", "【答案】C" in "".join(backend.stream_answer(png, "x")))

    # 没填地址
    try:
        "".join(SimpleHttpBackend({"url": ""}).stream_answer(png, "x"))
        check("没填地址时报错", False, "没报错")
    except BackendError as exc:
        check("没填地址时报错", "接口地址" in str(exc))


def _image_url(payload: dict) -> str:
    for message in payload.get("messages") or []:
        content = message.get("content")
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "image_url":
                    return (part.get("image_url") or {}).get("url") or ""
    return ""


def _fake_png(width: int = 420, height: int = 240) -> bytes:
    """造一张"题目截图"(只用 QImage,不需要 QApplication)。"""
    from PySide6.QtCore import QBuffer, QIODevice
    from PySide6.QtGui import QColor, QImage, QPainter

    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(QColor(245, 246, 250))
    painter = QPainter(image)
    painter.setPen(QColor(30, 30, 30))
    painter.drawText(24, 60, "1. 一辆汽车 2 小时行驶 72 千米,平均速度是多少?")
    painter.drawText(24, 100, "A. 72 km/h    B. 36 km/h    C. 18 km/h    D. 20 km/h")
    painter.end()

    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(buffer.data())


# ---------------------------------------------------------------- Qt 部分

def test_acp_helpers() -> None:
    section("WorkBuddy ACP 后端(纯逻辑)")
    from screen_qa.backend import build_backend
    from screen_qa.workbuddy_acp_backend import (
        WorkBuddyAcpBackend,
        parse_sse_line,
        shrink_image,
        text_from_event,
    )

    check("SSE 正常解析", parse_sse_line('data: {"jsonrpc":"2.0","id":1}') == {"jsonrpc": "2.0", "id": 1})
    check("忽略非 data 行", parse_sse_line(":ok") is None)
    check("忽略坏 JSON", parse_sse_line("data: {bad") is None)
    check("忽略 [DONE]", parse_sse_line("data: [DONE]") is None)

    good = {"jsonrpc": "2.0", "params": {"sessionId": "A", "update": {
        "sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "答案"}}}}
    other = {"jsonrpc": "2.0", "params": {"sessionId": "B", "update": {
        "sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "别人的会话"}}}}

    check("取到本会话的正文", text_from_event(good, "A") == "答案")
    # 这是最关键的一条:ACP 会把其它会话的通知也推进来,必须过滤掉
    check("过滤掉其它会话的通知", text_from_event(other, "A") is None)
    check("事件类型不符时返回 None", text_from_event(
        {"params": {"sessionId": "A", "update": {"sessionUpdate": "tool_call"}}}, "A") is None)

    small = _fake_png(120, 80)
    check("小图不做缩放", shrink_image(small) == small)

    backend = build_backend({"backend": "workbuddy_acp", "workbuddy_acp": {}})
    check("工厂产出 ACP 后端", isinstance(backend, WorkBuddyAcpBackend))
    check("ACP 后端有中文标签", backend.label == "WorkBuddy(ACP)")

    from screen_qa.workbuddy_acp_backend import workbuddy_running

    check("能判断 WorkBuddy 是否在运行", isinstance(workbuddy_running(), bool))


def test_collection_layer() -> None:
    section("收藏夹(本地存储)")
    import tempfile

    from screen_qa.collection import DEFAULT_FOLDER, FOLDER_ALL, Collection

    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "collection.db"
        c = Collection(db)

        check("初始没有题目", c.counts()[FOLDER_ALL] == 0)
        c.add_folder("数学")
        c.add_folder("数学")
        check("分类去重", c.folders() == ["数学"])

        first = c.add_item("1 + 1 = ?", "【答案】2", folder="数学", note="送分", image_png=b"\x89PNG")
        c.add_item("水的沸点?", "【答案】100℃")
        check("写入两题", c.counts()[FOLDER_ALL] == 2, c.counts())
        check("分类计数正确", c.counts().get("数学") == 1, c.counts())
        check("截图落盘", c.image_path(first) is not None)
        # 回归:传了临时库路径时,图片必须写在临时目录里,
        # 不能碰到真实的 collection/images/(之前就是这么误删用户截图的)
        check("截图写在临时目录里", str(c.image_path(first)).startswith(str(Path(tmp))),
              str(c.image_path(first)))

        check("按分类筛选", len(c.items(folder="数学")) == 1)
        check("题干搜索", len(c.items(keyword="沸点")) == 1)
        check("批注搜索", len(c.items(keyword="送分")) == 1)

        c.update_item(first, note="改了", folder="物理")
        check("改批注和分类", c.get_item(first)["note"] == "改了")
        check("新分类自动出现", "物理" in c.folders())

        c.delete_folder("物理")
        check("删分类后退回未分类", c.get_item(first)["folder"] == DEFAULT_FOLDER)

        c.delete_item(first)
        check("删除题目", c.counts()[FOLDER_ALL] == 1)
        check("截图一起删掉", c.image_path(first) is None)

        reopened = Collection(db)
        check("重开仍在", reopened.counts()[FOLDER_ALL] == 1)


def test_single_instance_control(qapp) -> None:
    section("单实例控制端口(再点一次启动 = 打开收藏夹)")
    from PySide6.QtNetwork import QLocalSocket

    import screen_qa.app as app_module
    import screen_qa.collection as collection_module

    original = collection_module.collection_dir
    with tempfile.TemporaryDirectory() as tmp:
        collection_module.collection_dir = lambda: Path(tmp)
        # 用独立端口名:前面的用例可能还留着一个实例占着默认名字
        unique = "screenqa-test-" + uuid.uuid4().hex[:8]
        app_module.CONTROL_SERVER = unique
        try:
            controller = app_module.ScreenQaApp(qapp)
            check("控制端口在监听", bool(controller._server.isListening()), unique)

            opened: list = []
            controller.open_collection = lambda: opened.append(1)

            for label, payload in (("collection", b"collection"), ("不带参数", b"collection")):
                socket = QLocalSocket()
                socket.connectToServer(unique)
                check(label + ":能连上已有实例", socket.waitForConnected(1500))
                socket.write(payload)
                socket.flush()
                socket.waitForBytesWritten(800)
                for _ in range(40):
                    qapp.processEvents()
                    time.sleep(0.02)
                socket.disconnectFromServer()

            check("已有实例收到指令并打开收藏夹", len(opened) >= 2, str(len(opened)))
            controller.quit()
        finally:
            collection_module.collection_dir = original


def test_floating_button(qapp) -> None:
    section("桌面悬浮按钮")
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtGui import QMouseEvent

    from screen_qa.floating_button import DRAG_THRESHOLD, FloatingButton

    button = FloatingButton({"floating_button": {"size": 40, "trigger": "single", "snap_edges": False}})
    fired: list = []
    button.capture_requested.connect(lambda: fired.append(1))
    moved: list = []
    button.position_changed.connect(lambda x, y: moved.append((x, y)))

    def event(kind, x, y):
        types = {
            "press": QMouseEvent.Type.MouseButtonPress,
            "move": QMouseEvent.Type.MouseMove,
            "release": QMouseEvent.Type.MouseButtonRelease,
        }
        buttons = Qt.MouseButton.LeftButton if kind != "release" else Qt.MouseButton.NoButton
        return QMouseEvent(types[kind], QPoint(x, y), QPoint(x, y),
                           Qt.MouseButton.LeftButton, buttons, Qt.KeyboardModifier.NoModifier)

    start = button.pos()
    # 原地点一下 -> 应该触发截图
    button.mousePressEvent(event("press", 20, 20))
    button.mouseReleaseEvent(event("release", 20, 20))
    check("单击触发截图", len(fired) == 1, str(fired))

    # 拖动一下 -> 不应该触发截图,而且应该移动了
    button.mousePressEvent(event("press", 20, 20))
    button.mouseMoveEvent(event("move", 20 + DRAG_THRESHOLD + 20, 20))
    button.mouseReleaseEvent(event("release", 20 + DRAG_THRESHOLD + 20, 20))
    check("拖动不会误触发截图(防误触)", len(fired) == 1, str(fired))
    check("拖动确实移动了窗口", button.pos() != start, "%s -> %s" % (start, button.pos()))
    check("拖动后上报新位置", bool(moved))

    # 双击模式:第一次不触发,第二次才触发
    button.apply_config({"floating_button": {"size": 40, "trigger": "double"}})
    fired.clear()
    button.mousePressEvent(event("press", 20, 20))
    button.mouseReleaseEvent(event("release", 20, 20))
    check("双击模式下第一次点击不触发", not fired, str(fired))
    button.mousePressEvent(event("press", 20, 20))
    button.mouseReleaseEvent(event("release", 20, 20))
    check("双击模式下第二次点击触发", len(fired) == 1, str(fired))
    button.deleteLater()


def test_macos_support() -> None:
    section("macOS 支持(纯逻辑,任何平台都能测)")
    from screen_qa import plat
    from screen_qa.autostart import _plist_text
    from screen_qa.hotkey_mac import to_pynput

    for spec, want in (
        ("Alt+Q", "<alt>+q"),
        ("Cmd+Shift+A", "<cmd>+<shift>+a"),
        ("Ctrl+Alt+S", "<ctrl>+<alt>+s"),
        ("Cmd+F2", "<cmd>+<f2>"),
        ("Alt+Space", "<alt>+<space>"),
        ("Cmd+Up", "<cmd>+<up>"),
    ):
        got = to_pynput(spec)
        check("快捷键转换 " + spec, got == want, got)

    for bad in ("", "Q", "Alt+", "Alt+Q+W", "Alt+Foo"):
        try:
            to_pynput(bad)
            check("拒绝非法快捷键 " + repr(bad), False, "竟然通过了")
        except ValueError:
            check("拒绝非法快捷键 " + repr(bad), True)

    plist = _plist_text()
    check("LaunchAgent 是合法 plist 头", plist.startswith("<?xml") and plist.strip().endswith("</plist>"))
    check("LaunchAgent 设了 RunAtLoad", "<key>RunAtLoad</key>" in plist and "<true/>" in plist)
    check("LaunchAgent 带上了启动命令", "main.py" in plist or ".app" in plist)

    cmd = plat.launch_command()
    check("launch_command 非空", isinstance(cmd, list) and len(cmd) >= 1, str(cmd))
    check("平台标志互斥", not (plat.IS_WINDOWS and plat.IS_MACOS))
    check("非 macOS 时屏幕权限视为已授权", plat.IS_MACOS or plat.macos_has_screen_permission())
    check("get_window_rect 在非 Windows 上返回 None",
          plat.IS_WINDOWS or __import__("screen_qa.winutil", fromlist=["x"]).get_window_rect(0) is None)


def test_ui(qapp) -> None:
    from PySide6.QtCore import QPoint, QRect, Qt
    from PySide6.QtGui import QColor, QGuiApplication, QImage, QMouseEvent, QPainter, QPixmap

    from screen_qa.overlay import ScreenOverlay

    out = Path(__file__).resolve().parent / "_output"
    out.mkdir(parents=True, exist_ok=True)

    # ---- 截图遮罩:裁剪数学 ----
    section("截图遮罩")
    screen = QGuiApplication.primaryScreen()
    synthetic = QPixmap(800, 500)
    synthetic.fill(QColor(12, 120, 200))
    painter = QPainter(synthetic)
    painter.setPen(QColor(255, 255, 255))
    painter.drawText(20, 40, "SYNTHETIC 800x500")
    painter.end()

    overlay = ScreenOverlay(screen, synthetic, show_highlight=True)
    overlay.resize(900, 600)  # 不改屏幕,只测逻辑
    picks: list = []
    overlay.picked.connect(lambda pm, rect: picks.append((pm, rect)))

    def send(kind, x, y, button=Qt.MouseButton.LeftButton):
        if kind == "press":
            event = QMouseEvent(
                QMouseEvent.Type.MouseButtonPress, QPoint(x, y), QPoint(x, y),
                button, button, Qt.KeyboardModifier.NoModifier,
            )
        elif kind == "move":
            event = QMouseEvent(
                QMouseEvent.Type.MouseMove, QPoint(x, y), QPoint(x, y),
                Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
            )
        else:
            event = QMouseEvent(
                QMouseEvent.Type.MouseButtonRelease, QPoint(x, y), QPoint(x, y),
                button, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
            )
        return event

    overlay.mousePressEvent(send("press", 100, 100))
    overlay.mouseMoveEvent(send("move", 300, 250))
    overlay.mouseReleaseEvent(send("release", 300, 250))
    qapp.processEvents()

    check("拖动后产生选区", len(picks) == 1, str(len(picks)))
    if picks:
        cropped, rect = picks[0]
        scale_x = 800 / 900
        scale_y = 500 / 600
        expect_w = round(200 * scale_x)
        expect_h = round(150 * scale_y)
        # QRect(两个角点) 是闭区间,所以 100->300 正好是 201 像素宽
        check("本地矩形正确", rect == QRect(100, 100, 201, 151), str(rect))
        check(
            "裁剪尺寸按缩放比换算",
            abs(cropped.width() - expect_w) <= 3 and abs(cropped.height() - expect_h) <= 3,
            "{0}x{1} vs {2}x{3}".format(cropped.width(), cropped.height(), expect_w, expect_h),
        )

    # 视觉检查用渲染(不显示窗口)
    overlay2 = ScreenOverlay(screen, synthetic, show_highlight=False)
    overlay2.resize(760, 460)
    overlay2._dragging = True
    overlay2._start = QPoint(120, 90)
    overlay2._current = QRect(QPoint(120, 90), QPoint(600, 360)).normalized()
    canvas = QImage(760, 460, QImage.Format.Format_ARGB32)
    canvas.fill(QColor(0, 0, 0, 255))
    painter = QPainter(canvas)
    overlay2.render(painter, QPoint(0, 0))
    painter.end()
    canvas.save(str(out / "overlay_render.png"))
    check("遮罩可渲染出图(拖动态)", (out / "overlay_render.png").exists())

    overlay2._dragging = False
    overlay2._hover = QRect(300, 140, 380, 240)
    canvas2 = QImage(760, 460, QImage.Format.Format_ARGB32)
    canvas2.fill(QColor(0, 0, 0, 255))
    painter = QPainter(canvas2)
    overlay2.render(painter, QPoint(0, 0))
    painter.end()
    canvas2.save(str(out / "overlay_hover.png"))
    check("遮罩可渲染出图(悬停态)", (out / "overlay_hover.png").exists())

    # ---- 全局热键:走真实的 WM_HOTKEY 通道(Windows 专属)----
    section("全局热键(真实 Win32 通道)")
    from screen_qa.app import ScreenQaApp
    from tests.mock_ark import MockArk as _MockArk
    if not IS_WINDOWS:
        print("  [SKIP] macOS/Linux 上没有 WM_HOTKEY,跳过(热键实现换成了 pynput)")

    with _MockArk() as mock:
        controller = ScreenQaApp(qapp)
        controller.cfg["backend"] = "openai"
        controller.cfg["api"]["base_url"] = mock.base_url
        controller.cfg["api"]["api_key"] = "test-key-123456"
        controller.cfg["api"]["model"] = "doubao-seed-1-6-250615"
        controller.cfg["behavior"]["save_history"] = False
        from screen_qa.backend import build_backend

        controller.backend = build_backend(controller.cfg)

        if controller.hotkey.registered:
            check("热键已注册", controller.hotkey.spec == "Alt+Q")
        else:
            # 允许本机已经有实例占着这个键(比如用户自己开着),此时必须给出明确提示
            check(
                "热键不可用时优雅降级(给出提示)",
                bool(controller._hotkey_error),
                controller._hotkey_error,
            )

        if IS_WINDOWS:
            import ctypes

            from screen_qa.hotkey import WM_HOTKEY, user32

            fired: list = []
            controller.hotkey.activated.disconnect(controller.start_capture)
            controller.hotkey.activated.connect(lambda: fired.append(1))
            user32.PostThreadMessageW(
                ctypes.windll.kernel32.GetCurrentThreadId(), WM_HOTKEY, 0xB001, 0
            )
            deadline = time.time() + 3
            while time.time() < deadline and not fired:
                qapp.processEvents()
                time.sleep(0.02)
            check("WM_HOTKEY 能触发回调", bool(fired))
            controller.hotkey.activated.connect(controller.start_capture)

        # ---- 端到端:截到图 -> 发给(假)豆包 -> 悬浮窗显示 ----
        section("端到端")
        controller.overlay.start = lambda show_highlight=True: controller.on_region_selected(
            QPixmap.fromImage(QImage(str(out / "question.png"))), QRect(300, 300, 420, 240)
        ) if (out / "question.png").exists() else None

        question = QPixmap.fromImage(QImage.fromData(_fake_png(), "PNG"))
        question.save(str(out / "question.png"))

        controller.on_region_selected(question, QRect(300, 300, 420, 240))
        check("窗口进入忙碌态", controller.window.busy)
        check("窗口已显示", controller.window.isVisible())

        ok = _wait(qapp, lambda: not controller.window.busy, timeout=20)
        check("等待回复结束", ok)
        check("答案已渲染到窗口", "【答案】B" in controller.window.answer_text, controller.window.answer_text[:60])
        check("窗口标题为完成态", "完成" in controller.window._status.text(), controller.window._status.text())
        check("复制按钮可用", controller.window._copy_button.isEnabled())
        check("追问输入已启用", controller.window._followup.isEnabled())

        _settle(qapp)
        window_shot = controller.window.grab()
        window_shot.save(str(out / "answer_window.png"))
        check("答案窗可截图", (out / "answer_window.png").exists())

        # 追问
        controller.ask_followup("为什么不是 C?")
        ok = _wait(qapp, lambda: not controller.window.busy, timeout=20)
        check("追问也能拿到回复", ok and "【答案】B" in controller.window.answer_text)
        follow_messages = mock.requests[-1]["payload"]["messages"]
        check("追问请求带上了历史", any(m.get("role") == "assistant" for m in follow_messages))

        # 历史落盘
        section("历史记录")
        with tempfile.TemporaryDirectory() as tmp:
            import screen_qa.app as app_module

            original = app_module.history_dir
            app_module.history_dir = lambda: Path(tmp)
            controller.cfg["behavior"]["save_history"] = True
            controller._last_answer = "【答案】B"
            controller._save_history()
            app_module.history_dir = original
            files = list(Path(tmp).rglob("*"))
            check("写入了截图和解答", any(f.suffix == ".png" for f in files) and any(f.suffix == ".md" for f in files))

        # 真的把遮罩显示一次,验证生命周期与坐标换算(只亮一瞬)
        section("遮罩显示与坐标换算")
        from screen_qa.overlay import SelectionOverlay as _Overlay

        overlay = _Overlay()
        overlay.start(show_highlight=False)
        qapp.processEvents()
        active = overlay.active
        check("遮罩可以打开", active)

        if active:
            first = overlay._overlays[0]
            physics = first.physical_rect()
            if IS_WINDOWS:
                check("拿到遮罩的物理矩形", physics is not None, str(physics))
            else:
                check("macOS 上物理矩形返回 None(优雅降级)", physics is None, str(physics))
            if physics:
                left, top, width, height = physics
                top_left = first._global_phys_to_local(left, top)
                bottom_right = first._global_phys_to_local(left + width, top + height)
                check(
                    "物理坐标 -> 本地坐标换算正确",
                    top_left == QPoint(0, 0)
                    and bottom_right == QPoint(first.width(), first.height()),
                    "{0} / {1}".format(top_left, bottom_right),
                )
        overlay.close_all()
        check("遮罩可以关闭", not overlay.active)

        # 窗口拾取:结果要么没有,要么是个真实矩形且包含该点
        from screen_qa import winutil

        found = winutil.window_under_point(200, 200)
        if found is None:
            check("光标下窗口拾取(该点无窗口)", True)
        else:
            hwnd, (left, top, width, height) = found
            inside = left <= 200 <= left + width and top <= 200 <= top + height
            check("光标下窗口拾取矩形合理", width > 8 and height > 8 and inside, str(found))

        controller.window.hide()
        controller.quit()
        check("退出后热键已注销", not controller.hotkey.registered)


def _settle(qapp, rounds: int = 6) -> None:
    """把事件循环多跑几轮,让布局/绘制都刷新到位。"""
    for _ in range(rounds):
        qapp.processEvents()
        time.sleep(0.03)


def _wait(qapp, condition, timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        qapp.processEvents()
        if condition():
            return True
        time.sleep(0.02)
    qapp.processEvents()
    return condition()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    from PySide6.QtWidgets import QApplication

    qapp = QApplication.instance() or QApplication(sys.argv)

    print("截图自动搜题 - 冒烟测试")
    print("平台:", sys.platform, "(Windows 专属的用例会自动跳过)" if not IS_WINDOWS else "")
    test_markdown()
    test_hotkey_parse()

    with MockArk() as mock:
        test_api(mock)
        test_simple_backend(mock)

    out_dir = Path(__file__).resolve().parent / "_output"
    out_dir.mkdir(parents=True, exist_ok=True)
    test_cli_backend(out_dir)
    test_acp_helpers()
    test_collection_layer()
    test_macos_support()

    test_ui(qapp)
    test_floating_button(qapp)
    test_single_instance_control(qapp)

    print("\n" + "=" * 46)
    print("通过 {0} 项,失败 {1} 项".format(len(PASSED), len(FAILED)))
    for name in FAILED:
        print("  FAILED: " + name)
    return 1 if FAILED else 0


if __name__ == "__main__":
    os.chdir(ROOT)
    raise SystemExit(main())