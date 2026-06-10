"""Backend-agnostic adapter exceptions.

Both subclass RuntimeError because cli.py's stage-3 skip path catches
RuntimeError, and the batch `partial` status detection keys off the
FileResult that path produces — existing behavior (F1 fail gate) must
keep working unchanged.
"""

from __future__ import annotations


class AdapterUnavailable(RuntimeError):
    """The backend cannot run at all: missing binary, package, or credentials."""


class AdapterCallError(RuntimeError):
    """A single model call failed after retries — defer the finding, don't crash."""
