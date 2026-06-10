# Multi-Backend AI Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make accessibleoffice's AI layer pluggable — direct LLM APIs (OpenAI, OpenRouter, Anthropic) and agent CLIs (Pi, OpenCode, Codex) as stage-3 backends, plus Codex as an alternative stage-4 agentic remediator — selectable via `--vlm` and `--agent`.

**Architecture:** Stage 3 already calls AI through the `VLMAdapter` Protocol (`src/a11yfix/ai/adapter.py`); we formalize the seam (typed errors, usage reporting, registry), then add adapters behind it. Cost recording moves out of adapters into `single_shot.py` so every backend meters identically. Stage 4 gets a small `AgenticLauncher` protocol wrapping the existing Claude launcher, plus a Codex launcher whose safety rails are a client-side verify-restore loop instead of Claude hooks.

**Tech Stack:** Python 3.11, click, httpx (new direct dependency), pytest (monkeypatched `subprocess.run` for CLI adapters), uv, ruff. External CLIs at runtime only: `pi`, `opencode`, `codex`.

---

## Phases (matches the agreed 4 stages)

| Phase | Tasks | Delivers |
|---|---|---|
| 1. Seam refactor | 1–6 | errors, `CallUsage`, shared confidence, registry, centralized cost recording, `--vlm-model` |
| 2. Direct LLM | 7–9 | `DirectLLMAdapter` → `--vlm openai`, `--vlm openrouter`, `anthropic` alias |
| 3. Agent CLIs | 10–14 | `PiAdapter`, `OpenCodeAdapter`, `CodexAdapter` + batch threading |
| 4. Stage-4 abstraction | 15–18 | `AgenticLauncher` protocol, `CodexLauncher` + verify gate, `--agent` flag |
| Final | 19 | full gates, live smoke script, merge readiness |

Out of scope (separate plans later): Pi/OpenCode stage-4 launchers, desktop-app backend picker, OpenCode HTTP-server mode.

## Gates

**Per-task gate:** the named pytest command passes; phase ends additionally require `uv run pytest tests -q` (zero failures) and `uvx ruff check src tests` (clean). Baseline before Phase 1: **166 passed**. Each task lists its expected new tests; final suite ≈ 210+.

**Fail gates — negative behaviors that MUST hold, each with a test:**

| # | Condition | Required behavior | Covering test |
|---|---|---|---|
| F1 | Backend binary/package/API key missing | `AdapterUnavailable` at construction → stage 3 skipped with `[warning] AI adapter unavailable`, single-file exit 0, batch status `partial` (exit 5, `[part]` line) | `test_registry.py::test_unavailable_backend_raises`, existing `test_batch_partial_status.py` |
| F2 | Backend call fails (nonzero exit, timeout, malformed JSON) | `AdapterCallError` → that finding defers, pipeline continues, nothing cached | each adapter's `test_*_adapter.py::test_malformed_output_defers` |
| F3 | Low confidence / UNCLEAR / DECORATIVE / empty | defer, not cached (existing `_cache_put` min_confidence=0.7) | existing tests in `test_fixers_partial_results.py` must stay green |
| F4 | Cost cap reached | remaining findings defer — must work for ALL backends (recording now centralized) | `test_usage_recording.py::test_cap_applies_to_any_adapter` |
| F5 | Stage 3 applies zero ops | file byte-identical (backup restore guard) | existing guard tests must stay green |
| F6 | Stage-4 Codex verification regression | backup restored, `launch` returns 7 | `test_stage4_codex.py::test_regression_restores_backup` |
| F7 | Unknown `--vlm` value | click.Choice rejection (exit 2); unit-level `create_adapter("nope")` → `AdapterUnavailable` naming valid backends | `test_registry.py::test_unknown_backend_lists_valid` |

**Success gate — a backend counts as "supported" only when ALL of:**
1. `uv run pytest tests -q` fully green and `uvx ruff check src tests` clean.
2. Live smoke passes: `uv run python scripts/live_smoke.py --vlm <backend>` asserts (a) alt text non-empty on the test image, (b) manifest `ai_model` matches the backend, (c) cost ledger `calls ≥ 1` (cost 0.0 allowed under subscription auth), (d) rerun is a cache hit — 0 new calls, output byte-identical, (e) with the backend binary shadowed off PATH the run exits 0 with the unavailable warning.
3. Regression matrix: live smoke for `claude` and `claude-api` still passes (run once at end of each phase that touched shared code).

---

### Task 0: Worktree branch setup

**Files:** none (git only)

- [ ] **Step 1: Verify clean state and create the worktree** (shared worktree dir per `~/LocalDev/CLAUDE.md`)

```bash
cd ~/LocalDev/office-a11y-fixer
git status --porcelain        # expect only ?? .claude/ — stop if tracked files dirty
git worktree list             # expect only the main checkout
git worktree add ~/LocalDev/.worktrees/office-a11y-fixer/multi-backend -b feature/multi-backend
cd ~/LocalDev/.worktrees/office-a11y-fixer/multi-backend
```

- [ ] **Step 2: Verify baseline**

```bash
uv sync && uv run pytest tests -q   # expect: 166 passed
uvx ruff check src tests            # expect: All checks passed!
```

All subsequent tasks run inside `~/LocalDev/.worktrees/office-a11y-fixer/multi-backend`.

---

## Phase 1 — Seam refactor

### Task 1: Typed adapter errors

**Files:**
- Create: `src/a11yfix/ai/errors.py`
- Test: `tests/unit/test_adapter_errors.py`

- [ ] **Step 1: Write the failing test**

```python
"""Adapter error taxonomy."""
from a11yfix.ai.errors import AdapterCallError, AdapterUnavailable


def test_unavailable_is_runtimeerror():
    # cli.py catches RuntimeError to skip stage 3; batch partial-status
    # detection keys off that same path. Subclassing is load-bearing.
    assert issubclass(AdapterUnavailable, RuntimeError)


def test_call_error_is_runtimeerror():
    assert issubclass(AdapterCallError, RuntimeError)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_adapter_errors.py -v`
Expected: FAIL with `ModuleNotFoundError: a11yfix.ai.errors`

- [ ] **Step 3: Implement**

```python
"""Backend-agnostic adapter exceptions.

Both subclass RuntimeError because cli.py's stage-3 skip path and the
batch `partial` status detection catch RuntimeError today — existing
behavior (F1 fail gate) must keep working unchanged.
"""

from __future__ import annotations


class AdapterUnavailable(RuntimeError):
    """The backend cannot run at all: missing binary, package, or credentials."""


class AdapterCallError(RuntimeError):
    """A single model call failed after retries — defer the finding, don't crash."""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_adapter_errors.py -v` → PASS

- [ ] **Step 5: Commit**

```bash
git add src/a11yfix/ai/errors.py tests/unit/test_adapter_errors.py
git commit -m "feat: typed adapter exceptions (AdapterUnavailable, AdapterCallError)"
```

### Task 2: CallUsage on result dataclasses + shared confidence heuristic

**Files:**
- Modify: `src/a11yfix/ai/adapter.py`
- Create: `src/a11yfix/ai/confidence.py`
- Test: `tests/unit/test_adapter_usage.py`

- [ ] **Step 1: Write the failing test**

```python
from a11yfix.ai.adapter import AltTextResult, CallUsage
from a11yfix.ai.confidence import confidence_from_text


def test_usage_defaults_none():
    r = AltTextResult(text="a chart", confidence=0.85, model="m")
    assert r.usage is None


def test_usage_carries_cost():
    u = CallUsage(input_tokens=100, output_tokens=20, cost_usd=0.0042)
    r = AltTextResult(text="a chart", confidence=0.85, model="m", usage=u)
    assert r.usage.cost_usd == 0.0042


def test_confidence_heuristic_matches_claude_adapter():
    # Extracted verbatim from ClaudeAdapter._confidence_from_text
    assert confidence_from_text("", 125) == 0.0
    assert confidence_from_text("UNCLEAR", 125) == 0.95
    assert confidence_from_text("x" * 200, 125) == 0.4
    assert confidence_from_text("a bar chart of Q3 revenue", 125) == 0.85
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/unit/test_adapter_usage.py -v` → FAIL (no `CallUsage`, no module)

