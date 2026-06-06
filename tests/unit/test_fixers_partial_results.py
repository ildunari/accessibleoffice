"""Regression tests for partial officecli batch result handling."""

from __future__ import annotations

from pathlib import Path

from a11yfix.fixers import deterministic, single_shot
from a11yfix.manifest import FileFormat, Finding, Severity
from a11yfix.ooxml.officecli import BatchResult, ValidationResult
from a11yfix.rules.base import OfficecliOp, RuleMeta, SingleShotFix


class FakeDoc:
    file_format = FileFormat.PPTX

    def __init__(self, path: Path) -> None:
        self.path = str(path)

    def root_xml(self):
        return None


class FakeRule:
    meta = RuleMeta(
        rule_id="fake-rule",
        severity=Severity.ERROR,
        formats={FileFormat.PPTX},
        wcag_sc=[],
        plain_impact="fake",
    )

    def fix_deterministic(self, finding, doc):
        return [OfficecliOp(verb="set", path=finding.officecli_path, props={"x": "y"})]

    def fix_single_shot(self, finding, doc):
        return SingleShotFix(kind="link-text", finding=finding)


class ShortResultClient:
    backup_path = None

    def __init__(self, path):
        self.path = path

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None

    def batch(self, ops):
        return BatchResult(success=False, per_op=[{"ok": True}])

    def validate(self):
        return ValidationResult(status="ok")


class FakeAdapter:
    name = "fake"

    def suggest_link_text(self, url, surrounding_text):
        class Result:
            text = "descriptive link"
            confidence = 0.9
            model = "fake"

        return Result()


def _finding(i: int) -> Finding:
    return Finding(
        id=f"f{i}",
        rule_id="fake-rule",
        severity=Severity.ERROR,
        wcag_sc=[],
        officecli_path=f"/sld[1]/pic[{i}]",
        extra={"url": "https://example.com", "shape_text": "click"},
    )


def test_deterministic_short_officecli_results_defer_missing_ops(tmp_path, monkeypatch):
    doc = FakeDoc(tmp_path / "deck.pptx")
    findings = [_finding(1), _finding(2)]

    monkeypatch.setitem(deterministic.REGISTRY, "fake-rule", FakeRule())
    monkeypatch.setattr(deterministic, "OfficecliClient", ShortResultClient)

    result = deterministic.apply_deterministic_fixes(findings, doc)

    assert [f.finding_id for f in result.applied] == ["f1"]
    assert [f.id for f in result.deferred] == ["f2"]


def test_single_shot_short_officecli_results_defer_missing_ops(tmp_path, monkeypatch):
    doc = FakeDoc(tmp_path / "deck.pptx")
    findings = [_finding(1), _finding(2)]

    monkeypatch.setitem(single_shot.REGISTRY, "fake-rule", FakeRule())
    monkeypatch.setattr(single_shot, "OfficecliClient", ShortResultClient)

    result = single_shot.apply_single_shot_fixes(findings, doc, FakeAdapter())

    assert [f.finding_id for f in result.applied] == ["f1"]
    assert [f.id for f in result.deferred] == ["f2"]
