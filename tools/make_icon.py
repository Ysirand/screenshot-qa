"""生成 assets/icon.png 和 assets/icon.ico(放大镜 + 试卷)。"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
SIZE = 512


def _lerp(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def build() -> Image.Image:
    top = (76, 155, 255)
    bottom = (122, 92, 255)
    image = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))

    gradient = Image.new("RGBA", (SIZE, SIZE))
    draw = ImageDraw.Draw(gradient)
    for y in range(SIZE):
        draw.line([(0, y), (SIZE, y)], fill=_lerp(top, bottom, y / (SIZE - 1)) + (255,))

    mask = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, SIZE - 1, SIZE - 1], radius=112, fill=255)
    image.paste(gradient, (0, 0), mask)

    draw = ImageDraw.Draw(image)

    # 试卷
    paper = [104, 108, 300, 356]
    draw.rounded_rectangle(paper, radius=26, fill=(255, 255, 255, 246))
    for index in range(4):
        y = 152 + index * 42
        width = 118 if index % 2 == 0 else 150
        draw.rounded_rectangle([136, y, 136 + width, y + 14], radius=7, fill=(178, 194, 216, 255))

    # 放大镜
    cx, cy, radius = 320, 316, 104
    draw.ellipse(
        [cx - radius, cy - radius, cx + radius, cy + radius],
        outline=(255, 255, 255, 255),
        width=30,
    )
    draw.ellipse(
        [cx - radius + 62, cy - radius + 62, cx + radius - 62, cy + radius - 62],
        fill=(255, 255, 255, 40),
    )
    draw.line([(cx + 74, cy + 74), (cx + 168, cy + 168)], fill=(255, 255, 255, 255), width=34)
    draw.ellipse([cx + 148, cy + 148, cx + 188, cy + 188], fill=(255, 255, 255, 255))

    return image


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    image = build()
    png = ASSETS / "icon.png"
    image.save(png)
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    image.save(ASSETS / "icon.ico", sizes=sizes)
    print("wrote", png)
    print("wrote", ASSETS / "icon.ico")


if __name__ == "__main__":
    main()