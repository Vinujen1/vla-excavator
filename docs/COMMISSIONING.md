# Supervised hardware commissioning

Hardware execution stays blocked until **every** item here is done and recorded. The dashboard
lists the remaining blockers by name. Do this with two people present, the physical E-stop in
reach, the machine on blocks or in a fenced sandbox, and nobody inside the swing radius.

Nothing in the automated tests or installation energises a motor.

## 0. Independent emergency stop

- [ ] A physical E-stop cuts **motor supply power** directly (not through the Pi, Jetson or Wi-Fi).
- [ ] Test it with motor power on and no software running: the drivers must lose power.

## 1. Power and wiring (motor power OFF)

- [ ] Motor power supply is separate from the Pi's supply. The Pi is never powered from the driver's 5 V regulator unless that is verified safe for this module.
- [ ] Each driver's logic ground is connected to Pi ground (driver 1 → physical pin 20, driver 2 → pin 34).
- [ ] Driver logic voltage is compatible with 3.3 V inputs. Check the module's datasheet and jumpers (e.g. the ENA/ENB jumpers on L298N-style boards must be **removed** so the Pi drives the enable pins; check the 5 V regulator jumper setting for your supply voltage).
- [ ] Driver continuous/stall current rating exceeds each motor's stall current. Fuse each motor supply.
- [ ] Every BCM pin in `pi_service/config.example.json` traced with a multimeter against the actual wiring, including physical pin numbers.

## 2. Outputs without motors

- [ ] Motors **disconnected** from the driver outputs.
- [ ] Copy `config.example.json` to `config.hardware.json`. Set `pins_verified: true` only after step 1. Set `feedback` to `null` for every joint (no feedback driver yet) and `limits` to `null`.
- [ ] Start with `PI_BACKEND=hardware PI_HARDWARE_CONFIG=config.hardware.json PI_COMMISSIONING=true`.
- [ ] Rearm, then send 100 ms `jog` actions per joint and check with a multimeter or LEDs that only the expected driver outputs change, in the expected direction.
- [ ] `POST /stop` and kill the process (`Ctrl+C`, then `kill -9`) during a jog: outputs must go low. **Note:** after `kill -9` the GPIO lines keep their last state until the kernel releases them. Confirm what your board does and rely on the physical E-stop for that case.
- [ ] Unplug Wi-Fi during a jog: the watchdog must stop and latch within `PI_WATCHDOG_S`.

## 3. Motors, polarity, duty (low duty, short pulses)

- [ ] Reconnect one motor at a time. Jog at the lowest duty that moves it, 100–200 ms.
- [ ] Record the direction for +1. Set `invert` so +1 means the agreed positive direction for each joint.
- [ ] Find a safe `max_duty` per joint and record it.

## 4. Position feedback (required for any autonomous movement)

- [ ] Fit position sensors to boom, stick, bucket and cabin (e.g. potentiometers through an ADC, or encoders).
- [ ] Write and test a feedback driver for them in `pi_service/excavator_pi/backends.py` (`read_feedback` returning `(position, monotonic_time)`), and a new `FeedbackConfig.kind` for it.
- [ ] Measure the mechanical end stops per joint and set `limits` well inside them.
- [ ] Verify `move_joint_to` per joint at low duty with tight timeouts. Check stall detection by holding a joint (carefully).

## 5. Workspace calibration

- [ ] Copy `config/calibration.hardware.template.json` to `config/calibration.json`.
- [ ] Teach each named joint target (home, carry, dig, scoop_end, curled, open, dump) by moving the arm to it and reading the feedback value.
- [ ] Measure `camera_to_base` (4×4, camera frame → excavator base frame, metres), e.g. with a checkerboard or measured fiducials. Check it by placing a marker at known base-frame positions.
- [ ] Define `dig_region` and `dump_region` as base-frame boxes the operator approves.
- [ ] Set `verified: true`, `verified_by` and `verified_at` only after a supervised dry run of each skill.

## 6. First autonomous run

- [ ] Simulation run of the same plan passes.
- [ ] Cosmos 3 endpoint verified with a real request (dashboard shows *Verified*), or explicitly allow the rule-based provider on hardware with `VLA_ALLOW_RULES_PROVIDER_ON_HARDWARE=true` for commissioning only.
- [ ] One cycle, short timeout, low duty, E-stop operator ready. Then increase gradually.
