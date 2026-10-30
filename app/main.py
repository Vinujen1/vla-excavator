"""VLA Excavator operator console backend. Perception, operator input and task previews only:
nothing here can move the machine."""
import asyncio
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from typing import Literal, Optional

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import audio, observations, retention
from .camera import RealSenseCamera
from .commands import CommandService
from .config import ROOT, settings
from .decisions import Cosmos3Provider, RulesProvider
from .detection import DetectionSettings, Detector
from .events import events
from .hardware import HardwareMonitor, PiError
from .planner import PROVIDERS, ProviderUnavailable
from .schemas import Instruction, TaskProposal
from .supervisor import Supervisor, TransitionError
from .targets import TargetSelector
from .validation import Calibrations
from .voice import RECORDINGS, VoiceService

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)  # Pi heartbeat/state polls at 5 Hz would flood the log

FRONTEND = ROOT / "frontend" / "dist"

LOGS = settings.data_dir / "logs"

camera = RealSenseCamera()
selector = TargetSelector(camera)
detector = Detector(camera, on_frame=selector.update)
hardware = HardwareMonitor()
calibrations = Calibrations()
cosmos = Cosmos3Provider()
decision_providers = {p.id: p for p in (cosmos, RulesProvider())}
supervisor = Supervisor(camera, detector, selector, hardware, calibrations, decision_providers)
commands = CommandService(supervisor, selector, detector, events)
voice = VoiceService(on_transcript=lambda t: commands.handle(t["text"], t["source"] or "jetson_mic", confirmed=False,
                                                             recording=t.get("recording"),
                                                             transcribed_at=t.get("transcribed_at_unix")))
task_preview: dict = {"proposal": None, "error": None, "observation_id": None}


@asynccontextmanager
async def lifespan(_app):
    camera.start()          # raises CameraBusyError if another process owns the camera
    retention.prune(LOGS, settings.keep_session_logs - 1, "session_*.jsonl")
    events.open_session_file(LOGS, settings.session_log_max_mb)
    detector.start()
    hardware.start()
    events.add("system", "Operator console started. Execution needs a connected Pi, calibration and operator approval; "
                         "the physical E-stop is the primary stop")
    yield
    if supervisor.state in ("EXECUTING", "VERIFYING", "PAUSED", "READY"):
        supervisor.stop(source="shutdown", reason="console shutting down")
    hardware.stop()
    detector.stop()
    camera.stop()


app = FastAPI(title="VLA Excavator Operator Console", lifespan=lifespan)


# ---- telemetry --------------------------------------------------------
MODE_LABELS = {
    "hardware": "Hardware execution (real Pi outputs)",
    "simulation": "Simulation (simulated Pi; camera perception is real when connected)",
    "perception_preview": "Perception + plan preview (no motion controller)",
}


def mode_summary():
    backend = hardware.backend()
    mode = backend if backend in ("hardware", "simulation") else "perception_preview"
    return {"id": mode, "label": MODE_LABELS[mode]}


def snapshot():
    det = detector.latest()
    target = selector.state()
    return {
        "mode": mode_summary(),
        "supervisor": supervisor.snapshot(),
        "decision_providers": [p.status() for p in decision_providers.values()],
        "calibration": calibrations.summary(),
        "command": commands.last,
        "session_log": events.session_file.name if events.session_file else None,
        "time": time.time(),
        "camera": camera.state(),
        "detection": {
            **detector.stats(),
            "frame": det.model_dump() if det else None,
            "frame_age_s": round(time.time() - det.frame_time, 3) if det else None,
        },
        "target": target.model_dump() if target else None,
        "voice": voice.snapshot(),
        "planner": {"providers": [p.status() for p in PROVIDERS.values()], **task_preview},
        "hardware": hardware.snapshot(),
    }


@app.get("/api/state")
def state():
    return snapshot()


