"""Rule: decorative shapes that look ornamental but lack adec:decorative=1.

WCAG 1.1.1 (Non-text Content). Severity: Tip.

Heuristic: zero-text auto-shapes used as page borders, line ornaments, or pure
decoration. Auto-fix only on a tight allowlist; everything else defers to stage 4.
"""

from __future__ import annotations

from collections.abc import Iterable

from a11yfix.manifest import FileFormat, Finding, Severity
from a11yfix.ooxml.namespaces import qn
from a11yfix.ooxml.pptx_paths import ppt_target_ref
from a11yfix.rules.base import BaseRule, DocumentHandle, RuleMeta, register_rule

DECORATIVE_PRESET_GEOMETRIES = {
    "line",
    "straightConnector1",
    "rect",  # only if no text and very thin / very wide
}


class DecorativeFlagRule(BaseRule):
    meta = RuleMeta(
        rule_id="decorative-flag-suggested",
        severity=Severity.TIP,
        formats={FileFormat.PPTX},
        wcag_sc=["1.1.1"],
        plain_impact="Decorative shapes should be marked so screen readers skip them.",
    )

    def detect(self, doc: DocumentHandle) -> Iterable[Finding]:
        from a11yfix.ooxml.pptx_reader import PptxHandle

        assert isinstance(doc, PptxHandle)
        for slide_idx, slide_xml in enumerate(doc.slides_xml, start=1):
            sp_tree = slide_xml.find(f".//{qn('p:cSld')}/{qn('p:spTree')}")
            if sp_tree is None:
                continue
            for sp in slide_xml.iter(qn("p:sp")):
                sp_ref = ppt_target_ref(
                    slide_idx=slide_idx,
                    sp_tree=sp_tree,
                    element=sp,
                    element_name="shape",
                    cnv_path=f"{qn('p:nvSpPr')}/{qn('p:cNvPr')}",
                )
                if sp_ref is None:
                    continue
                # has any text?
                txBody = sp.find(qn("p:txBody"))
                text = ""
                if txBody is not None:
                    text = "".join(t.text or "" for t in txBody.iter(qn("a:t")))
                if text.strip():
                    continue
                # geometry?
                spPr = sp.find(qn("p:spPr"))
                if spPr is None:
                    continue
                prstGeom = spPr.find(qn("a:prstGeom"))
                if prstGeom is None:
                    continue
                prst = prstGeom.get("prst") or ""
                if prst not in DECORATIVE_PRESET_GEOMETRIES:
                    continue
                # already decorative?
                nv = sp.find(qn("p:nvSpPr"))
                if nv is None:
                    continue
                cnv = nv.find(qn("p:cNvPr"))
                if cnv is None:
                    continue
                # Walk extLst for adec:decorative='1'
                already = False
                extlst = cnv.find(qn("a:extLst"))
                if extlst is not None:
                    for ext in extlst.findall(qn("a:ext")):
                        for d in ext.findall(qn("adec:decorative")):
                            if d.get("val") == "1":
                                already = True
                                break
                        if already:
                            break
                if already:
                    continue
                yield Finding(
                    id=f"decor-slide{slide_idx}-shape{sp_ref.shape_id}",
                    rule_id=self.meta.rule_id,
                    severity=self.meta.severity,
                    wcag_sc=self.meta.wcag_sc,
                    officecli_path=sp_ref.path,
                    current_value=f"empty {prst} shape",
                    plain_impact=self.meta.plain_impact,
                    extra={"prst": prst, "auto_fixable": prst == "line"},
                )


register_rule(DecorativeFlagRule())
