"""抓屏:优先用 Qt 的 QScreen.grabWindow,遇到纯黑/失败时回落到 mss(BitBlt)。"""

from __future__ import annotations

from PySide6.QtGui import QImage, QPixmap

from .logging_setup import log


def _looks_blank(pixmap: QPixmap) -> bool:
    """抽样看是不是一片全黑(某些显卡/驱动下 grabWindow 会抓到黑屏)。"""
    image = pixmap.toImage()
    if image.isNull():
        return True
    width, height = image.width(), image.height()
    if width < 4 or height < 4:
        return True
    step_x = max(1, width // 16)
    step_y = max(1, height // 16)
    for y in range(0, height, step_y):
        for x in range(0, width, step_x):
            color = image.pixelColor(x, y)
            if color.red() > 8 or color.green() > 8 or color.blue() > 8:
                return False
    return True


def _grab_with_mss(screen) -> QPixmap:
    try:
        import mss
    except ImportError:
        return QPixmap()

    geometry = screen.geometry()
    ratio = float(screen.devicePixelRatio() or 1.0)
    region = {
        "left": int(round(geometry.x() * ratio)),
        "top": int(round(geometry.y() * ratio)),
        "width": max(1, int(round(geometry.width() * ratio))),
        "height": max(1, int(round(geometry.height() * ratio))),
    }
    try:
        with mss.mss() as sct:
            shot = sct.grab(region)
    except Exception as exc:  # pragma: no cover
        log.warning("mss 抓屏失败: %s", exc)
        return QPixmap()

    # mss 给的是 BGRA,正好就是小端下 QImage.Format_ARGB32 的字节序
    image = QImage(
        bytes(shot.raw), shot.width, shot.height, shot.width * 4, QImage.Format.Format_ARGB32
    )
    return QPixmap.fromImage(image.copy())


def grab_screen(screen) -> QPixmap:
    """抓一整个屏幕。"""
    from . import plat

    if plat.IS_MACOS and not plat.macos_has_screen_permission():
        log.warning("macOS 没有屏幕录制权限,截出来可能只有壁纸")
    pixmap = QPixmap()
    try:
        pixmap = screen.grabWindow(0)
    except Exception as exc:
        log.warning("QScreen.grabWindow 失败: %s", exc)

    if not pixmap.isNull() and not _looks_blank(pixmap):
        return pixmap

    fallback = _grab_with_mss(screen)
    if not fallback.isNull():
        log.info("已回落到 mss 抓屏")
        return fallback
    return pixmap