"""Regression tests for partial officecli batch result handling."""

from __future__ import annotations

from pathlib import Path

from PIL import Image  # type: ignore[import-untyped]
from pptx import Presentation  # type: ignore[import-untyped]
from pptx.util import Inches  # type: ignore[import-untyped]

from a11yfix.fixers import deterministic, single_shot
from a11yfix.manifest import FileFormat, Finding, Severity
from a11yfix.ooxml.officecli import BatchResult, ValidationResult
from a11yfix.ooxml.pptx_reader import open_pptx
from a11yfix.rules.alt_text import AltTextRule
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
    calls = 0

    def suggest_link_text(self, url, surrounding_text):
        self.calls += 1

        class Result:
            text = "descriptive link"
            confidence = 0.9
            model = "fake"

        return Result()


class LowConfidenceAdapter:
    name = "low-confidence"

    def suggest_link_text(self, url, surrounding_text):
        class Result:
            text = "weak link text"
            confidence = 0.2
            model = "fake"

        return Result()


class UnclearAdapter:
    name = "unclear"

    def suggest_link_text(self, url, surrounding_text):
        class Result:
            text = "UNCLEAR"
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


def test_single_shot_zero_cost_cap_defers_without_adapter_call(tmp_path, monkeypatch):
    doc = FakeDoc(tmp_path / "deck.pptx")
    findings = [_finding(1)]
    adapter = FakeAdapter()

    monkeypatch.setitem(single_shot.REGISTRY, "fake-rule", FakeRule())

    result = single_shot.apply_single_shot_fixes(
        findings,
        doc,
        adapter,
        max_cost_total_usd=0,
    )

    assert adapter.calls == 0
    assert result.applied == []
    assert [f.id for f in result.deferred] == ["f1"]
    assert result.deferred[0].why_human_needed == "stage-3 deferred: batch cost cap reached"


def test_single_shot_low_confidence_deferral_has_reason(tmp_path, monkeypatch):
    doc = FakeDoc(tmp_path / "deck.pptx")
    findings = [_finding(1)]

    monkeypatch.setitem(single_shot.REGISTRY, "fake-rule", FakeRule())
    monkeypatch.setattr(single_shot, "_cache_get", lambda payload: None)
    monkeypatch.setattr(single_shot, "_cache_put", lambda payload, value: None)

    result = single_shot.apply_single_shot_fixes(findings, doc, LowConfidenceAdapter())

    assert result.applied == []
    assert result.deferred[0].why_human_needed == "stage-3 deferred: low confidence (0.20 < 0.60)"


def test_single_shot_unclear_deferral_has_reason(tmp_path, monkeypatch):
    doc = FakeDoc(tmp_path / "deck.pptx")
    findings = [_finding(1)]

    monkeypatch.setitem(single_shot.REGISTRY, "fake-rule", FakeRule())
    monkeypatch.setattr(single_shot, "_cache_get", lambda payload: None)
    monkeypatch.setattr(single_shot, "_cache_put", lambda payload, value: None)

    result = single_shot.apply_single_shot_fixes(findings, doc, UnclearAdapter())

    assert result.applied == []
    assert result.deferred[0].why_human_needed == "stage-3 deferred: model returned UNCLEAR"


def test_single_shot_pptx_alt_text_emits_officecli_alt_op(tmp_path, monkeypatch):
    pres = Presentation()
    slide = pres.slides.add_slide(pres.slide_layouts[6])
    img_path = tmp_path / "image.png"
    Image.new("RGB", (50, 50), color="blue").save(img_path)
    slide.shapes.add_picture(str(img_path), Inches(1), Inches(1), Inches(2), Inches(2))
    deck = tmp_path / "deck.pptx"
    pres.save(deck)

    doc = open_pptx(deck)
    finding = next(iter(AltTextRule().detect(doc)))
    captured: list[OfficecliOp] = []

    class AltAdapter:
        name = "fake-alt"

        def describe_image(self, image_bytes, *, max_chars, context):
            class Result:
                text = "Blue square image"
                confidence = 0.95
                model = "fake-alt"

            return Result()

    class CapturingClient:
        backup_path = None

        def __init__(self, path):
            self.path = path

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def batch(self, ops):
            captured.extend(ops)
            return BatchResult(success=True, per_op=[{"ok": True}])

        def validate(self):
            return ValidationResult(status="ok")

    monkeypatch.setattr(single_shot, "OfficecliClient", CapturingClient)
    monkeypatch.setattr(single_shot, "_cache_get", lambda payload: None)
    monkeypatch.setattr(single_shot, "_cache_put", lambda payload, value: None)

    result = single_shot.apply_single_shot_fixes([finding], doc, AltAdapter())

    assert [fix.finding_id for fix in result.applied] == [finding.id]
    assert result.deferred == []
    assert len(captured) == 1
    assert captured[0].verb == "set"
    assert captured[0].path == finding.officecli_path
    assert captured[0].props == {"alt": "Blue square image"}
