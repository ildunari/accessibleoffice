"""Unit tests for slide_title rule."""

from a11yfix.ooxml.pptx_reader import open_pptx
from a11yfix.rules.slide_title import SlideTitleRule


def test_slide_without_title_detected(pptx_no_alt):
    doc = open_pptx(pptx_no_alt)
    findings = list(SlideTitleRule().detect(doc))
    assert findings  # the blank slide has no title
    assert all(f.rule_id == "slide-title-missing" for f in findings)


def test_slide_with_title_not_flagged_for_that_slide(pptx_with_title):
    doc = open_pptx(pptx_with_title)
    findings = list(SlideTitleRule().detect(doc))
    # Slide 2 has the title; slide 1 (blank) does not. Expect ≥1 flagged.
    flagged_indices = {f.extra["slide_index"] for f in findings}
    # The title slide (index 2) should NOT be flagged.
    assert 2 not in flagged_indices
