"""生成小红书宣传图(1080x1440,3:4)。

    .venv\\Scripts\\python.exe tools\\make_xhs.py

演示内容全部是现场合成的示例数据,不含任何真实用户数据。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QBuffer, QIODevice, QPoint, QRect, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QApplication

W, H = 1080, 1440
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "xhs"
ACCENT = QColor("#4C9BFF")
INK = QColor("#161A21")
GREY = QColor("#697386")
LIGHT = QColor("#F4F6FA")


def font(size: int, bold: bool = False) -> QFont:
    f = QFont("Microsoft YaHei UI")
    f.setPixelSize(size)
    f.setBold(bold)
    return f


# ---------------------------------------------------------------- 绘制小工具

def fill_gradient(painter: QPainter, rect: QRect, top: QColor, bottom: QColor) -> None:
    grad = QLinearGradient(0, rect.top(), 0, rect.bottom())
    grad.setColorAt(0.0, top)
    grad.setColorAt(1.0, bottom)
    painter.fillRect(rect, grad)


def rounded(painter: QPainter, rect: QRectF, radius: float, color: QColor) -> None:
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    painter.fillPath(path, color)


def soft_shadow(painter: QPainter, rect: QRect, radius: float, spread: int = 16,
                alpha: int = 9, layers: int = 7) -> None:
    """用几层很淡的圆角矩形堆出柔和的投影(比真模糊便宜)。"""
    for i in range(layers, 0, -1):
        grow = spread * i / layers
        rounded(painter,
                QRectF(rect).adjusted(-grow, -grow + 6, grow, grow + 6),
                radius + grow, QColor(23, 32, 48, max(2, alpha - i)))


def draw_shot(painter: QPainter, rect: QRect, pixmap: QPixmap, radius: float = 24,
              frame: bool = False) -> None:
    """把截图放进卡片:等比缩放居中;frame=True 时套一圈白边(适合放在彩色背景上)。"""
    soft_shadow(painter, rect, radius)
    scaled = pixmap.scaled(rect.width(), rect.height(), Qt.AspectRatioMode.KeepAspectRatio,
                           Qt.TransformationMode.SmoothTransformation)
    box = QRect(rect.x() + (rect.width() - scaled.width()) // 2,
                rect.y() + (rect.height() - scaled.height()) // 2,
                scaled.width(), scaled.height())
    if frame:
        pad = 12
        frame_rect = QRectF(box).adjusted(-pad, -pad, pad, pad)
        rounded(painter, frame_rect, radius + 8, QColor(255, 255, 255, 250))
        box = box.adjusted(0, 0, 0, 0)
    path = QPainterPath()
    path.addRoundedRect(QRectF(box), radius, radius)
    painter.save()
    painter.setClipPath(path)
    painter.drawPixmap(box, scaled)
    painter.restore()


def text(painter: QPainter, rect: QRect, content: str, size: int, color: QColor,
         bold: bool = False, align=None) -> None:
    painter.setFont(font(size, bold))
    painter.setPen(color)
    flag = align or (Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    painter.drawText(rect, int(flag), content)


def pill(painter: QPainter, rect: QRect, content: str, bg: QColor, fg: QColor, size: int = 28) -> None:
    rounded(painter, QRectF(rect), rect.height() / 2, bg)
    text(painter, rect, content, size, fg, True, Qt.AlignmentFlag.AlignCenter)


def bullets(painter: QPainter, x: int, y: int, width: int, items: list[str],
            size: int = 36, gap: int = 68) -> int:
    painter.setFont(font(size))
    for i, item in enumerate(items):
        cy = y + i * gap
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(ACCENT)
        painter.drawEllipse(QPoint(x + 9, cy + size // 2 + 2), 8, 8)
        painter.setPen(INK)
        painter.drawText(QRect(x + 38, cy - 4, width - 38, size + 16),
                         int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), item)
    return y + len(items) * gap


# ---------------------------------------------------------------- 截图素材

def render_widget(widget, scale: int = 2) -> QPixmap:
    image = QImage(widget.width() * scale, widget.height() * scale, QImage.Format.Format_ARGB32)
    image.setDevicePixelRatio(scale)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    widget.render(painter, QPoint(0, 0))
    painter.end()
    return QPixmap.fromImage(image)


def pixmap_of(widget) -> QPixmap:
    image = QImage(widget.width(), widget.height(), QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    widget.render(painter, QPoint(0, 0))
    painter.end()
    return QPixmap.fromImage(image)


_BANKS = [
    ("下列关于长期金融工具的说法,正确的是:",
     ["A. 期限在一年以上", "B. 期限在一年以内", "C. 只包括股票", "D. 流动性最强"]),
    ("一辆汽车 2 小时行驶 72 千米,平均速度是多少?",
     ["A. 72 km/h", "B. 36 km/h", "C. 18 km/h", "D. 20 km/h"]),
    ("标准大气压下水的沸点是多少摄氏度?",
     ["A. 90", "B. 100", "C. 110", "D. 120"]),
]


def demo_question(seed: int, width: int = 1000, height: int = 330) -> QPixmap:
    title, options = _BANKS[seed % len(_BANKS)]
    img = QImage(width, height, QImage.Format.Format_ARGB32)
    img.fill(QColor(255, 255, 255))
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setPen(QColor(226, 231, 238))
    p.drawRoundedRect(QRectF(0.5, 0.5, width - 1, height - 1), 16, 16)
    p.setPen(QColor(28, 34, 44))
    p.setFont(font(30, True))
    p.drawText(QRect(40, 34, width - 80, 50), int(Qt.AlignmentFlag.AlignLeft), title)
    p.setFont(font(25))
    p.setPen(QColor(74, 82, 96))
    for i, option in enumerate(options):
        p.drawText(QRect(58, 112 + i * 48, width - 100, 42),
                   int(Qt.AlignmentFlag.AlignLeft), option)
    p.end()
    return QPixmap.fromImage(img)


DEMO_ANSWER = """【答案】A. 期限在一年以上

