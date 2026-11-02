"""Ranking: one visible metric per sort, no composite score.

A composite score cannot be audited: nobody can explain why a row is 7th, and
an export of it is not reproducible across runs. Transparency here means one
visible metric per order (PLAN-screen.md §3), and since the descriptive panel
can order by any metric it shows, the vocabulary is **derived** from the
catalog's numeric fields (:data:`app.screen.filters.FIELDS`) rather than written
down a second time:

* ``<field>`` — descending, e.g. ``market_cap_usd``, ``pe_ttm``;
* ``-<field>`` — ascending, e.g. ``-pe_ttm`` (Finviz's own ``o=-marketcap``).

**The vocabulary is the catalogue's, not a second one.** A client does not know
that the row spells the one-month return ``ret_1m``; it knows the column it
draws, which is the catalogue key ``perf_4w``. So every catalogue/UI spelling
that names a different row column is an alias of it — ``perf_4w`` →
``ret_1m``, ``pe`` → ``pe_ttm``, ``sma50`` → ``sma_50``, ``company`` →
``name`` — read from :data:`app.screen.columns.CATALOGUE_COLUMNS`, the one
table that already answers "which row field does this catalogue key mean?" for
the views, the ``columns`` projection and the filter DSL. There is no list
here to drift: a key that resolves to a comparable row column sorts, and a key
whose column no row carries (``eps``, ``news_url``, ``security_type``) is not an
alias at all, so it stays the 422 it should be. A catalogue key that already
*is* the row column (``sector``, ``price``, ``volume``) needs no entry either:
resolution falls through to :data:`app.screen.filters.FIELDS`.

The three legacy names the browse route and every bookmark use survive in the
same table: ``market_cap`` → ``market_cap_usd``, ``gainers`` → ``change_pct``,
and ``turnover`` → itself (a real catalog column, mapped to itself on purpose so
that a reader looking for "does turnover need translating?" finds the answer).

**Two refusals, both explicit.** A token that names nothing stays a ``ValueError``
(``422 unknown_sort``) naming the accepted fields — widening the vocabulary must
not turn a typo into a silent market-cap sort. ``no`` is refused with its own
message: it is the one column the client draws from the page offset, the server
has no per-row ordinal, and a sort on it could only be empty. It is listed in
:data:`CLIENT_ONLY_SORTS` so a refusal and the client's own
``CLIENT_COLUMNS`` say the same thing.

An alias may never re-kind a column: a text field the row already spells
(``sector``) cannot be pointed at a number by a synonym, because
:func:`_key` chooses its comparison from the *target's* kind — so a text sort
stays alphabetical instead of being coerced through ``float()``.

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

from app.screen.columns import CATALOGUE_COLUMNS
from app.screen.filters import FIELDS

#: The closed vocabulary ``GET /screens?sort=`` exposes — the browse route's
#: three buttons. Kept narrower than :data:`ORDER_BY` on purpose: that route
#: shows the top 20 of a snapshot by one of three metrics, and widening it would
#: change a published contract nobody asked to change. ``/screens/select`` is the
#: route that orders by any comparable column, under its own or its catalogue
#: spelling (:data:`ALIASES`).
SORTS: tuple[str, ...] = ("market_cap", "turnover", "gainers")

#: Every column a selection may order by: the catalog's numeric fields, sorted
#: so the published list is stable. Derived, never retyped — a metric added to
#: ``FIELDS`` becomes orderable in the same edit that makes it filterable.
ORDER_BY: tuple[str, ...] = tuple(
    sorted(name for name, kind in FIELDS.items() if kind == "number")
)

#: Comparison kinds a sort can use. A field outside this set (``is_primary`` is
#: a bool) is not an order.
_SORTABLE_KINDS: frozenset[str] = frozenset({"number", "text"})

#: Catalogue/UI spelling → the row column it names, for the spellings the row
#: writes differently. Derived from :data:`app.screen.columns.CATALOGUE_COLUMNS`
#: — the single answer to "which row field does this key mean?" — so the
#: screener's headers, the ``columns`` projection and the filter DSL cannot
#: disagree. Two filters are deliberate:
#:
#: * identity keys (``sector``, ``price``, ``volume``) are omitted, because
#:   :func:`order_metric` already falls through to ``FIELDS`` for them;
#: * a key whose column no row carries (``eps``, ``net_income``, ``news_url``,
#:   ``security_type``) is omitted too, so the catalogue spelling of an absent
#:   metric stays a 422 instead of sorting an all-null column.
_CATALOGUE_ALIASES: dict[str, str] = {
    name: column
    for name, column in CATALOGUE_COLUMNS.items()
    if name != column and FIELDS.get(column) in _SORTABLE_KINDS
}

#: The spellings ``criteria.sort`` accepts that are not a row column's own name.
#: The catalogue half is derived (above); the three legacy names are the browse
#: route's published contract and every bookmark that predates the panel.
ALIASES: Mapping[str, str] = MappingProxyType(
    {
        **_CATALOGUE_ALIASES,
        "gainers": "change_pct",
        "turnover": "turnover",
    }
)

#: Columns the client draws from the page's own geometry, and the reason a sort
#: on one is refused. ``no`` is ``ROW_NUMBER_KEY``/``CLIENT_COLUMNS`` on the
#: client: the server has no per-row ordinal, so there is no column to order and
#: an accepted token would answer an empty page that reads as "no rows".
CLIENT_ONLY_SORTS: Mapping[str, str] = MappingProxyType(
    {
        "no": (
            "the row number is drawn from the page offset in the browser, not a "
            "row column, so the server has no ordinal to order by"
        )
    }
)


def order_metric(sort: str) -> tuple[str, bool]:
    """``sort`` → ``(column, ascending)``; a leading ``-`` is ascending.

    The token may be a row column (``ret_1m``), a catalogue/UI spelling of one
    (``perf_4w``, ``pe``, ``company``) or a legacy name (``gainers``); the
    leading ``-`` is stripped before resolution and survives it.

    Raises :class:`ValueError` naming the accepted catalog fields for anything
    that is not a comparable column — a typo, an empty string, a client-only
    column (whose refusal names the reason instead).
    """

    text = sort if isinstance(sort, str) else str(sort)
    name = text[1:] if text.startswith("-") else text
    client_only = CLIENT_ONLY_SORTS.get(name)
    if client_only is not None:
        raise ValueError(
            f"sort {sort!r} is client-only: {client_only}; "
            "order by a served column such as market_cap, pe or perf_4w"
        )
    column = ALIASES.get(name, name)
    # Text columns sort too - a country list ordered A-Z is a legitimate order.
    # A field we do not know is still refused, and so is a typo.
    kind = FIELDS.get(column)
    if kind not in _SORTABLE_KINDS and column != "symbol":
        raise ValueError(
            f"unknown sort {sort!r}; expected one of {', '.join(ORDER_BY)} "
            "(or a catalogue/UI spelling such as pe, perf_4w or market_cap; "
            "a leading '-' orders ascending)"
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
    "CLIENT_ONLY_SORTS",
    "ORDER_BY",
    "SORTS",
    "order_metric",
    "rank_rows",
    "top_rows",
]
