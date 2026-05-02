"""`a11yfix-rollup` CLI: aggregate per-file manifests into one batch summary.

Reads the state-dir's per-shard progress.jsonl + each per-file manifest, sums
findings/fixes/residuals/cost, prints a table, writes `<state-dir>/rollup.json`.

Entry points:
    a11yfix-rollup <batch-id>            # auto-resolves under DEFAULT_BATCHES_ROOT
    a11yfix-rollup <state-dir>           # absolute path
    a11yfix-rollup <id> --json           # raw JSON to stdout
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from a11yfix.batch import write_rollup
from a11yfix.cli import _resolve_batch_id


@click.command()
@click.argument("batch", type=str)
@click.option("--json", "as_json", is_flag=True, help="Emit raw JSON to stdout.")
def main(batch: str, as_json: bool) -> None:
    """Aggregate a batch's per-file manifests into one rollup."""
    sd = _resolve_batch_id(batch)
    rollup = write_rollup(sd)

    if as_json:
        click.echo(json.dumps(rollup.to_json(), indent=2))
        return

    out: list[str] = []
    out.append(f"Batch:     {rollup.batch_id}")
    out.append(f"State:     {rollup.state_dir}")
    out.append("")
    out.append(
        f"Files: {rollup.files_total} total, "
        f"{rollup.files_done} done, {rollup.files_failed} failed"
    )
    out.append(f"Findings: {rollup.findings_total}")
    out.append(f"Stage 2 fixes: {rollup.fixes_stage_2}")
    out.append(f"Stage 3 fixes: {rollup.fixes_stage_3}")
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
        for rule, count in sorted(rollup.rule_counts.items(), key=lambda x: -x[1])[:10]:
            out.append(f"  {rule:<28} {count}")
    if rollup.errors:
        out.append("")
        out.append(f"Errors ({len(rollup.errors)}):")
        for e in rollup.errors[:10]:
            out.append(f"  {Path(e['file']).name:<40} {e.get('error') or ''}")
    click.echo("\n".join(out))


if __name__ == "__main__":
    main()
    sys.exit(0)
