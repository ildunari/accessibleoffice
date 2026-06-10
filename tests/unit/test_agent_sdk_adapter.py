"""Unit tests for Claude Code SDK image payload handling."""

from __future__ import annotations

import io

from PIL import Image  # type: ignore[import-untyped]

from a11yfix.ai import agent_sdk_adapter


def _png(size: tuple[int, int], color: str = "blue") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color=color).save(buf, format="PNG")
    return buf.getvalue()


def _noisy_png(size: tuple[int, int]) -> bytes:
    buf = io.BytesIO()
    Image.effect_noise(size, 100).convert("RGB").save(buf, format="PNG")
    return buf.getvalue()


def test_prepare_image_for_read_preserves_small_png():
    img = _png((40, 40))

    data, suffix = agent_sdk_adapter._prepare_image_for_read(img)

    assert data == img
    assert suffix == ".png"


def test_prepare_image_for_read_compresses_large_png_under_limit():
    img = _noisy_png((3000, 2200))
    assert len(img) > agent_sdk_adapter.MAX_READ_IMAGE_BYTES

    data, suffix = agent_sdk_adapter._prepare_image_for_read(img)

    assert suffix == ".jpg"
    assert len(data) <= agent_sdk_adapter.MAX_READ_IMAGE_BYTES
    with Image.open(io.BytesIO(data)) as im:
        assert max(im.size) <= agent_sdk_adapter.MAX_READ_IMAGE_DIMENSION


def test_missing_package_raises_adapter_unavailable(monkeypatch):
    """Registry contract: factories raise AdapterUnavailable from the
    constructor when the backend can't run (F1). No `claude` CLI probe is
    needed — claude-agent-sdk ships a bundled CLI and prefers it over PATH."""
    import builtins

    import pytest

    from a11yfix.ai.errors import AdapterUnavailable

    real_import = builtins.__import__

    def no_sdk(name, *args, **kwargs):
        if name == "claude_agent_sdk":
            raise ImportError("No module named 'claude_agent_sdk'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_sdk)
    with pytest.raises(AdapterUnavailable, match="claude-agent-sdk not installed"):
        agent_sdk_adapter.ClaudeAgentSDKAdapter()


def test_describe_image_maps_sdk_payload_error_to_clean_runtime_error(monkeypatch):
    adapter = object.__new__(agent_sdk_adapter.ClaudeAgentSDKAdapter)
    adapter._model = "fake-model"

    def fail_run(*args, **kwargs):
        raise RuntimeError("Failed to decode JSON: JSON message exceeded maximum buffer size")

    monkeypatch.setattr(adapter, "_run", fail_run)

    try:
        adapter.describe_image(_png((40, 40)), max_chars=125, context="Shape: chart")
    except RuntimeError as exc:
        assert str(exc) == "Claude Code SDK image payload exceeded its message buffer"
    else:
        raise AssertionError("expected RuntimeError")
