"""Ranking: three transparent sorts, no composite score.

A composite score cannot be audited: nobody can explain why a row is 7th, and
an export of it is not reproducible across runs. Transparency here means one
visible metric per sort (PLAN-screen.md §3):

* ``market_cap`` → ``market_cap_usd`` desc,
* ``turnover``   → ``turnover`` (traded value, local currency) desc,
* ``gainers``    → ``change_pct`` desc.

The sort is **stable**: equal values keep the order they arrived in, so the same
scan produces the same ranks twice — a screen whose order drifts between two
reads of one snapshot is unreadable. Missing values sort last, never as ``0``
(a missing market cap is not a small company).
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

#: The closed vocabulary the API exposes (``GET /screens?sort=``).
SORTS: tuple[str, ...] = ("market_cap", "turnover", "gainers")

#: Which visible metric each sort orders by.
SORT_METRICS: dict[str, str] = {
    "market_cap": "market_cap_usd",
    "turnover": "turnover",
    "gainers": "change_pct",
}


def metric_for(sort: str) -> str:
    """The column ``sort`` orders by; an unknown sort is a caller error."""

    try:
        return SORT_METRICS[sort]
    except KeyError as exc:
        raise ValueError(
            f"unknown sort {sort!r}; expected one of {', '.join(SORTS)}"
        ) from exc


def _value(row: Mapping[str, Any], column: str) -> float | None:
    value = row.get(column)
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _key(row: Mapping[str, Any], column: str) -> tuple[int, float]:
    """Descending sort with the missing values last.

    ``(1, value)`` for a present value and ``(0, 0.0)`` for a missing one: with
    ``reverse=True`` the first component puts every present value ahead of every
    missing one, and the value itself decides inside each group.
    """

    value = _value(row, column)
    if value is None:
        return (0, 0.0)
    return (1, value)


def rank_rows(rows: Iterable[Mapping[str, Any]], sort: str = "market_cap") -> list[dict[str, Any]]:
    """Order ``rows`` by ``sort`` and number them ``1..n``.

    Returns new dictionaries (the caller's rows are not mutated) so a ranked
    view can coexist with the universe it came from.
    """

    column = metric_for(sort)
    materialised = [dict(row) for row in rows]
    ordered = sorted(materialised, key=lambda row: _key(row, column), reverse=True)
    ranked: list[dict[str, Any]] = []
    for position, row in enumerate(ordered, start=1):
        row["rank"] = position
        ranked.append(row)
    return ranked


def top_rows(rows: Sequence[Mapping[str, Any]], count: int) -> list[dict[str, Any]]:
    """The first ``count`` rows of an already ranked list (never a re-sort)."""

    if count < 0:
        raise ValueError(f"count must be >= 0, got {count!r}")
    return [dict(row) for row in rows[:count]]


__all__ = ["SORTS", "SORT_METRICS", "metric_for", "rank_rows", "top_rows"]
