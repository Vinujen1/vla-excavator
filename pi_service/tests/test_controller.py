"""Pi controller tests (simulation backend only; no GPIO is touched)."""
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from excavator_pi.app import create_app
from excavator_pi.backends import LgpioBackend, SimulationBackend
from excavator_pi.config import HardwareConfig, ServiceSettings, load_hardware_config
from excavator_pi.controller import Controller, Rejected

CONFIG = Path(__file__).resolve().parent.parent / "config.example.json"
SESSION = "jetson-test"


def make(**kw):
    s = ServiceSettings(sim_speed_units_per_s=200.0, watchdog_s=kw.pop("watchdog_s", 5.0), **kw)
    hw = load_hardware_config(CONFIG)
    return Controller(s, hw, SimulationBackend(hw, s.sim_speed_units_per_s))


def armed(**kw):
    c = make(**kw)
    c.heartbeat(SESSION, int(time.time() * 1000))
    c.rearm(SESSION)
    return c


def action(aid="act-0001", kind="move_joint_to", params=None, issued=None, ttl=1000, session=SESSION):
    return {"schema_version": 1, "action_id": aid, "kind": kind,
            "params": params or {"joint": "boom", "target": 40.0, "tolerance": 1.0, "max_duty": 0.3, "timeout_ms": 3000},
            "issued_at_ms": int(time.time() * 1000) if issued is None else issued, "ttl_ms": ttl, "session": session}


