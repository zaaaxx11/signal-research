"""V1..V16 as pure functions: (row, context) -> list[Rejection] (02 §3.7)."""

from __future__ import annotations

from typing import Any, Mapping


def evaluate(row: Mapping[str, Any], *, rule_set: str = "v1") -> list[dict[str, Any]]:
    """Evaluate every rule for one row. Pure: no clock, no DB, no network."""

    raise NotImplementedError("verify.rules.evaluate is not implemented (Phase 4 RED)")
