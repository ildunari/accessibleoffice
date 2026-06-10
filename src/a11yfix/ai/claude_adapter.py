"""Default Anthropic Claude implementation of VLMAdapter."""

from __future__ import annotations

import base64
import os
from typing import Any

from a11yfix.ai.adapter import (
    AltTextResult,
    CallUsage,
    LinkTextResult,
    SlideTitleResult,
)
from a11yfix.ai.confidence import confidence_from_text
from a11yfix.ai.prompts import (
    ALT_TEXT_SYSTEM,
    LINK_TEXT_SYSTEM,
    SLIDE_TITLE_SYSTEM,
    alt_text_user,
    link_text_user,
    slide_title_user,
)

DEFAULT_MODEL = "claude-haiku-4-5-20251001"  # cheap default for single-shot calls


class ClaudeAdapter:
    name = "claude"

    def __init__(self, *, model: str = DEFAULT_MODEL, api_key: str | None = None) -> None:
        try:
            import anthropic  # type: ignore[import-untyped]
        except ImportError as exc:
            raise RuntimeError("anthropic package not installed") from exc
        self._anthropic = anthropic
        self._client = anthropic.Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))
        self._model = model

    def _confidence_from_text(self, text: str, max_chars: int) -> float:
        return confidence_from_text(text, max_chars)

    # --- alt text ---
    def describe_image(self, image_bytes: bytes, *, max_chars: int, context: str) -> AltTextResult:
        from a11yfix.ooxml.image_extract import ensure_vision_compatible

        # Sniff the real format: the API only accepts png/jpeg/gif/webp and
        # rejects declared-type/byte mismatches with a 400. Raises ValueError
        # for unconvertible formats (EMF/WMF/SVG) — caller defers the finding.
        send_bytes, media_type = ensure_vision_compatible(image_bytes)
        b64 = base64.b64encode(send_bytes).decode("ascii")
        msg = self._client.messages.create(
            model=self._model,
            max_tokens=200,
            system=ALT_TEXT_SYSTEM,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": b64,
                            },
                        },
                        {"type": "text", "text": alt_text_user(context=context)},
                    ],
                }
            ],
        )
        text = self._extract_text(msg).strip()
        if len(text) > max_chars:
            text = text[: max_chars - 1].rstrip()
        return AltTextResult(
            text=text,
            confidence=self._confidence_from_text(text, max_chars),
            model=self._model,
            usage=_usage_from_message(msg),
        )

    def suggest_link_text(self, url: str, surrounding_text: str) -> LinkTextResult:
        msg = self._client.messages.create(
            model=self._model,
            max_tokens=64,
            system=LINK_TEXT_SYSTEM,
            messages=[
                {
                    "role": "user",
                    "content": link_text_user(url=url, surrounding_text=surrounding_text),
                }
            ],
        )
        text = self._extract_text(msg).strip().strip('"').strip("'")
        return LinkTextResult(
            text=text,
            confidence=self._confidence_from_text(text, max_chars=64),
            model=self._model,
            usage=_usage_from_message(msg),
        )

    def suggest_slide_title(self, slide_text: str, slide_layout: str) -> SlideTitleResult:
        msg = self._client.messages.create(
            model=self._model,
            max_tokens=64,
            system=SLIDE_TITLE_SYSTEM,
            messages=[
                {
                    "role": "user",
                    "content": slide_title_user(slide_text=slide_text, slide_layout=slide_layout),
                }
            ],
        )
        text = self._extract_text(msg).strip().strip('"').strip("'")
        return SlideTitleResult(
            text=text,
            confidence=self._confidence_from_text(text, max_chars=80),
            model=self._model,
            usage=_usage_from_message(msg),
        )

    @staticmethod
    def _extract_text(msg: Any) -> str:
        # anthropic 0.40+ API
        for block in getattr(msg, "content", []) or []:
            if getattr(block, "type", None) == "text":
                return getattr(block, "text", "") or ""
        return ""


def _usage_from_message(msg: Any) -> CallUsage | None:
    """Token counts from the Anthropic response; the pipeline estimates USD.

    Malformed payloads yield None (the call goes unmetered) rather than an
    exception that would defer a finding whose model call already succeeded.
    """
    usage = getattr(msg, "usage", None)
    if usage is None:
        return None
    try:
        return CallUsage(
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            cache_read_tokens=int(getattr(usage, "cache_read_input_tokens", 0) or 0),
            cache_creation_tokens=int(getattr(usage, "cache_creation_input_tokens", 0) or 0),
        )
    except (TypeError, ValueError):
        return None
