"""Unit-test-wide fixtures."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated_a11yfix_cache(tmp_path, monkeypatch):
    """Keep the single-shot AI cache out of ~/.cache for every unit test.

    CACHE_DIR is a module global read at call time by _cache_key/_cache_put,
    so setattr is sufficient; the env var is set too so any subprocess or
    re-import resolves to the same isolated directory. Tests that patch
    CACHE_DIR themselves still win — their monkeypatch runs after this one.
    """
    from a11yfix.fixers import single_shot

    monkeypatch.setattr(single_shot, "CACHE_DIR", tmp_path / "a11yfix-cache")
    monkeypatch.setenv("A11YFIX_CACHE", str(tmp_path / "a11yfix-cache"))
