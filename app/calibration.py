"""Workspace calibration: named joint targets for each skill, approved dig/dump regions and the
camera-to-base transform.

* config/calibration.json (kind "hardware") must come from supervised commissioning. It doesn't
  exist yet, so hardware execution is blocked.
* config/calibration.simulation.json (kind "simulation") drives the simulated Pi only. It is
  never accepted for hardware execution.
"""
import json
from pathlib import Path
from typing import Literal, Optional

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

JOINTS = ("boom", "cabin", "bucket", "stick")

# Named joint targets each skill needs. Every skill is executed as one joint move at a time.
SKILL_MOVES: dict[str, list[tuple[str, str]]] = {
    "rotate_to_dig": [("boom", "carry"), ("cabin", "dig")],
    "lower_to_dig": [("bucket", "open"), ("stick", "dig"), ("boom", "dig")],
    "scoop": [("stick", "scoop_end"), ("bucket", "curled")],
    "lift": [("boom", "carry")],
    "rotate_to_dump": [("cabin", "dump")],
    "dump": [("bucket", "open")],
    "return_home": [("bucket", "home"), ("stick", "home"), ("boom", "home"), ("cabin", "home")],
}


class Region(BaseModel):
    """Axis-aligned box in the excavator base frame, metres."""
    model_config = ConfigDict(extra="forbid")
    min_xyz: tuple[float, float, float]
    max_xyz: tuple[float, float, float]

    def contains(self, p):
        return all(lo <= v <= hi for v, lo, hi in zip(p, self.min_xyz, self.max_xyz))


class Calibration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["simulation", "hardware"]
    version: int = 1
    verified: bool = False                      # set true only after supervised commissioning
    verified_by: Optional[str] = None
    verified_at: Optional[str] = None
    joint_units: dict[str, str]
    joint_limits: dict[str, tuple[float, float]]
    joint_targets: dict[str, dict[str, float]]
    tolerance: dict[str, float]
    camera_to_base: Optional[list[list[float]]] = None   # 4x4, camera frame -> base frame
    dig_region: Optional[Region] = None
    dump_region: Optional[Region] = None
    notes: str = ""

    @model_validator(mode="after")
    def _check(self):
        for skill, moves in SKILL_MOVES.items():
            for joint, name in moves:
                if name not in self.joint_targets.get(joint, {}):
                    raise ValueError(f"skill {skill} needs joint_targets.{joint}.{name}")
        for joint, targets in self.joint_targets.items():
            lo, hi = self.joint_limits[joint]
            for name, v in targets.items():
                if not lo <= v <= hi:
                    raise ValueError(f"joint_targets.{joint}.{name}={v} outside limits [{lo}, {hi}]")
        if self.camera_to_base is not None:
            m = np.asarray(self.camera_to_base, dtype=float)
            if m.shape != (4, 4) or not np.allclose(m[3], [0, 0, 0, 1]):
                raise ValueError("camera_to_base must be a 4x4 homogeneous transform")
        return self

    def to_base(self, xyz_camera):
        if self.camera_to_base is None or xyz_camera is None:
            return None
        p = np.asarray(self.camera_to_base, dtype=float) @ np.array([*xyz_camera, 1.0])
        return tuple(round(float(v), 3) for v in p[:3])

    def moves(self, skill):
        return [{"joint": j, "target": self.joint_targets[j][name], "tolerance": self.tolerance[j], "pose": name}
                for j, name in SKILL_MOVES[skill]]

    def summary(self):
        return {"kind": self.kind, "verified": self.verified, "verified_by": self.verified_by,
                "camera_to_base": self.camera_to_base is not None,
                "dig_region": self.dig_region is not None, "dump_region": self.dump_region is not None}


def load(path: Path) -> tuple[Optional[Calibration], Optional[str]]:
    """(calibration, error). A missing file is reported, not raised."""
    if not Path(path).exists():
        return None, f"{path} not found"
    try:
        data = json.loads(Path(path).read_text())
        return Calibration.model_validate({k: v for k, v in data.items() if not k.startswith("_")}), None
    except Exception as exc:
        return None, f"{Path(path).name} is invalid: {exc}"[:400]
