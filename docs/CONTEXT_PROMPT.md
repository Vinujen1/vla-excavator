# Project context: excavator-vla

Use this as the source of truth for what exists. If the code disagrees, the code wins. Don't
assume features that aren't listed. Last updated 2026-10-27.

## What it is

A localhost operator console on an **NVIDIA Jetson Orin Nano Super 8 GB** (JetPack 6.2.x / L4T
R36.4.4, Ubuntu 22.04, Python 3.10, CUDA 12.6) for a small excavator in a controlled sandbox, plus a
separately deployable **Raspberry Pi 5 motion service**. It provides:

1. Live **RealSense D435i** colour + aligned depth, **YOLO11n** (COCO, CUDA FP16) and HSV colour-marker detection.
2. Operator target selection with measured depth, validity and staleness.
3. **One command pipeline** for typed text and voice (Jetson mic push-to-talk, bounded listening mode,
   laptop browser mic), transcribed locally with faster-whisper.
4. A **bounded autonomous supervisor** for stationary scoop-and-dump: provider proposes → deterministic
   validation → operator approval → one Pi joint move at a time → re-observe and verify → next skill.
5. **Decision providers:** Cosmos 3 (via an OpenAI-compatible endpoint, through LangChain) and a labelled
   rule-based development provider.
6. A Pi service with authentication, idempotent bounded actions, a watchdog, stop latching and explicit rearm.

### Three operating modes (never conflated)

| Mode | When | What moves |
|---|---|---|
| Perception + plan preview | No fresh Pi telemetry | Nothing. Planning works; arming is blocked |
| Simulation | Pi service reports `backend: simulation` | Simulated joints only. Camera perception is real if connected |
| Hardware execution | Pi reports `backend: hardware` and **all** blockers clear | Real motors |

Hardware execution is currently **blocked**: there's no `config/calibration.json`, no position-feedback
driver, the pin mapping is unverified, and there's no camera-to-base calibration.

## Status as of 2026-10-27

- Camera: D435i fw 5.17.0.10 on USB 2.1, 640×480@30 colour+depth (it was unplugged at the first check and connected later).
- YOLO11n on cuda:0 FP16: ~55 ms inference, ~15 detections/s.
- Audio: only the Jetson's internal APE endpoints (hidden) and PulseAudio default. **No DJI receiver or USB mic detected.**
- Cosmos 3: **not deployed**. No local runtime fits this JetPack 6.2 board (see below). The provider code is tested against fake replies only.
- Pi: no physical Pi connected. The simulated Pi runs on the Jetson (`scripts/start_sim_pi.sh`).

## Cosmos 3 findings (checked against NVIDIA sources, 2026-10-27)

- Checkpoints: `nvidia/Cosmos3-Edge` (4B, reasoner + generator, BF16 only tested, OpenMDW-1.1 licence),
  `nvidia/Cosmos3-Nano` (16B), `nvidia/Cosmos3-Super` (64B).
- Reasoner interface: OpenAI-compatible chat completions (`vllm serve nvidia/Cosmos3-Edge`, or the NIM
  `nvcr.io/nim/nvidia/cosmos3-reasoner` serving `nvidia/cosmos3-nano-reasoner`). Images as JPEG/PNG data URIs,
  video as mp4 (4 fps default). Thinking on by default (`chat_template_kwargs.enable_thinking`); NIM uses a `<think>/<answer>` prompt format.
- Jetson AI Lab lists Cosmos3-Edge at 32 GB RAM with no Orin Nano serve command. The only Orin Nano 8 GB
  path found (NVIDIA-AI-IOT live-vision-cosmos-demo) needs **JetPack 7.2.1 / L4T 39.2.1**, CUDA 13.2 and
  TensorRT-Edge-LLM with a >1 h on-device quantised build. This board runs JetPack 6.2, so that means a reflash.
  Hence the supported setup is a **separately hosted** Cosmos 3 endpoint (`VLA_COSMOS_BASE_URL`).
- Cosmos Reason 1/2 are **not** used and must not be labelled Cosmos 3.

## Layout

