"""Motion controller core, independent of the HTTP layer so it can be tested directly.

Safety rules implemented here:
* Starts latched ("startup"); nothing moves until an explicit rearm.
* One action at a time; a busy controller rejects new actions instead of queueing them.
* Every action is bounded (timeout, duty cap, travel limits, stall detection).
* Closed-loop moves need fresh position feedback and calibrated limits for that joint.
* A timed "jog" pulse is commissioning-only and is reported as unverified (no position proof).
* Stop and every fault switch all outputs off and latch. Only an explicit rearm clears the latch;
  nothing resumes automatically after a reconnect or reboot.
* Heartbeat watchdog: losing the Jetson heartbeat while armed stops and latches.

Expiry semantics (clock differences between Jetson and Pi):
  Each command carries `issued_at_ms` (sender clock) and `ttl_ms`. The Pi estimates the clock
  offset from heartbeats as min(pi_receive_ms - sender_sent_ms) over recent samples, so the
  estimate includes the smallest observed network delay. A command's age is
  pi_now_ms - (issued_at_ms + offset). It is rejected if age > ttl_ms, if it appears to come
  from the future by more than max_clock_uncertainty_ms, or if no heartbeat has established an
  offset yet. Expiry only bounds how long a command may wait before it starts; the motion itself
  is bounded separately by timeout_ms. Commands are never queued, so they can't start late.
"""
import collections
import threading
import time
import uuid
from typing import Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .config import HardwareConfig, JointName, ServiceSettings

SCHEMA_VERSION = 1
FEEDBACK_MAX_AGE_S = 0.25
LIMIT_MARGIN = 1.0           # units beyond a calibrated limit before it counts as exceeded
PROGRESS_EPS = 0.2           # units of error reduction that count as progress


class MoveJointTo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    joint: JointName
    target: float                                   # feedback units (see joint config)
    tolerance: float = Field(1.0, ge=0.2, le=10.0)  # feedback units
    max_duty: float = Field(0.3, gt=0.0, le=1.0)    # fraction of full PWM, capped per joint
    timeout_ms: int = Field(4000, ge=100)


class Jog(BaseModel):
    model_config = ConfigDict(extra="forbid")
    joint: JointName
    direction: Literal[-1, 1]
    duty: float = Field(0.2, gt=0.0, le=1.0)
    duration_ms: int = Field(200, ge=20)


class ActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1]
    action_id: str = Field(pattern=r"^[A-Za-z0-9_.:-]{6,80}$")
    kind: Literal["move_joint_to", "jog"]
    params: dict
    issued_at_ms: int
    ttl_ms: int = Field(ge=50)
    session: str = Field(min_length=1, max_length=80)

    def typed_params(self) -> Union[MoveJointTo, Jog]:
        return (MoveJointTo if self.kind == "move_joint_to" else Jog).model_validate(self.params)


