"""Shared plumbing for adapters that shell out to an agent CLI (pi/opencode/codex)."""

from __future__ import annotations

import contextlib
import json
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path

from a11yfix.ai.errors import AdapterCallError, AdapterUnavailable

CLI_TIMEOUT = 120  # match the officecli subprocess guard


def require_binary(binary: str, *, hint: str) -> str:
    path = shutil.which(binary)
    if path is None:
        raise AdapterUnavailable(f"'{binary}' not found on PATH — install it: {hint}")
    return path


def run_cli(cmd: list[str], *, timeout: int = CLI_TIMEOUT) -> str:
    """Run an agent CLI; return stdout. AdapterCallError on failure/timeout."""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise AdapterCallError(f"{cmd[0]}: {exc}") from exc
    if proc.returncode != 0:
        raise AdapterCallError(
            f"{cmd[0]} exited {proc.returncode}: {(proc.stderr or proc.stdout)[:400]}"
        )
    return proc.stdout


def jsonl_events(stdout: str) -> Iterator[dict]:
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        with contextlib.suppress(json.JSONDecodeError):
            ev = json.loads(line)
            if isinstance(ev, dict):
                yield ev


@contextlib.contextmanager
def temp_image(image_bytes: bytes) -> Iterator[Path]:
    """Write vision-compatible bytes to a temp file the CLI can read."""
    from a11yfix.ooxml.image_extract import ensure_vision_compatible

    send_bytes, media_type = ensure_vision_compatible(image_bytes)
    suffix = {"image/png": ".png", "image/jpeg": ".jpg",
              "image/gif": ".gif", "image/webp": ".webp"}.get(media_type, ".png")
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        f.write(send_bytes)
        path = Path(f.name)
    try:
        yield path
    finally:
        path.unlink(missing_ok=True)
