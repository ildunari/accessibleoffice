"""OpenCode coding agent (opencode.ai) as a stage-3 VLM backend.

Non-interactive: `opencode run --format json [-m provider/model] [-f img]
"<prompt>"`. `run` has no system-prompt flag, so the system prompt is
prepended to the user prompt. Auth is whatever the user configured via
`opencode auth login` — nothing to plumb here.

The --format json event envelope is the least-pinned of the agent CLIs, so
the parser is deliberately tolerant: for each JSONL event it looks in the
event itself, its "part", and its "info" for text parts and usage fields.
"""

from __future__ import annotations

from pathlib import Path

from a11yfix.ai.adapter import AltTextResult, CallUsage, LinkTextResult, SlideTitleResult
from a11yfix.ai.agent_cli import jsonl_events, require_binary, run_cli, temp_image
from a11yfix.ai.confidence import confidence_from_text
from a11yfix.ai.errors import AdapterCallError
from a11yfix.ai.prompts import (
    ALT_TEXT_SYSTEM,
    LINK_TEXT_SYSTEM,
    SLIDE_TITLE_SYSTEM,
    alt_text_user,
    link_text_user,
    slide_title_user,
)


class OpenCodeAdapter:
    def __init__(self, *, model: str | None = None) -> None:
        require_binary("opencode", hint="curl -fsSL https://opencode.ai/install | bash")
        self._model = model
        self.name = f"opencode:{model}" if model else "opencode"

    def _call(
        self, *, system: str, user: str, image_path: Path | None = None
    ) -> tuple[str, CallUsage]:
        cmd = ["opencode", "run", "--format", "json"]
        if self._model:
            cmd += ["-m", self._model]
        if image_path is not None:
            cmd += ["-f", str(image_path)]
        cmd.append(f"{system}\n\n{user}")
        stdout = run_cli(cmd)
        text, usage = _parse_events(stdout)
        if not text:
            raise AdapterCallError("opencode: no text in --format json output")
        return text, usage

    def describe_image(
        self, image_bytes: bytes, *, max_chars: int, context: str
    ) -> AltTextResult:
        with temp_image(image_bytes) as p:
            text, usage = self._call(
                system=ALT_TEXT_SYSTEM, user=alt_text_user(context=context), image_path=p
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
        text, usage = self._call(
            system=LINK_TEXT_SYSTEM,
            user=link_text_user(url=url, surrounding_text=surrounding_text),
        )
        text = text.strip().strip('"').strip("'")
        return LinkTextResult(
            text=text,
            confidence=confidence_from_text(text, 64),
            model=self.name,
            usage=usage,
        )

    def suggest_slide_title(self, slide_text: str, slide_layout: str) -> SlideTitleResult:
        text, usage = self._call(
            system=SLIDE_TITLE_SYSTEM,
            user=slide_title_user(slide_text=slide_text, slide_layout=slide_layout),
        )
        text = text.strip().strip('"').strip("'")
        return SlideTitleResult(
            text=text,
            confidence=confidence_from_text(text, 80),
            model=self.name,
            usage=usage,
        )


def _parse_events(stdout: str) -> tuple[str, CallUsage]:
    """Tolerant parse of opencode --format json events.

    Text: OpenCode streams cumulative partial-text updates, so de-duplicate —
    if a new text part extends the accumulated text (or vice versa) keep the
    longer one; only genuinely new text is appended.
    Usage: numeric `cost` + `tokens` dict, last one wins.
    """
    text, usage = "", CallUsage()
    for ev in jsonl_events(stdout):
        for cand in (ev, ev.get("part"), ev.get("info")):
            if not isinstance(cand, dict):
                continue
            try:
                if cand.get("type") == "text" and isinstance(cand.get("text"), str):
                    part = cand["text"]
                    if part.startswith(text):
                        text = part  # cumulative update supersedes
                    elif not text.startswith(part):
                        text += part  # genuinely new fragment
                cost = cand.get("cost")
                tokens = cand.get("tokens")
                cost_ok = isinstance(cost, (int, float)) and not isinstance(cost, bool)
                if cost_ok or isinstance(tokens, dict):
                    tokens = tokens if isinstance(tokens, dict) else {}
                    usage = CallUsage(
                        input_tokens=int(tokens.get("input") or 0),
                        output_tokens=int(tokens.get("output") or 0),
                        cost_usd=float(cost) if cost_ok else None,
                    )
            except (TypeError, ValueError, AttributeError):
                continue  # junk event costs only its metering, never the fix
    return text.strip(), usage
