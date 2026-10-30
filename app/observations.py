"""Timestamped observations: instruction + target + scene + camera frame + robot state, saved to disk
(bounded by VLA_KEEP_OBSERVATIONS)."""
import json
import time
from datetime import datetime

import cv2
import numpy as np

from . import retention
from .config import settings
from .schemas import Instruction, Observation, TaskProposal

OBSERVATIONS = settings.data_dir / "observations"


def build(instruction: Instruction, camera, detector, selector, hardware, require_camera=True):
    """Snapshot the current state. Without a camera frame this raises, unless require_camera is
    False (simulation planning), in which case the observation records that no image exists."""
    frame = camera.latest()
    if frame is None and require_camera:
        raise RuntimeError(f"Camera not streaming ({camera.status}: {camera.error})")
    scene = detector.latest() if frame is not None else None
    now = time.time()
    obs = Observation(
        id=datetime.fromtimestamp(now).strftime("obs_%Y%m%d_%H%M%S_%f")[:-3],
        created_at=now,
        instruction=instruction,
        target=selector.state(),
        scene=scene.detections if scene else [],
        scene_frame_number=scene.frame_number if scene else None,
        scene_frame_time=scene.frame_time if scene else None,
        camera={
            "status": camera.status,
            "device": camera.device_info,
            "intrinsics": camera.intrinsics,
            "frame_seq": frame["seq"] if frame else None,
            "frame_time": frame["host_time"] if frame else None,
            "device_timestamp_ms": frame["device_timestamp_ms"] if frame else None,
            "timestamp_domain": frame["timestamp_domain"] if frame else None,
            "frame_age_at_observation_s": round(now - frame["host_time"], 3) if frame else None,
        },
        robot_state=hardware.robot_state(),
    )
    return obs, frame


def jpeg(frame, max_side=640, quality=85):
    """The frame's colour image as JPEG bytes for a reasoning request (None without a frame)."""
    if frame is None:
        return None
    img = frame["color"]
    h, w = img.shape[:2]
    if max(h, w) > max_side:
        s = max_side / max(h, w)
        img = cv2.resize(img, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return buf.tobytes() if ok else None


def save(obs: Observation, frame, proposal: TaskProposal | None, extra: dict | None = None):
    out = OBSERVATIONS / obs.id
    out.mkdir(parents=True, exist_ok=True)
    if frame is not None:
        cv2.imwrite(str(out / "color.jpg"), frame["color"], [cv2.IMWRITE_JPEG_QUALITY, 95])
        if frame["depth"] is not None:
            np.save(out / "depth_aligned_u16.npy", frame["depth"])
    (out / "observation.json").write_text(obs.model_dump_json(indent=2))
    if proposal is not None:
        (out / "proposal.json").write_text(proposal.model_dump_json(indent=2))
    if extra is not None:
        (out / "decision.json").write_text(json.dumps(extra, indent=2, default=str))
    retention.prune(OBSERVATIONS, settings.keep_observations, "obs_*")
    return out


def recent(limit=20):
    if not OBSERVATIONS.exists():
        return []
    items = []
    for p in sorted((p for p in OBSERVATIONS.iterdir() if p.is_dir()), reverse=True)[:limit]:
        try:
            meta = json.loads((p / "observation.json").read_text())
            items.append({"id": p.name, "instruction": meta["instruction"]["text"], "created_at": meta["created_at"],
                          "has_proposal": (p / "proposal.json").exists(), "has_decision": (p / "decision.json").exists(),
                          "has_image": (p / "color.jpg").exists()})
        except Exception:
            continue
    return items
