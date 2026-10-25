"""The stock-selection contract: two modes, one executor over the filter DSL.

``POST /screens/select`` answers one question: *which rows of a universe match
this selection?* The selection is a small JSON document with a ``mode``, and the
two modes are different questions, not two spellings of one:

* **``criteria``** is a query: ``region`` + ``criteria.filters`` + ``sort`` +
  ``limit``. It runs over the whole post-dedupe universe and answers the top
  ``limit`` rows plus the **post-filter total** — the count of every row that
  matched, not the size of the page. A screen that reports ``total=5`` because
  it counted the rows it happened to show is how a 5-row page gets read as a
  5-stock market.
* **``symbols``** is a lookup: an explicit, ordered list of tickers resolved
  inside the universe addressed by ``snapshot_id`` (which carries the region and
  the rule version) and declared by ``source``. Unknown tickers are listed in
  ``missing`` instead of being silently dropped, and the order of ``rows`` is the
  order of ``symbols``, not the universe's order. The snapshot fixes *which
  universe* the tickers are looked up in, not the prices: the rows are read now
  and dated by the response's own ``as_of``, so a client can never mistake a
  fresh quote for the one the snapshot stored.

Three rules the code has to earn:

1. **No filter is ignored.** A criterion names either a canonical row column
   (``field``) or a Finviz filter key (``key``), and both paths end in
   :func:`app.screen.filters.compile_filter`, so an unknown field, an unknown
   operator or a value of the wrong type is a 422 before a row is read. A key
   without a light column is refused by name through
   :mod:`app.screen.filter_fields` — never applied as a no-op. The three keys
   that span several columns (``ta_perf``, ``ta_perf2``, ``ta_volatility``) take
   their option token as the value — ``{"key": "ta_perf", "op": "eq",
   "value": "1w10u"}`` — and :mod:`app.screen.choice_filters` compiles it into
   the same ``field op number`` predicate a ``field`` criterion would have
   produced.
2. **The display is capped at 20 rows.** ``limit`` cannot widen the page: a
   larger value is clamped (not an error, because the caller asked for a page
   that does not exist) and the envelope reports the applied ``limit``, so a
   client can see what it got. ``symbols`` is capped the same way.
3. **Same input, same output.** The executor is a pure function of
   ``(rows, request)``: filters AND in the order given, ``rank_rows`` is a stable
   sort with missing values last, and ``symbols`` keeps request order. Two calls
   with the same arguments produce identical documents, which is what makes a
   selection reproducible in a test, a bug report or an export.

``parse_selection`` is the validator and knows nothing about HTTP; the API route
maps :class:`SelectionError` to the ``{error, errorMessage}`` envelope. The
published request schema is :data:`SELECTION_REQUEST_SCHEMA`, served at
``GET /screens/select/schema`` and mirrored byte-for-byte at
``schemas/stock_selection_request_v1.json``.

Two optional knobs shape the answer
-----------------------------------

``asset_class`` (``stock`` default, or ``etf``) picks the universe. It is
resolved per mode: a ``criteria`` query knows its region, so ``us_etf`` implies
``etf`` and ``global`` + ``etf`` is refused; a ``symbols`` lookup takes the
region from its snapshot and ``None`` means "the snapshot's own family". The
ETF universe is ``type=fund``/``subtype=etf`` with no cap floor — the same
criteria language, a different market.

``view`` (one of :data:`app.screen.views.VIEW_NAMES`) shapes every response row
as that Finviz tab: ``rank``/``symbol``/``as_of`` plus one cell per declared view
column, filled from the parity metrics the bulk scan carries
(:data:`VIEW_FIELDS`). Without ``view`` the row is the light browse row, exactly
as before; an unknown view is a 422 (:class:`SelectionError` code
``unknown_view``) before a row is read.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from app.screen import choice_filters
from app.screen import rank as ranking
from app.screen import views as screen_views
from app.screen.filter_fields import (
    CATALOGUE,
    CHOICE_FIELDS,
    ROW_FIELDS,
    FilterKeyError,
    binding,
    unavailable_keys,
)
from app.screen.filters import FIELDS, OPERATORS, apply_filters, compile_filter
from app.screen.snapshot import DISPLAY_ROWS, SOURCE, display_row
from app.screen.universe import ASSET_CLASSES, REGIONS, asset_class_for

#: The two modes. A selection is exactly one of them; the payload may not mix.
MODES: tuple[str, ...] = ("criteria", "symbols")

#: Sources a ``symbols`` selection may name. Closed on purpose: a typo in a
#: provenance field must not be accepted as "some other source".
SOURCES: tuple[str, ...] = (SOURCE,)

#: Rows a selection may display. Imported, not re-declared: the browse display
#: and the selection page are the same window, and two constants would drift.
DISPLAY_LIMIT = DISPLAY_ROWS

#: The sort a criteria selection uses when it does not name one.
DEFAULT_SORT = "market_cap"

#: Top-level keys a selection may carry (the strict contract).
SELECTION_KEYS: frozenset[str] = frozenset(
    {
        "mode",
        "region",
        "asset_class",
        "view",
        "criteria",
        "symbols",
        "source",
        "snapshot_id",
    }
)
CRITERIA_KEYS: frozenset[str] = frozenset({"filters", "sort", "limit"})
FILTER_KEYS: frozenset[str] = frozenset({"key", "field", "op", "value"})

#: View column (a Finviz quote metric key) → the canonical parity row field that
#: can answer it. One table, for the same reason :data:`app.screen.universe.
#: PARITY_FIELDS` is one table: a view is a layout decision, and the *source* of
#: every cell has to be stated once instead of being guessed per call site.
#:
#: A view column with no entry here is still emitted — with ``None`` — because a
#: table whose shape depends on the payload cannot be compared between two runs.
#: The bulk scan simply does not carry those metrics yet: enterprise value,
#: income/sales, the per-share book and cash, insider/institutional ownership,
#: employees, index membership, ATR and the 3-year window. ``volatility`` is two
#: cells (week and month) in Finviz, so one key cannot name one column.
VIEW_FIELDS: Mapping[str, str] = {
    # -- identity / light cells that are also view columns
    "price": "price",
    "change_pct": "change_pct",
    "volume": "volume",
    "market_cap": "market_cap_usd",
    # -- valuation
    "pe": "pe_ttm",
    "forward_pe": "pe_forward",
    "peg": "peg",
    "ps": "ps",
    "pb": "pb",
    "pfcf": "pfcf",
    "ev_ebitda": "ev_ebitda",
    # -- financial (levels the bulk scan really carries)
    "eps_next_y": "eps_forecast_next_fy",
    "eps_next_q": "eps_forecast_next_fq",
    "roa": "roa",
    "roe": "roe",
    "roic": "roic",
    "gross_margin": "gross_margin",
    "oper_margin": "oper_margin",
    "profit_margin": "profit_margin",
    "quick_ratio": "quick_ratio",
    "current_ratio": "current_ratio",
    "debt_to_equity": "debt_to_equity",
    # -- ownership (share counts; holdings come from the facts store)
    "shares_outstanding": "shares_outstanding",
    "shares_float": "float_shares",
    "insider_own": "insider_own",
    "inst_own": "inst_own",
    # -- technicals
    "sma20": "sma_20",
    "sma50": "sma_50",
    "sma200": "sma_200",
    "rsi_14": "rsi_14",
    "beta": "beta_1y",
    "week52_high": "high_52w",
    "week52_low": "low_52w",
    # -- performance
    "perf_week": "ret_1w",
    "perf_month": "ret_1m",
    "perf_quarter": "ret_3m",
    "perf_half_y": "ret_6m",
    "perf_ytd": "ret_ytd",
    "perf_year": "ret_1y",
    "perf_5y": "ret_5y",
    "perf_10y": "ret_10y",
    # -- profile / events
    "ipo": "listing_date",
}

#: Where a misplaced knob belongs — used to turn "unexpected key" into a fix.
_HINTS: Mapping[str, str] = {
    "limit": "'limit' belongs inside 'criteria'",
    "sort": "'sort' belongs inside 'criteria'",
    "filters": "'filters' belongs inside 'criteria'",
}

#: View columns that read a cell every universe row already carries (the light
#: row), not a parity metric. Kept explicit so the boot check below can tell
#: "light by design" from "a parity field that does not exist".
_VIEW_LIGHT_FIELDS: frozenset[str] = frozenset(
    {"price", "change_pct", "volume", "market_cap_usd"}
)
VIEW_FACT_FIELDS: frozenset[str] = frozenset({"insider_own", "inst_own"})


def _validate_view_fields() -> None:
    """Fail at import: a view cell must point at a column a row really carries."""

    from app.screen.universe import PARITY_FIELDS

    known = set(PARITY_FIELDS.values()) | set(_VIEW_LIGHT_FIELDS) | set(VIEW_FACT_FIELDS)
    unknown = sorted(set(VIEW_FIELDS.values()) - known)
    if unknown:
        raise RuntimeError(
            f"VIEW_FIELDS names field(s) no row carries: {unknown}; "
            "they are neither a light column nor a PARITY_FIELDS target"
        )
    declared = set().union(*(set(columns) for columns in screen_views.VIEW_COLUMNS.values()))
    stray = sorted(set(VIEW_FIELDS) - declared)
    if stray:
        raise RuntimeError(f"VIEW_FIELDS maps column(s) no view declares: {stray}")


_validate_view_fields()


class SelectionError(ValueError):
    """A selection payload that cannot be executed, with a stable code (422).

    ``status_code`` lives here so the API layer never has to guess: every
    validation failure is the caller's fault and none of them is a 500.
    """

    status_code = 422
    code = "invalid_selection"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


@dataclass(frozen=True, slots=True)
class Criterion:
    """One compiled predicate plus the Finviz key it came from, if any.

    ``key`` is ``None`` when the caller used the canonical ``field`` form, so the
    envelope can echo *how* the criterion was written and a client can round-trip
    a request from a response. For an option-driven key (``ta_perf``,
    ``ta_perf2``, ``ta_volatility``) ``key`` names the Finviz filter while
    ``field``/``op``/``value`` are the predicate its option token compiled to:
    both ends of the translation are visible, so a reader can check that
    ``1w10u`` really meant ``ret_1w <= -10`` without trusting the request.
    """

    field: str
    op: str
    value: Any
    key: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """The wire form: ``in``/``between`` tuples become JSON arrays."""

        value = list(self.value) if isinstance(self.value, tuple) else self.value
        return {"key": self.key, "field": self.field, "op": self.op, "value": value}


@dataclass(frozen=True, slots=True)
class SelectionRequest:
    """A validated selection: the mode's own knobs, never a mixture.

    ``asset_class`` is resolved for ``criteria`` (the region is known there) and
    kept as the caller's explicit choice for ``symbols`` — that mode learns its
    region from the snapshot, so ``None`` means "use the snapshot's tab family".
    ``view`` is the optional Finviz layout the response rows are shaped as.
    """

    mode: str
    region: str | None
    filters: tuple[Criterion, ...]
    sort: str | None
    limit: int
    symbols: tuple[str, ...]
    source: str | None
    snapshot_id: str | None
    asset_class: str | None = None
    view: str | None = None

    @property
    def is_criteria(self) -> bool:
        return self.mode == "criteria"

    @property
    def is_symbols(self) -> bool:
        return self.mode == "symbols"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _reject_extra(mapping: Mapping[str, Any], allowed: frozenset[str], what: str) -> None:
    """Unknown knobs are a 422, never ignored: a typo must not change the answer."""

    extra = sorted(str(name) for name in mapping if name not in allowed)
    if not extra:
        return
    hints = [f"{name!r}: {_HINTS[name]}" for name in extra if name in _HINTS]
    hint = f" ({'; '.join(hints)})" if hints else ""
    raise SelectionError(
        f"{what} does not accept {', '.join(repr(name) for name in extra)}{hint}; "
        f"allowed keys: {', '.join(sorted(allowed))}"
    )


def _limit(raw: Any) -> int:
    """The display cap: a positive integer, clamped to :data:`DISPLAY_LIMIT`."""

    if raw is None:
        return DISPLAY_LIMIT
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise SelectionError(f"'limit' must be an integer, got {raw!r}")
    if raw < 1:
        raise SelectionError(f"'limit' must be at least 1, got {raw}")
    return min(raw, DISPLAY_LIMIT)


def _sort(raw: Any) -> str:
    if raw is None:
        return DEFAULT_SORT
    if raw not in ranking.SORTS:
        raise SelectionError(
            f"unknown sort {raw!r}; expected one of {', '.join(ranking.SORTS)}"
        )
    return str(raw)


def _asset_class(raw: Any) -> str | None:
    """The optional instrument family; ``None`` is "the region's own family"."""

    if raw is None:
        return None
    if raw not in ASSET_CLASSES:
        raise SelectionError(
            f"unknown asset_class {raw!r}; expected one of {', '.join(ASSET_CLASSES)}"
        )
    return str(raw)


def _view_name(raw: Any) -> str | None:
    """The optional Finviz view; an unknown tab is a 422, never a light row."""

    if raw is None:
        return None
    if not isinstance(raw, str) or raw not in screen_views.VIEW_NAMES:
        raise SelectionError(
            f"unknown view {raw!r}; expected one of {', '.join(screen_views.VIEW_NAMES)}",
            code="unknown_view",
        )
    return raw


def _choice_criterion(
    key: str, item: Mapping[str, Any], where: str
) -> Criterion:
    """One option-driven key: ``op eq`` plus a Finviz option token, or a 422."""

    op = item["op"]
    token = op.strip().casefold() if isinstance(op, str) else None
    example = choice_filters.CHOICES[key].tokens[0]
    if token not in {"eq", "==", "="}:
        raise SelectionError(
            f"{where} (key {key!r}) takes op 'eq' and one of its Finviz option "
            f"tokens as the value (e.g. {{'key': {key!r}, 'op': 'eq', 'value': "
            f"{example!r}}}), got op {op!r}: the token already names the window "
            "and the direction, so a second comparison has nothing left to say",
            code="invalid_filter",
        )
    try:
        compiled = choice_filters.compile_option(key, item["value"])
    except choice_filters.ChoiceOptionError as exc:
        raise SelectionError(f"{where} (key {key!r}): {exc}", code=exc.code) from exc
    return Criterion(
        field=compiled.field,
        op=compiled.op,
        value=compiled.value,
        key=key,
    )


def _criterion(item: Any, index: int) -> Criterion:
    """One filter object → one compiled :class:`Criterion`, or a 422."""

    where = f"criteria.filters[{index}]"
    if not isinstance(item, Mapping):
        raise SelectionError(f"{where} must be an object, got {type(item).__name__}")
    _reject_extra(item, FILTER_KEYS, where)

    key = item.get("key")
    field = item.get("field")
    if (key is None) == (field is None):
        raise SelectionError(
            f"{where} must name exactly one of 'key' (a Finviz filter key, e.g. "
            "'cap') or 'field' (a screen row column, e.g. 'market_cap_usd')"
        )
    missing = [name for name in ("op", "value") if name not in item]
    if missing:
        raise SelectionError(f"{where} is missing {', '.join(missing)}")

    if key is not None:
        try:
            resolved = binding(key)
        except FilterKeyError as exc:
            raise SelectionError(str(exc), code=exc.code) from exc
        if resolved.choice:
            return _choice_criterion(str(key), item, where)
        column = resolved.field
        assert column is not None  # binding() refuses the unavailable keys
        prefix = f"{where} (key {key!r})"
    else:
        column = field
        prefix = where

    try:
        compiled = compile_filter(
            {"field": column, "op": item["op"], "value": item["value"]}
        )
    except ValueError as exc:
        raise SelectionError(f"{prefix}: {exc}", code="invalid_filter") from exc
    return Criterion(
        field=compiled.field,
        op=compiled.op,
        value=compiled.value,
        key=str(key) if key is not None else None,
    )


def _filters(raw: Any) -> tuple[Criterion, ...]:
    """The ANDed filter list; absent means the identity, not "filter nothing"."""

    if raw is None:
        return ()
    if isinstance(raw, (str, bytes)) or not isinstance(raw, (list, tuple)):
        raise SelectionError(
            "'criteria.filters' must be an array of {key|field, op, value} objects"
        )
    return tuple(_criterion(item, index) for index, item in enumerate(raw))


def _criteria_block(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    """The ``criteria`` object, or a 422 that says which mode the caller wants."""

    for name in ("symbols", "source", "snapshot_id"):
        if name in payload:
            raise SelectionError(
                f"mode 'criteria' selects by query and does not take {name!r}; "
                "use mode 'symbols' for an explicit ticker list"
            )
    block = payload.get("criteria")
    if not isinstance(block, Mapping):
        raise SelectionError(
            "criteria mode needs a 'criteria' object with filters, sort and limit"
        )
    _reject_extra(block, CRITERIA_KEYS, "'criteria'")
    return block


def _parse_criteria(payload: Mapping[str, Any]) -> SelectionRequest:
    block = _criteria_block(payload)
    region = payload.get("region")
    if region is None:
        raise SelectionError(
            f"criteria mode needs 'region'; expected one of {', '.join(REGIONS)}"
        )
    if region not in REGIONS:
        raise SelectionError(
            f"unknown region {region!r}; expected one of {', '.join(REGIONS)}"
        )
    family_raw = _asset_class(payload.get("asset_class"))
    try:
        family = asset_class_for(str(region), family_raw)
    except ValueError as exc:
        raise SelectionError(str(exc)) from exc
    return SelectionRequest(
        mode="criteria",
        region=str(region),
        filters=_filters(block.get("filters")),
        sort=_sort(block.get("sort")),
        limit=_limit(block.get("limit")),
        symbols=(),
        source=None,
        snapshot_id=None,
        asset_class=family,
        view=_view_name(payload.get("view")),
    )


def _parse_symbols(payload: Mapping[str, Any]) -> SelectionRequest:
    for name in ("region", "criteria"):
        if name in payload:
            raise SelectionError(
                f"mode 'symbols' takes its universe from 'snapshot_id' and does not "
                f"take {name!r}"
            )

    raw = payload.get("symbols")
    if isinstance(raw, (str, bytes)) or not isinstance(raw, (list, tuple)):
        raise SelectionError(
            "'symbols' must be an array of ticker strings (not a single string)"
        )
    if not raw:
        raise SelectionError("'symbols' needs at least one ticker")
    symbols: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, str) or not item:
            raise SelectionError(
                f"'symbols[{index}]' must be a non-empty string, got {item!r}"
            )
        if item != item.strip():
            raise SelectionError(
                f"'symbols[{index}]' has surrounding whitespace: {item!r}"
            )
        if item in seen:
            raise SelectionError(f"'symbols' repeats {item!r}; every ticker once")
        seen.add(item)
        symbols.append(item)

    source = payload.get("source")
    if source is None:
        raise SelectionError(
            f"symbols mode needs 'source'; expected one of {', '.join(SOURCES)}"
        )
    if source not in SOURCES:
        raise SelectionError(
            f"unknown source {source!r}; expected one of {', '.join(SOURCES)}"
        )

    snapshot_id = payload.get("snapshot_id")
    if not isinstance(snapshot_id, str) or not snapshot_id:
        raise SelectionError(
            "symbols mode needs 'snapshot_id': the snapshot whose region and rule "
            "version the tickers are resolved against"
        )

    return SelectionRequest(
        mode="symbols",
        region=None,
        filters=(),
        sort=None,
        limit=DISPLAY_LIMIT,
        symbols=tuple(symbols),
        source=str(source),
        snapshot_id=snapshot_id,
        asset_class=_asset_class(payload.get("asset_class")),
        view=_view_name(payload.get("view")),
    )


def parse_selection(payload: Any) -> SelectionRequest:
    """Validate a selection document and return the request the executor runs.

    Raises :class:`SelectionError` (always a 422) for anything that cannot be
    executed: a non-object body, an unknown or missing mode, a knob that belongs
    to the other mode, an unknown filter field or operator, a Finviz key with no
    light column, an unknown region/source/sort, or a non-positive limit.
    """

    if not isinstance(payload, Mapping):
        raise SelectionError(
            f"a selection is a JSON object, got {type(payload).__name__}"
        )
    mode = payload.get("mode")
    if mode is None:
        raise SelectionError(
            f"selection has no 'mode'; expected one of {', '.join(MODES)}"
        )
    if mode not in MODES:
        raise SelectionError(
            f"unknown selection mode {mode!r}; expected one of {', '.join(MODES)}"
        )
    _reject_extra(payload, SELECTION_KEYS, "selection")
    return _parse_criteria(payload) if mode == "criteria" else _parse_symbols(payload)


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Selection:
    """The executed selection: page rows, the honest totals and what it echoed.

    ``total`` is the number of rows that matched (criteria) or resolved
    (symbols) — the number a client reads as "how many stocks are there" — while
    ``returned`` is the page size and ``universe_total`` is the size of the
    universe the question ran over. All three are needed to tell a filter that
    matched 3 rows from a page that could only show 3.
    """

    mode: str
    region: str
    source: str
    as_of: str
    sort: str | None
    limit: int
    universe_total: int
    total: int
    filters: tuple[Criterion, ...]
    symbols: tuple[str, ...]
    missing: tuple[str, ...]
    rows: tuple[dict[str, Any], ...]
    snapshot_id: str | None = None

    @property
    def returned(self) -> int:
        return len(self.rows)

    def as_document(self) -> dict[str, Any]:
        """The ``POST /screens/select`` body: one envelope, both modes."""

        return {
            "mode": self.mode,
            "region": self.region,
            "source": self.source,
            "as_of": self.as_of,
            "snapshot_id": self.snapshot_id,
            "sort": self.sort,
            "limit": self.limit,
            "universe_total": self.universe_total,
            "total": self.total,
            "returned": self.returned,
            "filters": [criterion.as_dict() for criterion in self.filters],
            "symbols": list(self.symbols),
            "missing": list(self.missing),
            "rows": [dict(row) for row in self.rows],
        }


def _predicates(request: SelectionRequest) -> tuple[dict[str, Any], ...]:
    """The compiled criteria as the filter DSL's object form (one translation)."""

    return tuple(
        {"field": criterion.field, "op": criterion.op, "value": criterion.value}
        for criterion in request.filters
    )


