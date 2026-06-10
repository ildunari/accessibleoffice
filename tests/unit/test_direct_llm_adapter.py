import json

import httpx
import pytest

from a11yfix.ai.direct_llm_adapter import DirectLLMAdapter
from a11yfix.ai.errors import AdapterCallError, AdapterUnavailable

PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753"
    "de0000000c4944415408d763f8cfc00000030101009a9c181b0000000049454e44ae426082"
)


def _adapter(monkeypatch, handler, provider="openai"):
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    a = DirectLLMAdapter(provider=provider)
    a._client = httpx.Client(transport=httpx.MockTransport(handler))
    return a


def _ok(text="A bar chart", pt=100, ct=10):
    def handler(request):
        return httpx.Response(200, json={
            "choices": [{"message": {"content": text}}],
            "usage": {"prompt_tokens": pt, "completion_tokens": ct},
        })
    return handler


def test_missing_key_unavailable(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(AdapterUnavailable):
        DirectLLMAdapter(provider="openai")


def test_describe_image_returns_usage(monkeypatch):
    a = _adapter(monkeypatch, _ok())
    res = a.describe_image(PNG_1PX, max_chars=125, context="Shape: chart1")
    assert res.text == "A bar chart"
    assert res.usage.input_tokens == 100 and res.usage.output_tokens == 10
    assert res.usage.cost_usd is None  # estimator fallback


def test_image_sent_as_data_url(monkeypatch):
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return _ok()(request)

    a = _adapter(monkeypatch, handler)
    a.describe_image(PNG_1PX, max_chars=125, context="ctx")
    img = seen["body"]["messages"][1]["content"][0]
    assert img["type"] == "image_url"
    assert img["image_url"]["url"].startswith("data:image/png;base64,")


def test_http_error_raises_call_error(monkeypatch):
    monkeypatch.setattr("a11yfix.ai.direct_llm_adapter.time.sleep", lambda s: None)
    a = _adapter(monkeypatch, lambda r: httpx.Response(500, text="boom"))
    with pytest.raises(AdapterCallError):
        a.suggest_link_text(url="https://x.test", surrounding_text="see")


def test_4xx_fails_fast_no_retry(monkeypatch):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(401, text="bad key")

    a = _adapter(monkeypatch, handler)
    with pytest.raises(AdapterCallError):
        a.suggest_link_text(url="https://x.test", surrounding_text="see")
    assert len(calls) == 1


def test_malformed_body_fails_fast_no_retry(monkeypatch):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(200, json={"nope": 1})

    a = _adapter(monkeypatch, handler)
    with pytest.raises(AdapterCallError):
        a.suggest_link_text(url="https://x.test", surrounding_text="see")
    assert len(calls) == 1


def test_openrouter_base_url(monkeypatch):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return _ok()(request)

    a = _adapter(monkeypatch, handler, provider="openrouter")
    a.suggest_slide_title(slide_text="t", slide_layout="l")
    assert seen["url"].startswith("https://openrouter.ai/api/v1/")
