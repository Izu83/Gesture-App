# Gesture-App

Control your Windows PC with hand gestures. The app watches your webcam, tracks your hands with
[MediaPipe](https://developers.google.com/mediapipe), and turns gestures into actions.

Windows only (it sends Windows keyboard shortcuts and controls windows).

## Setup

You need Python 3 and a webcam.

```bash
pip install opencv-python mediapipe pillow numpy
python src/camera.py
```

On the first run the app downloads Google's hand model (`hand_landmarker.task`, about 8 MB) into `src/`.

If you get an error about `cv2.imshow` not being implemented, you have `opencv-python-headless`
installed as well. Uninstall both and reinstall only `opencv-python`.

## Gestures

The camera window is mirrored and shows your hand skeleton. The name of the gesture appears at the bottom.

| Gesture | How to make it | What it does |
|---|---|---|
| Open Palm | Palm to the camera, fingers spread | Shows the label only |
| Double Open Palm | Both palms to the camera | Shows the label only |
| Swipe | Open hand moved sideways (or a quick slap) | Left to right: next app (Alt+Tab). Right to left: previous app. In Task View: moves the selection |
| Search | Thumb and index fingertip touch in a circle | Opens Windows Search (Win+S) |
| Middle Finger | Only the middle finger up | Shows a rude reply |
| Help | Thumb, index and pinky up on both hands | Opens the Help window with all gestures |
| Fist | Hold a closed fist | Opens Task View (Win+Tab) |
| Push | Open hands toward the camera | One hand minimizes the window in front, two hands close it. In Task View: cancels (Esc) |
| Pull | Open hand back away from the camera | In Task View: opens the selected window (Enter) |

Two-hand push asks the program to close, so programs with unsaved work will still ask you to save.

## Keys

- **Esc** or **q**: quit the app (closing the camera window with the X also quits)
- **h**: close the Help window
- In the Help window: mouse wheel, arrow keys or **W** / **S** to scroll

## Files

- `src/camera.py`: camera loop, gesture detection and actions
- `src/help_screen.py`: draws the Help window
- `src/Limelight-Regular.ttf`: font used for the on-screen text (Google Fonts, SIL Open Font License)

## Tuning

Gesture sensitivity is set by constants near the top of `src/camera.py`, for example
`SWIPE_MIN_DX`, `PUSH_GROWTH`, `STABLE_S` and `FIST_HOLD_S`.