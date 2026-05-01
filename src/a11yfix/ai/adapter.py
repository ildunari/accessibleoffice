"""VLM adapter protocol — pluggable AI backend for stage-3 fixes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class AltTextResult:
    text: str
    confidence: float
    model: str


@dataclass
class LinkTextResult:
    text: str
    confidence: float
    model: str


@dataclass
class SlideTitleResult:
    text: str
    confidence: float
    model: str


class VLMAdapter(Protocol):
    name: str

    def describe_image(
        self, image_bytes: bytes, *, max_chars: int, context: str
    ) -> AltTextResult: ...

    def suggest_link_text(self, url: str, surrounding_text: str) -> LinkTextResult: ...

    def suggest_slide_title(self, slide_text: str, slide_layout: str) -> SlideTitleResult: ...
