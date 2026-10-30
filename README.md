# VLA Excavator — Operator Console

A browser dashboard served from the Jetson: live RealSense perception, target selection, typed and
voice commands, Cosmos 3-assisted decisions, and a **bounded autonomous scoop-and-dump supervisor**
that drives a Raspberry Pi motion service one joint move at a time.

**Three modes, always labelled in the header:** *Perception + preview* (no Pi), *Simulation*
(simulated Pi; nothing physical moves) and *Hardware execution*. Hardware execution is **blocked**
today: there's no position-feedback driver, no verified pin mapping, no `config/calibration.json` and no
camera-to-excavator calibration. See `docs/COMMISSIONING.md`. The physical E-stop is always the
primary stop.

* Project context and architecture: `docs/CONTEXT_PROMPT.md`
* Pi service, protocol and wiring: `pi_service/README.md`
* Supervised hardware bring-up: `docs/COMMISSIONING.md`

## Run everything (simulation)

```bash
cd /home/vinujen/Desktop/excavator-vla
cp .env.example .env             # then set VLA_PI_API_TOKEN to a random value (see the file)
scripts/start_sim_pi.sh          # simulated Pi on 127.0.0.1:8100 (starts latched)
scripts/start_console.sh         # console on 127.0.0.1:8000, log in data/logs/console.out
scripts/stop.sh                  # stops both
```

Then forward port 8000 (below), open http://localhost:8000, press **Rearm Pi controller**, type
*"Start autonomous scoop-and-dump for two cycles."*, **Send command**, check the interpreted goal and
target, then **Approve, arm & start**. Software stop: the red button or **Esc**.

## Quick start (on the Jetson)

```bash
cd /home/vinujen/Desktop/excavator-vla
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Wait for `YOLO model yolo11n.pt ready on cuda:0` in the log (~15 s), then open the page
from your laptop as described below. Stop the server with Ctrl+C.

Only one process can own the camera. A second server exits with `Camera is owned by another
process (pid …)`. Stop the first one, or run `tools/camera_check.py` only while the server is stopped.

## Open it on your laptop (port forwarding)

The backend binds to `127.0.0.1` only, so it's reachable only through a tunnel.

**VS Code Remote SSH (recommended):** open the *Ports* panel (View → Open View → Ports),
click *Forward a Port*, and enter `8000`. VS Code often does this automatically when
uvicorn starts. Open **http://localhost:8000** on the laptop.

**Plain SSH** (run on the laptop):

```bash
ssh -N -L 8000:localhost:8000 vinujen@<jetson-host>
```

Then open http://localhost:8000.

Use `localhost`, not the Jetson's IP. Browsers only allow microphone access (the
"Laptop mic" option) on `https://` or `http://localhost` pages.

## Dashboard layout

| Area | What it shows |
|---|---|
| Header | Backend link (WebSocket), camera, detector, Pi state, operating mode |
| Live camera | Colour or depth view, detection boxes (click to select), selected-target highlight and depth ROI; measured camera fps, frame age, detection fps, inference ms, box age |
| Selected target | Label, source, confidence, box/centre pixels, depth (median, spread, valid %), XYZ in the **camera frame**, last-seen time, stale state |
| Operator command | Typed instruction, **Jetson mic** push-to-record (ALSA device picker), or **Laptop mic** (browser recording uploaded to the Jetson); transcript with source, time and playback |
| Scene objects | Current frame's detections, linked to the boxes (hover/click) |
| Detection | YOLO on/off, confidence threshold, the model's 80 classes, colour-marker mode with HSV sliders |
| Stop bar | Software stop (button or Esc) with the Pi's acknowledgement; reminder that the physical E-stop is primary |
| Autonomous task | Provider choice, interpreted goal/target for approval, plan chips with progress, cycle count, timeout, pause/resume/cancel/reset, blockers, decision history with explanations and latency |
| Raspberry Pi & motion | Link, telemetry age, heartbeat, latch/fault, outputs, joints with feedback source (simulated is labelled), calibration status, Rearm |
| Cosmos 3 reasoning | Endpoint, configured and server-reported model, last real latency, errors, *Test with a real request* |
| Hardware & execution | Pi link, sensor freshness, joint state, motion controller (all *Not connected*), a disabled Execute button with the reasons, E-stop note |
| Event log | Timestamped camera/detection/target/voice/planner events |

