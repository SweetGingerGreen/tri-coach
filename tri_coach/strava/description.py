"""
Decide what to write into an activity's public description.

The athlete's own words win: the default ``fill`` mode only writes when the
description is empty, and ``append`` keeps whatever is already there.
"""
from __future__ import annotations


MODES = ("fill", "append", "replace")


def normalize(text: str | None) -> str:
    return " ".join((text or "").split())


def plan_description(current: str | None, new: str, mode: str = "fill") -> tuple[str | None, str]:
    """Return (text to write, or None for no change, and a reason code)."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
    current = (current or "").strip()
    new = new.strip()
    if not new:
        raise ValueError("refusing to write an empty description")
    if mode == "fill":
        if current:
            return None, "has-own-description"
        return new, "filled"
    if mode == "append":
        if not current:
            return new, "filled"
        if normalize(new) in normalize(current):
            return None, "already-present"
        return f"{current}\n\n{new}", "appended"
    if normalize(current) == normalize(new):
        return None, "unchanged"
    return new, "replaced"