@app.websocket("/ws/telemetry")
async def telemetry(ws: WebSocket):
    await ws.accept()
    last_event = 0
    period = 1.0 / settings.telemetry_hz
    try:
        while True:
            snap = await asyncio.to_thread(snapshot)
            new_events = events.since(last_event)[-100:]
            if new_events:
                last_event = new_events[-1]["seq"]
            await ws.send_json({"type": "telemetry", **snap, "events": new_events})
            await asyncio.sleep(period)
    except (WebSocketDisconnect, RuntimeError):
        pass


# ---- video ------------------------------------------------------------
@app.get("/video.mjpg")
def video(view: str = "color"):
    if view not in ("color", "depth"):
        raise HTTPException(400, "view must be color or depth")
    min_period = 1.0 / settings.stream_fps

    def frames():
        last_seq = 0
        while True:
            frame = camera.wait_for_new(last_seq, timeout=1.0)
            if frame is None:
                continue  # camera down: keep the connection, resume when frames return
            seq, jpg = camera.jpeg(view)
            if jpg is None:
                continue
            last_seq = seq
            yield b"--frame\r\nContent-Type: image/jpeg\r\nX-Frame-Seq: " + str(seq).encode() + b"\r\n\r\n" + jpg + b"\r\n"
            time.sleep(min_period)

    return StreamingResponse(frames(), media_type="multipart/x-mixed-replace; boundary=frame",
                             headers={"Cache-Control": "no-store"})


# ---- detection & targets ----------------------------------------------
@app.get("/api/detection/settings")
def get_detection_settings():
    return detector.settings


@app.put("/api/detection/settings")
def put_detection_settings(new: DetectionSettings):
    detector.update_settings(new)
    return detector.settings


class SelectRequest(BaseModel):
    frame_number: int
    detection_id: str


@app.post("/api/target/select")
def select_target(req: SelectRequest):
    frame = detector.frame(req.frame_number)
    if frame is None:
        raise HTTPException(409, "That detection frame is too old. Click the object again")
    try:
        return selector.select(frame, req.detection_id)
    except KeyError:
        raise HTTPException(404, f"Detection {req.detection_id} not in frame {req.frame_number}")


@app.post("/api/target/clear")
def clear_target():
    selector.clear()
    return {"ok": True}


# ---- commands & autonomous task -------------------------------------------
class CommandRequest(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    source: Literal["typed", "jetson_mic", "browser_mic"] = "typed"
    provider: str = "rules"
    recording: Optional[str] = None
    transcribed_at: Optional[float] = None


@app.post("/api/command")
def command(req: CommandRequest):
    """The single pipeline for typed text and confirmed transcripts."""
    return commands.handle(req.text, req.source, req.provider, confirmed=True, recording=req.recording,
                           transcribed_at=req.transcribed_at)


def _task_call(fn, *args):
    try:
        result = fn(*args)
    except TransitionError as exc:
        raise HTTPException(409, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "result": result.model_dump() if hasattr(result, "model_dump") else result,
            "supervisor": supervisor.snapshot()}


@app.post("/api/task/approve")
def task_approve():
    return _task_call(supervisor.approve)


@app.post("/api/task/reject")
def task_reject():
    return _task_call(supervisor.reject)


@app.post("/api/task/start")
def task_start():
    return _task_call(supervisor.start)


@app.post("/api/task/pause")
def task_pause():
    return _task_call(supervisor.pause, "dashboard")


@app.post("/api/task/resume")
def task_resume():
    return _task_call(supervisor.resume)


@app.post("/api/task/cancel")
def task_cancel():
    return _task_call(supervisor.cancel, "dashboard")


@app.post("/api/task/reset")
def task_reset():
    return _task_call(supervisor.reset)


class StopRequest(BaseModel):
    source: str = Field("dashboard", max_length=40)


@app.post("/api/stop")
def software_stop(req: StopRequest | None = None):
    """Software stop: supplementary to the physical E-stop. Sends a real stop to the Pi."""
    return supervisor.stop(source=(req or StopRequest()).source, reason="software stop")


@app.post("/api/pi/rearm")
def pi_rearm():
    if supervisor.state in ("EXECUTING", "VERIFYING", "READY", "PLANNING"):
        raise HTTPException(409, f"Rearm is not allowed while the supervisor is {supervisor.state}")
    try:
        return hardware.rearm()
    except PiError as exc:
        raise HTTPException(409, f"{exc.code}: {exc.message}")


@app.post("/api/cosmos/probe")
def cosmos_probe():
    """A real request to the configured Cosmos endpoint (lists models, then one tiny inference)."""
    report = cosmos.probe()
    events.add("cosmos", "Probe: " + ("inference OK in " f"{report['latency_ms']} ms" if report["inference_ok"]
                                      else f"failed ({report['error']})"), "info" if report["inference_ok"] else "warn")
    return report


@app.post("/api/calibration/reload")
def calibration_reload():
    calibrations.reload()
    events.add("calibration", "Calibration files reloaded")
    return calibrations.summary()


@app.get("/api/logs/session")
def session_log():
    if events.session_file is None or not events.session_file.exists():
        raise HTTPException(404, "No session log yet")
    return FileResponse(events.session_file, media_type="application/x-ndjson", filename=events.session_file.name)


# ---- voice ------------------------------------------------------------
@app.get("/api/audio/devices")
def audio_devices():
    return audio.list_devices()


class ListenStart(BaseModel):
    device: str
    max_s: Optional[int] = Field(None, ge=5, le=600)


@app.post("/api/voice/listen/start")
def listen_start(req: ListenStart):
    try:
        voice.start_listening(req.device, req.max_s)
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))
    return voice.snapshot()


