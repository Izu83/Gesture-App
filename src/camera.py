import os
import time
import urllib.request
from collections import deque

import cv2
import mediapipe as mp
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from help_screen import build_help_image
from mediapipe.tasks.python import BaseOptions, vision

MODEL_PATH = os.path.join(os.path.dirname(__file__), "hand_landmarker.task")
MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
    "hand_landmarker/float16/latest/hand_landmarker.task"
)

WRIST = 0
FINGERS = [(8, 6), (12, 10), (16, 14), (20, 18)]  # (tip, pip) for index..pinky
THUMB_TIP, THUMB_IP, PINKY_MCP = 4, 3, 17
INDEX_MCP = 5

TEXT_COLOR = (255, 140, 0)  # orange (RGB)
FONT_CANDIDATES = [
    os.path.join(os.path.dirname(__file__), "Limelight-Regular.ttf"),
    r"C:\Windows\Fonts\Inkfree.ttf",
    r"C:\Windows\Fonts\segoescb.ttf",
    r"C:\Windows\Fonts\comicbd.ttf",
]
SWIPE_WINDOW_S = 0.5  # time window the motion is measured over
SWIPE_MIN_DX = 0.15  # fraction of frame width the hand must travel
SLAP_WINDOW_S = 0.25 # a slap is a shorter, faster motion, open hand or not
SLAP_MIN_DX = 0.10
HELP_VIEW_H = 640  # visible height of the Help window; the rest scrolls
HELP_SCROLL_STEP = 60
HELP_HOLD_S = 0.5  # how long both hands must hold the Help sign
SWIPE_SHOW_S = 1.0  # how long the swipe text stays on screen


def ensure_model():
    if not os.path.exists(MODEL_PATH):
        print("Downloading hand model (first run only)...")
        urllib.request.urlretrieve(MODEL_URL, MODEL_PATH)


def dist(a, b):
    return ((a.x - b.x) ** 2 + (a.y - b.y) ** 2) ** 0.5


def is_open_palm(lm, label):
    # Four fingers extended: tip is farther from the wrist than the middle joint.
    for tip, pip in FINGERS:
        if dist(lm[tip], lm[WRIST]) <= dist(lm[pip], lm[WRIST]):
            return False
    # Thumb extended: tip is farther from the pinky base than the thumb joint.
    if dist(lm[THUMB_TIP], lm[PINKY_MCP]) <= dist(lm[THUMB_IP], lm[PINKY_MCP]):
        return False
    # Palm faces the camera (not the back of the hand), using the frame mirrored.
    v1 = (lm[INDEX_MCP].x - lm[WRIST].x, lm[INDEX_MCP].y - lm[WRIST].y)
    v2 = (lm[PINKY_MCP].x - lm[WRIST].x, lm[PINKY_MCP].y - lm[WRIST].y)
    cross = v1[0] * v2[1] - v1[1] * v2[0]
    return cross < 0 if label == "Right" else cross > 0


def is_middle_finger(lm):
    def extended(tip, pip):
        return dist(lm[tip], lm[WRIST]) > dist(lm[pip], lm[WRIST])

    return (
        extended(12, 10)
        and not extended(8, 6)
        and not extended(16, 14)
        and not extended(20, 18)
    )


help_scroll = 0


def on_help_mouse(event, x, y, flags, param):
    global help_scroll
    if event == cv2.EVENT_MOUSEWHEEL:
        help_scroll += -HELP_SCROLL_STEP if flags > 0 else HELP_SCROLL_STEP


def show_help(help_image):
    global help_scroll
    max_scroll = max(0, help_image.shape[0] - HELP_VIEW_H)
    help_scroll = min(max(help_scroll, 0), max_scroll)
    cv2.imshow("Help", help_image[help_scroll:help_scroll + HELP_VIEW_H])


def is_help_sign(lm):
    # Thumb, index and pinky up; middle and ring curled.
    def extended(tip, pip):
        return dist(lm[tip], lm[WRIST]) > dist(lm[pip], lm[WRIST])

    thumb_out = dist(lm[THUMB_TIP], lm[PINKY_MCP]) > dist(lm[THUMB_IP], lm[PINKY_MCP])
    return (
        thumb_out
        and extended(8, 6)
        and extended(20, 18)
        and not extended(12, 10)
        and not extended(16, 14)
    )


def is_search(lm):
    # Thumb and index tips touching in a circle; the other fingers are ignored.
    hand_size = dist(lm[WRIST], lm[9])
    touching = dist(lm[THUMB_TIP], lm[8]) < 0.3 * hand_size
    # Index is curled into a loop, not tucked into a fist.
    looped = dist(lm[8], lm[WRIST]) > 0.8 * dist(lm[INDEX_MCP], lm[WRIST])
    return touching and looped


def load_font(size):
    for path in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def draw_text_bottom(frame, text, font):
    img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(img)
    w, h = img.size
    box = draw.textbbox((0, 0), text, font=font)
    x = (w - (box[2] - box[0])) // 2 - box[0]
    y = h - (box[3] - box[1]) - 30 - box[1]
    draw.text((x, y), text, font=font, fill=TEXT_COLOR,
              stroke_width=2, stroke_fill=(0, 0, 0))
    return cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)


