"""Cumulative cost ledger for a batch, shared by every concurrent shard.

Anthropic SDK gives us token usage on each `query` completion; we map it to
USD via per-model rates and append to a flock'd ledger so two shards racing
to record can't clobber each other.

The meter is keyed by directory: pass `state_dir` and we write to
`<state_dir>/cost.json`. If `state_dir` is None or the file isn't writable
the meter is a no-op (single-file invocations don't need it).
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Per-million-token USD pricing. Used for cost estimation only — actual
# billing is whatever Anthropic charges. Update when pricing changes.
_PRICE_PER_M_INPUT = {
    "claude-opus-4-7": 15.0,
    "claude-opus-4-6": 15.0,
    "claude-opus-4-5": 15.0,
    "claude-sonnet-4-6": 3.0,
    "claude-sonnet-4-5": 3.0,
    "claude-haiku-4-5": 1.0,
}
_PRICE_PER_M_OUTPUT = {
    "claude-opus-4-7": 75.0,
    "claude-opus-4-6": 75.0,
    "claude-opus-4-5": 75.0,
    "claude-sonnet-4-6": 15.0,
    "claude-sonnet-4-5": 15.0,
    "claude-haiku-4-5": 5.0,
}
_DEFAULT_PRICE_INPUT = 3.0
_DEFAULT_PRICE_OUTPUT = 15.0

# One warning per process when the ledger degrades to in-memory no-op
# (e.g. read-only A11YFIX_STATE_DIR) — not one per metered call.
_warned_unwritable = False


def _warn_unwritable_once(path: Path, exc: OSError) -> None:
    global _warned_unwritable
    if _warned_unwritable:
        return
    _warned_unwritable = True
    print(
        f"[cost-meter] state dir not writable ({path.parent}: {exc}); "
        "cost metering disabled for this process",
        file=sys.stderr,
    )


def estimate_cost_usd(
    *,
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
) -> float:
    """Rough USD estimate. Cache reads cost 10% of input; creation costs 125%."""
    base = model.split("[")[0].strip()
    in_rate = _PRICE_PER_M_INPUT.get(base, _DEFAULT_PRICE_INPUT)
    out_rate = _PRICE_PER_M_OUTPUT.get(base, _DEFAULT_PRICE_OUTPUT)
    usd = (input_tokens * in_rate) / 1_000_000
    usd += (output_tokens * out_rate) / 1_000_000
    usd += (cache_read_tokens * in_rate * 0.10) / 1_000_000
    usd += (cache_creation_tokens * in_rate * 1.25) / 1_000_000
    return usd


@dataclass
class CostLedger:
    total_usd: float = 0.0
    calls: int = 0
    by_model: dict[str, float] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "total_usd": round(self.total_usd, 6),
            "calls": self.calls,
            "by_model": {k: round(v, 6) for k, v in self.by_model.items()},
        }

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> CostLedger:
        return cls(
            total_usd=float(d.get("total_usd") or 0.0),
            calls=int(d.get("calls") or 0),
            by_model={k: float(v) for k, v in (d.get("by_model") or {}).items()},
        )


class CostMeter:
    """Process-safe cumulative cost meter via fcntl.flock.

    Use as a context manager around the read-modify-write cycle, or call the
    convenience methods which acquire the lock internally.
    """

    def __init__(self, state_dir: Path | str | None) -> None:
        self.state_dir: Path | None = (
            Path(state_dir).expanduser().resolve() if state_dir else None
        )
        self.path: Path | None = (
            self.state_dir / "cost.json" if self.state_dir else None
        )

    @classmethod
    def from_env(cls) -> CostMeter:
        """Construct from `A11YFIX_STATE_DIR` env var (set by the batch runner)."""
        return cls(os.environ.get("A11YFIX_STATE_DIR"))

    def _read(self) -> CostLedger:
        if self.path is None or not self.path.exists():
            return CostLedger()
        try:
            return CostLedger.from_json(json.loads(self.path.read_text()))
        except (json.JSONDecodeError, OSError):
            return CostLedger()

    def _write(self, ledger: CostLedger) -> None:
        if self.path is None:
            return
        from a11yfix._io import atomic_write

        # Atomic so a process killed mid-write (even while holding the flock)
        # can't truncate the ledger and silently reset the running total.
        atomic_write(self.path, json.dumps(ledger.to_json(), indent=2))

    @contextlib.contextmanager
    def _locked(self):
        """Yield (ledger, write_fn). On exit the file lock is released.

        If the state dir isn't writable, degrades to the same in-memory
        no-op path as `self.path is None` instead of raising — a metering
        failure must never sink a model call that already succeeded.
        """
        if self.path is None:
            # No state dir: in-memory only, no locking.
            ledger = CostLedger()
            yield ledger, lambda new: None
            return
        lock_f = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # Use a sidecar lockfile so the JSON itself is never half-locked.
            lock_f = self.path.with_suffix(".lock").open("a+")
            fcntl.flock(lock_f.fileno(), fcntl.LOCK_EX)
        except OSError as exc:
            if lock_f is not None:
                lock_f.close()
            _warn_unwritable_once(self.path, exc)
            yield CostLedger(), lambda new: None
            return
        try:
            ledger = self._read()
            yield ledger, self._write
        finally:
            fcntl.flock(lock_f.fileno(), fcntl.LOCK_UN)
            lock_f.close()

    # ------- public API -------

    def total(self) -> float:
        with self._locked() as (ledger, _w):
            return ledger.total_usd

    def record(
        self,
        *,
        model: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read_tokens: int = 0,
        cache_creation_tokens: int = 0,
    ) -> float:
        """Record one API call via token counts (estimator). Returns new total."""
        usd = estimate_cost_usd(
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_creation_tokens=cache_creation_tokens,
        )
        return self.record_usd(model=model, usd=usd)

    def record_usd(self, *, model: str, usd: float) -> float:
        """Record one API call by exact USD amount (e.g. SDK-reported total).

        Preferred over `record()` when the runtime gives us authoritative cost.
        """
        with self._locked() as (ledger, write):
            ledger.total_usd += float(usd)
            ledger.calls += 1
            ledger.by_model[model] = ledger.by_model.get(model, 0.0) + float(usd)
            write(ledger)
            return ledger.total_usd

    def would_exceed(self, cap_usd: float | None, additional: float = 0.0) -> bool:
        """Check whether spending `additional` more would exceed cap."""
        if cap_usd is None:
            return False
        return (self.total() + additional) > cap_usd
