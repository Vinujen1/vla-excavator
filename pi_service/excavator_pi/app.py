"""HTTP/WebSocket API for the Pi motion service.

Every endpoint except GET /health needs `Authorization: Bearer $PI_API_TOKEN`
(WebSocket: `?token=`). The hardware backend refuses to start without a token.
"""
import asyncio
import hmac
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .backends import LgpioBackend, SimulationBackend
from .config import ServiceSettings, load_hardware_config
from .controller import SCHEMA_VERSION, Controller, Rejected

log = logging.getLogger("excavator_pi")

REJECT_STATUS = {"invalid": 422, "conflict": 409, "busy": 409, "latched": 409, "expired": 409,
                 "no_clock_sync": 409, "forbidden": 403, "limit": 422, "no_feedback": 409,
                 "uncalibrated": 409, "no_heartbeat": 409}


def build_controller(settings: ServiceSettings) -> Controller:
    hw = load_hardware_config(settings.hardware_config)
    if settings.backend == "hardware":
        if len(settings.api_token) < 16:
            raise RuntimeError("PI_API_TOKEN (16+ characters) is required for the hardware backend")
        backend = LgpioBackend(hw, settings.gpiochip)
    else:
        backend = SimulationBackend(hw, settings.sim_speed_units_per_s)
    return Controller(settings, hw, backend)


def create_app(settings: ServiceSettings | None = None, controller: Controller | None = None) -> FastAPI:
    settings = settings or ServiceSettings.from_env()

    @asynccontextmanager
    async def lifespan(app):
        if getattr(app.state, "controller", None) is None:
            app.state.controller = build_controller(settings)
        log.info("Pi service started: backend=%s, latched until explicit rearm", app.state.controller.backend.kind)
        yield
        app.state.controller.close()   # outputs off on shutdown

    app = FastAPI(title="Excavator Pi motion service", lifespan=lifespan)
    app.state.controller = controller

    def ctl() -> Controller:
        return app.state.controller

    def auth(authorization: str = Header(default="")):
        token = settings.api_token
        if not token:
            return  # simulation without a token (localhost development only)
        if not hmac.compare_digest(authorization, f"Bearer {token}"):
            raise HTTPException(401, "invalid or missing token")

    @app.exception_handler(Rejected)
    async def _rejected(_req, exc: Rejected):
        return JSONResponse({"detail": exc.message, "code": exc.code}, status_code=REJECT_STATUS.get(exc.code, 409))

    @app.get("/health")
    def health():
        c = ctl()
        return {"ok": True, "schema_version": SCHEMA_VERSION, "backend": c.backend.kind,
                "latched": c.latched is not None, "auth_required": bool(settings.api_token)}

    @app.get("/state", dependencies=[Depends(auth)])
    def state():
        return ctl().state()

    class Heartbeat(BaseModel):
        session: str = Field(min_length=1, max_length=80)
        sent_at_ms: int

    @app.post("/heartbeat", dependencies=[Depends(auth)])
    def heartbeat(hb: Heartbeat):
        return ctl().heartbeat(hb.session, hb.sent_at_ms)

    @app.post("/actions", dependencies=[Depends(auth)])
    def submit(body: dict):
        return ctl().submit(body)

    @app.get("/actions/{action_id}", dependencies=[Depends(auth)])
    def get_action(action_id: str):
        r = ctl().get_action(action_id)
        if r is None:
            raise HTTPException(404, "unknown action_id")
        return r

    class StopRequest(BaseModel):
        reason: str = Field("stop requested", max_length=200)
        source: str = Field("api", max_length=40)

    @app.post("/stop", dependencies=[Depends(auth)])
    def stop(req: StopRequest | None = None):
        req = req or StopRequest()
        return ctl().stop(req.reason, req.source)

    class Rearm(BaseModel):
        session: str

    @app.post("/rearm", dependencies=[Depends(auth)])
    def rearm(req: Rearm):
        return ctl().rearm(req.session)

    @app.websocket("/ws/telemetry")
    async def telemetry(ws: WebSocket, token: str = ""):
        if settings.api_token and not hmac.compare_digest(token, settings.api_token):
            await ws.close(code=4401)
            return
        await ws.accept()
        try:
            while True:
                await ws.send_json(await asyncio.to_thread(ctl().state))
                await asyncio.sleep(0.1)
        except (WebSocketDisconnect, RuntimeError):
            pass

    return app
