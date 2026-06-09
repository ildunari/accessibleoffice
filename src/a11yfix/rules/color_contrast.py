"""Rule: text color contrast against background.

WCAG 1.4.3 (Contrast Minimum). Severity: Intelligent Services (heuristic in our impl).

Detection scope (v1):
  - Inspect runs with explicit or nearby inherited a:srgbClr/a:schemeClr fills.
  - Resolve direct shape/slide backgrounds when determinable.
  - Computes contrast ratio; flags <4.5 (or <3.0 for large text).

This is intentionally conservative — real Microsoft Checker uses pixel analysis
plus full theme/layout/master inheritance we don't fully replicate. Unknown
foreground/background colors are skipped instead of guessed.
"""

from __future__ import annotations

import zipfile
from collections.abc import Iterable
from pathlib import Path
from xml.etree import ElementTree as ET

from a11yfix.manifest import FileFormat, Finding, Severity
from a11yfix.ooxml.namespaces import qn
from a11yfix.ooxml.pptx_paths import ppt_target_ref
from a11yfix.ooxml.theme_colors import DEFAULT_SCHEME, RGB, ThemeColorResolver, contrast_ratio
from a11yfix.ooxml.toggles import attr_bool_enabled
from a11yfix.rules.base import BaseRule, DocumentHandle, RuleMeta, register_rule

_A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _lum_transforms(color_el: object) -> tuple[float | None, float | None]:
    lum_mod = lum_off = None
    for child in color_el:  # type: ignore[union-attr]
        if child.tag.endswith("}lumMod"):
            lum_mod = int(child.get("val") or "100000") / 100000.0
        elif child.tag.endswith("}lumOff"):
            lum_off = int(child.get("val") or "0") / 100000.0
    return lum_mod, lum_off


def _theme_scheme_from_pptx(path: str | Path) -> dict[str, str]:
    scheme = dict(DEFAULT_SCHEME)
    try:
        with zipfile.ZipFile(path) as zf:
            theme_names = sorted(n for n in zf.namelist() if n.startswith("ppt/theme/theme"))
            if not theme_names:
                return scheme
            root = ET.fromstring(zf.read(theme_names[0]))
    except (OSError, KeyError, ET.ParseError, zipfile.BadZipFile):
        return scheme
    clr_scheme = root.find(f".//{{{_A_NS}}}clrScheme")
    if clr_scheme is None:
        return scheme
    for entry in clr_scheme:
        name = _local_name(entry.tag)
        srgb = entry.find(f".//{{{_A_NS}}}srgbClr")
        if srgb is not None and srgb.get("val"):
            scheme[name] = srgb.get("val", "").upper()
            continue
        sys_clr = entry.find(f".//{{{_A_NS}}}sysClr")
        if sys_clr is not None and sys_clr.get("lastClr"):
            scheme[name] = sys_clr.get("lastClr", "").upper()
    return scheme


def _color_from_solid(solid: object, resolver: ThemeColorResolver) -> RGB | None:
    srgb = solid.find(qn("a:srgbClr"))  # type: ignore[union-attr]
    if srgb is not None:
        hexv = srgb.get("val") or "000000"
        lum_mod, lum_off = _lum_transforms(srgb)
        return resolver.resolve_srgb(hexv, lum_mod=lum_mod, lum_off=lum_off)
    sch = solid.find(qn("a:schemeClr"))  # type: ignore[union-attr]
    if sch is not None:
        name = sch.get("val") or "tx1"
        lum_mod, lum_off = _lum_transforms(sch)
        return resolver.resolve_scheme(name, lum_mod=lum_mod, lum_off=lum_off)
    return None


def _solid_fill_color(parent: object, resolver: ThemeColorResolver) -> RGB | None:
    solid = parent.find(qn("a:solidFill"))  # type: ignore[union-attr]
    if solid is None:
        return None
    return _color_from_solid(solid, resolver)


def _color_from_color_parent(parent: object, resolver: ThemeColorResolver) -> RGB | None:
    srgb = parent.find(qn("a:srgbClr"))  # type: ignore[union-attr]
    if srgb is not None:
        lum_mod, lum_off = _lum_transforms(srgb)
        return resolver.resolve_srgb(srgb.get("val") or "000000", lum_mod=lum_mod, lum_off=lum_off)
    scheme = parent.find(qn("a:schemeClr"))  # type: ignore[union-attr]
    if scheme is not None:
        lum_mod, lum_off = _lum_transforms(scheme)
        return resolver.resolve_scheme(scheme.get("val") or "bg1", lum_mod=lum_mod, lum_off=lum_off)
    return None


