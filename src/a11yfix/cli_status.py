"""`a11yfix-status` CLI: list active batches, show detail, write resume briefs.

Entry points:
    a11yfix-status                       # table of active batches
    a11yfix-status --batch <id>          # detail view
    a11yfix-status --write-resume <id>   # rewrite RESUME.md (PreCompact hook)
    a11yfix-status --write-resume-all    # for every active batch
    a11yfix-status --gc                  # remove batches older than --gc-days
"""

from __future__ import annotations

import shutil
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import click

from a11yfix.batch import DEFAULT_BATCHES_ROOT, BatchState
from a11yfix.cli import _resolve_batch_id
from a11yfix.resume import (
    detail_table,
    find_active_batches,
    status_table,
    write_all_resume_briefs,
    write_resume_md,
)


@click.command()
@click.option("--batch", "batch_id", default=None, help="Show detail for this batch id.")
@click.option(
    "--write-resume",
    "write_resume_id",
    default=None,
    help="Rewrite RESUME.md for the named batch (id or state-dir path).",
)
@click.option(
    "--write-resume-all",
    is_flag=True,
    help="Rewrite RESUME.md for every active batch (PreCompact hook entry).",
)
@click.option("--gc", "gc_flag", is_flag=True, help="Remove finished batches older than --gc-days.")
@click.option("--gc-days", type=int, default=7, show_default=True)
@click.option(
    "--root",
    type=click.Path(path_type=Path),
    default=None,
    help=f"Override batches root (default: {DEFAULT_BATCHES_ROOT}).",
)
def main(
    batch_id: str | None,
    write_resume_id: str | None,
    write_resume_all: bool,
    gc_flag: bool,
    gc_days: int,
    root: Path | None,
) -> None:
    """Inspect and manage a11yfix batches."""
    base = root or DEFAULT_BATCHES_ROOT

    if write_resume_all:
        written = write_all_resume_briefs(base)
        for p in written:
            click.echo(f"wrote {p}")
        if not written:
            click.echo("no active batches")
        return

    if write_resume_id:
        sd = _resolve_batch_id(write_resume_id)
        out = write_resume_md(sd)
        click.echo(f"wrote {out}")
        return

    if batch_id:
        sd = _resolve_batch_id(batch_id)
        click.echo(detail_table(sd))
        return

    if gc_flag:
        removed = _gc(base, gc_days)
        click.echo(f"removed {removed} stale batch(es)")
        return

    actives = find_active_batches(base)
    click.echo(status_table(actives))
    if not actives:
        # Also show recent done ones briefly so users can find their last run.
        if base.exists():
            recent = sorted(base.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)[:5]
            done_ids = []
            for child in recent:
                if not child.is_dir():
                    continue
                if not (child / "state.json").exists():
                    continue
                try:
                    state = BatchState.load(child)
                except Exception:
                    continue
                if state.status() == "done":
                    done_ids.append(state.batch_id)
            if done_ids:
                click.echo("")
                click.echo(f"Recent completed: {', '.join(done_ids)}")


def _gc(root: Path, days: int) -> int:
    if not root.exists():
        return 0
    cutoff = datetime.now(UTC) - timedelta(days=days)
    removed = 0
    for child in root.iterdir():
        if not child.is_dir():
            continue
        if not (child / "state.json").exists():
            continue
        try:
            state = BatchState.load(child)
        except Exception:
            continue
        if state.status() != "done":
            continue
        try:
            last = datetime.fromisoformat(state.last_updated)
        except ValueError:
            continue
        if last >= cutoff:
            continue
        shutil.rmtree(child, ignore_errors=True)
        removed += 1
    return removed


if __name__ == "__main__":
    main()
    sys.exit(0)
