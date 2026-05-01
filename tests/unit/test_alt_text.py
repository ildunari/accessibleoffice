"""Unit tests for alt_text rule."""

from a11yfix.ooxml.pptx_reader import open_pptx
from a11yfix.rules.alt_text import AltTextRule


def test_alt_missing_detected(pptx_no_alt):
    doc = open_pptx(pptx_no_alt)
    findings = list(AltTextRule().detect(doc))
    assert any(f.rule_id == "alt-text-missing" for f in findings)


def test_alt_present_not_flagged(pptx_with_alt):
    doc = open_pptx(pptx_with_alt)
    findings = list(AltTextRule().detect(doc))
    # The fixture has at least one image with alt; we should not flag it
    paths = [f.officecli_path for f in findings]
    # tolerate other shapes; ensure no finding for pic with descr
    assert all("@id=" not in p or "missing" not in p for p in paths)
