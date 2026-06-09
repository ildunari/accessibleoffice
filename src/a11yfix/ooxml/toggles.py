"""OOXML boolean/toggle helpers."""

from __future__ import annotations

from a11yfix.ooxml.namespaces import qn

OFF_VALUES = {"0", "false", "off"}
ON_VALUES = {"1", "true", "on"}


def w_on_off_enabled(el: object | None) -> bool:
    """Return True for present Word OnOff elements unless w:val explicitly disables them."""
    if el is None:
        return False
    val = (el.get(qn("w:val")) or el.get("val") or "").lower()  # type: ignore[union-attr]
    return val not in OFF_VALUES


def attr_bool_enabled(value: str | None) -> bool:
    return (value or "").lower() in ON_VALUES
