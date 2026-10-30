"""Supervisor, command pipeline, validation and provider tests.

The Pi side is the real pi_service controller on its simulation backend, reached through the
real HTTP API (Starlette TestClient). No GPIO, no network, no camera and no model download.
"""
import os
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from app import audio, voice as voice_mod
from app.calibration import Calibration
from app.commands import CommandService, match_detections, parse_command
from app.decisions import Cosmos3Provider, ProviderError, RulesProvider
from app.events import events
from app.hardware import HardwareMonitor
from app.schemas import (BBox, DepthEstimate, Detection, DetectionFrame, GoalProposal, Instruction, Observation,
                         RobotState, SkillDecision)
from app.supervisor import STATES, TRANSITIONS, Supervisor, TransitionError, build_plan
from app.targets import TargetSelector
from app.validation import Calibrations, ExecContext, execution_blockers, validate_decision, validate_goal
from excavator_pi.app import create_app
from excavator_pi.backends import SimulationBackend
from excavator_pi.config import ServiceSettings, load_hardware_config
from excavator_pi.controller import Controller

ROOT = Path(__file__).resolve().parent.parent
PI_CONFIG = ROOT / "pi_service" / "config.example.json"
SIM_CAL = ROOT / "config" / "calibration.simulation.json"
TOKEN = "t" * 24


def wait_for(pred, timeout=20.0, step=0.01):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(step)
    return False


# ---- fakes for perception ----------------------------------------------------
class FakeCamera:
    def __init__(self, connected=False):
        self.status = "connected" if connected else "disconnected"
        self.error, self.device_info, self.intrinsics, self.rs_intrinsics = None, {}, None, None

    def latest(self):
        return None

    def state(self):
        return {"status": self.status, "frame_age_s": 0.05 if self.status == "connected" else None}


class FakeDetector:
    class _S:
        class marker:
            enabled = False
    settings = _S()

    def __init__(self, frame=None):
        self.frame = frame

    def latest(self):
        return self.frame


def det(id_, label, source="yolo", status="ok"):
    return Detection(id=id_, source=source, label=label, confidence=0.9 if source == "yolo" else None,
                     bbox=BBox(x1=100, y1=100, x2=200, y2=200),
                     depth=DepthEstimate(status=status, depth_m=1.0, valid_fraction=1.0, xyz_camera_m=(0.0, 0.0, 1.0)))


class SlowProvider(RulesProvider):
    """Rules provider whose replies wait for a gate, to test late replies."""
    id, name = "slow", "Slow test provider"

    def __init__(self, block_goal=False, block_skill_after=None):
        self.gate = threading.Event()
        self.block_goal, self.block_skill_after, self.calls = block_goal, block_skill_after, 0

    def interpret(self, context, image):
        if self.block_goal:
            self.gate.wait(10)
        return super().interpret(context, image)

    def next_skill(self, context, image):
        self.calls += 1
        if self.block_skill_after is not None and self.calls > self.block_skill_after:
            self.gate.wait(10)
        return super().next_skill(context, image)


# ---- harness ---------------------------------------------------------------
class Rig:
    def __init__(self, provider=None, camera_connected=False, frame=None, watchdog_s=1.0, rearm=True):
        s = ServiceSettings(api_token=TOKEN, sim_speed_units_per_s=150.0, control_hz=100.0, watchdog_s=watchdog_s)
        hw = load_hardware_config(PI_CONFIG)
        self.pi = Controller(s, hw, SimulationBackend(hw, s.sim_speed_units_per_s))
        self.client = TestClient(create_app(s, controller=self.pi), base_url="http://pi")
        self.hw = HardwareMonitor(endpoint="http://pi", token=TOKEN, client=self.client, hz=25)
        self.hw.start()
        assert wait_for(lambda: self.hw.fresh_state() is not None and self.pi.clock_offset_ms() is not None, 5)
        if rearm:
            self.hw.rearm()
            assert wait_for(lambda: self.hw.fresh_state()["latched"] is None, 2)
        self.camera = FakeCamera(camera_connected)
        self.detector = FakeDetector(frame)
        self.selector = TargetSelector(self.camera)
        self.provider = provider or RulesProvider()
        self.sup = Supervisor(self.camera, self.detector, self.selector, self.hw,
                              Calibrations(hardware_path=ROOT / "missing.json", simulation_path=SIM_CAL),
                              {"rules": RulesProvider(), self.provider.id: self.provider}, poll_s=0.01)
        self.commands = CommandService(self.sup, self.selector, self.detector, events)

    def plan(self, text="Plan a scoop-and-dump task for two cycles", cycles=2, start=False):
        self.sup.plan(Instruction(text=text, source="typed"), self.provider.id, operator_cycles=cycles,
                      start_requested=start)
        assert wait_for(lambda: self.sup.state == "AWAITING_APPROVAL", 5), self.sup.state

    def close(self):
        self.hw.stop()
        self.pi.close()


