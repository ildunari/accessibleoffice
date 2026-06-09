"""Tests for image format sniffing, vision-API compatibility, and zip-path
normalization in image_extract, plus adapter-side image preparation."""

import io

import pytest

from a11yfix.ai.agent_sdk_adapter import _prepare_image_for_read
from a11yfix.ooxml.image_extract import (
    _strip_rel_prefixes,
    ensure_vision_compatible,
    sniff_image,
)


def _png_bytes() -> bytes:
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (4, 4), "red").save(out, format="PNG")
    return out.getvalue()


def _bmp_bytes() -> bytes:
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (4, 4), "blue").save(out, format="BMP")
    return out.getvalue()


def _jpeg_bytes() -> bytes:
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (4, 4), "green").save(out, format="JPEG")
    return out.getvalue()


# ---- sniff_image -------------------------------------------------------------


def test_sniff_png_jpeg_bmp():
    assert sniff_image(_png_bytes()) == "image/png"
    assert sniff_image(_jpeg_bytes()) == "image/jpeg"
    assert sniff_image(_bmp_bytes()) == "image/bmp"


def test_sniff_gif_webp_tiff():
    assert sniff_image(b"GIF89a" + b"\x00" * 10) == "image/gif"
    assert sniff_image(b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 4) == "image/webp"
    assert sniff_image(b"II*\x00" + b"\x00" * 10) == "image/tiff"


def test_sniff_emf_wmf_svg():
    emf = b"\x01\x00\x00\x00" + b"\x00" * 36 + b" EMF" + b"\x00" * 10
    assert sniff_image(emf) == "image/x-emf"
    assert sniff_image(b"\xd7\xcd\xc6\x9a" + b"\x00" * 10) == "image/x-wmf"
    assert sniff_image(b'<svg xmlns="http://www.w3.org/2000/svg"/>') == "image/svg+xml"


def test_sniff_unknown_and_empty():
    assert sniff_image(b"") is None
    assert sniff_image(b"this is not an image at all") is None


# ---- ensure_vision_compatible -------------------------------------------------


def test_vision_compatible_png_passthrough():
    data = _png_bytes()
    out, media = ensure_vision_compatible(data)
    assert out == data
    assert media == "image/png"


def test_vision_compatible_bmp_converted_to_png():
    out, media = ensure_vision_compatible(_bmp_bytes())
    assert media == "image/png"
    assert sniff_image(out) == "image/png"


def test_vision_compatible_emf_rejected():
    emf = b"\x01\x00\x00\x00" + b"\x00" * 36 + b" EMF" + b"\x00" * 10
    with pytest.raises(ValueError, match="unsupported image format"):
        ensure_vision_compatible(emf)


def test_vision_compatible_garbage_rejected():
    with pytest.raises(ValueError):
        ensure_vision_compatible(b"not an image")


# ---- _prepare_image_for_read ---------------------------------------------------


def test_prepare_image_correct_suffixes():
    _, suffix = _prepare_image_for_read(_png_bytes())
    assert suffix == ".png"
    _, suffix = _prepare_image_for_read(_jpeg_bytes())
    assert suffix == ".jpg"
    _, suffix = _prepare_image_for_read(b"GIF89a" + b"\x00" * 32)
    assert suffix == ".gif"


def test_prepare_image_rejects_metafiles():
    wmf = b"\xd7\xcd\xc6\x9a" + b"\x00" * 32
    with pytest.raises(ValueError, match="unsupported image format"):
        _prepare_image_for_read(wmf)


def test_prepare_image_converts_bmp():
    data, suffix = _prepare_image_for_read(_bmp_bytes())
    # BMP is not Read-tool friendly; it gets re-encoded.
    assert suffix in (".jpg", ".png")
    assert sniff_image(data) in ("image/jpeg", "image/png")


# ---- _strip_rel_prefixes -------------------------------------------------------


def test_strip_rel_prefixes():
    assert _strip_rel_prefixes("../media/image1.png") == "media/image1.png"
    assert _strip_rel_prefixes("./media/image1.png") == "media/image1.png"
    assert _strip_rel_prefixes(".././media/x.png") == "media/x.png"
    # Characters are NOT stripped — only whole path segments.
    assert _strip_rel_prefixes("...dots.png") == "...dots.png"
    assert _strip_rel_prefixes("media/image1.png") == "media/image1.png"


# ---- per-stage backup names ----------------------------------------------------


def test_officecli_backup_suffix(tmp_path):
    from a11yfix.ooxml.officecli import OfficecliClient

    f = tmp_path / "doc.docx"
    f.write_bytes(b"fake")
    c2 = OfficecliClient(f)
    c2._snapshot()
    c3 = OfficecliClient(f, backup_suffix=".stage3.bak")
    c3._snapshot()
    assert c2.backup_path != c3.backup_path
    assert c2.backup_path.name == "doc.docx.bak"
    assert c3.backup_path.name == "doc.docx.stage3.bak"
    assert c2.backup_path.exists() and c3.backup_path.exists()


# ---- _defer must not clobber detection-set reasons -----------------------------


def test_defer_preserves_existing_reason():
    from a11yfix.fixers.single_shot import _defer
    from a11yfix.manifest import FileFormat, Finding, Severity  # noqa: F401

    f = Finding(
        id="x",
        rule_id="alt-text-missing",
        severity=Severity.ERROR,
        wcag_sc=["1.1.1"],
        officecli_path="/x",
        current_value="",
        plain_impact="",
        why_human_needed="detection-time reason",
    )
    deferred: list = []
    _defer(f, deferred, "stage-3 deferred: whatever")
    assert f.why_human_needed == "detection-time reason"
    assert deferred == [f]

    f.why_human_needed = ""
    _defer(f, deferred, "stage-3 deferred: filled in")
    assert f.why_human_needed == "stage-3 deferred: filled in"
