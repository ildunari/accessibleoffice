"""OpenAI-compatible chat-completions adapter — covers OpenAI and OpenRouter
(and by extension any /v1-compatible endpoint via A11YFIX_OPENAI_BASE_URL).
The direct Anthropic path is the existing ClaudeAdapter ('anthropic' alias).
"""

from __future__ import annotations

import base64
import os
import time

import httpx

from a11yfix.ai.adapter import AltTextResult, CallUsage, LinkTextResult, SlideTitleResult
from a11yfix.ai.confidence import confidence_from_text
from a11yfix.ai.errors import AdapterCallError, AdapterUnavailable
from a11yfix.ai.prompts import (
    ALT_TEXT_SYSTEM,
    LINK_TEXT_SYSTEM,
    SLIDE_TITLE_SYSTEM,
    alt_text_user,
    link_text_user,
    slide_title_user,
)

_PROVIDERS = {
    # name: (base_url env-override, default base_url, key env var, default model,
    #        max-tokens param name)
    "openai": (
        "A11YFIX_OPENAI_BASE_URL",
        "https://api.openai.com/v1",
        "OPENAI_API_KEY",
        "gpt-5-mini",
        # OpenAI rejects max_tokens for gpt-5-family models.
        "max_completion_tokens",
    ),
    "openrouter": (
        "A11YFIX_OPENROUTER_BASE_URL",
        "https://openrouter.ai/api/v1",
        "OPENROUTER_API_KEY",
        "anthropic/claude-haiku-4.5",
        "max_tokens",
    ),
}
_TIMEOUT = 120.0
_RETRIES = 3


class DirectLLMAdapter:
    def __init__(self, *, provider: str = "openai", model: str | None = None) -> None:
        if provider not in _PROVIDERS:
            raise AdapterUnavailable(f"unknown direct-LLM provider {provider!r}")
        url_env, base_url, key_env, default_model, max_tokens_param = _PROVIDERS[provider]
        key = os.environ.get(key_env)
        if not key:
            raise AdapterUnavailable(f"{key_env} not set (required for --vlm {provider})")
        self._base_url = os.environ.get(url_env, base_url)
        self._model = model or default_model
        self._max_tokens_param = max_tokens_param
        self.name = f"{provider}:{self._model}"
        self._client = httpx.Client(
            headers={"Authorization": f"Bearer {key}"}, timeout=_TIMEOUT
        )

    def _chat(self, *, system: str, content, max_tokens: int) -> tuple[str, CallUsage]:
        body = {
            "model": self._model,
            self._max_tokens_param: max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": content},
            ],
        }
        last: Exception | None = None
        for attempt in range(_RETRIES):
            try:
                r = self._client.post(f"{self._base_url}/chat/completions", json=body)
                if r.status_code in (429, 500, 502, 503, 529):
                    last = AdapterCallError(f"HTTP {r.status_code}: {r.text[:200]}")
                    time.sleep(0.5 * 2**attempt)
                    continue
                r.raise_for_status()
                data = r.json()
                text = (data["choices"][0]["message"]["content"] or "").strip()
                u = data.get("usage") or {}
                usage = CallUsage(
                    input_tokens=int(u.get("prompt_tokens") or 0),
                    output_tokens=int(u.get("completion_tokens") or 0),
                )
                return text, usage
            except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
                last = exc
                time.sleep(0.5 * 2**attempt)
        raise AdapterCallError(f"{self.name}: {last}") from last

    def describe_image(
        self, image_bytes: bytes, *, max_chars: int, context: str
    ) -> AltTextResult:
        from a11yfix.ooxml.image_extract import ensure_vision_compatible

        send_bytes, media_type = ensure_vision_compatible(image_bytes)
        data_url = f"data:{media_type};base64,{base64.b64encode(send_bytes).decode('ascii')}"
        text, usage = self._chat(
            system=ALT_TEXT_SYSTEM,
            content=[
                {"type": "image_url", "image_url": {"url": data_url}},
                {"type": "text", "text": alt_text_user(context=context)},
            ],
            max_tokens=200,
        )
        if len(text) > max_chars:
            text = text[: max_chars - 1].rstrip()
        return AltTextResult(
            text=text,
            confidence=confidence_from_text(text, max_chars),
            model=self.name,
            usage=usage,
        )

    def suggest_link_text(self, url: str, surrounding_text: str) -> LinkTextResult:
        text, usage = self._chat(
            system=LINK_TEXT_SYSTEM,
            content=link_text_user(url=url, surrounding_text=surrounding_text),
            max_tokens=64,
        )
        text = text.strip().strip('"').strip("'")
        return LinkTextResult(
            text=text,
            confidence=confidence_from_text(text, 64),
            model=self.name,
            usage=usage,
        )

    def suggest_slide_title(self, slide_text: str, slide_layout: str) -> SlideTitleResult:
        text, usage = self._chat(
            system=SLIDE_TITLE_SYSTEM,
            content=slide_title_user(slide_text=slide_text, slide_layout=slide_layout),
            max_tokens=64,
        )
        text = text.strip().strip('"').strip("'")
        return SlideTitleResult(
            text=text,
            confidence=confidence_from_text(text, 80),
            model=self.name,
            usage=usage,
        )
