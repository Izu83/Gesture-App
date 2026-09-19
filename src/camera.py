import ctypes
import os
import time
import urllib.request
from collections import deque

import cv2
import mediapipe as mp
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from help_screen import build_help_image
import commands
import commands
from voice import VoiceTyper, type_text, type_text
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

TEXT_COLOR = (251, 54, 64)  # Imperial #FB3640 (RGB)
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
PUSH_WINDOW_S = 0.5  # time window the hand growth is measured over
PUSH_GROWTH = 1.35  # hand must get this much bigger (moving toward the camera)
PUSH_MAX_SHIFT = 0.10  # a push stays in place; raising your hands does not count
STABLE_S = 0.3  # hands must be tracked steadily this long before swipes or pushes count
SCROLL_RATE = 240  # wheel units per second while a scroll sign is held (120 = one notch)
SCROLL_HOLD_S = 0.3  # the sign must be held this long before it starts scrolling
SCROLL_SHOW_S = 0.3
ESCAPE_HOLD_S = 0.3  # how long the pinky sign must be held to press Esc
ENTER_HOLD_S = 0.3  # how long the ring + pinky sign must be held to press Enter
PUSH_SHOW_S = 1.0
FIST_HOLD_S = 0.5  # how long the fist must be held to trigger
SEARCH_HOLD_S = 0.3  # how long the Search sign must be held to trigger
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


