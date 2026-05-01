"""Theme color resolver: theme color reference + tint/shade/lumMod/lumOff → effective RGB.

Word and PowerPoint differ in HOW they apply tint/shade modifications:
  - Word: themeTint / themeShade attributes (legacy)
  - PowerPoint: lumMod / lumOff transforms inside <a:srgbClr> / <a:schemeClr>

This module provides a single resolver shared by all rules. Excel is stubbed.

References:
  - Mike-Honey theme tint algorithm (openpyxl gist) — adapted here
  - ECMA-376 §17.18.40 (Word color), §20.1.2.3.10 (DrawingML scheme color)
"""

from __future__ import annotations

import colorsys
from dataclasses import dataclass

# Standard scheme color → default RGB fallback when no theme is provided.
# Real implementations should resolve via the document's theme1.xml.
DEFAULT_SCHEME: dict[str, str] = {
    "bg1": "FFFFFF",
    "tx1": "000000",
    "bg2": "EEECE1",
    "tx2": "1F497D",
    "accent1": "4F81BD",
    "accent2": "C0504D",
    "accent3": "9BBB59",
    "accent4": "8064A2",
    "accent5": "4BACC6",
    "accent6": "F79646",
    "hlink": "0000FF",
    "folHlink": "800080",
    "dk1": "000000",
    "lt1": "FFFFFF",
    "dk2": "1F497D",
    "lt2": "EEECE1",
}


@dataclass(frozen=True)
class RGB:
    r: int
    g: int
    b: int

    @property
    def hex(self) -> str:
        return f"{self.r:02X}{self.g:02X}{self.b:02X}"

    def relative_luminance(self) -> float:
        """WCAG relative luminance."""

        def channel(c: int) -> float:
            cs = c / 255.0
            return cs / 12.92 if cs <= 0.03928 else ((cs + 0.055) / 1.055) ** 2.4

        return 0.2126 * channel(self.r) + 0.7152 * channel(self.g) + 0.0722 * channel(self.b)

    @classmethod
    def from_hex(cls, h: str) -> RGB:
        h = h.lstrip("#").upper()
        if len(h) != 6:
            raise ValueError(f"invalid hex: {h}")
        return cls(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


# ----- transforms -------------------------------------------------------------------------------


def _hls_to_rgb(h: float, l: float, s: float) -> RGB:
    r, g, b = colorsys.hls_to_rgb(h, max(0.0, min(1.0, l)), max(0.0, min(1.0, s)))
    return RGB(round(r * 255), round(g * 255), round(b * 255))


def apply_lum_mod_off(rgb: RGB, *, lum_mod: float | None, lum_off: float | None) -> RGB:
    """PowerPoint-style: convert RGB→HLS, apply lumMod (multiply L) then lumOff (add to L)."""
    h, l, s = colorsys.rgb_to_hls(rgb.r / 255.0, rgb.g / 255.0, rgb.b / 255.0)
    if lum_mod is not None:
        l *= lum_mod
    if lum_off is not None:
        l += lum_off
    return _hls_to_rgb(h, l, s)


def apply_word_tint_shade(rgb: RGB, *, tint: int | None, shade: int | None) -> RGB:
    """Word-style: themeTint (lighten toward white) / themeShade (darken toward black).

    tint in [0, 255]: 0 = no change, 255 = full white
    shade in [0, 255]: 0 = no change, 255 = full black
    Per ECMA-376: result = base + (255 - base) * (tint/255) for tint
                  result = base * (1 - shade/255) for shade
    """
    r, g, b = rgb.r, rgb.g, rgb.b
    if tint is not None and tint > 0:
        f = tint / 255.0
        r = round(r + (255 - r) * f)
        g = round(g + (255 - g) * f)
        b = round(b + (255 - b) * f)
    if shade is not None and shade > 0:
        f = 1.0 - shade / 255.0
        r = round(r * f)
        g = round(g * f)
        b = round(b * f)
    return RGB(r, g, b)


# ----- resolver ---------------------------------------------------------------------------------


@dataclass
class ThemeColorResolver:
    """Resolve theme references to effective RGB.

    Pass an optional `scheme` map (resolved from the document's theme1.xml).
    `clr_map_ovr` carries slide-master-level overrides (e.g. tx1→tx2).
    """

    scheme: dict[str, str] = None  # type: ignore[assignment]
    clr_map_ovr: dict[str, str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.scheme is None:
            self.scheme = dict(DEFAULT_SCHEME)
        if self.clr_map_ovr is None:
            self.clr_map_ovr = {}

    # --- entry points ---

    def resolve_srgb(
        self,
        hex_color: str,
        *,
        lum_mod: float | None = None,
        lum_off: float | None = None,
    ) -> RGB:
        rgb = RGB.from_hex(hex_color)
        if lum_mod is not None or lum_off is not None:
            rgb = apply_lum_mod_off(rgb, lum_mod=lum_mod, lum_off=lum_off)
        return rgb

    def resolve_scheme(
        self,
        scheme_name: str,
        *,
        lum_mod: float | None = None,
        lum_off: float | None = None,
        theme_tint: int | None = None,
        theme_shade: int | None = None,
    ) -> RGB:
        # Apply slide-master colormap override if present.
        effective = self.clr_map_ovr.get(scheme_name, scheme_name)
        hex_color = self.scheme.get(effective, "000000")
        rgb = RGB.from_hex(hex_color)
        if lum_mod is not None or lum_off is not None:
            rgb = apply_lum_mod_off(rgb, lum_mod=lum_mod, lum_off=lum_off)
        if theme_tint is not None or theme_shade is not None:
            rgb = apply_word_tint_shade(rgb, tint=theme_tint, shade=theme_shade)
        return rgb


# ----- contrast ---------------------------------------------------------------------------------


def contrast_ratio(fg: RGB, bg: RGB) -> float:
    """WCAG contrast ratio (1.0–21.0)."""
    l1 = fg.relative_luminance()
    l2 = bg.relative_luminance()
    lighter, darker = (l1, l2) if l1 >= l2 else (l2, l1)
    return (lighter + 0.05) / (darker + 0.05)
