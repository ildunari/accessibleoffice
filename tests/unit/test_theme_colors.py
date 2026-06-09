"""Unit tests for ThemeColorResolver."""

import math

from a11yfix.ooxml.theme_colors import (
    RGB,
    ThemeColorResolver,
    apply_lum_mod_off,
    apply_word_tint_shade,
    contrast_ratio,
)


def test_rgb_from_hex():
    assert RGB.from_hex("FF0000") == RGB(255, 0, 0)
    assert RGB.from_hex("#00ff00") == RGB(0, 255, 0)


def test_relative_luminance_extremes():
    assert math.isclose(RGB(0, 0, 0).relative_luminance(), 0.0)
    assert math.isclose(RGB(255, 255, 255).relative_luminance(), 1.0)


def test_contrast_black_white():
    assert math.isclose(contrast_ratio(RGB(0, 0, 0), RGB(255, 255, 255)), 21.0, abs_tol=0.01)


def test_word_tint_lightens():
    base = RGB(100, 100, 100)
    out = apply_word_tint_shade(base, tint=128, shade=None)
    assert out.r > base.r and out.g > base.g and out.b > base.b


def test_word_shade_darkens():
    base = RGB(200, 200, 200)
    out = apply_word_tint_shade(base, tint=None, shade=128)
    assert out.r < base.r and out.g < base.g and out.b < base.b


def test_word_tint_spec_example():
    # ECMA-376 §17.3.2.6 worked example: themeTint="99" (0x99 = 153) applied
    # to component 0x50 (80) yields 0x96 (150).
    out = apply_word_tint_shade(RGB(0x50, 0x50, 0x50), tint=0x99, shade=None)
    assert out == RGB(0x96, 0x96, 0x96)


def test_word_shade_spec_example():
    # ECMA-376 §17.3.2.6 worked example: themeShade="80" (0x80 = 128) applied
    # to component 0x96 (150) yields 0x4B (75).
    out = apply_word_tint_shade(RGB(0x96, 0x96, 0x96), tint=None, shade=0x80)
    assert out == RGB(0x4B, 0x4B, 0x4B)


def test_word_tint_shade_255_is_identity():
    # 0xFF means "full original color" for both attributes.
    base = RGB(100, 150, 200)
    assert apply_word_tint_shade(base, tint=255, shade=None) == base
    assert apply_word_tint_shade(base, tint=None, shade=255) == base


def test_word_tint_zero_is_white_shade_zero_is_black():
    base = RGB(100, 150, 200)
    assert apply_word_tint_shade(base, tint=0, shade=None) == RGB(255, 255, 255)
    assert apply_word_tint_shade(base, tint=None, shade=0) == RGB(0, 0, 0)


def test_lum_mod_darkens():
    base = RGB(200, 200, 200)
    out = apply_lum_mod_off(base, lum_mod=0.5, lum_off=None)
    assert out.r < base.r


def test_resolver_default_scheme():
    r = ThemeColorResolver()
    assert r.resolve_scheme("tx1") == RGB(0, 0, 0)
    assert r.resolve_scheme("bg1") == RGB(255, 255, 255)


def test_resolver_clrmapovr():
    r = ThemeColorResolver(clr_map_ovr={"tx1": "accent1"})
    assert r.resolve_scheme("tx1") == RGB.from_hex("4F81BD")