## Behaviour and honesty rules built in

- **Detections are per frame.** IDs are only valid within one detection frame. A selected
  target is carried forward only by a simple match (same label, IoU ≥ 0.3, between
  consecutive detection frames). It is *not* a tracker. With no match for
  `VLA_TARGET_STALE_S` (1.5 s), or if the camera stalls or disconnects, the target is
  marked **stale**. The box turns amber and dashed, and the panel says why.
- **Target depth** is the median of valid aligned-depth pixels in the central 40% of the
  box. Pixels outside 0.28–8 m are rejected, and fewer than 25% valid pixels gives
  *no valid depth*. A large interquartile spread is flagged as *inconsistent*, since
  background or an occluder is probably mixed in.
- **XYZ is in the camera frame** (x right, y down, z forward, metres), computed by
  deprojecting with the RealSense colour intrinsics. There's no camera-to-excavator
  calibration, so no excavator coordinates or reachability are shown.
- **YOLO11n is pretrained on COCO.** It knows 80 everyday classes (person, cup, bottle,
  chair…) and *doesn't* know sandbox objects. The class list is shown in the UI.
- **Colour-marker mode** (OpenCV HSV threshold + contours) labels regions as
  `<name> marker candidate`. A matching colour doesn't make something a cone.
- **Audio source is always explicit.** *Jetson mic* records on a Jetson ALSA device and
  warns when no DJI receiver is present. *Laptop mic* records in the browser and uploads.
  Neither stands in for the other.
- **Providers only propose.** Cosmos 3 (`VLA_COSMOS_BASE_URL`, an OpenAI-compatible endpoint,
  called through LangChain) or the *rule-based development provider* (labelled; not Cosmos, not an LLM)
  return intent, target id, next skill and an explanation, validated by strict Pydantic schemas with no
  distance, coordinate or motor fields. Cosmos shows *Verified* only after a real inference succeeds.
  The API key stays in the request header and never reaches the browser or logs.
- **Deterministic validation decides.** Arming needs operator approval of the goal and target plus no
  blockers (Pi telemetry fresh and unlatched, feedback and limits for every joint, matching calibration,
  and for hardware: verified pins, verified calibration, camera-to-base, live camera, target inside the
  dig region). Each skill runs as single-joint moves, re-checked from fresh Pi feedback.
- **Stops.** The software stop sends a real `POST /stop` to the Pi and shows whether it was
  acknowledged. Faults, timeouts and lost heartbeats stop and latch the Pi; only an explicit rearm
  clears it. The physical E-stop is independent and primary.
- The legacy `POST /api/plan` step-list preview (demo planner) remains for API compatibility.
- Every plan and decision saves an observation (bounded retention) to
  `data/observations/obs_*/`: `color.jpg`, `depth_aligned_u16.npy` (aligned, ×0.001 m),
  `observation.json` (instruction + source, target, scene detections, camera intrinsics
  and timestamps, robot state), and `proposal.json` if one was produced.

## Configuration (environment variables)

Set these before the uvicorn command, e.g. `VLA_DETECT_MAX_FPS=8 .venv/bin/python -m uvicorn …`

