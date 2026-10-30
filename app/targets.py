"""Target depth estimation and operator target selection.

Depth: median of valid aligned-depth pixels in the central part of the box, with an
interquartile spread to flag regions that mix target and background/occluders.

Selection: detections are frame-specific. A selected target is carried forward only
by matching it to the next detection frame (same label, IoU >= 0.3). This is a simple
association, not a tracker; when no match arrives in time the target is marked stale.
"""
import threading
import time

import numpy as np
import pyrealsense2 as rs

from .config import settings
from .events import events
from .schemas import BBox, DepthEstimate, Detection, DetectionFrame, Target

ROI_FRACTION = 0.4        # central 40% of the box (edges are usually background)
MIN_ROI_HALF = 3          # px; at least a 7x7 region
MIN_VALID_FRACTION = 0.25
IOU_MATCH = 0.3


def estimate_depth(depth, scale, bbox: BBox, intrinsics=None) -> DepthEstimate:
    if depth is None:
        return DepthEstimate(status="unavailable", reason="no depth frame")
    h, w = depth.shape
    cx, cy = bbox.centre
    hw = max(MIN_ROI_HALF, (bbox.x2 - bbox.x1) * ROI_FRACTION / 2)
    hh = max(MIN_ROI_HALF, (bbox.y2 - bbox.y1) * ROI_FRACTION / 2)
    roi = BBox(x1=int(max(0, cx - hw)), y1=int(max(0, cy - hh)),
               x2=int(min(w - 1, cx + hw)), y2=int(min(h - 1, cy + hh)))
    patch = depth[roi.y1:roi.y2 + 1, roi.x1:roi.x2 + 1].astype(np.float32) * scale
    in_range = patch[(patch >= settings.depth_min_m) & (patch <= settings.depth_max_m)]
    frac = in_range.size / max(1, patch.size)
    if frac < MIN_VALID_FRACTION:
        return DepthEstimate(status="no_valid_depth", valid_fraction=round(frac, 3), roi=roi,
                             reason=f"only {frac:.0%} of ROI pixels have depth in "
                                    f"{settings.depth_min_m}-{settings.depth_max_m} m")
    q1, med, q3 = np.percentile(in_range, [25, 50, 75])
    spread = float(q3 - q1)
    xyz = None
    if intrinsics is not None:
        # Deproject the ROI centre at the median depth (camera frame: x right, y down, z forward).
        xyz = tuple(round(v, 3) for v in rs.rs2_deproject_pixel_to_point(intrinsics, [cx, cy], float(med)))
    inconsistent = spread > max(0.10, 0.1 * med)
    return DepthEstimate(
        status="inconsistent" if inconsistent else "ok",
        depth_m=round(float(med), 3), spread_m=round(spread, 3), valid_fraction=round(frac, 3), roi=roi,
        xyz_camera_m=xyz,
        reason="ROI mixes surfaces at different depths (background or occlusion likely)" if inconsistent else None,
    )


def iou(a: BBox, b: BBox):
    ix = max(0, min(a.x2, b.x2) - max(a.x1, b.x1))
    iy = max(0, min(a.y2, b.y2) - max(a.y1, b.y1))
    inter = ix * iy
    union = (a.x2 - a.x1) * (a.y2 - a.y1) + (b.x2 - b.x1) * (b.y2 - b.y1) - inter
    return inter / union if union > 0 else 0.0


class TargetSelector:
    def __init__(self, camera):
        self._camera = camera
        self._lock = threading.Lock()
        self._target: Target | None = None
        self._was_stale = False

    def select(self, frame: DetectionFrame, detection_id: str) -> Target:
        det = next((d for d in frame.detections if d.id == detection_id), None)
        if det is None:
            raise KeyError(detection_id)
        now = time.time()
        with self._lock:
            self._target = Target(detection=det, selected_at=now, first_frame_number=frame.frame_number,
                                  last_seen_frame_number=frame.frame_number, last_seen_time=frame.frame_time)
            self._was_stale = False
        events.add("target", f"Selected {det.label} ({det.id}) at frame {frame.frame_number}"
                             f"{'' if det.depth.depth_m is None else f', {det.depth.depth_m:.2f} m'}")
        return self.state()

    def clear(self):
        with self._lock:
            had = self._target is not None
            self._target = None
        if had:
            events.add("target", "Target cleared")

    def update(self, frame: DetectionFrame):
        """Carry the selection forward to a new detection frame if it matches."""
        with self._lock:
            t = self._target
            if t is None:
                return
            candidates = [d for d in frame.detections if d.label == t.detection.label and d.source == t.detection.source]
            best = max(candidates, key=lambda d: iou(d.bbox, t.detection.bbox), default=None)
            if best is not None and iou(best.bbox, t.detection.bbox) >= IOU_MATCH:
                t.detection = best
                t.last_seen_frame_number = frame.frame_number
                t.last_seen_time = frame.frame_time

    def state(self) -> Target | None:
        with self._lock:
            t = self._target
            if t is None:
                return None
            t = t.model_copy(deep=True)
        now = time.time()
        t.age_s = round(now - t.last_seen_time, 2)
        if self._camera.status != "connected":
            t.stale, t.stale_reason = True, f"camera {self._camera.status}"
        elif t.age_s > settings.target_stale_s:
            t.stale, t.stale_reason = True, f"not matched in a detection frame for {t.age_s:.1f} s"
        if t.stale != self._was_stale:
            self._was_stale = t.stale
            if t.stale:
                events.add("target", f"Target {t.detection.label} is stale: {t.stale_reason}", "warn")
            else:
                events.add("target", f"Target {t.detection.label} re-acquired")
        return t
