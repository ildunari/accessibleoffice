import pytest

from a11yfix.ai.errors import AdapterUnavailable
from a11yfix.ai.registry import backend_names, create_adapter


def test_initial_backends_registered():
    assert {"claude", "claude-api", "anthropic"} <= set(backend_names())


def test_direct_llm_backends_registered():
    assert {"openai", "openrouter"} <= set(backend_names())


def test_unknown_backend_lists_valid():
    with pytest.raises(AdapterUnavailable) as exc:
        create_adapter("nope")
    assert "claude" in str(exc.value)  # message names valid backends (F7)


def test_unavailable_backend_raises(monkeypatch):
    import a11yfix.ai.registry as reg

    def boom(model):
        raise AdapterUnavailable("anthropic package not installed")

    monkeypatch.setitem(reg._BACKENDS, "claude-api", boom)
    with pytest.raises(AdapterUnavailable):
        create_adapter("claude-api")


def test_pi_backend_registered():
    assert "pi" in backend_names()


def test_opencode_backend_registered():
    assert "opencode" in backend_names()


def test_codex_backend_registered():
    assert "codex" in backend_names()
