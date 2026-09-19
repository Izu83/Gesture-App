"""Regenerates the README images in docs/ (run from the repo root: python tools/make_readme_images.py)."""
import os
import sys

from PIL import Image, ImageDraw

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import help_screen as hs  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")
DOCS = os.path.join(ROOT, "docs")
GESTURES = os.path.join(DOCS, "gestures")

TILES = [  # (col, row, file name) in the order build_help_image lays them out
    (0, 0, "open-palm"), (1, 0, "double-open-palm"), (2, 0, "swipe"),
    (0, 1, "search"), (1, 1, "middle-finger"), (2, 1, "help"),
    (0, 2, "fist"), (1, 2, "push"), (2, 2, "pull"),
]


def save_help_images():
    full = Image.fromarray(hs.build_help_image()[:, :, ::-1])  # BGR -> RGB
    full.save(os.path.join(DOCS, "help.png"))
    for col, row, name in TILES:
        x0, y0 = col * hs.TILE_W + 10, hs.TITLE_H + row * hs.TILE_H + 10
        full.crop((x0, y0, x0 + hs.TILE_W - 20, y0 + hs.TILE_H - 20)).save(
            os.path.join(GESTURES, name + ".png"))


def badge(draw, x, y, text, font, fill, outline, text_fill):
    box = draw.textbbox((0, 0), text, font=font)
    w, h = box[2] - box[0] + 56, 62  # fixed height so every badge lines up
    draw.rounded_rectangle([x, y, x + w, y + h], radius=h // 2, fill=fill,
                           outline=outline, width=3)
    draw.text((x + 28 - box[0], y + h // 2), text, font=font, fill=text_fill, anchor="lm")
    return w


def save_banner():
    w, h = 1280, 320
    img = Image.new("RGB", (w, h), hs.BG)
    draw = ImageDraw.Draw(img)
    for x in range(w):  # soft orange glow from the right
        t = max(0.0, (x - w * 0.45) / (w * 0.55)) ** 2
        c = tuple(int(a + (b - a) * t * 0.35) for a, b in zip(hs.BG, hs.ORANGE))
        draw.line([(x, 0), (x, h)], fill=c)
    draw.rounded_rectangle([1, 1, w - 2, h - 2], radius=24, outline=(70, 70, 78), width=2)

    hs.draw_skeleton(draw, hs.OPEN_HAND, 1070, 175, 1.25)
    hs.draw_skeleton(draw, hs.SEARCH_HAND, 830, 200, 0.8)

    title = hs.load(hs.TITLE_FONT, 92)
    draw.text((70, 55), "Gesture-App", font=title, fill=hs.ORANGE)
    small = hs.load(hs.TEXT_FONT, 32)
    x = 74
    x += badge(draw, x, 200, "Izu83", small, (36, 36, 42), hs.ORANGE, hs.WHITE) + 18
    badge(draw, x, 200, "Python", small, (36, 36, 42), hs.ORANGE, hs.WHITE)
    img.save(os.path.join(DOCS, "banner.png"))


if __name__ == "__main__":
    os.makedirs(GESTURES, exist_ok=True)
    save_help_images()
    save_banner()
    print("wrote docs/banner.png, docs/help.png and docs/gestures/*.png")