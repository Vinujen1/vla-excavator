"""Runtime settings, all overridable by environment variables (see README)."""
import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _env(name, default, cast=str):
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    if cast is bool:
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    return cast(raw)


@dataclass(frozen=True)
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(_env("VLA_DATA_DIR", str(ROOT / "data"))))

    # Camera (640x480 @ 30 is verified drop-free on the current USB2 link)
    camera_width: int = _env("VLA_CAMERA_WIDTH", 640, int)
    camera_height: int = _env("VLA_CAMERA_HEIGHT", 480, int)
    camera_fps: int = _env("VLA_CAMERA_FPS", 30, int)

    # Browser video stream (encoding is shared between clients)
    stream_fps: int = _env("VLA_STREAM_FPS", 15, int)
    jpeg_quality: int = _env("VLA_JPEG_QUALITY", 75, int)

    # Object detection
    detect_enabled: bool = _env("VLA_DETECT", True, bool)
    yolo_model: str = _env("VLA_YOLO_MODEL", str(ROOT / "models" / "yolo11n.pt"))
    yolo_imgsz: int = _env("VLA_YOLO_IMGSZ", 640, int)
    yolo_device: str = _env("VLA_YOLO_DEVICE", "cuda:0")
    yolo_half: bool = _env("VLA_YOLO_HALF", True, bool)
    detect_max_fps: float = _env("VLA_DETECT_MAX_FPS", 15.0, float)
    detect_conf: float = _env("VLA_DETECT_CONF", 0.35, float)

    # Target depth
    depth_min_m: float = _env("VLA_DEPTH_MIN_M", 0.28, float)   # D435i minimum range
    depth_max_m: float = _env("VLA_DEPTH_MAX_M", 8.0, float)
    target_stale_s: float = _env("VLA_TARGET_STALE_S", 1.5, float)

    # Audio / speech
    record_max_s: int = _env("VLA_RECORD_MAX_S", 15, int)
    whisper_model: str = _env("WHISPER_MODEL", "base.en")

    listen_max_s: int = _env("VLA_LISTEN_MAX_S", 60, int)        # listening mode ends automatically
    listen_chunk_s: int = _env("VLA_LISTEN_CHUNK_S", 3, int)

    # Cosmos 3 reasoner behind an OpenAI-compatible endpoint (vLLM `vllm serve nvidia/Cosmos3-Edge`
    # or the Cosmos 3 Reasoner NIM). Unset = not connected. Keys stay on the backend only.
    cosmos_base_url: str = _env("VLA_COSMOS_BASE_URL", "")
    cosmos_model: str = _env("VLA_COSMOS_MODEL", "nvidia/Cosmos3-Edge")
    cosmos_api_key: str = _env("VLA_COSMOS_API_KEY", "")
    cosmos_timeout_s: float = _env("VLA_COSMOS_TIMEOUT_S", 30.0, float)
    cosmos_enable_thinking: str = _env("VLA_COSMOS_ENABLE_THINKING", "")  # "", "true" or "false"
    cosmos_max_tokens: int = _env("VLA_COSMOS_MAX_TOKENS", 1024, int)

    # Raspberry Pi motion service
    pi_endpoint: str = _env("VLA_PI_ENDPOINT", "")
    pi_api_token: str = _env("VLA_PI_API_TOKEN", "")
    pi_state_max_age_s: float = _env("VLA_PI_STATE_MAX_AGE_S", 1.0, float)

    # Autonomous task limits
    calibration_file: Path = field(default_factory=lambda: Path(_env("VLA_CALIBRATION_FILE", str(ROOT / "config" / "calibration.json"))))
    sim_calibration_file: Path = field(default_factory=lambda: Path(_env("VLA_SIM_CALIBRATION_FILE", str(ROOT / "config" / "calibration.simulation.json"))))
    max_cycles: int = _env("VLA_MAX_CYCLES", 5, int)
    default_task_timeout_s: float = _env("VLA_TASK_TIMEOUT_S", 300.0, float)
    max_task_timeout_s: float = _env("VLA_MAX_TASK_TIMEOUT_S", 900.0, float)
    decision_max_age_s: float = _env("VLA_DECISION_MAX_AGE_S", 45.0, float)
    approval_max_age_s: float = _env("VLA_APPROVAL_MAX_AGE_S", 120.0, float)
    action_ttl_ms: int = _env("VLA_ACTION_TTL_MS", 1500, int)
    move_timeout_ms: int = _env("VLA_MOVE_TIMEOUT_MS", 6000, int)
    move_max_duty: float = _env("VLA_MOVE_MAX_DUTY", 0.3, float)
    rules_provider_on_hardware: bool = _env("VLA_ALLOW_RULES_PROVIDER_ON_HARDWARE", False, bool)

    # Disk bounds
    keep_observations: int = _env("VLA_KEEP_OBSERVATIONS", 200, int)
    keep_recordings: int = _env("VLA_KEEP_RECORDINGS", 50, int)
    keep_session_logs: int = _env("VLA_KEEP_SESSION_LOGS", 20, int)
    session_log_max_mb: float = _env("VLA_SESSION_LOG_MAX_MB", 20.0, float)

    telemetry_hz: float = _env("VLA_TELEMETRY_HZ", 15.0, float)


settings = Settings()
