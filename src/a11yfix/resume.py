"""Resume + status helpers.

`find_active_batches` scans `~/.a11yfix/batches/` for batches that aren't
done. `write_resume_md` materializes a human/AI-readable brief that the
PreCompact hook drops into the conversation post-compaction. `next_unfinished_*`
let a worker recover its place after compaction.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from a11yfix.batch import (
    DEFAULT_BATCHES_ROOT,
    BatchState,
    aggregate_rollup,
    latest_progress_by_file,
    shard_completed_files,
    shard_pending_files,
)


@dataclass
class BatchInfo:
    batch_id: str
    state_dir: Path
    status: str
    started_at: str
    last_updated: str
    files_total: int
    files_done: int
    files_failed: int


def find_active_batches(root: Path | str | None = None) -> list[BatchInfo]:
    """Return batches that aren't fully done, sorted oldest-first."""
    base = Path(root or DEFAULT_BATCHES_ROOT)
    if not base.exists():
        return []
    out: list[BatchInfo] = []
    for child in sorted(base.iterdir()):
        if not child.is_dir():
            continue
        state_path = child / "state.json"
        if not state_path.exists():
            continue
        try:
            state = BatchState.load(child)
        except Exception:
            continue
        status = state.status()
        if status == "done":
            continue
        # Aggregate quickly via per-shard progress files (no manifest reads).
        # Latest entry per file: retried files must not double-count.
        done = 0
        failed = 0
        for s in state.shards:
            for entry in latest_progress_by_file(child, s.id).values():
                st = entry.get("status")
                if st == "done":
                    done += 1
                elif st == "failed":
                    failed += 1
        files_total = sum(s.files for s in state.shards)
        out.append(
            BatchInfo(
                batch_id=state.batch_id,
                state_dir=child,
                status=status,
                started_at=state.started_at,
                last_updated=state.last_updated,
                files_total=files_total,
                files_done=done,
                files_failed=failed,
            )
        )
    out.sort(key=lambda b: b.started_at)
    return out


def next_unfinished_files(state_dir: Path | str, shard_id: str) -> list[str]:
    """Files this shard still needs to process. Used by a compacted worker."""
    return shard_pending_files(state_dir, shard_id)


# -----------------------------------------------------------------------------
# RESUME.md generation (fed into the conversation post-compaction)
# -----------------------------------------------------------------------------


def _humanize_short(p: str | None) -> str:
    if not p:
        return ""
    return Path(p).name


def write_resume_md(state_dir: Path | str) -> Path:
    """Rewrite RESUME.md from current state. Called by the PreCompact hook.

    The brief is intentionally compact: shard table + first 5 unfinished files
    so the orchestrator can pick up immediately without re-reading the entire
    batch state.
    """
    state = BatchState.load(state_dir)
    sd = Path(state.state_dir)

    completed_total = sum(
        sum(1 for e in latest_progress_by_file(sd, s.id).values() if e.get("status") == "done")
        for s in state.shards
    )
    failed_total = sum(
        sum(1 for e in latest_progress_by_file(sd, s.id).values() if e.get("status") == "failed")
        for s in state.shards
    )
    files_total = sum(s.files for s in state.shards)

    lines: list[str] = []
    lines.append(f"# Active a11yfix batch — {state.batch_id}")
    lines.append("")
    lines.append(
        f"**Status:** {state.status()}  |  "
        f"**Mode:** {state.mode}  |  "
        f"**Model:** {state.model}"
    )
    lines.append(
        f"**Files:** {files_total} total, "
        f"{completed_total} done, "
        f"{failed_total} failed, "
        f"{max(0, files_total - completed_total - failed_total)} pending"
    )
    if state.source_root:
        lines.append(f"**Source:** `{state.source_root}`")
    lines.append(f"**State dir:** `{sd}`")
    lines.append("")
    lines.append("## Shards")
    lines.append("")
    lines.append("| Shard | Files | Status | Done | Failed | Pending |")
    lines.append("|---|---|---|---|---|---|")
    for s in state.shards:
        prog = latest_progress_by_file(sd, s.id).values()
        sd_done = sum(1 for e in prog if e.get("status") == "done")
        sd_failed = sum(1 for e in prog if e.get("status") == "failed")
        sd_pending = max(0, s.files - sd_done - sd_failed)
        lines.append(
            f"| {s.id} | {s.files} | {s.status} | {sd_done} | {sd_failed} | {sd_pending} |"
        )
    lines.append("")

    # Show first few unfinished files per still-running shard.
    for s in state.shards:
        if s.status in ("done",):
            continue
        pending = shard_pending_files(sd, s.id)
        if not pending:
            continue
        lines.append(f"### {s.id} — next up")
        for p in pending[:5]:
            lines.append(f"- `{_humanize_short(p)}`")
        if len(pending) > 5:
            lines.append(f"- ... and {len(pending) - 5} more")
        lines.append("")

    lines.append("## How to resume")
    lines.append("")
    lines.append(
        "Re-invoke the `using-a11yfix` skill. It will detect this batch via "
        "`a11yfix-status` and continue from where it left off. To resume "
        "directly from the CLI:"
    )
    lines.append("")
    lines.append("```bash")
    lines.append(f"a11yfix --resume {state.batch_id}")
    lines.append("```")
    lines.append("")
    lines.append("Per-shard progress: `<state-dir>/shards/<shard-id>/progress.jsonl`")

    out = sd / "RESUME.md"
    from a11yfix._io import atomic_write

    atomic_write(out, "\n".join(lines))
    return out


