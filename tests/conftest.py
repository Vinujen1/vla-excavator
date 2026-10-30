"""Test setup: keep runtime output out of the real data/ directory and make pi_service importable."""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("VLA_DATA_DIR", tempfile.mkdtemp(prefix="vla-test-"))
os.environ.setdefault("VLA_CALIBRATION_FILE", str(Path(os.environ["VLA_DATA_DIR"]) / "no-hardware-calibration.json"))
sys.path.insert(0, str(ROOT / "pi_service"))
