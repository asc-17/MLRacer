"""Hand-gesture rules: turn the 21 MediaPipe hand joints into a car command.

There is no trained classifier here. MediaPipe's neural network finds the joints, and
every decision after that is plain geometry:

  * how extended each finger is, from 3-D joint angles (so it works whichever way the hand is turned)
  * the pointing direction of the index finger, in the camera image as the user sees it (mirrored)
  * which side the thumb sticks out to, also in the image

Commands are the letters the car's /cmd endpoint already understands:
  F forward   B backward   L spin left      R spin right
  G fwd-left  I fwd-right  H backward-left  J backward-right   S stop

Landmark numbering (MediaPipe):
  0 wrist | thumb 1-4 | index 5-8 | middle 9-12 | ring 13-16 | pinky 17-20
  (for each finger: knuckle/MCP, PIP, DIP, tip; for the thumb: CMC, MCP, IP, tip)
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

# ---------------------------------------------------------------- landmarks
WRIST = 0
THUMB = (1, 2, 3, 4)
INDEX = (5, 6, 7, 8)
MIDDLE = (9, 10, 11, 12)
RING = (13, 14, 15, 16)
PINKY = (17, 18, 19, 20)
LONG_FINGERS = (INDEX, MIDDLE, RING, PINKY)
FINGER_LETTERS = "TIMRP"          # thumb, index, middle, ring, pinky

# ---------------------------------------------------------------- modes / commands
MODE_POINTING = "pointing"
MODE_SHAPES = "shapes"
MODES = (MODE_POINTING, MODE_SHAPES)
MODE_NAMES = {MODE_POINTING: "Pointing direction", MODE_SHAPES: "Hand shapes"}

COMMAND_NAMES = {
    "F": "FORWARD", "B": "BACKWARD", "L": "LEFT", "R": "RIGHT",
    "G": "FORWARD LEFT", "I": "FORWARD RIGHT", "H": "BACKWARD LEFT", "J": "BACKWARD RIGHT",
    "S": "STOP",
}
ARROWS = {"F": "↑", "B": "↓", "L": "↺", "R": "↻", "G": "↖", "I": "↗", "H": "↙", "J": "↘", "S": "■"}

LEGEND = {
    MODE_POINTING: [
        ("Point index finger up", "F"),
        ("Point down", "B"),
        ("Point left", "L"),
        ("Point right", "R"),
        ("Point up-left", "G"),
        ("Point up-right", "I"),
        ("Point down-left", "H"),
        ("Point down-right", "J"),
        ("Fist, other pose, no hand", "S"),
    ],
    MODE_SHAPES: [
        ("Open palm", "F"),
        ("Peace sign (V)", "B"),
        ("Thumb only, pointing left", "L"),
        ("Thumb only, pointing right", "R"),
        ("L-shape (thumb+index), thumb left", "G"),
        ("L-shape (thumb+index), thumb right", "I"),
        ("Shaka (thumb+pinky), thumb left", "H"),
        ("Shaka (thumb+pinky), thumb right", "J"),
        ("Fist, other pose, no hand", "S"),
    ],
}

# ---------------------------------------------------------------- tunables
# Values below were checked on real photos: straight fingers bend 35-65 deg, a relaxed open hand
# 60-105, curled fingers (fist, pointing, peace sign) 145-240.
# Finger extension: total bend in degrees over a finger's three joints.
FINGER_BEND_STRAIGHT = 70.0    # at or below this the finger counts as fully extended (score 1)
FINGER_BEND_CURLED = 140.0     # at or above this it counts as fully curled (score 0)
# Thumb extension, two tests that must both pass:
#   "out":    tip-to-pinky-knuckle distance / IP-to-pinky-knuckle distance (>1 when the tip points away from the palm)
#   "spread": tip-to-index-knuckle distance / palm length
# Measured: thumbs up/down out 1.16-1.36, spread 0.74-0.89; thumb resting half-open while pointing
# out 1.06-1.10, spread 0.53-0.59; thumb tucked for a peace sign out 0.81-0.87.
THUMB_OUT_LO, THUMB_OUT_HI = 1.00, 1.15
THUMB_SPREAD_LO, THUMB_SPREAD_HI = 0.50, 0.70
# Pointing: index length in the image / palm length in the image. Small when the finger points at the camera.
POINT_LEN_LO, POINT_LEN_HI = 0.30, 0.55
# Thumb side: |horizontal part| of the thumb direction. 0 = thumb straight up/down, 1 = flat sideways.
SIDE_LO, SIDE_HI = 0.35, 0.60
# Extra degrees a pointing sector keeps once chosen, so the command doesn't flicker on a boundary.
SECTOR_HYSTERESIS = 8.0
# A pose whose finger match is below this is reported as "Unknown pose".
MIN_POSE_MATCH = 0.5


@dataclass
class GestureResult:
    """What one frame shows, before smoothing and the confidence threshold."""
    cmd: str = "S"                       # candidate command letter
    confidence: float = 0.0              # 0..1
    gesture: str = "No hand"             # human-readable pose name
    fingers: tuple = (0.0, 0.0, 0.0, 0.0, 0.0)   # extension 0..1 for T I M R P
    arrow: tuple | None = None           # ((x0, y0), (x1, y1)) image points to draw a direction arrow


# ---------------------------------------------------------------- geometry helpers
def _ramp(x: float, lo: float, hi: float) -> float:
    """0 at lo, 1 at hi, linear in between (lo may be greater than hi)."""
    if hi == lo:
        return float(x >= hi)
    return float(min(1.0, max(0.0, (x - lo) / (hi - lo))))


def _joint_angle(a, b, c) -> float:
    """Angle at b in degrees; 180 means a-b-c is a straight line."""
    v1, v2 = a - b, c - b
    n = float(np.linalg.norm(v1) * np.linalg.norm(v2))
    if n < 1e-12:
        return 180.0
    return math.degrees(math.acos(float(np.clip(np.dot(v1, v2) / n, -1.0, 1.0))))


def _dist(a, b) -> float:
    return float(np.linalg.norm(np.asarray(a, float) - np.asarray(b, float)))


def image_angle(p_from, p_to) -> float:
    """Direction from p_from to p_to in image pixels, as a compass angle: 0 = right, 90 = up."""
    dx = float(p_to[0] - p_from[0])
    dy = float(p_to[1] - p_from[1])
    return math.degrees(math.atan2(-dy, dx))       # image y grows downwards


def _angle_diff(a: float, b: float) -> float:
    return abs((a - b + 180.0) % 360.0 - 180.0)


# ---------------------------------------------------------------- finger state
def finger_bend(world, finger) -> float:
    mcp, pip, dip, tip = finger
    w = world
    return ((180.0 - _joint_angle(w[WRIST], w[mcp], w[pip]))
            + (180.0 - _joint_angle(w[mcp], w[pip], w[dip]))
            + (180.0 - _joint_angle(w[pip], w[dip], w[tip])))


def thumb_extension(world) -> float:
    w = world
    palm = max(_dist(w[WRIST], w[MIDDLE[0]]), 1e-9)
    out = _dist(w[4], w[17]) / max(_dist(w[3], w[17]), 1e-9)
    spread = _dist(w[4], w[INDEX[0]]) / palm
    return min(_ramp(out, THUMB_OUT_LO, THUMB_OUT_HI), _ramp(spread, THUMB_SPREAD_LO, THUMB_SPREAD_HI))


def finger_extension(world) -> np.ndarray:
    """Extension score 0 (curled) .. 1 (straight) for thumb, index, middle, ring, pinky.

    `world` is 21 x 3 (MediaPipe world landmarks, metres) so the angles do not depend on
    how the hand is turned relative to the camera. 21 x 2 also works, less reliably.
    """
    w = np.asarray(world, float)
    scores = [thumb_extension(w)]
    for f in LONG_FINGERS:
        scores.append(_ramp(finger_bend(w, f), FINGER_BEND_CURLED, FINGER_BEND_STRAIGHT))
    return np.array(scores)


def _match(ext, pattern) -> float:
    """How well finger scores fit a pattern of 1 (extended), 0 (curled), None (don't care). Weakest finger wins."""
    m = 1.0
    for e, want in zip(ext, pattern):
        if want is None:
            continue
        m = min(m, e if want else 1.0 - e)
    return m


# ---------------------------------------------------------------- pointing mode
SECTORS = ((0.0, "R"), (45.0, "I"), (90.0, "F"), (135.0, "G"),
           (180.0, "L"), (-135.0, "H"), (-90.0, "B"), (-45.0, "J"))
SECTOR_CENTER = {cmd: ang for ang, cmd in SECTORS}


def pointing_sector(angle: float, prev: str | None = None) -> tuple[str, float]:
    """Nearest of 8 directions for a compass angle, with hysteresis toward `prev`.

    Returns (command, angle score): 1.0 dead on a direction, 0.75 at a sector boundary.
    """
    cmd = min(SECTORS, key=lambda s: _angle_diff(angle, s[0]))[1]
    if prev in SECTOR_CENTER and prev != cmd \
            and _angle_diff(angle, SECTOR_CENTER[prev]) <= 22.5 + SECTOR_HYSTERESIS:
        cmd = prev
    off = _angle_diff(angle, SECTOR_CENTER[cmd])
    return cmd, 0.75 + 0.25 * _ramp(22.5 - off, 0.0, 22.5)


def _classify_pointing(img, ext, prev):
    _, i, m, r, p = ext
    point = min(i, 1 - m, 1 - r, 1 - p)
    fist = min(1 - i, 1 - m, 1 - r, 1 - p)
    if fist > point:
        if fist < MIN_POSE_MATCH:
            return GestureResult("S", 1 - fist, "Unknown pose", tuple(ext))
        return GestureResult("S", fist, "Fist", tuple(ext))
    if point < MIN_POSE_MATCH:
        return GestureResult("S", 1 - point, "Unknown pose", tuple(ext))

    base, tip = img[INDEX[0]], img[INDEX[3]]
    cmd, angle_score = pointing_sector(image_angle(base, tip), prev)
    palm_px = max(_dist(img[WRIST], img[MIDDLE[0]]), 1e-9)
    length_score = _ramp(_dist(base, tip) / palm_px, POINT_LEN_LO, POINT_LEN_HI)
    arrow_end = base + (tip - base) * 1.8
    conf = point * angle_score * length_score
    gesture = "Pointing" if length_score > 0.5 else "Pointing (at camera?)"
    return GestureResult(cmd, conf, gesture, tuple(ext),
                         (tuple(base), tuple(arrow_end)))


# ---------------------------------------------------------------- hand-shape mode
#                 T     I  M  R  P
SHAPES = {
    "Open palm": (None, 1, 1, 1, 1),
    "Peace (V)": (None, 1, 1, 0, 0),
    "Thumb": (1, 0, 0, 0, 0),
    "L-shape": (1, 1, 0, 0, 0),
    "Shaka": (1, 0, 0, 0, 1),
    "Fist": (0, 0, 0, 0, 0),
}
# shape -> (command if thumb on the left, command if thumb on the right); None = no steering
SHAPE_COMMANDS = {
    "Open palm": "F",
    "Peace (V)": "B",
    "Thumb": ("L", "R"),
    "L-shape": ("G", "I"),
    "Shaka": ("H", "J"),
    "Fist": "S",
}


def thumb_side(img) -> tuple[str, float]:
    """Which side the thumb points to in the (mirrored) image, and how clearly (0..1)."""
    v = np.asarray(img[THUMB[3]], float) - np.asarray(img[THUMB[1]], float)
    n = float(np.linalg.norm(v))
    if n < 1e-9:
        return "left", 0.0
    horizontal = abs(float(v[0])) / n
    return ("left" if v[0] < 0 else "right"), _ramp(horizontal, SIDE_LO, SIDE_HI)


def _classify_shapes(img, ext):
    scores = {name: _match(ext, pat) for name, pat in SHAPES.items()}
    name = max(scores, key=scores.get)
    match = scores[name]
    if match < MIN_POSE_MATCH:
        return GestureResult("S", 1 - match, "Unknown pose", tuple(ext))
    target = SHAPE_COMMANDS[name]
    if isinstance(target, str):
        return GestureResult(target, match, name, tuple(ext))

    side, side_score = thumb_side(img)
    cmd = target[0] if side == "left" else target[1]
    start = np.asarray(img[THUMB[1]], float)
    end = start + (np.asarray(img[THUMB[3]], float) - start) * 1.8
    label = f"{name}, thumb {side}" if side_score > 0.5 else f"{name}, thumb not sideways"
    return GestureResult(cmd, match * side_score, label, tuple(ext), (tuple(start), tuple(end)))


# ---------------------------------------------------------------- public entry point
def classify(mode: str, img, world, hand_score: float = 1.0, prev: str | None = None) -> GestureResult:
    """Classify one hand.

    img        21 x 2 landmark positions in pixels, in the image as the user sees it (mirrored).
    world      21 x 3 MediaPipe world landmarks (any unit), used for finger bending.
    hand_score MediaPipe's confidence that this is a hand; multiplies the result.
    prev       previous candidate command, for pointing-sector hysteresis.
    """
    img = np.asarray(img, float)[:, :2]
    ext = finger_extension(world)
    if mode == MODE_SHAPES:
        res = _classify_shapes(img, ext)
    else:
        res = _classify_pointing(img, ext, prev)
    res.confidence = float(max(0.0, min(1.0, res.confidence * hand_score)))
    return res


class GestureFilter:
    """Debounce: a new driving command must be seen `hold_frames` frames in a row before it is used.
    Dropping to STOP (no hand, fist, low confidence) is always immediate."""

    def __init__(self, hold_frames: int = 3):
        self.hold_frames = max(1, int(hold_frames))
        self.output = "S"
        self.last_candidate: str | None = None   # raw candidate from the last frame (for hysteresis)
        self._pending: str | None = None
        self._count = 0

    def reset(self):
        self.output, self.last_candidate, self._pending, self._count = "S", None, None, 0

    def update(self, result: GestureResult, threshold: float) -> str:
        self.last_candidate = result.cmd
        raw = result.cmd if result.confidence >= threshold else "S"
        if raw == "S":
            self.output, self._pending, self._count = "S", None, 0
            return self.output
        if raw == self._pending:
            self._count += 1
        else:
            self._pending, self._count = raw, 1
        if self._count >= self.hold_frames:
            self.output = raw
        return self.output