class Rejected(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


class Controller:
    def __init__(self, settings: ServiceSettings, hw: HardwareConfig, backend):
        self.s, self.hw, self.backend = settings, hw, backend
        self._lock = threading.RLock()
        self._started = time.time()
        self.latched: Optional[str] = "startup: explicit rearm required"
        self.fault: Optional[dict] = None
        self.current: Optional[dict] = None
        self._actions: "collections.OrderedDict[str, dict]" = collections.OrderedDict()
        self._stop_evt = threading.Event()
        self._session: Optional[str] = None
        self._last_hb: Optional[float] = None      # monotonic
        self._offsets = collections.deque(maxlen=20)
        self.last_stop: Optional[dict] = None
        self._closed = threading.Event()
        self.backend.all_off()
        self._watchdog = threading.Thread(target=self._watch, name="watchdog", daemon=True)
        self._watchdog.start()

    # ---- clock / heartbeat -------------------------------------------
    def heartbeat(self, session: str, sent_at_ms: int):
        now_ms = time.time() * 1000
        with self._lock:
            if session != self._session:
                self._session = session
                self._offsets.clear()
            self._offsets.append(now_ms - sent_at_ms)
            self._last_hb = time.monotonic()
            return {"ok": True, "pi_time_ms": int(now_ms), "clock_offset_ms": self.clock_offset_ms(),
                    "latched": self.latched}

    def clock_offset_ms(self):
        return round(min(self._offsets), 1) if self._offsets else None

    def heartbeat_age_s(self):
        return None if self._last_hb is None else time.monotonic() - self._last_hb

    def _watch(self):
        while not self._closed.wait(0.05):
            age = self.heartbeat_age_s()
            with self._lock:
                armed = self.latched is None
            if armed and (age is None or age > self.s.watchdog_s):
                self._latch_fault("communication_lost",
                                  f"no heartbeat for {age:.2f} s" if age is not None else "no heartbeat received")

    # ---- actions -----------------------------------------------------
    def submit(self, raw: dict) -> dict:
        try:
            req = ActionRequest.model_validate(raw)
            params = req.typed_params()
        except ValidationError as exc:
            raise Rejected("invalid", f"{exc.error_count()} schema error(s): "
                                      + "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()[:4]))
        fingerprint = req.model_dump(exclude={"issued_at_ms", "ttl_ms"})
        with self._lock:
            existing = self._actions.get(req.action_id)
            if existing is not None:
                if existing["fingerprint"] != fingerprint:
                    raise Rejected("conflict", "action_id reused with different content")
                return {**self._public(existing), "duplicate": True}
            record = {"action_id": req.action_id, "kind": req.kind, "params": params.model_dump(),
                      "session": req.session, "fingerprint": fingerprint, "status": "rejected",
                      "received_at": time.time(), "started_at": None, "finished_at": None,
                      "result": None, "error": None}
            self._remember(record)
            try:
                self._check_admissible(req, params)
            except Rejected as rej:
                record.update(error=f"{rej.code}: {rej.message}", finished_at=time.time())
                raise
            record["status"] = "running"
            record["started_at"] = time.time()
            self.current = record
            self._stop_evt.clear()
        target = self._run_move if req.kind == "move_joint_to" else self._run_jog
        threading.Thread(target=target, args=(record, params), name=f"action-{req.action_id}", daemon=True).start()
        return self._public(record)

    def _remember(self, record):
        self._actions[record["action_id"]] = record
        while len(self._actions) > 500:
            self._actions.popitem(last=False)

    def _check_admissible(self, req: ActionRequest, p):
        if req.ttl_ms > self.s.max_ttl_ms:
            raise Rejected("invalid", f"ttl_ms exceeds {self.s.max_ttl_ms}")
        if self.latched:
            raise Rejected("latched", f"controller latched ({self.latched}); explicit rearm required")
        if self.current is not None:
            raise Rejected("busy", f"action {self.current['action_id']} is running; one action at a time")
        if req.session != self._session or self.clock_offset_ms() is None:
            raise Rejected("no_clock_sync", "no heartbeat from this session yet")
        age = time.time() * 1000 - (req.issued_at_ms + self.clock_offset_ms())
        if age > req.ttl_ms:
            raise Rejected("expired", f"command is {age:.0f} ms old (ttl {req.ttl_ms} ms)")
        if age < -self.s.max_clock_uncertainty_ms:
            raise Rejected("expired", f"command timestamp is {-age:.0f} ms in the future (clock jump?)")
        j = self.hw.joint(p.joint)
        if isinstance(p, Jog):
            if not self.s.commissioning:
                raise Rejected("forbidden", "jog is only available in supervised commissioning mode")
            if p.duration_ms > self.s.max_jog_ms:
                raise Rejected("limit", f"jog duration exceeds {self.s.max_jog_ms} ms")
            if p.duty > j.max_duty:
                raise Rejected("limit", f"duty exceeds the {j.name} cap {j.max_duty}")
            return
        if p.timeout_ms > self.s.max_move_ms:
            raise Rejected("limit", f"timeout_ms exceeds {self.s.max_move_ms}")
        if p.max_duty > j.max_duty:
            raise Rejected("limit", f"max_duty exceeds the {j.name} cap {j.max_duty}")
        if j.feedback is None:
            raise Rejected("no_feedback", f"{j.name} has no position feedback configured")
        if j.limits is None:
            raise Rejected("uncalibrated", f"{j.name} travel limits are not calibrated")
        lo, hi = j.limits
        if not lo <= p.target <= hi:
            raise Rejected("limit", f"target {p.target} outside {j.name} limits [{lo}, {hi}]")
        fb = self.backend.read_feedback(j.name)
        if fb is None or time.monotonic() - fb[1] > FEEDBACK_MAX_AGE_S:
            raise Rejected("no_feedback", f"{j.name} feedback unavailable or stale")

    def _run_move(self, record, p: MoveJointTo):
        j = self.hw.joint(p.joint)
        lo, hi = j.limits
        started = time.monotonic()
        best_err, best_t = None, started
        period = 1.0 / self.s.control_hz
        outcome, detail = "failed", None
        try:
            while True:
                if self._stop_evt.is_set():
                    outcome, detail = "stopped", "stop requested"
                    break
                fb = self.backend.read_feedback(j.name)
                now = time.monotonic()
                if fb is None or now - fb[1] > FEEDBACK_MAX_AGE_S:
                    outcome, detail = "failed", "feedback lost"
                    break
                pos = fb[0]
                err = p.target - pos
                if abs(err) <= p.tolerance:
                    outcome, detail = "succeeded", None
                    break
                direction = 1 if err > 0 else -1
                if (pos > hi + LIMIT_MARGIN and direction > 0) or (pos < lo - LIMIT_MARGIN and direction < 0):
                    outcome, detail = "failed", f"travel limit exceeded at {pos:.2f}"
                    break
                if (now - started) * 1000 > p.timeout_ms:
                    outcome, detail = "failed", f"timeout after {p.timeout_ms} ms (error {err:.2f})"
                    break
                if best_err is None or abs(err) < best_err - PROGRESS_EPS:
                    best_err, best_t = abs(err), now
                elif (now - best_t) * 1000 > j.stall_timeout_ms:
                    outcome, detail = "failed", f"no progress for {j.stall_timeout_ms} ms (stall)"
                    break
                duty = min(p.max_duty, max(0.4 * p.max_duty, abs(err) / 20.0 * p.max_duty))
                self.backend.set_output(j.name, direction, duty)
                time.sleep(period)
        finally:
            self.backend.set_output(j.name, 0, 0.0)
        fb = self.backend.read_feedback(j.name)
        self._finish(record, outcome, {"joint": j.name, "target": p.target,
                                       "final_position": fb[0] if fb else None,
                                       "elapsed_ms": round((time.monotonic() - started) * 1000)}, detail)
        if outcome == "failed":
            self._latch_fault("action_failed", f"{record['action_id']}: {detail}")

    def _run_jog(self, record, p: Jog):
        started = time.monotonic()
        try:
            self.backend.set_output(p.joint, p.direction, p.duty)
            self._stop_evt.wait(p.duration_ms / 1000)
        finally:
            self.backend.set_output(p.joint, 0, 0.0)
        stopped = self._stop_evt.is_set()
        self._finish(record, "stopped" if stopped else "completed_unverified",
                     {"joint": p.joint, "elapsed_ms": round((time.monotonic() - started) * 1000),
                      "note": "timed pulse only; no position proof"}, "stop requested" if stopped else None)

    def _finish(self, record, status, result, error):
        with self._lock:
            record.update(status=status, result=result, error=error, finished_at=time.time())
            if self.current is record:
                self.current = None

    def get_action(self, action_id):
        with self._lock:
            r = self._actions.get(action_id)
            return self._public(r) if r else None

    @staticmethod
    def _public(r):
        return {k: v for k, v in r.items() if k != "fingerprint"}

    # ---- stop / fault / rearm -----------------------------------------
    def stop(self, reason="stop requested", source="api"):
        """Outputs off immediately, then latch. Safe to call repeatedly from any thread."""
        self._stop_evt.set()
        self.backend.all_off()
        with self._lock:
            if not (self.latched or "").startswith("fault"):
                self.latched = f"stopped: {reason}"
            self.last_stop = {"id": uuid.uuid4().hex[:10], "at": time.time(), "source": source, "reason": reason}
            return {"acknowledged": True, "stop_id": self.last_stop["id"], "outputs_off": not self.backend.energised,
                    "latched": self.latched, "pi_time": self.last_stop["at"]}

    def _latch_fault(self, code, message):
        self._stop_evt.set()
        self.backend.all_off()
        with self._lock:
            self.fault = {"code": code, "message": message, "at": time.time()}
            self.latched = f"fault: {code}"

    def rearm(self, session: str):
        with self._lock:
            if self.current is not None:
                raise Rejected("busy", "an action is still finishing")
            if session != self._session or (self.heartbeat_age_s() or 1e9) > self.s.watchdog_s:
                raise Rejected("no_heartbeat", "rearm requires a live heartbeat from this session")
            self.backend.all_off()
            self.latched, self.fault = None, None
            self._stop_evt.clear()
            return {"ok": True, "latched": None}

    # ---- telemetry ---------------------------------------------------
    def state(self):
        joints = {}
        for j in self.hw.joints:
            fb = self.backend.read_feedback(j.name)
            joints[j.name] = {
                "position": fb[0] if fb else None,
                "feedback": j.feedback.kind if j.feedback else None,
                "units": j.feedback.units if j.feedback else None,
                "feedback_age_s": round(time.monotonic() - fb[1], 3) if fb else None,
                "limits": list(j.limits) if j.limits else None,
                "max_duty": j.max_duty,
            }
        with self._lock:
            hb = self.heartbeat_age_s()
            return {
                "schema_version": SCHEMA_VERSION,
                "backend": self.backend.kind,
                "pins_verified": self.hw.pins_verified,
                "commissioning": self.s.commissioning,
                "state": "fault" if self.fault else "latched" if self.latched else "moving" if self.current else "armed_idle",
                "latched": self.latched,
                "fault": self.fault,
                "current_action": self._public(self.current) if self.current else None,
                "last_stop": self.last_stop,
                "joints": joints,
                "outputs": self.backend.outputs(),
                "energised": self.backend.energised,
                "heartbeat": {"session": self._session, "age_s": round(hb, 3) if hb is not None else None,
                              "clock_offset_ms": self.clock_offset_ms(), "watchdog_s": self.s.watchdog_s},
                "limits": {"max_ttl_ms": self.s.max_ttl_ms, "max_move_ms": self.s.max_move_ms,
                           "max_jog_ms": self.s.max_jog_ms},
                "pwm_notes": self.hw.pwm_notes(),
                "pi_time": time.time(),
                "uptime_s": round(time.time() - self._started, 1),
            }

    def close(self):
        self._closed.set()
        self._stop_evt.set()
        self.backend.close()
