"""Air mouse: your index fingertip moves the cursor, your pinky clicks and drags.

Point (index finger only) and hold to start; make a fist and hold to stop.
Raise your pinky to click (keep it up to drag) and your middle finger for a right click.
While it is on, the other gestures are switched off so they cannot fire by accident.
"""
import ctypes
import math
import time
from collections import deque

import cv2

user32 = ctypes.windll.user32
user32.SetProcessDPIAware()  # real pixels, not scaled ones
SCREEN_W = user32.GetSystemMetrics(0)
SCREEN_H = user32.GetSystemMetrics(1)

# The part of the camera picture (x0, x1, y0, y1) that maps onto the whole screen,
# so you can reach the screen edges without leaving the camera view.
REGION = (0.2, 0.8, 0.15, 0.75)

POINT_HOLD_S = 0.8  # hold the pointing sign this long to start
EXIT_HOLD_S = 0.7  # hold a fist this long to stop
AUTO_EXIT_S = 6.0  # stop by itself if your hand is out of view this long
PINKY_ON = 1.35  # pinky tip this far from the wrist, compared with its base = pinky up (pressed)
PINKY_OFF = 1.15  # ...and released when it drops below this
SETTLE_BACK_S = 0.12  # on a press, put the cursor back to where it was this long ago
CLICK_FREEZE_S = 0.15  # ...and hold it there this long, so raising the pinky cannot drag it
RIGHT_CLICK_HOLD_S = 0.25  # hold the middle finger up this long for a right click
DRAG_AFTER_S = 0.4  # a press held this long is shown as "Dragging"
FLASH_S = 0.6

WRIST, THUMB_TIP, THUMB_IP, INDEX_TIP = 0, 4, 3, 8
PINKY_MCP, PINKY_TIP = 17, 20


def _dist(a, b):
    return ((a.x - b.x) ** 2 + (a.y - b.y) ** 2) ** 0.5


def _extended(lm, tip, pip):
    return _dist(lm[tip], lm[WRIST]) > _dist(lm[pip], lm[WRIST])


def _curled(lm, tip, mcp):
    return _dist(lm[tip], lm[WRIST]) < _dist(lm[mcp], lm[WRIST])


def _thumb_out(lm):
    return _dist(lm[THUMB_TIP], lm[PINKY_MCP]) > _dist(lm[THUMB_IP], lm[PINKY_MCP])


def is_pointing(lm):
    """Only the index finger up, thumb tucked in: the sign that starts mouse mode."""
    return (
        not _thumb_out(lm)
        and _extended(lm, 8, 6)
        and _curled(lm, 12, 9)
        and _curled(lm, 16, 13)
        and _curled(lm, 20, 17)
    )


def is_fist(lm):
    return all(_dist(lm[tip], lm[WRIST]) < 0.9 * _dist(lm[mcp], lm[WRIST])
               for tip, mcp in ((8, 5), (12, 9), (16, 13), (20, 17)))


# --- the actual mouse (kept apart so it can be swapped out in tests) --------------------

def move_cursor(x, y):
    user32.SetCursorPos(int(x), int(y))


def button(name, down):
    flags = {("left", True): 0x0002, ("left", False): 0x0004,
             ("right", True): 0x0008, ("right", False): 0x0010}[(name, down)]
    user32.mouse_event(flags, 0, 0, 0, 0)


class Smoother:
    """Smooths the cursor: steady when you move slowly, quick when you move fast."""

    def __init__(self):
        self.x = self.y = None

    def reset(self):
        self.x = self.y = None

    def __call__(self, x, y):
        if self.x is None:
            self.x, self.y = x, y
        else:
            speed = math.hypot(x - self.x, y - self.y)  # pixels since the last frame
            alpha = min(0.85, 0.12 + speed / 300)
            self.x += (x - self.x) * alpha
            self.y += (y - self.y) * alpha
        return self.x, self.y


