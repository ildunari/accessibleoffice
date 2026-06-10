"""Cost recording is the pipeline's job, not the adapter's (one policy, F4)."""
from pathlib import Path

from a11yfix.ai.adapter import AltTextResult, CallUsage
from a11yfix.cost_meter import CostMeter
from a11yfix.fixers.single_shot import _record_usage


def test_authoritative_cost_recorded(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("A11YFIX_STATE_DIR", str(tmp_path))
    res = AltTextResult(text="t", confidence=0.9, model="some-backend",
                        usage=CallUsage(cost_usd=0.01))
    _record_usage(res)
    assert abs(CostMeter.from_env().total() - 0.01) < 1e-9


def test_token_estimate_fallback(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("A11YFIX_STATE_DIR", str(tmp_path))
    res = AltTextResult(text="t", confidence=0.9, model="unknown-model",
                        usage=CallUsage(input_tokens=1_000_000, output_tokens=0))
    _record_usage(res)
    # falls back to _DEFAULT_PRICE_INPUT = 3.0 USD/M
    assert abs(CostMeter.from_env().total() - 3.0) < 1e-6


def test_no_usage_is_noop(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("A11YFIX_STATE_DIR", str(tmp_path))
    _record_usage(AltTextResult(text="t", confidence=0.9, model="m"))
    assert CostMeter.from_env().total() == 0.0
