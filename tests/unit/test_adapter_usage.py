from a11yfix.ai.adapter import AltTextResult, CallUsage
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
