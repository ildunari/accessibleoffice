"""Rule: missing slide title (PPT).

WCAG 2.4.10 (Section Headings) / 2.4.6 (Headings and Labels). Severity: Error.

Detection:
  1. Slide must have a title placeholder (p:sp where nvPr/ph type='title' or 'ctrTitle').
  2. The placeholder's text must be non-empty.

Known limitation (gotcha #10): off-canvas-but-present titles and "fake
titles" (large bold text in a non-placeholder text box) are NOT detected —
geometry inspection is out of scope for this rule version.
"""

from __future__ import annotations

from collections.abc import Iterable

from a11yfix.manifest import FileFormat, Finding, Severity
from a11yfix.ooxml.namespaces import qn
from a11yfix.rules.base import (
    BaseRule,
    DocumentHandle,
    RuleMeta,
    SingleShotFix,
    register_rule,
)

TITLE_PH_TYPES = {"title", "ctrTitle"}


def _has_title_placeholder_with_text(slide_xml: object) -> tuple[bool, bool]:
    """Returns (placeholder_present, placeholder_has_text)."""
    spTree = slide_xml.find(f".//{qn('p:cSld')}/{qn('p:spTree')}")  # type: ignore[union-attr]
    if spTree is None:
        return (False, False)
    placeholder_present = False
    has_text = False
    for sp in spTree.iter(qn("p:sp")):
        nv = sp.find(qn("p:nvSpPr"))
        if nv is None:
            continue
        nvPr = nv.find(qn("p:nvPr"))
        if nvPr is None:
            continue
        ph = nvPr.find(qn("p:ph"))
        if ph is None:
            continue
        ph_type = ph.get("type") or "body"
        if ph_type not in TITLE_PH_TYPES:
            continue
        placeholder_present = True
        # Text content
        txBody = sp.find(qn("p:txBody"))
        if txBody is not None:
            text = "".join(t.text or "" for t in txBody.iter(qn("a:t")))
            if text.strip():
                has_text = True
                break
    return placeholder_present, has_text


class SlideTitleRule(BaseRule):
    meta = RuleMeta(
        rule_id="slide-title-missing",
        severity=Severity.ERROR,
        formats={FileFormat.PPTX},
        wcag_sc=["2.4.6", "2.4.10"],
        plain_impact="Screen readers can't navigate this slide by title; users may get lost.",
    )

    def detect(self, doc: DocumentHandle) -> Iterable[Finding]:
        from a11yfix.ooxml.pptx_reader import PptxHandle

        assert isinstance(doc, PptxHandle)
        for idx, slide_xml in enumerate(doc.slides_xml, start=1):
            present, has_text = _has_title_placeholder_with_text(slide_xml)
            if has_text:
                continue
            why = (
                "Title placeholder exists but is empty"
                if present
                else "No title placeholder on this slide"
            )
            yield Finding(
                id=f"slide-title-{idx}",
                rule_id=self.meta.rule_id,
                severity=self.meta.severity,
                wcag_sc=self.meta.wcag_sc,
                officecli_path=f"/slide[{idx}]",
                current_value="",
                plain_impact=self.meta.plain_impact,
                why_human_needed=why,
                extra={"slide_index": idx, "placeholder_present": present},
            )

    def fix_single_shot(self, finding: Finding, doc: DocumentHandle) -> SingleShotFix | None:
        return SingleShotFix(kind="slide-title", finding=finding, context=dict(finding.extra))


register_rule(SlideTitleRule())
