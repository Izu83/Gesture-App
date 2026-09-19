"""Regenerates the README images in docs/ (run from the repo root: python tools/make_readme_images.py)."""
import os
import sys

from PIL import Image, ImageDraw

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import help_screen as hs  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")
DOCS = os.path.join(ROOT, "docs")
GESTURES = os.path.join(DOCS, "gestures")
HEADINGS = os.path.join(DOCS, "headings")
HEADING_TITLES = [  # README section titles, drawn in the app font because GitHub cannot change fonts
    "What it does", "Contents", "Quick start", "Gestures", "Voice commands", "Air mouse", "Keys",
    "Tips for best results", "Troubleshooting", "How it works", "Project structure",
    "Tuning", "License", "Credits",
]

TILES = [  # (col, row, file name) in the order build_help_image lays them out
    (0, 0, "open-palm"), (1, 0, "double-open-palm"), (2, 0, "swipe"),
    (0, 1, "search"), (1, 1, "middle-finger"), (2, 1, "help"),
    (0, 2, "fist"), (1, 2, "push"), (2, 2, "pull"),
    (0, 3, "scroll-up"), (1, 3, "scroll-down"), (2, 3, "escape"), (0, 4, "enter"), (1, 4, "mouse-mode"), (2, 4, "mouse-click"), (1, 5, "dictate"),
]


def save_help_images():
    full = Image.fromarray(hs.build_help_image()[:, :, ::-1])  # BGR -> RGB
    full.save(os.path.join(DOCS, "help.png"))
    for col, row, name in TILES:
        x0, y0 = col * hs.TILE_W + 10, hs.TITLE_H + row * hs.TILE_H + 10
        full.crop((x0, y0, x0 + hs.TILE_W - 20, y0 + hs.TILE_H - 20)).save(
            os.path.join(GESTURES, name + ".png"))


def save_headings():
    font = hs.load(hs.TITLE_FONT, 96, 800)
    ref = font.getbbox("Hgjpq")  # same height for every heading, so they scale alike
    for title in HEADING_TITLES:
        box = font.getbbox(title)
        img = Image.new("RGBA", (box[2] - box[0] + 8, ref[3] - ref[1] + 8), (0, 0, 0, 0))
        ImageDraw.Draw(img).text((4 - box[0], 4 - ref[1]), title, font=font, fill=hs.ACCENT)
        img.save(os.path.join(HEADINGS, title.lower().replace(" ", "-") + ".png"))


def round_icon(path, size, circle=False):
    icon = Image.open(path).convert("RGBA").resize((size, size), Image.LANCZOS)
    if circle:
        mask = Image.new("L", (size * 4, size * 4), 0)
        ImageDraw.Draw(mask).ellipse([0, 0, size * 4 - 1, size * 4 - 1], fill=255)
        icon.putalpha(mask.resize((size, size), Image.LANCZOS))
    return icon


def save_banner():
    w, h = 1280, 320
    img = Image.new("RGB", (w, h), hs.BG)
    draw = ImageDraw.Draw(img)
    for x in range(w):  # soft accent glow from the right
        t = max(0.0, (x - w * 0.45) / (w * 0.55)) ** 2
        c = tuple(int(a + (b - a) * t * 0.3) for a, b in zip(hs.BG, hs.ACCENT))
        draw.line([(x, 0), (x, h)], fill=c)
    draw.rounded_rectangle([1, 1, w - 2, h - 2], radius=24, outline=(40, 64, 52), width=2)

    hs.draw_skeleton(draw, hs.OPEN_HAND, 1070, 175, 1.25)
    hs.draw_skeleton(draw, hs.SEARCH_HAND, 830, 200, 0.8)

    # The project comes first: its name, then what it does.
    draw.text((70, 30), "Gesture-App", font=hs.load(hs.TITLE_FONT, 100, 800), fill=hs.ACCENT)
    draw.text((74, 158), "Control your PC with your hands and your voice",
              font=hs.load(hs.TEXT_FONT, 30, 500), fill=hs.WHITE)

    # The author is the maker; Python is only mentioned as the tool, in small grey text.
    avatar = round_icon(os.path.join(DOCS, "izu83.png"), 58, circle=True)
    img.paste(avatar, (74, 208), avatar)
    draw.ellipse([72, 206, 134, 268], outline=hs.ACCENT, width=3)
    draw.text((150, 238), "Made by Izu83", font=hs.load(hs.TEXT_FONT, 38, 700),
              fill=hs.WHITE, anchor="lm")

    logo = round_icon(os.path.join(DOCS, "python-logo.png"), 26)
    img.paste(logo, (76, 282), logo)
    draw.text((112, 295), "Built with Python  \u00b7  MediaPipe  \u00b7  OpenCV  \u00b7  Whisper",
              font=hs.load(hs.TEXT_FONT, 24, 500), fill=hs.GREY, anchor="lm")
    img.save(os.path.join(DOCS, "banner.png"))

if __name__ == "__main__":
    os.makedirs(GESTURES, exist_ok=True)
    os.makedirs(HEADINGS, exist_ok=True)
    save_headings()
    save_help_images()
    save_banner()
    print("wrote the banner, help picture, gesture pictures and README headings in docs/")