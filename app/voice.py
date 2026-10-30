"""Voice command service: Jetson mic (push-to-talk or bounded listening mode) or laptop-browser
upload, then Whisper.

The source is always recorded with the result; one is never substituted for the other.
Each finished transcript is handed to `on_transcript` (the command pipeline), which runs only
motion-reducing commands automatically and leaves everything else for operator confirmation.
"""
import threading
import time
from datetime import datetime
from pathlib import Path

from . import audio, retention, stt
from .config import settings
from .events import events

RECORDINGS = settings.data_dir / "recordings"


class VoiceService:
    def __init__(self, on_transcript=None):
        self._recorder = audio.PushToRecord(settings.record_max_s)
        self._lock = threading.Lock()
        self.on_transcript = on_transcript
        self.state = "idle"          # idle | recording | listening | transcribing | error
        self.source = None           # jetson_mic | browser_mic
        self.source_label = None
        self.error = None
        self.recording = None        # stats of the last recording
        self.transcript = None       # last transcription result
        self._listen_stop = threading.Event()
        self.listening = None        # {"device", "started", "max_s", "chunks"} while listening

    def snapshot(self):
        return {
            "state": self.state, "source": self.source, "source_label": self.source_label,
            "error": self.error, "recording": self.recording, "transcript": self.transcript,
            "recording_elapsed_s": round(time.time() - self._recorder.started, 1)
            if self.state == "recording" and self._recorder.started else None,
            "listening": None if not self.listening else {
                **self.listening, "elapsed_s": round(time.time() - self.listening["started"], 1)},
            "max_record_s": settings.record_max_s, "whisper_model": stt.MODEL_NAME,
        }

    def _device(self, device_id):
        dev = next((d for d in audio.list_devices() if d["id"] == device_id), None)
        if dev is None:
            raise ValueError(f"Audio device not found on the Jetson: {device_id}")
        return dev

    def start_jetson(self, device_id):
        dev = self._device(device_id)
        with self._lock:
            if self.state in ("recording", "transcribing", "listening"):
                raise RuntimeError(f"Busy: {self.state}")
            path = RECORDINGS / datetime.now().strftime("jetson_%Y%m%d_%H%M%S.wav")
            try:
                self._recorder.start(device_id, path)
            except Exception as exc:
                self._fail(f"Could not start Jetson recording: {exc}")
                raise
            self.state, self.error, self.transcript = "recording", None, None
            self.source, self.source_label = "jetson_mic", f"Jetson input: {dev['label']}"
        events.add("voice", f"Recording started on {self.source_label}"
                            + ("" if dev["is_dji"] else " (not a DJI receiver)"))

    def stop_jetson(self):
        with self._lock:
            if self.state != "recording":
                raise RuntimeError("Not recording")
            try:
                self.recording = self._recorder.stop()
            except Exception as exc:
                self._fail(f"Recording failed: {exc}")
                raise
            self.recording["source"] = "jetson_mic"
            self.state = "transcribing"
        events.add("voice", f"Recorded {self.recording['duration_s']} s, level {self.recording['rms_dbfs']} dBFS")
        self._transcribe_async(RECORDINGS / self.recording["file"])

    def accept_browser_audio(self, data: bytes, content_type: str):
        """Audio recorded by the laptop browser and uploaded through the tunnel."""
        if not data:
            raise ValueError("Empty upload")
        with self._lock:
            if self.state in ("recording", "transcribing", "listening"):
                raise RuntimeError(f"Busy: {self.state}")
            ext = ".ogg" if "ogg" in content_type else ".wav" if "wav" in content_type else ".webm"
            path = RECORDINGS / datetime.now().strftime(f"browser_%Y%m%d_%H%M%S{ext}")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            now = time.time()
            self.recording = {"file": path.name, "device": "laptop browser", "source": "browser_mic",
                              "start_time_unix": None, "end_time_unix": now, "bytes": len(data)}
            self.source, self.source_label = "browser_mic", "Laptop browser microphone (uploaded to Jetson)"
            self.state, self.error, self.transcript = "transcribing", None, None
        events.add("voice", f"Received {len(data) // 1024} KB of audio from the laptop browser")
        self._transcribe_async(path)

    # ---- listening mode (bounded) ----------------------------------------
    def start_listening(self, device_id, max_s=None):
        """Record fixed-length chunks on the Jetson and transcribe each, for at most max_s seconds.
        Chunk audio is deleted after transcription; only transcripts are kept."""
        dev = self._device(device_id)
        max_s = min(max_s or settings.listen_max_s, settings.listen_max_s)
        with self._lock:
            if self.state != "idle" and self.state != "error":
                raise RuntimeError(f"Busy: {self.state}")
            self.state, self.error = "listening", None
            self.source, self.source_label = "jetson_mic", f"Jetson input: {dev['label']} (listening mode)"
            self.listening = {"device": dev["label"], "started": time.time(), "max_s": max_s, "chunks": 0}
            self._listen_stop.clear()
        events.add("voice", f"Listening mode on {dev['label']} for up to {max_s} s"
                            + ("" if dev["is_dji"] else " (not a DJI receiver)"), "warn")
        threading.Thread(target=self._listen_loop, args=(device_id, max_s), name="listening", daemon=True).start()

    def stop_listening(self):
        self._listen_stop.set()

    def _listen_loop(self, device_id, max_s):
        end = time.time() + max_s
        reason = "time limit reached"
        try:
            while not self._listen_stop.is_set():
                if time.time() >= end:
                    break
                path = RECORDINGS / datetime.now().strftime("listen_%Y%m%d_%H%M%S_%f.wav")
                chunk = min(settings.listen_chunk_s, max(1, int(end - time.time())))
                audio.record(device_id, chunk, path)
                self.listening["chunks"] += 1
                try:
                    result = stt.transcribe(path)
                finally:
                    path.unlink(missing_ok=True)
                if result["text"]:
                    self._deliver(result, None)
            else:
                reason = "stopped by operator"
        except Exception as exc:
            reason = f"error: {exc}"
            self._fail(f"Listening stopped: {exc}")
        with self._lock:
            self.listening = None
            if self.state == "listening":
                self.state = "idle"
        events.add("voice", f"Listening mode ended ({reason})")

    # ---- transcription -------------------------------------------------
    def _transcribe_async(self, path: Path):
        def work():
            try:
                result = stt.transcribe(path)
            except Exception as exc:
                self._fail(f"Transcription failed: {exc}")
                return
            with self._lock:
                if self.recording and self.recording.get("duration_s") is None:
                    self.recording["duration_s"] = result["audio_duration_s"]
                self.state = "idle"
            self._deliver(result, path.name)
            retention.prune(RECORDINGS, settings.keep_recordings, "*.*")
        threading.Thread(target=work, name="whisper", daemon=True).start()

    def _deliver(self, result, recording_name):
        result.update(source=self.source, source_label=self.source_label, recording=recording_name)
        self.transcript = result
        if result["text"]:
            events.add("voice", f"Transcript ({result['processing_s']} s"
                                + (", LOW CONFIDENCE" if result.get("low_confidence") else "")
                                + f"): \"{result['text']}\"", "warn" if result.get("low_confidence") else "info")
        else:
            events.add("voice", "Transcript empty: no speech detected", "warn")
        if self.on_transcript and result["text"]:
            try:
                result["dispatch"] = self.on_transcript(result)
            except Exception as exc:
                result["dispatch"] = {"handled": False, "message": f"Command pipeline error: {exc}"}

    def _fail(self, message):
        self.state, self.error = "error", message
        events.add("voice", message, "error")
