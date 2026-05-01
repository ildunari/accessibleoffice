"""Prompts for single-shot AI fixes. Constrained to one-shot calls."""

from __future__ import annotations

ALT_TEXT_SYSTEM = (
    "You write accessibility alt text for images embedded in Office documents. "
    "Output ONLY the alt text — no preface, no quotes, no labels. "
    "Hard limit: 125 characters. "
    "Do not start with 'image of', 'picture of', 'photo of', or similar redundant phrases. "
    "Do not include surrounding punctuation. "
    "If the image is purely decorative, output the literal token: DECORATIVE"
)

LINK_TEXT_SYSTEM = (
    "You replace generic hyperlink text (like 'click here') with descriptive text. "
    "Read the URL and the surrounding paragraph context, then output ONLY the replacement text. "
    "Aim for 2-6 words. Be specific about the destination. No quotes, no preface, no punctuation."
)

SLIDE_TITLE_SYSTEM = (
    "You write concise accessibility slide titles for PowerPoint slides. "
    "Read the slide content and output ONLY a title — no preface, no quotes, no punctuation. "
    "8 words max. Title-case. If you cannot infer a meaningful title, output the literal token: UNCLEAR"
)


def alt_text_user(*, context: str) -> str:
    return f"Context: {context}\n\nWrite alt text for the attached image."


def link_text_user(*, url: str, surrounding_text: str) -> str:
    return f"URL: {url}\nSurrounding paragraph: {surrounding_text}\n\nReplacement link text:"


def slide_title_user(*, slide_text: str, slide_layout: str) -> str:
    return f"Slide layout: {slide_layout}\n\nSlide content:\n{slide_text}\n\nProposed title:"