def write_all_resume_briefs(root: Path | str | None = None) -> list[Path]:
    """Rewrite RESUME.md for every active batch. Hook entry-point."""
    written: list[Path] = []
    for info in find_active_batches(root):
        try:
            written.append(write_resume_md(info.state_dir))
        except Exception:
            continue
    return written


# -----------------------------------------------------------------------------
# Status table for `a11yfix-status` CLI
# -----------------------------------------------------------------------------


def status_table(infos: Iterable[BatchInfo]) -> str:
    rows = list(infos)
    if not rows:
        return "No active a11yfix batches."
    out = ["BATCH        STATUS                FILES   DONE   FAILED   STARTED"]
    for b in rows:
        out.append(
            f"{b.batch_id:<12} {b.status:<20} {b.files_total:>5}   "
            f"{b.files_done:>4}   {b.files_failed:>5}   {b.started_at}"
        )
    return "\n".join(out)


def detail_table(state_dir: Path | str) -> str:
    """Detailed view for `a11yfix-status --batch <id>`."""
    rollup = aggregate_rollup(state_dir)
    state = BatchState.load(state_dir)
    out: list[str] = []
    out.append(f"Batch:         {state.batch_id}")
    out.append(f"State dir:     {state.state_dir}")
    out.append(f"Source folder: {state.source_root or '(none)'}")
    out.append(f"Status:        {state.status()}")
    out.append(f"Mode:          {state.mode}")
    out.append(f"Model:         {state.model}")
    out.append(f"Started:       {state.started_at}")
    out.append(f"Updated:       {state.last_updated}")
    out.append("")
    out.append(f"Files: {rollup.files_total} total, "
               f"{rollup.files_done} done, {rollup.files_failed} failed")
    out.append(f"Findings: {rollup.findings_total}  "
               f"(stage 2: {rollup.fixes_stage_2}, stage 3: {rollup.fixes_stage_3})")
    out.append(f"Residual: {rollup.residual_total}")
    out.append(f"Cost:     ${rollup.cost_usd:.4f}")
    if rollup.severity_counts:
        out.append("")
        out.append("Residual by severity:")
        for sev, count in sorted(rollup.severity_counts.items(), key=lambda x: -x[1]):
            out.append(f"  {sev:<22} {count}")
    if rollup.rule_counts:
        out.append("")
        out.append("Top residual rules:")
        for rule, count in sorted(rollup.rule_counts.items(), key=lambda x: -x[1])[:8]:
            out.append(f"  {rule:<28} {count}")
    if rollup.errors:
        out.append("")
        out.append(f"Errors ({len(rollup.errors)}):")
        for e in rollup.errors[:10]:
            out.append(f"  {Path(e['file']).name:<40} {e.get('error') or ''}")
    return "\n".join(out)


__all__ = [
    "BatchInfo",
    "detail_table",
    "find_active_batches",
    "next_unfinished_files",
    "shard_completed_files",
    "status_table",
    "write_all_resume_briefs",
    "write_resume_md",
]
