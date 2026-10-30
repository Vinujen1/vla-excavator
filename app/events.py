"""Timestamped operator event log shared by all modules (in memory, bounded), mirrored to a
per-session JSONL file that the dashboard can download. The file stops growing at a size cap."""
import itertools
import json
import logging
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path

log = logging.getLogger("vla")


class EventLog:
    def __init__(self, maxlen=300):
        self._events = deque(maxlen=maxlen)
        self._seq = itertools.count(1)
        self._lock = threading.Lock()
        self._file: Path | None = None
        self._max_bytes = 0
        self._written = 0
        self._capped = False

    def open_session_file(self, directory: Path, max_mb: float):
        directory.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self._file = directory / datetime.now().strftime("session_%Y%m%d_%H%M%S.jsonl")
            self._max_bytes, self._written, self._capped = int(max_mb * 1024 * 1024), 0, False
        return self._file

    @property
    def session_file(self):
        return self._file

    def add(self, source, message, level="info", **data):
        event = {"seq": next(self._seq), "time": time.time(), "level": level, "source": source, "message": message}
        if data:
            event["data"] = data
        with self._lock:
            self._events.append(event)
            self._write(event)
        getattr(log, "warning" if level == "warn" else level, log.info)("[%s] %s", source, message)
        return event

    def _write(self, event):
        if self._file is None or self._capped:
            return
        line = json.dumps(event, default=str) + "\n"
        if self._written + len(line) > self._max_bytes:
            self._capped = True
            line = json.dumps({"time": time.time(), "level": "warn", "source": "system",
                               "message": "Session log size cap reached; further events are kept in memory only"}) + "\n"
        try:
            with self._file.open("a") as f:
                f.write(line)
            self._written += len(line)
        except OSError:
            self._capped = True

    def since(self, seq):
        with self._lock:
            return [e for e in self._events if e["seq"] > seq]


events = EventLog()
