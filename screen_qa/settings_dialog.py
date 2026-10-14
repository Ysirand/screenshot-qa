"""设置窗口:选回答来源(官方 API / 本地智能体 / 自定义接口)、快捷键、外观。"""

from __future__ import annotations

import copy
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSlider,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from . import autostart
from .backend import BACKEND_CHOICES, build_backend
from .cli_backend import find_cli
from .config import get, set_value
from .errors import BackendError
from .hotkey import parse_hotkey
from .logging_setup import log

_API_HELP = (
    '<div style="color:#8A93A5;font-size:12px;line-height:1.6">'
    "在 <a href='https://console.volcengine.com/ark'>火山方舟控制台</a> 建一个 API Key,"
    "并在「开通管理」里开通支持图片的豆包模型(如 <code>doubao-seed-1-6-250615</code>)。<br>"
    "Base URL 不用改,除非你要换成别的 OpenAI 兼容服务(通义千问 VL、智谱 GLM-4V 等)。"
    "</div>"
)

_CLI_HELP = (
    '<div style="color:#8A93A5;font-size:12px;line-height:1.6">'
    "用本机的智能体(WorkBuddy / CodeBuddy)在后台答题,不需要 API Key。<br>"
    '<span style="color:#FFB020">实测结论:这个 WorkBuddy 版本的凭据是加密存储的,'
    "解密密钥只有桌面端启动它时才会注入,所以单独运行无法登录。</span><br>"
    "要走这条路,请把 AUTH TOKEN 填到这里(接口协议已经全部就绪)。<br>"
    "如果你装的是完整版 CodeBuddy Code,可以点「登录 / 诊断」,"
    "等它出现自己的界面后输入 <code>/login</code>(看到 <code>&gt;&gt;&gt;</code> 说明进了 Python,窗口不对)。"
    "</div>"
)

_WORKBUDDY_HELP = (
    '<div style="color:#8A93A5;font-size:12px;line-height:1.6">'
    "直接调用你本机正在运行的 <b>WorkBuddy 桌面端</b>答题:<br>"
    "不需要 API Key,不操作窗口、不抢焦点、不动剪贴板,图片原样发过去。<br>"
    "每次答题都会<b>新建一个独立会话</b>,不会污染你其它的对话和项目。<br>"
    "前提:WorkBuddy 开着并已登录(最小化没关系,别退出)。</div>"
)

_SIMPLE_HELP = (
    '<div style="color:#8A93A5;font-size:12px;line-height:1.6">'
    "把你自己的本地平台接口填进来。程序会 POST 一个 JSON:<br>"
    "<code>{ prompt, image_base64, image_mime }</code>,再把返回里的文本显示到桌面窗口。<br>"
    "支持普通 JSON 和 SSE 流式两种返回。</div>"
)


class _CheckWorker(QThread):
    """在后台跑"测试连接",别卡住界面。"""

    succeeded = Signal(str)
    failed = Signal(str)

    def __init__(self, cfg: dict) -> None:
        super().__init__()
        self._cfg = cfg

    def run(self) -> None:  # noqa: D102
        try:
            backend = build_backend(self._cfg)
            message = backend.test()
        except BackendError as exc:
            self.failed.emit(str(exc))
            return
        except Exception as exc:  # pragma: no cover
            log.exception("测试连接失败")
            self.failed.emit("测试失败:" + str(exc))
            return
        self.succeeded.emit(message)


