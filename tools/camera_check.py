"""Camera readiness check for VLA use: frame rate, drops, latency, depth quality, intrinsics.

Run with the web server stopped (only one process can own the camera):
    .venv/bin/python tools/camera_check.py [seconds_per_profile]
Keep the camera still and the scene static for the depth-noise numbers to mean anything.
"""
import json
import sys
import time

import numpy as np
import pyrealsense2 as rs

SECONDS = float(sys.argv[1]) if len(sys.argv) > 1 else 10
# (colour w, h, depth w, h, fps) combinations that the USB2 link offers for both streams.
PROFILES = [(640, 480, 640, 480, 15), (640, 480, 640, 480, 30), (424, 240, 480, 270, 30)]


def run(cw, ch, dw, dh, fps):
    pipe, cfg = rs.pipeline(), rs.config()
    cfg.enable_stream(rs.stream.color, cw, ch, rs.format.bgr8, fps)
    cfg.enable_stream(rs.stream.depth, dw, dh, rs.format.z16, fps)
    prof = pipe.start(cfg)
    scale = prof.get_device().first_depth_sensor().get_depth_scale()
    align = rs.align(rs.stream.color)
    for _ in range(fps):  # let auto-exposure settle
        pipe.wait_for_frames(5000)

    nums, latencies, align_ms, valid, depths = [], [], [], [], []
    start = time.time()
    while time.time() - start < SECONDS:
        fs = pipe.wait_for_frames(5000)
        now_ms = time.time() * 1000
        c = fs.get_color_frame()
        if c.get_frame_timestamp_domain() == rs.timestamp_domain.global_time:
            latencies.append(now_ms - c.get_timestamp())
        nums.append(c.get_frame_number())
        t0 = time.perf_counter()
        fs = align.process(fs)
        align_ms.append((time.perf_counter() - t0) * 1000)
        d = np.asanyarray(fs.get_depth_frame().get_data())
        valid.append((d > 0).mean())
        if len(depths) < 30:
            depths.append(d.astype(np.float32) * scale)
    elapsed = time.time() - start
    intr = prof.get_stream(rs.stream.color).as_video_stream_profile().get_intrinsics()
    pipe.stop()

    gaps = np.diff(nums)
    stack = np.stack(depths)
    always_valid = (stack > 0).all(axis=0)
    noise_mm = float(np.median(stack[:, always_valid].std(axis=0)) * 1000) if always_valid.any() else None
    return {
        "profile": f"colour {cw}x{ch} + depth {dw}x{dh} @ {fps}",
        "delivered_fps": round(len(nums) / elapsed, 1),
        "dropped_frames": int((gaps[gaps > 1] - 1).sum()),
        "frames": len(nums),
        "latency_ms_median": round(float(np.median(latencies)), 1) if latencies else None,
        "latency_ms_p95": round(float(np.percentile(latencies, 95)), 1) if latencies else None,
        "align_ms_median": round(float(np.median(align_ms)), 1),
        "depth_valid_fraction": round(float(np.mean(valid)), 3),
        "depth_temporal_noise_mm_median": round(noise_mm, 1) if noise_mm is not None else None,
        "depth_median_range_m": round(float(np.median(stack[:, always_valid])), 2) if always_valid.any() else None,
        "color_intrinsics": {"fx": round(intr.fx, 2), "fy": round(intr.fy, 2), "ppx": round(intr.ppx, 2),
                             "ppy": round(intr.ppy, 2), "model": str(intr.model), "coeffs": [round(x, 4) for x in intr.coeffs]},
    }


def main():
    dev = rs.context().query_devices()[0]
    print(json.dumps({
        "device": dev.get_info(rs.camera_info.name),
        "firmware": dev.get_info(rs.camera_info.firmware_version),
        "usb": dev.get_info(rs.camera_info.usb_type_descriptor),
        "sensors": [s.get_info(rs.camera_info.name) for s in dev.query_sensors()],
    }, indent=1))
    for p in PROFILES:
        try:
            print(json.dumps(run(*p), indent=1))
        except Exception as exc:
            print(json.dumps({"profile": str(p), "error": str(exc)}))


if __name__ == "__main__":
    main()