def _selected(
    rows: Sequence[dict[str, Any]], request: SelectionRequest
) -> tuple[list[dict[str, Any]], int]:
    """Criteria: AND the filters over the whole universe, then rank, then cut.

    The order matters. Filtering first means ``rank`` numbers the *matches*
    (1..n of the selection), and cutting last means ``total`` still counts all of
    them — ranking before filtering would number rows the caller never sees and
    cutting before counting would report the page as the market. ``total`` comes
    from the same pass that built the page, so the two can never disagree.
    """

    matched = apply_filters(rows, _predicates(request))
    ranked = ranking.rank_rows(matched, request.sort or DEFAULT_SORT)
    return ranking.top_rows(ranked, request.limit), len(matched)


def _resolved(
    rows: Sequence[dict[str, Any]], request: SelectionRequest
) -> tuple[list[dict[str, Any]], int, tuple[str, ...]]:
    """Symbols: resolve in request order; unknown tickers are named, not dropped.

    ``rank`` is the ticker's position in the request (1..n), because that is the
    order the caller asked for — sorting a watchlist by market cap would answer a
    question nobody asked. A ticker that the universe does not carry is reported
    in ``missing``; an empty row for it would be a fabricated instrument.
    """

    index: dict[str, dict[str, Any]] = {}
    for row in rows:
        symbol = row.get("symbol")
        if isinstance(symbol, str) and symbol not in index:
            index[symbol] = row

    found: list[dict[str, Any]] = []
    missing: list[str] = []
    for symbol in request.symbols:
        row = index.get(symbol)
        if row is None:
            missing.append(symbol)
            continue
        ranked = dict(row)
        ranked["rank"] = len(found) + 1
        found.append(ranked)

    return found[: request.limit], len(found), tuple(missing)