【解析】
1. 金融工具按期限划分:一年以内(含一年)的是短期
   金融工具,如商业票据、国库券;
2. 一年以上的为长期金融工具,如股票、长期债券。
   因此 A 正确。

【题目】长期金融工具的期限判断"""


def shot_answer(app) -> QPixmap:
    from screen_qa.answer_window import AnswerWindow
    from screen_qa.config import load_config

    win = AnswerWindow(load_config())
    win.resize(600, 470)
    win.begin(QRect(300, 300, 500, 200), (500, 200), source_label="WorkBuddy")
    win.append(DEMO_ANSWER)
    for _ in range(10):
        app.processEvents()
    win.finish(4.2)
    for _ in range(10):
        app.processEvents()
    pixmap = render_widget(win)
    win.hide()
    return pixmap


def shot_collection(app) -> QPixmap:
    import screen_qa.collection as coll
    from screen_qa.collection_window import CollectionWindow

    tmp = Path(tempfile.mkdtemp()) / "c.db"
    coll.collection_dir = lambda: tmp.parent
    collection = coll.Collection(tmp)
    collection.add_folder("数学")
    collection.add_folder("英语")
    rows = [("数学", "注意期限"), ("数学", ""), ("英语", "记固定搭配")]
    for i, (folder, note) in enumerate(rows):
        buf = QBuffer()
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        demo_question(i).save(buf, "PNG")
        collection.add_item(
            ["长期金融工具的期限特征判断", "平均速度的计算", "标准大气压下水沸点"][i],
            ["【答案】A", "【答案】B", "【答案】B"][i],
            folder=folder, note=note, image_png=bytes(buf.data()),
        )
    win = CollectionWindow()
    win.collection = collection
    win.resize(1240, 780)
    win.reload()
    for _ in range(14):
        app.processEvents()
    pixmap = render_widget(win)
    win.hide()
    return pixmap


def shot_overlay(app) -> QPixmap:
    from screen_qa.overlay import ScreenOverlay

    bg = QPixmap(1520, 900)
    bg.fill(QColor(247, 249, 252))
    p = QPainter(bg)
    p.setPen(QColor(212, 219, 229))
    for x in range(0, 1520, 56):
        p.drawLine(x, 0, x, 900)
    for y in range(0, 900, 56):
        p.drawLine(0, y, 1520, y)
    p.fillRect(QRect(300, 250, 920, 330), QColor(255, 255, 255))
    p.setPen(QColor(36, 42, 52))
    p.setFont(font(40, True))
    p.drawText(QRect(360, 300, 820, 60), int(Qt.AlignmentFlag.AlignLeft),
               "下列关于长期金融工具的说法,正确的是:")
    p.setFont(font(33))
    p.setPen(QColor(88, 96, 110))
    for i, option in enumerate(["A. 期限在一年以上", "B. 期限在一年以内", "C. 只包括股票"]):
        p.drawText(QRect(380, 380 + i * 56, 700, 48), int(Qt.AlignmentFlag.AlignLeft), option)
    p.end()

    overlay = ScreenOverlay(app.primaryScreen(), bg, show_highlight=False)
    overlay.resize(1520, 900)
    overlay._dragging = True
    overlay._start = QPoint(300, 250)
    overlay._current = QRect(QPoint(300, 250), QPoint(1220, 580))
    return render_widget(overlay)


def shot_button(app) -> QPixmap:
    from screen_qa.floating_button import FloatingButton

    button = FloatingButton({"floating_button": {"size": 60, "trigger": "single"}})
    button.resize(60, 60)
    for _ in range(6):
        app.processEvents()
    return pixmap_of(button)


# ---------------------------------------------------------------- 五张图

def page_cover(answer: QPixmap) -> None:
    img = QImage(W, H, QImage.Format.Format_ARGB32)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    fill_gradient(p, QRect(0, 0, W, H), QColor("#4C9BFF"), QColor("#6E5BFF"))

    pill(p, QRect(80, 96, 320, 66), "刷题党 · 效率工具",
         QColor(255, 255, 255, 62), QColor(255, 255, 255), 30)
    text(p, QRect(80, 196, 920, 108), "不会的题", 94, QColor(255, 255, 255), True)
    text(p, QRect(80, 304, 960, 126), "截一下就出答案", 100, QColor(255, 255, 255), True)
    text(p, QRect(84, 450, 920, 54), "不用切窗口 · 不用粘贴 · 答案直接浮在桌面",
         33, QColor(255, 255, 255, 225))

    draw_shot(p, QRect(130, 556, 820, 540), answer, radius=23, frame=True)

    for i, (label, w) in enumerate([("免 API Key", 252), ("开源免费", 216), ("Win / Mac", 258)]):
        pill(p, QRect(80 + i * 306, 1170, w, 76), label,
             QColor(255, 255, 255, 52), QColor(255, 255, 255), 31)

    text(p, QRect(80, 1292, 920, 56), "答案贴在桌面,抬眼就能看", 36,
         QColor(255, 255, 255, 210))
    p.end()
    img.save(str(OUT / "01-封面.png"))
    print("  写出 01-封面.png")


def page_block(kicker: str, title: str, shot: QPixmap, items: list[str], name: str,
               card: QRect = QRect(70, 600, 940, 660), extra: QPixmap | None = None,
               extra_label: str = "") -> None:
    img = QImage(W, H, QImage.Format.Format_ARGB32)
    img.fill(LIGHT)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    fill_gradient(p, QRect(0, 0, W, 620), QColor("#FFFFFF"), QColor("#EEF3FB"))

    pill(p, QRect(70, 96, 190, 60), kicker, QColor(76, 155, 255, 40), ACCENT, 28)
    text(p, QRect(70, 182, 940, 88), title, 68, INK, True)
    bullets(p, 74, 306, 950, items, size=36, gap=66)
    draw_shot(p, card, shot, radius=22)

    if extra is not None:
        size = 150
        box = QRect(W - size - 96, card.top() - size // 2 + 10, size, size)
        rounded(p, QRectF(box).adjusted(-10, -10, 10, 10), size, QColor(255, 255, 255, 235))
        p.drawPixmap(box, extra.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                                       Qt.TransformationMode.SmoothTransformation))
        if extra_label:
            text(p, QRect(card.right() - 420, box.bottom() + 6, 420, 44), extra_label,
                 28, GREY, True, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    p.end()
    img.save(str(OUT / name))
    print("  写出", name)


def page_get(qr: QPixmap) -> None:
    img = QImage(W, H, QImage.Format.Format_ARGB32)
    img.fill(QColor("#FFFFFF"))
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    fill_gradient(p, QRect(0, 0, W, H), QColor("#FFFFFF"), QColor("#E9F0FF"))

    text(p, QRect(70, 116, 940, 92), "开源免费,自取", 76, INK, True)
    text(p, QRect(72, 224, 940, 54), "Windows / macOS 都能跑,代码全部公开", 35, GREY)
    bullets(p, 74, 336, 950, [
        "不用 API Key(直接借本机 WorkBuddy 答题)",
        "也支持豆包官方 API / 你自己的接口",
        "截图原图直接发给模型,公式图形都认得",
    ], size=35, gap=68)

    qr_card = QRect(80, 620, 380, 380)
    draw_shot(p, qr_card, qr, radius=20)
    text(p, QRect(510, 660, 500, 52), "扫码直达仓库", 34, GREY)
    text(p, QRect(510, 726, 520, 76), "screenshot-qa", 52, INK, True)
    text(p, QRect(510, 812, 520, 48), "github.com/Ysirand", 30, ACCENT)

    p.setPen(QPen(QColor(214, 222, 236), 2))
    p.drawLine(80, 1076, 1000, 1076)
    text(p, QRect(80, 1116, 940, 56), "有问题欢迎提 Issue,看到都会回", 34, GREY)
    text(p, QRect(80, 1186, 940, 60), "觉得有用的话,给个 Star 就是最大的支持 ⭐", 34,
         INK, True)
    p.end()
    img.save(str(OUT / "05-获取方式.png"))
    print("  写出 05-获取方式.png")


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    OUT.mkdir(exist_ok=True)
    print("渲染界面截图…")
    answer = shot_answer(app)
    collection = shot_collection(app)
    overlay = shot_overlay(app)
    button = shot_button(app)

    print("生成二维码…")
    import qrcode

    code = qrcode.QRCode(box_size=10, border=2)
    code.add_data("https://github.com/Ysirand/screenshot-qa")
    code.make(fit=True)
    qr_path = OUT / "_qr.png"
    code.make_image(fill_color="#12161C", back_color="white").convert("RGB").save(qr_path)
    qr = QPixmap(str(qr_path))

    print("合成小红书图…")
    page_cover(answer)
    page_block(
        "功能 1", "答案直接贴在桌面", answer,
        ["置顶浮窗,抬眼就能看,不用切窗口",
         "不抢焦点,不打断你正在打的字",
         "能接着追问,还能一键收藏进错题册"],
        "02-答案浮窗.png",
        card=QRect(80, 600, 920, 660),
    )
    page_block(
        "功能 2", "点一下按钮就截图", overlay,
        ["左键截图,拖框选中题目就出答案",
         "按住能拖动,自动吸边,不挡视线",
         "右键出菜单:收藏夹 / 设置 / 退出"],
        "03-截图框选.png",
        card=QRect(80, 700, 920, 600),
        extra=button,
        extra_label="桌面常驻的小按钮",
    )
    page_block(
        "功能 3", "做过就攒成一本错题册", collection,
        ["截图就是题干,一眼认出是哪道题",
         "自己建分类、写批注、随手搜",
         "全存在本地,不联网也是秒开"],
        "04-收藏夹.png",
        card=QRect(70, 620, 940, 640),
    )
    page_get(qr)
    qr_path.unlink(missing_ok=True)
    print("\n完成!图片在", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())