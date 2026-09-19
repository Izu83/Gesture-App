"""Builds the "Help" image: one tile per gesture, drawn as a hand skeleton."""
import os

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(__file__)
TITLE_FONT = os.path.join(HERE, "Limelight-Regular.ttf")
TEXT_FONT = r"C:\Windows\Fonts\segoeui.ttf"

BG = (24, 24, 28)
TILE_BG = (36, 36, 42)
LINE = (255, 255, 255)
DOT = (255, 60, 60)
ORANGE = (255, 140, 0)
GREY = (190, 190, 190)

TILE_W, TILE_H = 330, 400
COLS = 3
TITLE_H = 100
FOOTER_H = 50

CONNECTIONS = [
    (0, 1), (1, 2), (2, 3), (3, 4),
    (0, 5), (5, 6), (6, 7), (7, 8),
    (5, 9), (9, 10), (10, 11), (11, 12),
    (9, 13), (13, 14), (14, 15), (15, 16),
    (13, 17), (0, 17), (17, 18), (18, 19), (19, 20),
]

WRIST = (150, 270)
MCP = {"index": (120, 175), "middle": (148, 168), "ring": (176, 175), "pinky": (202, 190)}
SPLAY = {"index": -0.15, "middle": 0.0, "ring": 0.12, "pinky": 0.3}
LENGTHS = {"index": (38, 26, 22), "middle": (42, 28, 24),
           "ring": (38, 26, 22), "pinky": (30, 20, 18)}
THUMB = {
    "out": [(115, 245), (90, 218), (68, 196), (48, 178)],
    "in": [(115, 245), (126, 222), (138, 208), (150, 202)],
    "loop": [(115, 245), (95, 215), (82, 180), (76, 140)],
}


def finger_points(name, state):
    x, y = MCP[name]
    pts = [(x, y)]
    if state == "up":
        s = SPLAY[name]
        norm = (1 + s * s) ** 0.5
        for length in LENGTHS[name]:
            x += length * s / norm
            y -= length / norm
            pts.append((x, y))
    elif state == "loop":  # index curled round to meet the thumb
        pts += [(x - 12, y - 29), (x - 30, y - 47), (x - 44, y - 43)]
    else:  # curled into the palm
        pts += [(x, y - 26), (x, y - 10), (x, y + 8)]
    return pts


def hand(thumb, index, middle, ring, pinky):
    pts = [WRIST] + THUMB[thumb]
    for name, state in (("index", index), ("middle", middle),
                        ("ring", ring), ("pinky", pinky)):
        pts += finger_points(name, state)
    return pts


OPEN_HAND = hand("out", "up", "up", "up", "up")
SEARCH_HAND = hand("loop", "loop", "up", "up", "up")
MIDDLE_HAND = hand("in", "curl", "up", "curl", "curl")
HELP_HAND = hand("out", "up", "curl", "curl", "up")


def draw_skeleton(draw, pts, cx, cy, scale=1.0, mirror=False):
    m = -1 if mirror else 1
    p = [(cx + (x - 150) * scale * m, cy + (y - 170) * scale) for x, y in pts]
    for a, b in CONNECTIONS:
        draw.line([p[a], p[b]], fill=LINE, width=3)
    for x, y in p:
        draw.ellipse([x - 4, y - 4, x + 4, y + 4], fill=DOT)


def draw_arrow(draw, x_from, x_to, y):
    draw.line([(x_from, y), (x_to, y)], fill=ORANGE, width=6)
    d = 1 if x_to > x_from else -1
    draw.polygon([(x_to, y), (x_to - 18 * d, y - 12), (x_to - 18 * d, y + 12)], fill=ORANGE)


def load(path, size):
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()


def centered(draw, text, font, cx, y, fill):
    box = draw.textbbox((0, 0), text, font=font)
    draw.text((cx - (box[2] - box[0]) / 2 - box[0], y), text, font=font, fill=fill)


def tile(draw, col, row, title, sub, art, fonts):
    x0 = col * TILE_W
    y0 = TITLE_H + row * TILE_H
    draw.rounded_rectangle([x0 + 10, y0 + 10, x0 + TILE_W - 10, y0 + TILE_H - 10],
                           radius=18, fill=TILE_BG)
    art(draw, x0 + TILE_W // 2, y0 + 5)
    centered(draw, title, fonts["name"], x0 + TILE_W / 2, y0 + 305, ORANGE)
    centered(draw, sub, fonts["sub"], x0 + TILE_W / 2, y0 + 350, GREY)


def build_help_image():
    fonts = {"title": load(TITLE_FONT, 52), "name": load(TITLE_FONT, 26),
             "sub": load(TEXT_FONT, 17)}
    rows = 2
    img = Image.new("RGB", (TILE_W * COLS, TITLE_H + TILE_H * rows + FOOTER_H), BG)
    draw = ImageDraw.Draw(img)
    centered(draw, "Gesture Help", fonts["title"], img.width / 2, 20, ORANGE)

    def single(pts):
        return lambda d, x, y: draw_skeleton(d, pts, x, y + 170)

    def double(pts):
        def art(d, x, y):
            draw_skeleton(d, pts, x - 70, y + 185, 0.62)
            draw_skeleton(d, pts, x + 70, y + 185, 0.62, mirror=True)
        return art

    def swipe(d, x, y):
        draw_skeleton(d, OPEN_HAND, x, y + 190)
        draw_arrow(d, x + 110, x - 110, y + 30)

    tile(draw, 0, 0, "Open Palm", "Palm to the camera, fingers spread",
         single(OPEN_HAND), fonts)
    tile(draw, 1, 0, "Double Open Palm", "Both palms to the camera",
         double(OPEN_HAND), fonts)
    tile(draw, 2, 0, "Swipe", "Open hand, move it sideways (or slap)", swipe, fonts)
    tile(draw, 0, 1, "Search", "Thumb and index tip touch in a circle",
         single(SEARCH_HAND), fonts)
    tile(draw, 1, 1, "Middle Finger", "Only the middle finger up",
         single(MIDDLE_HAND), fonts)
    tile(draw, 2, 1, "Help", "Thumb, index, pinky up - both hands",
         double(HELP_HAND), fonts)

    centered(draw, "Scroll: mouse wheel, arrows or W/S. Close: the X or H",
             fonts["sub"], img.width / 2, img.height - 38, GREY)
    return cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)


if __name__ == "__main__":
    cv2.imshow("Help", build_help_image())
    cv2.waitKey(0)