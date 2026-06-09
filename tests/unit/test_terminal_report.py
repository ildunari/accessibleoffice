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


def test_offcanvas_title_counted_manual_not_ai_fixable():
    """An off-canvas title shares the slide-title rule id but its fixer
    declines (the slide HAS a title; repositioning is a human call). The
    footer must not promise --mode full will fix it."""
    offcanvas = _finding("slide-title-missing", Severity.ERROR, 1)
    offcanvas.extra["off_canvas"] = True
    manifest = Manifest(
        file_path="/tmp/deck.pptx",
        file_format=FileFormat.PPTX,
        stage_1_findings_total=1,
        residual_findings=[offcanvas],
        validation=ValidationResult(status="skipped"),
    )

    out = _render(manifest)
    assert "--mode full" not in out, "footer promised an AI fix the fixer refuses"
    assert "0" in out and "manual" in out.lower()


def test_document_language_footer_points_to_default_lang():
    """A residual document-language finding is only deterministic with
    --default-lang; the footer must say how to actually fix it."""
    manifest = Manifest(
        file_path="/tmp/doc.docx",
        file_format=FileFormat.DOCX,
        stage_1_findings_total=1,
        residual_findings=[_finding("document-language-missing", Severity.WARNING, 1)],
        validation=ValidationResult(status="skipped"),
    )

    out = _render(manifest)
    assert "--default-lang" in out


def test_finding_fixability_classification():
    from a11yfix.rules.base import finding_fixability

    ai = _finding("alt-text-missing", Severity.ERROR, 1)
    det = _finding("document-title-missing", Severity.TIP, 2)
    manual = _finding("color-contrast", Severity.INTELLIGENT, 3)
    offcanvas = _finding("slide-title-missing", Severity.ERROR, 4)
    offcanvas.extra["off_canvas"] = True

    assert finding_fixability(ai) == "ai"
    assert finding_fixability(det) == "deterministic"
    assert finding_fixability(manual) == "manual"
    assert finding_fixability(offcanvas) == "manual"
