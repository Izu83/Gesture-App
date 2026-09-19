import os
import time
import urllib.request
from collections import deque

import cv2
import mediapipe as mp
import numpy as np
from PIL import Image, ImageDraw, ImageFont
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
        for lm, handed in zip(result.hand_landmarks, result.handedness):
            draw_hand(frame, lm)
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

        gesture = ""
        if middle_finger:
            gesture = "Fuck you too"
        elif now < swipe_until:
            gesture = swipe_text
        elif open_palms >= 2:
            gesture = "Double Open Palm"
        elif open_palms == 1:
            gesture = "Open Palm"

        if gesture:
            frame = draw_text_bottom(frame, gesture, font)

        cv2.imshow("Camera", frame)
        key = cv2.waitKey(1) & 0xFF
        if key in (27, ord("q")):
            break
        if cv2.getWindowProperty("Camera", cv2.WND_PROP_VISIBLE) < 1:
            break

    landmarker.close()
    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()