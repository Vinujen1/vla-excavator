# Supervisor demo script (about 8 minutes)

> **Updated milestone:** sections 6–7 below describe the earlier preview-only console. The task
> preview panel is now the *Autonomous task* panel (plan → approve & arm → start, in **Simulation**
> mode with `scripts/start_sim_pi.sh`), and there is a real software stop that reports the Pi's
> acknowledgement. Hardware execution is still blocked; see README and docs/COMMISSIONING.md.

**Before the meeting:** start the server (README "Quick start"), forward port 8000, and
open http://localhost:8000. Put a few everyday objects in view (cup, bottle, phone,
chair, or a person) plus something clearly red. Keep the scene static.

### 1. Framing (30 s)
> "This milestone is perception, operator interaction and task previews. Nothing here can
> move the excavator. The header says so, and the Execute button is disabled with the
> reasons listed."

Point at the header chips: backend, camera, detector, Pi *not connected*, mode.

### 2. Live camera (1 min)
- Show the measured **camera fps (~30)**, **frame age (tens of ms)**, **detection fps (~15)**
  and **inference ms**. These are measured live, not fixed numbers.
- Toggle **Depth**: near is red, far is blue, black is no depth. Toggle back to **Colour**.
- Mention the camera link warning (USB 2.1) as a known hardware item.

### 3. Detection (1.5 min)
- Point at the boxes with labels, confidence and per-object depth. Hover a row in
  **Scene objects** to highlight its box.
- Move the **confidence slider** up and down and watch boxes appear and disappear.
- Click **Show the model's 80 supported classes**:
  > "This is a COCO-pretrained YOLO11n. It doesn't know our sandbox objects, so we don't
  > claim it does."
- Turn on **Colour-marker mode**. Red regions appear as **"red marker candidate"**:
  > "This is a colour threshold, labelled as a candidate. Colour alone doesn't prove it's a cone."

### 4. Target selection and depth (1.5 min)
- Click a box (or a row). It gets the white **TARGET** outline and a dashed centre ROI.
- In **Selected target**, show depth (median), spread, valid %, and **XYZ (camera frame)**:
  > "Depth is the median of valid aligned-depth pixels in the centre of the box.
  > A big spread means background or occlusion is mixed in. XYZ is camera-frame only;
  > there's no camera-to-excavator calibration yet, so no reachability claims."
- **Staleness:** cover the object with your hand, or remove it. Within ~1.5 s the target turns
  **amber / dashed / Stale**, and the event log records why. Put it back: it re-acquires.

### 5. Command (1 min)
- **Type** tab: the example *"Scoop beside the red marker."* is pre-filled and editable.
- **Laptop mic** tab: hold up the note that this records on the laptop and uploads.
  Record a short command, then show the transcript, its source label, timestamp and playback.
- **Jetson mic** tab: show the amber warning that **no DJI receiver is detected** (until it's connected).

### 6. Task preview (1.5 min)
- Provider list: **Cosmos reasoning service (not connected)**, showing *"Reasoning model
  not connected"*.
- Choose **Demo planner (rule-based, not Cosmos or an LLM)** → **Generate preview**.
- Walk the steps: *Select target → Approach → Scoop → Lift → Rotate → Dump → Operator review*.
- Point out the **warnings**. For example, select a *cup* while the instruction says
  "red marker", and the preview flags the mismatch. Also show the assumptions (camera frame,
  dump zone undefined) and the **"Proposal only · not executable"** badge.
- Mention it's validated against a Pydantic `TaskProposal` schema that only allows known
  high-level actions and forces `executable: false`.
- Show the saved observation folder named in the event log
  (`data/observations/obs_*/` with image, aligned depth, `observation.json`, `proposal.json`).
- **Clear task preview.**

### 7. Hardware and safety (30 s)
- Pi link, sensor freshness, joint state, motion controller: all *Not connected / unavailable*.
- > "We deliberately don't show a software stop button that does nothing. The physical E-stop
  > is the stop control. A future software stop would supplement it, not replace it."

### 8. Robustness (optional, 30 s)
- Unplug and replug the camera. The view shows **Camera disconnected/stalled**, the target goes
  stale, and the feed recovers automatically within a few seconds. The event log records it.

### Next steps to mention
DJI receiver hookup · USB3 cable · TensorRT export for faster detection · custom-trained
sandbox detector · camera-to-excavator calibration · Pi telemetry API · Cosmos endpoint ·
execution checks and authenticated controller link (a later milestone).