def view_row(row: Mapping[str, Any], name: str, *, as_of: str) -> dict[str, Any]:
    """One selection row shaped as view ``name`` — identity + view columns.

    This is deliberately **not** the light row: a view request asks for the
    columns of that Finviz tab, one cell per declared column in view order.
    ``rank``/``symbol``/``as_of`` stay so the row is still addressable and
    dated; everything else is looked up through :data:`VIEW_FIELDS` and copied
    verbatim (a column the bulk scan does not carry is ``None``, never ``0``).
    """

    definition = screen_views.view(name)
    shaped: dict[str, Any] = {
        "rank": int(row["rank"]),
        "symbol": row.get("symbol"),
        "name": row.get("name"),
        "as_of": as_of,
    }
    for column in definition.columns:
        field = VIEW_FIELDS.get(column)
        shaped[column] = row.get(field) if field is not None else None
    return shaped


def select_rows(
    rows: Sequence[Mapping[str, Any]],
    request: SelectionRequest,
    *,
    region: str,
    as_of: str,
    source: str = SOURCE,
    snapshot_id: str | None = None,
) -> Selection:
    """Run ``request`` over ``rows`` (a post-dedupe universe) and return the page.

    Pure: the input rows are never mutated (``rank_rows`` copies, the symbol path
    copies) and the result is a function of the arguments alone. ``as_of`` is the
    moment the rows were read, supplied by the caller so this layer never has to
    guess a clock; ``region``/``source`` are echoed into the envelope because a
    selection without provenance is not auditable.
    """

    if not isinstance(request, SelectionRequest):
        raise TypeError(f"select_rows needs a SelectionRequest, got {type(request).__name__}")
    if region not in REGIONS:
        raise ValueError(f"unknown region {region!r}; expected one of {', '.join(REGIONS)}")
    if source not in SOURCES:
        raise ValueError(f"unknown source {source!r}; expected one of {', '.join(SOURCES)}")
    if not isinstance(as_of, str) or not as_of:
        raise ValueError("as_of is required: a selection must date its own rows")

    universe = [dict(row) for row in rows]
    if request.is_criteria:
        selected, total = _selected(universe, request)
        missing: tuple[str, ...] = ()
        sort: str | None = request.sort
        echoed_snapshot: str | None = None
    else:
        if not snapshot_id:
            raise ValueError("symbols mode needs the snapshot_id it resolves against")
        selected, total, missing = _resolved(universe, request)
        sort = None
        echoed_snapshot = snapshot_id

    if request.view:
        page = tuple(
            view_row(row, request.view, as_of=as_of) for row in selected
        )
    else:
        page = tuple(display_row(row, as_of=as_of) for row in selected)
    return Selection(
        mode=request.mode,
        region=region,
        source=source,
        as_of=as_of,
        sort=sort,
        limit=request.limit,
        universe_total=len(universe),
        total=total,
        filters=request.filters,
        symbols=request.symbols,
        missing=missing,
        rows=page,
        snapshot_id=echoed_snapshot,
    )


