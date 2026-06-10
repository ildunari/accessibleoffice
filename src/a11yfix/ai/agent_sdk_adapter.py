"""VLMAdapter that uses claude-agent-sdk (Claude Code OAuth session).

No ANTHROPIC_API_KEY required — auth comes from the local Claude Code login
stored in the macOS keychain ("Claude Code-credentials").

Vision: `query()` is text-only, but Claude Code can read files via its Read
tool, so we write image bytes to a temp PNG and reference it in the prompt.

Resilience:
  - Records `ResultMessage.total_cost_usd` into the batch CostMeter (if env
    var `A11YFIX_STATE_DIR` is set).
  - Detects `RateLimitEvent` mid-stream and applies exponential backoff
    (0.5s/1s/2s/4s, max 4 retries) before re-issuing.
  - Catches CLIConnectionError / ProcessError and treats them as transient.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import random
import tempfile
import time
from pathlib import Path
from typing import Any

from a11yfix.ai.adapter import (
    AltTextResult,
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

DEFAULT_MODEL = "claude-sonnet-4-6"
MAX_RETRIES = 4
BASE_BACKOFF_SEC = 0.5
MAX_READ_IMAGE_BYTES = 750_000
MAX_READ_IMAGE_DIMENSION = 1600


class RateLimitedError(Exception):
    """All retries exhausted on a rate-limited call."""


class ClaudeAgentSDKAdapter:
    """Single-shot AI adapter backed by the Claude Code OAuth session."""

    name = "claude-agent-sdk"
    supports_vision = True  # via Read-tool path on a temp file

    def __init__(self, *, model: str = DEFAULT_MODEL) -> None:
        try:
            import claude_agent_sdk  # noqa: F401
        except ImportError as exc:
            raise RuntimeError(
                "claude-agent-sdk not installed; pip install claude-agent-sdk"
            ) from exc
        self._model = model

    def _confidence_from_text(self, text: str, max_chars: int) -> float:
        return confidence_from_text(text, max_chars)

    # -------- core query with retries + cost recording --------

    def _run(
        self,
        system: str,
        user: str,
        *,
        allowed_tools: list[str] | None = None,
    ) -> str:
        """Run one query with bounded backoff on rate-limit events.

        Records cost into the batch ledger via env-var configured CostMeter.
        """
        from claude_agent_sdk import (
            AssistantMessage,
            ClaudeAgentOptions,
            ClaudeSDKError,
            CLIConnectionError,
            CLINotFoundError,
            ProcessError,
            RateLimitEvent,
            ResultMessage,
            TextBlock,
            query,
        )

        from a11yfix.cost_meter import CostMeter

        meter = CostMeter.from_env()

        async def _go() -> str:
            opts = ClaudeAgentOptions(
                model=self._model,
                system_prompt=system,
                max_turns=3 if allowed_tools else 1,
                allowed_tools=allowed_tools or [],
            )
            chunks: list[str] = []
            rate_limited = False
            async for msg in query(prompt=user, options=opts):
                if isinstance(msg, RateLimitEvent):
                    rate_limited = True
                    continue
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, TextBlock):
                            chunks.append(block.text)
                elif isinstance(msg, ResultMessage):
                    # Prefer the SDK's authoritative cost; fall back to our
                    # estimator if it isn't surfaced.
                    if msg.total_cost_usd is not None:
                        meter.record_usd(model=self._model, usd=msg.total_cost_usd)
                    elif msg.usage:
                        meter.record(
                            model=self._model,
                            input_tokens=int(msg.usage.get("input_tokens") or 0),
                            output_tokens=int(msg.usage.get("output_tokens") or 0),
                            cache_read_tokens=int(msg.usage.get("cache_read_input_tokens") or 0),
                            cache_creation_tokens=int(
                                msg.usage.get("cache_creation_input_tokens") or 0
                            ),
                        )
            if rate_limited and not chunks:
                # Known limitation: a rate-limited stream usually ends without
                # a ResultMessage, so the partial attempt's cost is never
                # surfaced by the SDK and goes unrecorded in the ledger.
                raise RateLimitedError("rate-limited mid-stream with no text")
            return "".join(chunks).strip()

        last_exc: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                return _run_async(_go)
            except CLINotFoundError:
                # Permanent: the claude binary is missing; retrying can't help.
                raise
            except RateLimitedError as exc:
                last_exc = exc
            except (CLIConnectionError, ProcessError, ClaudeSDKError) as exc:
                # Transient transport errors → retry.
                last_exc = exc
            if attempt < MAX_RETRIES - 1:  # no pointless sleep before the final raise
                sleep_for = BASE_BACKOFF_SEC * (2 ** attempt) + random.uniform(0, 0.25)
                time.sleep(sleep_for)
        raise RateLimitedError(
            f"adapter exhausted {MAX_RETRIES} retries: {last_exc}"
        ) from last_exc

    # -------- public surface --------

    def describe_image(
        self, image_bytes: bytes, *, max_chars: int, context: str
    ) -> AltTextResult:
        read_bytes, suffix = _prepare_image_for_read(image_bytes)
        with tempfile.NamedTemporaryFile(
            prefix="a11yfix-img-", suffix=suffix, delete=False
        ) as tmp:
            tmp.write(read_bytes)
            tmp_path = Path(tmp.name)
        try:
            user = (
                f"Use the Read tool to read the image at {tmp_path}, then write "
                "alt text for it.\n\n"
                + alt_text_user(context=context)
                + "\n\n=== OUTPUT FORMAT (strict) ===\n"
                "After reading, your FINAL response must be exactly one line: the "
                "alt-text string only — no commentary, no 'Here is...', no quotes, "
                "no markdown. If the image is purely decorative, reply with the "
                "single word DECORATIVE. If you cannot read the file, reply with "
                "the single word UNCLEAR."
            )
            try:
                text = self._run(ALT_TEXT_SYSTEM, user, allowed_tools=["Read"])
            except Exception as exc:
                if _looks_like_sdk_payload_error(exc):
                    raise RuntimeError(
                        "Claude Code SDK image payload exceeded its message buffer"
                    ) from None
                raise
        finally:
            with contextlib.suppress(OSError):
                tmp_path.unlink()
        # Strip any tool-narration preamble: keep only the last non-empty line.
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if lines:
            text = lines[-1]
        for prefix in ("Alt text:", "Alt-text:", "ALT:", "Here is", "Here's"):
            if text.lower().startswith(prefix.lower()):
                text = text[len(prefix):].lstrip(" :-").strip()
        text = text.strip('"').strip("'")
        if len(text) > max_chars:
            text = text[: max_chars - 1].rstrip()
        return AltTextResult(
            text=text,
            confidence=self._confidence_from_text(text, max_chars),
            model=self._model,
        )

    def suggest_link_text(self, url: str, surrounding_text: str) -> LinkTextResult:
        text = self._run(
            LINK_TEXT_SYSTEM,
            link_text_user(url=url, surrounding_text=surrounding_text),
        ).strip('"').strip("'")
        return LinkTextResult(
            text=text,
            confidence=self._confidence_from_text(text, max_chars=64),
            model=self._model,
        )

    def suggest_slide_title(self, slide_text: str, slide_layout: str) -> SlideTitleResult:
        text = self._run(
            SLIDE_TITLE_SYSTEM,
            slide_title_user(slide_text=slide_text, slide_layout=slide_layout),
        ).strip('"').strip("'")
        return SlideTitleResult(
            text=text,
            confidence=self._confidence_from_text(text, max_chars=80),
            model=self._model,
        )


# -----------------------------------------------------------------------------
# Async invocation helper (handles already-running event loops)
# -----------------------------------------------------------------------------


def _run_async(coro_fn) -> Any:
    """Run an async function from sync code, even if an event loop is active."""
    try:
        return asyncio.run(coro_fn())
    except RuntimeError:
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(lambda: asyncio.run(coro_fn())).result()


# ensure_vision_compatible only ever returns these media types.
_SUFFIX_BY_MEDIA = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
}


def _prepare_image_for_read(image_bytes: bytes) -> tuple[bytes, str]:
    """Bound image payloads before asking Claude Code's Read tool to ingest them.

    Format gating (sniffing, BMP/TIFF→PNG conversion, EMF/WMF/SVG/unknown
    rejection) is owned by ensure_vision_compatible — one policy for both the
    API and SDK adapters. Its ValueError propagates so the caller defers the
    finding instead of getting a junk description back. This helper only adds
    the Read-tool size bound: oversized payloads are downscaled/recompressed.
    """
    from a11yfix.ooxml.image_extract import ensure_vision_compatible

    data, media = ensure_vision_compatible(image_bytes)
    suffix = _SUFFIX_BY_MEDIA[media]
    if len(data) <= MAX_READ_IMAGE_BYTES:
        return data, suffix
    try:
        from PIL import Image, ImageOps  # type: ignore[import-untyped]
    except ImportError:
        return data, suffix
    try:
        with Image.open(io.BytesIO(data)) as im:
            im = ImageOps.exif_transpose(im)
            im.thumbnail((MAX_READ_IMAGE_DIMENSION, MAX_READ_IMAGE_DIMENSION))
            if im.mode not in ("RGB", "L"):
                bg = Image.new("RGB", im.size, "white")
                if "A" in im.getbands():
                    bg.paste(im, mask=im.getchannel("A"))
                else:
                    bg.paste(im)
                im = bg
            elif im.mode != "RGB":
                im = im.convert("RGB")
            for quality in (85, 75, 65, 55):
                out = io.BytesIO()
                im.save(out, format="JPEG", quality=quality, optimize=True)
                jpeg = out.getvalue()
                if len(jpeg) <= MAX_READ_IMAGE_BYTES or quality == 55:
                    return jpeg, ".jpg"
    except Exception:
        return data, suffix
    return data, suffix


def _looks_like_sdk_payload_error(exc: Exception) -> bool:
    msg = f"{type(exc).__name__}: {exc}".lower()
    return (
        "json message exceeded maximum buffer size" in msg
        or "message exceeded maximum buffer" in msg
        or "maximum buffer size" in msg
    )