def foreground_window():
    """The window in front, or None for the desktop and taskbar."""
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    cls = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, cls, 256)
    if cls.value in ("Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
        return None
    return hwnd


def minimize_foreground_window():
    hwnd = foreground_window()
    if hwnd:
        ctypes.windll.user32.ShowWindow(hwnd, 6)  # SW_MINIMIZE


def close_foreground_window():
    # WM_CLOSE asks the program to close, so it can still ask to save your work.
    hwnd = foreground_window()
    if hwnd:
        ctypes.windll.user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE


def camera_window_minimized():
    hwnd = ctypes.windll.user32.FindWindowW(None, "Camera")
    return bool(hwnd and ctypes.windll.user32.IsIconic(hwnd))


def task_view_active():
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return False
    title = ctypes.create_unicode_buffer(256)
    cls = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(hwnd, title, 256)
    user32.GetClassNameW(hwnd, cls, 256)
    return title.value == "Task View" or cls.value in (
        "XamlExplorerHostIslandWindow", "MultitaskingViewFrame")


def switch_app(swipe):
    # Right Swipe -> next app (Alt+Tab), Left Swipe -> previous (Alt+Shift+Tab).
    # While Task View is open, the swipes move its selection with the arrow keys.
    VK_MENU, VK_SHIFT, VK_TAB, KEYUP = 0x12, 0x10, 0x09, 0x0002
    VK_LEFT, VK_RIGHT, EXTENDED = 0x25, 0x27, 0x0001
    keybd_event = ctypes.windll.user32.keybd_event
    if task_view_active():
        vk = VK_LEFT if swipe == "Left Swipe" else VK_RIGHT
        keybd_event(vk, 0, EXTENDED, 0)
        keybd_event(vk, 0, EXTENDED | KEYUP, 0)
        return
    keybd_event(VK_MENU, 0, 0, 0)
    if swipe == "Left Swipe":
        keybd_event(VK_SHIFT, 0, 0, 0)
    keybd_event(VK_TAB, 0, 0, 0)
    keybd_event(VK_TAB, 0, KEYUP, 0)
    if swipe == "Left Swipe":
        keybd_event(VK_SHIFT, 0, KEYUP, 0)
    keybd_event(VK_MENU, 0, KEYUP, 0)


def hand_scale(lm, w, h):
    def d(a, b):
        return (((lm[a].x - lm[b].x) * w) ** 2 + ((lm[a].y - lm[b].y) * h) ** 2) ** 0.5

    return (d(WRIST, 9) + d(5, 17)) / 2


def is_two_fingers(lm):
    # Index and middle fingers up, ring and pinky curled; the thumb is ignored.
    return (
        dist(lm[8], lm[WRIST]) > dist(lm[6], lm[WRIST])
        and dist(lm[12], lm[WRIST]) > dist(lm[10], lm[WRIST])
        and dist(lm[16], lm[WRIST]) < dist(lm[13], lm[WRIST])
        and dist(lm[20], lm[WRIST]) < dist(lm[17], lm[WRIST])
    )


def scroll_windows(amount):
    # Mouse wheel: positive scrolls up, negative scrolls down (120 = one notch).
    ctypes.windll.user32.mouse_event(0x0800, 0, 0, amount, 0)  # MOUSEEVENTF_WHEEL


def is_l_sign(lm):
    # "L" shape: thumb and index out, middle, ring and pinky curled.
    thumb_out = dist(lm[THUMB_TIP], lm[PINKY_MCP]) > dist(lm[THUMB_IP], lm[PINKY_MCP])
    return (
        thumb_out
        and dist(lm[8], lm[WRIST]) > dist(lm[6], lm[WRIST])
        and dist(lm[12], lm[WRIST]) < dist(lm[9], lm[WRIST])
        and dist(lm[16], lm[WRIST]) < dist(lm[13], lm[WRIST])
        and dist(lm[20], lm[WRIST]) < dist(lm[17], lm[WRIST])
    )


def is_pinky_only(lm):
    # Only the pinky up; index, middle and ring curled; the thumb is ignored.
    return (
        dist(lm[20], lm[WRIST]) > dist(lm[18], lm[WRIST])
        and dist(lm[8], lm[WRIST]) < dist(lm[5], lm[WRIST])
        and dist(lm[12], lm[WRIST]) < dist(lm[9], lm[WRIST])
        and dist(lm[16], lm[WRIST]) < dist(lm[13], lm[WRIST])
    )


def is_ring_pinky(lm):
    # Ring and pinky up; index and middle curled; the thumb is ignored.
    return (
        dist(lm[16], lm[WRIST]) > dist(lm[14], lm[WRIST])
        and dist(lm[20], lm[WRIST]) > dist(lm[18], lm[WRIST])
        and dist(lm[8], lm[WRIST]) < dist(lm[5], lm[WRIST])
        and dist(lm[12], lm[WRIST]) < dist(lm[9], lm[WRIST])
    )


def camera_window_in_front():
    hwnd = ctypes.windll.user32.FindWindowW(None, "Camera")
    return bool(hwnd) and hwnd == ctypes.windll.user32.GetForegroundWindow()


def is_fist(lm):
    return all(
        dist(lm[tip], lm[WRIST]) < 0.9 * dist(lm[mcp], lm[WRIST])
        for tip, mcp in ((8, 5), (12, 9), (16, 13), (20, 17))
    )


def press_key(vk):
    keybd_event = ctypes.windll.user32.keybd_event
    keybd_event(vk, 0, 0, 0)
    keybd_event(vk, 0, 0x0002, 0)  # key up


def open_task_view():
    # Press Win+Tab, which opens Windows Task View.
    VK_LWIN, VK_TAB, KEYUP = 0x5B, 0x09, 0x0002
    keybd_event = ctypes.windll.user32.keybd_event
    keybd_event(VK_LWIN, 0, 0, 0)
    keybd_event(VK_TAB, 0, 0, 0)
    keybd_event(VK_TAB, 0, KEYUP, 0)
    keybd_event(VK_LWIN, 0, KEYUP, 0)


def detect_push(history, now):
    """Return (hand_count, "push" or "pull") for a steady move, or None.

    history holds (time, hand_count, mean_hand_size, center_x, center_y) for a
    steady run of frames. Pushing makes the open hands grow quickly in the image
    and pulling back makes them shrink, both while staying in place; raising your
    hands moves them, so it does not count.
    """
    while history and now - history[0][0] > PUSH_WINDOW_S:
        history.popleft()
    if len(history) < 3 or history[-1][0] - history[0][0] < 0.2:
        return None
    first, last = history[0], history[-1]
    shift = ((last[3] - first[3]) ** 2 + (last[4] - first[4]) ** 2) ** 0.5
    growth = last[2] / first[2]
    if shift > PUSH_MAX_SHIFT:
        return None
    if growth >= PUSH_GROWTH:
        direction = "push"
    elif growth <= 1 / PUSH_GROWTH:
        direction = "pull"
    else:
        return None
    history.clear()
    return last[1], direction


def open_windows_search():
    # Press Win+S, which opens Windows Search.
    VK_LWIN, VK_S, KEYUP = 0x5B, 0x53, 0x0002
    keybd_event = ctypes.windll.user32.keybd_event
    keybd_event(VK_LWIN, 0, 0, 0)
    keybd_event(VK_S, 0, 0, 0)
    keybd_event(VK_S, 0, KEYUP, 0)
    keybd_event(VK_LWIN, 0, KEYUP, 0)


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
    looped = dist(lm[8], lm[WRIST]) > dist(lm[INDEX_MCP], lm[WRIST])
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
              stroke_width=2, stroke_fill=(0, 15, 8))
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
    growth = samples[-1][4] / samples[0][4]
    if abs(dx) < min_dx or abs(dy) > abs(dx) * 0.5 or not 0.8 < growth < 1.25:
        return None
    return dx