| Variable | Default | Purpose |
|---|---|---|
| `VLA_CAMERA_WIDTH/HEIGHT/FPS` | 640 / 480 / 30 | RealSense colour + depth profile |
| `VLA_STREAM_FPS` | 15 | Max MJPEG rate to the browser (JPEG encoded once per frame, shared by clients) |
| `VLA_JPEG_QUALITY` | 75 | Stream JPEG quality |
| `VLA_DETECT` | true | Load and run YOLO at all |
| `VLA_YOLO_MODEL` | `models/yolo11n.pt` | Weights file (a TensorRT `.engine` also works) |
| `VLA_YOLO_IMGSZ` | 640 | Inference size (480 or 320 is faster) |
| `VLA_YOLO_DEVICE` / `VLA_YOLO_HALF` | cuda:0 / true | GPU + FP16 |
| `VLA_DETECT_MAX_FPS` | 15 | Detection rate cap (headroom for the 8 GB shared memory/GPU) |
| `VLA_DETECT_CONF` | 0.35 | Initial confidence threshold |
| `VLA_TARGET_STALE_S` | 1.5 | Seconds without a match before a target is stale |
| `VLA_DEPTH_MIN_M` / `VLA_DEPTH_MAX_M` | 0.28 / 8.0 | Plausible depth range |
| `VLA_RECORD_MAX_S` | 15 | Push-to-record cap |
| `WHISPER_MODEL` | base.en | faster-whisper model (CPU int8) |
| `VLA_COSMOS_BASE_URL` / `VLA_COSMOS_MODEL` / `VLA_COSMOS_API_KEY` | unset / `nvidia/Cosmos3-Edge` / unset | OpenAI-compatible Cosmos 3 reasoner (hosted) |
| `VLA_PI_ENDPOINT` / `VLA_PI_API_TOKEN` | unset | Pi motion service (`pi_service/`) |
| `VLA_MAX_CYCLES` / `VLA_TASK_TIMEOUT_S` | 5 / 300 | Autonomous task bounds |
| `VLA_LISTEN_MAX_S` | 60 | Listening-mode limit |
| `VLA_KEEP_OBSERVATIONS` / `VLA_KEEP_RECORDINGS` | 200 / 50 | Disk retention |

The full list with comments is in `.env.example`.

Detection uses a bounded latest-frame loop. It waits for the newest camera frame,
runs inference, and drops any frames that arrived meanwhile (counted as
`frames_skipped_while_busy`). Nothing queues up.

## Project layout

```
app/config.py        settings (env vars)
app/camera.py        RealSense thread: aligned colour+depth, reconnect, stall detection, process lock, shared JPEG cache
app/detection.py     YOLO + HSV colour-marker detector (bounded latest-frame loop)
app/targets.py       target depth estimate, XYZ deprojection, selection / association / staleness
app/audio.py         ALSA device list, fixed-length and push-to-record capture
app/voice.py         Jetson-mic / browser-mic voice service + Whisper transcription
app/stt.py           faster-whisper wrapper
app/observations.py  observation assembly and saving
app/planner.py       ReasoningProvider interface: CosmosProvider, DemoPlanner
app/hardware.py      Pi / motion-controller status (read-only)
app/schemas.py       Pydantic models (Detection, Target, Observation, TaskProposal, …)
app/events.py        event log
app/main.py          FastAPI routes, WebSocket /ws/telemetry, MJPEG /video.mjpg, serves frontend/dist
frontend/            React + TypeScript + Tailwind (Vite)
tests/test_core.py   hardware-free tests
tools/camera_check.py  camera fps / latency / depth-noise benchmark
models/yolo11n.pt    detector weights
```

## Development

```bash
# backend tests (no hardware needed)
.venv/bin/python -m pytest -q tests
(cd pi_service && ../.venv/bin/python -m pytest -q tests)

# frontend production build (output: frontend/dist, served by the backend)
export PATH=~/.local/opt/node/bin:$PATH
cd frontend && npm run build

# frontend dev server with hot reload (proxies to the backend on :8000; forward port 5173)
npm run dev
```

## One-time setup (already done on this Jetson)

No sudo was available, so everything is installed per-user or in the venv. System
Python, CUDA, OpenCV and TensorRT are untouched.

