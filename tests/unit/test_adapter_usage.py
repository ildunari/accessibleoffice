from types import SimpleNamespace

from a11yfix.ai.adapter import AltTextResult, CallUsage
from a11yfix.ai.agent_sdk_adapter import _usage_from_tokens
from a11yfix.ai.claude_adapter import _usage_from_message
from a11yfix.ai.confidence import confidence_from_text


def test_usage_defaults_none():
    r = AltTextResult(text="a chart", confidence=0.85, model="m")
    assert r.usage is None


def test_usage_carries_cost():
    u = CallUsage(input_tokens=100, output_tokens=20, cost_usd=0.0042)
    r = AltTextResult(text="a chart", confidence=0.85, model="m", usage=u)
    assert r.usage.cost_usd == 0.0042


def test_confidence_heuristic_matches_claude_adapter():
    # Extracted verbatim from ClaudeAdapter._confidence_from_text
    assert confidence_from_text("", 125) == 0.0
    assert confidence_from_text("UNCLEAR", 125) == 0.95
    assert confidence_from_text("x" * 200, 125) == 0.4
    assert confidence_from_text("a bar chart of Q3 revenue", 125) == 0.85


# --- usage extractors: cache-token fidelity (F2) + defensive parsing (F3) ---


def test_agent_sdk_token_fallback_carries_cache_tokens():
    u = _usage_from_tokens(
        {
            "input_tokens": 100,
            "output_tokens": 20,
            "cache_read_input_tokens": 5_000,
            "cache_creation_input_tokens": 300,
        }
    )
    assert u == CallUsage(
        input_tokens=100,
        output_tokens=20,
        cache_read_tokens=5_000,
        cache_creation_tokens=300,
    )


def test_agent_sdk_token_fallback_malformed_payload_yields_none():
    assert _usage_from_tokens({"input_tokens": "not-a-number"}) is None
    assert _usage_from_tokens({"input_tokens": ["junk"]}) is None
    assert _usage_from_tokens("not a dict") is None


def test_claude_usage_from_message_carries_cache_tokens():
    msg = SimpleNamespace(
        usage=SimpleNamespace(
            input_tokens=100,
            output_tokens=20,
            cache_read_input_tokens=5_000,
            cache_creation_input_tokens=300,
        )
    )
    assert _usage_from_message(msg) == CallUsage(
        input_tokens=100,
        output_tokens=20,
        cache_read_tokens=5_000,
        cache_creation_tokens=300,
    )


def test_claude_usage_from_message_malformed_payload_yields_none():
    msg = SimpleNamespace(usage=SimpleNamespace(input_tokens="garbage", output_tokens=20))
    assert _usage_from_message(msg) is None
