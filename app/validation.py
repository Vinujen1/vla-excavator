"""Deterministic checks between a provider's proposal and anything the Pi is asked to do.

The provider (Cosmos 3 or the rule-based development provider) only proposes an intent, a
target id and the next skill. Everything here decides from measured state: Pi telemetry,
calibration, camera freshness and the operator-selected target.
"""
from dataclasses import dataclass, field
from typing import Optional

from .calibration import JOINTS, SKILL_MOVES, Calibration
from .calibration import load as load_calibration
from .config import settings
from .schemas import GoalProposal, Observation, SkillDecision, Target, TargetRef

CAMERA_MAX_AGE_S = 1.0
FEEDBACK_MAX_AGE_S = 0.5
DIG_SKILLS = {"rotate_to_dig", "lower_to_dig", "scoop"}


class Calibrations:
    """Loads the simulation and hardware calibration files; reload() picks up edits."""

    def __init__(self, hardware_path=None, simulation_path=None):
        self._paths = {"hardware": hardware_path or settings.calibration_file,
                       "simulation": simulation_path or settings.sim_calibration_file}
        self.reload()

    def reload(self):
        self._loaded = {kind: load_calibration(path) for kind, path in self._paths.items()}

    def get(self, mode) -> tuple[Optional[Calibration], Optional[str]]:
        if mode not in self._loaded:
            return None, "no execution mode"
        cal, err = self._loaded[mode]
        if cal is not None and cal.kind != mode:
            return None, f"{self._paths[mode].name} has kind '{cal.kind}', expected '{mode}'"
        return cal, err

    def summary(self):
        out = {}
        for kind, (cal, err) in self._loaded.items():
            out[kind] = {"file": str(self._paths[kind].name), "loaded": cal is not None, "error": err,
                         **(cal.summary() if cal else {})}
        return out


@dataclass
class ExecContext:
    mode: Optional[str]                    # "simulation" | "hardware" | None (no fresh Pi state)
    pi_status: str
    pi_state: Optional[dict]
    calibration: Optional[Calibration]
    calibration_error: Optional[str]
    camera_status: str
    camera_frame_age_s: Optional[float]
    target: Optional[Target]
    notes: list[str] = field(default_factory=list)


def build_context(hardware, camera, selector, calibrations: Calibrations) -> ExecContext:
    pi = hardware.fresh_state()
    mode = pi.get("backend") if pi else None
    cal, err = calibrations.get(mode) if mode else (None, None)
    cam = camera.state()
    return ExecContext(mode=mode, pi_status=hardware.status, pi_state=pi, calibration=cal, calibration_error=err,
                       camera_status=cam["status"], camera_frame_age_s=cam.get("frame_age_s"), target=selector.state())


def execution_blockers(ctx: ExecContext, provider_kind: Optional[str] = None) -> list[str]:
    """Reasons the approved task can't run right now. Empty means the deterministic checks pass."""
    b = []
    if ctx.mode is None:
        b.append(f"Raspberry Pi motion service not available (link: {ctx.pi_status}, or telemetry older than "
                 f"{settings.pi_state_max_age_s:.1f} s)")
        return b
    pi = ctx.pi_state
    if pi.get("fault"):
        b.append(f"Pi fault: {pi['fault']['code']} ({pi['fault']['message']}); rearm after resolving it")
    elif pi.get("latched"):
        b.append(f"Pi controller latched ({pi['latched']}); explicit rearm required")
    if pi.get("current_action"):
        b.append("Pi is still executing an action")

    cal = ctx.calibration
    if cal is None:
        b.append(f"No {ctx.mode} calibration ({ctx.calibration_error})")
    if ctx.mode == "hardware":
        if not pi.get("pins_verified"):
            b.append("Pi pin mapping is not physically verified (pins_verified=false)")
        if cal is not None:
            if not cal.verified:
                b.append("Hardware calibration is not marked verified")
            if cal.camera_to_base is None:
                b.append("No camera-to-excavator calibration (camera_to_base)")
            if cal.dig_region is None or cal.dump_region is None:
                b.append("Approved dig and dump regions are not defined in the calibration")
        if ctx.camera_status != "connected" or (ctx.camera_frame_age_s or 99) > CAMERA_MAX_AGE_S:
            b.append(f"Camera not live ({ctx.camera_status})")
        if provider_kind == "rules" and not settings.rules_provider_on_hardware:
            b.append("Hardware execution needs the Cosmos 3 provider; the rule-based provider is for development")

    joints = pi.get("joints", {})
    for j in JOINTS:
        info = joints.get(j)
        if info is None:
            b.append(f"Pi reports no '{j}' joint")
            continue
        if info.get("feedback") is None:
            b.append(f"No position feedback for {j}")
        elif info.get("feedback_age_s") is None or info["feedback_age_s"] > FEEDBACK_MAX_AGE_S:
            b.append(f"{j} feedback is stale")
        if info.get("limits") is None:
            b.append(f"{j} travel limits are not calibrated on the Pi")
        elif cal is not None:
            lo, hi = info["limits"]
            for name, v in cal.joint_targets.get(j, {}).items():
                if not lo <= v <= hi:
                    b.append(f"Calibration target {j}.{name}={v} is outside the Pi limits [{lo}, {hi}]")
        if ctx.mode == "hardware" and info.get("feedback") == "simulated":
            b.append(f"{j} uses simulated feedback on the hardware backend")
    return b