def is_hand_open(lm):
    return all(
        dist(lm[tip], lm[WRIST]) > dist(lm[pip], lm[WRIST])
        for tip, pip in FINGERS
    )


def _horizontal_move(samples, min_dx):
    if len(samples) < 3:
        return None
    dx = samples[-1][1] - samples[0][1]
    dy = samples[-1][2] - samples[0][2]
    if abs(dx) < min_dx or abs(dy) > abs(dx) * 0.8:
        return None
    return dx


def detect_swipe(history, now):
    """Return "Right Swipe" (moving right to left), "Left Swipe" or None.

    history holds (time, x, y, hand_open). A swipe is a wide move with an open
    hand; a slap is a short, fast move that counts even if the hand is not open.
    """
    while history and now - history[0][0] > SWIPE_WINDOW_S:
        history.popleft()

    dx = None
    if history[-1][3]:
        dx = _horizontal_move(history, SWIPE_MIN_DX)
    if dx is None:
        recent = [s for s in history if now - s[0] <= SLAP_WINDOW_S]
        dx = _horizontal_move(recent, SLAP_MIN_DX)
    if dx is None:
        return None
    history.clear()
    return "Right Swipe" if dx < 0 else "Left Swipe"


def draw_hand(frame, lm):
    h, w = frame.shape[:2]
    pts = [(int(p.x * w), int(p.y * h)) for p in lm]
    for c in vision.HandLandmarksConnections.HAND_CONNECTIONS:
        cv2.line(frame, pts[c.start], pts[c.end], (255, 255, 255), 2)
    for p in pts:
        cv2.circle(frame, p, 4, (0, 0, 255), -1)


def main():
    global help_scroll
    ensure_model()
    landmarker = vision.HandLandmarker.create_from_options(
        vision.HandLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=MODEL_PATH),
            running_mode=vision.RunningMode.VIDEO,
            num_hands=2,
        )
    )

    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("Could not open camera")
        return

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

    font = load_font(72)
    swipe_history = deque()
    swipe_text = ""
    swipe_until = 0.0
    help_image = None
    help_open = False
    help_armed = True
    help_since = None
    timestamp_ms = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        frame = cv2.flip(frame, 1)  # mirror view

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        timestamp_ms += 33
        result = landmarker.detect_for_video(image, timestamp_ms)

        open_palms = 0
        middle_finger = False
        search = False
        help_hands = 0
        for lm, handed in zip(result.hand_landmarks, result.handedness):
            draw_hand(frame, lm)
            if is_help_sign(lm):
                help_hands += 1
            if is_search(lm):
                search = True
            if is_middle_finger(lm):
                middle_finger = True
            if is_open_palm(lm, handed[0].category_name):
                open_palms += 1

        now = time.time()
        if result.hand_landmarks:
            lm = result.hand_landmarks[0]
            cx = sum(lm[i].x for i in (0, 5, 9, 13, 17)) / 5
            cy = sum(lm[i].y for i in (0, 5, 9, 13, 17)) / 5
            swipe_history.append((now, cx, cy, is_hand_open(lm)))
            swipe = detect_swipe(swipe_history, now)
            if swipe:
                swipe_text = swipe
                swipe_until = now + SWIPE_SHOW_S
        else:
            swipe_history.clear()

        # Both hands making the Help sign (held briefly) opens the Help window.
        if help_hands >= 2:
            help_since = help_since or now
            if help_armed and not help_open and now - help_since >= HELP_HOLD_S:
                if help_image is None:
                    help_image = build_help_image()
                help_scroll = 0
                show_help(help_image)
                cv2.setMouseCallback("Help", on_help_mouse)
                help_open = True
                help_armed = False
        else:
            help_since = None
            help_armed = True

        gesture = ""
        if middle_finger:
            gesture = "Fuck you too"
        elif now < swipe_until:
            gesture = swipe_text
        elif help_hands >= 2:
            gesture = "Help"
        elif search:
            gesture = "Search"
        elif open_palms >= 2:
            gesture = "Double Open Palm"
        elif open_palms == 1:
            gesture = "Open Palm"

        if gesture:
            frame = draw_text_bottom(frame, gesture, font)

        cv2.imshow("Camera", frame)
        raw_key = cv2.waitKeyEx(1)
        key = raw_key & 0xFF
        if key in (27, ord("q")):
            break
        if key == ord("h") and help_open:
            cv2.destroyWindow("Help")
            help_open = False
        if help_open and cv2.getWindowProperty("Help", cv2.WND_PROP_VISIBLE) < 1:
            help_open = False
        if help_open:
            if raw_key == 2621440 or key == ord("s"):  # Down arrow / S
                help_scroll += HELP_SCROLL_STEP
            elif raw_key == 2490368 or key == ord("w"):  # Up arrow / W
                help_scroll -= HELP_SCROLL_STEP
            show_help(help_image)
        if cv2.getWindowProperty("Camera", cv2.WND_PROP_VISIBLE) < 1:
            break

    landmarker.close()
    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()