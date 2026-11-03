"""The one mapping from a client's column name to the row field that holds it.

Why this file exists
--------------------
Three tables used to answer "which row column does ``pe`` mean?" — ``SCREEN_ALIASES``
(18 entries, owned by the depth overlay), ``VIEW_FIELDS`` (57 entries, owned by the
selection renderer) and ``LEGACY_COLUMN_ALIASES`` (23 entries, owned by the
compatibility views). Only 11 keys were shared, and they had already drifted: a
filter accepted ``perf_1w`` and rejected ``perf_4w``; a view filled ``pe`` and left
``net_income`` null; ``columns`` filled neither. The nine canonical views name 100
distinct catalogue keys and 62 of them were in no table at all, so they rendered
``null`` forever while the number sat on the row.

There is exactly one table now: :data:`CATALOGUE_COLUMNS`, name → row field. It is
the only literal in the codebase of its kind; ``SCREEN_ALIASES``,
``VIEW_FIELDS`` and ``LEGACY_COLUMN_ALIASES`` are *projections* of it (see the end
of this docstring) and a boot check fails the process if a projection is edited
into a second source of truth.

Three readers, one answer
-------------------------
* a **view** renders a column with :func:`value_for`;
* ``columns`` projects a column with :func:`value_for`;
* a **filter** compiles its key with :func:`field_for` (the key must resolve to a
  column the filter DSL can compare — :data:`app.screen.filters.FIELDS`).

:func:`harmonise` runs once per row inside
:func:`app.screen.selection.select_rows`, before any filter reads a cell, so the
row's canonical spelling and the catalogue's spelling of one metric cannot hold
two different numbers when the filter runs.

The direction of the table: the canonical screen column wins
------------------------------------------------------------
Where a metric has two spellings on a row — the screen column
(``pe_ttm``, ``market_cap_usd``, ``price_to_cash``, ``ret_1w``) and the catalogue
key the depth store copies verbatim (``pe``, ``market_cap``, ``pc``,
``perf_1w``) — the table names the **screen column**, because that is the name
the filter DSL, the ranker and the parity scan already use, and the catalogue key
becomes a synonym that :func:`value_for` and :func:`harmonise` fall back to. A
non-null canonical cell always wins; a synonym only fills a *null*, and only with
a non-null of its own. Nothing here invents a value: an absent metric is
``None``, never ``0``.

Where the screen has no column for a catalogue key (``company``, ``dividend``,
``eps``, ``security_type`` …) the key resolves to itself and the depth overlay is
the only writer — which is the whole point of that overlay. ``company`` is the one
identity column the row spells ``name``; both spellings name one column and the
table says so.

The four names that are genuinely absent
----------------------------------------
A name can exist in the catalogue and have **no row field anywhere**, and a table
that guessed one would be inventing data. Those are listed in
:data:`ABSENT_COLUMNS` with the reason, and they render ``null``:

* ``open``, ``high``, ``low`` — the bulk scan does not request TradingView's
  session open/high/low and neither the depth store nor the fundamentals snapshot
  carries them. They are the only three canonical view columns that cannot be
  answered today. **The fix is outside this module**: add ``open``/``high``/
  ``low`` to ``app.screen.universe.PARITY_FIELDS`` and ``SCAN_COLUMNS``. Until
  then the table says "absent" instead of pointing at a cell that never holds a
  value;
* ``sort_order``, ``row_type`` — transport plumbing the screener uses to order and
  type rows, never published on a screen row;
* ``option_short`` — the Finviz-era *composite* of ``optionable`` and
  ``shortable``. One name cannot address two cells, so a single-cell client
  renders nothing and the two catalogue keys are addressable by their own names.

``average_volume`` is not in that list, and the decision is worth stating because
it was the audit's F6: the catalogue sources it from the provider's
``averageVolume`` (a trailing multi-week average), while the row's
``avg_volume_10d`` is a *ten-day* window. Reading the ten-day cell for a
three-month question answered a different question. The table maps
``average_volume`` to ``avg_volume_30d`` — the only window the row carries that
can honestly answer it — and the ten-day cell stays addressable under its own
name. Finviz's own filter key for the same metric (``sh_avgvol``) reads the same
column, so a filter on ``average_volume`` and a filter on ``sh_avgvol`` select
the same rows by construction.

Compatibility with the shipped client
-------------------------------------
The 23 Finviz-era names (``perf_week``, ``week52_high``, ``eps_ttm``, …) are
entries in the same table, resolved to the same row fields the frozen
:data:`app.screen.views.LEGACY_VIEWS` tabs render. :func:`resolve_column` answers
"which catalogue key(s) does this old name mean?" by inverting the table, so no
second list of successors exists.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from app.market.depth_fields import field_names

__all__ = [
    "ABSENT_COLUMNS",
    "CATALOGUE_COLUMNS",
    "CATALOGUE_KEYS",
    "CATALOGUE_KEY_SET",
    "FIELD_SYNONYMS",
    "ROW_COLUMNS",
    "Absent",
    "absent_reason",
    "catalogue_keys_for",
    "field_for",
    "harmonise",
    "harmonise_rows",
    "view_fields",
    "value_for",
]

#: The 116 catalogue keys, in catalogue order, read from the one registry that
#: parses ``finviz_columns_116.json`` (via :mod:`app.market.depth_fields`) rather
#: than copied here: a hand-kept list is how the old tables drifted.
CATALOGUE_KEYS: tuple[str, ...] = field_names()
CATALOGUE_KEY_SET: frozenset[str] = frozenset(CATALOGUE_KEYS)


#: Column names that are a row column but not a catalogue key: the light row's
#: identity/display cells and the two cells the API adds to a projection. They
#: resolve to themselves, so ``field_for`` is total for anything a row carries.
_EXTRA_ROW_COLUMNS: frozenset[str] = frozenset(
    {
        "symbol",
        "name",
        "exchange",
        "country",
        "sector",
        "price",
        "change_pct",
        "volume",
        "turnover",
        "turnover_usd",
        "market_cap_usd",
        "is_primary",
        "rank",
        "as_of",
        "logo",
    }
)


@dataclass(frozen=True, slots=True)
class Absent:
    """A name with no row field: why, and the catalogue keys it stands for.

    ``parts`` is non-empty only for a name that is a *composite* of real keys
    (``option_short``). It lets :func:`catalogue_keys_for` answer "which keys does
    this old name mean?" without the name pretending to be one cell.
    """

    reason: str
    parts: tuple[str, ...] = ()


#: Name → the row field that holds its value. The one table.
#:
#: Every one of the 116 catalogue keys appears exactly once (an entry here or a
#: documented absence in :data:`ABSENT_COLUMNS`), plus the shipped client's 23
#: Finviz-era names. Read the comments in groups: identity means "the row already
#: spells this metric the way the catalogue does"; a rename means the screen has
#: its own column for the same measurement, and the catalogue spelling is a
#: synonym of it (see :data:`FIELD_SYNONYMS`).
CATALOGUE_COLUMNS: Mapping[str, str] = MappingProxyType(
    {
        # -- identity lane ---------------------------------------------------
        # The row names its instrument ``symbol``; the catalogue calls it
        # ``ticker``. One column, two spellings.
        "ticker": "symbol",
        # The row's company name is ``name`` (the provider description the browse
        # row, the search hits and the exports all use). The depth store's
        # ``company`` is the same instrument in legal-name form, not a second
        # column: one metric, one number.
        "company": "name",
        "sector": "sector",
        "industry": "industry",
        # The scan classifies rows by type/subtype and drops both columns; the
        # depth store carries the human label.
        "security_type": "security_type",
        "country": "country",
        # A MIC on the row (``XNAS``): that is the row's exchange column, and a
        # second venue-name column would be a second answer to one question.
        "exchange": "exchange",
        "index_membership": "index_membership",
        "optionable": "optionable",
        "shortable": "shortable",
        "employees": "employees",
        "website": "website",
        "earnings_date": "earnings_date",
        # The parity scan copies the provider's ``ipo_date`` into ``listing_date``
        # and answers null on every live row; the depth store measures the real
        # IPO date under the catalogue's own spelling. One metric, one row
        # column: harmonise() moves the store's cell into ``listing_date`` so the
        # view, ``columns`` and the filter read the same value. The store spells
        # it as an ISO date and the parity column is documented as epoch seconds —
        # a unit mismatch inherited with the column, stated here rather than
        # papered over by a second column that would answer "when did it list?"
        # twice.
        "ipo_date": "listing_date",
        # -- valuation -------------------------------------------------------
        "market_cap": "market_cap_usd",
        "enterprise_value": "enterprise_value",
        "pe": "pe_ttm",
        "forward_pe": "pe_forward",
        "peg": "peg",
        "ps": "ps",
        "pb": "pb",
        # The depth store measures P/C under ``pc``; the fundamentals join
        # computes ``price_to_cash`` from the snapshot and answers ``None``
        # whenever an input is missing. The column the filters and the views read
        # is ``price_to_cash``; harmonise() fills it from ``pc`` when the join
        # could not, so one metric keeps one number.
        "pc": "price_to_cash",
        "pfcf": "pfcf",
        "ev_ebitda": "ev_ebitda",
        "ev_sales": "ev_to_sales",
        "fcf_margin": "fcf_margin",
        "target_price": "target_price",
        "recommendation": "recommendation",
        # -- quality ---------------------------------------------------------
        "roa": "roa",
        "roe": "roe",
        "roic": "roic",
        "current_ratio": "current_ratio",
        "quick_ratio": "quick_ratio",
        "lt_debt_to_equity": "lt_debt_to_equity",
        "debt_to_equity": "debt_to_equity",
        "book_per_share": "book_per_share",
        "cash_per_share": "cash_per_share",
        "gross_margin": "gross_margin",
        "operating_margin": "oper_margin",
        "net_margin": "profit_margin",
        # -- growth ----------------------------------------------------------
        # ``eps``/``net_income``/``sales`` are the depth store's own cells; the
        # snapshot's ``eps_ttm``/``net_income_ttm``/``revenue_ttm`` are synonyms
        # that fill them when the store has nothing.
        "eps": "eps",
        "net_income": "net_income",
        "sales": "sales",
        # A *level* (next quarter's EPS estimate), not a growth rate.
        "eps_next_q": "eps_next_q",
        # These four are growth rates in percent, and so are the depth store's
        # cells. The old table mapped ``eps_next_y`` to the provider's forward
        # EPS *level* — a unit error this table does not repeat.
        "eps_this_y": "eps_this_y",
        "eps_next_y": "eps_next_y",
        "eps_past_3y": "eps_past_3y",
        "eps_past_5y": "eps_past_5y",
        "eps_next_5y": "eps_next_5y",
        "sales_past_3y": "sales_past_3y",
        "sales_past_5y": "sales_past_5y",
        "eps_qoq": "eps_qoq",
        "sales_qoq": "sales_qoq",
        "eps_yoy_ttm": "eps_yoy_ttm",
        "sales_yoy_ttm": "sales_yoy_ttm",
        "eps_surprise": "eps_surprise",
        "revenue_surprise": "revenue_surprise",
        # -- momentum (the four windows the old table was missing) -----------
        "perf_1w": "ret_1w",
        "perf_4w": "ret_1m",
        "perf_13w": "ret_3m",
        "perf_26w": "ret_6m",
        "perf_52w": "ret_1y",
        "perf_ytd": "ret_ytd",
        "perf_3y": "ret_3y",
        "perf_5y": "ret_5y",
        "perf_10y": "ret_10y",
        "rsi": "rsi_14",
        # -- trend -----------------------------------------------------------
        "sma20": "sma_20",
        "sma50": "sma_50",
        "sma200": "sma_200",
        "high_50d": "high_50d",
        "low_50d": "low_50d",
        "high_52w": "high_52w",
        "low_52w": "low_52w",
        "atr": "atr_14",
        "beta": "beta_1y",
        "volatility_1w": "volatility_w",
        "volatility_4w": "volatility_m",
        "all_time_high": "all_time_high",
        "all_time_low": "all_time_low",
        "week_52_range": "week_52_range",
        "candlestick_pattern": "candlestick_pattern",
        # -- ownership -------------------------------------------------------
        "shares_outstanding": "shares_outstanding",
        "shares_float": "float_shares",
        "float_pct": "float_pct",
        "insider_own": "insider_own",
        "insider_trans": "insider_trans",
        "inst_own": "inst_own",
        "inst_trans": "inst_trans",
        "short_float": "short_float",
        "short_ratio": "short_ratio",
        "short_interest": "short_interest",
        # -- income ----------------------------------------------------------
        "dividend": "dividend",
        "dividend_yield": "dividend_yield",
        "dividend_ttm": "dividend_ttm",
        "payout_ratio": "payout_ratio",
        "dividend_ex_date": "dividend_ex_date",
        "dividend_growth_1y": "dividend_growth_1y",
        "dividend_growth_3y": "dividend_growth_3y",
        "dividend_growth_5y": "dividend_growth_5y",
        # -- today -----------------------------------------------------------
        "price": "price",
        "change": "change_pct",
        "change_from_open": "change_from_open",
        "gap": "gap_pct",
        "prev_close": "prev_close",
        "volume": "volume",
        # See the module docstring: the 30-day window, never the ten-day one.
        "average_volume": "avg_volume_30d",
        "relative_volume": "rel_volume_10d",
        # -- news / plumbing that only the detail panel asks for --------------
        "news_url": "news_url",
        "news_title": "news_title",
        "news_time": "news_time",
        "daily_digest": "daily_digest",
        # -- the shipped client's 23 Finviz-era names -------------------------
        # A row field that is also a legacy name resolves to itself; the rest
        # are renames of catalogue keys above, so both ends stay in one table.
        "atr_14": "atr_14",
        "avg_volume": "avg_volume_30d",
        "change_pct": "change_pct",
        "eps_ttm": "eps",
        "eps_next_y_growth": "eps_next_y",
        "eps_past_3_5y": "eps_past_5y",
        "income": "net_income",
        "index": "index_membership",
        "ipo": "listing_date",
        "oper_margin": "oper_margin",
        "perf_week": "ret_1w",
        "perf_month": "ret_1m",
        "perf_quarter": "ret_3m",
        "perf_half_y": "ret_6m",
        "perf_year": "ret_1y",
        "profit_margin": "profit_margin",
        "rel_volume": "rel_volume_10d",
        "rsi_14": "rsi_14",
        "sales_past_3_5y": "sales_past_5y",
        # Finviz prints volatility as one cell holding two windows. A single-cell
        # view can only show one; the week window is what the frozen tab has
        # always rendered, and ``volatility_4w`` is addressable by its own name.
        "volatility": "volatility_w",
        "week52_high": "high_52w",
        "week52_low": "low_52w",
    }
)

#: Names with **no** row field, and the reason. A name here renders ``None`` and
#: a filter that names it is refused by :mod:`app.screen.filter_fields` — never
#: silently applied to a column that never holds a value.
ABSENT_COLUMNS: Mapping[str, Absent] = MappingProxyType(
    {
        "open": Absent(
            "the session open: the bulk scan does not request the provider's "
            "open column and neither the depth store nor the fundamentals "
            "snapshot carries one (add 'open' to PARITY_FIELDS to fill it)"
        ),
        "high": Absent(
            "the session high: the bulk scan does not request the provider's "
            "high column and neither store carries one (add 'high' to "
            "PARITY_FIELDS to fill it)"
        ),
        "low": Absent(
            "the session low: the bulk scan does not request the provider's "
            "low column and neither store carries one (add 'low' to "
            "PARITY_FIELDS to fill it)"
        ),
        "sort_order": Absent(
            "transport plumbing: the screener orders rows by it, a screen row "
            "does not publish it"
        ),
        "row_type": Absent(
            "transport plumbing: the screener types rows by it, a screen row "
            "does not publish it"
        ),
        "option_short": Absent(
            "a Finviz composite of two catalogue keys with two sources "
            "(optionable, shortable); one name cannot address two cells, so the "
            "frozen tab renders it blank and the two keys are addressable by "
            "name",
            parts=("optionable", "shortable"),
        ),
    }
)

#: Row column → the other names in :data:`CATALOGUE_COLUMNS` that mean it.
#: Derived, never written by hand: ``pe_ttm`` collects ``pe``, and
#: :func:`value_for` reads the canonical cell first and a synonym only when the
#: canonical cell is ``None``. This is what makes ``pe`` and ``pe_ttm`` one
#: number per row instead of two numbers 0.8 % apart.
FIELD_SYNONYMS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        field: tuple(
            name
            for name, target in CATALOGUE_COLUMNS.items()
            if target == field and name != field
        )
        for field in dict.fromkeys(CATALOGUE_COLUMNS.values())
    }
)

#: Row field → the catalogue keys that name it, in catalogue order. Derived by
#: inverting the table, so :func:`catalogue_keys_for` (the shipped client's old
#: name → catalogue key) needs no list of its own.
_KEYS_BY_FIELD: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        field: tuple(key for key in CATALOGUE_KEYS if CATALOGUE_COLUMNS.get(key) == field)
        for field in dict.fromkeys(CATALOGUE_COLUMNS.values())
    }
)


def _row_column_universe() -> frozenset[str]:
    """Every column a row can carry, for the boot check (imported lazily).

    The row layer is four tables deep — the light row, the parity copies, the two
    joins and the display-only fill — and :mod:`app.screen.columns` is imported by
    the depth overlay, which those modules in turn import. Resolving the union
    inside the check keeps the import graph acyclic.
    """

    from app.screen import facts as facts_store
    from app.screen import fundamentals as joined
    from app.screen import universe as parity
    from app.screen.filters import FIELDS

    return (
        frozenset(FIELDS)
        | frozenset(parity.CARRIED_FIELDS)
        | frozenset(joined.FUNDAMENTALS_ROW_FIELDS)
        | frozenset(joined.VIEW_FILL_FIELDS)
        | frozenset(facts_store.FACTS_ROW_FIELDS)
        | CATALOGUE_KEY_SET
        | _EXTRA_ROW_COLUMNS
    )


#: Every name a caller may spell, in table order: catalogue keys first, then the
#: shipped client's names. ``columns`` validates against this plus the documented
#: absences (a caller may ask for ``open`` and get an honest ``null``).
ROW_COLUMNS: frozenset[str] = frozenset(CATALOGUE_COLUMNS) | frozenset(ABSENT_COLUMNS)


def _validate() -> None:
    """Fail at import: one disposition per catalogue key, one real field each.

    The view-side half of the audit's finding (a declared view column that
    resolves to nothing) is checked in :func:`app.screen.views._validate`, which
    is the module that declares the views.
    """

    missing = sorted(CATALOGUE_KEY_SET - set(CATALOGUE_COLUMNS) - set(ABSENT_COLUMNS))
    if missing:
        raise RuntimeError(
            f"catalogue key(s) with no column and no documented absence: {missing}"
        )
    both = sorted(set(CATALOGUE_COLUMNS) & set(ABSENT_COLUMNS))
    if both:
        raise RuntimeError(f"name(s) both mapped and declared absent: {both}")
    stray = sorted(
        name
        for name in ABSENT_COLUMNS
        if name not in CATALOGUE_KEY_SET and name in _row_column_universe()
    )
    if stray:
        raise RuntimeError(
            f"name(s) declared absent but a row really carries them: {stray}"
        )

    universe = _row_column_universe()
    unknown = sorted(
        f"{name} -> {field}"
        for name, field in CATALOGUE_COLUMNS.items()
        if field not in universe
    )
    if unknown:
        raise RuntimeError(
            f"CATALOGUE_COLUMNS names row field(s) no row carries: {unknown}"
        )
    for name, absent in ABSENT_COLUMNS.items():
        if not absent.reason.strip():
            raise RuntimeError(f"{name!r} has an empty absence reason")
        bad = sorted(set(absent.parts) - CATALOGUE_KEY_SET)
        if bad:
            raise RuntimeError(f"{name!r} names part(s) that are not catalogue keys: {bad}")

    # The view-side half of the same check lives in :func:`app.screen.views._validate`
    # (a declared view column that resolves to nothing is the 62-null-columns bug
    # growing back). It cannot be stated here: ``views`` imports this module, so
    # importing ``views`` from here would close a cycle.


_validate()


#: Every column a row can carry: the light row, the parity copies, the three
#: derived columns, the two joins, the display fill and the catalogue keys the
#: depth overlay copies. The resolver validates its own table against it, and
#: this module uses it to reject a view target that no row could ever hold.
ROW_FIELD_COLUMNS: frozenset[str] = _row_column_universe()


def field_for(name: object) -> str | None:
    """The row field ``name`` reads, or ``None`` when no row carries it.

    Total for every name the product accepts: a catalogue key, a shipped-client
    name, a row column. ``None`` means "documented absent" (see
    :data:`ABSENT_COLUMNS`) or "not a name at all" — callers that must tell the
    two apart use :func:`absent_reason`.
    """

    if not isinstance(name, str):
        return None
    if name in ABSENT_COLUMNS:
        return None
    found = CATALOGUE_COLUMNS.get(name)
    if found is not None:
        return found
    if name in _EXTRA_ROW_COLUMNS or name in ROW_FIELD_COLUMNS:
        return name
    return None


def absent_reason(name: object) -> str | None:
    """Why ``name`` has no row field, or ``None`` when it has one."""

    if not isinstance(name, str):
        return None
    recorded = ABSENT_COLUMNS.get(name)
    return recorded.reason if recorded is not None else None


def catalogue_keys_for(name: str) -> tuple[str, ...]:
    """The catalogue key(s) ``name`` means — the inverse of the table.

    A catalogue key answers itself. A shipped-client name answers the key whose
    row field it shares (``perf_week`` → ``perf_1w``), and a documented composite
    answers its parts (``option_short`` → ``optionable``, ``shortable``). A name
    that means nothing (a bare row column, a typo) answers ``()``.
    """

    if name in CATALOGUE_KEY_SET:
        return (name,)
    absent = ABSENT_COLUMNS.get(name)
    if absent is not None:
        return absent.parts
    field = field_for(name)
    if field is None:
        return ()
    return _KEYS_BY_FIELD.get(field, ())


def value_for(row: Mapping[str, Any], name: str) -> Any:
    """The value of ``name`` on ``row``: canonical cell first, synonym second.

    One metric, one number. The table's field is read first; only when that cell
    is absent or ``None`` is a synonym consulted, and only a non-``None`` synonym
    is used. Nothing is coerced: a missing metric stays ``None``, never ``0``.
    """

    field = field_for(name)
    if field is None:
        return None
    value = row.get(field)
    if value is not None:
        return value
    for synonym in FIELD_SYNONYMS.get(field, ()):
        alternative = row.get(synonym)
        if alternative is not None:
            return alternative
    return None


def harmonise(row: Mapping[str, Any]) -> dict[str, Any]:
    """Return a copy of ``row`` with every canonical cell its synonyms can fill.

    This is the row-level half of the resolver and the reason a filter, a view and
    a ``columns`` projection cannot disagree: after it, ``pe_ttm`` holds the value
    ``pe`` carried, ``price_to_cash`` holds ``pc``, ``listing_date`` holds
    ``ipo_date`` — and a non-null canonical cell is never overwritten.

    The input is never mutated (the row layer hands out cached scan pages), and
    the copy keeps every original key.
    """

    merged = dict(row)
    for field, synonyms in FIELD_SYNONYMS.items():
        if merged.get(field) is not None:
            continue
        for synonym in synonyms:
            alternative = merged.get(synonym)
            if alternative is not None:
                merged[field] = alternative
                break
    return merged


def harmonise_rows(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """:func:`harmonise` over a page; a list so the caller can count it twice."""

    return [harmonise(row) for row in rows]


def view_fields(columns: Iterable[str]) -> Mapping[str, str]:
    """The resolvable subset of ``columns`` — the old ``VIEW_FIELDS`` shape.

    Published for the renderer and for the tests that read the table as
    ``{column: field}``; it is computed from :data:`CATALOGUE_COLUMNS` on every
    call and has no entries of its own.
    """

    return MappingProxyType(
        {
            column: field
            for column in columns
            if (field := field_for(column)) is not None
        }
    )
