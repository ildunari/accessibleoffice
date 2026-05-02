"""Batch state machine for folder-level orchestration.

The single-file pipeline (cli.py + stages 1-4) is unchanged. This module adds
a coordination layer on top: queue → shards → progress.jsonl → aggregate
rollup. All state lives on disk so compaction and interruption are recoverable.

Layout:
    <state_dir>/
        state.json          # canonical batch state
        queue.txt           # remaining file paths
        shards/<sid>/
            files.txt       # files this shard owns
            progress.jsonl  # one line per processed file (append-only)
            rollup.json     # filled when shard completes
            status          # queued|running|done|failed|partial
        manifests/          # symlinks to per-file <file>.manifest.json
        cost.json           # cumulative USD (file-locked)
        rollup.json         # aggregate, filled when all shards done
        RESUME.md           # human/AI-readable resume brief
"""

from __future__ import annotations

import json
import math
import os
import tempfile
import uuid
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFAULT_BATCHES_ROOT = Path.home() / ".a11yfix" / "batches"

# Files we never enqueue.
_SKIP_PREFIXES = ("~$", ".~lock")
_VALID_SUFFIXES = (".docx", ".pptx")
# Threshold (bytes) above which a file gets its own dedicated shard.
LARGE_FILE_BYTES = 50 * 1024 * 1024


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _atomic_write(path: Path, data: str) -> None:
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


@dataclass
class ShardState:
    id: str
    files: int
    status: str = "queued"  # queued|running|done|failed|partial
    completed: int = 0
    failed: int = 0
    last_updated: str = ""

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BatchTotals:
    queued: int = 0
    in_flight: int = 0
    done: int = 0
    failed: int = 0


@dataclass
class BatchState:
    batch_id: str
    state_dir: str
    started_at: str
    last_updated: str
    mode: str  # scan|auto|full-dry
    model: str
    shard_size: int
    max_concurrent: int
    shards: list[ShardState] = field(default_factory=list)
    totals: BatchTotals = field(default_factory=BatchTotals)
    rollup_path: str = "rollup.json"
    source_root: str = ""  # the original --folder argument, for context
    max_cost_total_usd: float | None = None

    # ------- IO -------

    def to_json(self) -> dict[str, Any]:
        d = asdict(self)
        d["shards"] = [s.to_json() for s in self.shards]
        return d

    def save(self) -> None:
        self.last_updated = _now()
        path = Path(self.state_dir) / "state.json"
        _atomic_write(path, json.dumps(self.to_json(), indent=2))

    @classmethod
    def load(cls, state_dir: Path | str) -> BatchState:
        p = Path(state_dir) / "state.json"
        d = json.loads(p.read_text())
        d["totals"] = BatchTotals(**d.get("totals", {}))
        d["shards"] = [ShardState(**s) for s in d.get("shards", [])]
        return cls(**d)

    # ------- helpers -------

    def shard_dir(self, shard_id: str) -> Path:
        return Path(self.state_dir) / "shards" / shard_id

    def shard(self, shard_id: str) -> ShardState | None:
        for s in self.shards:
            if s.id == shard_id:
                return s
        return None

    def status(self) -> str:
        if all(s.status == "done" for s in self.shards):
            return "done"
        if any(s.status == "running" for s in self.shards):
            return "running"
        if all(s.status in ("done", "failed", "partial") for s in self.shards):
            return "complete-with-failures"
        return "queued"

    def recompute_totals(self) -> None:
        t = BatchTotals()
        for s in self.shards:
            if s.status == "done":
                t.done += s.completed
                t.failed += s.failed
            elif s.status == "running":
                t.in_flight += s.files
            else:
                t.queued += s.files
        self.totals = t


# -----------------------------------------------------------------------------
# Queue construction
# -----------------------------------------------------------------------------


