"""Rule: missing slide title (PPT).

WCAG 2.4.10 (Section Headings) / 2.4.6 (Headings and Labels). Severity: Error.

Detection:
  1. Slide must have a title placeholder (p:sp where nvPr/ph type='title' or 'ctrTitle').
  2. The placeholder's text must be non-empty.
  3. Gotcha #10 (off-canvas titles): a title with text whose explicit
     geometry places it entirely outside the slide bounds is flagged — it
     satisfies the structural check but is invisible to sighted users.
     Titles with no explicit xfrm inherit layout geometry and are assumed
     on-canvas (layout placeholders are on-canvas by design).

Known limitation: "fake titles" (large bold text in a non-placeholder text
box on a slide with no real title) are NOT detected.
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


def _title_placeholder_state(slide_xml: object) -> tuple[bool, bool, object | None, str]:
    """Returns (placeholder_present, has_text, title_sp_with_text, text)."""
    spTree = slide_xml.find(f".//{qn('p:cSld')}/{qn('p:spTree')}")  # type: ignore[union-attr]
    if spTree is None:
        return (False, False, None, "")
    placeholder_present = False
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
                return (True, True, sp, text.strip())
    return (placeholder_present, False, None, "")


def _emu(val: str | None) -> int | None:
    try:
        return int(val) if val else None
    except ValueError:
        return None


def _title_is_off_canvas(sp: object, slide_w: int | None, slide_h: int | None) -> bool:
    """True when the title's explicit geometry lies entirely outside the slide.

    Conservative: no explicit xfrm (layout-inherited geometry) or unknown
    slide size means "assume on-canvas" — we only flag boxes that provably
    don't intersect the slide rectangle.
    """
    if not slide_w or not slide_h:
        return False
    spPr = sp.find(qn("p:spPr"))  # type: ignore[union-attr]
    if spPr is None:
        return False
    xfrm = spPr.find(qn("a:xfrm"))
    if xfrm is None:
        return False
    off = xfrm.find(qn("a:off"))
    ext = xfrm.find(qn("a:ext"))
    if off is None or ext is None:
        return False
    x, y = _emu(off.get("x")), _emu(off.get("y"))
    cx, cy = _emu(ext.get("cx")), _emu(ext.get("cy"))
    if x is None or y is None:
        return False
    cx, cy = cx or 0, cy or 0
    # Entirely off any edge: the box and the slide rect don't intersect.
    return x + cx <= 0 or y + cy <= 0 or x >= slide_w or y >= slide_h


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
        try:
            slide_w = int(doc.pptx.slide_width or 0)
            slide_h = int(doc.pptx.slide_height or 0)
        except (TypeError, ValueError):
            slide_w = slide_h = 0
        for idx, slide_xml in enumerate(doc.slides_xml, start=1):
            present, has_text, title_sp, title_text = _title_placeholder_state(slide_xml)
            if has_text:
                if title_sp is not None and _title_is_off_canvas(title_sp, slide_w, slide_h):
                    yield Finding(
                        id=f"slide-title-offcanvas-{idx}",
                        rule_id=self.meta.rule_id,
                        severity=self.meta.severity,
                        wcag_sc=self.meta.wcag_sc,
                        officecli_path=f"/slide[{idx}]",
                        current_value=title_text[:80],
                        plain_impact=(
                            "The slide title sits entirely off-canvas: screen readers "
                            "announce it but sighted users see no title."
                        ),
                        why_human_needed=(
                            "Title has text but is positioned outside the slide bounds; "
                            "moving it on-canvas is a design decision."
                        ),
                        extra={
                            "slide_index": idx,
                            "placeholder_present": True,
                            "off_canvas": True,
                        },
                    )
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
        # Off-canvas titles already HAVE text — generating a new title is the
        # wrong fix; repositioning is a geometry/design change for stage 4.
        if finding.extra.get("off_canvas"):
            return None
        return SingleShotFix(kind="slide-title", finding=finding, context=dict(finding.extra))


register_rule(SlideTitleRule())
