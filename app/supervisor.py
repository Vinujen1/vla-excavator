"""Bounded autonomous task supervisor (stationary scoop-and-dump).

States and the only allowed transitions are in TRANSITIONS. Separation of concerns:
  provider (Cosmos 3 / rules)  -> proposes intent, target id, next skill, explanation
  validation.py                -> decides from measured state; blocks with specific reasons
  Pi service                   -> executes one bounded joint move at a time, with its own watchdog
  physical E-stop              -> independent of all software

Concurrency rules:
* The lock is never held across HTTP calls or inference.
* Every provider request carries (task_id, generation, step, observation_id). A reply is used only
  if all still match and it is younger than decision_max_age_s; otherwise it is discarded.
  stop/cancel/pause bump the generation, so late replies after cancellation can't act.
* stop() sends the Pi stop from the caller's thread immediately; it doesn't wait for the worker.
"""
import threading
import time
import uuid
from typing import Optional

from . import observations
from .config import settings
from .decisions import DecisionProvider, ProviderError
from .events import events
from .hardware import PiError
from .schemas import Instruction, SKILLS, TaskSpec
from .validation import (DIG_SKILLS, build_context, execution_blockers, skill_moves, target_issues, target_ref,
                         validate_decision, validate_goal)

STATES = ("DISARMED", "READY", "PLANNING", "AWAITING_APPROVAL", "EXECUTING", "VERIFYING", "PAUSED",
          "COMPLETED", "FAULT", "STOPPED")
TRANSITIONS = {
    "DISARMED": {"PLANNING", "STOPPED"},
    "PLANNING": {"AWAITING_APPROVAL", "DISARMED", "STOPPED"},
    "AWAITING_APPROVAL": {"READY", "PLANNING", "DISARMED", "STOPPED"},
    "READY": {"EXECUTING", "DISARMED", "STOPPED", "FAULT"},
    "EXECUTING": {"VERIFYING", "PAUSED", "DISARMED", "FAULT", "STOPPED"},
    "VERIFYING": {"EXECUTING", "PAUSED", "COMPLETED", "DISARMED", "FAULT", "STOPPED"},
    "PAUSED": {"VERIFYING", "DISARMED", "FAULT", "STOPPED"},
    "COMPLETED": {"DISARMED", "STOPPED"},
    "FAULT": {"DISARMED"},
    "STOPPED": {"DISARMED"},
}
CYCLE = ["rotate_to_dig", "lower_to_dig", "scoop", "lift", "rotate_to_dump", "dump"]
MAX_RETRIES_PER_SKILL = 1


class TransitionError(RuntimeError):
    pass


def build_plan(cycles: int) -> list[str]:
    return CYCLE * cycles + ["return_home"]


