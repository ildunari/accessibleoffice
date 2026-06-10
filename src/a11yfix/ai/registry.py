"""--vlm backend registry.

Factories import their adapter lazily (heavy deps stay optional) and raise
AdapterUnavailable from the constructor when the backend can't run.
Phases 2-3 append entries here; cli.py derives its click.Choice from
backend_names() so the flag and registry can't drift.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from a11yfix.ai.errors import AdapterUnavailable

if TYPE_CHECKING:
    from a11yfix.ai.adapter import VLMAdapter


def _claude(model: str | None) -> VLMAdapter:
    from a11yfix.ai.agent_sdk_adapter import ClaudeAgentSDKAdapter

    return ClaudeAgentSDKAdapter(**({"model": model} if model else {}))


def _claude_api(model: str | None) -> VLMAdapter:
    from a11yfix.ai.claude_adapter import ClaudeAdapter

    return ClaudeAdapter(**({"model": model} if model else {}))


def _openai(model: str | None) -> VLMAdapter:
    from a11yfix.ai.direct_llm_adapter import DirectLLMAdapter

    return DirectLLMAdapter(provider="openai", model=model)


def _openrouter(model: str | None) -> VLMAdapter:
    from a11yfix.ai.direct_llm_adapter import DirectLLMAdapter

    return DirectLLMAdapter(provider="openrouter", model=model)


def _pi(model: str | None) -> VLMAdapter:
    from a11yfix.ai.pi_adapter import PiAdapter

    return PiAdapter(model=model)


_BACKENDS: dict[str, Callable[[str | None], VLMAdapter]] = {
    "claude": _claude,
    "claude-api": _claude_api,
    "anthropic": _claude_api,  # alias: "direct Anthropic API"
    "openai": _openai,
    "openrouter": _openrouter,
    "pi": _pi,
}


def backend_names() -> list[str]:
    return list(_BACKENDS)


def create_adapter(name: str, model: str | None = None) -> VLMAdapter:
    factory = _BACKENDS.get(name)
    if factory is None:
        raise AdapterUnavailable(
            f"unknown AI backend {name!r}; valid: {', '.join(_BACKENDS)}"
        )
    return factory(model)