class SettingsDialog(QDialog):
    """编辑并返回一份新的配置。"""

    def __init__(self, cfg: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.cfg = copy.deepcopy(cfg)
        self._worker: _CheckWorker | None = None

        self.setWindowTitle("设置 - 截图自动搜题")
        self.setMinimumWidth(640)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)

        root = QVBoxLayout(self)
        root.setSpacing(11)

        tabs = QTabWidget(self)
        tabs.addTab(self._build_backend_group(), "回答来源")
        tabs.addTab(self._build_ui_group(), "快捷键与外观")
        tabs.addTab(self._build_misc_group(), "其他")
        root.addWidget(tabs)
        self.tabs = tabs

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            Qt.Orientation.Horizontal,
            self,
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self._load_values()

    # ---------- 回答来源 ----------

    def _build_backend_group(self) -> QWidget:
        box = QWidget(self)
        layout = QVBoxLayout(box)
        layout.setSpacing(9)

        top = QWidget(box)
        top_row = QHBoxLayout(top)
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(8)

        self.backend_box = QComboBox(top)
        for key, label in BACKEND_CHOICES:
            self.backend_box.addItem(label, key)
        self.backend_box.currentIndexChanged.connect(self._on_backend_changed)

        top_row.addWidget(QLabel("使用", top))
        top_row.addWidget(self.backend_box, 1)
        layout.addWidget(top)

        # 独立一行,别和下拉框挤在一起
        self.detect_label = QLabel("", box)
        self.detect_label.setWordWrap(True)
        layout.addWidget(self.detect_label)

        # 三个后端各自的配置区
        self.pages = QWidget(box)
        self.pages_layout = QVBoxLayout(self.pages)
        self.pages_layout.setContentsMargins(0, 0, 0, 0)
        self.pages_layout.setSpacing(9)

        self.page_wb = self._build_workbuddy_page()
        self.page_api = self._build_api_page()
        self.page_cli = self._build_cli_page()
        self.page_simple = self._build_simple_page()
        for page in (self.page_wb, self.page_api, self.page_cli, self.page_simple):
            page.setVisible(False)
            self.pages_layout.addWidget(page)
        layout.addWidget(self.pages)

        # 测试连接
        test_row = QWidget(box)
        test_layout = QHBoxLayout(test_row)
        test_layout.setContentsMargins(0, 0, 0, 0)
        test_layout.setSpacing(8)
        self.test_button = QPushButton("测试连接", test_row)
        self.test_button.clicked.connect(self._test_connection)
        self.test_result = QLabel("", test_row)
        self.test_result.setWordWrap(True)
        test_layout.addWidget(self.test_button)
        test_layout.addWidget(self.test_result, 1)
        layout.addWidget(test_row)
        return box

    def _build_workbuddy_page(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        form.setContentsMargins(0, 0, 0, 0)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setSpacing(8)

        self.wb_reuse = QCheckBox("复用同一个会话(追问能记得上下文)", page)
        form.addRow("", self.wb_reuse)

        self.wb_cwd = QLineEdit(page)
        self.wb_cwd.setPlaceholderText("留空 = 用临时目录(不会挂到你的项目下)")
        form.addRow("会话目录", self.wb_cwd)

        self.wb_timeout = QSpinBox(page)
        self.wb_timeout.setRange(30, 1800)
        self.wb_timeout.setSuffix(" 秒")
        form.addRow("超时时间", self.wb_timeout)

        self.wb_thought = QComboBox(page)
        for value, label in (
            ("", "跟随 WorkBuddy(不修改)"),
            ("low", "低 —— 更快、更省额度(实测速度差异在噪声内)"),
            ("enabled", "默认 —— 用模型自带的思考强度"),
            ("high", "高"),
            ("max", "最高"),
        ):
            self.wb_thought.addItem(label, value)
        form.addRow("思考强度", self.wb_thought)

        self.wb_model = QComboBox(page)
        for value, label in (
            ("", "跟随 WorkBuddy 里选的模型"),
            ("fast-model", "快速(x0.21)"),
            ("balanced-model", "均衡(x0.65)"),
            ("deep-model", "极致(x1.20)"),
            ("hy4-preview", "Hy4 preview(x0.29)"),
            ("deepseek-v4.1-flash", "Deepseek-V4.1-Flash(x0.11)"),
            ("glm-5.3-flash", "GLM-5.3-Flash(x0.06)"),
            ("hy3", "Hy3(免费)"),
        ):
            self.wb_model.addItem(label, value)
        form.addRow("模型", self.wb_model)

        self.wb_warm = QCheckBox("启动时预热会话(省掉第一次的冷启动等待)", page)
        form.addRow("", self.wb_warm)

        help_label = QLabel(_WORKBUDDY_HELP, page)
        help_label.setWordWrap(True)
        form.addRow("", help_label)
        return page

    def _build_api_page(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        form.setContentsMargins(0, 0, 0, 0)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setSpacing(8)

        self.base_url = QLineEdit(page)
        self.base_url.setPlaceholderText("https://ark.cn-beijing.volces.com/api/v3")
        form.addRow("Base URL", self.base_url)

        key_row = QWidget(page)
        key_layout = QHBoxLayout(key_row)
        key_layout.setContentsMargins(0, 0, 0, 0)
        key_layout.setSpacing(6)
        self.api_key = QLineEdit(key_row)
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key.setPlaceholderText("粘贴火山方舟的 API Key")
        self.show_key = QCheckBox("显示", key_row)
        self.show_key.toggled.connect(self._toggle_key_echo)
        key_layout.addWidget(self.api_key, 1)
        key_layout.addWidget(self.show_key)
        form.addRow("API Key", key_row)

        self.model = QLineEdit(page)
        self.model.setPlaceholderText("doubao-seed-1-6-250615")
        form.addRow("模型 ID", self.model)

        help_label = QLabel(_API_HELP, page)
        help_label.setWordWrap(True)
        help_label.setOpenExternalLinks(True)
        help_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        form.addRow("", help_label)
        return page

    def _build_cli_page(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        form.setContentsMargins(0, 0, 0, 0)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setSpacing(8)

        cmd_row = QWidget(page)
        cmd_layout = QHBoxLayout(cmd_row)
        cmd_layout.setContentsMargins(0, 0, 0, 0)
        cmd_layout.setSpacing(6)
        self.cli_command = QLineEdit(cmd_row)
        self.cli_command.setPlaceholderText("留空自动探测(WorkBuddy 自带)")
        detect = QPushButton("自动探测", cmd_row)
        detect.clicked.connect(self._detect_cli)
        login = QPushButton("登录 / 诊断", cmd_row)
        login.setToolTip("试试能不能登录,并给出诊断结论")
        login.clicked.connect(self._open_cli_login)
        cmd_layout.addWidget(self.cli_command, 1)
        cmd_layout.addWidget(detect)
        cmd_layout.addWidget(login)
        self.cli_command.textChanged.connect(self._refresh_detect_label)
        form.addRow("CLI 路径", cmd_row)

        self.cli_tools = QLineEdit(page)
        self.cli_tools.setPlaceholderText('Read(默认,只给读权限);留空表示禁用全部工具')
        form.addRow("允许的工具", self.cli_tools)

        self.cli_timeout = QSpinBox(page)
        self.cli_timeout.setRange(30, 1800)
        self.cli_timeout.setSuffix(" 秒")
        form.addRow("超时时间", self.cli_timeout)

        self.cli_token = QLineEdit(page)
        self.cli_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.cli_token.setPlaceholderText("可留空(留空就用登录窗口登录的凭据)")
        form.addRow("AUTH TOKEN", self.cli_token)

        self.cli_base_url = QLineEdit(page)
        self.cli_base_url.setPlaceholderText("可留空")
        form.addRow("BASE URL", self.cli_base_url)

        help_label = QLabel(_CLI_HELP, page)
        help_label.setWordWrap(True)
        form.addRow("", help_label)
        return page

    def _build_simple_page(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        form.setContentsMargins(0, 0, 0, 0)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setSpacing(8)

        self.simple_url = QLineEdit(page)
        self.simple_url.setPlaceholderText("http://127.0.0.1:8000/ask")
        form.addRow("接口地址", self.simple_url)

        self.simple_response_path = QLineEdit(page)
        self.simple_response_path.setPlaceholderText('留空自动猜;也可指定,如 data.answer')
        form.addRow("取值路径", self.simple_response_path)

        self.simple_prompt_field = QLineEdit(page)
        self.simple_prompt_field.setPlaceholderText("prompt")
        form.addRow("提示词字段", self.simple_prompt_field)

        self.simple_image_field = QLineEdit(page)
        self.simple_image_field.setPlaceholderText("image_base64")
        form.addRow("图片字段", self.simple_image_field)

        self.simple_headers = QPlainTextEdit(page)
        self.simple_headers.setFixedHeight(56)
        self.simple_headers.setPlaceholderText("额外请求头,一行一个,例如:\nAuthorization: Bearer xxx")
        form.addRow("请求头", self.simple_headers)

        help_label = QLabel(_SIMPLE_HELP, page)
        help_label.setWordWrap(True)
        form.addRow("", help_label)
        return page

    # ---------- 快捷键与外观 ----------

    def _build_ui_group(self) -> QWidget:
        box = QWidget(self)
        form = QFormLayout(box)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setSpacing(9)

        self.hotkey = QLineEdit(box)
        self.hotkey.setPlaceholderText("Alt+Q")
        form.addRow("截图快捷键", self.hotkey)

        opacity_row = QWidget(box)
        opacity_layout = QHBoxLayout(opacity_row)
        opacity_layout.setContentsMargins(0, 0, 0, 0)
        self.opacity = QSlider(Qt.Orientation.Horizontal, opacity_row)
        self.opacity.setRange(35, 100)
        self.opacity_value = QLabel("94%", opacity_row)
        self.opacity_value.setFixedWidth(44)
        self.opacity.valueChanged.connect(
            lambda value: self.opacity_value.setText(str(value) + "%")
        )
        opacity_layout.addWidget(self.opacity, 1)
        opacity_layout.addWidget(self.opacity_value)
        form.addRow("窗口不透明度", opacity_row)

        self.font_size = QSpinBox(box)
        self.font_size.setRange(11, 26)
        self.font_size.setSuffix(" px")
        form.addRow("答案字号", self.font_size)

        self.auto_hide = QSpinBox(box)
        self.auto_hide.setRange(0, 600)
        self.auto_hide.setSuffix(" 秒")
        self.auto_hide.setSpecialValueText("不自动隐藏")
        form.addRow("答完自动隐藏", self.auto_hide)

        self.remember_position = QCheckBox("记住答案窗位置(默认每次贴在题目旁边)", box)
        form.addRow("", self.remember_position)

        self.activate_on_show = QCheckBox("弹出答案窗时抢占焦点", box)
        self.activate_on_show.setToolTip("关闭时答案窗不会打断刷题软件的输入")
        form.addRow("", self.activate_on_show)

        self.ball_enabled = QCheckBox("显示桌面悬浮按钮(左键截图,右键更多功能)", box)
        form.addRow("", self.ball_enabled)

        self.ball_trigger = QComboBox(box)
        self.ball_trigger.addItem("单击截图(推荐)", "single")
        self.ball_trigger.addItem("双击截图(防误触更强)", "double")
        form.addRow("悬浮按钮触发", self.ball_trigger)

        self.ball_size = QSpinBox(box)
        self.ball_size.setRange(32, 96)
        self.ball_size.setSuffix(" px")
        form.addRow("悬浮按钮大小", self.ball_size)
        return box

    # ---------- 其他 ----------

    def _build_misc_group(self) -> QWidget:
        box = QWidget(self)
        form = QFormLayout(box)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setSpacing(9)

        self.auto_copy = QCheckBox("答完自动复制答案到剪贴板", box)
        form.addRow("", self.auto_copy)
        self.save_history = QCheckBox("把题目截图和解答保存到 history 文件夹", box)
        form.addRow("", self.save_history)
        self.notify = QCheckBox("答完弹一条气泡提醒", box)
        form.addRow("", self.notify)
        self.autostart = QCheckBox("开机自动启动(静默驻留托盘)", box)
        form.addRow("", self.autostart)

        self.prompt = QPlainTextEdit(box)
        self.prompt.setFixedHeight(92)
        self.prompt.setPlaceholderText("发给模型的系统提示词")
        form.addRow("提示词", self.prompt)
        return box

    # ---------- 数据 ----------

    def _load_values(self) -> None:
        name = str(get(self.cfg, "backend", "openai"))
        index = self.backend_box.findData(name)
        self.backend_box.setCurrentIndex(index if index >= 0 else 0)

        self.wb_reuse.setChecked(bool(get(self.cfg, "workbuddy_acp.reuse_session", True)))
        self.wb_cwd.setText(str(get(self.cfg, "workbuddy_acp.cwd", "")))
        self.wb_timeout.setValue(int(get(self.cfg, "workbuddy_acp.timeout_seconds", 300)))
        for combo, key, default in (
            (self.wb_thought, "thought_level", ""),
            (self.wb_model, "model", ""),
        ):
            index = combo.findData(str(get(self.cfg, "workbuddy_acp." + key, default) or ""))
            combo.setCurrentIndex(max(0, index))
        self.wb_warm.setChecked(bool(get(self.cfg, "workbuddy_acp.warm_up", True)))

        self.base_url.setText(str(get(self.cfg, "api.base_url", "")))
        self.api_key.setText(str(get(self.cfg, "api.api_key", "")))
        self.model.setText(str(get(self.cfg, "api.model", "")))

        self.cli_command.setText(str(get(self.cfg, "cli.command", "")))
        self.cli_tools.setText(str(get(self.cfg, "cli.tools", "Read")))
        self.cli_timeout.setValue(int(get(self.cfg, "cli.timeout_seconds", 300)))
        env = get(self.cfg, "cli.env", {}) or {}
        self.cli_token.setText(str(env.get("CODEBUDDY_AUTH_TOKEN", "")))
        self.cli_base_url.setText(str(env.get("CODEBUDDY_BASE_URL", "")))

        self.simple_url.setText(str(get(self.cfg, "simple.url", "")))
        self.simple_response_path.setText(str(get(self.cfg, "simple.response_path", "")))
        self.simple_prompt_field.setText(str(get(self.cfg, "simple.prompt_field", "prompt")))
        self.simple_image_field.setText(str(get(self.cfg, "simple.image_field", "image_base64")))
        headers = get(self.cfg, "simple.headers", {}) or {}
        self.simple_headers.setPlainText(
            "\n".join(str(k) + ": " + str(v) for k, v in headers.items())
        )

        self.hotkey.setText(str(get(self.cfg, "hotkey", "Alt+Q")))
        opacity = int(float(get(self.cfg, "appearance.opacity", 0.94)) * 100)
        self.opacity.setValue(max(35, min(100, opacity)))
        self.opacity_value.setText(str(self.opacity.value()) + "%")
        self.font_size.setValue(int(get(self.cfg, "appearance.font_size", 15)))
        self.auto_hide.setValue(int(get(self.cfg, "appearance.auto_hide_seconds", 0)))
        self.remember_position.setChecked(bool(get(self.cfg, "appearance.remember_position", False)))
        self.activate_on_show.setChecked(bool(get(self.cfg, "appearance.activate_on_show", False)))
        self.ball_enabled.setChecked(bool(get(self.cfg, "floating_button.enabled", True)))
        index = self.ball_trigger.findData(str(get(self.cfg, "floating_button.trigger", "single")))
        self.ball_trigger.setCurrentIndex(max(0, index))
        self.ball_size.setValue(int(get(self.cfg, "floating_button.size", 46)))

        self.auto_copy.setChecked(bool(get(self.cfg, "behavior.copy_answer_to_clipboard", False)))
        self.save_history.setChecked(bool(get(self.cfg, "behavior.save_history", True)))
        self.notify.setChecked(bool(get(self.cfg, "behavior.notify_on_done", False)))
        self.autostart.setChecked(autostart.is_enabled())
        self.prompt.setPlainText(str(get(self.cfg, "system_prompt", "")))

        self._on_backend_changed()
        self._refresh_detect_label()

    def _on_backend_changed(self) -> None:
        key = self.backend_box.currentData()
        self.page_wb.setVisible(key == "workbuddy_acp")
        self.page_api.setVisible(key == "openai")
        self.page_cli.setVisible(key == "cli")
        self.page_simple.setVisible(key == "simple")
        self.test_result.setText("")
        self._refresh_detect_label()

    def _refresh_detect_label(self) -> None:
        if self.backend_box.currentData() != "cli":
            self.detect_label.setText("")
            return
        configured = self.cli_command.text().strip()
        path = Path(configured) if configured else find_cli()
        if path is None:
            self.detect_label.setText(
                '<span style="color:#FFB020">没有找到 CLI,请手动填写路径</span>'
            )
        elif not path.exists():
            self.detect_label.setText('<span style="color:#FF6B6B">路径不存在</span>')
        else:
            self.detect_label.setText(
                '<span style="color:#4ED18B">已找到:' + str(path) + "</span>"
            )

    def _detect_cli(self) -> None:
        found = find_cli()
        if found is None:
            QMessageBox.warning(
                self,
                "没找到",
                "没找到本地智能体 CLI。\n\n"
                "WorkBuddy 一般在:\n<安装目录>\\resources\\app.asar.unpacked\\cli\\dist\\codebuddy-lite-wb.mjs\n"
                "请手动把完整路径填到上面的输入框。",
            )
            return
        self.cli_command.setText(str(found))
        self._refresh_detect_label()

    def _open_cli_login(self) -> None:
        """开一个命令行窗口让用户执行 /login。"""
        try:
            argv = self._cli_login_argv()
        except BackendError as exc:
            QMessageBox.warning(self, "无法打开", str(exc))
            return
        try:
            if sys.platform == "win32":
                subprocess.Popen(argv, creationflags=0x00000010)  # CREATE_NEW_CONSOLE
            else:
                subprocess.Popen(argv)
        except OSError as exc:
            QMessageBox.warning(self, "无法打开", "启动失败:" + str(exc))
            return
        QMessageBox.information(
            self,
            "登录本地智能体",
            "已经打开一个诊断窗口。\n\n"
            "如果它显示出 CodeBuddy 自己的界面,输入 /login 并按提示授权;\n"
            "如果窗口一闪就退回了,说明这个版本不支持单独登录 ——\n"
            "那就改用「豆包官方 API」,或者把 AUTH TOKEN 填进来。\n\n"
            "注意:看到 >>> 这种提示符说明你在 Python 里,那个窗口不对。",
        )

    def _cli_login_argv(self) -> list[str]:
        """只取 CLI 本体(不带 -p 参数),用来开交互式登录窗口。"""
        temp = copy.deepcopy(self.cfg)
        self._collect_cli(temp)
        from .cli_backend import CliBackend

        backend = CliBackend(temp.get("cli") or {}, "")
        node_and_cli = backend.resolve_argv()[:2]
        return [str(item) for item in node_and_cli]

    def _test_connection(self) -> None:
        temp = copy.deepcopy(self.cfg)
        self._collect(temp)
        key = self.backend_box.currentData()
        if key == "openai" and not temp["api"].get("api_key", "").strip():
            self.test_result.setText('<span style="color:#FF6B6B">请先填写 API Key</span>')
            return
        if key == "cli" and not str(temp["cli"].get("command", "")).strip() and find_cli() is None:
            self.test_result.setText('<span style="color:#FF6B6B">没有找到 CLI,请先填写路径</span>')
            return
        if key == "simple" and not temp["simple"].get("url", "").strip():
            self.test_result.setText('<span style="color:#FF6B6B">请先填写接口地址</span>')
            return

        self.test_button.setEnabled(False)
        self.test_result.setText('<span style="color:#93A0B4">正在测试…(本地智能体首次会比较慢)</span>')
        self._worker = _CheckWorker(temp)
        self._worker.succeeded.connect(self._on_test_ok)
        self._worker.failed.connect(self._on_test_fail)
        self._worker.finished.connect(lambda: self.test_button.setEnabled(True))
        self._worker.start()

    def _on_test_ok(self, message: str) -> None:
        safe = message.replace("<", "&lt;").replace("\n", "<br>")
        self.test_result.setText('<span style="color:#4ED18B">' + safe + "</span>")

    def _on_test_fail(self, message: str) -> None:
        safe = message.replace("<", "&lt;").replace("\n", "<br>")
        self.test_result.setText('<span style="color:#FF6B6B">' + safe + "</span>")

    def _toggle_key_echo(self, shown: bool) -> None:
        self.api_key.setEchoMode(
            QLineEdit.EchoMode.Normal if shown else QLineEdit.EchoMode.Password
        )

    # ---------- 收集 ----------

    def _collect_cli(self, cfg: dict) -> None:
        cli = cfg.setdefault("cli", {})
        cli["command"] = self.cli_command.text().strip()
        cli["tools"] = self.cli_tools.text().strip()
        cli["timeout_seconds"] = self.cli_timeout.value()
        env = cli.setdefault("env", {})
        env["CODEBUDDY_AUTH_TOKEN"] = self.cli_token.text().strip()
        env["CODEBUDDY_BASE_URL"] = self.cli_base_url.text().strip()

    def _collect(self, cfg: dict) -> dict:
        cfg["backend"] = str(self.backend_box.currentData() or "openai")

        wb = cfg.setdefault("workbuddy_acp", {})
        wb["reuse_session"] = self.wb_reuse.isChecked()
        wb["cwd"] = self.wb_cwd.text().strip()
        wb["timeout_seconds"] = self.wb_timeout.value()
        wb["thought_level"] = str(self.wb_thought.currentData() or "")
        wb["model"] = str(self.wb_model.currentData() or "")
        wb["warm_up"] = self.wb_warm.isChecked()

        api = cfg.setdefault("api", {})
        api["base_url"] = self.base_url.text().strip() or "https://ark.cn-beijing.volces.com/api/v3"
        api["api_key"] = self.api_key.text().strip()
        api["model"] = self.model.text().strip()

        self._collect_cli(cfg)

        simple = cfg.setdefault("simple", {})
        simple["url"] = self.simple_url.text().strip()
        simple["response_path"] = self.simple_response_path.text().strip()
        simple["prompt_field"] = self.simple_prompt_field.text().strip() or "prompt"
        simple["image_field"] = self.simple_image_field.text().strip() or "image_base64"
        headers = {}
        for line in self.simple_headers.toPlainText().splitlines():
            if ":" in line:
                key, _, value = line.partition(":")
                if key.strip():
                    headers[key.strip()] = value.strip()
        simple["headers"] = headers

        set_value(cfg, "hotkey", self.hotkey.text().strip() or "Alt+Q")
        set_value(cfg, "appearance.opacity", self.opacity.value() / 100.0)
        set_value(cfg, "appearance.font_size", self.font_size.value())
        set_value(cfg, "appearance.auto_hide_seconds", self.auto_hide.value())
        set_value(cfg, "appearance.remember_position", self.remember_position.isChecked())
        set_value(cfg, "appearance.activate_on_show", self.activate_on_show.isChecked())
        set_value(cfg, "behavior.copy_answer_to_clipboard", self.auto_copy.isChecked())
        set_value(cfg, "behavior.save_history", self.save_history.isChecked())
        set_value(cfg, "behavior.notify_on_done", self.notify.isChecked())
        set_value(cfg, "floating_button.enabled", self.ball_enabled.isChecked())
        set_value(cfg, "floating_button.trigger", str(self.ball_trigger.currentData() or "single"))
        set_value(cfg, "floating_button.size", self.ball_size.value())
        set_value(cfg, "system_prompt", self.prompt.toPlainText())
        return cfg

    def accept(self) -> None:  # noqa: D102
        try:
            parse_hotkey(self.hotkey.text().strip() or "Alt+Q")
        except ValueError as exc:
            QMessageBox.warning(self, "快捷键无效", str(exc))
            return

        key = str(self.backend_box.currentData())
        if key == "workbuddy_acp":
            from .workbuddy_acp_backend import workbuddy_running

            if not workbuddy_running():
                answer = QMessageBox.question(
                    self,
                    "WorkBuddy 好像没开",
                    "没检测到 WorkBuddy 桌面端在运行。\n"
                    "这条通道需要它开着(最小化没关系)。\n\n仍然保存吗?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return
        elif key == "openai":
            if not self.model.text().strip():
                QMessageBox.warning(self, "还差一步", "请填写模型 ID,例如 doubao-seed-1-6-250615。")
                return
            if not self.api_key.text().strip():
                answer = QMessageBox.question(
                    self,
                    "还没填 API Key",
                    "没有 API Key 就发不出题目。要保存吗?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return
        elif key == "cli":
            if not self.cli_command.text().strip() and find_cli() is None:
                QMessageBox.warning(
                    self, "还差一步", "没找到本地智能体 CLI,请点「自动探测」或手动填路径。"
                )
                return
        elif key == "simple" and not self.simple_url.text().strip():
            QMessageBox.warning(self, "还差一步", "请填写你本地平台的接口地址。")
            return

        self._collect(self.cfg)
        ok, message = autostart.set_enabled(self.autostart.isChecked())
        if not ok:
            QMessageBox.warning(self, "开机自启设置失败", message)
        super().accept()