def discover_files(folder: Path | str, max_depth: int = 4) -> list[Path]:
    """Find .docx/.pptx in a folder up to max_depth, skipping temp/lock files."""
    root = Path(folder).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"not a directory: {root}")
    out: list[Path] = []
    root_depth = len(root.parts)
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if (len(p.parts) - root_depth) > max_depth:
            continue
        if p.suffix.lower() not in _VALID_SUFFIXES:
            continue
        if any(p.name.startswith(pre) for pre in _SKIP_PREFIXES):
            continue
        out.append(p.resolve())
    return sorted(out)


def dedupe_and_validate(paths: Iterable[Path | str]) -> tuple[list[Path], list[str]]:
    """Resolve symlinks, drop duplicates by inode, drop missing/temp/wrong-suffix.

    Returns (kept, skipped_reasons).
    """
    kept: list[Path] = []
    skipped: list[str] = []
    seen_inodes: set[tuple[int, int]] = set()  # (st_dev, st_ino)
    for raw in paths:
        p = Path(raw).expanduser()
        try:
            p = p.resolve(strict=True)
        except (FileNotFoundError, OSError) as exc:
            skipped.append(f"{raw}: {exc}")
            continue
        if p.suffix.lower() not in _VALID_SUFFIXES:
            skipped.append(f"{p}: unsupported suffix")
            continue
        if any(p.name.startswith(pre) for pre in _SKIP_PREFIXES):
            skipped.append(f"{p}: temp/lock file")
            continue
        try:
            st = p.stat()
        except OSError as exc:
            skipped.append(f"{p}: {exc}")
            continue
        key = (st.st_dev, st.st_ino)
        if key in seen_inodes:
            skipped.append(f"{p}: duplicate (inode already seen)")
            continue
        seen_inodes.add(key)
        kept.append(p)
    return kept, skipped


# -----------------------------------------------------------------------------
# Sharding
# -----------------------------------------------------------------------------


def partition(
    files: list[Path],
    *,
    shard_size: int = 10,
    max_shards: int = 8,
) -> list[list[Path]]:
    """Partition files into shards.

    Strategy:
      1. Files larger than LARGE_FILE_BYTES become single-file shards (so a
         long-running file doesn't drag a whole batch).
      2. Remaining small files are grouped into shards of <= shard_size,
         capped at max_shards total. If max_shards forces shard_size up,
         that's accepted (better to absorb extra than to drop files).

    Returns a list of shards. Caller assigns shard_ids.
    """
    if shard_size <= 0:
        raise ValueError("shard_size must be > 0")
    if max_shards <= 0:
        raise ValueError("max_shards must be > 0")

    big: list[Path] = []
    small: list[Path] = []
    for p in files:
        try:
            if p.stat().st_size >= LARGE_FILE_BYTES:
                big.append(p)
                continue
        except OSError:
            pass
        small.append(p)

    big_shards: list[list[Path]] = [[p] for p in big]

    # Compute small-shard count under the max-shards budget left after bigs.
    remaining_budget = max(1, max_shards - len(big_shards))
    if not small:
        small_shards: list[list[Path]] = []
    else:
        ideal = math.ceil(len(small) / shard_size)
        n = max(1, min(ideal, remaining_budget))
        # Even-out: distribute round-robin so shard sizes differ by <= 1.
        small_shards = [[] for _ in range(n)]
        for i, p in enumerate(small):
            small_shards[i % n].append(p)

    return big_shards + small_shards


# -----------------------------------------------------------------------------
# Batch creation / resume
# -----------------------------------------------------------------------------