def _slide_background(slide_xml: object, resolver: ThemeColorResolver) -> RGB | None:
    bg = slide_xml.find(f".//{qn('p:cSld')}/{qn('p:bg')}")
    if bg is None:
        return None
    bg_pr = bg.find(qn("p:bgPr"))
    if bg_pr is not None:
        color = _solid_fill_color(bg_pr, resolver)
        if color is not None:
            return color
    bg_ref = bg.find(qn("p:bgRef"))
    if bg_ref is not None:
        color = _color_from_color_parent(bg_ref, resolver)
        if color is not None:
            return color
    return None


def _shape_background(sp: object, resolver: ThemeColorResolver) -> RGB | None:
    sp_pr = sp.find(qn("p:spPr"))  # type: ignore[union-attr]
    if sp_pr is None:
        return None
    return _solid_fill_color(sp_pr, resolver)


def _inherited_run_color(r: object, sp: object, resolver: ThemeColorResolver) -> RGB | None:
    parent = r.getparent() if hasattr(r, "getparent") else None  # type: ignore[attr-defined]
    if parent is not None:
        p_pr = parent.find(qn("a:pPr"))
        if p_pr is not None:
            def_rpr = p_pr.find(qn("a:defRPr"))
            if def_rpr is not None:
                color = _solid_fill_color(def_rpr, resolver)
                if color is not None:
                    return color
    tx_body = sp.find(qn("p:txBody"))  # type: ignore[union-attr]
    if tx_body is not None:
        lst_style = tx_body.find(qn("a:lstStyle"))
        if lst_style is not None:
            for lvl in ("a:lvl1pPr", "a:lvl2pPr", "a:lvl3pPr"):
                lvl_pr = lst_style.find(qn(lvl))
                if lvl_pr is None:
                    continue
                def_rpr = lvl_pr.find(qn("a:defRPr"))
                if def_rpr is None:
                    continue
                color = _solid_fill_color(def_rpr, resolver)
                if color is not None:
                    return color
    style = sp.find(qn("p:style"))  # type: ignore[union-attr]
    if style is not None:
        font_ref = style.find(qn("a:fontRef"))
        if font_ref is not None:
            return _color_from_color_parent(font_ref, resolver)
    return None


def _run_color(r: object, sp: object, resolver: ThemeColorResolver) -> RGB | None:
    rPr = r.find(qn("a:rPr"))  # type: ignore[union-attr]
    if rPr is not None:
        solid_fill = rPr.find(qn("a:solidFill"))
        if solid_fill is not None:
            return _color_from_solid(solid_fill, resolver)
    return _inherited_run_color(r, sp, resolver)


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
        resolver = ThemeColorResolver(scheme=_theme_scheme_from_pptx(doc.path))
        for slide_idx, slide_xml in enumerate(doc.slides_xml, start=1):
            slide_bg = _slide_background(slide_xml, resolver)
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
                bg = _shape_background(sp, resolver) or slide_bg
                if bg is None:
                    continue
                for p_idx, para in enumerate(sp.iter(qn("a:p")), start=1):
                    for r_idx, r in enumerate(para.findall(qn("a:r")), start=1):
                        rPr = r.find(qn("a:rPr"))
                        fg = _run_color(r, sp, resolver)
                        if fg is None:
                            continue
                        ratio = contrast_ratio(fg, bg)
                        # Determine large-text threshold (>=18pt or >=14pt bold)
                        sz = rPr.get("sz") if rPr is not None else None
                        sz_pt = int(sz) / 100 if sz else 12.0
                        is_bold = rPr is not None and attr_bool_enabled(rPr.get("b"))
                        is_large = sz_pt >= 18 or (sz_pt >= 14 and is_bold)
                        threshold = 3.0 if is_large else 4.5
                        if ratio >= threshold:
                            continue
                        yield Finding(
                            id=(
                                f"contrast-slide{slide_idx}-shape{sp_ref.shape_id}"
                                f"-p{p_idx}-r{r_idx}"
                            ),
                            rule_id=self.meta.rule_id,
                            severity=self.meta.severity,
                            wcag_sc=self.meta.wcag_sc,
                            officecli_path=f"{sp_ref.path}/p[{p_idx}]/r[{r_idx}]",
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
