"""Object detection on the latest camera frame (bounded: never queues frames).

Two independent sources, both frame-specific:
  * YOLO (Ultralytics, pretrained COCO classes). It only knows its own classes and does
    not know our sandbox objects.
  * Colour-marker candidates: HSV threshold + contours. Colour alone doesn't prove
    what an object is, so these are labelled "marker candidate".
"""
import logging
import os
import threading
import time
from collections import deque

import cv2
import numpy as np
from pydantic import BaseModel, Field

from .config import settings
from .events import events
from .schemas import BBox, Detection, DetectionFrame
from .targets import estimate_depth

os.environ.setdefault("YOLO_AUTOINSTALL", "false")  # never pip-install at runtime
os.environ.setdefault("YOLO_OFFLINE", "true")       # no update checks / analytics calls


class MarkerSettings(BaseModel):
    enabled: bool = False
    name: str = Field("red", max_length=30)
    # OpenCV HSV: H 0-179, S/V 0-255. If h_min > h_max the hue range wraps (needed for red).
    h_min: int = Field(170, ge=0, le=179)
    h_max: int = Field(10, ge=0, le=179)
    s_min: int = Field(120, ge=0, le=255)
    s_max: int = Field(255, ge=0, le=255)
    v_min: int = Field(70, ge=0, le=255)
    v_max: int = Field(255, ge=0, le=255)
    min_area_px: int = Field(150, ge=10, le=100000)


class DetectionSettings(BaseModel):
    yolo_enabled: bool = settings.detect_enabled
    confidence: float = Field(settings.detect_conf, ge=0.05, le=0.95)
    marker: MarkerSettings = MarkerSettings()


