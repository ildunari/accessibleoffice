"""Tests for the honest 'what can actually be fixed' footer in the terminal report."""

from __future__ import annotations

from io import StringIO

from rich.console import Console

from a11yfix.manifest import FileFormat, Finding, Manifest, Severity, ValidationResult
from a11yfix.reporting.terminal import print_report


def _finding(rule_id: str, sev: Severity, i: int) -> Finding:
    return Finding(
        id=f"{rule_id}-{i}",
        rule_id=rule_id,
        severity=sev,
        wcag_sc=[],
        officecli_path=f"/slide[{i}]/x",
        plain_impact="",
    )


def _render(manifest: Manifest) -> str:
    buf = StringIO()
    print_report(manifest, console=Console(file=buf, width=200))
    return buf.getvalue()


def test_footer_breaks_down_fixability_and_points_to_full_mode():
    """When deterministic auto-fix applied nothing and AI-fixable issues remain,
    the report must say so and point the user at --mode full, instead of leaving
    the impression that 'auto' addressed the deck."""
    residual = [
        _finding("alt-text-missing", Severity.ERROR, 1),
        _finding("alt-text-missing", Severity.ERROR, 2),
        _finding("slide-title-missing", Severity.ERROR, 3),
        _finding("reading-order", Severity.WARNING, 4),
        _finding("color-contrast", Severity.INTELLIGENT, 5),
    ]
    manifest = Manifest(
        file_path="/tmp/deck.pptx",
        file_format=FileFormat.PPTX,
        stage_1_findings_total=5,
        residual_findings=residual,
        validation=ValidationResult(status="skipped"),
    )

    out = _render(manifest)
    assert "--mode full" in out
    # 3 AI-fixable (2 alt + 1 title), 2 manual (reading-order + contrast)
    assert "3" in out and "manual" in out.lower()


def test_clean_file_has_no_fixability_footer():
    manifest = Manifest(
        file_path="/tmp/clean.pptx",
        file_format=FileFormat.PPTX,
        stage_1_findings_total=0,
        residual_findings=[],
        validation=ValidationResult(status="ok"),
    )
    out = _render(manifest)
    assert "--mode full" not in out
