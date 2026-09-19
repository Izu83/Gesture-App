"""Air mouse: your index fingertip moves the cursor, your pinky clicks and drags.

Point (index finger only) and hold to start; make a fist and hold to stop.
Raise your pinky to click (keep it up to drag) and your middle finger for a right click.
While it is on, the other gestures are switched off so they cannot fire by accident.
"""
import ctypes
import math
import threading
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
MIDDLE_ON = 1.5  # middle tip this far from the wrist, compared with its base = middle up
MIDDLE_OFF = 1.3  # ...and down again below this
RIGHT_CLICK_HOLD_S = 0.35  # hold the middle finger up this long for a right click
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


class OneEuro:
    """One Euro filter: very steady when the hand is slow, quick to follow when it moves fast."""

    def __init__(self, min_cutoff, beta, d_cutoff=1.0):
        self.min_cutoff, self.beta, self.d_cutoff = min_cutoff, beta, d_cutoff
        self.t = self.x = self.dx = None

    @staticmethod
    def _alpha(dt, cutoff):
        return 1.0 / (1.0 + 1.0 / (2 * math.pi * cutoff) / dt)

    def reset(self, x=None):
        self.t, self.x, self.dx = None, x, None

    def __call__(self, x, t):
        if self.x is None or self.t is None:
            self.t, self.x, self.dx = t, x, 0.0
            return x
        dt = max(t - self.t, 1e-3)
        dx = (x - self.x) / dt
        self.dx += self._alpha(dt, self.d_cutoff) * (dx - self.dx)
        cutoff = self.min_cutoff + self.beta * abs(self.dx)
        self.x += self._alpha(dt, cutoff) * (x - self.x)
        self.t = t
        return self.x


class Smoother:
    """Removes the shake from the hand tracking. Works on screen pixels."""

    MIN_CUTOFF = 0.5  # Hz: lower = steadier when slow
    BETA = 0.01  # higher = less lag when you move fast

    def __init__(self):
        self.fx = OneEuro(self.MIN_CUTOFF, self.BETA)
        self.fy = OneEuro(self.MIN_CUTOFF, self.BETA)

    def reset(self):
        self.fx.reset()
        self.fy.reset()

    def set(self, x, y, t):
        self.fx.t, self.fx.x, self.fx.dx = t, x, 0.0
        self.fy.t, self.fy.x, self.fy.dx = t, y, 0.0

    def __call__(self, x, y, t):
        return self.fx(x, t), self.fy(y, t)


class CursorDriver:
    """Moves the real cursor about 200 times a second, gliding toward the latest target.

    The camera only gives a new hand position about 20 times a second, so moving the cursor
    once per camera frame looks steppy. This turns those steps into smooth motion."""

    TAU = 0.03  # seconds: how quickly the cursor catches up with the target

    def __init__(self):
        self._lock = threading.Lock()
        self._target = self._pos = None
        self._running = False

    def start(self):
        if self._running:
            return
        self._running = True
        ctypes.windll.winmm.timeBeginPeriod(1)  # 1 ms timer, so sleeps are short
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self):
        if self._running:
            self._running = False
            ctypes.windll.winmm.timeEndPeriod(1)

    def set_target(self, x, y):
        with self._lock:
            self._target = (x, y)
            if self._pos is None:
                self._pos = (x, y)

    def jump(self, x, y):
        with self._lock:
            self._target = self._pos = (x, y)
        move_cursor(x, y)

    def reset(self):
        with self._lock:
            self._target = self._pos = None

    def _loop(self):
        last = time.perf_counter()
        shown = None
        while self._running:
            time.sleep(0.004)
            now = time.perf_counter()
            dt, last = now - last, now
            with self._lock:
                if self._target is None:
                    continue
                k = 1.0 - math.exp(-dt / self.TAU)
                px = self._pos[0] + (self._target[0] - self._pos[0]) * k
                py = self._pos[1] + (self._target[1] - self._pos[1]) * k
                self._pos = (px, py)
            point = (round(px), round(py))
            if point != shown:
                move_cursor(*point)
                shown = point

class AirMouse:
    def __init__(self, driver=None):
        self.active = False
        self._smooth = Smoother()
        self._driver = driver or CursorDriver()
        self._pressed = False
        self._press_since = 0.0
        self._freeze_until = 0.0
        self._trail = deque()  # recent cursor positions: (time, x, y)
        self._middle_since = None
        self._middle_state = False
        self._right_armed = True
        self._fist_since = None
        self._last_seen = 0.0
        self._flash = ""
        self._flash_until = 0.0

    def start(self, now):
        self.active = True
        self._last_seen = now
        self._smooth.reset()
        self._driver.reset()
        self._driver.start()
        self._pressed = False
        self._trail.clear()
        self._middle_since = self._fist_since = None
        self._middle_state = False
        self._right_armed = True
        self._flash, self._flash_until = "Mouse mode on", now + FLASH_S

    def stop(self, now):
        if self._pressed:
            button("left", False)
        self.active = False
        self._pressed = False
        self._driver.stop()
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

        pinky_reach = _dist(lm[PINKY_TIP], lm[WRIST]) / max(_dist(lm[PINKY_MCP], lm[WRIST]), 1e-6)
        middle_reach = _dist(lm[12], lm[WRIST]) / max(_dist(lm[9], lm[WRIST]), 1e-6)
        if middle_reach > MIDDLE_ON:
            self._middle_state = True
        elif middle_reach < MIDDLE_OFF:
            self._middle_state = False
        # Raising the pinky must never count as a right click, whatever the other fingers do.
        middle_up = self._middle_state and pinky_reach < PINKY_OFF and not self._pressed

        # Middle finger up = right click, and the cursor stays put while you do it.
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
                x, y = self._smooth(nx * (SCREEN_W - 1), ny * (SCREEN_H - 1), now)
                self._driver.set_target(x, y)
                self._trail.append((now, x, y))
                while self._trail and now - self._trail[0][0] > 0.5:
                    self._trail.popleft()

        # Pinky up = press. A quick tap is a click; keeping it up while you move is a drag.
        reach = pinky_reach
        if not self._pressed and reach > PINKY_ON and not middle_up:
            # Raising the pinky nudges the hand, so go back to where the cursor was a
            # moment ago and keep it there briefly before pressing.
            back = [p for p in self._trail if p[0] <= now - SETTLE_BACK_S]
            if back:
                _, x, y = back[-1]
                self._driver.jump(x, y)
                self._smooth.set(x, y, now)
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