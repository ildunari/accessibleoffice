"""OpenAI Codex CLI as a stage-3 VLM backend, via `codex exec --json`.

Non-interactive and sandboxed read-only: `codex exec --json --ephemeral
--skip-git-repo-check --color never -s read-only [-m model] [-i img]
"<prompt>"`. `exec` has no system-prompt flag, so the system prompt is
prepended to the user prompt. Auth is whatever the user configured via
`codex login` — nothing to plumb here.

The --json JSONL stream carries the answer in
{"type":"item.completed","item":{"type":"agent_message","text":...}} and
token usage in {"type":"turn.completed","usage":{...}}. Codex reports
tokens but no dollar cost, so cost_usd stays None and CostMeter's
estimator prices the call from the gpt-* rows.

Caveat: each `codex exec` call carries ~27k input tokens of agent-harness
overhead (instructions, tool schemas), so per-call input cost is dominated
by the harness, not the prompt. Cheap default model for that reason.
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

_DEFAULT_MODEL = "gpt-5.4-mini"


class CodexAdapter:
    def __init__(self, *, model: str | None = None) -> None:
        require_binary("codex", hint="npm install -g @openai/codex")
        self._model = model or _DEFAULT_MODEL
        self.name = f"codex:{model}" if model else "codex"

    def _call(
        self, *, system: str, user: str, image_path: Path | None = None
    ) -> tuple[str, CallUsage]:
        cmd = [
            "codex", "exec", "--json", "--ephemeral", "--skip-git-repo-check",
            "--color", "never", "-s", "read-only", "-m", self._model,
        ]
        if image_path is not None:
            # --image is multi-value greedy in clap; =-form keeps it to one
            # value so the positional prompt survives
            cmd.append(f"--image={image_path}")
        cmd.append(f"{system}\n\n{user}")
        stdout = run_cli(cmd)
        text, usage = "", CallUsage()
        for ev in jsonl_events(stdout):
            etype = ev.get("type")
            if etype == "turn.failed":
                err = ev.get("error")
                msg = err.get("message") if isinstance(err, dict) else err
                raise AdapterCallError(f"codex: turn failed: {msg or 'unknown error'}")
            if etype == "item.completed":
                item = ev.get("item")
                if (
                    isinstance(item, dict)
                    and item.get("type") == "agent_message"
                    and isinstance(item.get("text"), str)
                ):
                    text = item["text"].strip()  # last agent message wins
            elif etype == "turn.completed":
                usage = _usage_from_turn(ev)
        if not text:
            raise AdapterCallError("codex: no agent_message in --json output")
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
            # Bare model id (not self.name): cost_usd is None so CostMeter's
            # estimator looks this string up in its gpt-* pricing rows.
            model=self._model,
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
            model=self._model,
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
            model=self._model,
            usage=usage,
        )


def _usage_from_turn(ev: dict) -> CallUsage:
    """Defensive: junk usage payloads cost only the metering, never the fix."""
    u = ev.get("usage") or {}
    try:
        # cached_input_tokens is a subset of input_tokens — split so the
        # estimator prices the cached portion at 10%, not 110%.
        cached = int(u.get("cached_input_tokens") or 0)
        return CallUsage(
            input_tokens=max(0, int(u.get("input_tokens") or 0) - cached),
            output_tokens=int(u.get("output_tokens") or 0),
            cache_read_tokens=cached,
            cost_usd=None,  # codex exec reports tokens only; estimator prices them
        )
    except (TypeError, ValueError, AttributeError):
        return CallUsage()
