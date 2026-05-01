"""Rule: text color contrast against background.

WCAG 1.4.3 (Contrast Minimum). Severity: Intelligent Services (heuristic in our impl).

Detection scope (v1):
  - Inspect runs with explicit a:srgbClr or a:schemeClr fill.
  - Background defaults to slide white / page white when not directly determinable.
  - Computes contrast ratio; flags <4.5 (or <3.0 for large text).

This is intentionally conservative — real Microsoft Checker uses pixel analysis
plus theme resolution we don't fully replicate. We flag with low confidence;
stage 4 disambiguates.
"""

from __future__ import annotations

from collections.abc import Iterable

from a11yfix.manifest import FileFormat, Finding, Severity
from a11yfix.ooxml.namespaces import qn
from a11yfix.ooxml.theme_colors import RGB, ThemeColorResolver, contrast_ratio
from a11yfix.rules.base import BaseRule, DocumentHandle, RuleMeta, register_rule

WHITE = RGB(255, 255, 255)
BLACK = RGB(0, 0, 0)


def _color_from_solid(solid: object, resolver: ThemeColorResolver) -> RGB | None:
    srgb = solid.find(qn("a:srgbClr"))  # type: ignore[union-attr]
    if srgb is not None:
        hexv = srgb.get("val") or "000000"
        lum_mod = lum_off = None
        for child in srgb:
            if child.tag.endswith("}lumMod"):
                lum_mod = int(child.get("val") or "100000") / 100000.0
            elif child.tag.endswith("}lumOff"):
                lum_off = int(child.get("val") or "0") / 100000.0
        return resolver.resolve_srgb(hexv, lum_mod=lum_mod, lum_off=lum_off)
    sch = solid.find(qn("a:schemeClr"))  # type: ignore[union-attr]
    if sch is not None:
        name = sch.get("val") or "tx1"
        lum_mod = lum_off = None
        for child in sch:
            if child.tag.endswith("}lumMod"):
                lum_mod = int(child.get("val") or "100000") / 100000.0
            elif child.tag.endswith("}lumOff"):
                lum_off = int(child.get("val") or "0") / 100000.0
        return resolver.resolve_scheme(name, lum_mod=lum_mod, lum_off=lum_off)
    return None


class ColorContrastRule(BaseRule):
    meta = RuleMeta(
        rule_id="color-contrast",
        severity=Severity.INTELLIGENT,
        formats={FileFormat.DOCX, FileFormat.PPTX},
        wcag_sc=["1.4.3"],
        plain_impact="Text may be hard to read for users with low vision.",
    )

    def detect(self, doc: DocumentHandle) -> Iterable[Finding]:
        if doc.file_format != FileFormat.PPTX:
            return  # docx contrast check is more involved; defer
        from a11yfix.ooxml.pptx_reader import PptxHandle

        assert isinstance(doc, PptxHandle)
        resolver = ThemeColorResolver()
        for slide_idx, slide_xml in enumerate(doc.slides_xml, start=1):
            bg = WHITE  # assume white slide background unless we resolve otherwise
            for sp_idx, sp in enumerate(slide_xml.iter(qn("p:sp")), start=1):
                for r_idx, r in enumerate(sp.iter(qn("a:r")), start=1):
                    rPr = r.find(qn("a:rPr"))
                    if rPr is None:
                        continue
                    solidFill = rPr.find(qn("a:solidFill"))
                    if solidFill is None:
                        continue
                    fg = _color_from_solid(solidFill, resolver)
                    if fg is None:
                        continue
                    ratio = contrast_ratio(fg, bg)
                    # Determine large-text threshold (≥18pt or ≥14pt bold)
                    sz = rPr.get("sz")  # in hundredths of a point
                    sz_pt = int(sz) / 100 if sz else 12.0
                    is_bold = rPr.get("b") == "1"
                    is_large = sz_pt >= 18 or (sz_pt >= 14 and is_bold)
                    threshold = 3.0 if is_large else 4.5
                    if ratio >= threshold:
                        continue
                    yield Finding(
                        id=f"contrast-sld{slide_idx}-sp{sp_idx}-r{r_idx}",
                        rule_id=self.meta.rule_id,
                        severity=self.meta.severity,
                        wcag_sc=self.meta.wcag_sc,
                        officecli_path=f"/sld[{slide_idx}]/sp[{sp_idx}]/p[1]/r[{r_idx}]",
                        current_value=f"{fg.hex} on {bg.hex} = {ratio:.2f}:1",
                        plain_impact=self.meta.plain_impact,
                        why_human_needed=(
                            "Auto-darkening would change the design system; defer to human."
                        ),
                        extra={
                            "fg_hex": fg.hex,
                            "bg_hex": bg.hex,
                            "ratio": round(ratio, 2),
                            "threshold": threshold,
                        },
                    )


register_rule(ColorContrastRule())