def target_ref(ctx: ExecContext) -> Optional[TargetRef]:
    t = ctx.target
    if t is None:
        return None
    d = t.detection
    base = ctx.calibration.to_base(d.depth.xyz_camera_m) if (ctx.calibration and ctx.mode == "hardware") else None
    return TargetRef(detection_id=d.id, label=d.label, source=d.source, frame_number=t.last_seen_frame_number,
                     depth_status=d.depth.status, depth_m=d.depth.depth_m, xyz_camera_m=d.depth.xyz_camera_m,
                     xyz_base_m=base)


def target_issues(ctx: ExecContext) -> tuple[list[str], list[str]]:
    """(blocking issues, warnings) for the operator-selected target in the current mode."""
    hard = ctx.mode == "hardware"
    blocking, warn = [], []
    issue = blocking.append if hard else warn.append
    t = ctx.target
    if t is None:
        issue("No target selected" + ("" if hard else " (simulation runs against the simulated dig pose)"))
        return blocking, warn
    if t.stale:
        issue(f"Target is stale: {t.stale_reason}")
    if t.detection.depth.status != "ok":
        issue(f"Target depth is {t.detection.depth.status.replace('_', ' ')}")
    if hard:
        ref = target_ref(ctx)
        if ref is None or ref.xyz_base_m is None:
            blocking.append("Target has no base-frame position (needs valid depth and camera_to_base)")
        elif ctx.calibration.dig_region and not ctx.calibration.dig_region.contains(ref.xyz_base_m):
            blocking.append(f"Target at {ref.xyz_base_m} m (base frame) is outside the approved dig region")
    else:
        warn.append("Simulation: target reachability is not evaluated (no camera-to-excavator calibration)")
    return blocking, warn


@dataclass
class GoalCheck:
    ok: bool
    errors: list[str]
    warnings: list[str]
    cycles: int
    target_id: Optional[str]


def validate_goal(p: GoalProposal, obs: Observation, operator_cycles: Optional[int]) -> GoalCheck:
    errors, warnings = [], []
    if p.intent != "scoop_and_dump":
        errors.append(f"Provider interpreted the instruction as '{p.intent}', not a supported scoop-and-dump task")
    scene_ids = {d.id for d in obs.scene}
    selected = obs.target.detection.id if obs.target else None
    target_id = p.target_id
    if target_id is not None and target_id not in scene_ids and target_id != selected:
        errors.append(f"Provider referred to target '{target_id}', which is not in the observation")
    elif target_id is not None and selected is not None and target_id != selected:
        errors.append(f"Provider chose '{target_id}' but the operator selected '{selected}': ambiguous target, "
                      "select the intended object and plan again")
    elif target_id is None and selected is not None:
        warnings.append("Provider did not name a target; using the operator-selected target")
        target_id = selected
    elif target_id is not None and selected is None:
        errors.append(f"Provider proposed target '{target_id}' but no target is selected: confirm it by selecting it")
    if operator_cycles is not None:
        cycles = operator_cycles
        if p.cycles is not None and p.cycles != operator_cycles:
            warnings.append(f"Provider said {p.cycles} cycle(s); using the {operator_cycles} the operator stated")
    elif p.cycles is not None:
        cycles = p.cycles
        warnings.append(f"Cycle count {cycles} was inferred by the provider, not stated; check it before arming")
    else:
        cycles = 1
        warnings.append("No cycle count stated; defaulting to 1")
    if cycles > settings.max_cycles:
        errors.append(f"{cycles} cycles exceeds the limit of {settings.max_cycles}")
    return GoalCheck(ok=not errors, errors=errors, warnings=warnings, cycles=cycles, target_id=target_id)


def validate_decision(d: SkillDecision, expected: Optional[str], obs: Observation, retry_available: bool):
    """Returns ("accept", skill) / ("retry", skill) / ("complete", None) / ("pause", reason)."""
    if d.target_id is not None and obs.target is not None and d.target_id != obs.target.detection.id:
        return "pause", f"Provider referred to '{d.target_id}', not the tracked target '{obs.target.detection.id}'"
    if d.next_skill == "pause":
        return "pause", f"Provider requested a pause: {d.explanation}"
    if d.next_skill == "complete":
        if expected is None:
            return "complete", None
        return "pause", f"Provider said the task is complete, but the next planned skill is {expected}"
    if d.next_skill == "retry_last":
        if retry_available:
            return "retry", None
        return "pause", "Provider asked for a retry, but no bounded retry is available for this skill"
    if expected is None:
        return "pause", f"Provider proposed {d.next_skill} after the task finished"
    if d.next_skill != expected:
        return "pause", f"Provider proposed {d.next_skill}; the approved plan allows only {expected} next"
    return "accept", expected


def skill_moves(skill: str, cal: Calibration, pi_state: dict) -> list[dict]:
    """Joint moves for a skill, each checked against the Pi-reported limits."""
    if skill not in SKILL_MOVES:
        raise ValueError(f"unknown skill {skill}")
    moves = cal.moves(skill)
    for m in moves:
        info = pi_state["joints"].get(m["joint"]) or {}
        lim = info.get("limits")
        if lim is None or not lim[0] <= m["target"] <= lim[1]:
            raise ValueError(f"{skill}: {m['joint']} target {m['target']} not within Pi limits {lim}")
    return moves