@app.post("/api/voice/listen/stop")
def listen_stop():
    voice.stop_listening()
    return voice.snapshot()


class JetsonStart(BaseModel):
    device: str


@app.post("/api/voice/jetson/start")
def voice_start(req: JetsonStart):
    try:
        voice.start_jetson(req.device)
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))
    return voice.snapshot()


@app.post("/api/voice/jetson/stop")
def voice_stop():
    try:
        voice.stop_jetson()
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))
    return voice.snapshot()


@app.post("/api/voice/browser")
async def voice_browser(request: Request):
    data = await request.body()
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(413, "Recording too large")
    try:
        voice.accept_browser_audio(data, request.headers.get("content-type", ""))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))
    return voice.snapshot()


@app.get("/recordings/{name}")
def get_recording(name: str):
    path = RECORDINGS / Path(name).name
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path)


# ---- task preview -----------------------------------------------------
class PlanRequest(BaseModel):
    instruction: Instruction
    provider: str = "demo"


@app.post("/api/plan")
def plan(req: PlanRequest):
    provider = PROVIDERS.get(req.provider)
    if provider is None:
        raise HTTPException(404, f"Unknown provider {req.provider}")
    try:
        obs, frame = observations.build(req.instruction, camera, detector, selector, hardware)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc))
    try:
        proposal: TaskProposal = provider.propose(obs)
    except ProviderUnavailable as exc:
        observations.save(obs, frame, None)
        task_preview.update(proposal=None, error=f"{provider.name}: {exc}", observation_id=obs.id)
        events.add("planner", f"{provider.name}: {exc}", "warn")
        raise HTTPException(503, str(exc))
    observations.save(obs, frame, proposal)
    task_preview.update(proposal=proposal.model_dump(), error=None, observation_id=obs.id)
    events.add("planner", f"Task preview from {provider.name}: {len(proposal.steps)} steps, "
                          f"{len(proposal.warnings)} warning(s). Saved {obs.id}")
    return {"observation_id": obs.id, "proposal": proposal}


@app.post("/api/plan/clear")
def clear_plan():
    task_preview.update(proposal=None, error=None, observation_id=None)
    events.add("planner", "Task preview cleared")
    return {"ok": True}


@app.get("/api/observations")
def list_observations():
    return observations.recent()


# ---- frontend (production build) --------------------------------------
if FRONTEND.exists():
    app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")
else:
    @app.get("/")
    def no_frontend():
        return {"error": "Frontend not built. Run: cd frontend && npm run build"}
