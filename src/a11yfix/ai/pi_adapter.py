"""Pi coding agent (pi.dev) as a stage-3 VLM backend, via `pi --mode json`.

Pi is run as a pure completion endpoint: --no-tools --no-session
--no-context-files. Auth (Claude Max / ChatGPT / Copilot OAuth, API keys)
is whatever the user configured via `pi /login` — nothing to plumb here.
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


class PiAdapter:
    def __init__(self, *, model: str | None = None) -> None:
        require_binary("pi", hint="npm install -g @earendil-works/pi-coding-agent")
        self._model = model
        self.name = f"pi:{model}" if model else "pi"

    def _call(
        self, *, system: str, user: str, image_path: Path | None = None
    ) -> tuple[str, CallUsage]:
        cmd = [
            "pi", "--mode", "json", "--no-session", "--no-context-files",
            "--no-tools", "--system-prompt", system,
        ]
        if self._model:
            cmd += ["--model", self._model]
        if image_path is not None:
            cmd.append(f"@{image_path}")
        cmd.append(user)
        stdout = run_cli(cmd)
        text, usage = "", CallUsage()
        for ev in jsonl_events(stdout):
            if ev.get("type") != "message_end":
                continue
            msg = ev.get("message") or {}
            if msg.get("role") != "assistant":
                continue
            parts = [
                p.get("text", "")
                for p in (msg.get("content") or [])
                if isinstance(p, dict) and p.get("type") == "text"
            ]
            text = "\n".join(p for p in parts if p).strip()
            usage = _usage_from_message(msg)
        if not text:
            raise AdapterCallError("pi: no assistant message in --mode json output")
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


def _usage_from_message(msg: dict) -> CallUsage:
    """Defensive: junk usage payloads cost only the metering, never the fix."""
    u = msg.get("usage") or {}
    try:
        cost = (u.get("cost") or {}).get("total")
        return CallUsage(
            input_tokens=int(u.get("input") or 0),
            output_tokens=int(u.get("output") or 0),
            cost_usd=float(cost) if cost is not None else None,
        )
    except (TypeError, ValueError, AttributeError):
        return CallUsage()
