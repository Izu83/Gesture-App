"""Voice typing: listen on the microphone, transcribe offline with Whisper, type the text.

Uses faster-whisper with the large-v3 model on the GPU (falls back to the CPU).
Nothing leaves the PC.
"""
import ctypes
import importlib.util
import os
import queue
import sys
import threading
import time
import types

import numpy as np
import sounddevice as sd

# PyAV is only used by faster-whisper to decode audio *files*. We pass raw microphone
# arrays, so a stub is enough (and avoids a native DLL that Windows may block).
try:
    import av  # noqa: F401
except ImportError:
    sys.modules["av"] = types.ModuleType("av")

SAMPLE_RATE = 16000
MODEL_NAME = "large-v3"
LANGUAGE = "en"
CHUNK = 480  # 30 ms
MIN_THRESHOLD = 0.012  # minimum RMS that counts as speech
NO_SPEECH_TIMEOUT_S = 8.0  # give up if nothing is said
END_SILENCE_S = 1.0  # stop after this much silence once you have spoken
MAX_RECORD_S = 20.0
PRE_ROLL_S = 0.3
TYPE_DELAY_S = 0.8  # wait for the search box to be ready before typing

# Whisper sometimes "hears" these in silence.
HALLUCINATIONS = {"", "you", "thank you", "thanks for watching", "bye", "thank you for watching"}


def _add_cuda_dlls():
    """Let CTranslate2 find cuBLAS/cuDNN, which ship inside the PyTorch wheel."""
    spec = importlib.util.find_spec("torch")
    if not spec or not spec.submodule_search_locations:
        return
    lib = os.path.join(list(spec.submodule_search_locations)[0], "lib")
    if os.path.isdir(lib):
        os.add_dll_directory(lib)
        os.environ["PATH"] = lib + os.pathsep + os.environ.get("PATH", "")


# --- typing text into the focused window (Win32 SendInput, unicode) -------------------

ULONG_PTR = ctypes.c_size_t


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort),
                ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong),
                ("dwExtraInfo", ULONG_PTR)]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long), ("mouseData", ctypes.c_ulong),
                ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong),
                ("dwExtraInfo", ULONG_PTR)]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", ctypes.c_ulong), ("wParamL", ctypes.c_ushort),
                ("wParamH", ctypes.c_ushort)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", _KEYBDINPUT), ("mi", _MOUSEINPUT), ("hi", _HARDWAREINPUT)]


class _INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", ctypes.c_ulong), ("u", _INPUTUNION)]


def type_text(text):
    """Type text into whatever window has the keyboard focus."""
    events = []
    data = text.encode("utf-16-le")
    for i in range(0, len(data), 2):
        code = int.from_bytes(data[i:i + 2], "little")
        for flags in (0x0004, 0x0004 | 0x0002):  # KEYEVENTF_UNICODE, then key up
            ev = _INPUT(type=1)
            ev.ki = _KEYBDINPUT(0, code, flags, 0, 0)
            events.append(ev)
    if events:
        array = (_INPUT * len(events))(*events)
        ctypes.windll.user32.SendInput(len(events), array, ctypes.sizeof(_INPUT))


# --- the voice typer -------------------------------------------------------------------

class VoiceTyper:
    def __init__(self):
        self._model = None
        self._model_error = None
        self._model_ready = threading.Event()
        self._busy = threading.Lock()
        self._state = "idle"  # idle | listening | thinking
        self._typed_until = 0.0
        self.device = None

    # Loading the 3 GB model takes several seconds, so do it in the background.
    def preload(self):
        threading.Thread(target=self._load, daemon=True).start()

    def _load(self):
        try:
            _add_cuda_dlls()
            from faster_whisper import WhisperModel
            try:
                self._model = WhisperModel(MODEL_NAME, device="cuda", compute_type="float16")
                self.device = "GPU"
            except Exception as gpu_error:  # no usable GPU: slower, but still works
                print(f"Whisper could not use the GPU ({gpu_error}); using the CPU.")
                self._model = WhisperModel(MODEL_NAME, device="cpu", compute_type="int8")
                self.device = "CPU"
            # The first transcription is slow (GPU warm-up), so do a throwaway one now.
            list(self._model.transcribe(np.zeros(SAMPLE_RATE, dtype=np.float32),
                                        language=LANGUAGE, vad_filter=False)[0])
            print(f"Voice model ready ({MODEL_NAME} on {self.device}).")
        except Exception as error:
            self._model_error = error
            print(f"Voice typing is unavailable: {error}")
        finally:
            self._model_ready.set()

    def close(self):
        # Free the GPU model explicitly so the interpreter exits cleanly.
        self._model = None

    @property
    def label(self):
        """Text for the on-screen label, or "" when there is nothing to show."""
        if self._state == "listening":
            return "Listening..."
        if self._state == "thinking":
            return "Thinking..."
        if time.time() < self._typed_until:
            return "Typed"
        return ""

    def listen_and_type(self):
        """Record one phrase, transcribe it and type it. Runs in the background."""
        if not self._busy.acquire(blocking=False):
            return  # already listening
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        started = time.time()
        try:
            self._state = "listening"
            audio = self.record()
            if audio is None:
                return
            self._state = "thinking"
            text = self.transcribe(audio)
            if text:
                wait = TYPE_DELAY_S - (time.time() - started)
                if wait > 0:
                    time.sleep(wait)
                type_text(text)
                self._typed_until = time.time() + 1.0
        except Exception as error:
            print(f"Voice typing failed: {error}")
        finally:
            self._state = "idle"
            self._busy.release()

    def record(self):
        """Record from the default microphone until you stop talking. None if silent."""
        blocks = queue.Queue()

        def callback(indata, frames, time_info, status):
            blocks.put(indata[:, 0].copy())

        pre_roll = []
        speech = []
        noise = []
        threshold = MIN_THRESHOLD
        speaking = False
        loud_run = 0
        silence = 0.0
        start = time.time()
        with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                            blocksize=CHUNK, callback=callback):
            while True:
                try:
                    block = blocks.get(timeout=1.0)
                except queue.Empty:
                    return None
                rms = float(np.sqrt(np.mean(block ** 2)))
                elapsed = time.time() - start
                if elapsed < 0.4:  # learn the background noise level first
                    noise.append(rms)
                    threshold = max(MIN_THRESHOLD, 3 * float(np.mean(noise)))
                    continue
                if not speaking:
                    pre_roll.append(block)
                    pre_roll = pre_roll[-int(PRE_ROLL_S * SAMPLE_RATE / CHUNK):]
                    loud_run = loud_run + 1 if rms > threshold else 0
                    if loud_run >= 3:
                        speaking = True
                        speech = list(pre_roll)
                    elif elapsed > NO_SPEECH_TIMEOUT_S:
                        return None
                else:
                    speech.append(block)
                    silence = 0.0 if rms > threshold else silence + CHUNK / SAMPLE_RATE
                    if silence >= END_SILENCE_S or elapsed > MAX_RECORD_S:
                        break
        return np.concatenate(speech)

    def transcribe(self, audio):
        """Turn a 16 kHz mono float32 array into text ("" if nothing was said)."""
        self._model_ready.wait()
        if self._model is None:
            return ""
        segments, _ = self._model.transcribe(
            audio, language=LANGUAGE, beam_size=5, vad_filter=True,
            condition_on_previous_text=False, temperature=0.0)
        text = " ".join(s.text.strip() for s in segments).strip()
        text = text.rstrip(".!?,;: ")  # a search box does not need the full stop
        return "" if text.lower() in HALLUCINATIONS else text