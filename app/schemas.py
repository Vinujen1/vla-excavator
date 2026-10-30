"""Pydantic models shared by the API, observations and planner providers."""
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class BBox(BaseModel):
    x1: int
    y1: int
    x2: int
    y2: int

    @property
    def centre(self):
        return ((self.x1 + self.x2) / 2, (self.y1 + self.y2) / 2)


class DepthEstimate(BaseModel):
    status: Literal["ok", "inconsistent", "no_valid_depth", "unavailable"]
    depth_m: Optional[float] = None          # median of valid pixels in the target ROI
    spread_m: Optional[float] = None         # interquartile range of those pixels
    valid_fraction: float = 0.0
    roi: Optional[BBox] = None
    xyz_camera_m: Optional[tuple[float, float, float]] = None  # camera frame, metres
    reason: Optional[str] = None


class Detection(BaseModel):
    id: str                                  # unique within one detection frame only
    source: Literal["yolo", "color_marker"]
    label: str
    class_id: Optional[int] = None
    confidence: Optional[float] = None       # None for colour-marker candidates
    bbox: BBox
    depth: DepthEstimate


class DetectionFrame(BaseModel):
    frame_number: int
    frame_time: float
    inference_ms: Optional[float] = None
    marker_ms: Optional[float] = None
    detections: list[Detection]


class Target(BaseModel):
    detection: Detection
    selected_at: float
    first_frame_number: int
    last_seen_frame_number: int
    last_seen_time: float
    age_s: float = 0.0
    stale: bool = False
    stale_reason: Optional[str] = None
    association: str = "IoU >= 0.3 with same label between consecutive detection frames (not a full tracker)"


class Instruction(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    source: Literal["typed", "jetson_mic", "browser_mic"]
    audio_device: Optional[str] = None
    recording: Optional[str] = None
    transcribed_at: Optional[float] = None


class RobotState(BaseModel):
    pi_connected: bool
    motion_controller: str
    joint_state: Optional[dict] = None       # None until the Pi provides it
    sensors: list[dict] = []
    backend: Optional[Literal["simulation", "hardware"]] = None
    latched: Optional[str] = None
    fault: Optional[dict] = None
    telemetry_age_s: Optional[float] = None
    observed_at: Optional[float] = None


class Observation(BaseModel):
    id: str
    created_at: float
    instruction: Instruction
    target: Optional[Target]
    scene: list[Detection]
    scene_frame_number: Optional[int]
    scene_frame_time: Optional[float]
    camera: dict
    robot_state: RobotState
    coordinate_note: str = ("Positions are in the camera frame (x right, y down, z forward, metres). "
                            "No camera-to-excavator calibration has been supplied.")


TaskAction = Literal["select_target", "approach", "scoop", "lift", "rotate", "dump", "return", "wait_for_operator"]


class TaskStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    index: int = Field(ge=1)
    action: TaskAction
    description: str = Field(min_length=1, max_length=300)
    parameters: dict[str, Optional[str | float | int | list[float]]] = {}


class TaskProposal(BaseModel):
    """A high-level proposal for the operator to review. Never a motor trajectory."""
    model_config = ConfigDict(extra="forbid")
    id: str
    created_at: float
    provider: str
    provider_kind: Literal["reasoning_model", "demo_planner"]
    observation_id: str
    instruction: str
    target_summary: Optional[str]
    steps: list[TaskStep] = Field(min_length=1, max_length=12)
    warnings: list[str] = []
    assumptions: list[str] = []
    executable: Literal[False] = False

    @field_validator("steps")
    @classmethod
    def _ordered(cls, steps):
        if [s.index for s in steps] != list(range(1, len(steps) + 1)):
            raise ValueError("step indices must be 1..N in order")
        return steps


# ---- autonomous task (bounded scoop-and-dump) ----------------------------
SkillName = Literal["rotate_to_dig", "lower_to_dig", "scoop", "lift", "rotate_to_dump", "dump", "return_home"]
SKILLS: tuple[str, ...] = SkillName.__args__
DecisionChoice = Literal["rotate_to_dig", "lower_to_dig", "scoop", "lift", "rotate_to_dump", "dump", "return_home",
                         "pause", "retry_last", "complete"]


class GoalProposal(BaseModel):
    """What a decision provider may say about the operator's instruction. Deliberately has no
    distances, coordinates or motor fields: geometry comes from calibration and sensors only."""
    model_config = ConfigDict(extra="forbid")
    intent: Literal["scoop_and_dump", "identify", "unsupported", "clarify"]
    target_id: Optional[str] = Field(None, max_length=20)   # a detection id from the observation
    cycles: Optional[int] = Field(None, ge=1, le=20)
    explanation: str = Field(min_length=1, max_length=600)
    questions: list[str] = Field(default=[], max_length=3)


class SkillDecision(BaseModel):
    """The provider's choice of the next supported skill (or pause/retry/complete)."""
    model_config = ConfigDict(extra="forbid")
    next_skill: DecisionChoice
    explanation: str = Field(min_length=1, max_length=400)
    target_id: Optional[str] = Field(None, max_length=20)


class TargetRef(BaseModel):
    detection_id: str
    label: str
    source: str
    frame_number: int
    depth_status: str
    depth_m: Optional[float] = None
    xyz_camera_m: Optional[tuple[float, float, float]] = None
    xyz_base_m: Optional[tuple[float, float, float]] = None   # only with a hardware camera-to-base calibration


class TaskSpec(BaseModel):
    """An operator-approved, bounded task. Built deterministically; the provider only proposes."""
    task_id: str
    goal: Literal["scoop_and_dump"]
    instruction: Instruction
    observation_id: str
    mode: Literal["simulation", "hardware"]
    provider: str
    cycles: int = Field(ge=1)
    timeout_s: float = Field(gt=0)
    target: Optional[TargetRef] = None
    plan: list[SkillName]
    explanation: str
    warnings: list[str] = []
    start_requested: bool = False
    created_at: float
    approved_at: Optional[float] = None
