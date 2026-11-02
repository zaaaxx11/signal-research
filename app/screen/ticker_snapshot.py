"""One symbol's catalogue row: the scan cell, the depth gap, and its unit.

The ticker page's six-column snapshot asks one question — "what does the
catalogue know about this symbol *right now*" — and until this module it
answered it from three places with three vocabularies: the SEC facts document
(``GET /engine/fundamentals/{symbol}``), the selection row, and the profile.
The facts document has no P/E, PEG, P/S, P/B, P/FCF, EV/EBITDA, EV/Sales or
quick ratio at all, so twelve Valuation cells rendered an em dash; it *does*
compute ``debt_to_equity`` as ``total_debt / equity * 100`` (76.54) while
declaring the cell a multiple, so the one number that arrived was a percentage
printed as a ratio.

This module is the single answer: **the scan row the screener already builds**
(read through :func:`app.screen.columns.value_for`, so a row's ``pe_ttm``,
``sma_50`` and ``float_shares`` spellings resolve to ``pe``, ``sma50`` and
``shares_float``), **plus the depth overlay the screener already merges**
(:func:`app.screen.depth_overlay.overlay`, fill-missing-only). No second scan,
no second computation: ``prev_close`` is the screener's own identity, imported
from :func:`app.screen.fundamentals.derive_view_cells`, and nothing else is
arithmetic.

The unit contract
-----------------
A cell's unit is part of its value, and this module states it for every key a
client formats as a quantity in a per-response ``units`` map. The vocabulary is
closed and has exactly five members:

* ``ratio`` — a dimensionless multiple (P/E, EV/EBITDA, Debt/Equity);
* ``percent`` — percent points, ``18.52`` is 18.52% (performance, margins);
* ``usd`` — money (price, market cap, per-share levels);
* ``count`` — a quantity of things (shares outstanding, float, volume, employees);
* ``bool`` — a yes/no flag (optionable, shortable).

Date cells (``ipo_date``, ``earnings_date``, ``dividend_ex_date``) and the one
list cell (``index_membership``) are **not** in the map: their values are
self-describing text, and inventing a numeric unit for them would be the same
class of lie this endpoint exists to end. ``values`` still carries them.

**The two leverage keys are ratios.** ``debt_to_equity`` and
``lt_debt_to_equity`` hold ``0.7844`` — never ``78.44`` — because the scan row
and the depth store both carry the ratio (TradingView's provider column is
``0.784``; the depth store normalises Yahoo's percent at its own write
boundary). The percentage spelling has its own key, ``debt_to_equity_pct`` /
``lt_debt_to_equity_pct``, and its own ``percent`` unit. A client therefore has
one unambiguous place to put a ratio and one to put a percentage, and no way to
print 76.54 next to "Debt/Equity". :func:`leverage_pair` states the conversion
once — ``76.54`` + ``unit="percent"`` → ``(0.7654, 76.54)`` — and is the only
place the two spellings can be derived from each other.

Two keys are **published in the unit the source actually measured**, because
the scan and the depth store disagree:

* ``sma20`` / ``sma50`` / ``sma200`` are price **levels** in the parity scan
  (a dollar figure, what the client draws) and **percent distances from the
  moving average** in the depth store (``(close / SMA - 1) * 100``);
* ``eps_this_y`` / ``eps_next_y`` are per-share **levels** in the catalogue and
  **growth percentages** in the depth store.

When the scan carried the cell, ``units`` states the catalogue unit (``usd``);
when only the depth store could fill it, the response states ``percent`` for
those five keys (:data:`DEPTH_UNIT_OVERRIDES`). Keeping the number while
lying about its unit is exactly the class of bug this route exists to end, so
the alternative — dropping the observation — is the only other honest answer,
and a value with an accurate unit is worth more than an em dash.

Absence
-------
A cell neither store carried is ``None`` and is named in ``missing``; a reported
``0`` is a value (a zero P/E is not an unknown P/E) and a non-finite number is
not. ``index_membership`` keeps the distinction the header already makes:
``None`` means the vendored snapshot was not read (unknown), ``[]`` means it was
read and the symbol is in none of the four.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any

from app.screen.columns import value_for
from app.screen.fundamentals import derive_view_cells

__all__ = [
    "DEPTH_UNIT_OVERRIDES",
    "GRID_COLUMNS",
    "LEVERAGE_KEYS",
    "SNAPSHOT_KEYS",
    "SNAPSHOT_UNITS",
    "SNAPSHOT_UNIT_VOCABULARY",
    "TEXT_KEYS",
    "flag_value",
    "leverage_pair",
    "snapshot_document",
]

# ---------------------------------------------------------------------------
# The grid — six columns by fourteen rows, the client's own grouping
# ---------------------------------------------------------------------------

#: The Overview snapshot, column by column, exactly as
#: ``web/src/stock/snapshot.ts`` draws it. Kept here because the backend has to
#: answer all 84 cells in **one** call: a client that has to ask three documents
#: and merge them is a client that renders an em dash whenever one of the three
#: does not carry a key. The order is the draw order; ``test_engine_snapshot``
#: pins the six-by-fourteen shape.
GRID_COLUMNS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "profile": (
            "index_membership",
            "market_cap",
            "enterprise_value",
            "net_income",
            "sales",
            "book_per_share",
            "cash_per_share",
            "dividend",
            "dividend_ttm",
            "dividend_ex_date",
            "dividend_growth_3y",
            "payout_ratio",
            "employees",
            "ipo_date",
        ),
        "valuation": (
            "pe",
            "forward_pe",
            "peg",
            "ps",
            "pb",
            "pc",
            "pfcf",
            "ev_ebitda",
            "ev_sales",
            "quick_ratio",
            "current_ratio",
            "debt_to_equity",
            "lt_debt_to_equity",
            "optionable",
        ),
        "growth": (
            "eps",
            "eps_next_q",
            "eps_next_y",
            "eps_this_y",
            "eps_past_3y",
            "eps_past_5y",
            "eps_qoq",
            "eps_yoy_ttm",
            "sales_qoq",
            "sales_yoy_ttm",
            "earnings_date",
            "recommendation",
            "target_price",
            "dividend_growth_5y",
        ),
        "quality": (
            "insider_own",
            "insider_trans",
            "inst_own",
            "inst_trans",
            "roa",
            "roe",
            "roic",
            "gross_margin",
            "operating_margin",
            "net_margin",
            "sma20",
            "sma50",
            "sma200",
            "float_pct",
        ),
        "technicals": (
            "shares_outstanding",
            "shares_float",
            "short_float",
            "short_ratio",
            "short_interest",
            "high_52w",
            "low_52w",
            "volatility_1w",
            "volatility_4w",
            "atr",
            "rsi",
            "beta",
            "average_volume",
            "volume",
        ),
        "performance": (
            "perf_1w",
            "perf_4w",
            "perf_13w",
            "perf_26w",
            "perf_ytd",
            "perf_52w",
            "perf_3y",
            "perf_5y",
            "perf_10y",
            "change",
            "change_from_open",
            "gap",
            "prev_close",
            "price",
        ),
    }
)

#: Keys the grid's labels ask for under a second spelling, a cell the grid does
#: not draw but the ticker page's header needs (``dividend_yield``), plus the
#: percent sibling of the two leverage keys. ``change_pct`` is the wire name the
#: rest of the API uses for the grid's ``change``; ``float_shares`` is the row's
#: field name for the grid's ``shares_float``; ``shortable`` is the depth
#: store's second availability flag (the grid only draws ``optionable``).
_EXTRA_KEYS: tuple[str, ...] = (
    "dividend_yield",
    "change_pct",
    "float_shares",
    "shortable",
    "debt_to_equity_pct",
    "lt_debt_to_equity_pct",
)

#: Every key the response publishes, in draw order: the 84 grid cells followed
#: by the five extras. ``dict.fromkeys`` is the guard against a key that a
#: future column adds twice — the document would otherwise be one key shorter
#: than the grid and one cell wider.
SNAPSHOT_KEYS: tuple[str, ...] = tuple(
    dict.fromkeys(
        [key for keys in GRID_COLUMNS.values() for key in keys] + list(_EXTRA_KEYS)
    )
)

#: The closed unit vocabulary. A unit not in this set is a typo, not a format.
SNAPSHOT_UNIT_VOCABULARY: frozenset[str] = frozenset(
    {"ratio", "percent", "usd", "count", "bool"}
)

#: Keys whose value is text (a date or a list) rather than a quantity. They
#: still appear in ``values``; they simply have no entry in ``units`` because
#: there is no formatting decision to state.
TEXT_KEYS: frozenset[str] = frozenset(
    {"index_membership", "dividend_ex_date", "ipo_date", "earnings_date"}
)

#: The leverage cells: one ratio on the wire, one percent sibling beside it.
LEVERAGE_KEYS: tuple[str, ...] = ("debt_to_equity", "lt_debt_to_equity")

#: Units per key. Every key a client formats as a quantity has exactly one
#: entry; the four :data:`TEXT_KEYS` do not, because their values are already
#: text. A test asserts the two sets differ by exactly those four rather than
#: trusting a reader to notice a missing line. Values follow the **grid's**
#: semantics (the client's column kinds), which is why ``sma50`` is a dollar
#: level here even though ``app.market.fields`` — the Finviz print table —
#: calls the cell a percent.
SNAPSHOT_UNITS: Mapping[str, str] = MappingProxyType(
    {
        # -- profile --------------------------------------------------------
        "market_cap": "usd",
        "enterprise_value": "usd",
        "net_income": "usd",
        "sales": "usd",
        "book_per_share": "usd",
        "cash_per_share": "usd",
        "dividend": "usd",
        "dividend_ttm": "usd",
        "dividend_growth_3y": "percent",
        "payout_ratio": "percent",
        "employees": "count",
        # -- valuation ------------------------------------------------------
        "pe": "ratio",
        "forward_pe": "ratio",
        "peg": "ratio",
        "ps": "ratio",
        "pb": "ratio",
        "pc": "ratio",
        "pfcf": "ratio",
        "ev_ebitda": "ratio",
        "ev_sales": "ratio",
        "quick_ratio": "ratio",
        "current_ratio": "ratio",
        "debt_to_equity": "ratio",
        "lt_debt_to_equity": "ratio",
        "optionable": "bool",
        # -- growth ---------------------------------------------------------
        "eps": "usd",
        "eps_next_q": "usd",
        "eps_next_y": "usd",
        "eps_this_y": "usd",
        "eps_past_3y": "percent",
        "eps_past_5y": "percent",
        "eps_qoq": "percent",
        "eps_yoy_ttm": "percent",
        "sales_qoq": "percent",
        "sales_yoy_ttm": "percent",
        "recommendation": "ratio",
        "target_price": "usd",
        "dividend_growth_5y": "percent",
        # -- quality --------------------------------------------------------
        "insider_own": "percent",
        "insider_trans": "percent",
        "inst_own": "percent",
        "inst_trans": "percent",
        "roa": "percent",
        "roe": "percent",
        "roic": "percent",
        "gross_margin": "percent",
        "operating_margin": "percent",
        "net_margin": "percent",
        "sma20": "usd",
        "sma50": "usd",
        "sma200": "usd",
        "float_pct": "percent",
        # -- technicals -----------------------------------------------------
        "shares_outstanding": "count",
        "shares_float": "count",
        "short_float": "percent",
        "short_ratio": "ratio",
        "short_interest": "count",
        "high_52w": "usd",
        "low_52w": "usd",
        "volatility_1w": "percent",
        "volatility_4w": "percent",
        "atr": "usd",
        "rsi": "ratio",
        "beta": "ratio",
        "average_volume": "count",
        "volume": "count",
        # -- performance ----------------------------------------------------
        "perf_1w": "percent",
        "perf_4w": "percent",
        "perf_13w": "percent",
        "perf_26w": "percent",
        "perf_ytd": "percent",
        "perf_52w": "percent",
        "perf_3y": "percent",
        "perf_5y": "percent",
        "perf_10y": "percent",
        "change": "percent",
        "change_from_open": "percent",
        "gap": "percent",
        "prev_close": "usd",
        "price": "usd",
        # -- the extras -----------------------------------------------------
        "dividend_yield": "percent",
        "change_pct": "percent",
        "float_shares": "count",
        "shortable": "bool",
        "debt_to_equity_pct": "percent",
        "lt_debt_to_equity_pct": "percent",
    }
)

#: Keys the depth store measures in a **different unit** from the scan row.
#: ``sma*`` is a price level in the parity scan and a percent distance from the
#: average in the depth store; ``eps_this_y`` / ``eps_next_y`` are per-share
#: levels in the catalogue and growth percentages in the depth store. A cell
#: only the depth store could fill declares the store's unit instead of the
#: catalogue's — see the module docstring.
DEPTH_UNIT_OVERRIDES: Mapping[str, str] = MappingProxyType(
    {
        "sma20": "percent",
        "sma50": "percent",
        "sma200": "percent",
        "eps_this_y": "percent",
        "eps_next_y": "percent",
    }
)

#: The two availability flags, in the depth store's ``"Yes"``/``"No"`` spelling.
FLAG_KEYS: frozenset[str] = frozenset({"optionable", "shortable"})

#: Date cells keep the source's ISO text; the scan's epoch seconds are the one
#: numeric spelling, converted in :func:`_date` rather than guessed at.
DATE_KEYS: frozenset[str] = frozenset(
    {"ipo_date", "earnings_date", "dividend_ex_date"}
)

_YES = frozenset({"yes", "y", "true", "t", "1"})
_NO = frozenset({"no", "n", "false", "f", "0"})


# ---------------------------------------------------------------------------
# One cell
# ---------------------------------------------------------------------------


def leverage_pair(value: Any, *, unit: str = "ratio") -> tuple[float | None, float | None]:
    """``(ratio, percent)`` for one leverage cell stated in ``unit``.

    The one place the two spellings are related. ``unit="percent"`` is the
    engine's SEC derivation (``total_debt / equity * 100``: 76.54) and yields
    ``(0.7654, 76.54)``; ``unit="ratio"`` is the scan row and the depth store
    (0.7844) and yields ``(0.7844, 78.44)``. An unknown unit is treated as a
    ratio — the catalogue's own declaration — rather than silently scaled.
    """

    number = _number(value)
    if number is None:
        return (None, None)
    if unit == "percent":
        return (number / 100.0, number)
    return (number, number * 100.0)


def flag_value(value: Any) -> bool | None:
    """``"Yes"``/``"No"`` (and their variants) as a boolean, or ``None``.

    The depth store publishes availability as text; the grid's badge prints a
    boolean. Anything that is not an unambiguous yes/no — a number other than
    ``1``/``0``, a blank, ``"maybe"`` — is ``None``: an unknown flag must not
    read as "not optionable".
    """

    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
        if not math.isfinite(number):
            return None
        if number == 1.0:
            return True
        if number == 0.0:
            return False
        return None
    if isinstance(value, str):
        text = value.strip().casefold()
        if text in _YES:
            return True
        if text in _NO:
            return False
    return None


# ---------------------------------------------------------------------------
# The document
# ---------------------------------------------------------------------------


def snapshot_document(
    symbol: str,
    row: Mapping[str, Any],
    *,
    as_of: str | None = None,
    index_members: Sequence[str] | None = None,
    scan_row: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """One symbol's catalogue row, its units, and the keys it could not fill.

    ``row`` is the scan row **after** the depth overlay (the route passes the
    merge); ``scan_row`` is the same row **before** it, used only to tell which
    cells only the depth store could fill so ``units`` can state the store's
    unit for the five keys where the two sources disagree. Omitting
    ``scan_row`` means "assume every cell came from the catalogue" — the right
    answer for a row that arrived whole.

    ``index_members`` is :func:`app.api.engine.index_members_for`'s answer and
    keeps its three-way meaning (``None`` unknown, ``[]`` in none of the four,
    a list of names). ``as_of`` is the depth collector's stamp, or ``None``.
    """

    target = str(symbol or "").strip().upper()
    values: dict[str, Any] = {}
    units = dict(SNAPSHOT_UNITS)

    if scan_row is not None:
        for key in SNAPSHOT_KEYS:
            if key in DEPTH_UNIT_OVERRIDES and _read(scan_row, key, index_members) is None:
                if _read(row, key, index_members) is not None:
                    units[key] = DEPTH_UNIT_OVERRIDES[key]

    for key in SNAPSHOT_KEYS:
        values[key] = _read(row, key, index_members)

    return {
        "symbol": target,
        "as_of": as_of,
        "values": values,
        "units": units,
        "missing": [key for key in SNAPSHOT_KEYS if values[key] is None],
    }


def _read(
    row: Mapping[str, Any], key: str, index_members: Sequence[str] | None
) -> Any:
    """One catalogue key off one row, in the unit the response publishes."""

    if key == "index_membership":
        return None if index_members is None else list(index_members)

    if key in LEVERAGE_KEYS:
        ratio, _percent = leverage_pair(_catalogue_cell(row, key))
        return ratio

    if key.endswith("_pct") and key[: -len("_pct")] in LEVERAGE_KEYS:
        _ratio, percent = leverage_pair(_catalogue_cell(row, key[: -len("_pct")]))
        return percent

    if key == "prev_close":
        return _prev_close(row)

    if key in FLAG_KEYS:
        return flag_value(_catalogue_cell(row, key))

    if key in DATE_KEYS:
        return _date(_catalogue_cell(row, key))

    return _number(_catalogue_cell(row, key))


def _catalogue_cell(row: Mapping[str, Any], key: str) -> Any:
    """The row's cell for ``key``, through the one name→field table.

    ``value_for`` is the screener's resolver: ``pe`` reads ``pe_ttm``,
    ``sma50`` reads ``sma_50``, ``shares_float`` reads ``float_shares``, and a
    synonym only fills a ``None``. Reading ``row.get(key)`` here would answer
    ``None`` for every one of those keys on a real scan row.
    """

    return value_for(row, key)


def _prev_close(row: Mapping[str, Any]) -> float | None:
    """The screener's own ``price / (1 + change_pct / 100)`` identity.

    A row that already carries ``prev_close`` (the depth store does) keeps it;
    otherwise the identity is imported from
    :func:`app.screen.fundamentals.derive_view_cells` rather than restated, so a
    header, a snapshot and a wide table cannot print three different closes.
    """

    own = _number(value_for(row, "prev_close"))
    if own is not None:
        return own
    return _number(derive_view_cells(row, None).get("prev_close"))


def _date(value: Any) -> str | None:
    """A date cell as ISO-8601 text, from a string or an epoch-second number.

    The two stores spell the same fact differently: the depth store writes
    ``"1980-12-12"`` and the parity scan writes TradingView's epoch seconds
    (``1793655000.0`` for a 2026-11-02 earnings date). Accepting only the string
    dropped the scan's own observation and let the overlay see a non-null cell,
    so the endpoint printed an em dash for a date it had. A number outside the
    epoch range is not a date — returning ``None`` is honest where guessing from
    a bare ``19801212`` would not be.
    """

    if isinstance(value, str):
        return _text(value)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    # 1973-03-03 .. 5138-11-16: wide enough for every listed security and narrow
    # enough that a YYYYMMDD integer (19801212) cannot be mistaken for seconds.
    if not math.isfinite(number) or not 1e8 <= number <= 1e11:
        return None
    return datetime.fromtimestamp(number, UTC).date().isoformat()


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _number(value: Any) -> float | None:
    """A finite float, or ``None``; a bool is not a number here."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None
