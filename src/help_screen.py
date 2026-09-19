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
WHITE = (240, 240, 240)

TILE_W, TILE_H = 330, 480
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
FIST_HAND = hand("in", "curl", "curl", "curl", "curl")
TWO_FINGER_HAND = hand("in", "up", "up", "curl", "curl")
L_HAND = hand("out", "up", "curl", "curl", "curl")
PINKY_HAND = hand("in", "curl", "curl", "curl", "up")
RING_HAND = hand("in", "curl", "curl", "up", "up")


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


def draw_growth_arrows(draw, cx, cy, outward):
    """Four orange arrows around a hand: outward = toward the camera, inward = away."""
    for sx, sy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
        far = (cx + sx * 120, cy + sy * 95)
        near = (cx + sx * 80, cy + sy * 62)
        a, b = (near, far) if outward else (far, near)
        draw.line([a, b], fill=ORANGE, width=5)
        dx, dy = b[0] - a[0], b[1] - a[1]
        n = (dx * dx + dy * dy) ** 0.5
        ux, uy = dx / n, dy / n
        px, py = -uy, ux
        draw.polygon([b, (b[0] - 16 * ux + 9 * px, b[1] - 16 * uy + 9 * py),
                      (b[0] - 16 * ux - 9 * px, b[1] - 16 * uy - 9 * py)], fill=ORANGE)


def draw_arrow_v(draw, y_from, y_to, x):
    draw.line([(x, y_from), (x, y_to)], fill=ORANGE, width=6)
    d = 1 if y_to > y_from else -1
    draw.polygon([(x, y_to), (x - 12, y_to - 18 * d), (x + 12, y_to - 18 * d)], fill=ORANGE)


def load(path, size):
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()


def centered(draw, text, font, cx, y, fill):
    box = draw.textbbox((0, 0), text, font=font)
    draw.text((cx - (box[2] - box[0]) / 2 - box[0], y), text, font=font, fill=fill)


def tile(draw, col, row, title, how, does, art, fonts):
    """One gesture: a picture, its name, how to make it, and what it does."""
    x0 = col * TILE_W
    y0 = TITLE_H + row * TILE_H
    cx = x0 + TILE_W / 2
    draw.rounded_rectangle([x0 + 10, y0 + 10, x0 + TILE_W - 10, y0 + TILE_H - 10],
                           radius=18, fill=TILE_BG)
    art(draw, x0 + TILE_W // 2, y0 + 5)
    centered(draw, title, fonts["name"], cx, y0 + 295, ORANGE)
    y = y0 + 335
    for line in how.split("\n"):
        centered(draw, line, fonts["sub"], cx, y, GREY)
        y += 22
    y += 12
    for i, line in enumerate(does.split("\n")):
        centered(draw, ("\u2192 " if i == 0 else "") + line, fonts["sub"], cx, y, WHITE)
        y += 22


def build_help_image():
    fonts = {"title": load(TITLE_FONT, 52), "name": load(TITLE_FONT, 26),
             "sub": load(TEXT_FONT, 17)}
    rows = 5
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
        draw_arrow(d, x, x - 120, y + 30)
        draw_arrow(d, x, x + 120, y + 30)

    def scroll(pts, up):
        def art(d, x, y):
            draw_skeleton(d, pts, x - 25, y + 190)
            if up:
                draw_arrow_v(d, y + 250, y + 80, x + 105)
            else:
                draw_arrow_v(d, y + 80, y + 250, x + 105)
        return art

    def growth(outward):
        def art(d, x, y):
            draw_skeleton(d, OPEN_HAND, x, y + 175, 0.7)
            draw_growth_arrows(d, x, y + 175, outward)
        return art

    tile(draw, 0, 0, "Open Palm", "Palm to the camera,\nfingers spread",
         "Just shows the label,\nno action", single(OPEN_HAND), fonts)
    tile(draw, 1, 0, "Double Open Palm", "Both palms to the camera",
         "Just shows the label,\nno action", double(OPEN_HAND), fonts)
    tile(draw, 2, 0, "Swipe", "Open hand moved sideways\n(or a quick slap)",
         "Left to right: next app\nRight to left: previous app\nIn Task View: moves the pick",
         swipe, fonts)
    tile(draw, 0, 1, "Search", "Thumb and index touch\nin a circle",
         "Opens Windows Search and listens:\nsay it and it is typed for you,\nthen make the Enter sign", single(SEARCH_HAND), fonts)
    tile(draw, 1, 1, "Middle Finger", "Only the middle finger up",
         "Shows a rude reply\non screen", single(MIDDLE_HAND), fonts)
    tile(draw, 2, 1, "Help", "Thumb, index and pinky up\non both hands",
         "Opens this Help window", double(HELP_HAND), fonts)
    tile(draw, 0, 2, "Fist", "Hold a closed fist",
         "Opens Task View\n(Win+Tab)", single(FIST_HAND), fonts)
    tile(draw, 1, 2, "Push", "Open hands toward\nthe camera",
         "1 hand: minimizes the window\n2 hands: closes the window\nIn Task View: cancels",
         growth(True), fonts)
    tile(draw, 2, 2, "Pull", "Open hand back away\nfrom the camera",
         "In Task View: opens the\nselected window", growth(False), fonts)
    tile(draw, 0, 3, "Scroll Up", "Thumb and index out in an L,\nother fingers curled",
         "Slowly scrolls the window\nin front up while held", scroll(L_HAND, True), fonts)
    tile(draw, 1, 3, "Scroll Down", "Index and middle finger up,\nring and pinky curled",
         "Slowly scrolls the window\nin front down while held", scroll(TWO_FINGER_HAND, False), fonts)

    tile(draw, 2, 3, "Escape", "Only the pinky up",
         "Presses the Esc key", single(PINKY_HAND), fonts)

    tile(draw, 1, 4, "Enter", "Ring and pinky up,\nindex and middle curled",
         "Presses the Enter key", single(RING_HAND), fonts)

    centered(draw, "Scroll: mouse wheel, arrows or W/S. Close: the X or H",
             fonts["sub"], img.width / 2, img.height - 38, GREY)
    return cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)


if __name__ == "__main__":
    cv2.imshow("Help", build_help_image())
    cv2.waitKey(0)