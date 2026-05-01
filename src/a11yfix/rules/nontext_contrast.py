"""Rule: non-text contrast (1.4.11) for borders, lines, UI elements (PPT shapes).

Currently a stub — flags PPT lines with very low contrast against assumed-white BG.
"""

from __future__ import annotations

from collections.abc import Iterable

from a11yfix.manifest import FileFormat, Finding, Severity
from a11yfix.ooxml.namespaces import qn
from a11yfix.ooxml.theme_colors import RGB, ThemeColorResolver, contrast_ratio
from a11yfix.rules.base import BaseRule, DocumentHandle, RuleMeta, register_rule

WHITE = RGB(255, 255, 255)


class NonTextContrastRule(BaseRule):
    meta = RuleMeta(
        rule_id="nontext-contrast",
        severity=Severity.TIP,
        formats={FileFormat.PPTX},
        wcag_sc=["1.4.11"],
        plain_impact="Borders/UI shapes may be invisible to users with low vision.",
    )

    def detect(self, doc: DocumentHandle) -> Iterable[Finding]:
        from a11yfix.ooxml.pptx_reader import PptxHandle

        assert isinstance(doc, PptxHandle)
        resolver = ThemeColorResolver()
        for slide_idx, slide_xml in enumerate(doc.slides_xml, start=1):
            for sp_idx, sp in enumerate(slide_xml.iter(qn("p:sp")), start=1):
                spPr = sp.find(qn("p:spPr"))
                if spPr is None:
                    continue
                ln = spPr.find(qn("a:ln"))
                if ln is None:
                    continue
                solidFill = ln.find(qn("a:solidFill"))
                if solidFill is None:
                    continue
                srgb = solidFill.find(qn("a:srgbClr"))
                if srgb is None:
                    continue
                hexv = srgb.get("val") or "000000"
                fg = resolver.resolve_srgb(hexv)
                ratio = contrast_ratio(fg, WHITE)
                if ratio >= 3.0:
                    continue
                yield Finding(
                    id=f"nontext-sld{slide_idx}-sp{sp_idx}",
                    rule_id=self.meta.rule_id,
                    severity=self.meta.severity,
                    wcag_sc=self.meta.wcag_sc,
                    officecli_path=f"/sld[{slide_idx}]/sp[{sp_idx}]",
                    current_value=f"line color {fg.hex} on white = {ratio:.2f}:1",
                    plain_impact=self.meta.plain_impact,
                    why_human_needed="Defer color changes to human review.",
                    extra={"ratio": round(ratio, 2)},
                )


register_rule(NonTextContrastRule())
