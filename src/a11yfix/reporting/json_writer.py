"""Write a manifest JSON file for stage-4 handoff."""

from __future__ import annotations

from pathlib import Path

from a11yfix.manifest import Manifest


def write_manifest(manifest: Manifest, path: Path | str) -> Path:
    p = Path(path)
    manifest.write(p)
    return p
