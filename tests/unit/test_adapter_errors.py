"""Adapter error taxonomy."""
from a11yfix.ai.errors import AdapterCallError, AdapterUnavailable


def test_unavailable_is_runtimeerror():
    # cli.py catches RuntimeError to skip stage 3; batch partial-status
    # detection keys off that same path. Subclassing is load-bearing.
    assert issubclass(AdapterUnavailable, RuntimeError)


def test_call_error_is_runtimeerror():
    assert issubclass(AdapterCallError, RuntimeError)