class Detector:
    def __init__(self, camera, on_frame=None):
        self._camera = camera
        self._on_frame = on_frame   # called with each DetectionFrame (e.g. target association)
        self.settings = DetectionSettings()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="detector", daemon=True)
        self._model = None
        self.model_status = "not loaded"
        self.model_error = None
        self.model_info = {}
        self._latest: DetectionFrame | None = None
        self._history = deque(maxlen=30)  # recent frames, so a click on a slightly older frame resolves
        self._done_times = deque(maxlen=30)
        self._skipped = 0                 # camera frames that arrived while inference was busy

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=5)

    def latest(self):
        with self._lock:
            return self._latest

    def frame(self, frame_number):
        with self._lock:
            return next((f for f in self._history if f.frame_number == frame_number), None)

    def update_settings(self, new: DetectionSettings):
        with self._lock:
            self.settings = new
        events.add("detection", f"Settings: YOLO {'on' if new.yolo_enabled else 'off'}, conf >= {new.confidence:.2f}, "
                                f"marker mode {'on' if new.marker.enabled else 'off'}")

    def stats(self):
        with self._lock:
            times = list(self._done_times)
            latest = self._latest
        fps = (len(times) - 1) / (times[-1] - times[0]) if len(times) > 1 and time.time() - times[-1] < 2 else 0.0
        return {
            "model_status": self.model_status,
            "model_error": self.model_error,
            "model": self.model_info,
            "detection_fps": round(fps, 1),
            "inference_ms": latest.inference_ms if latest else None,
            "marker_ms": latest.marker_ms if latest else None,
            "frames_skipped_while_busy": self._skipped,
            "max_fps": settings.detect_max_fps,
            "settings": self.settings.model_dump(),
        }

    # ------------------------------------------------------------------
    def _load_model(self):
        self.model_status = "loading"
        try:
            from .gpu_libs import preload
            preload()
            import torch
            from ultralytics import YOLO
            # Ultralytics resets its logger on import; silence the per-frame 'half' deprecation warning.
            logging.getLogger("ultralytics").setLevel(logging.ERROR)

            device = settings.yolo_device if torch.cuda.is_available() else "cpu"
            model = YOLO(settings.yolo_model, task="detect")
            dummy = np.zeros((settings.camera_height, settings.camera_width, 3), np.uint8)
            model.predict(dummy, imgsz=settings.yolo_imgsz, device=device, half=settings.yolo_half and device != "cpu",
                          verbose=False)  # warm-up (CUDA init, kernel selection)
            self._model, self._device = model, device
            names = model.names
            self.model_info = {
                "file": os.path.basename(settings.yolo_model),
                "device": device,
                "gpu": torch.cuda.get_device_name(0) if device != "cpu" else None,
                "half": settings.yolo_half and device != "cpu",
                "imgsz": settings.yolo_imgsz,
                "classes": [names[i] for i in sorted(names)],
                "dataset_note": "Pretrained on COCO (80 everyday classes). It doesn't know sandbox-specific objects.",
            }
            self.model_status = "ready"
            events.add("detection", f"YOLO model {self.model_info['file']} ready on {device}")
        except Exception as exc:
            self.model_status, self.model_error = "error", str(exc)
            events.add("detection", f"YOLO model failed to load: {exc}", "error")

    def _run(self):
        if settings.detect_enabled:
            self._load_model()
        else:
            self.model_status = "disabled"
        last_seq = 0
        min_period = 1.0 / settings.detect_max_fps
        while not self._stop.is_set():
            frame = self._camera.wait_for_new(last_seq, timeout=0.5)
            if frame is None:
                continue
            if last_seq and frame["seq"] > last_seq + 1:
                self._skipped += frame["seq"] - last_seq - 1
            last_seq = frame["seq"]
            started = time.perf_counter()
            try:
                result = self._detect(frame)
            except Exception as exc:
                events.add("detection", f"Detection error: {exc}", "error")
                self._stop.wait(1)
                continue
            with self._lock:
                self._latest = result
                self._history.append(result)
                self._done_times.append(time.time())
            if self._on_frame:
                self._on_frame(result)
            # Cap the rate to leave GPU/CPU headroom on the 8 GB Orin; frames in between are dropped.
            self._stop.wait(max(0.0, min_period - (time.perf_counter() - started)))

    def _detect(self, frame) -> DetectionFrame:
        cfg = self.settings
        intr = self._camera.rs_intrinsics
        dets, inference_ms, marker_ms = [], None, None

        if cfg.yolo_enabled and self._model is not None:
            t0 = time.perf_counter()
            res = self._model.predict(frame["color"], imgsz=settings.yolo_imgsz, conf=cfg.confidence,
                                      device=self._device, half=self.model_info["half"], verbose=False)[0]
            inference_ms = round((time.perf_counter() - t0) * 1000, 1)
            boxes = res.boxes
            for i, (xyxy, conf, cls) in enumerate(zip(boxes.xyxy.cpu().numpy(), boxes.conf.cpu().numpy(),
                                                      boxes.cls.cpu().numpy().astype(int))):
                bbox = BBox(x1=int(xyxy[0]), y1=int(xyxy[1]), x2=int(xyxy[2]), y2=int(xyxy[3]))
                dets.append(Detection(id=f"y{i}", source="yolo", label=res.names[cls], class_id=int(cls),
                                      confidence=round(float(conf), 3), bbox=bbox,
                                      depth=estimate_depth(frame["depth"], frame["depth_scale"], bbox, intr)))

        if cfg.marker.enabled:
            t0 = time.perf_counter()
            for i, bbox in enumerate(find_markers(frame["color"], cfg.marker)):
                dets.append(Detection(id=f"m{i}", source="color_marker", label=f"{cfg.marker.name} marker candidate",
                                      bbox=bbox, depth=estimate_depth(frame["depth"], frame["depth_scale"], bbox, intr)))
            marker_ms = round((time.perf_counter() - t0) * 1000, 1)

        return DetectionFrame(frame_number=frame["seq"], frame_time=frame["host_time"],
                              inference_ms=inference_ms, marker_ms=marker_ms, detections=dets)


def find_markers(bgr, m: MarkerSettings, max_markers=10):
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    lo_sv, hi_sv = (m.s_min, m.v_min), (m.s_max, m.v_max)
    if m.h_min <= m.h_max:
        mask = cv2.inRange(hsv, (m.h_min, *lo_sv), (m.h_max, *hi_sv))
    else:  # wrap-around hue range, e.g. red 170..10
        mask = cv2.inRange(hsv, (m.h_min, *lo_sv), (179, *hi_sv)) | cv2.inRange(hsv, (0, *lo_sv), (m.h_max, *hi_sv))
    kernel = np.ones((5, 5), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    contours = sorted((c for c in contours if cv2.contourArea(c) >= m.min_area_px), key=cv2.contourArea, reverse=True)
    out = []
    for c in contours[:max_markers]:
        x, y, w, h = cv2.boundingRect(c)
        out.append(BBox(x1=x, y1=y, x2=x + w, y2=y + h))
    return out