class AirMouse:
    def __init__(self):
        self.active = False
        self._smooth = Smoother()
        self._pressed = False
        self._press_since = 0.0
        self._freeze_until = 0.0
        self._trail = deque()  # recent cursor positions: (time, x, y)
        self._middle_since = None
        self._right_armed = True
        self._fist_since = None
        self._last_seen = 0.0
        self._flash = ""
        self._flash_until = 0.0

    def start(self, now):
        self.active = True
        self._last_seen = now
        self._smooth.reset()
        self._pressed = False
        self._trail.clear()
        self._middle_since = self._fist_since = None
        self._right_armed = True
        self._flash, self._flash_until = "Mouse mode on", now + FLASH_S

    def stop(self, now):
        if self._pressed:
            button("left", False)
        self.active = False
        self._pressed = False
        self._flash, self._flash_until = "Mouse mode off", now + FLASH_S

    @property
    def label(self):
        if time.time() < self._flash_until:
            return self._flash
        if self._pressed and time.time() - self._press_since >= DRAG_AFTER_S:
            return "Dragging"
        return "Mouse mode" if self.active else ""

    def update(self, hands, now):
        if not self.active:
            return
        if not hands:
            if now - self._last_seen > AUTO_EXIT_S:
                self.stop(now)
            return
        lm = hands[0]
        self._last_seen = now

        # A fist stops mouse mode.
        if is_fist(lm):
            self._fist_since = self._fist_since or now
            if now - self._fist_since >= EXIT_HOLD_S:
                self.stop(now)
            return
        self._fist_since = None

        # Middle finger up = right click, and the cursor stays put while you do it.
        middle_up = _extended(lm, 12, 10)
        if middle_up:
            self._middle_since = self._middle_since or now
            if self._right_armed and now - self._middle_since >= RIGHT_CLICK_HOLD_S:
                button("right", True)
                button("right", False)
                self._right_armed = False
                self._flash, self._flash_until = "Right click", now + FLASH_S
        else:
            self._middle_since = None
            self._right_armed = True
            x0, x1, y0, y1 = REGION
            nx = min(max((lm[INDEX_TIP].x - x0) / (x1 - x0), 0.0), 1.0)
            ny = min(max((lm[INDEX_TIP].y - y0) / (y1 - y0), 0.0), 1.0)
            if now >= self._freeze_until:
                x, y = self._smooth(nx * (SCREEN_W - 1), ny * (SCREEN_H - 1))
                move_cursor(x, y)
                self._trail.append((now, x, y))
                while self._trail and now - self._trail[0][0] > 0.5:
                    self._trail.popleft()

        # Pinky up = press. A quick tap is a click; keeping it up while you move is a drag.
        reach = _dist(lm[PINKY_TIP], lm[WRIST]) / max(_dist(lm[PINKY_MCP], lm[WRIST]), 1e-6)
        if not self._pressed and reach > PINKY_ON and not middle_up:
            # Raising the pinky nudges the hand, so go back to where the cursor was a
            # moment ago and keep it there briefly before pressing.
            back = [p for p in self._trail if p[0] <= now - SETTLE_BACK_S]
            if back:
                _, x, y = back[-1]
                move_cursor(x, y)
                self._smooth.x, self._smooth.y = x, y
            self._freeze_until = now + CLICK_FREEZE_S
            self._pressed, self._press_since = True, now
            button("left", True)
            self._flash, self._flash_until = "Click", now + 0.25
        elif self._pressed and reach < PINKY_OFF:
            self._pressed = False
            button("left", False)

    def draw(self, frame, hands):
        """Show the area that maps to the screen and where your fingertip is."""
        h, w = frame.shape[:2]
        x0, x1, y0, y1 = REGION
        cv2.rectangle(frame, (int(x0 * w), int(y0 * h)), (int(x1 * w), int(y1 * h)),
                      (255, 255, 255), 1)
        if hands:
            tip = hands[0][INDEX_TIP]
            thickness = -1 if self._pressed else 3
            cv2.circle(frame, (int(tip.x * w), int(tip.y * h)), 14, (64, 54, 251), thickness)