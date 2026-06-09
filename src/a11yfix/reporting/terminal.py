"""Rich-formatted human-readable report to the terminal."""

from __future__ import annotations

from collections import Counter

from rich.console import Console
from rich.table import Table

from a11yfix.manifest import Manifest, Severity
from a11yfix.rules.base import finding_fixability

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

    _print_fixability_footer(manifest, console)


def _print_fixability_footer(manifest: Manifest, console: Console) -> None:
    """Honest breakdown of what is left to fix and how to fix it.

    Prevents the false impression that `auto` mode addressed an image-heavy deck:
    most real findings (alt text, slide titles) need stage 3 (`--mode full`), and
    contrast/reading-order need human judgment. `auto` only resolves the small
    deterministic set. Classification is per finding (finding_fixability), not
    per rule id — e.g. off-canvas titles share the slide-title rule id but are
    a manual repositioning call, not an AI generation target.
    """
    fixability = Counter(finding_fixability(f) for f in manifest.residual_findings)
    ai_n = fixability["ai"]
    det_left = fixability["deterministic"]
    manual_n = fixability["manual"]
    ran_ai = bool(manifest.stage_3_fixes_applied)

    console.print(
        "\n[bold]What's left:[/] "
        f"[blue]{ai_n}[/] AI-fixable, "
        f"[green]{det_left}[/] deterministic, "
        f"[dim]{manual_n}[/] need manual review"
    )
    if ai_n and not ran_ai:
        console.print(
            f"[dim]↳ Run [bold]--mode full[/] to auto-generate the {ai_n} AI-fixable "
            "item(s) (alt text, link text, slide titles). "
            "'auto' applies only deterministic fixes.[/]"
        )
    if any(f.rule_id == "document-language-missing" for f in manifest.residual_findings):
        console.print(
            "[dim]↳ The document-language fix is opt-in: re-run with "
            "[bold]--default-lang[/] (e.g. --default-lang en-US).[/]"
        )
    if manual_n:
        console.print(
            "[dim]↳ Manual-review items (contrast, reading order, decorative flags, "
            "off-canvas titles) are judgment calls and are never auto-applied.[/]"
        )