@pytest.fixture
def rig():
    r = Rig()
    yield r
    r.close()


# ---- state machine -----------------------------------------------------------
def test_transition_table_is_complete_and_enforced(rig):
    assert set(TRANSITIONS) == set(STATES)
    assert all(t <= set(STATES) for t in TRANSITIONS.values())
    assert TRANSITIONS["FAULT"] == {"DISARMED"} and TRANSITIONS["STOPPED"] == {"DISARMED"}
    with pytest.raises(TransitionError):
        rig.sup._to("EXECUTING")         # DISARMED -> EXECUTING skips approval
    with pytest.raises(TransitionError):
        rig.sup.start()
    with pytest.raises(TransitionError):
        rig.sup.approve()


def test_plan_is_cycle_bounded():
    assert build_plan(2) == ["rotate_to_dig", "lower_to_dig", "scoop", "lift", "rotate_to_dump", "dump"] * 2 + ["return_home"]


# ---- full bounded task in simulation -------------------------------------------
def test_complete_bounded_scoop_and_dump_in_simulation(rig):
    rig.plan()
    spec = rig.sup.pending["spec"]
    assert spec.mode == "simulation" and spec.cycles == 2 and len(spec.plan) == 13
    rig.sup.approve()
    assert rig.sup.state == "READY"
    rig.sup.start()
    assert wait_for(lambda: rig.sup.state in ("COMPLETED", "FAULT", "PAUSED"), 60), rig.sup.state
    assert rig.sup.state == "COMPLETED", rig.sup.reason
    p = rig.sup.progress
    assert p["cycle"] == 2 and p["completed_skills"] == 13
    joints = rig.hw.fresh_state()["joints"]
    assert abs(joints["cabin"]["position"] - 0) <= 2.0  # returned home
    accepted = [d for d in rig.sup.decisions if d["kind"] == "skill" and d["verdict"] == "accept"]
    assert len(accepted) == 13
    assert not rig.pi.backend.energised


def test_typed_command_to_validated_plan_and_auto_start(rig):
    r = rig.commands.handle("Start autonomous scoop-and-dump for two cycles.", "typed", provider_id=rig.provider.id)
    assert r["handled"] and r["kind"] == "plan_task"
    assert wait_for(lambda: rig.sup.state == "AWAITING_APPROVAL", 5)
    spec = rig.sup.pending["spec"]
    assert spec.cycles == 2 and spec.start_requested
    assert any("No target selected" in w for w in spec.warnings)
    rig.sup.approve()  # approval of goal + target arms and, as requested, starts
    assert rig.sup.state in ("EXECUTING", "VERIFYING")
    assert wait_for(lambda: rig.sup.state == "COMPLETED", 60), rig.sup.reason


def test_unsupported_and_identify_commands(rig):
    assert rig.commands.handle("dance please")["kind"] == "unsupported"
    r = rig.commands.handle("Identify the red marker.")
    assert not r["handled"] and "No detection frame" in r["message"]


def test_cycles_over_limit_rejected(rig):
    rig.sup.plan(Instruction(text="scoop twenty times", source="typed"), "rules", operator_cycles=20)
    assert wait_for(lambda: rig.sup.state == "AWAITING_APPROVAL", 5)
    assert rig.sup.pending["spec"] is None and "exceeds the limit" in rig.sup.pending["errors"][0]
    with pytest.raises(TransitionError):
        rig.sup.approve()


# ---- stops, late replies, pause --------------------------------------------------
def test_stop_during_planning_discards_late_reply():
    slow = SlowProvider(block_goal=True)
    r = Rig(provider=slow)
    try:
        r.sup.plan(Instruction(text="plan a scoop", source="typed"), "slow")
        assert r.sup.state == "PLANNING"
        ack = r.sup.stop(source="test")
        assert r.sup.state == "STOPPED" and ack["acknowledged"]
        slow.gate.set()
        assert wait_for(lambda: any(d.get("discarded") for d in r.sup.decisions), 5)
        assert r.sup.state == "STOPPED" and r.sup.pending is None
    finally:
        r.close()