def wait_done(c, aid, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        r = c.get_action(aid)
        if r["status"] != "running":
            return r
        time.sleep(0.01)
    raise AssertionError("action did not finish")


def test_starts_latched_with_outputs_off():
    c = make()
    assert c.latched.startswith("startup")
    assert not c.backend.energised
    with pytest.raises(Rejected) as e:
        c.heartbeat(SESSION, int(time.time() * 1000))
        c.submit(action())
    assert e.value.code == "latched"


def test_closed_loop_move_succeeds_in_simulation():
    c = armed()
    c.submit(action())
    r = wait_done(c, "act-0001")
    assert r["status"] == "succeeded"
    assert abs(r["result"]["final_position"] - 40.0) <= 1.0
    assert not c.backend.energised


def test_duplicate_is_idempotent_and_conflicting_reuse_rejected():
    c = armed()
    first = c.submit(action())
    again = c.submit(action())
    assert again["duplicate"] and again["action_id"] == first["action_id"]
    with pytest.raises(Rejected) as e:
        c.submit(action(params={"joint": "boom", "target": 10.0}))
    assert e.value.code == "conflict"


def test_expired_and_future_commands_rejected():
    c = armed()
    with pytest.raises(Rejected) as e:
        c.submit(action(aid="old-0001", issued=int(time.time() * 1000) - 5000))
    assert e.value.code == "expired"
    with pytest.raises(Rejected) as e:
        c.submit(action(aid="fut-0001", issued=int(time.time() * 1000) + 5000))
    assert e.value.code == "expired"
    # The rejection is recorded, so a retry with the same id gets the same answer.
    assert c.get_action("old-0001")["status"] == "rejected"


def test_clock_offset_is_accounted_for():
    c = make()
    skew = 60_000  # Jetson clock one minute behind the Pi
    c.heartbeat(SESSION, int(time.time() * 1000) - skew)
    c.rearm(SESSION)
    r = c.submit(action(issued=int(time.time() * 1000) - skew))
    assert r["status"] == "running"


def test_unknown_session_and_ttl_cap():
    c = armed()
    with pytest.raises(Rejected) as e:
        c.submit(action(session="other"))
    assert e.value.code == "no_clock_sync"
    with pytest.raises(Rejected) as e:
        c.submit(action(aid="ttl-0001", ttl=60_000))
    assert e.value.code == "invalid"


def test_one_action_at_a_time():
    c = armed()
    c.submit(action(params={"joint": "cabin", "target": 80.0, "timeout_ms": 3000}))
    with pytest.raises(Rejected) as e:
        c.submit(action(aid="act-0002"))
    assert e.value.code == "busy"


def test_limits_duty_and_timeout_bounds():
    c = armed()
    for aid, params, code in [
        ("lim-0001", {"joint": "boom", "target": 500.0}, "limit"),
        ("lim-0002", {"joint": "boom", "target": 30.0, "max_duty": 0.9}, "limit"),
        ("lim-0003", {"joint": "boom", "target": 30.0, "timeout_ms": 60_000}, "limit"),
        ("lim-0004", {"joint": "boom", "target": 30.0, "speed": 3}, "invalid"),
    ]:
        with pytest.raises(Rejected) as e:
            c.submit(action(aid=aid, params=params))
        assert e.value.code == code, aid


def test_move_without_feedback_or_calibration_refused():
    hw = load_hardware_config(CONFIG)
    data = hw.model_dump()
    data["joints"][0]["feedback"] = None
    data["joints"][1]["limits"] = None
    hw2 = HardwareConfig.model_validate(data)
    s = ServiceSettings(watchdog_s=5)
    c = Controller(s, hw2, SimulationBackend(hw2))
    c.heartbeat(SESSION, int(time.time() * 1000))
    c.rearm(SESSION)
    with pytest.raises(Rejected) as e:
        c.submit(action(aid="fb-0001", params={"joint": "boom", "target": 30.0}))
    assert e.value.code == "no_feedback"
    with pytest.raises(Rejected) as e:
        c.submit(action(aid="cal-0001", params={"joint": "cabin", "target": 0.0}))
    assert e.value.code == "uncalibrated"


def test_jog_requires_commissioning_and_is_unverified():
    c = armed()
    jog = {"joint": "boom", "direction": 1, "duty": 0.2, "duration_ms": 50}
    with pytest.raises(Rejected) as e:
        c.submit(action(aid="jog-0001", kind="jog", params=jog))
    assert e.value.code == "forbidden"
    c2 = armed(commissioning=True)
    c2.submit(action(aid="jog-0002", kind="jog", params=jog))
    assert wait_done(c2, "jog-0002")["status"] == "completed_unverified"


def test_stop_turns_outputs_off_latches_and_needs_rearm():
    c = armed()
    c.submit(action(params={"joint": "cabin", "target": 80.0, "timeout_ms": 3000}))
    time.sleep(0.05)
    ack = c.stop("test")
    assert ack["acknowledged"] and ack["outputs_off"] and ack["latched"].startswith("stopped")
    assert wait_done(c, "act-0001")["status"] == "stopped"
    with pytest.raises(Rejected):
        c.submit(action(aid="act-0002"))
    c.heartbeat(SESSION, int(time.time() * 1000))
    c.rearm(SESSION)
    assert c.submit(action(aid="act-0003"))["status"] == "running"


def test_heartbeat_loss_while_armed_faults_and_latches():
    c = armed(watchdog_s=0.2)
    c.submit(action(params={"joint": "cabin", "target": 80.0, "timeout_ms": 3000}))
    time.sleep(0.5)
    assert c.fault["code"] == "communication_lost"
    assert c.latched.startswith("fault")
    assert not c.backend.energised
    # Reconnecting alone does not resume anything.
    c.heartbeat(SESSION, int(time.time() * 1000))
    assert c.latched.startswith("fault")


def test_stall_faults():
    c = armed()
    c.backend._speed = 0.0  # motor "jammed"
    c.submit(action(params={"joint": "boom", "target": 50.0, "timeout_ms": 3000}))
    r = wait_done(c, "act-0001")
    assert r["status"] == "failed" and "stall" in r["error"]
    assert c.fault["code"] == "action_failed"


def test_hardware_backend_refuses_unverified_pins():
    with pytest.raises(RuntimeError, match="pins_verified"):
        LgpioBackend(load_hardware_config(CONFIG))


def test_http_api_auth_and_flow():
    settings = ServiceSettings(api_token="t" * 20, sim_speed_units_per_s=200.0, watchdog_s=5.0)
    with TestClient(create_app(settings)) as client:
        assert client.get("/health").json()["auth_required"]
        assert client.get("/state").status_code == 401
        h = {"Authorization": "Bearer " + "t" * 20}
        client.post("/heartbeat", json={"session": SESSION, "sent_at_ms": int(time.time() * 1000)}, headers=h)
        assert client.post("/actions", json=action(), headers=h).status_code == 409   # latched at startup
        assert client.post("/rearm", json={"session": SESSION}, headers=h).json()["ok"]
        assert client.post("/actions", json=action(aid="http-0001"), headers=h).json()["status"] == "running"
        assert client.get("/actions/http-0001", headers=h).status_code == 200
        ack = client.post("/stop", json={"reason": "test"}, headers=h).json()
        assert ack["acknowledged"]
        assert client.get("/state", headers=h).json()["latched"].startswith("stopped")
