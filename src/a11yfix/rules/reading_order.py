"""Rule: reading order on slides (PPT only — Word reading order is content order).

WCAG 1.3.2 (Meaningful Sequence). Severity: Warning.

PowerPoint reads shapes in document (z-order) order, NOT spatial. If the spatial
flow is significantly different from the z-order, screen readers will read the
slide in a confusing sequence.
"""

from __future__ import annotations

from collections.abc import Iterable

from a11yfix.manifest import FileFormat, Finding, Severity
from a11yfix.ooxml.namespaces import qn
from a11yfix.rules.base import BaseRule, DocumentHandle, RuleMeta, register_rule


def _emu(val: str | None) -> int:
    try:
        return int(val) if val else 0
    except ValueError:
        return 0


def _shape_position(sp: object) -> tuple[int, int]:
    """Return (top, left) in EMUs from spPr/xfrm/off."""
    spPr = sp.find(qn("p:spPr"))  # type: ignore[union-attr]
    if spPr is None:
        return (0, 0)
    xfrm = spPr.find(qn("a:xfrm"))
    if xfrm is None:
        return (0, 0)
    off = xfrm.find(qn("a:off"))
    if off is None:
        return (0, 0)
    return (_emu(off.get("y")), _emu(off.get("x")))


class ReadingOrderRule(BaseRule):
    meta = RuleMeta(
        rule_id="reading-order",
        severity=Severity.WARNING,
        formats={FileFormat.PPTX},
        wcag_sc=["1.3.2"],
        plain_impact="Screen readers may read slide content in the wrong order.",
    )

    def detect(self, doc: DocumentHandle) -> Iterable[Finding]:
        from a11yfix.ooxml.pptx_reader import PptxHandle

        assert isinstance(doc, PptxHandle)
        for slide_idx, slide_xml in enumerate(doc.slides_xml, start=1):
            spTree = slide_xml.find(f".//{qn('p:cSld')}/{qn('p:spTree')}")
            if spTree is None:
                continue
            # Collect non-placeholder, non-decorative shapes with positions
            shapes: list[tuple[int, tuple[int, int]]] = []
            for child_idx, child in enumerate(spTree, start=1):
                # Skip group properties
                if not child.tag.endswith("}sp") and not child.tag.endswith("}pic"):
                    continue
                pos = _shape_position(child)
                shapes.append((child_idx, pos))
            if len(shapes) < 3:
                continue
            # Compare z-order ranking vs spatial ranking (top-to-bottom, then left-to-right).
            spatial_order = sorted(shapes, key=lambda t: (t[1][0], t[1][1]))
            z_indices = [s[0] for s in shapes]
            spatial_indices = [s[0] for s in spatial_order]
            if z_indices == spatial_indices:
                continue
            # Inversion count
            inversions = 0
            for i, idx in enumerate(z_indices):
                expected = spatial_indices.index(idx)
                if expected != i:
                    inversions += 1
            if inversions < max(2, len(shapes) // 2):
                continue
            yield Finding(
                id=f"reading-order-slide{slide_idx}",
                rule_id=self.meta.rule_id,
                severity=self.meta.severity,
                wcag_sc=self.meta.wcag_sc,
                officecli_path=f"/slide[{slide_idx}]",
                current_value=f"{inversions} shapes out of order",
                plain_impact=self.meta.plain_impact,
                why_human_needed="Spatial inference may not match author intent.",
                extra={"slide_index": slide_idx, "shape_count": len(shapes)},
            )


register_rule(ReadingOrderRule())
