"""Tests for the gesture rules.

Two kinds of input:
  * real hands: landmarks MediaPipe found in public sample photos (tests/fixtures/real_hands.json)
  * synthetic hands: a simple 3-D hand model below, for poses with no sample photo
"""
import json
import math
import pathlib
import sys

import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import gestures as g  # noqa: E402

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "real_hands.json"
REAL = json.loads(FIXTURE.read_text())
THRESHOLD = 0.6


# ---------------------------------------------------------------- synthetic hand model (cm, y up)
MCP = {"I": (-2.4, 9.0), "M": (-0.6, 9.4), "R": (1.2, 9.0), "P": (2.8, 8.2)}
SEG = {"I": (4.0, 2.5, 2.0), "M": (4.5, 2.8, 2.2), "R": (4.2, 2.6, 2.1), "P": (3.3, 2.0, 1.8)}
CMC = np.array([-1.8, 2.2, 0.0])


def _finger(name, extended):
    mcp = np.array([*MCP[name], 0.0])
    base = mcp / np.linalg.norm(mcp)             # fingers fan out from the wrist
    toward_palm = np.array([0.0, 0.0, -1.0])
    angles = (0, 0, 0) if extended else (75, 170, 245)
    pts, p = [mcp], mcp
    for length, a in zip(SEG[name], angles):
        r = math.radians(a)
        p = p + length * (math.cos(r) * base + math.sin(r) * toward_palm)
        pts.append(p)
    return pts


def _thumb(extended):
    if extended:
        dirs = [(-0.9, 0.45, 0.0)] * 3
    else:                                         # folded across the palm
        dirs = [(-0.3, 0.954, 0.0), (0.6, 0.6, -0.5), (0.9, 0.0, -0.4)]
    pts, p = [CMC], CMC
    for length, d in zip((3.6, 3.0, 2.5), dirs):
        d = np.array(d) / np.linalg.norm(d)
        p = p + length * d
        pts.append(p)
    return pts


def make_hand(fingers="", rotate=0.0, mirror=False):
    """3-D hand with the listed fingers extended (letters from 'TIMRP'), palm facing the camera,
    thumb on the left. `rotate` turns it in the image plane (degrees, counter-clockwise),
    `mirror` flips it left-right (the other hand). Returns (image points px, world points)."""
    world = [np.zeros(3)] + _thumb("T" in fingers)
    for name in "IMRP":
        world += _finger(name, name in fingers)
    world = np.array(world)
    if mirror:
        world[:, 0] *= -1
    r = math.radians(rotate)
    rot = np.array([[math.cos(r), -math.sin(r), 0], [math.sin(r), math.cos(r), 0], [0, 0, 1]])
    world = world @ rot.T
    img = np.column_stack([world[:, 0] * 20 + 320, -world[:, 1] * 20 + 240])   # image y points down
    return img, world


def rotated_to(fingers, tip_from, tip_to, target_angle, mirror=False):
    """Hand rotated so that the image direction tip_from -> tip_to points at target_angle (compass deg)."""
    img, _ = make_hand(fingers, mirror=mirror)
    current = g.image_angle(img[tip_from], img[tip_to])
    return make_hand(fingers, rotate=target_angle - current, mirror=mirror)


# ---------------------------------------------------------------- real hands
@pytest.mark.parametrize("sample", REAL, ids=[s["source"] for s in REAL])
@pytest.mark.parametrize("mode", g.MODES)
def test_real_hands(sample, mode):
    res = g.classify(mode, sample["img"], sample["world"], sample["hand_score"])
    final = res.cmd if res.confidence >= THRESHOLD else "S"
    assert final == sample[f"expect_{mode}"], f"{res.gesture} {res.confidence:.2f}"
    if final != "S":
        assert res.confidence > 0.8


# ---------------------------------------------------------------- pointing mode
@pytest.mark.parametrize("angle,cmd", [(90, "F"), (-90, "B"), (180, "L"), (0, "R"),
                                       (135, "G"), (45, "I"), (-135, "H"), (-45, "J")])
@pytest.mark.parametrize("mirror", [False, True])
def test_pointing_eight_directions(angle, cmd, mirror):
    img, world = rotated_to("I", 5, 8, angle, mirror)
    res = g.classify(g.MODE_POINTING, img, world)
    assert res.cmd == cmd and res.gesture == "Pointing"
    assert res.confidence > 0.95
    assert res.arrow is not None


def test_pointing_ignores_thumb():
    img, world = rotated_to("TI", 5, 8, 90)
    assert g.classify(g.MODE_POINTING, img, world).cmd == "F"


def test_pointing_fist_is_stop():
    img, world = make_hand("")
    res = g.classify(g.MODE_POINTING, img, world)
    assert (res.cmd, res.gesture) == ("S", "Fist")