- [ ] **Step 3: Implement**

In `src/a11yfix/ai/adapter.py`, add above the result dataclasses:

```python
@dataclass
class CallUsage:
    """Per-call token/cost report. cost_usd is authoritative backend-reported
    USD when available (Pi, OpenCode, Agent SDK); None means 'estimate from
    tokens via CostMeter pricing'."""

    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None
```

and add to each of `AltTextResult`, `LinkTextResult`, `SlideTitleResult`:

```python
    usage: CallUsage | None = None
```

Create `src/a11yfix/ai/confidence.py`:

```python
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
```

Then in `src/a11yfix/ai/claude_adapter.py` replace the body of `_confidence_from_text` with a delegation to the shared function (`from a11yfix.ai.confidence import confidence_from_text`), keeping the method so callers don't change. Do the same in `agent_sdk_adapter.py` (it has its own copy — grep `_confidence_from_text`).

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_adapter_usage.py tests/unit/test_agent_sdk_adapter.py -v` → PASS

- [ ] **Step 5: Commit**

```bash
git add src/a11yfix/ai/adapter.py src/a11yfix/ai/confidence.py src/a11yfix/ai/claude_adapter.py src/a11yfix/ai/agent_sdk_adapter.py tests/unit/test_adapter_usage.py
git commit -m "feat: CallUsage on adapter results; shared confidence heuristic"
```

### Task 3: Centralize cost recording in single_shot

**Files:**
- Modify: `src/a11yfix/fixers/single_shot.py` (the three `res = adapter.*` call sites, ~lines 154/200/239)
- Modify: `src/a11yfix/ai/agent_sdk_adapter.py` (remove internal CostMeter writes)
- Modify: `src/a11yfix/ai/claude_adapter.py` (attach usage from `msg.usage`)
- Test: `tests/unit/test_usage_recording.py`

- [ ] **Step 1: Write the failing test**

```python
"""Cost recording is the pipeline's job, not the adapter's (one policy, F4)."""
from pathlib import Path

from a11yfix.ai.adapter import AltTextResult, CallUsage
from a11yfix.cost_meter import CostMeter
from a11yfix.fixers.single_shot import _record_usage


