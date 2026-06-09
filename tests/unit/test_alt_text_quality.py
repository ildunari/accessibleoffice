"""Classifier-level tests for alt-text quality heuristics.

Values are taken verbatim from real lecture decks (Bioadhesion 2024 course):
PowerPoint auto-populates `descr` from the source filename / scan ID, which is
useless to a screen reader but non-empty, so Microsoft's own checker passes it.
The `alt-text-generic` rule is supposed to catch these; regressions here mean
real-world junk alt text silently passes as acceptable.
"""

from __future__ import annotations

import pytest

from a11yfix.rules.alt_text import _alt_quality_reason, _is_missing_alt


def _flagged(text: str) -> bool:
    """True if the value is caught by either the missing OR the quality rule."""
    return _is_missing_alt(text) or _alt_quality_reason(text) is not None


# --- junk that MUST be flagged (was passing as acceptable before the fix) ---

JUNK_VALUES = [
    # scan / catalog numbers (HISTO-GI)
    "001 - 14_01",
    "007 - 14_03b",
    "015 - 14_09a",
    "064 - 14_31h",
    # pure numbers / short codes (Bioadhesion)
    "018",
    "1",
    "F06-09",
    # non-ASCII filename with a real image extension (regex was ASCII-only)
    "βgalExpressionInRatTissue.png",
    # underscore/instrument-export filename, no extension (EM)
    "PS_091006_Rat_1_200nm_PJ_5min_16bit_005",
    # trailing scanner ID code after padding (EM)
    "zein1                                                          000022D",
    "Cropped PLGA Uptake 1.2                                        0000380",
    # PDF-extraction auto-name (Bioadhesion)
    "page1image23120112",
    # windows / unix file paths (already worked — keep as regression guard)
    r"C:\Users\cmb2\Desktop\Edith Presentation 5-Jun-15\CADDI_NMDD",
    r"E:\urine check.tif",
    "Figure 1 copy.tiff",
]


@pytest.mark.parametrize("value", JUNK_VALUES)
def test_junk_alt_text_is_flagged(value):
    assert _flagged(value), f"junk alt text passed as acceptable: {value!r}"


# --- legitimate descriptions that MUST NOT be flagged (false-positive guard) ---

GOOD_VALUES = [
    "Scanning electron micrograph of a polymer microsphere at 200 nm scale",
    "Bar chart comparing drug release over 24 hours for three formulations",
    "Diagram of the intestinal epithelium showing tight junctions",
    "Cross-section of rat ileum stained with hematoxylin and eosin",
    "Insulin-loaded nanoparticles adhering to the mucosal surface",
    "Photo of beads in gel capsules arranged in a row",
    # Terse scientific labels: digits with ≤2 letters, but deliberate alt text.
    # The catalog-number heuristic must not eat these (they'd be sent to AI
    # regeneration, destroying the author's text).
    "CO2",
    "p53",
    "CD4",
    "Ki 67",
    "pH 7.4",
    "B12",
    "H2O",
    "IL-6",
    "3D",
]


@pytest.mark.parametrize("value", GOOD_VALUES)
def test_descriptive_alt_text_not_flagged(value):
    assert not _flagged(value), f"good alt text wrongly flagged: {value!r}"