def create_batch(
    *,
    files: list[Path],
    state_dir: Path | str | None = None,
    mode: str = "auto",
    model: str = "claude-sonnet-4-6",
    shard_size: int = 10,
    max_concurrent: int = 8,
    source_root: str = "",
    max_cost_total_usd: float | None = None,
) -> BatchState:
    """Create a fresh batch on disk and return its BatchState."""
    batch_id = uuid.uuid4().hex[:12]
    if state_dir is None:
        state_dir = DEFAULT_BATCHES_ROOT / batch_id
    state_path = Path(state_dir).expanduser().resolve()
    state_path.mkdir(parents=True, exist_ok=True)
    (state_path / "shards").mkdir(exist_ok=True)
    (state_path / "manifests").mkdir(exist_ok=True)

    # Write queue.txt for resilience (lets us reconstruct the original file
    # set even if state.json is corrupted).
    _atomic_write(
        state_path / "queue.txt",
        "\n".join(str(p) for p in files) + ("\n" if files else ""),
    )

    shards = partition(files, shard_size=shard_size, max_shards=max_concurrent)
    shard_states: list[ShardState] = []
    for i, shard_files in enumerate(shards, start=1):
        sid = f"shard-{i:03d}"
        sdir = state_path / "shards" / sid
        sdir.mkdir(parents=True, exist_ok=True)
        _atomic_write(
            sdir / "files.txt",
            "\n".join(str(p) for p in shard_files) + ("\n" if shard_files else ""),
        )
        _atomic_write(sdir / "status", "queued\n")
        # Touch progress.jsonl so workers can `tail` it without errors.
        (sdir / "progress.jsonl").touch(exist_ok=True)
        shard_states.append(
            ShardState(
                id=sid,
                files=len(shard_files),
                status="queued",
                last_updated=_now(),
            )
        )

    state = BatchState(
        batch_id=batch_id,
        state_dir=str(state_path),
        started_at=_now(),
        last_updated=_now(),
        mode=mode,
        model=model,
        shard_size=shard_size,
        max_concurrent=max_concurrent,
        shards=shard_states,
        rollup_path="rollup.json",
        source_root=source_root,
        max_cost_total_usd=max_cost_total_usd,
    )
    state.recompute_totals()
    state.save()
    return state


# -----------------------------------------------------------------------------
# Progress recording (append-only progress.jsonl)
# -----------------------------------------------------------------------------


def record_progress(
    state_dir: Path | str,
    shard_id: str,
    *,
    file: str,
    status: str,
    manifest: str | None = None,
    stage_2: int = 0,
    stage_3: int = 0,
    residual: int = 0,
    error: str | None = None,
    cost_usd: float = 0.0,
) -> None:
    """Append one progress line. Atomic via O_APPEND on POSIX.

    Each call writes a single JSON line (no trailing rewrite) so concurrent
    appends from different processes don't clobber each other.
    """
    line = json.dumps(
        {
            "ts": _now(),
            "file": file,
            "manifest": manifest,
            "status": status,
            "stage_2": stage_2,
            "stage_3": stage_3,
            "residual": residual,
            "error": error,
            "cost_usd": round(cost_usd, 6),
        },
        ensure_ascii=False,
    )
    sdir = Path(state_dir) / "shards" / shard_id
    sdir.mkdir(parents=True, exist_ok=True)
    with (sdir / "progress.jsonl").open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def read_progress(state_dir: Path | str, shard_id: str) -> list[dict[str, Any]]:
    """Return parsed progress entries for a shard (oldest first)."""
    p = Path(state_dir) / "shards" / shard_id / "progress.jsonl"
    if not p.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # skip corrupted lines, don't crash
    return out


def shard_completed_files(state_dir: Path | str, shard_id: str) -> set[str]:
    """Set of absolute file paths already processed in this shard.

    Used by workers to skip already-done files after compaction or restart.
    """
    return {
        e.get("file", "")
        for e in read_progress(state_dir, shard_id)
        if e.get("file") and e.get("status") in ("done", "failed", "skipped")
    }