# ---------------------------------------------------------------------------
# The published request schema
# ---------------------------------------------------------------------------


def _filter_key_extension() -> dict[str, dict[str, object]]:
    """All 82 keys, in panel order: the column, or the reason it is refused."""

    return {entry.key: entry.as_dict() for entry in CATALOGUE}


def _filter_value_schema() -> dict[str, object]:
    scalar: dict[str, object] = {"type": ["number", "string", "boolean"]}
    return {
        "description": (
            "The comparison value. `in` and `between` take an array (`between` "
            "exactly two numbers); every other operator takes one scalar. For an "
            f"option-driven key ({', '.join(CHOICE_FIELDS)}) the value is the "
            "Finviz option token itself (`1w10u`, `dup`, `wo2`), and the predicate "
            "each token compiles to is published under "
            "`x-filter-keys.<key>.choice.options`. Everywhere else Finviz's option "
            "tokens (`mega`, `u5`, `ob70`) are display buckets, not values: send "
            "the number the bucket means."
        ),
        "oneOf": [
            {"type": "number"},
            {"type": "string"},
            {"type": "boolean"},
            {"type": "array", "minItems": 1, "items": scalar},
        ],
    }


SELECTION_REQUEST_SCHEMA: dict[str, object] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://ztockz.local/schemas/stock-selection-request-v1.json",
    "title": "StockSelectionRequestV1",
    "description": (
        "A stock selection in one of two modes. `criteria` is a query over a "
        "region's universe: filters are ANDed, `sort` orders the matches and "
        "`limit` caps the page at 20 rows. `symbols` is an explicit ticker list "
        "resolved inside the universe addressed by `snapshot_id`. The response "
        "carries `total` (every row that matched) separately from `returned` "
        "(the page), so a 20-row display never claims to be a 20-stock market."
    ),
    "type": "object",
    "additionalProperties": False,
    "required": ["mode"],
    "properties": {
        "mode": {
            "enum": list(MODES),
            "description": "Which question is being asked; the two modes share no knobs.",
        },
        "region": {
            "enum": list(REGIONS),
            "description": "Criteria only: the universe tab to query.",
        },
        "asset_class": {
            "enum": list(ASSET_CLASSES),
            "default": "stock",
            "description": (
                "Both modes: the instrument family. `stock` (default) and `etf` "
                "are different universes — `us_etf` implies `etf`, and `global` "
                "is stock-only. The ETF universe is `type=fund` + `subtype=etf` "
                "with no market-cap floor and the same OTC exclusion."
            ),
        },
        "view": {
            "enum": list(screen_views.VIEW_NAMES),
            "description": (
                "Both modes: shape every response row as one of the six Finviz "
                "views. A viewed row carries `rank`/`symbol`/`as_of` plus one "
                "cell per declared view column, filled from the parity metrics "
                "the bulk scan carries (missing columns stay null, never 0); "
                "without `view` the row is the light screen row."
            ),
        },
        "criteria": {
            "type": "object",
            "additionalProperties": False,
            "description": "Criteria only: the query itself.",
            "properties": {
                "filters": {
                    "type": "array",
                    "items": {"$ref": "#/$defs/filter"},
                    "default": [],
                    "description": (
                        "ANDed predicates; an empty list is the identity. A key "
                        "with no light row column is refused with a 422 naming the "
                        "missing metric — never ignored, because an ignored filter "
                        "returns the unfiltered screen."
                    ),
                },
                "sort": {
                    "enum": list(ranking.SORTS),
                    "default": DEFAULT_SORT,
                    "description": "The one visible metric the page is ordered by.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "default": DISPLAY_LIMIT,
                    "description": (
                        "Page size. Clamped to 20: a larger value is not an error, "
                        "it simply cannot widen the display, and the response "
                        "reports the limit that was applied."
                    ),
                },
            },
        },
        "symbols": {
            "type": "array",
            "minItems": 1,
            "uniqueItems": True,
            "items": {"type": "string", "minLength": 1},
            "description": (
                "Symbols only: tickers in the order the response should list them. "
                "A ticker the universe does not carry is reported in `missing`."
            ),
        },
        "source": {
            "enum": list(SOURCES),
            "description": (
                "Symbols only: the provenance the snapshot must declare. A source "
                "the snapshot was not built from is refused, never relabelled."
            ),
        },
        "snapshot_id": {
            "type": "string",
            "minLength": 1,
            "description": (
                "Symbols only: the snapshot whose region and rule version the "
                "tickers are resolved against. It fixes *which* universe, not the "
                "prices — the rows are read fresh and dated by the response's "
                "`as_of`, never by the snapshot's."
            ),
        },
    },
    "allOf": [
        {
            "if": {"required": ["mode"], "properties": {"mode": {"const": "criteria"}}},
            "then": {
                "required": ["region", "criteria"],
                "not": {
                    "anyOf": [
                        {"required": ["symbols"]},
                        {"required": ["source"]},
                        {"required": ["snapshot_id"]},
                    ]
                },
            },
        },
        {
            "if": {"required": ["mode"], "properties": {"mode": {"const": "symbols"}}},
            "then": {
                "required": ["symbols", "source", "snapshot_id"],
                "not": {"anyOf": [{"required": ["region"]}, {"required": ["criteria"]}]},
            },
        },
    ],
    "$defs": {
        "filter": {
            "type": "object",
            "additionalProperties": False,
            "required": ["op", "value"],
            "oneOf": [{"required": ["key"]}, {"required": ["field"]}],
            "description": (
                "One predicate: exactly one of `key` (a Finviz filter key, "
                "resolved to its row column) or `field` (the column itself), plus "
                "an operator and a value. A key whose entry in `x-filter-keys` "
                "carries `choice` takes `op: eq` and an option token as `value`."
            ),
            "properties": {
                "key": {
                    "type": "string",
                    "description": (
                        f"One of the 82 Finviz filter keys. The {len(ROW_FIELDS)} "
                        "that resolve to one row column, the "
                        f"{len(CHOICE_FIELDS)} that filter through their option "
                        f"vocabulary, and the {len(unavailable_keys())} that are "
                        "refused with their reason, are listed in x-filter-keys."
                    ),
                },
                "field": {
                    "enum": sorted(FIELDS),
                    "description": "A canonical screen row column.",
                },
                "op": {"enum": sorted(OPERATORS)},
                "value": _filter_value_schema(),
            },
        }
    },
    "x-filter-key-count": len(CATALOGUE),
    "x-filter-keys": _filter_key_extension(),
}

__all__ = [
    "CRITERIA_KEYS",
    "DEFAULT_SORT",
    "DISPLAY_LIMIT",
    "FILTER_KEYS",
    "MODES",
    "SELECTION_KEYS",
    "SELECTION_REQUEST_SCHEMA",
    "SOURCES",
    "VIEW_FIELDS",
    "VIEW_FACT_FIELDS",
    "Criterion",
    "Selection",
    "SelectionError",
    "SelectionRequest",
    "parse_selection",
    "select_rows",
    "view_row",
]