def test_authoritative_cost_recorded(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("A11YFIX_STATE_DIR", str(tmp_path))
    res = AltTextResult(text="t", confidence=0.9, model="some-backend",
                        usage=CallUsage(cost_usd=0.01))
    _record_usage(res)
    assert abs(CostMeter.from_env().total() - 0.01) < 1e-9


def test_token_estimate_fallback(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("A11YFIX_STATE_DIR", str(tmp_path))
    res = AltTextResult(text="t", confidence=0.9, model="unknown-model",
                        usage=CallUsage(input_tokens=1_000_000, output_tokens=0))
    _record_usage(res)
    # falls back to _DEFAULT_PRICE_INPUT = 3.0 USD/M
    assert abs(CostMeter.from_env().total() - 3.0) < 1e-6


def test_no_usage_is_noop(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("A11YFIX_STATE_DIR", str(tmp_path))
    _record_usage(AltTextResult(text="t", confidence=0.9, model="m"))
    assert CostMeter.from_env().total() == 0.0
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/unit/test_usage_recording.py -v` → FAIL (`_record_usage` not defined)

- [ ] **Step 3: Implement `_record_usage` in single_shot.py**

```python
def _record_usage(res) -> None:
    """Record one fresh adapter call into the batch cost ledger.

    Centralized here (not in adapters) so every backend meters identically
    and cache hits never record. Backend-reported USD wins over estimates.
    """
    usage = getattr(res, "usage", None)
    if usage is None:
        return
    meter = CostMeter.from_env()
    if usage.cost_usd is not None:
        meter.record_usd(model=res.model, usd=usage.cost_usd)
    else:
        meter.record(
            model=res.model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
        )
```

Call `_record_usage(res)` immediately after each of the three fresh-call sites (`adapter.describe_image`, `adapter.suggest_link_text`, `adapter.suggest_slide_title`) — inside the `else:` branch of the cache check, never on cache hits. Update the `apply_single_shot_fixes` docstring sentence "Per-call cost is recorded by the adapter." → "Per-call cost is recorded centrally from each result's `usage`."

- [ ] **Step 4: Strip adapter-internal recording**

In `agent_sdk_adapter.py`: grep `CostMeter` — `_run` currently records `ResultMessage.total_cost_usd` itself. Change `_run` to return the cost alongside the text (e.g. `tuple[str, float | None]`), delete the CostMeter import/calls, and in the three public methods attach `usage=CallUsage(cost_usd=cost)` to the returned result.
In `claude_adapter.py`: each method has the `msg` response in scope — attach `usage=CallUsage(input_tokens=msg.usage.input_tokens, output_tokens=msg.usage.output_tokens)` to each result.

- [ ] **Step 5: Run focused then full tests**

Run: `uv run pytest tests/unit/test_usage_recording.py tests/unit/test_agent_sdk_adapter.py tests/unit/test_fixers_partial_results.py -v` → PASS
Run: `uv run pytest tests -q` → all pass (existing FakeAdapter results have `usage=None` → no-op, so nothing else changes)

- [ ] **Step 6: Commit**

```bash
git add src/a11yfix/fixers/single_shot.py src/a11yfix/ai/agent_sdk_adapter.py src/a11yfix/ai/claude_adapter.py tests/unit/test_usage_recording.py
git commit -m "refactor: centralize cost recording in single_shot via CallUsage"
```

### Task 4: Backend registry

**Files:**
- Create: `src/a11yfix/ai/registry.py`
- Test: `tests/unit/test_registry.py`

- [ ] **Step 1: Write the failing test**

```python
import pytest

from a11yfix.ai.errors import AdapterUnavailable
from a11yfix.ai.registry import backend_names, create_adapter


def test_initial_backends_registered():
    assert {"claude", "claude-api", "anthropic"} <= set(backend_names())


def test_unknown_backend_lists_valid():
    with pytest.raises(AdapterUnavailable) as exc:
        create_adapter("nope")
    assert "claude" in str(exc.value)  # message names valid backends (F7)


def test_unavailable_backend_raises(monkeypatch):
    # claude-api without the anthropic package importable at construction
    # is covered by ClaudeAdapter itself; here verify the registry lets the
    # constructor's RuntimeError propagate untouched.
    import a11yfix.ai.registry as reg

    def boom(model):
        raise AdapterUnavailable("anthropic package not installed")

    monkeypatch.setitem(reg._BACKENDS, "claude-api", boom)
    with pytest.raises(AdapterUnavailable):
        create_adapter("claude-api")
```

- [ ] **Step 2: Run to verify it fails** → `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
"""--vlm backend registry.

Factories import their adapter lazily (heavy deps stay optional) and raise
AdapterUnavailable from the constructor when the backend can't run.
Phases 2-3 append entries here; cli.py derives its click.Choice from
backend_names() so the flag and registry can't drift.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from a11yfix.ai.errors import AdapterUnavailable

if TYPE_CHECKING:
    from a11yfix.ai.adapter import VLMAdapter


def _claude(model: str | None) -> "VLMAdapter":
    from a11yfix.ai.agent_sdk_adapter import ClaudeAgentSDKAdapter

    return ClaudeAgentSDKAdapter(**({"model": model} if model else {}))


def _claude_api(model: str | None) -> "VLMAdapter":
    from a11yfix.ai.claude_adapter import ClaudeAdapter

    return ClaudeAdapter(**({"model": model} if model else {}))


_BACKENDS: dict[str, Callable[[str | None], "VLMAdapter"]] = {
    "claude": _claude,
    "claude-api": _claude_api,
    "anthropic": _claude_api,  # alias: "direct Anthropic API"
}


def backend_names() -> list[str]:
    return list(_BACKENDS)


def create_adapter(name: str, model: str | None = None) -> "VLMAdapter":
    factory = _BACKENDS.get(name)
    if factory is None:
        raise AdapterUnavailable(
            f"unknown AI backend {name!r}; valid: {', '.join(_BACKENDS)}"
        )
    return factory(model)
```

- [ ] **Step 4: Run tests** → PASS, then **Step 5: Commit**

```bash
git add src/a11yfix/ai/registry.py tests/unit/test_registry.py
git commit -m "feat: VLM backend registry with lazy factories"
```

### Task 5: Wire registry + `--vlm-model` into cli.py

**Files:**
- Modify: `src/a11yfix/cli.py` (adapter selection ~lines 236–253; `--vlm` option ~line 646; `_process_one_file` signature ~line 105 and its callers)
- Test: `tests/unit/test_cli_backend_selection.py`

- [ ] **Step 1: Write the failing test**

```python
from click.testing import CliRunner

from a11yfix.cli import main  # the click group/command — confirm symbol name via `grep "^def main\|@click.command\|@click.group" src/a11yfix/cli.py`


def test_vlm_choices_come_from_registry():
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert "claude-api" in result.output
    assert "anthropic" in result.output


def test_unknown_vlm_rejected():
    runner = CliRunner()
    result = runner.invoke(main, ["nofile.pptx", "--vlm", "bogus"])
    assert result.exit_code == 2  # click.Choice rejection (F7)
```

- [ ] **Step 2: Run to verify it fails** (choices are still the hardcoded 3-tuple)

- [ ] **Step 3: Implement**

Replace the `--vlm` option declaration:

```python
@click.option(
    "--vlm",
    type=click.Choice(backend_names()),
    default="claude",
    show_default=True,
    help=(
        "AI backend for stage-3 fixes. claude = Claude Code OAuth (no API key). "
        "claude-api/anthropic = Anthropic SDK (ANTHROPIC_API_KEY). "
        "Later phases add: openai, openrouter, pi, opencode, codex."
    ),
)
@click.option(
    "--vlm-model",
    default=None,
    help="Override the backend's default model (e.g. gpt-5-mini, anthropic/claude-haiku-4.5).",
)
```

(`from a11yfix.ai.registry import backend_names, create_adapter` at top of cli.py.)

Replace the if/elif adapter construction in `_process_one_file`:

```python
    try:
        adapter = create_adapter(vlm, model=vlm_model)
    except RuntimeError as exc:  # AdapterUnavailable subclasses RuntimeError
        click.echo(f"[warning] AI adapter unavailable: {exc} — skipping stage 3", err=True)
        ...existing skip-path body unchanged...
```

Delete the `else: return FileResult(... f"vlm={vlm} not implemented" ...)` branch — the registry covers it. Thread `vlm_model: str | None` through `_process_one_file` and every caller (grep `_process_one_file(` and `vlm=` in cli.py and batch.py).

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_cli_backend_selection.py -v` → PASS
Run: `uv run pytest tests -q && uvx ruff check src tests` → **Phase-1 gate: all green**

- [ ] **Step 5: Commit**

```bash
git add src/a11yfix/cli.py tests/unit/test_cli_backend_selection.py
git commit -m "feat: --vlm choices from registry; add --vlm-model override"
```

### Task 6: Phase-1 checkpoint

- [ ] Run full gate: `uv run pytest tests -q` (expect ≥ 175 passed, 0 failed) and `uvx ruff check src tests`
- [ ] Push branch: `git push -u accofc feature/multi-backend`

---

## Phase 2 — Direct LLM adapter (OpenAI / OpenRouter / Anthropic)

### Task 7: Add httpx dependency

**Files:** Modify: `pyproject.toml`

- [ ] **Step 1:** `uv add "httpx>=0.27"` (it's already a transitive dep of anthropic; this makes it explicit)
- [ ] **Step 2:** `uv run pytest tests -q` → still green; commit `pyproject.toml` + `uv.lock`: `git commit -m "chore: explicit httpx dependency"`

### Task 8: DirectLLMAdapter

**Files:**
- Create: `src/a11yfix/ai/direct_llm_adapter.py`
- Test: `tests/unit/test_direct_llm_adapter.py`

- [ ] **Step 1: Write the failing tests** (httpx transport mock — no network)

```python
import json

import httpx
import pytest

from a11yfix.ai.direct_llm_adapter import DirectLLMAdapter
from a11yfix.ai.errors import AdapterCallError, AdapterUnavailable

PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753"
    "de0000000c4944415408d763f8cfc00000030101009a9c181b0000000049454e44ae426082"
)


def _adapter(monkeypatch, handler, provider="openai"):
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    a = DirectLLMAdapter(provider=provider)
    a._client = httpx.Client(transport=httpx.MockTransport(handler))
    return a


def _ok(text="A bar chart", pt=100, ct=10):
    def handler(request):
        return httpx.Response(200, json={
            "choices": [{"message": {"content": text}}],
            "usage": {"prompt_tokens": pt, "completion_tokens": ct},
        })
    return handler


def test_missing_key_unavailable(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(AdapterUnavailable):
        DirectLLMAdapter(provider="openai")


def test_describe_image_returns_usage(monkeypatch):
    a = _adapter(monkeypatch, _ok())
    res = a.describe_image(PNG_1PX, max_chars=125, context="Shape: chart1")
    assert res.text == "A bar chart"
    assert res.usage.input_tokens == 100 and res.usage.output_tokens == 10
    assert res.usage.cost_usd is None  # estimator fallback


def test_image_sent_as_data_url(monkeypatch):
    seen = {}
    def handler(request):
        seen["body"] = json.loads(request.content)
        return _ok()(request)
    a = _adapter(monkeypatch, handler)
    a.describe_image(PNG_1PX, max_chars=125, context="ctx")
    img = seen["body"]["messages"][1]["content"][0]
    assert img["type"] == "image_url"
    assert img["image_url"]["url"].startswith("data:image/png;base64,")


def test_http_error_raises_call_error(monkeypatch):
    a = _adapter(monkeypatch, lambda r: httpx.Response(500, text="boom"))
    with pytest.raises(AdapterCallError):
        a.suggest_link_text(url="https://x.test", surrounding_text="see")


def test_openrouter_base_url(monkeypatch):
    seen = {}
    def handler(request):
        seen["url"] = str(request.url)
        return _ok()(request)
    a = _adapter(monkeypatch, handler, provider="openrouter")
    a.suggest_slide_title(slide_text="t", slide_layout="l")
    assert seen["url"].startswith("https://openrouter.ai/api/v1/")
```

- [ ] **Step 2: Run to verify failure** → `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
"""OpenAI-compatible chat-completions adapter — covers OpenAI and OpenRouter
(and by extension any /v1-compatible endpoint via A11YFIX_OPENAI_BASE_URL).
The direct Anthropic path is the existing ClaudeAdapter ('anthropic' alias).

NOTE for executor: confirm current cheap-default model ids against provider
docs at implementation time; the constants below are June-2026 best guesses.
"""

from __future__ import annotations

import base64
import os
import time

import httpx

from a11yfix.ai.adapter import AltTextResult, CallUsage, LinkTextResult, SlideTitleResult
from a11yfix.ai.confidence import confidence_from_text
from a11yfix.ai.errors import AdapterCallError, AdapterUnavailable
from a11yfix.ai.prompts import (
    ALT_TEXT_SYSTEM, LINK_TEXT_SYSTEM, SLIDE_TITLE_SYSTEM,
    alt_text_user, link_text_user, slide_title_user,
)

_PROVIDERS = {
    # name: (base_url env-override, default base_url, key env var, default model)
    "openai": ("A11YFIX_OPENAI_BASE_URL", "https://api.openai.com/v1",
               "OPENAI_API_KEY", "gpt-5-mini"),
    "openrouter": ("A11YFIX_OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1",
                   "OPENROUTER_API_KEY", "anthropic/claude-haiku-4.5"),
}
_TIMEOUT = 120.0
_RETRIES = 3


class DirectLLMAdapter:
    def __init__(self, *, provider: str = "openai", model: str | None = None) -> None:
        if provider not in _PROVIDERS:
            raise AdapterUnavailable(f"unknown direct-LLM provider {provider!r}")
        url_env, base_url, key_env, default_model = _PROVIDERS[provider]
        key = os.environ.get(key_env)
        if not key:
            raise AdapterUnavailable(f"{key_env} not set (required for --vlm {provider})")
        self._base_url = os.environ.get(url_env, base_url)
        self._model = model or default_model
        self.name = f"{provider}:{self._model}"
        self._client = httpx.Client(
            headers={"Authorization": f"Bearer {key}"}, timeout=_TIMEOUT
        )

    def _chat(self, *, system: str, content: list | str, max_tokens: int) -> tuple[str, CallUsage]:
        body = {
            "model": self._model,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": content},
            ],
        }
        last: Exception | None = None
        for attempt in range(_RETRIES):
            try:
                r = self._client.post(f"{self._base_url}/chat/completions", json=body)
                if r.status_code in (429, 500, 502, 503, 529):
                    last = AdapterCallError(f"HTTP {r.status_code}: {r.text[:200]}")
                    time.sleep(0.5 * 2**attempt)
                    continue
                r.raise_for_status()
                data = r.json()
                text = (data["choices"][0]["message"]["content"] or "").strip()
                u = data.get("usage") or {}
                usage = CallUsage(
                    input_tokens=int(u.get("prompt_tokens") or 0),
                    output_tokens=int(u.get("completion_tokens") or 0),
                )
                return text, usage
            except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
                last = exc
                time.sleep(0.5 * 2**attempt)
        raise AdapterCallError(f"{self.name}: {last}") from last

    def describe_image(self, image_bytes: bytes, *, max_chars: int, context: str) -> AltTextResult:
        from a11yfix.ooxml.image_extract import ensure_vision_compatible

        send_bytes, media_type = ensure_vision_compatible(image_bytes)
        data_url = f"data:{media_type};base64,{base64.b64encode(send_bytes).decode('ascii')}"
        text, usage = self._chat(
            system=ALT_TEXT_SYSTEM,
            content=[
                {"type": "image_url", "image_url": {"url": data_url}},
                {"type": "text", "text": alt_text_user(context=context)},
            ],
            max_tokens=200,
        )
        if len(text) > max_chars:
            text = text[: max_chars - 1].rstrip()
        return AltTextResult(text=text, confidence=confidence_from_text(text, max_chars),
                             model=self.name, usage=usage)

    def suggest_link_text(self, url: str, surrounding_text: str) -> LinkTextResult:
        text, usage = self._chat(
            system=LINK_TEXT_SYSTEM,
            content=link_text_user(url=url, surrounding_text=surrounding_text),
            max_tokens=64,
        )
        text = text.strip().strip('"').strip("'")
        return LinkTextResult(text=text, confidence=confidence_from_text(text, 64),
                              model=self.name, usage=usage)

    def suggest_slide_title(self, slide_text: str, slide_layout: str) -> SlideTitleResult:
        text, usage = self._chat(
            system=SLIDE_TITLE_SYSTEM,
            content=slide_title_user(slide_text=slide_text, slide_layout=slide_layout),
            max_tokens=64,
        )
        text = text.strip().strip('"').strip("'")
        return SlideTitleResult(text=text, confidence=confidence_from_text(text, 80),
                                model=self.name, usage=usage)
```

- [ ] **Step 4: Run tests** → PASS. **Step 5: Commit** `feat: DirectLLMAdapter (OpenAI/OpenRouter chat-completions)`

### Task 9: Register openai/openrouter backends

**Files:** Modify: `src/a11yfix/ai/registry.py`; Test: extend `tests/unit/test_registry.py`

- [ ] **Step 1: Failing test** — add to test_registry.py:

```python
def test_direct_llm_backends_registered():
    assert {"openai", "openrouter"} <= set(backend_names())
```

- [ ] **Step 2/3:** Add factories to `_BACKENDS`:

```python
def _openai(model: str | None) -> "VLMAdapter":
    from a11yfix.ai.direct_llm_adapter import DirectLLMAdapter

    return DirectLLMAdapter(provider="openai", model=model)


def _openrouter(model: str | None) -> "VLMAdapter":
    from a11yfix.ai.direct_llm_adapter import DirectLLMAdapter

    return DirectLLMAdapter(provider="openrouter", model=model)
```

- [ ] **Step 4: Phase-2 gate:** `uv run pytest tests -q && uvx ruff check src tests` → green. **Step 5: Commit + push** `feat: register openai/openrouter VLM backends`

---

## Phase 3 — Agent-CLI adapters (Pi, OpenCode, Codex)

### Task 10: Shared agent-CLI plumbing

**Files:**
- Create: `src/a11yfix/ai/agent_cli.py`
- Test: `tests/unit/test_agent_cli.py`

- [ ] **Step 1: Failing tests**

```python
import subprocess

import pytest

from a11yfix.ai.agent_cli import jsonl_events, require_binary, run_cli, temp_image
from a11yfix.ai.errors import AdapterCallError, AdapterUnavailable

PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753"
    "de0000000c4944415408d763f8cfc00000030101009a9c181b0000000049454e44ae426082"
)


def test_require_binary_missing(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda b: None)
    with pytest.raises(AdapterUnavailable):
        require_binary("pi", hint="npm install -g @earendil-works/pi-coding-agent")


def test_run_cli_nonzero_raises(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 1, "", "err"))
    with pytest.raises(AdapterCallError):
        run_cli(["x"], timeout=5)


def test_jsonl_skips_garbage():
    events = list(jsonl_events('{"a":1}\nnot json\n{"b":2}\n'))
    assert events == [{"a": 1}, {"b": 2}]


def test_temp_image_roundtrip():
    with temp_image(PNG_1PX) as p:
        assert p.suffix == ".png" and p.read_bytes()[:4] == b"\x89PNG"
    assert not p.exists()
```

- [ ] **Step 2: Run → fails.** **Step 3: Implement**

```python
"""Shared plumbing for adapters that shell out to an agent CLI (pi/opencode/codex)."""

from __future__ import annotations

import contextlib
import json
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path

from a11yfix.ai.errors import AdapterCallError, AdapterUnavailable

CLI_TIMEOUT = 120  # match the officecli subprocess guard


def require_binary(binary: str, *, hint: str) -> str:
    path = shutil.which(binary)
    if path is None:
        raise AdapterUnavailable(f"'{binary}' not found on PATH — install it: {hint}")
    return path


def run_cli(cmd: list[str], *, timeout: int = CLI_TIMEOUT) -> str:
    """Run an agent CLI; return stdout. AdapterCallError on failure/timeout."""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise AdapterCallError(f"{cmd[0]}: {exc}") from exc
    if proc.returncode != 0:
        raise AdapterCallError(
            f"{cmd[0]} exited {proc.returncode}: {(proc.stderr or proc.stdout)[:400]}"
        )
    return proc.stdout


def jsonl_events(stdout: str) -> Iterator[dict]:
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        with contextlib.suppress(json.JSONDecodeError):
            ev = json.loads(line)
            if isinstance(ev, dict):
                yield ev


@contextlib.contextmanager
def temp_image(image_bytes: bytes) -> Iterator[Path]:
    """Write vision-compatible bytes to a temp file the CLI can read."""
    from a11yfix.ooxml.image_extract import ensure_vision_compatible

    send_bytes, media_type = ensure_vision_compatible(image_bytes)
    suffix = {"image/png": ".png", "image/jpeg": ".jpg",
              "image/gif": ".gif", "image/webp": ".webp"}.get(media_type, ".png")
    f = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        f.write(send_bytes)
        f.close()
        yield Path(f.name)
    finally:
        Path(f.name).unlink(missing_ok=True)
```

- [ ] **Step 4: tests PASS.** **Step 5: Commit** `feat: shared agent-CLI adapter plumbing`

### Task 11: PiAdapter

**Files:**
- Create: `src/a11yfix/ai/pi_adapter.py`
- Test: `tests/unit/test_pi_adapter.py`
- Modify: `src/a11yfix/ai/registry.py` (+ `"pi"` factory entry, same shape as Task 9)

Pi invocation (verified against pi.dev docs June 2026 — re-verify `--mode json` event shape against a real `pi` install during the live smoke): `pi --mode json --no-session --no-context-files --no-tools [--model m] [--system-prompt s] [@/tmp/img.png] "<user prompt>"`. Cost comes back on the final assistant `message_end` event as `message.usage` with `input`, `output`, and `cost.total` (USD).

- [ ] **Step 1: Failing tests** (monkeypatch `subprocess.run`; canned JSONL)

```python
import subprocess

import pytest

from a11yfix.ai.errors import AdapterCallError
from a11yfix.ai.pi_adapter import PiAdapter

PI_OK = "\n".join([
    '{"type":"agent_start"}',
    '{"type":"message_end","message":{"role":"assistant",'
    '"content":[{"type":"text","text":"A bar chart of Q3 revenue"}],'
    '"usage":{"input":900,"output":12,"cost":{"total":0.0009}}}}',
    '{"type":"agent_end"}',
])

PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753"
    "de0000000c4944415408d763f8cfc00000030101009a9c181b0000000049454e44ae426082"
)


@pytest.fixture
def pi(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda b: "/usr/local/bin/pi")
    return PiAdapter()


def _fake_run(monkeypatch, stdout, rc=0, capture=None):
    def run(cmd, **kw):
        if capture is not None:
            capture.append(cmd)
        return subprocess.CompletedProcess(cmd, rc, stdout, "")
    monkeypatch.setattr(subprocess, "run", run)


def test_describe_image(pi, monkeypatch):
    cmds: list = []
    _fake_run(monkeypatch, PI_OK, capture=cmds)
    res = pi.describe_image(PNG_1PX, max_chars=125, context="Shape: chart1")
    assert res.text == "A bar chart of Q3 revenue"
    assert res.usage.cost_usd == 0.0009
    cmd = cmds[0]
    assert cmd[:2] == ["pi", "--mode"] and "--no-tools" in cmd
    assert any(str(a).startswith("@") for a in cmd)  # image attached


def test_no_image_flag_for_text_calls(pi, monkeypatch):
    cmds: list = []
    _fake_run(monkeypatch, PI_OK, capture=cmds)
    pi.suggest_link_text(url="https://x.test/a", surrounding_text="see docs")
    assert not any(str(a).startswith("@") for a in cmds[0])


def test_malformed_output_defers(pi, monkeypatch):
    _fake_run(monkeypatch, "garbage\nnot json")
    with pytest.raises(AdapterCallError):
        pi.suggest_slide_title(slide_text="t", slide_layout="l")
```

- [ ] **Step 2: Run → fails.** **Step 3: Implement**

```python
"""Pi coding agent (pi.dev) as a stage-3 VLM backend, via `pi --mode json`.

Pi is run as a pure completion endpoint: --no-tools --no-session
--no-context-files. Auth (Claude Max / ChatGPT / Copilot OAuth, API keys)
is whatever the user configured via `pi /login` — nothing to plumb here.
"""

from __future__ import annotations

from a11yfix.ai.adapter import AltTextResult, CallUsage, LinkTextResult, SlideTitleResult
from a11yfix.ai.agent_cli import jsonl_events, require_binary, run_cli, temp_image
from a11yfix.ai.confidence import confidence_from_text
from a11yfix.ai.errors import AdapterCallError
from a11yfix.ai.prompts import (
    ALT_TEXT_SYSTEM, LINK_TEXT_SYSTEM, SLIDE_TITLE_SYSTEM,
    alt_text_user, link_text_user, slide_title_user,
)


class PiAdapter:
    def __init__(self, *, model: str | None = None) -> None:
        require_binary("pi", hint="npm install -g @earendil-works/pi-coding-agent")
        self._model = model
        self.name = f"pi:{model}" if model else "pi"

    def _call(self, *, system: str, user: str, image_path=None) -> tuple[str, CallUsage]:
        cmd = ["pi", "--mode", "json", "--no-session", "--no-context-files",
               "--no-tools", "--system-prompt", system]
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
            parts = [p.get("text", "") for p in (msg.get("content") or [])
                     if isinstance(p, dict) and p.get("type") == "text"]
            text = "\n".join(p for p in parts if p).strip()
            u = msg.get("usage") or {}
            usage = CallUsage(
                input_tokens=int(u.get("input") or 0),
                output_tokens=int(u.get("output") or 0),
                cost_usd=(u.get("cost") or {}).get("total"),
            )
        if not text:
            raise AdapterCallError("pi: no assistant message in --mode json output")
        return text, usage

    def describe_image(self, image_bytes: bytes, *, max_chars: int, context: str) -> AltTextResult:
        with temp_image(image_bytes) as p:
            text, usage = self._call(system=ALT_TEXT_SYSTEM,
                                     user=alt_text_user(context=context), image_path=p)
        if len(text) > max_chars:
            text = text[: max_chars - 1].rstrip()
        return AltTextResult(text=text, confidence=confidence_from_text(text, max_chars),
                             model=self.name, usage=usage)

    def suggest_link_text(self, url: str, surrounding_text: str) -> LinkTextResult:
        text, usage = self._call(
            system=LINK_TEXT_SYSTEM,
            user=link_text_user(url=url, surrounding_text=surrounding_text))
        text = text.strip().strip('"').strip("'")
        return LinkTextResult(text=text, confidence=confidence_from_text(text, 64),
                              model=self.name, usage=usage)

    def suggest_slide_title(self, slide_text: str, slide_layout: str) -> SlideTitleResult:
        text, usage = self._call(
            system=SLIDE_TITLE_SYSTEM,
            user=slide_title_user(slide_text=slide_text, slide_layout=slide_layout))
        text = text.strip().strip('"').strip("'")
        return SlideTitleResult(text=text, confidence=confidence_from_text(text, 80),
                                model=self.name, usage=usage)
```

Registry entry (and `tests/unit/test_registry.py` assertion `"pi" in backend_names()`):

```python
def _pi(model: str | None) -> "VLMAdapter":
    from a11yfix.ai.pi_adapter import PiAdapter

    return PiAdapter(model=model)
```

- [ ] **Step 4: tests PASS.** **Step 5: Commit** `feat: Pi coding agent VLM backend (--vlm pi)`

### Task 12: OpenCodeAdapter

**Files:**
- Create: `src/a11yfix/ai/opencode_adapter.py`
- Test: `tests/unit/test_opencode_adapter.py`
- Modify: `src/a11yfix/ai/registry.py` (+ `"opencode"` entry)

Invocation: `opencode run --format json [-m provider/model] [-f /tmp/img.png] "<system>\n\n<user>"` (`run` has no system flag — prepend; v1 uses subprocess, not `opencode serve`). Assistant message events carry `cost` (USD) and `tokens.{input,output}`.

Same structure as Task 11 — class `OpenCodeAdapter`, `name = f"opencode:{model}" or "opencode"`, `require_binary("opencode", hint="curl -fsSL https://opencode.ai/install | bash")`, `_call` builds the cmd above and parses events. Parsing rules:

```python
        # text: concatenate every event part where part["type"] == "text",
        # from events shaped {"type": "message.part.updated"|..., "part": {...}}
        # OR a final assistant message object {"info": {...}, "parts": [...]}.
        # cost: first event payload found carrying numeric "cost" → cost_usd;
        # "tokens": {"input": .., "output": ..} → token counts.
```

Because OpenCode's `--format json` event stream shape is the least-pinned of the three (it's "raw JSON events"), write the parser as: collect all dict events; for each, look in `ev`, `ev.get("part")`, `ev.get("info")` for `{"cost": float}` / `{"tokens": {...}}`; collect text from any `{"type":"text","text":...}` part. **During live smoke, capture one real `opencode run --format json` output into `tests/unit/data/opencode_events.jsonl` and add a regression test parsing the real capture.**

- [ ] **Step 1:** failing tests mirroring Task 11's three cases (describe_image with `-f` flag asserted, text-call without `-f`, malformed → `AdapterCallError`), with canned events:

```python
OPENCODE_OK = "\n".join([
    '{"type":"message.part.updated","part":{"type":"text","text":"A bar chart of Q3 revenue"}}',
    '{"type":"message.updated","info":{"role":"assistant","cost":0.0011,'
    '"tokens":{"input":1500,"output":14,"reasoning":0}}}',
])
```

- [ ] **Steps 2–4:** implement per above, tests PASS. **Step 5: Commit** `feat: OpenCode VLM backend (--vlm opencode)`

### Task 13: CodexAdapter

**Files:**
- Create: `src/a11yfix/ai/codex_adapter.py`
- Test: `tests/unit/test_codex_adapter.py`
- Modify: `src/a11yfix/ai/registry.py` (+ `"codex"` entry); `src/a11yfix/cost_meter.py` (gpt pricing rows)

Invocation: `codex exec --json --ephemeral --skip-git-repo-check --color never -s read-only [-m model] [-i /tmp/img.png] "<system>\n\n<user>"`. Default model `gpt-5.4-mini` (cheap tier; ~27k tokens harness overhead per call is inherent — document in the class docstring). Parse JSONL: `item.completed` with `item.type == "agent_message"` → text; `turn.completed` → `usage.{input_tokens,cached_input_tokens,output_tokens}`; `turn.failed` → `AdapterCallError` with the error payload. `cost_usd=None` (tokens only) → estimator fallback, so add to `cost_meter.py`:

```python
# in _PRICE_PER_M_INPUT:                    in _PRICE_PER_M_OUTPUT:
"gpt-5.5": 1.75,                            "gpt-5.5": 14.0,
"gpt-5.4-mini": 0.25,                       "gpt-5.4-mini": 2.0,
# NOTE for executor: verify against current OpenAI API pricing at implementation time.
```

- [ ] **Step 1:** failing tests mirroring Task 11 (canned JSONL below; assert `-i` flag for images, usage tokens parsed, `turn.failed` → `AdapterCallError`):

```python
CODEX_OK = "\n".join([
    '{"type":"thread.started","thread_id":"t1"}',
    '{"type":"turn.started"}',
    '{"type":"item.completed","item":{"id":"i1","type":"agent_message","text":"A bar chart of Q3 revenue"}}',
    '{"type":"turn.completed","usage":{"input_tokens":27000,"cached_input_tokens":2000,"output_tokens":12}}',
])
CODEX_FAIL = '{"type":"error","message":"boom"}\n{"type":"turn.failed","error":{"message":"boom"}}'
```

- [ ] **Steps 2–4:** implement (class `CodexAdapter`, `require_binary("codex", hint="npm install -g @openai/codex")`, structure identical to PiAdapter with the codex cmd/parse), tests PASS, plus a `test_cost_meter.py`-style assertion that `estimate_cost_usd(model="gpt-5.4-mini", input_tokens=1_000_000)` returns 0.25.
- [ ] **Step 5: Commit** `feat: Codex exec VLM backend (--vlm codex); gpt pricing rows`

### Task 14: Thread --vlm/--vlm-model through batch + Phase-3 gate

**Files:**
- Modify: `src/a11yfix/batch.py` (the per-file invocation — grep `model: str = "claude-sonnet-4-6"` at batch.py:265 and the arg/JSON plumbing around it)
- Test: `tests/unit/test_batch_partial_status.py` (extend)

- [ ] **Step 1:** failing test — batch run with `vlm="pi"` while `shutil.which` is monkeypatched to None records the file as `partial` (reuse the fixtures/builders already in `test_batch_partial_status.py`; copy its existing partial-status test and switch the trigger from "adapter unavailable" simulation to `--vlm pi` + missing binary).
- [ ] **Step 2/3:** add `vlm: str = "claude"` and `vlm_model: str | None = None` params alongside the existing `model` param; thread to wherever batch invokes `_process_one_file` (or builds the per-file CLI args — follow the existing `model` param's path exactly).
- [ ] **Step 4: Phase-3 gate:** `uv run pytest tests -q && uvx ruff check src tests` → green (expect ≥ 200 passed).
- [ ] **Step 5: Commit + push** `feat: thread --vlm/--vlm-model through batch runner`

---

## Phase 4 — Stage-4 agentic launcher abstraction + Codex

### Task 15: AgenticLauncher protocol + Claude wrapper

**Files:**
- Modify: `src/a11yfix/stage4.py` (add protocol + wrapper at bottom; existing functions unchanged)
- Test: `tests/unit/test_stage4_launchers.py`

- [ ] **Step 1: Failing test**

```python
from a11yfix.stage4 import ClaudeLauncher, get_launcher


def test_claude_launcher_default():
    launcher = get_launcher("claude")
    assert isinstance(launcher, ClaudeLauncher)
    assert launcher.name == "claude"


def test_unknown_launcher():
    import pytest
    with pytest.raises(ValueError):
        get_launcher("bogus")
```

- [ ] **Step 2/3:** implement in stage4.py:

```python
class AgenticLauncher(Protocol):
    name: str

    def available(self) -> bool: ...
    def launch(self, plan: LaunchPlan, *, dry_run: bool = False) -> int: ...


class ClaudeLauncher:
    name = "claude"

    def available(self) -> bool:
        return claude_cli_available()

    def launch(self, plan: LaunchPlan, *, dry_run: bool = False) -> int:
        return launch(plan, dry_run=dry_run)


def get_launcher(agent: str) -> AgenticLauncher:
    if agent == "claude":
        return ClaudeLauncher()
    if agent == "codex":
        from a11yfix.stage4_codex import CodexLauncher

        return CodexLauncher()
    raise ValueError(f"unknown agentic backend {agent!r}; valid: claude, codex")
```

(`from typing import Protocol` already importable; Task 16 creates stage4_codex — until then the codex branch is exercised only by Task 16's tests.)

- [ ] **Step 4: tests PASS** (skip the codex import path until Task 16: keep `get_launcher("codex")` untested in this task). **Step 5: Commit** `feat: AgenticLauncher protocol; Claude wrapper`

### Task 16: CodexLauncher with client-side verification gate

**Files:**
- Create: `src/a11yfix/stage4_codex.py`
- Test: `tests/unit/test_stage4_codex.py`

Design (replaces Claude hooks with rails Codex can honor):
- **Sandbox**: `-s workspace-write -C <file's directory>` — Codex can only write inside the doc's folder.
- **No approval prompts**: `-a never` (non-interactive).
- **Bootstrap**: codex-specific prompt = `_hard_rules()` from stage4.py (reuse — it's backend-neutral officecli procedure) + manifest/file paths + "apply ops via officecli, validate after every write, never touch other files". No skills, no subagents.
- **Verification gate (client-side, replaces the Stop hook)**: after `codex exec` returns, re-run detection on the file and compare error-severity finding count against the manifest's pre-stage-4 baseline. Worse → restore `plan.backup`, return exit 7. Findings remain but not worse → ONE follow-up via `codex exec resume --last "<verifier report>"`, then re-verify; still failing → keep the file (it improved or held), return 0 with a warning. **Executor note:** the detection call to reuse is whatever `_process_one_file` in cli.py invokes for stage 1 — grep `findings =` in cli.py and extract/import that path (likely a `run_rules`/detect helper in `a11yfix.rules`); if it's inline, factor it into `detect_findings(file: Path) -> list[Finding]` first (small refactor commit). Likewise confirm the officecli validation entry point in `src/a11yfix/ooxml/officecli.py` (tests import `ValidationResult` from there) and run it as part of the gate.

```python
"""Stage-4 launcher for OpenAI Codex (`codex exec`).

Safety model vs the Claude launcher: Codex gets a workspace-write sandbox
scoped to the document's directory instead of PreToolUse hooks, and the
verification gate runs client-side AFTER the session instead of blocking
Stop. Edit caps / identical-write loop detection are NOT ported in v1 —
the sandbox plus verify-restore bounds the blast radius to the one file.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from a11yfix.stage4 import DEFAULT_EDIT_CAP, LaunchPlan, _hard_rules  # noqa: F401

DEFAULT_CODEX_MODEL = "gpt-5.5"
SESSION_TIMEOUT = 1800  # 30 min hard cap on one codex session


def _codex_bootstrap(manifest_path: Path, file_path: Path) -> str:
    return (
        "You are remediating Microsoft Office accessibility findings.\n"
        f"Manifest (read it first): {manifest_path}\n"
        f"Target file (the ONLY file you may modify): {file_path}\n\n"
        + _hard_rules()
        + "\nWork through residual_findings in the manifest one at a time. "
        "Apply each fix with officecli, run officecli validation after every "
        "write, and stop when every fixable finding is addressed. Do not edit "
        "any other file."
    )


class CodexLauncher:
    name = "codex"

    def available(self) -> bool:
        return shutil.which("codex") is not None

    def launch(self, plan: LaunchPlan, *, dry_run: bool = False) -> int:
        prompt = _codex_bootstrap(plan.manifest, plan.file)
        model = plan.model if plan.model.startswith("gpt") else DEFAULT_CODEX_MODEL
        cmd = [
            "codex", "exec", "--skip-git-repo-check", "--color", "never",
            "-s", "workspace-write", "-C", str(plan.file.parent),
            "-a", "never", "-m", model, prompt,
        ]
        if dry_run:
            print("DRY RUN — would execute:")
            print(" ".join(cmd[:-1]) + " <bootstrap prompt>")
            return 0
        baseline = _error_count(plan.file)
        proc = subprocess.run(cmd, timeout=SESSION_TIMEOUT)
        ok, after = _verify(plan.file, baseline)
        if not ok:
            follow = subprocess.run(
                ["codex", "exec", "resume", "--last", "--skip-git-repo-check",
                 "-s", "workspace-write", "-C", str(plan.file.parent), "-a", "never",
                 f"Verification failed: error findings went {baseline} -> {after}. "
                 "Re-check your officecli ops and fix the regression."],
                timeout=SESSION_TIMEOUT,
            )
            ok, after = _verify(plan.file, baseline)
        if not ok:
            if plan.backup and plan.backup.exists():
                shutil.copy2(plan.backup, plan.file)
                print(f"[stage4-codex] regression ({baseline} -> {after}); restored backup")
            return 7
        return proc.returncode


def _error_count(file: Path) -> int:
    findings = _detect(file)
    return sum(1 for f in findings if f.severity.value == "error")


def _verify(file: Path, baseline: int) -> tuple[bool, int]:
    after = _error_count(file)
    return (after <= baseline, after)


def _detect(file: Path):
    # Executor: import the same stage-1 detection entry _process_one_file uses
    # (see task note); placeholder-free once that import is resolved in Step 3.
    from a11yfix.cli import detect_findings

    return detect_findings(file)
```

- [ ] **Step 1: Failing tests** — use a fake `codex` script on PATH:

```python
import os
import stat
from pathlib import Path

import pytest

from a11yfix.stage4 import LaunchPlan
from a11yfix.stage4_codex import CodexLauncher


def _fake_codex(tmp_path: Path, monkeypatch, rc=0) -> Path:
    log = tmp_path / "codex_calls.log"
    script = tmp_path / "bin" / "codex"
    script.parent.mkdir()
    script.write_text(f'#!/bin/sh\necho "$@" >> {log}\nexit {rc}\n')
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{script.parent}:{os.environ['PATH']}")
    return log


def _plan(tmp_path: Path) -> LaunchPlan:
    f = tmp_path / "deck.pptx"; f.write_bytes(b"fake")
    b = tmp_path / "deck.backup.pptx"; b.write_bytes(b"orig")
    m = tmp_path / "deck.manifest.json"; m.write_text("{}")
    return LaunchPlan(file=f, manifest=m, model="gpt-5.5", subagent_model="",
                      grunt_model="", skills=[], bootstrap="", backup=b,
                      use_skill=False, settings_path=None)


def test_sandbox_and_approval_flags(tmp_path, monkeypatch):
    log = _fake_codex(tmp_path, monkeypatch)
    monkeypatch.setattr("a11yfix.stage4_codex._error_count", lambda f: 0)
    assert CodexLauncher().launch(_plan(tmp_path)) == 0
    args = log.read_text()
    assert "-s workspace-write" in args and "-a never" in args


def test_dry_run_executes_nothing(tmp_path, monkeypatch):
    log = _fake_codex(tmp_path, monkeypatch)
    CodexLauncher().launch(_plan(tmp_path), dry_run=True)
    assert not log.exists()


def test_regression_restores_backup(tmp_path, monkeypatch):
    _fake_codex(tmp_path, monkeypatch)
    counts = iter([2, 5, 5])  # baseline 2, after session 5, after follow-up 5
    monkeypatch.setattr("a11yfix.stage4_codex._error_count", lambda f: next(counts))
    plan = _plan(tmp_path)
    assert CodexLauncher().launch(plan) == 7          # F6 fail gate
    assert plan.file.read_bytes() == b"orig"          # backup restored


def test_follow_up_recovers(tmp_path, monkeypatch):
    _fake_codex(tmp_path, monkeypatch)
    counts = iter([2, 5, 1])  # regression, then follow-up fixes it
    monkeypatch.setattr("a11yfix.stage4_codex._error_count", lambda f: next(counts))
    plan = _plan(tmp_path)
    assert CodexLauncher().launch(plan) == 0
    assert plan.file.read_bytes() == b"fake"          # NOT restored
```

- [ ] **Step 2: Run → fails.** **Step 3: Implement** (including resolving the `_detect` import per the executor note — that resolution is part of this step, with its own small refactor commit if `detect_findings` must be extracted).
- [ ] **Step 4: tests PASS.** **Step 5: Commit** `feat: Codex stage-4 launcher with client-side verify-restore gate`

### Task 17: `--agent` CLI flag

**Files:**
- Modify: `src/a11yfix/cli.py` (the `--remediate` block that calls `launch(plan, ...)` ~line 215)
- Test: extend `tests/unit/test_cli_backend_selection.py`

- [ ] **Step 1:** failing test: `--help` output contains `--agent` with choices claude/codex.
- [ ] **Step 2/3:** add option + route:

```python
@click.option(
    "--agent",
    type=click.Choice(["claude", "codex"]),
    default="claude",
    show_default=True,
    help="Agentic backend for stage-4 remediation (--remediate / --mode full).",
)
```

and replace the direct `rc = launch(plan, dry_run=dry_run)` call with:

```python
            from a11yfix.stage4 import get_launcher

            launcher = get_launcher(agent)
            if not launcher.available():
                click.echo(
                    f"[warning] {launcher.name} CLI not found — skipping stage 4", err=True
                )
                rc = 0
            else:
                rc = launcher.launch(plan, dry_run=dry_run)
```

Thread `agent: str` through `_process_one_file` like `vlm` (same callers).

- [ ] **Step 4: Phase-4 gate:** `uv run pytest tests -q && uvx ruff check src tests` → green. **Step 5: Commit + push** `feat: --agent flag selects stage-4 backend`

### Task 18: Update stage4 fallback tests + docs touch

**Files:**
- Modify: `tests/unit/test_stage4_fallback.py` (confirm still green; add one test that `get_launcher("claude").launch` is what `--remediate` hits by default)
- Modify: `README.md` — extend the Usage section's mode explanation with one paragraph: backends are selectable via `--vlm` (claude, claude-api/anthropic, openai, openrouter, pi, opencode, codex) and `--agent` (claude, codex); note auth expectations per backend in one line each.

- [ ] Steps: test → implement → `uv run pytest tests -q` → commit `docs: README backend selection; stage4 launcher test`

---

### Task 19: Final verification — gates, live smoke, merge readiness

**Files:**
- Create: `scripts/live_smoke.py`

- [ ] **Step 1: Write the live smoke script** (opt-in, real model calls, NOT part of pytest):

```python
"""Live end-to-end smoke for one --vlm backend. Costs real money/quota.

Usage: uv run python scripts/live_smoke.py --vlm pi [--vlm-model ...]
Builds a one-slide deck with an image missing alt text, runs stages 1-3,
and asserts the success-gate criteria from the multi-backend plan.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def build_deck(path: Path) -> None:
    from PIL import Image
    from pptx import Presentation
    from pptx.util import Inches

    img = path.parent / "chart.png"
    Image.new("RGB", (320, 200), (200, 40, 40)).save(img)
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])  # blank
    slide.shapes.add_picture(str(img), Inches(1), Inches(1))
    prs.save(path)


def run(deck: Path, state: Path, vlm: str, vlm_model: str | None) -> subprocess.CompletedProcess:
    env = {**os.environ, "A11YFIX_STATE_DIR": str(state), "A11YFIX_CACHE": str(state / "cache")}
    cmd = [sys.executable, "-m", "a11yfix.cli", str(deck), "--mode", "full",
           "--vlm", vlm, "--output", str(deck.with_suffix(".manifest.json"))]
    if vlm_model:
        cmd += ["--vlm-model", vlm_model]
    return subprocess.run(cmd, env=env, capture_output=True, text=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vlm", required=True)
    ap.add_argument("--vlm-model", default=None)
    args = ap.parse_args()

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        deck = tmp / "smoke.pptx"
        build_deck(deck)

        r1 = run(deck, tmp / "state", args.vlm, args.vlm_model)
        print(r1.stdout[-2000:], r1.stderr[-2000:], sep="\n---\n")
        manifest = json.loads(deck.with_suffix(".manifest.json").read_text())
        fixes = [x for x in manifest.get("applied_fixes", []) if x.get("ai_model")]
        assert fixes, "GATE (a) FAILED: no AI fix applied"
        assert any(args.vlm.split(":")[0] in (x.get("ai_model") or "") for x in fixes), \
            f"GATE (b) FAILED: ai_model {fixes} does not match backend {args.vlm}"
        ledger = json.loads((tmp / "state" / "cost.json").read_text())
        assert ledger["calls"] >= 1, "GATE (c) FAILED: no cost-ledger calls recorded"

        before = deck.read_bytes()
        run(deck, tmp / "state", args.vlm, args.vlm_model)
        ledger2 = json.loads((tmp / "state" / "cost.json").read_text())
        assert ledger2["calls"] == ledger["calls"], "GATE (d) FAILED: rerun was not a cache hit"
        assert deck.read_bytes() == before, "GATE (d) FAILED: rerun changed bytes"

        print(f"LIVE SMOKE PASSED for --vlm {args.vlm} "
              f"(cost ${ledger['total_usd']:.4f}, {ledger['calls']} calls)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

(Executor: confirm the manifest's applied-fix JSON key names against `src/a11yfix/manifest.py` — `ai_model` is set in single_shot.py:324; adjust the two `manifest.get(...)` lookups if the serialized names differ. Confirm `python -m a11yfix.cli` is invokable — grep `[project.scripts]` in pyproject.toml and use the console-script name instead if not.)

- [ ] **Step 2: Run the full fail-gate checklist** (all in-suite):

```bash
uv run pytest tests -q                       # expect ~210+ passed, 0 failed
uvx ruff check src tests                     # clean
uv run pytest tests/unit/test_registry.py tests/unit/test_usage_recording.py \
  tests/unit/test_pi_adapter.py tests/unit/test_opencode_adapter.py \
  tests/unit/test_codex_adapter.py tests/unit/test_stage4_codex.py -v   # F1-F7 spot check
```

- [ ] **Step 3: Run the success-gate live smoke** for every backend installed on this machine (skip-and-note any that aren't):

```bash
uv run python scripts/live_smoke.py --vlm claude       # regression guard
uv run python scripts/live_smoke.py --vlm claude-api   # regression guard (needs ANTHROPIC_API_KEY)
uv run python scripts/live_smoke.py --vlm pi
uv run python scripts/live_smoke.py --vlm opencode
uv run python scripts/live_smoke.py --vlm codex
uv run python scripts/live_smoke.py --vlm openrouter   # needs OPENROUTER_API_KEY
# gate (e): shadow one backend off PATH and confirm graceful skip
PATH=/usr/bin:/bin uv run python -m a11yfix.cli <deck> --mode full --vlm pi  # expect exit 0 + warning
```

Record which backends passed live in the PR description. **A backend that fails live smoke gets its registry entry kept but its README line marked experimental — do not silently ship a green checkmark.** Where canned-JSONL assumptions proved wrong (Pi/OpenCode event shapes), capture the real output into `tests/unit/data/` and fix parser + tests in the same commit.

- [ ] **Step 4: Commit, push, PR**

```bash
git add scripts/live_smoke.py && git commit -m "test: live smoke harness for backend success gate"
git push accofc feature/multi-backend
# PR via gh against ildunari/accessibleoffice main; include live-smoke matrix results
```

- [ ] **Step 5: After merge** — clean up per worktree protocol:

```bash
git worktree remove ~/LocalDev/.worktrees/office-a11y-fixer/multi-backend
git branch -d feature/multi-backend
```

---

## Self-review notes (already applied)

- Spec coverage: 4 phases ↔ Tasks 1–6 / 7–9 / 10–14 / 15–18; gates ↔ F1–F7 table + Task 19; "OpenRouter/OpenAI/Anthropic selectable" ↔ Tasks 8–9 (`anthropic` aliases the existing ClaudeAdapter).
- Known verify-at-execution items (flagged inline, not placeholders): default model ids (Task 8/13), gpt API pricing (Task 13), Pi/OpenCode JSON event shapes (Tasks 11/12 — backstopped by live-smoke capture), the stage-1 detection import for `stage4_codex._detect` (Task 16), manifest key names + console-script name (Task 19).
- Type consistency: `CallUsage` (Task 2) used by Tasks 3/8/11/12/13; `create_adapter(name, model)` (Task 4) matches cli.py call (Task 5); `LaunchPlan` fields in Task 16 tests match the dataclass in stage4.py:444.
