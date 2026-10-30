"""Keep saved observations, recordings and session logs within fixed counts (newest kept)."""
import shutil
from pathlib import Path


def prune(directory: Path, keep: int, pattern="*"):
    """Delete the oldest entries matching `pattern` beyond `keep`. Returns how many were removed."""
    if not directory.exists():
        return 0
    entries = sorted(directory.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    removed = 0
    for p in entries[keep:]:
        try:
            shutil.rmtree(p) if p.is_dir() else p.unlink()
            removed += 1
        except OSError:
            pass
    return removed
