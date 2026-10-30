"""Link to the Raspberry Pi motion service (pi_service/).

Two independent threads keep the link honest: a heartbeat sender (feeds the Pi's watchdog and
clock-offset estimate) and a /state poller (joint feedback, latch, faults, backend). Stop
requests are sent directly from the caller's thread with short timeouts, so they never wait
behind model inference or a slow poll.

Without VLA_PI_ENDPOINT everything reports "not configured" and execution stays blocked.
"""
import itertools
import threading
import time
import uuid

import httpx

from .config import settings
from .events import events
from .schemas import RobotState

PI_SCHEMA_VERSION = 1


class PiError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


class HardwareMonitor:
    def __init__(self, endpoint=settings.pi_endpoint, token=settings.pi_api_token, client=None, hz=5.0):
        self.endpoint = endpoint
        self._headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._client = client
        if self._client is None and endpoint:
            self._client = httpx.Client(base_url=endpoint.rstrip("/"), timeout=httpx.Timeout(0.8, connect=0.5))
        self.session = f"jetson-{uuid.uuid4().hex[:8]}"
        self._ids = itertools.count(1)
        self._period = 1.0 / hz
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.status = "not configured" if not endpoint else "connecting"
        self.error = None
        self.state = None
        self._state_at = None          # monotonic time the last /state arrived
        self._state_req_at = None      # monotonic time that /state request was sent
        self.last_contact = None
        self.hb_rtt_ms = None
        self.last_stop = None

    # ---- lifecycle -----------------------------------------------------
    def start(self):
        if self._client is None:
            return
        threading.Thread(target=self._loop, args=(self._heartbeat,), name="pi-heartbeat", daemon=True).start()
        threading.Thread(target=self._loop, args=(self._poll,), name="pi-state", daemon=True).start()

    def stop(self):
        self._stop.set()

    def _loop(self, fn):
        while not self._stop.is_set():
            fn()
            self._stop.wait(self._period)

    def _heartbeat(self):
        sent = time.time()
        try:
            r = self._client.post("/heartbeat", json={"session": self.session, "sent_at_ms": int(sent * 1000)},
                                  headers=self._headers)
            if r.status_code == 200:
                self.hb_rtt_ms = round((time.time() - sent) * 1000, 1)
        except Exception:
            pass  # the poller reports reachability

    def _poll(self):
        prev = self.status
        sent = time.monotonic()
        try:
            r = self._client.get("/state", headers=self._headers)
            if r.status_code == 401:
                self._set("auth_failed", "Pi rejected the API token (check VLA_PI_API_TOKEN)")
            elif r.status_code != 200:
                self._set("unreachable", f"/state returned HTTP {r.status_code}")
            else:
                data = r.json()
                if data.get("schema_version") != PI_SCHEMA_VERSION:
                    self._set("incompatible", f"Pi schema {data.get('schema_version')} != {PI_SCHEMA_VERSION}")
                else:
                    with self._lock:
                        self.state, self._state_at, self.last_contact = data, time.monotonic(), time.time()
                        self._state_req_at = sent
                    self._set("connected", None)
        except Exception as exc:
            self._set("unreachable", f"{type(exc).__name__}: {exc}"[:200])
        if self.status != prev:
            events.add("hardware", f"Raspberry Pi {self.status}" + (f": {self.error}" if self.error else ""),
                       "info" if self.status == "connected" else "warn")

    def _set(self, status, error):
        self.status, self.error = status, error

    # ---- readers -------------------------------------------------------
    def state_age_s(self):
        with self._lock:
            return None if self._state_at is None else round(time.monotonic() - self._state_at, 3)

    def fresh_state(self):
        """The last Pi state if it is recent enough to act on, else None."""
        age = self.state_age_s()
        if self.status != "connected" or age is None or age > settings.pi_state_max_age_s:
            return None
        with self._lock:
            return self.state

    def state_requested_after(self, mono):
        """The latest state if its request was sent after `mono` (local monotonic clock), else None.
        Uses only the Jetson clock, so Pi/Jetson clock differences don't matter."""
        with self._lock:
            if self._state_req_at is None or self._state_req_at < mono:
                return None
        return self.fresh_state()

    def backend(self):
        s = self.fresh_state()
        return s.get("backend") if s else None

    # ---- commands ------------------------------------------------------
    def send_action(self, kind, params, ttl_ms=settings.action_ttl_ms):
        if self._client is None:
            raise PiError("not_configured", "No Pi endpoint configured")
        action_id = f"{self.session}-{next(self._ids):05d}"
        body = {"schema_version": PI_SCHEMA_VERSION, "action_id": action_id, "kind": kind, "params": params,
                "issued_at_ms": int(time.time() * 1000), "ttl_ms": ttl_ms, "session": self.session}
        try:
            r = self._client.post("/actions", json=body, headers=self._headers)
        except Exception as exc:
            raise PiError("unreachable", f"{type(exc).__name__}: {exc}") from None
        if r.status_code != 200:
            detail = _detail(r)
            raise PiError(detail.get("code", f"http_{r.status_code}"), detail.get("detail", r.text[:200]))
        return r.json()

    def get_action(self, action_id):
        r = self._client.get(f"/actions/{action_id}", headers=self._headers)
        if r.status_code != 200:
            raise PiError(f"http_{r.status_code}", _detail(r).get("detail", "action lookup failed"))
        return r.json()

    def request_stop(self, reason, source):
        """Send a real stop to the Pi and record whether it was acknowledged. Never raises."""
        sent = time.time()
        ack = {"sent_at": sent, "source": source, "reason": reason, "acknowledged": False,
               "ack_latency_ms": None, "pi_response": None, "error": None, "attempts": 0}
        if self._client is None:
            ack["error"] = "No Pi configured: there was nothing to send the stop to"
        else:
            for _ in range(3):
                ack["attempts"] += 1
                try:
                    r = self._client.post("/stop", json={"reason": reason[:200], "source": source[:40]},
                                          headers=self._headers, timeout=0.5)
                    if r.status_code == 200 and r.json().get("acknowledged"):
                        ack.update(acknowledged=True, pi_response=r.json(),
                                   ack_latency_ms=round((time.time() - sent) * 1000, 1), error=None)
                        break
                    ack["error"] = f"HTTP {r.status_code}"
                except Exception as exc:
                    ack["error"] = f"{type(exc).__name__}: {exc}"[:200]
        self.last_stop = ack
        events.add("stop", f"Software stop ({source}): "
                           + (f"acknowledged by Pi in {ack['ack_latency_ms']} ms" if ack["acknowledged"]
                              else f"NOT acknowledged ({ack['error']})"),
                   "warn" if ack["acknowledged"] else "error")
        return ack

    def rearm(self):
        if self._client is None:
            raise PiError("not_configured", "No Pi endpoint configured")
        r = self._client.post("/rearm", json={"session": self.session}, headers=self._headers)
        if r.status_code != 200:
            d = _detail(r)
            raise PiError(d.get("code", f"http_{r.status_code}"), d.get("detail", "rearm failed"))
        events.add("hardware", "Pi controller rearmed by operator")
        return r.json()

    # ---- telemetry -----------------------------------------------------
    def snapshot(self):
        with self._lock:
            state = self.state
        age = self.state_age_s()
        return {
            "pi": {"status": self.status, "endpoint_configured": bool(self.endpoint), "error": self.error,
                   "last_contact": self.last_contact, "state_age_s": age, "heartbeat_rtt_ms": self.hb_rtt_ms,
                   "session": self.session, "fresh": self.fresh_state() is not None},
            "state": state if self.status == "connected" else None,
            "last_stop": self.last_stop,
        }

    def robot_state(self) -> RobotState:
        s = self.fresh_state()
        return RobotState(
            pi_connected=s is not None,
            motion_controller=(f"{s['backend']} backend, {s['state']}" if s else "Not connected"),
            joint_state={k: {"position": v["position"], "units": v["units"], "feedback": v["feedback"],
                             "feedback_age_s": v["feedback_age_s"]} for k, v in s["joints"].items()} if s else None,
            sensors=[],
            backend=s["backend"] if s else None,
            latched=s["latched"] if s else None,
            fault=s["fault"] if s else None,
            telemetry_age_s=self.state_age_s(),
            observed_at=time.time(),
        )


def _detail(r):
    try:
        d = r.json()
        return d if isinstance(d, dict) else {"detail": str(d)}
    except Exception:
        return {"detail": r.text[:200]}