```
app/
  config.py        Settings from env vars (VLA_*); see .env.example
  camera.py        RealSense thread: aligned colour+depth, reconnect, stall detection, process lock, JPEG cache
  detection.py     YOLO + HSV markers, bounded latest-frame loop
  targets.py       Depth estimate (median of central ROI, IQR spread), XYZ deprojection, selection/association/staleness
  audio.py, stt.py ALSA capture (arecord), faster-whisper (CPU int8) with low-confidence flag
  voice.py         Push-to-talk, bounded listening mode, browser upload; hands transcripts to the command pipeline
  commands.py      parse_command() (deterministic) + CommandService: stop/pause/cancel/resume/identify/plan_task
  decisions.py     DecisionProvider ABC; Cosmos3Provider (LangChain ChatOpenAI + PydanticOutputParser); RulesProvider
  supervisor.py    State machine, TRANSITIONS table, execution worker, late-reply rejection
  validation.py    Execution blockers, target checks, goal/decision validation, skill→joint moves with limit checks
  calibration.py   Calibration schema, SKILL_MOVES, camera_to_base transform, regions
  hardware.py      Pi link: heartbeat thread, /state poller, actions, stop with acknowledgement, rearm
  observations.py  Observation snapshots (image, aligned depth, JSON, decision) with retention
  events.py        Event log + per-session JSONL file (size-capped, downloadable)
  retention.py     Keep newest N observations/recordings/logs
  planner.py       Legacy DemoPlanner preview (POST /api/plan), kept for API compatibility
  main.py          FastAPI routes, WebSocket telemetry, MJPEG stream, static frontend
pi_service/        Separately deployable Pi service (excavator_pi package, tests, README, config.example.json)
config/            calibration.simulation.json (simulation only), calibration.hardware.template.json
frontend/src/      React 18 + TS + Tailwind: StopBar, TaskPanel, CosmosPanel, HardwarePanel, CommandPanel, …
scripts/           start_console.sh, start_sim_pi.sh, stop.sh
docs/              CONTEXT_PROMPT.md (this file), COMMISSIONING.md
tests/             Jetson tests (core + autonomy); pi_service/tests (controller)
```

## Supervisor

States: DISARMED, READY, PLANNING, AWAITING_APPROVAL, EXECUTING, VERIFYING, PAUSED, COMPLETED, FAULT, STOPPED.
Allowed transitions are listed in `TRANSITIONS` in `app/supervisor.py`.

- Plan: command → observation (image, scene, target, robot state) → provider `interpret` → `validate_goal`
  (intent allowlist, target id must match the operator's selection, operator-stated cycles win, ≤ `VLA_MAX_CYCLES`) → AWAITING_APPROVAL.
- **Approve & arm** re-checks the mode, target identity/freshness, proposal age and all blockers → READY.
  If the command said "start", approval also starts the task.
- Execution: before each skill: fresh Pi state, calibration, camera (hardware), target freshness before
  dig skills → provider `next_skill` → `validate_decision` (must equal the next planned skill, or pause / one
  bounded retry / complete). Each skill is a sequence of single-joint `move_joint_to` actions; after each,
  the joint position is re-read from a Pi state requested after the move and checked against tolerance.
- Plan per cycle: rotate_to_dig, lower_to_dig, scoop, lift, rotate_to_dump, dump; then return_home once.
- Pause and cancel take effect after the current bounded move; stop is immediate (Pi `/stop` from the request thread).
- Every provider request carries a generation number; stop, pause and cancel bump it, so late replies are discarded.
- Active-time task timeout leads to FAULT, plus a real Pi stop.
- FAULT and STOPPED need an explicit reset; the Pi needs an explicit rearm.

Cosmos never outputs distances, coordinates or motor commands: its schemas (`GoalProposal`,
`SkillDecision`) have no such fields and forbid extra keys.

## API (Jetson, 127.0.0.1:8000)

Existing: `/api/state`, `WS /ws/telemetry`, `/video.mjpg`, `/api/detection/settings`, `/api/target/select|clear`,
`/api/audio/devices`, `/api/voice/jetson/start|stop`, `/api/voice/browser`, `/recordings/{name}`,
`/api/plan` (legacy), `/api/observations`.
New: `POST /api/command`, `/api/task/{approve,reject,start,pause,resume,cancel,reset}`, `POST /api/stop`,
`POST /api/pi/rearm`, `POST /api/cosmos/probe`, `POST /api/calibration/reload`, `GET /api/logs/session`,
`POST /api/voice/listen/start|stop`.

Pi service API and protocol: `pi_service/README.md`.

## Constraints for anyone extending this

- Never add a path that moves hardware without calibrated feedback, validated limits, operator approval and the physical E-stop.
- Don't label simulated data or unverified integrations as live. Keep the three modes distinct in the UI.
- Jetson: 8 GB shared memory, no sudo, NVIDIA's system OpenCV/TensorRT, numpy<2, `.venv` with system site packages.
- Tests must never energise motors; Pi tests use the simulation backend only.