def test_cancel_during_planning_discards_late_reply():
    slow = SlowProvider(block_goal=True)
    r = Rig(provider=slow)
    try:
        r.sup.plan(Instruction(text="plan a scoop", source="typed"), "slow")
        r.sup.cancel("test")
        assert r.sup.state == "DISARMED"
        slow.gate.set()
        assert wait_for(lambda: any(d.get("discarded") for d in r.sup.decisions), 5)
        assert r.sup.state == "DISARMED" and r.sup.pending is None
    finally:
        r.close()


def test_stop_during_execution_stops_pi_and_latches(rig):
    rig.plan(cycles=1)
    rig.sup.approve()
    rig.sup.start()
    assert wait_for(lambda: rig.pi.current is not None, 10)
    ack = rig.sup.stop(source="test")
    assert ack["acknowledged"] and ack["pi_response"]["outputs_off"]
    assert rig.sup.state == "STOPPED"
    assert not rig.pi.backend.energised and rig.pi.latched.startswith("stopped")
    time.sleep(0.3)
    assert rig.sup.state == "STOPPED" and rig.pi.current is None
    # Explicit reset + rearm are both required before anything can run again.
    with pytest.raises(TransitionError):
        rig.sup.plan(Instruction(text="scoop", source="typed"), "rules")
    rig.sup.reset()
    assert any("latched" in b for b in rig.sup.blockers())


def test_stop_during_verification_discards_pending_decision():
    slow = SlowProvider(block_skill_after=1)
    r = Rig(provider=slow)
    try:
        r.plan(cycles=1)
        r.sup.approve()
        r.sup.start()
        assert wait_for(lambda: slow.calls == 2 and r.sup.state == "VERIFYING", 20)
        sent_before = len(r.pi._actions)
        r.sup.stop(source="test")
        slow.gate.set()
        assert wait_for(lambda: any(d.get("discarded") for d in r.sup.decisions), 5)
        time.sleep(0.2)
        assert r.sup.state == "STOPPED" and len(r.pi._actions) == sent_before
    finally:
        r.close()


def test_pause_and_resume_complete_the_task(rig):
    rig.plan(cycles=1)
    rig.sup.approve()
    rig.sup.start()
    assert wait_for(lambda: rig.sup.progress["completed_skills"] >= 2, 20)
    rig.sup.pause("test")
    assert wait_for(lambda: rig.sup.state == "PAUSED", 10)
    done = rig.sup.progress["completed_skills"]
    time.sleep(0.3)
    assert rig.sup.progress["completed_skills"] == done and rig.pi.current is None
    rig.sup.resume()
    assert wait_for(lambda: rig.sup.state == "COMPLETED", 60), rig.sup.reason


def test_voice_stop_runs_immediately_but_voice_plan_needs_confirmation(rig):
    r = rig.commands.handle("plan a scoop and dump", "jetson_mic", confirmed=False)
    assert r["needs_confirmation"] and rig.sup.state == "DISARMED"
    r = rig.commands.handle("Stop.", "jetson_mic", confirmed=False)
    assert r["handled"] and rig.sup.state == "STOPPED" and r["stop_ack"]["acknowledged"]


# ---- faults: Pi link, heartbeat, timeout ------------------------------------------
def test_lost_pi_connection_faults_task_and_pi_watchdog_latches():
    r = Rig(watchdog_s=0.4)
    try:
        r.plan(cycles=1)
        r.sup.approve()
        r.sup.start()
        assert wait_for(lambda: r.sup.progress["completed_skills"] >= 1, 20)

        class Broken:
            def __getattr__(self, name):
                def fail(*a, **k):
                    raise ConnectionError("wifi down")
                return fail
        r.hw._client = Broken()
        assert wait_for(lambda: r.sup.state == "FAULT", 10), r.sup.state
        assert wait_for(lambda: r.pi.fault is not None and r.pi.fault["code"] in ("communication_lost", "action_failed"), 5)
        assert not r.pi.backend.energised
        assert r.hw.last_stop and not r.hw.last_stop["acknowledged"]   # stop attempted, honestly not acknowledged
    finally:
        r.close()


def test_task_timeout_faults(rig):
    rig.plan(cycles=1)
    rig.sup.approve()
    rig.sup.task.timeout_s = 0.3
    rig.sup.start()
    assert wait_for(lambda: rig.sup.state == "FAULT", 20)
    assert "timeout" in rig.sup.reason
    assert wait_for(lambda: rig.pi.latched is not None, 3)   # the fault also sends a real stop


def test_arming_blocked_while_pi_latched():
    r = Rig(rearm=False)
    try:
        r.plan(cycles=1)
        with pytest.raises(TransitionError, match="latched"):
            r.sup.approve()
    finally:
        r.close()


