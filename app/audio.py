"""Audio capture through ALSA's `arecord` (no PortAudio dependency needed)."""
import re
import signal
import subprocess
import threading
import time
import wave
from pathlib import Path

import numpy as np

SAMPLE_RATE = 16000  # what Whisper expects; plughw resamples from the device rate

_CARD_RE = re.compile(r"^card (\d+): (\S+) \[(.*?)\], device (\d+): (.*?) \[(.*?)\]")


def list_devices():
    """Jetson capture devices: real sound cards first, then the PulseAudio default."""
    try:
        out = subprocess.run(["arecord", "-l"], capture_output=True, text=True, timeout=5).stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []  # no ALSA tools: no Jetson capture devices
    devices = []
    for line in out.splitlines():
        m = _CARD_RE.match(line)
        if not m:
            continue
        num, card_id, card_name, dev, _, dev_name = m.groups()
        # The Jetson APE card exposes ~20 internal I2S/XBAR endpoints that are
        # not microphones unless the mixer is routed; hide them to keep it simple.
        if card_id == "APE":
            continue
        devices.append({
            "id": f"plughw:CARD={card_id},DEV={dev}",
            "label": f"{card_name} ({dev_name}) [card {num}, dev {dev}]",
            "is_dji": "dji" in f"{card_id} {card_name}".lower(),
        })
    devices.append({"id": "default", "label": "System default (PulseAudio)", "is_dji": False})
    return devices


def wav_stats(path: Path):
    with wave.open(str(path)) as w:
        samples = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    level = samples.astype(np.float32) / 32768.0
    return {
        "duration_s": round(len(samples) / SAMPLE_RATE, 2),
        "rms_dbfs": round(20 * np.log10(np.sqrt(np.mean(level ** 2)) + 1e-9), 1) if len(level) else None,
        "peak": round(float(np.abs(level).max()), 3) if len(level) else 0.0,
    }


def record(device, seconds, out_path: Path):
    """Record `seconds` of 16 kHz mono 16-bit audio. Returns level stats."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.time()
    proc = subprocess.run(
        ["arecord", "-q", "-D", device, "-f", "S16_LE", "-r", str(SAMPLE_RATE),
         "-c", "1", "-d", str(int(seconds)), str(out_path)],
        capture_output=True, text=True, timeout=seconds + 10,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"arecord exited {proc.returncode}")
    return {"file": out_path.name, "device": device, "start_time_unix": started,
            "end_time_unix": time.time(), **wav_stats(out_path)}


class PushToRecord:
    """Start/stop recording on the Jetson; capped at max_s so a forgotten stop can't fill the disk."""

    def __init__(self, max_s):
        self.max_s = max_s
        self._proc = None
        self._lock = threading.Lock()
        self.device = None
        self.path = None
        self.started = None

    @property
    def active(self):
        return self._proc is not None and self._proc.poll() is None

    def start(self, device, out_path: Path):
        with self._lock:
            if self.active:
                raise RuntimeError("A recording is already in progress")
            out_path.parent.mkdir(parents=True, exist_ok=True)
            self._proc = subprocess.Popen(
                ["arecord", "-q", "-D", device, "-f", "S16_LE", "-r", str(SAMPLE_RATE), "-c", "1",
                 "-d", str(self.max_s), str(out_path)],
                stderr=subprocess.PIPE, text=True)
            self.device, self.path, self.started = device, out_path, time.time()
        time.sleep(0.15)  # a bad device fails immediately; report that instead of "recording"
        if self._proc.poll() not in (None, 0):
            err = self._proc.stderr.read().strip()
            self._proc = None
            raise RuntimeError(err or "arecord failed to start")

    def stop(self):
        with self._lock:
            proc = self._proc
            if proc is None:
                raise RuntimeError("Not recording")
            if proc.poll() is None:
                proc.send_signal(signal.SIGINT)  # arecord finalises the WAV header on SIGINT
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
            self._proc = None
        if not self.path.exists() or self.path.stat().st_size <= 44:
            raise RuntimeError("No audio was captured")
        return {"file": self.path.name, "device": self.device, "start_time_unix": self.started,
                "end_time_unix": time.time(), **wav_stats(self.path)}
