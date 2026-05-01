"""Default Anthropic Claude implementation of VLMAdapter."""

from __future__ import annotations

import base64
import os
from typing import Any

from a11yfix.ai.adapter import (
    AltTextResult,
    LinkTextResult,
    SlideTitleResult,
)
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
        if not text:
            return 0.0
        if "UNCLEAR" in text or "DECORATIVE" in text:
            return 0.95  # explicit signal — actually high confidence in saying "I don't know"
        # If the model returned overly long text, low confidence.
        if len(text) > max_chars * 1.5:
            return 0.4
        return 0.85

    # --- alt text ---
    def describe_image(self, image_bytes: bytes, *, max_chars: int, context: str) -> AltTextResult:
        b64 = base64.b64encode(image_bytes).decode("ascii")
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
                                "media_type": "image/png",
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
        )

    @staticmethod
    def _extract_text(msg: Any) -> str:
        # anthropic 0.40+ API
        for block in getattr(msg, "content", []) or []:
            if getattr(block, "type", None) == "text":
                return getattr(block, "text", "") or ""
        return ""
