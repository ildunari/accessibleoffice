"""Subprocess wrapper around the officecli binary.

All writes go through this. Reads stay in python-docx / python-pptx for speed.
Batch results are NOT atomic per officecli design — partial-success outputs
are surfaced and the wrapper supports snapshot-restore on failure.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from a11yfix.rules.base import OfficecliOp


class OfficecliError(RuntimeError):
    """Raised on officecli invocation failure (process error, parse error, etc.)."""


# Wall-clock cap for a single officecli invocation. Without it a hung
# subprocess (corrupt file, AV hold) blocks the caller forever.
OFFICECLI_TIMEOUT_SEC = 120


@dataclass
class BatchResult:
    success: bool
    per_op: list[dict[str, Any]]
    raw_stdout: str = ""
    raw_stderr: str = ""

    @property
    def failed_ops(self) -> list[dict[str, Any]]:
        return [op for op in self.per_op if not op.get("ok", False)]


@dataclass
class ValidationResult:
    status: str  # "ok" | "errors"
    errors: list[dict[str, Any]] = field(default_factory=list)


def _run(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    try:
        proc = subprocess.run(
            args, capture_output=True, text=True, timeout=OFFICECLI_TIMEOUT_SEC
        )
    except subprocess.TimeoutExpired as exc:
        raise OfficecliError(
            f"officecli timed out after {OFFICECLI_TIMEOUT_SEC}s: {' '.join(args[:3])}"
        ) from exc
    if check and proc.returncode != 0:
        raise OfficecliError(
            f"officecli exited {proc.returncode}: {proc.stderr.strip() or proc.stdout.strip()}"
        )
    return proc


class OfficecliClient:
    """Context manager that takes a backup snapshot on entry and supports restore."""

    def __init__(
        self,
        file_path: str | Path,
        *,
        binary: str = "officecli",
        backup_suffix: str = ".bak",
    ) -> None:
        self.file_path = Path(file_path).resolve()
        self.binary = binary
        # Per-stage suffix: stage 2 keeps the default ".bak" (the pristine
        # pre-pipeline copy the manifest points at); stage 3 must use a
        # distinct name so it can't stomp that original.
        self._backup_suffix = backup_suffix
        self._backup_path: Path | None = None

    # -- context manager --

    def __enter__(self) -> OfficecliClient:
        self._snapshot()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        # caller decides whether to restore via .restore_from_backup()
        return None

    # -- snapshot / restore --

    def _snapshot(self) -> None:
        backup_dir = self.file_path.parent / ".a11yfix"
        backup_dir.mkdir(exist_ok=True)
        self._backup_path = backup_dir / f"{self.file_path.name}{self._backup_suffix}"
        shutil.copy2(self.file_path, self._backup_path)

    @property
    def backup_path(self) -> Path | None:
        return self._backup_path

    def restore_from_backup(self) -> None:
        if self._backup_path is None or not self._backup_path.exists():
            raise OfficecliError("no backup available to restore")
        shutil.copy2(self._backup_path, self.file_path)

    # -- operations --

    def batch(self, ops: list[OfficecliOp]) -> BatchResult:
        if not ops:
            return BatchResult(success=True, per_op=[])
        entries = [op.to_batch_entry() for op in ops]
        with NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(entries, f)
            tmp_path = f.name
        try:
            proc = _run(
                [self.binary, "batch", str(self.file_path), "--input", tmp_path, "--json"],
                check=False,
            )
        finally:
            Path(tmp_path).unlink(missing_ok=True)

        per_op: list[dict[str, Any]] = []
        try:
            payload = json.loads(proc.stdout) if proc.stdout.strip() else {}
            if isinstance(payload, dict):
                # officecli wraps results as {"data": {"results": [...]}}; older paths
                # used a top-level "results" array. Support both.
                if "results" in payload:
                    per_op = list(payload["results"])
                elif isinstance(payload.get("data"), dict) and "results" in payload["data"]:
                    per_op = list(payload["data"]["results"])
            elif isinstance(payload, list):
                per_op = list(payload)
        except json.JSONDecodeError:
            # fall back: no parseable JSON; treat whole batch as failed
            pass

        # Normalize per-op shape: officecli emits {"success": bool, ...} but
        # older callers in this codebase look for "ok". Mirror both.
        for op in per_op:
            if "ok" not in op and "success" in op:
                op["ok"] = bool(op["success"])

        success = (
            proc.returncode == 0
            and len(per_op) == len(ops)
            and all(op.get("ok", False) for op in per_op)
        )
        return BatchResult(
            success=success,
            per_op=per_op,
            raw_stdout=proc.stdout,
            raw_stderr=proc.stderr,
        )

    def validate(self) -> ValidationResult:
        proc = _run([self.binary, "validate", str(self.file_path), "--json"], check=False)
        try:
            payload = json.loads(proc.stdout) if proc.stdout.strip() else {}
        except json.JSONDecodeError:
            return ValidationResult(status="errors", errors=[{"raw": proc.stdout}])
        if isinstance(payload, dict):
            errs = payload.get("errors") or []
            if proc.returncode != 0 and not errs:
                errs = [{"raw": proc.stderr.strip() or proc.stdout.strip()}]
            status = "ok" if not errs else "errors"
            return ValidationResult(status=status, errors=list(errs))
        if proc.returncode != 0:
            return ValidationResult(
                status="errors", errors=[{"raw": proc.stderr.strip() or proc.stdout.strip()}]
            )
        return ValidationResult(status="ok")

    def query(self, selector: str) -> list[dict[str, Any]]:
        proc = _run([self.binary, "query", str(self.file_path), selector, "--json"], check=False)
        try:
            payload = json.loads(proc.stdout) if proc.stdout.strip() else []
        except json.JSONDecodeError:
            return []
        if isinstance(payload, list):
            return list(payload)
        if isinstance(payload, dict) and "results" in payload:
            return list(payload["results"])
        return []

    def get(self, path: str) -> dict[str, Any]:
        proc = _run([self.binary, "get", str(self.file_path), path, "--json"], check=False)
        try:
            payload = json.loads(proc.stdout) if proc.stdout.strip() else {}
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}


def is_officecli_available(binary: str = "officecli") -> bool:
    return shutil.which(binary) is not None