class Supervisor:
    def __init__(self, camera, detector, selector, hardware, calibrations, providers: dict[str, DecisionProvider],
                 poll_s=0.05):
        self.camera, self.detector, self.selector, self.hardware = camera, detector, selector, hardware
        self.calibrations, self.providers = calibrations, providers
        self._poll = poll_s
        self._lock = threading.RLock()
        self.state = "DISARMED"
        self.reason: Optional[str] = None
        self.generation = 0
        self.task: Optional[TaskSpec] = None
        self.pending: Optional[dict] = None         # interpreted goal awaiting approval
        self.progress = self._fresh_progress()
        self.decisions: list[dict] = []             # bounded history shown on the dashboard
        self.last_plan_error: Optional[str] = None
        self._pause_requested = False
        self._cancel_requested = False
        self._worker: Optional[threading.Thread] = None

    @staticmethod
    def _fresh_progress():
        return {"step": 0, "cycle": 0, "current_skill": None, "current_move": None, "completed_skills": 0,
                "started_at": None, "active_s": 0.0, "retries": {}, "last_result": None}

    # ---- state machine -------------------------------------------------
    def _to(self, new, reason=None):
        with self._lock:
            if new not in TRANSITIONS[self.state]:
                raise TransitionError(f"{self.state} -> {new} is not allowed")
            old, self.state, self.reason = self.state, new, reason
        events.add("supervisor", f"{old} → {new}" + (f": {reason}" if reason else ""),
                   "error" if new == "FAULT" else "warn" if new in ("STOPPED", "PAUSED") else "info")

    def _bump(self):
        with self._lock:
            self.generation += 1
            return self.generation

    def _record(self, entry):
        with self._lock:
            self.decisions.append({"at": time.time(), **entry})
            del self.decisions[:-60]

    def context(self):
        return build_context(self.hardware, self.camera, self.selector, self.calibrations)

    def blockers(self, provider_id=None):
        provider = self.providers.get(provider_id or (self.task.provider if self.task else "rules"))
        ctx = self.context()
        b = execution_blockers(ctx, provider.kind if provider else None)
        if ctx.mode is not None:
            b += target_issues(ctx)[0]
        return b

    # ---- planning ------------------------------------------------------
    def plan(self, instruction: Instruction, provider_id: str, operator_cycles: Optional[int] = None,
             start_requested=False):
        provider = self.providers.get(provider_id)
        if provider is None:
            raise ValueError(f"Unknown provider {provider_id}")
        if not provider.status()["available"]:
            raise ValueError(provider.status()["message"])
        with self._lock:
            if self.state not in ("DISARMED", "AWAITING_APPROVAL"):
                raise TransitionError(f"Can't plan while {self.state}")
            self._to("PLANNING", f"{provider.name}: \"{instruction.text}\"")
            gen = self._bump()
            self.pending, self.last_plan_error = None, None
        obs, frame = observations.build(instruction, self.camera, self.detector, self.selector, self.hardware,
                                        require_camera=False)
        threading.Thread(target=self._plan_worker, name="planner",
                         args=(gen, provider, obs, frame, operator_cycles, start_requested), daemon=True).start()
        return obs.id

    def _plan_worker(self, *args):
        try:
            self._plan(*args)
        except Exception as exc:  # never leave the supervisor stuck in PLANNING
            with self._lock:
                stuck = self.state == "PLANNING"
                self.last_plan_error = f"planner error: {type(exc).__name__}: {exc}"
            if stuck:
                self._to("DISARMED", self.last_plan_error)

    def _plan(self, gen, provider, obs, frame, operator_cycles, start_requested):
        ctx = self.context()
        prompt_ctx = self._prompt_context(obs, ctx, expected=None)
        requested = time.time()
        try:
            proposal = provider.interpret(prompt_ctx, observations.jpeg(frame))
            error = None
        except ProviderError as exc:
            proposal, error = None, f"{exc.code}: {exc.message}"
        latency = round((time.time() - requested) * 1000)
        with self._lock:
            late = gen != self.generation or self.state != "PLANNING"
        self._record({"kind": "goal", "provider": provider.name, "provider_kind": provider.kind,
                      "observation_id": obs.id, "latency_ms": latency, "discarded": late,
                      "output": proposal.model_dump() if proposal else None, "error": error})
        if late:
            events.add("planner", f"Late reply from {provider.name} discarded (task was stopped or replanned)", "warn")
            return
        observations.save(obs, frame, None, extra={"goal_proposal": proposal.model_dump() if proposal else None,
                                                   "provider": provider.name, "error": error})
        if proposal is None:
            self.last_plan_error = error
            self._to("DISARMED", f"Planning failed: {error}")
            return
        check = validate_goal(proposal, obs, operator_cycles)
        tref = target_ref(ctx) if check.target_id else None
        blocking, target_warn = target_issues(ctx) if ctx.mode else ([], [])
        mode = ctx.mode or "simulation"
        spec = None
        if check.ok:
            spec = TaskSpec(task_id=f"task_{uuid.uuid4().hex[:8]}", goal="scoop_and_dump", instruction=obs.instruction,
                            observation_id=obs.id, mode=mode, provider=provider.id, cycles=check.cycles,
                            timeout_s=settings.default_task_timeout_s, target=tref, plan=build_plan(check.cycles),
                            explanation=proposal.explanation, warnings=check.warnings + target_warn,
                            start_requested=start_requested, created_at=time.time())
        with self._lock:
            if gen != self.generation or self.state != "PLANNING":
                return
            self.pending = {"spec": spec, "proposal": proposal.model_dump(), "errors": check.errors,
                            "warnings": check.warnings + target_warn, "provider": provider.name,
                            "provider_kind": provider.kind, "latency_ms": latency, "observation_id": obs.id,
                            "mode_at_plan": ctx.mode, "blockers": execution_blockers(ctx, provider.kind) + blocking}
        if check.ok:
            self._to("AWAITING_APPROVAL", f"{check.cycles} cycle(s) of scoop-and-dump proposed; operator approval needed")
        else:
            self.last_plan_error = "; ".join(check.errors)
            self._to("AWAITING_APPROVAL", "Proposal rejected by validation; see errors")

    def _prompt_context(self, obs, ctx, expected):
        t = obs.target
        return {
            "observation_id": obs.id, "observed_at": obs.created_at,
            "instruction": {"text": obs.instruction.text, "source": obs.instruction.source},
            "camera": {"status": ctx.camera_status, "frame_age_s": ctx.camera_frame_age_s,
                       "image_attached": obs.camera.get("frame_seq") is not None},
            "scene": [{"id": d.id, "label": d.label, "source": d.source, "confidence": d.confidence,
                       "bbox_px": d.bbox.model_dump(), "depth_status": d.depth.status, "measured_depth_m": d.depth.depth_m}
                      for d in obs.scene[:25]],
            "selected_target": {"id": t.detection.id, "label": t.detection.label, "stale": t.stale,
                                "measured_depth_m": t.detection.depth.depth_m} if t else None,
            "calibration": ctx.calibration.summary() if ctx.calibration else None,
            "execution_mode": ctx.mode,
            "robot_state": obs.robot_state.model_dump(),
            "supported_goals": ["scoop_and_dump"], "supported_skills": list(SKILLS),
            "constraints": {"max_cycles": settings.max_cycles, "stationary": True,
                            "no_track_travel": True, "one_joint_move_at_a_time": True},
            "task": None if self.task is None else {
                "task_id": self.task.task_id, "goal": self.task.goal, "cycles": self.task.cycles,
                "cycle": self.progress["cycle"], "completed_skills": self.progress["completed_skills"],
                "expected_next_skill": expected, "last_result": self.progress["last_result"],
                "recent_decisions": [{k: d.get(k) for k in ("kind", "output", "verdict")} for d in self.decisions[-4:]]},
        }

    # ---- operator actions ----------------------------------------------
    def approve(self):
        """Operator confirms the interpreted goal and target and arms the task (-> READY)."""
        with self._lock:
            if self.state != "AWAITING_APPROVAL" or not self.pending:
                raise TransitionError("Nothing awaiting approval")
            spec: TaskSpec = self.pending["spec"]
        if spec is None:
            raise TransitionError("The proposal failed validation; plan again")
        if time.time() - spec.created_at > settings.approval_max_age_s:
            raise TransitionError("Proposal is too old; plan again with a fresh observation")
        ctx = self.context()
        if ctx.mode != spec.mode:
            raise TransitionError(f"Execution mode changed ({spec.mode} → {ctx.mode}); plan again")
        if spec.target is not None:
            t = ctx.target
            if t is None or t.detection.label != spec.target.label or t.stale:
                raise TransitionError("The selected target changed or went stale since planning; plan again")
        blockers = self.blockers(spec.provider)
        if blockers:
            raise TransitionError("Can't arm: " + "; ".join(blockers))
        with self._lock:
            spec.approved_at = time.time()
            self.task, self.pending = spec, None
            self.progress = self._fresh_progress()
            self._to("READY", f"Armed: {spec.cycles} cycle(s), timeout {spec.timeout_s:.0f} s ({spec.mode})")
        if spec.start_requested:
            self.start()
        return spec

    def reject(self):
        with self._lock:
            if self.state != "AWAITING_APPROVAL":
                raise TransitionError("Nothing awaiting approval")
            self.pending = None
            self._to("DISARMED", "Proposal rejected by operator")

    def start(self):
        with self._lock:
            if self.state != "READY":
                raise TransitionError(f"Can't start while {self.state}")
        blockers = self.blockers()
        if blockers:
            raise TransitionError("Can't start: " + "; ".join(blockers))
        with self._lock:
            gen = self._bump()
            self._pause_requested = self._cancel_requested = False
            self.progress["started_at"] = time.time()
            self._to("EXECUTING", f"Task {self.task.task_id} started")
            self._worker = threading.Thread(target=self._run, args=(gen,), name="supervisor", daemon=True)
            self._worker.start()

    def pause(self, source="operator"):
        with self._lock:
            if self.state in ("EXECUTING", "VERIFYING"):
                self._pause_requested = True
                self._bump()   # a pending decision can no longer advance the task
                events.add("supervisor", f"Pause requested ({source}); takes effect after the current bounded move", "warn")
                return "pausing"
            raise TransitionError(f"Nothing to pause in {self.state}")

    def resume(self):
        with self._lock:
            if self.state != "PAUSED":
                raise TransitionError(f"Can't resume from {self.state}")
        blockers = self.blockers()
        if blockers:
            raise TransitionError("Can't resume: " + "; ".join(blockers))
        with self._lock:
            gen = self._bump()
            self._pause_requested = self._cancel_requested = False
            self._to("VERIFYING", "Resumed by operator; re-checking state before the next skill")
            self._worker = threading.Thread(target=self._run, args=(gen,), name="supervisor", daemon=True)
            self._worker.start()

    def cancel(self, source="operator"):
        with self._lock:
            if self.state in ("AWAITING_APPROVAL", "READY", "PAUSED", "PLANNING"):
                self._bump()
                self.pending = None
                self._to("DISARMED", f"Cancelled ({source})")
                return "cancelled"
            if self.state in ("EXECUTING", "VERIFYING"):
                self._cancel_requested = True
                self._bump()
                events.add("supervisor", f"Cancel requested ({source}); stops after the current bounded move", "warn")
                return "cancelling"
            raise TransitionError(f"Nothing to cancel in {self.state}")

    def stop(self, source="operator", reason="software stop"):
        """Software stop: invalidate everything in flight and send a real stop to the Pi now."""
        with self._lock:
            self._bump()
            self._pause_requested = self._cancel_requested = False
            prev = self.state
            if prev not in ("FAULT", "STOPPED"):
                self.state, self.reason = "STOPPED", f"{reason} ({source})"
        if prev not in ("FAULT", "STOPPED"):
            events.add("supervisor", f"{prev} → STOPPED: {reason} ({source})", "warn")
        return self.hardware.request_stop(f"{reason} ({source})", source)

    def reset(self):
        with self._lock:
            if self.state not in ("FAULT", "STOPPED", "COMPLETED"):
                raise TransitionError(f"Reset is for FAULT/STOPPED/COMPLETED, not {self.state}")
            self.task, self.pending = None, None
            self._to("DISARMED", "Reset by operator (the Pi stays latched until rearmed)")

    def _fault(self, gen, message):
        with self._lock:
            if gen != self.generation or self.state in ("FAULT", "STOPPED"):
                return
            self._bump()
            self.state, self.reason = "FAULT", message
        events.add("supervisor", f"FAULT: {message}", "error")
        self.hardware.request_stop(f"supervisor fault: {message}", "supervisor")

    # ---- execution -----------------------------------------------------
    def _alive(self, gen):
        with self._lock:
            return gen == self.generation

    def _active_s(self):
        p = self.progress
        return p["active_s"] + (time.time() - p["_segment"] if p.get("_segment") else 0.0)

    def _run(self, gen):
        p = self.progress
        p["_segment"] = time.time()
        try:
            self._loop(gen)
        except Exception as exc:  # never leave the machine in an unknown state
            self._fault(gen, f"supervisor error: {type(exc).__name__}: {exc}")
        finally:
            p["active_s"] += time.time() - p.pop("_segment", time.time())

    def _loop(self, gen):
        task, p = self.task, self.progress
        retry_pending = False
        while True:
            # pause()/cancel() bump the generation (so in-flight decisions are void) and set a flag;
            # the flags are checked first so the worker itself performs the transition.
            if self._cancel_requested:
                self._finish_interrupt("DISARMED", "Cancelled; stopped after the last completed move")
                return
            if self._pause_requested:
                self._finish_interrupt("PAUSED", "Paused by operator")
                return
            if not self._alive(gen):
                return
            if self._active_s() > task.timeout_s:
                self._fault(gen, f"task timeout ({task.timeout_s:.0f} s)")
                return
            if self.state == "EXECUTING":
                self._to("VERIFYING", "Checking state before the next skill")
            idx = p["completed_skills"]
            expected = task.plan[idx] if idx < len(task.plan) else None

            # Re-observe: fresh Pi state, calibration, camera and target before deciding.
            ctx = self.context()
            blockers = execution_blockers(ctx, self.providers[task.provider].kind)
            if blockers:
                critical = [b for b in blockers if b.startswith(("Pi fault", "Pi controller latched", "Raspberry Pi"))]
                if critical:
                    self._fault(gen, critical[0])
                else:
                    self._pause(gen, blockers[0])
                return
            if expected in DIG_SKILLS and task.target is not None:
                t = ctx.target
                if t is None or t.stale or t.detection.label != task.target.label:
                    self._pause(gen, f"Target {task.target.label} lost or ambiguous ({t.stale_reason if t else 'not selected'})")
                    return

            verdict, _ = self._decide(gen, ctx, expected, retry_available=retry_pending)
            if verdict is None:
                continue  # superseded (stop/pause/cancel) or paused: the loop top handles it
            if verdict == "complete":
                self._to("COMPLETED", f"{task.cycles} cycle(s) completed and returned home")
                return
            # "accept" and "retry" both run `expected`: after a soft miss the index isn't advanced,
            # so the expected skill *is* the retry.
            skill, retry_pending = expected, False
            ok, soft = self._execute_skill(gen, ctx, skill)
            if ok:
                p["completed_skills"] += 1
                if skill == "dump":
                    p["cycle"] += 1
                continue
            if self._pause_requested or self._cancel_requested or not self._alive(gen):
                continue
            key = f"{idx}:{skill}"
            if soft and p["retries"].get(key, 0) < MAX_RETRIES_PER_SKILL:
                p["retries"][key] = p["retries"].get(key, 0) + 1
                retry_pending = True
                events.add("supervisor", f"Bounded retry of {skill}: {p['last_result']['error']}", "warn")
                continue
            self._fault(gen, f"{skill} missed its target after the allowed retry: {p['last_result']['error']}" if soft
                        else f"{skill} failed: {p['last_result'].get('error') or p['last_result'].get('status')}")
            return

    def _finish_interrupt(self, state, reason):
        with self._lock:
            self._pause_requested = self._cancel_requested = False
        if self.state in ("EXECUTING", "VERIFYING"):
            self._to(state, reason)

    def _pause(self, gen, reason):
        if self._alive(gen) and self.state in ("EXECUTING", "VERIFYING"):
            self._bump()
            self._to("PAUSED", reason)

    def _decide(self, gen, ctx, expected, retry_available):
        task = self.task
        provider = self.providers[task.provider]
        instruction = task.instruction
        obs, frame = observations.build(instruction, self.camera, self.detector, self.selector, self.hardware,
                                        require_camera=False)
        step = self.progress["completed_skills"]
        requested = time.time()
        try:
            d = provider.next_skill(self._prompt_context(obs, ctx, expected), observations.jpeg(frame))
            error = None
        except ProviderError as exc:
            d, error = None, f"{exc.code}: {exc.message}"
        age = time.time() - requested
        stale = not self._alive(gen) or self.progress["completed_skills"] != step or age > settings.decision_max_age_s
        entry = {"kind": "skill", "provider": provider.name, "provider_kind": provider.kind, "task_id": task.task_id,
                 "observation_id": obs.id, "step": step, "expected": expected, "latency_ms": round(age * 1000),
                 "output": d.model_dump() if d else None, "error": error, "discarded": stale}
        if stale:
            entry["verdict"] = "discarded (late or superseded)"
            self._record(entry)
            if self._alive(gen) and age > settings.decision_max_age_s:
                self._pause(gen, f"Decision took {age:.1f} s (> {settings.decision_max_age_s:.0f} s); not used")
            return None, None
        if d is None:
            entry["verdict"] = "pause"
            self._record(entry)
            self._pause(gen, f"No usable decision from {provider.name}: {error}")
            return None, None
        verdict, detail = validate_decision(d, expected, obs, retry_available)
        entry["verdict"] = verdict if verdict != "pause" else f"pause: {detail}"
        self._record(entry)
        if verdict == "pause":
            self._pause(gen, detail)
            return None, None
        return verdict, detail

    def _execute_skill(self, gen, ctx, skill):
        """Run a skill as single-joint moves, verifying each from fresh Pi feedback.
        Returns (ok, soft_failure). Soft = the move finished but missed the tolerance."""
        p = self.progress
        moves = skill_moves(skill, ctx.calibration, ctx.pi_state)
        p["current_skill"] = skill
        for i, m in enumerate(moves):
            if not self._alive(gen):
                return False, False
            self._to("EXECUTING", f"{skill}: {m['joint']} → {m['pose']} ({m['target']})")
            p["current_move"] = {**m, "index": i + 1, "of": len(moves)}
            params = {"joint": m["joint"], "target": m["target"], "tolerance": m["tolerance"],
                      "max_duty": settings.move_max_duty, "timeout_ms": settings.move_timeout_ms}
            try:
                rec = self.hardware.send_action("move_joint_to", params)
            except PiError as exc:
                p["last_result"] = {"skill": skill, "move": m, "status": "rejected", "error": f"{exc.code}: {exc.message}"}
                return False, False
            rec = self._wait_action(gen, rec["action_id"], settings.move_timeout_ms / 1000 + 2.0)
            if rec is None:
                return False, False
            self._to("VERIFYING", f"Checking {m['joint']} position")
            pos = self._fresh_position(m["joint"])
            p["last_result"] = {"skill": skill, "move": m, "status": rec["status"], "error": rec.get("error"),
                                "final_position": pos, "action_id": rec["action_id"]}
            if rec["status"] != "succeeded":
                return False, False
            if pos is None:
                p["last_result"]["error"] = "no fresh feedback after the move"
                return False, False
            if abs(pos - m["target"]) > m["tolerance"]:
                p["last_result"]["error"] = f"position {pos} misses target {m['target']} ± {m['tolerance']}"
                return False, True
        p["current_move"] = None
        return True, False

    def _wait_action(self, gen, action_id, timeout_s):
        end = time.time() + timeout_s
        while time.time() < end:
            if not self._alive(gen) and self.state in ("STOPPED", "FAULT"):
                return None
            try:
                rec = self.hardware.get_action(action_id)
            except Exception:
                rec = None
            if rec and rec["status"] != "running":
                return rec
            time.sleep(self._poll)
        self._fault(gen, f"action {action_id} did not finish in {timeout_s:.1f} s")
        return None

    def _fresh_position(self, joint, timeout_s=1.0):
        """Wait for a Pi state sample taken after the move finished, then read the joint."""
        after = time.monotonic()
        end = time.time() + timeout_s
        while time.time() < end:
            s = self.hardware.state_requested_after(after)
            if s:
                return s["joints"][joint]["position"]
            time.sleep(self._poll)
        return None

    # ---- telemetry -----------------------------------------------------
    def snapshot(self):
        with self._lock:
            task, pending, p = self.task, self.pending, dict(self.progress)
            decisions = list(self.decisions[-12:])
            state, reason = self.state, self.reason
        ctx = self.context()
        provider_kind = self.providers[task.provider].kind if task else None
        return {
            "state": state, "reason": reason, "generation": self.generation,
            "execution_mode": ctx.mode,
            "task": task.model_dump() if task else None,
            "pending": {**pending, "spec": pending["spec"].model_dump() if pending["spec"] else None} if pending else None,
            "progress": {k: v for k, v in p.items() if not k.startswith("_")} | {
                "active_s": round(self._active_s(), 1) if task else 0.0,
                "total_skills": len(task.plan) if task else 0,
                "pausing": self._pause_requested, "cancelling": self._cancel_requested},
            "decisions": decisions,
            "blockers": execution_blockers(ctx, provider_kind) + (target_issues(ctx)[0] if ctx.mode else []),
            "last_plan_error": self.last_plan_error,
        }
