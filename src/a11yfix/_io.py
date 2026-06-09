"""Shared low-level IO helpers.

Lives in its own module so state-bearing writers (batch, manifest, cost
meter, resume briefs) can share the atomic-write primitive without import
cycles.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def atomic_write(path: Path, data: str) -> None:
    """Write atomically: tmp + rename. Survives crashes mid-write."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(data)
        os.replace(tmp, path)
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise
