"""Strict unit/scale parsing. "N/A" -> None, never 0 (02 §3.7)."""

from __future__ import annotations


def parse_number(raw: object, *, unit: str) -> float | None:
    """Parse a provider scalar; raise on sentinels, never coerce to zero."""

    raise NotImplementedError("verify.units.parse_number is not implemented (Phase 4 RED)")
