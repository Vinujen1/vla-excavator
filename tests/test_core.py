"""Hardware-free tests for target association/staleness, depth estimation, markers and schemas.

Run: .venv/bin/python -m pytest -q tests
"""
import time

import cv2
import numpy as np
import pytest
from pydantic import ValidationError

from app import targets
from app.config import settings
from app.detection import MarkerSettings, find_markers
from app.planner import DemoPlanner
from app.schemas import (BBox, DepthEstimate, Detection, DetectionFrame, Instruction, Observation, RobotState,
                         TaskProposal)
from app.targets import TargetSelector, estimate_depth

SCALE = 0.001


class FakeCamera:
    status = "connected"


def det(id_, label, box, depth_m=1.0):
    return Detection(id=id_, source="yolo", label=label, confidence=0.9, bbox=BBox(x1=box[0], y1=box[1], x2=box[2], y2=box[3]),
                     depth=DepthEstimate(status="ok", depth_m=depth_m))


def frame(n, dets, t=None):
    return DetectionFrame(frame_number=n, frame_time=t if t is not None else time.time(), detections=dets)


# ---- depth -----------------------------------------------------------
def test_depth_median_and_xyz_on_flat_surface():
    depth = np.full((480, 640), 1500, np.uint16)  # 1.5 m everywhere
    est = estimate_depth(depth, SCALE, BBox(x1=300, y1=200, x2=340, y2=280))
    assert est.status == "ok" and est.depth_m == pytest.approx(1.5) and est.spread_m == pytest.approx(0)


def test_depth_rejects_missing_pixels():
    depth = np.zeros((480, 640), np.uint16)
    depth[::4, ::4] = 1500  # ~6% valid
    est = estimate_depth(depth, SCALE, BBox(x1=300, y1=200, x2=340, y2=280))
    assert est.status == "no_valid_depth" and est.depth_m is None


def test_depth_rejects_out_of_range():
    depth = np.full((480, 640), 150, np.uint16)  # 0.15 m: closer than the D435i minimum
    assert estimate_depth(depth, SCALE, BBox(x1=300, y1=200, x2=340, y2=280)).status == "no_valid_depth"


def test_depth_flags_mixed_surfaces():
    depth = np.full((480, 640), 1000, np.uint16)
    depth[:, 320:] = 3000  # half the ROI is background 2 m further away
    est = estimate_depth(depth, SCALE, BBox(x1=280, y1=200, x2=360, y2=280))
    assert est.status == "inconsistent" and "background" in est.reason


def test_depth_unavailable_without_frame():
    assert estimate_depth(None, SCALE, BBox(x1=0, y1=0, x2=10, y2=10)).status == "unavailable"


# ---- selection / association / staleness ----------------------------
def test_target_follows_matching_detection():
    sel = TargetSelector(FakeCamera())
    sel.select(frame(1, [det("y0", "person", (100, 100, 200, 300))]), "y0")
    sel.update(frame(2, [det("y3", "person", (105, 102, 205, 302)), det("y0", "chair", (100, 100, 200, 300))]))
    t = sel.state()
    assert t.detection.id == "y3" and t.last_seen_frame_number == 2 and not t.stale


def test_target_does_not_jump_to_other_class_or_far_box():
    sel = TargetSelector(FakeCamera())
    sel.select(frame(1, [det("y0", "person", (100, 100, 200, 300))]), "y0")
    sel.update(frame(2, [det("y0", "chair", (100, 100, 200, 300)), det("y1", "person", (400, 100, 500, 300))]))
    assert sel.state().last_seen_frame_number == 1


def test_target_goes_stale_without_matches(monkeypatch):
    monkeypatch.setattr(targets.settings.__class__, "target_stale_s", 0.2, raising=False)
    sel = TargetSelector(FakeCamera())
    sel.select(frame(1, [det("y0", "cup", (10, 10, 50, 50))], t=time.time() - 5), "y0")
    t = sel.state()
    assert t.stale and "not matched" in t.stale_reason


def test_target_stale_when_camera_down():
    cam = FakeCamera()
    sel = TargetSelector(cam)
    sel.select(frame(1, [det("y0", "cup", (10, 10, 50, 50))]), "y0")
    cam.status = "disconnected"
    t = sel.state()
    assert t.stale and t.stale_reason == "camera disconnected"


def test_select_unknown_id_raises():
    with pytest.raises(KeyError):
        TargetSelector(FakeCamera()).select(frame(1, []), "y9")


# ---- colour markers -------------------------------------------------
def test_red_marker_hue_wraparound():
    img = np.full((240, 320, 3), 128, np.uint8)
    cv2.rectangle(img, (40, 40), (80, 100), (0, 0, 230), -1)      # red  (hue ~0)
    cv2.rectangle(img, (200, 40), (240, 100), (40, 0, 230), -1)   # red past the wrap (hue ~175)
    cv2.rectangle(img, (120, 150), (160, 200), (0, 200, 0), -1)   # green: must be ignored
    boxes = find_markers(img, MarkerSettings(enabled=True))
    assert len(boxes) == 2 and all(b.y2 <= 101 for b in boxes)


# ---- planner & schemas -----------------------------------------------
def _obs(target=None, text="Scoop beside the red marker."):
    return Observation(id="o1", created_at=time.time(), instruction=Instruction(text=text, source="typed"), target=target,
                       scene=[], scene_frame_number=None, scene_frame_time=None, camera={},
                       robot_state=RobotState(pi_connected=False, motion_controller="Not connected"))


def test_demo_planner_flags_label_mismatch_and_is_not_executable():
    sel = TargetSelector(FakeCamera())
    sel.select(frame(1, [det("y0", "person", (100, 100, 200, 300))]), "y0")
    p = DemoPlanner().propose(_obs(sel.state()))
    assert [s.action for s in p.steps][:4] == ["select_target", "approach", "scoop", "lift"]
    assert p.executable is False and p.provider_kind == "demo_planner"
    assert any("marker, red" in w and "person" in w for w in p.warnings)
    assert p.steps[1].parameters["relation"] == "adjacent to"


def test_proposal_schema_rejects_unknown_actions_and_execution():
    base = dict(id="p", created_at=0, provider="x", provider_kind="reasoning_model", observation_id="o",
                instruction="i", target_summary=None)
    with pytest.raises(ValidationError):
        TaskProposal(**base, steps=[{"index": 1, "action": "set_joint_velocity", "description": "d"}])
    with pytest.raises(ValidationError):
        TaskProposal(**base, steps=[{"index": 1, "action": "scoop", "description": "d"}], executable=True)
    with pytest.raises(ValidationError):
        TaskProposal(**base, steps=[{"index": 2, "action": "scoop", "description": "d"}])
