# Excavator Pi motion service

Runs on the Raspberry Pi 5 that drives the two H-bridge motor drivers. The Jetson console talks
to it over the private Wi-Fi. It is also used **in simulation on the Jetson** (no GPIO) for
development and tests.

> Software cannot certify wiring, driver current ratings or mechanical safety. The physical
> emergency stop must cut motor power independently of the Pi, the Jetson and the network.

## What it does

| Endpoint | Auth | Purpose |
|---|---|---|
| `GET /health` | no | Liveness, backend (`simulation`/`hardware`), whether auth is on |
| `GET /state` | yes | Joints, feedback age, limits, outputs, latch, fault, heartbeat, clock offset |
| `POST /heartbeat` | yes | `{session, sent_at_ms}`: feeds the watchdog and the clock-offset estimate |
| `POST /actions` | yes | Submit one bounded action (schema below) |
| `GET /actions/{action_id}` | yes | Action status/result |
| `POST /stop` | yes | Outputs off **immediately**, then latch. Idempotent |
| `POST /rearm` | yes | Clear the latch (needs a live heartbeat from the same session) |
| `WS /ws/telemetry?token=` | yes | `/state` at 10 Hz |

Auth is `Authorization: Bearer $PI_API_TOKEN`. The hardware backend refuses to start without a
token of at least 16 characters.

### Action schema (v1)

```json
{"schema_version": 1, "action_id": "jetson-ab12cd34-00001", "kind": "move_joint_to",
 "params": {"joint": "boom", "target": 45.0, "tolerance": 1.5, "max_duty": 0.3, "timeout_ms": 6000},
 "issued_at_ms": 1793000000000, "ttl_ms": 1500, "session": "jetson-ab12cd34"}
```

* `move_joint_to`: closed-loop move of **one** joint to a target in feedback units. Needs position
  feedback and calibrated limits for that joint. Bounded by `timeout_ms` (≤ `PI_MAX_MOVE_MS`), the
  joint duty cap, travel limits and stall detection. Any failure switches outputs off and latches a fault.
* `jog`: a timed pulse (≤ `PI_MAX_JOG_MS`), **only** when `PI_COMMISSIONING=true`. Reported as
  `completed_unverified`, because a timed pulse proves nothing about position. The autonomous
  supervisor never sends it.

**Idempotency:** the same `action_id` with the same content returns the existing record
(`duplicate: true`). The same id with different content is rejected (`409 conflict`). Rejections are
recorded too, so a retry with the same id gets the same answer; use a new id to try again.

**One action at a time:** a busy controller rejects (`409 busy`). Nothing is queued.

**Expiry and clock differences:** the Pi estimates `offset = min(pi_receive_ms − sent_at_ms)` over
recent heartbeats (this includes the smallest network delay). A command's age is
`pi_now − (issued_at_ms + offset)`. It's rejected if older than `ttl_ms` (≤ `PI_MAX_TTL_MS`), if it
seems to come from more than 250 ms in the future (clock jump), or if this session hasn't sent a
heartbeat yet. Expiry bounds only how long a command waits before it starts; motion is bounded by
`timeout_ms`.

**Latching:** the service starts latched. `POST /stop`, any action failure and a lost heartbeat
(`PI_WATCHDOG_S`, default 1 s, while armed) switch every output off and latch. Only `POST /rearm`
clears it. Nothing resumes by itself after a reconnect or reboot.

## Pin mapping (proposed, NOT verified)

From `config.example.json` (BCM numbering):

| Joint | Driver / side | Enable (PWM) | IN a | IN b |
|---|---|---|---|---|
| boom | driver 1 A | GPIO17 (pin 11) | GPIO27 (pin 13) | GPIO22 (pin 15) |
| cabin rotation | driver 1 B | GPIO25 (pin 22) | GPIO23 (pin 16) | GPIO24 (pin 18) |
| bucket | driver 2 A | GPIO12 (pin 32) | GPIO5 (pin 29) | GPIO6 (pin 31) |
| stick / arm | driver 2 B | GPIO26 (pin 37) | GPIO13 (pin 33) | GPIO19 (pin 35) |

Grounds: driver 1 to physical pin 20, driver 2 to physical pin 34.

**PWM on the Pi 5:** hardware PWM is only on GPIO12/13/18/19 (RP1 PWM0). Of the four enable pins
only GPIO12 is one of them, so this service uses **lgpio software-timed PWM** on every enable pin
(500 Hz default). GPIO13 and GPIO19 are hardware-PWM capable but are wired as direction inputs here.
Check jitter on a scope during commissioning.

`config.example.json` uses `"feedback": {"kind": "simulated"}` so the simulator can run closed-loop
moves. The hardware backend **refuses** simulated feedback and `pins_verified: false`. No real
feedback driver exists yet: one has to be written for whatever sensors are actually fitted
(e.g. potentiometers through an ADC, or encoders) before any closed-loop hardware move is possible.

## Install on the Pi (no motors energised)

Raspberry Pi OS Bookworm (64-bit), Python 3.11:

```bash
sudo apt install python3-lgpio python3-venv
cd ~/excavator-vla/pi_service          # copy just this folder to the Pi
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -r requirements.txt
```

Run in **simulation** first, even on the Pi:

```bash
PI_BACKEND=simulation PI_API_TOKEN=<token> PI_HOST=0.0.0.0 PI_PORT=8100 .venv/bin/python -m excavator_pi
```

`PI_HOST=0.0.0.0` exposes the API on the Pi's network. Only do that on the private Wi-Fi between
the Jetson and the Pi, always with a token, and preferably firewall the port to the Jetson's IP:
`sudo ufw allow from <jetson-ip> to any port 8100 proto tcp`.

Environment: `PI_BACKEND` (`simulation`|`hardware`), `PI_API_TOKEN`, `PI_HARDWARE_CONFIG`,
`PI_WATCHDOG_S`, `PI_MAX_TTL_MS`, `PI_MAX_MOVE_MS`, `PI_MAX_JOG_MS`, `PI_COMMISSIONING`,
`PI_GPIOCHIP` (auto-detects the RP1 chip), `PI_SIM_SPEED`, `PI_HOST`, `PI_PORT`.

Tests (simulation only, nothing is energised): `python -m pytest -q tests`.

Hardware bring-up is a supervised procedure: see `../docs/COMMISSIONING.md`.
