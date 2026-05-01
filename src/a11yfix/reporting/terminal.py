"""Rich-formatted human-readable report to the terminal."""

from __future__ import annotations

from collections import Counter

from rich.console import Console
from rich.table import Table

from a11yfix.manifest import Manifest, Severity

SEVERITY_STYLE = {
    Severity.ERROR.value: "bold red",
    Severity.WARNING.value: "yellow",
    Severity.TIP.value: "cyan",
    Severity.INTELLIGENT.value: "magenta",
}


def print_report(manifest: Manifest, *, console: Console | None = None) -> None:
    console = console or Console()

    counts: Counter[str] = Counter()
    for f in manifest.residual_findings:
        counts[f.severity.value] += 1
    by_rule: Counter[str] = Counter(f.rule_id for f in manifest.residual_findings)

    console.rule(f"a11yfix report — {manifest.file_path}")
    console.print(
        f"[bold]Total findings (stage 1):[/] {manifest.stage_1_findings_total}    "
        f"[green]Auto-fixed (stage 2):[/] {len(manifest.stage_2_fixes_applied)}    "
        f"[blue]AI-fixed (stage 3):[/] {len(manifest.stage_3_fixes_applied)}    "
        f"[bold]Residual:[/] {len(manifest.residual_findings)}"
    )
    console.print(f"Validation: [bold]{manifest.validation.status}[/]")

    if not manifest.residual_findings:
        console.print("\n[bold green]No residual findings — file is clean.[/]\n")
        return

    by_sev = Table(title="Residual by severity")
    by_sev.add_column("Severity")
    by_sev.add_column("Count", justify="right")
    for sev, count in counts.most_common():
        by_sev.add_row(f"[{SEVERITY_STYLE.get(sev, '')}]{sev}[/]", str(count))
    console.print(by_sev)

    by_rule_t = Table(title="Residual by rule")
    by_rule_t.add_column("Rule")
    by_rule_t.add_column("Count", justify="right")
    for rule, count in by_rule.most_common():
        by_rule_t.add_row(rule, str(count))
    console.print(by_rule_t)

    detail = Table(title="Residual findings (first 20)")
    detail.add_column("ID")
    detail.add_column("Rule")
    detail.add_column("Sev")
    detail.add_column("Path")
    detail.add_column("Why human")
    for f in manifest.residual_findings[:20]:
        detail.add_row(
            f.id,
            f.rule_id,
            f"[{SEVERITY_STYLE.get(f.severity.value, '')}]{f.severity.value}[/]",
            f.officecli_path,
            f.why_human_needed[:40],
        )
    console.print(detail)
    if len(manifest.residual_findings) > 20:
        console.print(f"[dim]... and {len(manifest.residual_findings) - 20} more[/]")
