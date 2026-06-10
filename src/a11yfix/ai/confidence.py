"""Shared output-shape confidence heuristic (was ClaudeAdapter._confidence_from_text)."""

from __future__ import annotations


def confidence_from_text(text: str, max_chars: int) -> float:
    if not text:
        return 0.0
    if "UNCLEAR" in text or "DECORATIVE" in text:
        return 0.95  # explicit signal — high confidence in saying "I don't know"
    if len(text) > max_chars * 1.5:
        return 0.4
    return 0.85