def detect_swipe(history, now):
    """Return "Right Swipe" (moving left to right), "Left Swipe" or None.

    history holds (time, x, y, hand_open, hand_size) for a steady single hand.
    A swipe is a wide, mostly sideways move with an open hand at a steady
    distance from the camera; a slap is a shorter, faster move that counts even
    if the hand is not open.
    """
    while history and now - history[0][0] > SWIPE_WINDOW_S:
        history.popleft()
    if not history:
        return None

    dx = None
    if history[-1][3]:
        dx = _horizontal_move(history, SWIPE_MIN_DX)
    if dx is None:
        recent = [s for s in history if now - s[0] <= SLAP_WINDOW_S]
        dx = _horizontal_move(recent, SLAP_MIN_DX)
    if dx is None:
        return None
    history.clear()
    return "Right Swipe" if dx > 0 else "Left Swipe"


def draw_hand(frame, lm):
    h, w = frame.shape[:2]
    pts = [(int(p.x * w), int(p.y * h)) for p in lm]
    for c in vision.HandLandmarksConnections.HAND_CONNECTIONS:
        cv2.line(frame, pts[c.start], pts[c.end], (255, 255, 255), 2)
    for p in pts:
        cv2.circle(frame, p, 4, (64, 54, 251), -1)  # Imperial (BGR)


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
    push_history = deque()
    prev_n_hands = 0
    escape_armed = True
    escape_since = None
    escape_until = 0.0
    enter_armed = True
    enter_since = None
    enter_until = 0.0
    scroll_active = None
    scroll_since = 0.0
    last_scroll_t = 0.0
    scroll_accum = 0.0
    scroll_text = ""
    scroll_until = 0.0
    n_changed_at = 0.0
    fist_armed = True
    fist_since = None
    fist_until = 0.0
    push_until = 0.0
    push_text = ""
    search_armed = True
    search_since = None
    help_image = None
    help_open = False
    help_armed = True
    help_since = None
    def handle_voice(text):
        # Commands (open an app or site) run right away; anything else is typed
        # into Windows Search, and you finish it with the Enter sign.
        def type_into_search(words):
            open_windows_search()
            time.sleep(0.7)  # let the search box open
            type_text(words)

        return commands.route(text, type_into_search)

    commands.preload_apps()  # lists installed apps in the background
    voice = VoiceTyper(handler=handle_voice, vocabulary=commands.vocabulary)
    voice.preload()  # loads the speech model in the background
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
        fist = False
        for lm, handed in zip(result.hand_landmarks, result.handedness):
            draw_hand(frame, lm)
            if is_fist(lm) and not is_search(lm):
                fist = True
            if is_help_sign(lm):
                help_hands += 1
            if is_search(lm):
                search = True
            if is_middle_finger(lm):
                middle_finger = True
            if is_open_palm(lm, handed[0].category_name):
                open_palms += 1

        now = time.time()
        fh, fw = frame.shape[:2]
        n_hands = len(result.hand_landmarks)
        if n_hands != prev_n_hands:  # a hand appeared or vanished: start over
            prev_n_hands = n_hands
            n_changed_at = now
            swipe_history.clear()
            push_history.clear()
        stable = now - n_changed_at >= STABLE_S

        # Static scroll signs, held for slow scrolling: the "L" shape (thumb + index)
        # scrolls up, index + middle finger scrolls down.
        scroll_dir = None
        if n_hands == 1 and stable:
            lm = result.hand_landmarks[0]
            if is_l_sign(lm):
                scroll_dir = "up"
            elif is_two_fingers(lm):
                scroll_dir = "down"
        if scroll_dir:
            if scroll_dir != scroll_active:
                scroll_active = scroll_dir
                scroll_since = now
                scroll_accum = 0.0
            elif now - scroll_since >= SCROLL_HOLD_S:
                sign = 1 if scroll_dir == "up" else -1
                scroll_accum += sign * SCROLL_RATE * (now - last_scroll_t)
                amount = int(scroll_accum)
                if abs(amount) >= 40:
                    scroll_windows(amount)
                    scroll_accum -= amount
                scroll_text = "Scroll Up" if scroll_dir == "up" else "Scroll Down"
                scroll_until = now + SCROLL_SHOW_S
            last_scroll_t = now
        else:
            scroll_active = None
            scroll_accum = 0.0

        # Only the pinky up (held briefly) presses Esc, once per sign. It is skipped
        # while the Camera window is in front, because Esc would quit this app.
        if n_hands == 1 and stable and is_pinky_only(result.hand_landmarks[0]):
            escape_since = escape_since or now
            if escape_armed and now - escape_since >= ESCAPE_HOLD_S:
                if not camera_window_in_front():
                    press_key(0x1B)  # VK_ESCAPE
                escape_armed = False
                escape_until = now + PUSH_SHOW_S
        else:
            escape_since = None
            escape_armed = True

        # Ring and pinky up (held briefly) presses Enter, once per sign.
        if n_hands == 1 and stable and is_ring_pinky(result.hand_landmarks[0]):
            enter_since = enter_since or now
            if enter_armed and now - enter_since >= ENTER_HOLD_S:
                press_key(0x0D)  # VK_RETURN
                enter_armed = False
                enter_until = now + PUSH_SHOW_S
        else:
            enter_since = None
            enter_armed = True

        # Swipes use a single, steadily tracked hand.
        if n_hands == 1 and stable and scroll_dir is None:
            lm = result.hand_landmarks[0]
            cx = sum(lm[i].x for i in (0, 5, 9, 13, 17)) / 5
            cy = sum(lm[i].y for i in (0, 5, 9, 13, 17)) / 5
            swipe_history.append(
                (now, cx, cy, is_hand_open(lm), hand_scale(lm, fw, fh)))
            swipe = detect_swipe(swipe_history, now)
            if swipe and now >= swipe_until:  # cooldown stops the return motion firing
                switch_app(swipe)
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

        # Holding a closed fist (all fingers down) opens Task View (Win+Tab).
        # Search needs the index finger looped out, so the two never overlap.
        if fist:
            fist_since = fist_since or now
            if fist_armed and now - fist_since >= FIST_HOLD_S:
                open_task_view()
                fist_armed = False
                fist_until = now + PUSH_SHOW_S
        else:
            fist_since = None
            fist_armed = True

        # Pushing open hands toward the camera: two hands close the window in front, one minimizes it.
        if stable and n_hands and all(is_hand_open(lm) for lm in result.hand_landmarks):
            sizes = [hand_scale(lm, fw, fh) for lm in result.hand_landmarks]
            cxs = [sum(lm[i].x for i in (0, 5, 9, 13, 17)) / 5 for lm in result.hand_landmarks]
            cys = [sum(lm[i].y for i in (0, 5, 9, 13, 17)) / 5 for lm in result.hand_landmarks]
            push_history.append((now, n_hands, sum(sizes) / n_hands,
                                 sum(cxs) / n_hands, sum(cys) / n_hands))
            pushed = detect_push(push_history, now)
            if pushed and task_view_active():
                # In Task View: push cancels (Esc), pull opens the selection (Enter).
                if pushed[1] == "push":
                    press_key(0x1B)  # VK_ESCAPE
                    push_text = "Push"
                else:
                    press_key(0x0D)  # VK_RETURN
                    push_text = "Pull"
                push_until = now + PUSH_SHOW_S
            elif pushed and pushed[1] == "push":
                push_until = now + PUSH_SHOW_S
                if pushed[0] == 2:
                    push_text = "Double Push"
                    close_foreground_window()
                else:
                    push_text = "Push"
                    minimize_foreground_window()
        else:
            push_history.clear()

        # Holding the Search sign briefly opens Windows Search (once per sign).
        if search and not middle_finger and help_hands < 2:
            search_since = search_since or now
            if search_armed and now - search_since >= SEARCH_HOLD_S:
                voice.listen_and_type()  # say a command or a search
                search_armed = False
        else:
            search_since = None
            search_armed = True

        gesture = ""
        if middle_finger:
            gesture = "Fuck you too"
        elif voice.label:
            gesture = voice.label
        elif now < enter_until:
            gesture = "Enter"
        elif now < escape_until:
            gesture = "Escape"
        elif now < scroll_until:
            gesture = scroll_text
        elif now < fist_until:
            gesture = "Fist"
        elif now < push_until:
            gesture = push_text
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
        if (cv2.getWindowProperty("Camera", cv2.WND_PROP_VISIBLE) < 1
                and not camera_window_minimized()):
            break

    voice.close()
    landmarker.close()
    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()