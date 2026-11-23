"""Camera thread: grab a frame, find the hand with MediaPipe, classify the gesture, draw the overlay.

The GUI reads the newest annotated frame and gesture with `latest()`; the car link reads the
command with `command()`. Nothing here waits on the GUI or the network.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks.python import BaseOptions, vision

import gestures as g

HAND_CONNECTIONS = [(c.start, c.end) for c in vision.HandLandmarksConnections.HAND_CONNECTIONS]
FINGERTIPS = (4, 8, 12, 16, 20)

# BGR colours
GREEN = (80, 220, 60)
AMBER = (0, 175, 255)
GREY = (170, 170, 170)
RED = (60, 60, 235)
WHITE = (240, 240, 240)
BLACK = (0, 0, 0)
FONT = cv2.FONT_HERSHEY_SIMPLEX


@dataclass
class VisionState:
    cmd: str = "S"                          # debounced command (what the car should do if armed)
    result: g.GestureResult = field(default_factory=g.GestureResult)
    hand: bool = False
    fps: float = 0.0
    camera_ok: bool = False
    error: str = "Starting camera..."
    time: float = 0.0                       # monotonic time of this state


class VisionWorker(threading.Thread):
    def __init__(self, settings, model_path: Path):
        super().__init__(daemon=True, name="vision")
        self.settings = settings            # read every frame, so Settings changes apply live
        self.model_bytes = Path(model_path).read_bytes()   # buffer avoids path issues on Windows
        self.armed = False                  # set by the GUI, only changes colours here
        self.preview = False
        self._quit = threading.Event()
        self._lock = threading.Lock()
        self._frame = None
        self._state = VisionState(time=time.monotonic())
        self._seq = 0
        self._filter = g.GestureFilter(settings.hold_frames)

    # -------- read from other threads
    def latest(self):
        with self._lock:
            return self._frame, self._state, self._seq

    def command(self, max_age: float = 0.3) -> str:
        """The debounced command, or STOP if the camera thread has gone quiet."""
        st = self._state
        if time.monotonic() - st.time > max_age:
            return "S"
        return st.cmd

    def stop(self, timeout: float = 3.0):
        self._quit.set()
        self.join(timeout)

    # -------- thread
    def run(self):
        options = vision.HandLandmarkerOptions(
            base_options=BaseOptions(model_asset_buffer=self.model_bytes),
            running_mode=vision.RunningMode.VIDEO,
            num_hands=1,
            min_hand_detection_confidence=0.6,
            min_hand_presence_confidence=0.5,
            min_tracking_confidence=0.5,
        )
        cap = None
        try:
            with vision.HandLandmarker.create_from_options(options) as landmarker:
                last_ts, fps, t_prev, fails = 0, 0.0, time.monotonic(), 0
                while not self._quit.is_set():
                    if cap is None:
                        cap = self._open_camera()
                        if cap is None:
                            self._publish_error(f"Camera {self.settings.camera_index} not available")
                            self._quit.wait(2.0)
                            continue
                    ok, frame = cap.read()
                    if not ok or frame is None:
                        fails += 1
                        if fails > 15:
                            cap.release()
                            cap, fails = None, 0
                            self._publish_error("Camera stopped sending pictures - retrying")
                        continue
                    fails = 0

                    # Always work on the mirrored picture: that is the user's own left/right.
                    frame = cv2.flip(frame, 1)
                    h, w = frame.shape[:2]
                    ts = max(last_ts + 1, int(time.monotonic() * 1000))
                    last_ts = ts
                    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    res = landmarker.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb), ts)

                    s = self.settings
                    self._filter.hold_frames = s.hold_frames
                    pts = None
                    if res.hand_landmarks:
                        pts = np.array([[p.x * w, p.y * h] for p in res.hand_landmarks[0]])
                        world = np.array([[p.x, p.y, p.z] for p in res.hand_world_landmarks[0]])
                        score = res.handedness[0][0].score if res.handedness else 1.0
                        result = g.classify(s.mode, pts, world, score, self._filter.last_candidate)
                    else:
                        result = g.GestureResult()
                    cmd = self._filter.update(result, s.confidence_threshold)

                    now = time.monotonic()
                    dt = now - t_prev
                    t_prev = now
                    if dt > 0:
                        fps = 1 / dt if fps == 0 else fps * 0.9 + 0.1 / dt

                    state = VisionState(cmd=cmd, result=result, hand=pts is not None, fps=fps,
                                        camera_ok=True, error="", time=now)
                    view = self._draw(frame, pts, result, cmd, s)
                    self._publish(cv2.cvtColor(view, cv2.COLOR_BGR2RGB), state)
        except Exception as e:  # keep the GUI alive and the car stopped whatever happens
            self._publish_error(f"Vision error: {e}")
            raise
        finally:
            if cap is not None:
                cap.release()

    def _open_camera(self):
        idx = self.settings.camera_index
        cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW)       # DirectShow opens fast on Windows
        if not cap.isOpened():
            cap.release()
            cap = cv2.VideoCapture(idx)
        if not cap.isOpened():
            cap.release()
            return None
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))   # 30 fps at 720p on most webcams
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)                # newest frame, not a stale one
        return cap

    def _publish(self, frame, state):
        with self._lock:
            self._frame, self._state = frame, state
            self._seq += 1

    def _publish_error(self, msg):
        self._filter.reset()
        img = np.full((480, 640, 3), 30, np.uint8)
        cv2.putText(img, msg, (20, 240), FONT, 0.7, WHITE, 2, cv2.LINE_AA)
        self._publish(img, VisionState(cmd="S", camera_ok=False, error=msg, time=time.monotonic()))

    # -------- overlay
    def _draw(self, frame, pts, result, cmd, s):
        if s.mirror:
            view = frame
        else:                                             # show the camera unmirrored, same gestures
            view = cv2.flip(frame, 1)
            if pts is not None:
                pts = pts.copy()
                pts[:, 0] = frame.shape[1] - 1 - pts[:, 0]
        h, w = view.shape[:2]
        live = self.armed and not self.preview

        if cmd != "S":
            color = GREEN if live else AMBER
        elif result.gesture == "Fist":
            color = RED
        else:
            color = GREY

        # banner, top left
        if self.preview:
            banner, bcol = "PREVIEW - not connected to the car", AMBER
        elif self.armed:
            banner, bcol = "ARMED - gestures drive the car", GREEN
        else:
            banner, bcol = "DISARMED - press Space to arm", AMBER
        _label(view, banner, (12, 12), bcol, scale=0.6, thick=2)
        if live:
            cv2.rectangle(view, (2, 2), (w - 3, h - 3), GREEN, 4)

        if pts is None:
            _label(view, "Show one hand to the camera", (12, 52), GREY, scale=0.6, thick=1)
            return view

        p = pts.astype(int)
        pl = [tuple(q) for q in p.tolist()]               # plain ints for OpenCV
        # skeleton: bones as lines (dark outline so they show on light and dark backgrounds),
        # joints as dots, fingertips bigger
        for a, b in HAND_CONNECTIONS:
            cv2.line(view, pl[a], pl[b], BLACK, 6, cv2.LINE_AA)
        for a, b in HAND_CONNECTIONS:
            cv2.line(view, pl[a], pl[b], color, 3, cv2.LINE_AA)
        for i, q in enumerate(pl):
            r = 7 if i in FINGERTIPS else 5
            cv2.circle(view, q, r, WHITE, -1, cv2.LINE_AA)
            cv2.circle(view, q, r, BLACK, 2, cv2.LINE_AA)

        # direction arrow (pointing finger or thumb)
        if result.arrow is not None and result.cmd != "S":
            a0, a1 = np.array(result.arrow[0], float), np.array(result.arrow[1], float)
            if not s.mirror:
                a0[0], a1[0] = w - 1 - a0[0], w - 1 - a1[0]
            cv2.arrowedLine(view, (int(a0[0]), int(a0[1])), (int(a1[0]), int(a1[1])), color, 4,
                            cv2.LINE_AA, tipLength=0.25)

        # box around the hand
        pad = int(0.15 * max(np.ptp(p[:, 0]), np.ptp(p[:, 1]))) + 10
        x0, y0 = max(0, int(p[:, 0].min()) - pad), max(0, int(p[:, 1].min()) - pad)
        x1, y1 = min(w - 1, int(p[:, 0].max()) + pad), min(h - 1, int(p[:, 1].max()) + pad)
        cv2.rectangle(view, (x0, y0), (x1, y1), color, 3, cv2.LINE_AA)

        # label: command + confidence, then the pose name
        line1 = f"{g.COMMAND_NAMES[cmd]}  {result.confidence * 100:.0f}%"
        line2 = result.gesture
        if cmd == "S" and result.cmd != "S":
            line2 += f" -> {g.COMMAND_NAMES[result.cmd]}?"
            line2 += " (low confidence)" if result.confidence < s.confidence_threshold else " (hold...)"
        (tw1, th1), _ = cv2.getTextSize(line1, FONT, 0.9, 2)
        (tw2, th2), _ = cv2.getTextSize(line2, FONT, 0.55, 1)
        box_h = th1 + th2 + 24
        ly = y0 - box_h if y0 - box_h >= 0 else min(y1, h - box_h)
        lx = min(x0, max(0, w - max(tw1, tw2) - 16))
        cv2.rectangle(view, (lx, ly), (lx + max(tw1, tw2) + 16, ly + box_h), color, -1)
        cv2.putText(view, line1, (lx + 8, ly + th1 + 8), FONT, 0.9, BLACK, 2, cv2.LINE_AA)
        cv2.putText(view, line2, (lx + 8, ly + th1 + th2 + 16), FONT, 0.55, BLACK, 1, cv2.LINE_AA)
        return view


def _label(img, text, org, bg, scale=0.6, thick=1):
    (tw, th), base = cv2.getTextSize(text, FONT, scale, thick)
    x, y = org
    cv2.rectangle(img, (x, y), (x + tw + 12, y + th + base + 10), bg, -1)
    cv2.putText(img, text, (x + 6, y + th + 5), FONT, scale, BLACK, thick, cv2.LINE_AA)