```bash
cd /home/vinujen/Desktop/excavator-vla
# venv that can see NVIDIA's system OpenCV/TensorRT; pip bootstrapped (python3.10-venv isn't installed)
python3 -m venv --system-site-packages --without-pip .venv
curl -fsSL https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py && .venv/bin/python /tmp/get-pip.py

# GPU PyTorch for JetPack 6 (wheels from the Ultralytics Jetson guide)
.venv/bin/python -m pip install "numpy<2" \
  https://github.com/ultralytics/assets/releases/download/v0.0.0/torch-2.10.0-cp310-cp310-linux_aarch64.whl \
  https://github.com/ultralytics/assets/releases/download/v0.0.0/torchvision-0.25.0-cp310-cp310-linux_aarch64.whl
# torch 2.10 needs libcudss.so.0. The guide installs it with sudo apt; this pip wheel
# avoids that. --no-deps keeps JetPack's own cuBLAS. app/gpu_libs.py preloads it.
.venv/bin/python -m pip install --no-deps nvidia-cudss-cu12==0.7.1.6
# Ultralytics without opencv-python (which would shadow NVIDIA's OpenCV build)
.venv/bin/python -m pip install --no-deps ultralytics==8.4.191
.venv/bin/python -m pip install -r requirements.txt
mkdir -p models && curl -fsSL -o models/yolo11n.pt \
  https://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11n.pt

# Node.js 22 LTS in the home directory (checksum-verified tarball from nodejs.org)
#   ~/.local/opt/node -> node-v22.20.0-linux-arm64
export PATH=~/.local/opt/node/bin:$PATH
cd frontend && npm install && npm run build
```

## Tested environment (2026-10-27)

| Item | Finding |
|---|---|
| Board / OS | Jetson Orin Nano Super 8 GB, L4T R36.4.4 (JetPack 6.2.x), Ubuntu 22.04.5, power mode 25 W |
| Python / CUDA | 3.10.12 system; CUDA 12.6, cuDNN 9.3, TensorRT 10.3 (JetPack) |
| PyTorch | 2.10.0 + torchvision 0.25.0 (venv), CUDA available on "Orin" |
| Camera | D435i fw 5.17.0.10 on **USB 2.1**; 640×480 @ 30 fps colour+depth, 0 drops |
| Detector | YOLO11n, CUDA FP16, imgsz 640: ~50–70 ms per frame incl. pre/post-processing, ~15 detections/s |
| Microphone | DJI receiver **not connected** |

## Known issues / blockers

- **DJI receiver not detected.** Jetson-mic recording works with the system default input
  but captures silence. Live speech on the DJI hasn't been tested. Whisper was verified
  with a known speech clip.
- **Camera on USB 2.1.** 640×480 @ 30 works, but use a USB3 data cable directly on the
  Jetson for higher resolutions.
- **IMU unavailable.** The pip `pyrealsense2` uses the V4L2 backend, and the stock Jetson
  kernel lacks the `hid-sensor-*` modules. Fix: build librealsense with
  `-DFORCE_RSUSB_BACKEND=true` (needs sudo).
- **Detector latency** could be cut by exporting a TensorRT engine
  (`yolo export model=models/yolo11n.pt format=engine half=True`, then
  `VLA_YOLO_MODEL=models/yolo11n.engine`). Not done yet. MAXN power mode would also help.
- **Whisper on CPU.** The aarch64 CTranslate2 wheel has no CUDA.
- **No camera-to-excavator calibration, no Pi, no controller.** These are expected in this milestone.

## Camera readiness check

Stop the server first, then run:

```bash
.venv/bin/python tools/camera_check.py 10
```

| Profile (USB 2.1, static scene) | fps | drops | latency | align | depth noise |
|---|---|---|---|---|---|
| 640×480 @ 15 | 15.0 | 0 | 69 ms | 4.2 ms | 7.4 mm @ 0.65 m |
| 640×480 @ 30 | 30.0 | 0 | 36 ms | 4.1 ms | 2.7 mm @ 0.65 m |
| 424×240 + 480×270 @ 30 | 30.0 | 0 | 34 ms | 2.0 ms | 4.0 mm @ 1.04 m |

See `DEMO.md` for the supervisor demonstration script.