def shard_pending_files(state_dir: Path | str, shard_id: str) -> list[str]:
    """Files in files.txt that are not yet present in progress.jsonl."""
    sdir = Path(state_dir) / "shards" / shard_id
    files_txt = sdir / "files.txt"
    if not files_txt.exists():
        return []
    all_files = [
        ln.strip()
        for ln in files_txt.read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]
    done = shard_completed_files(state_dir, shard_id)
    return [f for f in all_files if f not in done]


def set_shard_status(state_dir: Path | str, shard_id: str, status: str) -> None:
    sdir = Path(state_dir) / "shards" / shard_id
    sdir.mkdir(parents=True, exist_ok=True)
    _atomic_write(sdir / "status", status + "\n")
    # Reflect in state.json too.
    try:
        state = BatchState.load(state_dir)
    except (FileNotFoundError, json.JSONDecodeError):
        return
    s = state.shard(shard_id)
    if s is None:
        return
    s.status = status
    s.last_updated = _now()
    progress = read_progress(state_dir, shard_id)
    s.completed = sum(1 for e in progress if e.get("status") == "done")
    s.failed = sum(1 for e in progress if e.get("status") == "failed")
    state.recompute_totals()
    state.save()


# -----------------------------------------------------------------------------
# Aggregation
# -----------------------------------------------------------------------------


@dataclass
class RollupResult:
    batch_id: str
    state_dir: str
    files_total: int = 0
    files_done: int = 0
    files_failed: int = 0
    findings_total: int = 0
    fixes_stage_2: int = 0
    fixes_stage_3: int = 0
    residual_total: int = 0
    severity_counts: dict[str, int] = field(default_factory=dict)
    rule_counts: dict[str, int] = field(default_factory=dict)
    cost_usd: float = 0.0
    files: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def aggregate_rollup(state_dir: Path | str) -> RollupResult:
    """Walk every shard's progress + each per-file manifest; return aggregate."""
    state = BatchState.load(state_dir)
    result = RollupResult(batch_id=state.batch_id, state_dir=str(state_dir))

    for shard in state.shards:
        for entry in read_progress(state_dir, shard.id):
            result.files_total += 1
            status = entry.get("status")
            if status == "done":
                result.files_done += 1
            elif status == "failed":
                result.files_failed += 1
                result.errors.append(
                    {
                        "file": entry.get("file"),
                        "shard": shard.id,
                        "error": entry.get("error"),
                    }
                )
            result.cost_usd += float(entry.get("cost_usd") or 0.0)

            manifest_path = entry.get("manifest")
            if not manifest_path or not Path(manifest_path).exists():
                result.files.append(
                    {"file": entry.get("file"), "status": status, "manifest": None}
                )
                continue
            try:
                m = json.loads(Path(manifest_path).read_text())
            except json.JSONDecodeError:
                continue

            stage_2 = len(m.get("stage_2_fixes_applied") or [])
            stage_3 = len(m.get("stage_3_fixes_applied") or [])
            residual = m.get("residual_findings") or []
            findings_total = m.get("stage_1_findings_total") or 0
            result.findings_total += findings_total
            result.fixes_stage_2 += stage_2
            result.fixes_stage_3 += stage_3
            result.residual_total += len(residual)
            for f in residual:
                sev = f.get("severity") or "unknown"
                result.severity_counts[sev] = result.severity_counts.get(sev, 0) + 1
                rid = f.get("rule_id") or "unknown"
                result.rule_counts[rid] = result.rule_counts.get(rid, 0) + 1
            result.files.append(
                {
                    "file": entry.get("file"),
                    "status": status,
                    "manifest": manifest_path,
                    "findings_total": findings_total,
                    "stage_2": stage_2,
                    "stage_3": stage_3,
                    "residual": len(residual),
                }
            )

    return result


def write_rollup(state_dir: Path | str) -> RollupResult:
    """Aggregate and write rollup.json."""
    result = aggregate_rollup(state_dir)
    _atomic_write(
        Path(state_dir) / "rollup.json",
        json.dumps(result.to_json(), indent=2),
    )
    return result
