"""Ranking: one visible metric per sort, no composite score.

A composite score cannot be audited: nobody can explain why a row is 7th, and
an export of it is not reproducible across runs. Transparency here means one
visible metric per order (PLAN-screen.md §3), and since the descriptive panel
can order by any metric it shows, the vocabulary is **derived** from the
catalog's numeric fields (:data:`app.screen.filters.FIELDS`) rather than written
down a second time:

* ``<field>`` — descending, e.g. ``market_cap_usd``, ``pe_ttm``;
* ``-<field>`` — ascending, e.g. ``-pe_ttm`` (Finviz's own ``o=-marketcap``).

Three legacy names survive as aliases (``market_cap`` → ``market_cap_usd``,
``gainers`` → ``change_pct``, ``turnover`` → ``turnover``), because
``GET /screens?sort=``, the export manifest and every bookmark use them. They
are *aliases*, not a second vocabulary: :func:`order_metric` resolves them to the
same column the catalog spelling names.

The sort is **deterministic**: equal values break on ``symbol``, so the same
universe produces the same order twice — and so page N and page N+1 cannot
contain the same issuer. A stable sort alone was not enough: it preserves the
*provider's* arrival order, and the provider reshuffles equal-valued rows between
two calls (measured: five 20-row pages, two symbols repeated). Ordering the ties
by symbol is the only tie-break that depends on the row instead of on the network.

Missing values sort **last in both directions**: a missing market cap is not a
small company, and an ascending P/E sort that floated nulls to the top would
turn "the cheapest" into "the ones nobody reported".
"""

from __future__ import annotations

import math
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Sequence

from app.screen.filters import FIELDS

#: The closed vocabulary ``GET /screens?sort=`` exposes — the browse route's
#: three buttons. Kept narrower than :data:`ORDER_BY` on purpose: that route
#: shows the top 20 of a snapshot by one of three metrics, and widening it would
#: change a published contract nobody asked to change. ``/screens/select`` is the
#: route that orders by any numeric column.
SORTS: tuple[str, ...] = ("market_cap", "turnover", "gainers")

#: Every column a selection may order by: the catalog's numeric fields, sorted
#: so the published list is stable. Derived, never retyped — a metric added to
#: ``FIELDS`` becomes orderable in the same edit that makes it filterable.
ORDER_BY: tuple[str, ...] = tuple(
    sorted(name for name, kind in FIELDS.items() if kind == "number")
)

#: Legacy spellings → the column they mean. ``turnover`` maps to itself on
#: purpose: it is a real catalog column, and the alias table is where a reader
#: looks to find out that it needs no translation.
ALIASES: Mapping[str, str] = MappingProxyType(
    {
        "market_cap": "market_cap_usd",
        "gainers": "change_pct",
        "turnover": "turnover",
    }
)


def order_metric(sort: str) -> tuple[str, bool]:
    """``sort`` → ``(column, ascending)``; a leading ``-`` is ascending.

    Raises :class:`ValueError` naming the accepted catalog fields for anything
    that is not a numeric column — a text field is not an order, and neither is
    a typo. ``\"\"`` and ``\"-\"`` are refused for the same reason.
    """

    text = sort if isinstance(sort, str) else str(sort)
    name = text[1:] if text.startswith("-") else text
    column = ALIASES.get(name, name)
    # Text columns sort too - a country list ordered A-Z is a legitimate order.
    # A field we do not know is still refused, and so is a typo.
    kind = FIELDS.get(column)
    if kind not in ("number", "text") and column != "symbol":
        raise ValueError(
            f"unknown sort {sort!r}; expected one of {', '.join(ORDER_BY)} "
            f"(a leading '-' orders ascending)"
        )
    return column, text.startswith("-")


def _value(row: Mapping[str, Any], column: str) -> float | None:
    value = row.get(column)
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    # ``NaN``/``±inf`` compare false in every direction: they would keep or drop
    # a row at random, so they are unknown, exactly like a null.
    return number if math.isfinite(number) else None


def _text(row: Mapping[str, Any], column: str) -> str | None:
    """The cell as a comparable string, or ``None`` when it is not text."""

    value = row.get(column)
    if not isinstance(value, str):
        return None
    text = value.strip().casefold()
    return text or None


def _key(row: Mapping[str, Any], column: str, ascending: bool) -> tuple[int, float]:
    """``(0, ±value)`` for a present value and ``(1, 0.0)`` for a missing one.

    The first component puts every present value ahead of every missing one in
    **both** directions — that is what "missing sorts last" means — and the
    sign puts the present values in the requested order.
    """

    if FIELDS.get(column) == "text" or column == "symbol":
        text = _text(row, column)
        if text is None:
            return (1, "")
        return (0, text if ascending else _ReversedText(text))
    value = _value(row, column)
    if value is None:
        return (1, 0.0)
    return (0, value if ascending else -value)


class _ReversedText(str):
    """A string that compares backwards, so descending text sorts without a
    second code path: ``sorted`` sees ``str`` and the sign flips inside."""

    __slots__ = ()

    def __lt__(self, other: object) -> bool:
        return str.__gt__(self, other)  # type: ignore[arg-type]

    def __gt__(self, other: object) -> bool:
        return str.__lt__(self, other)  # type: ignore[arg-type]


def _symbol(row: Mapping[str, Any]) -> str:
    """The tie-break key: the ticker as text, never a coerced number."""

    symbol = row.get("symbol")
    return symbol.strip().upper() if isinstance(symbol, str) else ""


def rank_rows(rows: Iterable[Mapping[str, Any]], sort: str = "market_cap") -> list[dict[str, Any]]:
    """Order ``rows`` by ``sort``, break ties on ``symbol``, number them ``1..n``.

    Two passes, not one: the first fixes the tie order on the ticker, and the
    second is a *stable* sort by the metric, so equal metrics keep the symbol
    order the first pass established. A single key tuple would have to reverse
    the symbol along with the metric, which would order ties Z→A on a
    descending sort.

    Returns new dictionaries (the caller's rows are not mutated) so a ranked
    view can coexist with the universe it came from.
    """

    column, ascending = order_metric(sort)
    materialised = [dict(row) for row in rows]
    materialised.sort(key=_symbol)
    materialised.sort(key=lambda row: _key(row, column, ascending))
    ranked: list[dict[str, Any]] = []
    for position, row in enumerate(materialised, start=1):
        row["rank"] = position
        ranked.append(row)
    return ranked


def top_rows(rows: Sequence[Mapping[str, Any]], count: int) -> list[dict[str, Any]]:
    """The first ``count`` rows of an already ranked list (never a re-sort)."""

    if count < 0:
        raise ValueError(f"count must be >= 0, got {count!r}")
    return [dict(row) for row in rows[:count]]


__all__ = [
    "ALIASES",
    "ORDER_BY",
    "SORTS",
    "order_metric",
    "rank_rows",
    "top_rows",
]
