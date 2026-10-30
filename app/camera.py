"""RealSense D435i capture thread: colour + depth aligned to colour, with reconnect.

Acquisition runs in its own thread regardless of how many browsers are watching.
Consumers (video stream, detector, observations) only read the latest frame.
"""
import fcntl
import os
import threading
import time
from collections import deque

import cv2
import numpy as np
import pyrealsense2 as rs

from .config import settings
from .events import events

CENTRE_WINDOW = 5  # half-size of the centre patch used for depth (11x11 px)
STALL_S = 1.0      # no frames for this long -> status "stalled"
RESTART_S = 3.0    # ...and for this long -> tear down and reopen the pipeline


class CameraBusyError(RuntimeError):
    pass


class RealSenseCamera:
    def __init__(self, width=settings.camera_width, height=settings.camera_height, fps=settings.camera_fps):
        self.width, self.height, self.fps = width, height, fps
        self._lock = threading.Lock()
        self._new_frame = threading.Condition(self._lock)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="camera", daemon=True)
        self._lock_file = None
        self.status = "starting"
        self.error = None
        self.device_info = {}
        self.intrinsics = None       # dict, JSON-friendly
        self.rs_intrinsics = None    # pyrealsense2 object, for deprojection
        self._latest = None
        self._seq = 0                # increases across reconnects (RealSense frame numbers reset)
        self._frame_times = deque(maxlen=60)
        self._jpeg_cache = {}        # view -> (seq, bytes); shared by all stream clients
        self._jpeg_lock = threading.Lock()

    # ---- lifecycle ----------------------------------------------------
    def start(self):
        self._acquire_process_lock()
        self._thread.start()

    def stop(self):
        self._stop.set()
        with self._new_frame:
            self._new_frame.notify_all()
        self._thread.join(timeout=5)
        if self._lock_file:
            self._lock_file.close()

    def _acquire_process_lock(self):
        """Only one process may own the camera; a second server fails clearly instead of fighting."""
        path = settings.data_dir / "camera.lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        f = open(path, "a+")
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            f.seek(0)
            owner = f.read().strip() or "unknown"
            f.close()
            raise CameraBusyError(f"Camera is owned by another process (pid {owner}). Stop it first.")
        f.seek(0)
        f.truncate()
        f.write(str(os.getpid()))
        f.flush()
        self._lock_file = f

    # ---- readers ------------------------------------------------------
    def latest(self):
        with self._lock:
            return self._latest

    def wait_for_new(self, last_seq, timeout=1.0):
        """Block until a frame newer than last_seq exists (or timeout); returns it or None."""
        with self._new_frame:
            if self._latest is None or self._latest["seq"] <= last_seq:
                self._new_frame.wait(timeout)
            frame = self._latest
        return frame if frame is not None and frame["seq"] > last_seq else None

    def jpeg(self, view="color"):
        """JPEG of the latest frame, encoded at most once per frame per view."""
        frame = self.latest()
        if frame is None:
            return None, None
        with self._jpeg_lock:
            cached = self._jpeg_cache.get(view)
            if cached and cached[0] == frame["seq"]:
                return cached
            image = frame["color"] if view == "color" else colourise_depth(frame["depth"], frame["depth_scale"])
            if image is None:
                return None, None
            ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, settings.jpeg_quality])
            if not ok:
                return None, None
            self._jpeg_cache[view] = (frame["seq"], buf.tobytes())
            return self._jpeg_cache[view]

    def measured_fps(self):
        with self._lock:
            times = list(self._frame_times)
        if len(times) < 2 or time.time() - times[-1] > 1.0:
            return 0.0
        return round((len(times) - 1) / (times[-1] - times[0]), 1)

    def state(self):
        frame = self.latest()
        out = {
            "status": self.status,
            "error": self.error,
            "device": self.device_info,
            "configured": {"width": self.width, "height": self.height, "fps": self.fps},
            "measured_fps": self.measured_fps(),
            "intrinsics": self.intrinsics,
        }
        if frame is not None:
            out.update(
                seq=frame["seq"],
                frame_number=frame["frame_number"],
                frame_time_unix=frame["host_time"],
                frame_age_s=round(time.time() - frame["host_time"], 3),
                centre_depth_m=frame["centre_depth_m"],
            )
        return out

    # ---- acquisition --------------------------------------------------
    def _run(self):
        was_connected = False
        while not self._stop.is_set():
            pipeline = rs.pipeline()
            try:
                devices = rs.context().query_devices()
                if len(devices) == 0:
                    self._set_status("disconnected", "No RealSense device found")
                    self._stop.wait(2)
                    continue
                dev = devices[0]
                self.device_info = {
                    "name": dev.get_info(rs.camera_info.name),
                    "serial": dev.get_info(rs.camera_info.serial_number),
                    "firmware": dev.get_info(rs.camera_info.firmware_version),
                    "usb": dev.get_info(rs.camera_info.usb_type_descriptor),
                }
                cfg = rs.config()
                cfg.enable_device(self.device_info["serial"])
                cfg.enable_stream(rs.stream.color, self.width, self.height, rs.format.bgr8, self.fps)
                cfg.enable_stream(rs.stream.depth, self.width, self.height, rs.format.z16, self.fps)
                profile = pipeline.start(cfg)
                scale = profile.get_device().first_depth_sensor().get_depth_scale()
                intr = profile.get_stream(rs.stream.color).as_video_stream_profile().get_intrinsics()
                self.rs_intrinsics = intr
                self.intrinsics = {  # depth is aligned to colour, so these apply to both
                    "width": intr.width, "height": intr.height, "fx": intr.fx, "fy": intr.fy,
                    "ppx": intr.ppx, "ppy": intr.ppy, "model": str(intr.model), "coeffs": list(intr.coeffs),
                    "depth_scale_m_per_unit": scale,
                }
                align = rs.align(rs.stream.color)
                self._set_status("connected", None)
                events.add("camera", f"Connected: {self.device_info['name']} (USB {self.device_info['usb']}), "
                                     f"{self.width}x{self.height} @ {self.fps} fps")
                was_connected = True
                self._stream(pipeline, align, scale)
            except Exception as exc:  # device unplugged, busy, timeout...
                self._set_status("disconnected", str(exc))
                if was_connected:
                    events.add("camera", f"Disconnected: {exc}. Retrying every 2 s", "error")
                    was_connected = False
            finally:
                try:
                    pipeline.stop()
                except Exception:
                    pass
                with self._lock:
                    self._latest = None
                    self._frame_times.clear()
            self._stop.wait(2)

    def _stream(self, pipeline, align, scale):
        last_frame = time.time()
        while not self._stop.is_set():
            ok, frames = pipeline.try_wait_for_frames(500)
            if not ok:
                # librealsense may recover a USB reset internally without raising, so watch for gaps.
                gap = time.time() - last_frame
                if gap > STALL_S and self.status == "connected":
                    self._set_status("stalled", f"No frames for {gap:.1f} s")
                    events.add("camera", f"Stalled: no frames for {gap:.1f} s", "warn")
                if gap > RESTART_S:
                    raise RuntimeError(f"no frames for {gap:.1f} s; restarting pipeline")
                continue
            host_time = last_frame = time.time()
            if self.status == "stalled":
                self._set_status("connected", None)
                events.add("camera", "Frames resumed")
            frames = align.process(frames)
            color_f = frames.get_color_frame()
            depth_f = frames.get_depth_frame()
            if not color_f:
                continue
            color = np.asanyarray(color_f.get_data()).copy()
            depth = np.asanyarray(depth_f.get_data()).copy() if depth_f else None
            with self._new_frame:
                self._seq += 1
                self._frame_times.append(host_time)
                self._latest = {
                    "seq": self._seq,
                    "color": color,
                    "depth": depth,  # uint16, aligned to colour; 0 = invalid
                    "depth_scale": scale,
                    "centre_depth_m": centre_depth(depth, scale),
                    "host_time": host_time,
                    "device_timestamp_ms": color_f.get_timestamp(),
                    "timestamp_domain": str(color_f.get_frame_timestamp_domain()),
                    "frame_number": color_f.get_frame_number(),
                }
                self._new_frame.notify_all()

    def _set_status(self, status, error):
        self.status, self.error = status, error


def centre_depth(depth, scale):
    """Median of valid (non-zero) depth in a small centre patch, in metres, or None."""
    if depth is None:
        return None
    h, w = depth.shape
    cy, cx, r = h // 2, w // 2, CENTRE_WINDOW
    patch = depth[cy - r:cy + r + 1, cx - r:cx + r + 1]
    valid = patch[patch > 0]
    if valid.size < patch.size * 0.2:  # too few valid pixels to trust
        return None
    return round(float(np.median(valid)) * scale, 3)


def colourise_depth(depth, scale, max_m=settings.depth_max_m):
    """Depth as a colour image for display only; invalid pixels are black."""
    if depth is None:
        return None
    metres = depth.astype(np.float32) * scale
    norm = np.clip(metres / max_m * 255, 0, 255).astype(np.uint8)
    img = cv2.applyColorMap(255 - norm, cv2.COLORMAP_TURBO)  # near = red, far = blue
    img[depth == 0] = 0
    return img
