"""Pi service configuration: environment variables plus a JSON hardware file.

The pin mapping and motor assignments come from the operator's *proposed* wiring. They are
treated as unverified until someone physically checks them and sets `pins_verified: true`.
Polarity, speed, travel limits and feedback wiring are never assumed.
"""
import json
import os
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

JointName = Literal["boom", "cabin", "bucket", "stick"]

# Raspberry Pi 5 (RP1) hardware PWM is available on GPIO12, 13, 18 and 19 only (PWM0 channels).
# Of the proposed enable pins only GPIO12 is one of them, so this service uses lgpio's
# software-timed PWM on every enable pin for uniform behaviour.
PI5_HARDWARE_PWM_PINS = frozenset({12, 13, 18, 19})
# BCM pins exposed on the 40-pin header.
HEADER_GPIOS = frozenset(range(2, 28))


class Channel(BaseModel):
    """One H-bridge channel: an enable pin (PWM) and two direction inputs."""
    model_config = ConfigDict(extra="forbid")
    driver: str
    side: Literal["A", "B"]
    enable: int
    in_a: int
    in_b: int


class FeedbackConfig(BaseModel):
    """A position sensor for one joint. Only "simulated" exists until real sensors are wired and a
    driver for them is added (see pi_service/README.md, Commissioning)."""
    model_config = ConfigDict(extra="forbid")
    kind: Literal["simulated"]
    units: str = "deg"


class JointConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: JointName
    channel: Channel
    invert: bool = False                     # polarity is unknown until commissioning
    max_duty: float = Field(0.35, gt=0, le=1.0)
    feedback: Optional[FeedbackConfig] = None
    # Travel limits in feedback units; None means "not calibrated" and closed-loop moves are refused.
    limits: Optional[tuple[float, float]] = None
    stall_timeout_ms: int = Field(1500, ge=100, le=10000)  # no progress for this long -> fault


class HardwareConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pins_verified: bool = False
    pwm_frequency_hz: int = Field(500, ge=50, le=10000)
    joints: list[JointConfig]

    @model_validator(mode="after")
    def _check_pins(self):
        names = [j.name for j in self.joints]
        if len(set(names)) != len(names):
            raise ValueError("each joint may appear only once")
        pins = [p for j in self.joints for p in (j.channel.enable, j.channel.in_a, j.channel.in_b)]
        if len(set(pins)) != len(pins):
            raise ValueError("a GPIO is assigned to more than one function")
        bad = [p for p in pins if p not in HEADER_GPIOS]
        if bad:
            raise ValueError(f"GPIOs not on the 40-pin header: {bad}")
        return self

    def joint(self, name) -> JointConfig:
        return next(j for j in self.joints if j.name == name)

    def pwm_notes(self):
        return [f"{j.name}: enable GPIO{j.channel.enable} "
                f"({'hardware-PWM capable' if j.channel.enable in PI5_HARDWARE_PWM_PINS else 'software PWM only'}; "
                f"this service uses lgpio software PWM)" for j in self.joints]


def _env(name, default, cast=str):
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    if cast is bool:
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    return cast(raw)


class ServiceSettings(BaseModel):
    backend: Literal["simulation", "hardware"] = "simulation"
    api_token: str = ""
    hardware_config: Path = Path(__file__).resolve().parent.parent / "config.example.json"
    watchdog_s: float = 1.0           # no heartbeat for this long while moving -> stop + fault latch
    max_ttl_ms: int = 2000            # longest a command may wait before it must start
    max_clock_uncertainty_ms: int = 250
    max_move_ms: int = 8000           # absolute cap on any single action
    max_jog_ms: int = 400
    commissioning: bool = False       # enables timed "jog" pulses; never used by the autonomous supervisor
    sim_speed_units_per_s: float = 30.0
    control_hz: float = 50.0
    gpiochip: Optional[int] = None    # None = auto-detect the RP1 chip

    @classmethod
    def from_env(cls):
        chip = _env("PI_GPIOCHIP", None)
        return cls(
            backend=_env("PI_BACKEND", "simulation"),
            api_token=_env("PI_API_TOKEN", ""),
            hardware_config=Path(_env("PI_HARDWARE_CONFIG", str(cls.model_fields["hardware_config"].default))),
            watchdog_s=_env("PI_WATCHDOG_S", 1.0, float),
            max_ttl_ms=_env("PI_MAX_TTL_MS", 2000, int),
            max_move_ms=_env("PI_MAX_MOVE_MS", 8000, int),
            max_jog_ms=_env("PI_MAX_JOG_MS", 400, int),
            commissioning=_env("PI_COMMISSIONING", False, bool),
            sim_speed_units_per_s=_env("PI_SIM_SPEED", 30.0, float),
            gpiochip=int(chip) if chip is not None else None,
        )


def load_hardware_config(path: Path) -> HardwareConfig:
    data = json.loads(Path(path).read_text())
    return HardwareConfig.model_validate({k: v for k, v in data.items() if not k.startswith("_")})