def test_stale_target_pauses_before_dig_skill():
    frame = DetectionFrame(frame_number=1, frame_time=time.time(), detections=[det("m0", "red marker candidate", "color_marker")])
    r = Rig(camera_connected=True, frame=frame)
    try:
        r.selector.select(frame, "m0")
        r.plan(text="scoop beside the red marker", cycles=1)
        assert r.sup.pending["spec"].target.label == "red marker candidate"
        r.sup.approve()
        r.camera.status = "stalled"          # camera stalls -> target stale
        r.sup.start()
        assert wait_for(lambda: r.sup.state == "PAUSED", 10)
        assert "lost or ambiguous" in r.sup.reason
        assert r.pi._actions == {} or all(a["status"] != "running" for a in r.pi._actions.values())
    finally:
        r.close()


# ---- validation --------------------------------------------------------------
def _obs(target_id=None, scene=("y0", "m0")):
    from app.schemas import Target
    dets = [det(i, "cup" if i.startswith("y") else "red marker candidate", "yolo" if i.startswith("y") else "color_marker")
            for i in scene]
    t = None
    if target_id:
        d = next(x for x in dets if x.id == target_id)
        t = Target(detection=d, selected_at=0, first_frame_number=1, last_seen_frame_number=1, last_seen_time=0)
    return Observation(id="obs_test", created_at=0, instruction=Instruction(text="x", source="typed"), target=t,
                       scene=dets, scene_frame_number=1, scene_frame_time=0, camera={},
                       robot_state=RobotState(pi_connected=False, motion_controller="none"))


def test_goal_validation_flags_ambiguous_and_unknown_targets():
    g = GoalProposal(intent="scoop_and_dump", target_id="y0", cycles=2, explanation="x")
    assert "ambiguous" in validate_goal(g, _obs("m0"), 2).errors[0]
    assert "not in the observation" in validate_goal(g.model_copy(update={"target_id": "z9"}), _obs("m0"), 2).errors[0]
    check = validate_goal(g.model_copy(update={"target_id": "m0", "cycles": 3}), _obs("m0"), 2)
    assert check.ok and check.cycles == 2 and "operator stated" in check.warnings[0]
    assert not validate_goal(GoalProposal(intent="unsupported", explanation="x"), _obs(), None).ok


def test_decision_validation_allowlist():
    obs = _obs("m0")
    ok = SkillDecision(next_skill="scoop", explanation="x")
    assert validate_decision(ok, "scoop", obs, False) == ("accept", "scoop")
    assert validate_decision(ok, "lift", obs, False)[0] == "pause"
    assert validate_decision(SkillDecision(next_skill="retry_last", explanation="x"), "scoop", obs, False)[0] == "pause"
    assert validate_decision(SkillDecision(next_skill="retry_last", explanation="x"), "scoop", obs, True)[0] == "retry"
    assert validate_decision(SkillDecision(next_skill="complete", explanation="x"), "dump", obs, False)[0] == "pause"
    assert validate_decision(SkillDecision(next_skill="scoop", explanation="x", target_id="y0"), "scoop", obs, False)[0] == "pause"
    with pytest.raises(Exception):
        SkillDecision(next_skill="drive_forward", explanation="x")


def test_hardware_mode_blocked_without_calibration_feedback_and_verified_pins():
    pi = {"backend": "hardware", "pins_verified": False, "latched": None, "fault": None, "current_action": None,
          "joints": {j: {"feedback": None, "feedback_age_s": None, "limits": None} for j in ("boom", "cabin", "bucket", "stick")}}
    ctx = ExecContext(mode="hardware", pi_status="connected", pi_state=pi, calibration=None,
                      calibration_error="config/calibration.json not found", camera_status="disconnected",
                      camera_frame_age_s=None, target=None)
    b = " | ".join(execution_blockers(ctx, "rules"))
    for needle in ("No hardware calibration", "pins_verified", "Camera not live", "Cosmos 3 provider",
                   "No position feedback for boom", "travel limits are not calibrated"):
        assert needle in b, needle


def test_simulation_calibration_never_accepted_as_hardware():
    cals = Calibrations(hardware_path=SIM_CAL, simulation_path=SIM_CAL)
    cal, err = cals.get("hardware")
    assert cal is None and "expected 'hardware'" in err
    sim, _ = cals.get("simulation")
    assert isinstance(sim, Calibration) and sim.kind == "simulation" and sim.camera_to_base is None


# ---- Cosmos provider output handling (fake chat; no network) ----------------------
class FakeChat:
    def __init__(self, reply=None, exc=None):
        self.reply, self.exc, self.messages = reply, exc, None

    def invoke(self, messages):
        self.messages = messages
        if self.exc:
            raise self.exc
        return AIMessage(content=self.reply, response_metadata={"model_name": "nvidia/Cosmos3-Edge"})