def test_pointing_open_palm_is_stop():
    img, world = make_hand("TIMRP")
    res = g.classify(g.MODE_POINTING, img, world)
    assert res.cmd == "S" and res.gesture == "Unknown pose"


def test_pointing_at_camera_is_low_confidence():
    img, world = rotated_to("I", 5, 8, 90)
    img = img.copy()
    img[6:9] = img[5] + (img[6:9] - img[5]) * 0.2     # index finger foreshortened in the image
    assert g.classify(g.MODE_POINTING, img, world).confidence < THRESHOLD


def test_sector_boundaries_and_hysteresis():
    assert g.pointing_sector(115)[0] == "G"
    assert g.pointing_sector(115, prev="F")[0] == "F"       # within 22.5 + 8 deg of forward: keep it
    assert g.pointing_sector(125, prev="F")[0] == "G"       # well past the boundary: switch
    assert g.pointing_sector(179)[0] == g.pointing_sector(-179)[0] == "L"
    assert g.pointing_sector(90)[1] == pytest.approx(1.0)
    assert g.pointing_sector(112.4)[1] == pytest.approx(0.75, abs=0.01)


# ---------------------------------------------------------------- hand-shape mode
@pytest.mark.parametrize("fingers,cmd,name", [
    ("TIMRP", "F", "Open palm"),
    ("IMRP", "F", "Open palm"),      # thumb doesn't matter for the palm
    ("IM", "B", "Peace (V)"),
    ("", "S", "Fist"),
])
@pytest.mark.parametrize("rotate", [0, 30, -30])
def test_shapes_without_steering(fingers, cmd, name, rotate):
    img, world = make_hand(fingers, rotate=rotate)
    res = g.classify(g.MODE_SHAPES, img, world)
    assert (res.cmd, res.gesture) == (cmd, name)
    assert res.confidence > 0.95


@pytest.mark.parametrize("fingers,left,right", [("T", "L", "R"), ("TI", "G", "I"), ("TP", "H", "J")])
def test_shapes_thumb_side_steers(fingers, left, right):
    # natural pose: the thumb sticks out to the left for one hand, to the right for the other
    for mirror, want in ((False, left), (True, right)):
        img, world = make_hand(fingers, mirror=mirror)
        res = g.classify(g.MODE_SHAPES, img, world)
        assert res.cmd == want, res.gesture
        assert res.confidence > 0.8


@pytest.mark.parametrize("target,cmd", [(180, "L"), (0, "R")])
def test_thumb_only_pointing_sideways(target, cmd):
    img, world = rotated_to("T", 2, 4, target)
    res = g.classify(g.MODE_SHAPES, img, world)
    assert res.cmd == cmd and res.confidence > 0.95


@pytest.mark.parametrize("target", [90, -90])
def test_thumb_up_or_down_does_not_steer(target):
    img, world = rotated_to("T", 2, 4, target)
    assert g.classify(g.MODE_SHAPES, img, world).confidence < THRESHOLD


def test_half_bent_fingers_are_unknown():
    img, world = make_hand("IMRP")
    curled_img, curled_world = make_hand("")
    world = (world + curled_world) / 2                # every finger half curled
    res = g.classify(g.MODE_SHAPES, (img + curled_img) / 2, world)
    assert res.confidence < THRESHOLD or res.cmd == "S"


def test_hand_score_scales_confidence():
    img, world = make_hand("IM")
    assert g.classify(g.MODE_SHAPES, img, world, hand_score=0.5).confidence == pytest.approx(0.5, abs=0.02)


# ---------------------------------------------------------------- filter
def _r(cmd, conf=0.9):
    return g.GestureResult(cmd=cmd, confidence=conf)


def test_filter_needs_consecutive_frames():
    f = g.GestureFilter(hold_frames=3)
    assert [f.update(_r("F"), THRESHOLD) for _ in range(4)] == ["S", "S", "F", "F"]


def test_filter_stops_immediately():
    f = g.GestureFilter(hold_frames=3)
    for _ in range(3):
        f.update(_r("F"), THRESHOLD)
    assert f.update(g.GestureResult(), THRESHOLD) == "S"           # hand gone
    for _ in range(3):
        f.update(_r("F"), THRESHOLD)
    assert f.update(_r("F", conf=0.4), THRESHOLD) == "S"            # low confidence


def test_filter_ignores_one_frame_glitch():
    f = g.GestureFilter(hold_frames=3)
    for _ in range(3):
        f.update(_r("F"), THRESHOLD)
    assert f.update(_r("G"), THRESHOLD) == "F"
    assert f.update(_r("F"), THRESHOLD) == "F"
    assert [f.update(_r("G"), THRESHOLD) for _ in range(3)] == ["F", "F", "G"]


def test_filter_remembers_candidate_for_hysteresis():
    f = g.GestureFilter()
    f.update(_r("G", conf=0.1), THRESHOLD)
    assert f.last_candidate == "G"
