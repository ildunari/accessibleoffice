"""VLM adapter protocol — pluggable AI backend for stage-3 fixes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class CallUsage:
    """Per-call token/cost report. cost_usd is authoritative backend-reported
    USD when available (Pi, OpenCode, Agent SDK); None means 'estimate from
    tokens via CostMeter pricing'."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    cost_usd: float | None = None


@dataclass
class AltTextResult:
    text: str
    confidence: float
    model: str
    usage: CallUsage | None = None


@dataclass
class LinkTextResult:
    text: str
    confidence: float
    model: str
    usage: CallUsage | None = None


@dataclass
class SlideTitleResult:
    text: str
    confidence: float
    model: str
    usage: CallUsage | None = None


class VLMAdapter(Protocol):
    name: str

    def describe_image(
        self, image_bytes: bytes, *, max_chars: int, context: str
    ) -> AltTextResult: ...

    def suggest_link_text(self, url: str, surrounding_text: str) -> LinkTextResult: ...

    def suggest_slide_title(self, slide_text: str, slide_layout: str) -> SlideTitleResult: ...