@pytest.mark.parametrize("reply,code", [
    ("I think you should scoop.", "invalid_output"),
    ('<answer>{"next_skill": "drive_forward", "explanation": "go"}</answer>', "invalid_output"),
    ('<answer>{"next_skill": "scoop", "explanation": "x", "gpio": 17}</answer>', "invalid_output"),
])
def test_cosmos_rejects_invalid_or_unsupported_output(reply, code):
    p = Cosmos3Provider(base_url="http://cosmos.test/v1", chat=FakeChat(reply))
    with pytest.raises(ProviderError) as e:
        p.next_skill({"task": {}}, None)
    assert e.value.code == code
    assert p.status()["verified"] is False and p.status()["last_error"].startswith(code)


def test_cosmos_parses_reasoning_reply_and_sends_image():
    reply = '<think>bucket is above the dig area</think>\n<answer>{"next_skill": "scoop", "explanation": "Bucket is lowered"}</answer>'
    chat = FakeChat(reply)
    p = Cosmos3Provider(base_url="http://cosmos.test/v1", chat=chat)
    d = p.next_skill({"task": {}}, b"\xff\xd8fakejpeg")
    assert d.next_skill == "scoop" and p.status()["verified"] and p.reported_model == "nvidia/Cosmos3-Edge"
    parts = chat.messages[1].content
    assert parts[1]["type"] == "image_url" and parts[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_cosmos_request_failure_does_not_leak_details():
    p = Cosmos3Provider(base_url="http://cosmos.test/v1", api_key="secret-key",
                        chat=FakeChat(exc=RuntimeError("401 for key secret-key")))
    with pytest.raises(ProviderError) as e:
        p.interpret({"instruction": {"text": "x"}}, None)
    assert "secret-key" not in e.value.message and e.value.code == "request_failed"


def test_cosmos_not_configured_is_reported_honestly():
    p = Cosmos3Provider(base_url="")
    s = p.status()
    assert not s["available"] and not s["verified"] and "not connected" in s["message"]


@pytest.mark.skipif(not os.environ.get("VLA_COSMOS_BASE_URL"), reason="VLA_COSMOS_BASE_URL not set: no real Cosmos endpoint")
def test_real_cosmos_inference_request():
    report = Cosmos3Provider(base_url=os.environ["VLA_COSMOS_BASE_URL"],
                             model=os.environ.get("VLA_COSMOS_MODEL", "nvidia/Cosmos3-Edge"),
                             api_key=os.environ.get("VLA_COSMOS_API_KEY", "")).probe()
    assert report["inference_ok"], report


# ---- commands and voice -----------------------------------------------------------
def test_command_parsing():
    assert parse_command("Stop.").kind == "stop"
    assert parse_command("pause").kind == "pause"
    c = parse_command("Start autonomous scoop-and-dump for two cycles.")
    assert c.kind == "plan_task" and c.cycles == 2 and c.start_requested
    c = parse_command("Plan a scoop-and-dump task.")
    assert c.kind == "plan_task" and c.cycles is None and not c.start_requested
    c = parse_command("Identify the red marker.")
    assert c.kind == "identify" and c.object_phrase == "red marker"
    assert parse_command("don't scoop, stop now").kind == "stop"   # stop always wins


def test_identify_matching_and_ambiguity():
    dets = [det("m0", "red marker candidate", "color_marker"), det("m1", "red marker candidate", "color_marker"),
            det("y0", "cup")]
    assert [d.id for d in match_detections("red marker", dets)] == ["m0", "m1"]
    assert [d.id for d in match_detections("cup", dets)] == ["y0"]
    assert match_detections("blue marker", dets) == []


def test_audio_device_absence(monkeypatch):
    def missing(*a, **k):
        raise FileNotFoundError("arecord")
    monkeypatch.setattr(audio.subprocess, "run", missing)
    assert audio.list_devices() == []
    v = voice_mod.VoiceService()
    with pytest.raises(ValueError, match="not found"):
        v.start_jetson("plughw:CARD=DJI,DEV=0")
    with pytest.raises(ValueError):
        v.start_listening("plughw:CARD=DJI,DEV=0")


def test_transcription_failure_sets_error_state(monkeypatch, tmp_path):
    def boom(path):
        raise RuntimeError("model file corrupt")
    monkeypatch.setattr(voice_mod.stt, "transcribe", boom)
    v = voice_mod.VoiceService()
    v.accept_browser_audio(b"RIFFfake", "audio/wav")
    assert wait_for(lambda: v.state == "error", 5)
    assert "Transcription failed" in v.error
