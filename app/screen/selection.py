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

The rules the code has to earn:

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
2. **A bucket token is a criterion with no field.** Finviz's panels offer names,
   not ranges: ``price:u5``, ``cap:mega``, ``earn:todaybefore``, ``idx:sp500``,
   ``pe:u15``, ``rsi:ob70``, ``divgrowth:3yo10``, ``highlow52w:nh``. Three of those four shapes cannot be written as one
   ``field op value`` triple — ``targetprice:a50`` relates two columns,
   ``earn:today`` needs New York's clock, ``idx:sp500`` is set membership — so
   the token is compiled by :mod:`app.screen.buckets` and travels as
   ``Criterion(field=None, key="sh_price", op="eq", value="price:u5")``. The
   wire shape is unchanged (``{key, op, value}``); ``field=None`` is what splits
   the two compilers, so :func:`_predicates` never hands a token to the DSL. A
   token is canonicalised (case-folded, expanded to its full id) before it is
   stored, which makes ``SP500`` and ``idx:sp500`` one question and one ETag; an
   unknown token is ``422 unknown_bucket``, one whose source is missing is
   ``422 unavailable_bucket``, and both name the ids that would have worked.
3. **One page holds at most :data:`PANEL_LIMIT` rows (100).** ``limit`` cannot
   widen the page past the panel: a larger value is clamped (not an error,
   because the caller asked for a page that does not exist) and the envelope
   reports the applied ``limit``, so a client can see what it got. ``symbols``
   is capped the same way and takes the same knob. The old 20-row window is
   :data:`DISPLAY_LIMIT`, which is now the *browse* display only: a screener
   panel is 100 rows × 9–12 columns, and selling it five 20-row pages is how the
   same symbol appeared twice across a page boundary.
4. **Same input, same output.** The executor is a pure function of
   ``(rows, request)``: filters AND in the order given, ``rank_rows`` orders by
   the metric and breaks ties on ``symbol`` (so page boundaries are stable even
   if the provider reshuffles equal-valued rows), and ``symbols`` keeps request
   order. Two calls with the same arguments produce identical documents, which
   is what makes a selection reproducible in a test, a bug report or an export.

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

One vocabulary, two spellings
-----------------------------

``columns`` speaks the catalogue (``pe_ttm``, ``roic``, ``market_cap``), while a
filter used to speak only Finviz's screener keys (``fa_pe``, ``fa_roi``, ``cap``).
Requiring a caller to know both is how a request ends up filtering on a key the
other half of the document never mentions. So a filter key is resolved in one
order, and the order is the contract:

1. the 82 Finviz keys, unchanged — a legacy name can never be shadowed by a
   catalogue alias;
2. a catalogue key, translated by :func:`app.screen.columns.field_for` into the
   row column it already means (``pe`` → ``pe_ttm``, ``market_cap`` →
   ``market_cap_usd``, ``perf_4w`` → ``ret_1m``), or itself when the catalogue key
   *is* the column (``roic``, ``ps``, ``dividend_yield``);
3. anything else is a 422 that names the key — including a real catalogue column
   no filter can read (``enterprise_value``), because a filter that cannot change
   the answer must never return the unfiltered screen as if it matched.

That resolver is the one table, and it has three readers: the filter compiler
(which needs a column the DSL can compare), :func:`view_row` (which needs the
column a view renders) and the ``columns`` projection. ``SCREEN_ALIASES`` and
``VIEW_FIELDS`` are projections of it, not copies of it, so ``pe`` cannot mean
``pe_ttm`` to a view and something else to a filter. :func:`select_rows` also
harmonises every row through the same table before a filter runs, so the
catalogue's spelling and the screen's spelling of one metric hold one number.

Caching: the tag is a function of the question, not of the clock
----------------------------------------------------------------

:func:`selection_etag` derives a strong ETag from the *canonical* request (every
default and clamp applied, keys sorted) plus a data revision. It deliberately
does not hash the response: hashing a 40 KB document to answer "did anything
change?" costs more than the payload saves, and the response carries ``as_of`` —
the read moment — which changes on every call and would invalidate every tag.
The revision is the newest store stamp the answer is built on (the depth store's
cycle ``as_of``, or the snapshot a ``symbols`` lookup is pinned to), so a repeat
call inside one data cycle revalidates for free while a new cycle, a new filter
or a new region gets a new tag.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from app.engine.catalog import CATALOG_BY_KEY
from app.screen import buckets
from app.screen import choice_filters
from app.screen import columns as column_map
from app.screen import descriptive
from app.screen import entitlements
from app.screen import rank as ranking
from app.screen import views as screen_views
from app.screen.filter_fields import (
    CATALOGUE,
    CHOICE_FIELDS,
    ROW_FIELDS,
    FilterKeyError,
    UnavailableFilterKey,
    UnknownFilterKey,
    binding,
    unavailable_keys,
)
from app.screen.filters import (
    FIELDS,
    OPERATORS,
    US_VALUES,
    VALUE_ALIASES,
    UnknownValue,
    apply_filters,
    compile_filter,
)
from app.screen.snapshot import DISPLAY_ROWS, SOURCE, display_row
from app.screen.universe import ASSET_CLASSES, REGIONS, asset_class_for

#: The two modes. A selection is exactly one of them; the payload may not mix.
MODES: tuple[str, ...] = ("criteria", "symbols")

#: Sources a ``symbols`` selection may name. Closed on purpose: a typo in a
#: provenance field must not be accepted as "some other source".
SOURCES: tuple[str, ...] = (SOURCE,)

#: Rows one selection page may display: the interface's panel (100 rows × 9–12
#: columns). One constant, exported, so the UI and the API cannot disagree about
#: how many rows a view holds.
PANEL_LIMIT = 100

#: The browse display window, imported rather than re-declared: ``GET /screens``
#: is still the top 20 of a snapshot, and a second constant would drift.
DISPLAY_LIMIT = DISPLAY_ROWS

#: The sort a criteria selection uses when it does not name one.
DEFAULT_SORT = "market_cap"

#: Top-level keys a selection may carry (the strict contract). ``limit`` is here
#: for ``symbols`` mode, which has no ``criteria`` block to put it in; a criteria
#: query must still send it inside ``criteria``, where the hint points it.
#: ``signal`` is top-level because it names the **population**, not a predicate
#: inside the query: Finviz's signal set is a different question ("show me the
#: most active names"), not another filter ANDed onto one.
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
        "fill_wide",
        "columns",
        "limit",
        "signal",
    }
)

#: How many cells one selection may ask for. A screener table is a handful of
#: columns, not the whole catalog; the ceiling keeps a request from turning the
#: response into an export.
COLUMN_LIMIT = 40
CRITERIA_KEYS: frozenset[str] = frozenset(
    {"filters", "sort", "limit", "offset", "common_only", "entitlement"}
)
FILTER_KEYS: frozenset[str] = frozenset({"key", "field", "op", "value"})

#: View column → the row field that holds its value — **derived**, never written
#: here. One table states what every name means
#: (:data:`app.screen.columns.CATALOGUE_COLUMNS`); this mapping is that table
#: projected onto the columns the views declare (canonical *and* frozen tabs), so
#: :func:`view_row`, the ``columns`` projection and the filter compiler cannot
#: disagree about ``pe``, ``perf_4w`` or ``net_income`` again.
#:
#: A view column that resolves to nothing renders ``None``; the only such columns
#: are the documented session cells ``open``/``high``/``low``
#: (:data:`app.screen.columns.ABSENT_COLUMNS`), and the boot check below fails the
#: process if a fourth appears.
VIEW_FIELDS: Mapping[str, str] = column_map.view_fields(
    tuple(
        dict.fromkeys(
            name
            for definition in screen_views.VIEW_COLUMNS.values()
            for name in definition
        )
    )
)

#: Where a misplaced knob belongs — used to turn "unexpected key" into a fix.
_HINTS: Mapping[str, str] = {
    "limit": "'limit' belongs inside 'criteria'",
    "sort": "'sort' belongs inside 'criteria'",
    "filters": "'filters' belongs inside 'criteria'",
}

#: View columns that read a cell every universe row already carries (the light
#: row), not a parity metric. Kept because the compatibility view
#: ``market_cap`` → ``market_cap_usd`` is the one target that is a light cell;
#: everything else is checked against the resolver's row-column universe.
_VIEW_LIGHT_FIELDS: frozenset[str] = frozenset(
    {"price", "change_pct", "volume", "market_cap_usd"}
)
VIEW_FACT_FIELDS: frozenset[str] = frozenset({"insider_own", "inst_own"})


def _validate_view_fields() -> None:
    """Fail at import: a view cell must resolve to a column a row really carries.

    Two directions, because the old check only had the first and therefore let the
    bug through: every target must be a row column, **and** every column a view
    declares must have an entry — or be one of the documented session cells
    (``open``/``high``/``low``, see :data:`app.screen.columns.ABSENT_COLUMNS`).
    """

    known = column_map.ROW_FIELD_COLUMNS | set(_VIEW_LIGHT_FIELDS)
    unknown = sorted(set(VIEW_FIELDS.values()) - known)
    if unknown:
        raise RuntimeError(
            f"VIEW_FIELDS names field(s) no row carries: {unknown}; "
            "they are neither a light column nor a row column"
        )
    declared = set().union(*(set(columns) for columns in screen_views.VIEW_COLUMNS.values()))
    unmapped = sorted(
        declared - set(VIEW_FIELDS) - set(column_map.ABSENT_COLUMNS)
    )
    if unmapped:
        raise RuntimeError(
            f"{len(unmapped)} view column(s) map to no row field: {unmapped}; "
            "add them to app.screen.columns.CATALOGUE_COLUMNS"
        )
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


class PremiumRequired(SelectionError):
    """A filter this caller's plan does not reach — 403, never an empty 200.

    Deliberately a different status from every other :class:`SelectionError`:
    the request is well-formed and the deployment can answer it, so a 422 would
    blame the syntax and a 200 with zero rows would blame the market. What the
    caller has to change is the plan, and the code says exactly that.
    """

    status_code = 403
    code = "premium_required"


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

    ``field`` is ``None`` for a **bucket** criterion (``price:u5``, ``idx:sp500``):
    the predicate lives in :mod:`app.screen.buckets`, the token is the canonical
    id in ``value``, and the null field is the only marker that tells the
    executor to use the second compiler — which is why it is published rather
    than dropped.
    """

    field: str | None
    op: str
    value: Any
    key: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """The wire form: ``in``/``between`` tuples become JSON arrays.

        A bucket criterion publishes ``field: null`` and its token in ``value``
        — the null is the thing that tells the executor which compiler to use,
        so a "cleanup" that drops it would collide ``price:u5`` with
        ``price:o5`` in the ETag digest.
        """

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
    #: The Finviz signal whose population this query runs over, or ``None`` for
    #: the whole universe. A signal narrows the population and, when the
    #: request names no ``sort``, supplies the order its measure implies.
    signal: str | None = None
    asset_class: str | None = None
    view: str | None = None
    offset: int = 0
    common_only: bool = False
    fill_wide: bool = False
    #: The exact cells the caller wants back, in order. Empty keeps the
    #: historical light row, so every existing client is unaffected.
    columns: tuple[str, ...] = ()

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
    """Page size: a positive integer, clamped to :data:`PANEL_LIMIT`.

    Clamped, not refused: a caller asking for 1,000 rows asked for a page that
    does not exist, and the envelope reports the limit that was applied so the
    difference is visible instead of silent.
    """

    if raw is None:
        return PANEL_LIMIT
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise SelectionError(f"'limit' must be an integer, got {raw!r}")
    if raw < 1:
        raise SelectionError(f"'limit' must be at least 1, got {raw}")
    return min(raw, PANEL_LIMIT)


def _sort(raw: Any) -> str | None:
    """The order the page is ranked by: a numeric catalog field, ``-`` ascending.

    ``None`` means the payload named no sort at all, which is **not** the same
    answer as ``market_cap``: a signal's own measure becomes the default order
    when one is present (:mod:`app.screen.descriptive`), and a query with
    neither a sort nor a signal falls back to :data:`DEFAULT_SORT`. The
    distinction is resolved in :func:`_parse_criteria`, so a request that names
    ``market_cap`` explicitly and one that names nothing still answer with the
    same applied order.

    Anything else is a ``422 unknown_sort`` naming the accepted fields —
    including a text column (``sector`` is not an order) and a misspelling.
    """

    if raw is None:
        return None
    try:
        ranking.order_metric(raw)
    except ValueError as exc:
        raise SelectionError(str(exc), code="unknown_sort") from exc
    return str(raw)


def _signal(raw: Any) -> descriptive.Signal | None:
    """The signal the payload names, or ``None``. Two refusals, both 422.

    An unknown id is a typo (``unknown_signal``); an id the build cannot compute
    is ``unavailable_signal`` naming the metric it needs. Neither is ever an
    empty page — a caller reads an empty page as the signal's population.
    """

    if raw is None:
        return None
    try:
        return descriptive.signal(raw)
    except descriptive.SignalError as exc:
        raise SelectionError(str(exc), code=exc.code) from exc


def _asset_class(raw: Any) -> str | None:
    """The optional instrument family; ``None`` is "the region's own family"."""

    if raw is None:
        return None
    if raw not in ASSET_CLASSES:
        raise SelectionError(
            f"unknown asset_class {raw!r}; expected one of {', '.join(ASSET_CLASSES)}"
        )
    return str(raw)


def _common_only(raw: Any) -> bool:
    """Display-only switch: leave preferred/warrant lines out of the page."""

    if raw is None:
        return False
    if not isinstance(raw, bool):
        raise SelectionError(
            f"'criteria.common_only' must be a boolean, got {raw!r}"
        )
    return raw


def _fill_wide(raw: Any) -> bool:
    """Opt-in display enrichment; never part of the selection contract."""

    if raw is None:
        return False
    if not isinstance(raw, bool):
        raise SelectionError(f"'fill_wide' must be a boolean, got {raw!r}")
    return raw


def _offset(raw: Any) -> int:
    """The page offset; zero when absent, never a negative window."""

    if raw is None:
        return 0
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        raise SelectionError(
            f"'criteria.offset' must be an integer >= 0, got {raw!r}"
        )
    return raw


def _view_name(raw: Any) -> str | None:
    """The optional Finviz view; an unknown tab is a 422, never a light row."""

    if raw is None:
        return None
    if not isinstance(raw, str) or raw not in screen_views.SCREEN_VIEW_NAMES:
        raise SelectionError(
            f"unknown view {raw!r}; expected one of {', '.join(screen_views.SCREEN_VIEW_NAMES)}",
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
            f"{where} (key {key!r}) takes op 'eq' and one of its option "
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


def _numeric_literal(text: str) -> bool:
    """Is ``text`` a number a comparison could take? (``"5"``, ``"-0.5"``.)"""

    try:
        float(text)
    except (TypeError, ValueError):
        return False
    return True


def _bucket_criterion(
    key: str, item: Mapping[str, Any], where: str
) -> Criterion | None:
    """One bucket token → one field-less :class:`Criterion`, or ``None``.

    ``None`` means "not a bucket": the value is an ordinary number (or an
    ``in``/``between`` list) and the filter DSL must judge it, because a bucket
    key such as ``sh_price`` still answers ``{"op": "lt", "value": 5}``.

    A value that names a bucket is **canonicalised** before it is stored — the
    token is case-folded and expanded to its full ``<control>:<token>`` id — so
    ``SP500``, ``sp500`` and ``idx:sp500`` are one question and therefore one
    ETag. An id from another control (``price:u5`` sent to ``cap``) is refused:
    the namespace is the half of the id that says which metric is meant.
    """

    control = buckets.control_for(key)
    raw = item["value"]
    if control is None or not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text and buckets.example_token(key) is None:
        # A static bucket key refuses an empty value here; a dynamic one lets its
        # resolver name the rule it broke (``invalid_news_keyword``, not a generic
        # ``invalid_filter``).
        return None
    if ":" in text:
        namespace, _, token = text.partition(":")
        if namespace.strip().casefold() != control.casefold():
            accepted = ", ".join(item.id for item in buckets.for_key(key))
            raise SelectionError(
                f"{where} (key {key!r}): bucket {text!r} belongs to control "
                f"{namespace.strip()!r}, but key {key!r} only accepts {control!r} "
                f"ids ({accepted})",
                code="unknown_bucket",
            )
        identifier = f"{control}:{token.strip()}"
    else:
        identifier = f"{control}:{text}"

    try:
        found = buckets.bucket(identifier)
    except buckets.UnknownBucket as exc:
        if ":" not in text and _numeric_literal(text):
            return None
        raise SelectionError(f"{where} (key {key!r}): {exc}", code=exc.code) from exc
    except buckets.BucketError as exc:
        # ``InvalidBucketToken`` (a keyword outside its alphabet, a fund the
        # control does not offer) and ``UnavailableBucket`` (a snapshot that
        # cannot answer) carry their own stable codes.
        raise SelectionError(f"{where} (key {key!r}): {exc}", code=exc.code) from exc

    op = item["op"].strip().casefold() if isinstance(item["op"], str) else None
    if op not in {"eq", "==", "="}:
        raise SelectionError(
            f"{where} (key {key!r}): bucket {found.id!r} names its own comparison "
            f"({found.describe}), so op must be 'eq', got {item['op']!r}",
            code="invalid_filter",
        )
    return Criterion(field=None, op="eq", value=found.id, key=key)


def _criterion(item: Any, index: int, *, region: str | None = None) -> Criterion:
    """One filter object → one compiled :class:`Criterion`, or a 422.

    A ``key`` is resolved in the order the module docstring states: the 82 Finviz
    keys first (a legacy name is never shadowed), then a catalogue key through
    :func:`_alias_column`, and otherwise the Finviz refusal stands. The alias
    path echoes the key the caller wrote *and* the column it compiled to, so the
    envelope shows both ends of the translation.

    ``region`` is the tab the value vocabulary belongs to: a value column with a
    declared vocabulary refuses a value that region cannot carry, and it does so
    here — in ``parse_selection``, before a scan or a universe exists.
    """

    where = f"criteria.filters[{index}]"
    if not isinstance(item, Mapping):
        raise SelectionError(f"{where} must be an object, got {type(item).__name__}")
    _reject_extra(item, FILTER_KEYS, where)

    key = item.get("key")
    field = item.get("field")
    if (key is None) == (field is None):
        raise SelectionError(
            f"{where} must name exactly one of 'key' (a filter key, e.g. "
            "'cap') or 'field' (a screen row column, e.g. 'market_cap_usd')"
        )
    missing = [name for name in ("op", "value") if name not in item]
    if missing:
        raise SelectionError(f"{where} is missing {', '.join(missing)}")

    if key is not None:
        key_text = str(key)
        try:
            resolved = binding(key)
        except FilterKeyError as exc:
            column = _alias_column(key_text)
            if column is None:
                if isinstance(exc, UnavailableFilterKey) and buckets.control_for(key_text):
                    # A **withdrawn** control (``sh_insidertrans``): refused as a
                    # key, and still the owner of a registered vocabulary in
                    # which every token is refused. The *token* is the unit of
                    # refusal the registry, the document and this route have to
                    # agree on, so it answers first — 422 ``unavailable_bucket``
                    # with the family's own sentence. A value that names none of
                    # its tokens is not a token question, so it falls through to
                    # the key-level refusal below.
                    by_bucket = _bucket_criterion(key_text, item, where)
                    if by_bucket is not None:  # pragma: no cover - every token refuses
                        return by_bucket
                if isinstance(exc, UnknownFilterKey) and key_text in CATALOG_BY_KEY:
                    # A real catalogue column that no filter can read. Naming it
                    # is the whole point: a silent no-op would return the
                    # unfiltered screen and read as "no company has one".
                    raise SelectionError(
                        f"filter key {key_text!r} cannot be applied: it is a "
                        "catalog column, but no screen row carries a value a "
                        "filter can compare (ask for it in 'columns' instead). "
                        "Keys that filter a row today are listed at "
                        "GET /screens/select/schema",
                        code="unavailable_filter_key",
                    ) from exc
                raise SelectionError(str(exc), code=exc.code) from exc
            # A catalogue spelling of a real column (``pe``, ``market_cap``).
            column_value: Any = column
            prefix = f"{where} (key {key!r})"
        else:
            if resolved.choice:
                return _choice_criterion(key_text, item, where)
            if resolved.bucket:
                by_bucket = _bucket_criterion(key_text, item, where)
                if by_bucket is not None:
                    return by_bucket
            column_value = resolved.field
            if column_value is None:
                # A bucket-only key (``idx``) has no column and no numeric form:
                # the value must be one of its tokens, so the refusal names them.
                # A **dynamic** key has no enumerable family; it shows one
                # accepted shape instead (an empty value gets here too).
                family = buckets.for_key(key_text)
                if family:
                    accepted = ", ".join(item.id for item in family)
                    example = family[0].id
                else:
                    accepted = "a value this control resolves"
                    example = buckets.example_token(key_text) or f"{key_text}:<value>"
                raise SelectionError(
                    f"{where} (key {key_text!r}) takes a bucket token as its value "
                    f"(e.g. {{'key': {key_text!r}, 'op': 'eq', 'value': "
                    f"{example!r}}}); accepted: {accepted}",
                    code="invalid_filter",
                )
            prefix = f"{where} (key {key!r})"
    else:
        column_value = field
        prefix = where

    try:
        compiled = compile_filter(
            {"field": column_value, "op": item["op"], "value": item["value"]},
            region=region,
        )
    except UnknownValue as exc:
        # The word is wrong, not the grammar: a client switching on `error` can
        # tell the two apart, and the message carries the accepted list.
        raise SelectionError(f"{prefix}: {exc}", code=exc.code) from exc
    except ValueError as exc:
        raise SelectionError(f"{prefix}: {exc}", code="invalid_filter") from exc
    return Criterion(
        field=compiled.field,
        op=compiled.op,
        value=compiled.value,
        key=str(key) if key is not None else None,
    )


def _filters(raw: Any, *, region: str | None = None) -> tuple[Criterion, ...]:
    """The ANDed filter list; absent means the identity, not "filter nothing"."""

    if raw is None:
        return ()
    if isinstance(raw, (str, bytes)) or not isinstance(raw, (list, tuple)):
        raise SelectionError(
            "'criteria.filters' must be an array of {key|field, op, value} objects"
        )
    return tuple(_criterion(item, index, region=region) for index, item in enumerate(raw))


def _entitlement(raw: Any) -> str | None:
    """``criteria.entitlement`` → a tier, or a 422 naming the two tiers.

    Absent is ``None`` — the request is judged at the caller's plan. The field
    can only **lower** that plan: a premium caller may ask for the free view, and
    a free caller naming ``premium`` is overreach, not a grant.
    """

    if raw is None:
        return None
    if not isinstance(raw, str) or raw.strip().casefold() not in entitlements.TIERS:
        raise SelectionError(
            f"'criteria.entitlement' must be one of {', '.join(entitlements.TIERS)}, "
            f"got {raw!r}",
            code="unknown_entitlement",
        )
    return raw.strip().casefold()


def _gate(
    filters: Sequence[Criterion], *, plan: str | None, declared: str | None
) -> None:
    """Refuse a request whose filters this plan does not reach — 403.

    Two ways to be over the plan, one answer:

    * the **body** declares a tier above the caller's plan (``entitlement:
      premium`` from an anonymous caller) — refused, because honouring it would
      make the field a self-issued licence and ignoring it would be the silent
      downgrade this endpoint exists to avoid;
    * a **filter key** is configured ``premium`` in
      ``app/screen/data/entitlements.json`` and the request's entitlement is
      ``free`` — refused by name, before a scan is spent.

    The gate reads the **key** the request named, which is why
    :mod:`app.screen.filter_fields` refuses to map a gated feature to a row
    field: a ``field`` spelling would be the same question with the gate skipped.
    """

    resolved = plan or entitlements.DEFAULT_PLAN
    if entitlements.overreach(resolved, declared):
        raise PremiumRequired(
            f"this request declares entitlement {declared!r} but the caller's plan "
            f"is {resolved!r}: the plan comes from the "
            f"{entitlements.PLAN_HEADER} header or the "
            f"{entitlements.API_KEY_HEADER} key, and a body field cannot raise it"
        )
    entitlement = entitlements.narrow(resolved, declared)
    requested = {
        str(criterion.key)
        for criterion in filters
        if criterion.key is not None and criterion.key in entitlements.FEATURES
    }
    blocked = entitlements.blocked(requested, entitlement)
    if not blocked:
        return
    names = ", ".join(repr(key) for key in blocked)
    raise PremiumRequired(
        f"filter key(s) {names} are premium features in "
        f"{entitlements.config_label()}, and this request's plan is "
        f"{entitlement!r}: send a {entitlements.API_KEY_HEADER} carrying a premium "
        f"key, or declare the plan in the {entitlements.PLAN_HEADER} header. The "
        "request is refused instead of answered, because a premium filter served "
        "as an empty page reads as 'no company matches'"
    )


def _criteria_block(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    """The ``criteria`` object, or a 422 that says which mode the caller wants."""

    for name in ("symbols", "source", "snapshot_id"):
        if name in payload:
            raise SelectionError(
                f"mode 'criteria' selects by query and does not take {name!r}; "
                "use mode 'symbols' for an explicit ticker list"
            )
    if "limit" in payload:
        # Top-level ``limit`` exists for symbols mode, which has no block of its
        # own; a query has one, and a limit in two places is two answers.
        raise SelectionError(f"{_HINTS['limit']}: a criteria selection sends it inside 'criteria'")
    block = payload.get("criteria")
    if not isinstance(block, Mapping):
        raise SelectionError(
            "criteria mode needs a 'criteria' object with filters, sort and limit"
        )
    _reject_extra(block, CRITERIA_KEYS, "'criteria'")
    return block


#: Every name a caller may ask for: the resolver's table (catalogue keys, the
#: shipped client's names, every row field) plus the two structural cells a
#: projection can always ask for.
_KNOWN_COLUMNS: frozenset[str] = column_map.ROW_COLUMNS | column_map.ROW_FIELD_COLUMNS | frozenset(
    {
        "rank",
        "as_of",
        # The brand mark is part of the row identity (the browse row has always
        # carried it), so a projection must be able to ask for it by name — and
        # only by name: it is a 600-byte base64 monogram and 100 implicit copies
        # would triple a twelve-column panel.
        "logo",
    }
)


def _alias_column(key: str) -> str | None:
    """A catalogue key's row column, through the one resolver.

    :func:`app.screen.columns.field_for` maps the catalogue spelling to the column
    ``/screens/select`` reads (``pe`` → ``pe_ttm``, ``market_cap`` →
    ``market_cap_usd``, ``perf_4w`` → ``ret_1m``); a catalogue key whose spelling
    *is* the column (``roic``, ``ps``, ``dividend_yield``) resolves to itself.
    ``None`` means the key is not a catalogue column, or is one no *filter* can
    compare — the caller then keeps its own refusal.

    The filter is the third reader of the same table: it reads the column the
    views render and the ``columns`` projection returns, so one key cannot mean
    two numbers depending on which half of the document mentions it.
    """

    if key not in CATALOG_BY_KEY and key not in FIELDS:
        return None
    column = column_map.field_for(key)
    return column if column in FIELDS else None


def _columns(raw: Any) -> tuple[str, ...]:
    """Validate ``columns``: known names, no duplicates, order preserved."""

    if raw is None:
        return ()
    if not isinstance(raw, (list, tuple)) or not raw:
        raise SelectionError(
            "selection 'columns' is a non-empty list of column names"
        )
    if len(raw) > COLUMN_LIMIT:
        raise SelectionError(
            f"selection 'columns' asks for {len(raw)} cells; the maximum is "
            f"{COLUMN_LIMIT}"
        )
    ordered: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            raise SelectionError(
                "every entry of 'columns' must be a non-blank string"
            )
        key = item.strip()
        if key not in _KNOWN_COLUMNS:
            raise SelectionError(
                f"unknown column {key!r}; ask for a screener field or a catalog column"
            )
        if key in seen:
            continue
        seen.add(key)
        ordered.append(key)
    return tuple(ordered)


def _parse_criteria(payload: Mapping[str, Any], *, plan: str | None = None) -> SelectionRequest:
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
    view = _view_name(payload.get("view"))
    columns = _columns(payload.get("columns"))
    if view is not None and columns:
        raise SelectionError(
            "a selection takes 'view' or 'columns', not both: two ways to name "
            "the same output is a question with two answers"
        )
    family_raw = _asset_class(payload.get("asset_class"))
    try:
        family = asset_class_for(str(region), family_raw)
    except ValueError as exc:
        raise SelectionError(str(exc)) from exc
    # The signal is resolved before the sort because it can *become* the order:
    # a signal names a population and its own measure, so an absent `sort` means
    # "the signal's order", not "market cap". An explicit sort always wins.
    chosen = _signal(payload.get("signal"))
    sort = _sort(block.get("sort"))
    if sort is None:
        sort = descriptive.default_sort(chosen) if chosen is not None else DEFAULT_SORT
    # The filters are compiled first (so an unknown key is a 422 with its own
    # message) and then gated: a *premium* key this plan cannot ask is a 403
    # naming it, decided before a scan, a universe or a store is touched.
    filters = _filters(block.get("filters"), region=str(region))
    _gate(filters, plan=plan, declared=_entitlement(block.get("entitlement")))
    return SelectionRequest(
        mode="criteria",
        region=str(region),
        filters=filters,
        sort=sort,
        limit=_limit(block.get("limit")),
        offset=_offset(block.get("offset")),
        common_only=_common_only(block.get("common_only")),
        symbols=(),
        source=None,
        snapshot_id=None,
        signal=chosen.id if chosen is not None else None,
        asset_class=family,
        view=view,
        fill_wide=_fill_wide(payload.get("fill_wide")),
        columns=columns,
    )


def _parse_symbols(payload: Mapping[str, Any]) -> SelectionRequest:
    for name in ("region", "criteria", "signal"):
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

    view = _view_name(payload.get("view"))
    columns = _columns(payload.get("columns"))
    if view is not None and columns:
        raise SelectionError(
            "a selection takes 'view' or 'columns', not both: two ways to name "
            "the same output is a question with two answers"
        )

    return SelectionRequest(
        mode="symbols",
        region=None,
        filters=(),
        sort=None,
        limit=_limit(payload.get("limit")),
        symbols=tuple(symbols),
        source=str(source),
        snapshot_id=snapshot_id,
        asset_class=_asset_class(payload.get("asset_class")),
        view=view,
        offset=0,
        common_only=False,
        fill_wide=_fill_wide(payload.get("fill_wide")),
        columns=columns,
    )


def parse_selection(payload: Any, *, plan: str | None = None) -> SelectionRequest:
    """Validate a selection document and return the request the executor runs.

    Raises :class:`SelectionError` (always a 422) for anything that cannot be
    executed: a non-object body, an unknown or missing mode, a knob that belongs
    to the other mode, an unknown filter field or operator, a Finviz key with no
    light column, an unknown region/source/sort, or a non-positive limit.

    ``plan`` is the caller's plan, resolved by the route from the request headers
    (:func:`app.screen.entitlements.resolve_plan`); ``None`` is an anonymous
    caller, which is the free plan. A premium filter on a plan that does not
    reach it raises :class:`PremiumRequired` — a **403**, not a 422.
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
    if mode == "criteria":
        return _parse_criteria(payload, plan=plan)
    return _parse_symbols(payload)


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
    offset: int = 0
    columns: tuple[str, ...] = ()

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
            "columns": list(self.columns),
            "symbols": list(self.symbols),
            "missing": list(self.missing),
            "rows": [dict(row) for row in self.rows],
        }


def _predicates(request: SelectionRequest) -> tuple[dict[str, Any], ...]:
    """The DSL half of the criteria: every criterion that has a field.

    A bucket criterion has ``field=None`` and is applied by
    :func:`app.screen.buckets.apply_bucket_filters` instead. Dropping it here is
    what keeps a token out of :func:`app.screen.filters.compile_filter`, where it
    would be either a 422 or — worse — coerced into a number.
    """

    return tuple(
        {"field": criterion.field, "op": criterion.op, "value": criterion.value}
        for criterion in request.filters
        if criterion.field is not None
    )


def _selected(
    rows: Sequence[dict[str, Any]], request: SelectionRequest, *, as_of: str
) -> tuple[list[dict[str, Any]], int]:
    """Criteria: AND the filters over the whole universe, then rank, then cut.

    The order matters. Filtering first means ``rank`` numbers the *matches*
    (1..n of the selection), and cutting last means ``total`` still counts all of
    them — ranking before filtering would number rows the caller never sees and
    cutting before counting would report the page as the market. ``total`` comes
    from the same pass that built the page, so the two can never disagree.

    Two compilers, one predicate list each: the filter DSL over ``field``
    criteria and :mod:`app.screen.buckets` over the field-less ones. They AND, so
    the order between them is presentation only — the DSL first keeps the common
    case (a numeric range) independent of a clock.

    ``as_of`` is the answer's own read moment: ``earn:today`` is New York's day
    *of this answer*, never ``date.today()`` inside a predicate, or two rows of
    one page could be judged against two days.
    """

    matched = apply_filters(rows, _predicates(request), region=request.region)
    matched = buckets.apply_bucket_filters(matched, request.filters, as_of=as_of)
    if request.signal is not None:
        # The signal's population, applied where every other filter runs: after
        # the DSL and the buckets, before the rank — so `total` counts the
        # signal's rows and the page numbers them 1..n.
        chosen = descriptive.signal(request.signal)
        matched = [dict(row) for row in matched if chosen.accepts(row)]
    ranked = ranking.rank_rows(matched, request.sort or DEFAULT_SORT)
    page = ranked[request.offset :]
    return ranking.top_rows(page, request.limit), len(matched)


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
    dated; everything else is read through
    :func:`app.screen.columns.value_for` — the same resolver ``columns`` and the
    filter compiler use, so two spellings of one metric cannot return two numbers.
    A column with no row field (the documented session cells ``open``/``high``/
    ``low``) is ``None``, never ``0``.
    """

    definition = screen_views.view(name)
    shaped: dict[str, Any] = {
        "rank": int(row["rank"]),
        "symbol": row.get("symbol"),
        "name": row.get("name"),
        "as_of": as_of,
    }
    for column in definition.columns:
        shaped[column] = column_map.value_for(row, column)
    return shaped


def _column_row(
    row: Mapping[str, Any], columns: Sequence[str], *, as_of: str
) -> dict[str, Any]:
    """One row carrying the requested cells.

    ``symbol`` and ``as_of`` are structural: a row that cannot name its
    instrument or date its observation is not a row a client can use, so they
    are always present. Everything else is exactly what was asked for, resolved
    through the one table: ``columns=["pe"]`` and ``view=value`` read the same
    cell because both call :func:`app.screen.columns.value_for`. A requested cell
    the row does not carry answers ``None``, because the column exists and its
    value is unknown, which is different from a column that was never requested
    at all.
    """

    document: dict[str, Any] = {"symbol": row.get("symbol")}
    for column in columns:
        document[column] = column_map.value_for(row, column)
    document["as_of"] = as_of
    return document


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

    universe = column_map.harmonise_rows(rows)
    if request.is_criteria:
        selected, total = _selected(universe, request, as_of=as_of)
        missing: tuple[str, ...] = ()
        sort: str | None = request.sort
        echoed_snapshot: str | None = None
    else:
        if not snapshot_id:
            raise ValueError("symbols mode needs the snapshot_id it resolves against")
        selected, total, missing = _resolved(universe, request)
        sort = None
        echoed_snapshot = snapshot_id

    if request.columns:
        page = tuple(
            _column_row(row, request.columns, as_of=as_of) for row in selected
        )
    elif request.view:
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
        offset=request.offset,
        columns=request.columns,
    )


# ---------------------------------------------------------------------------
# Caching: one canonical question, one strong validator
# ---------------------------------------------------------------------------

#: Namespace of the selection validator, so a tag from this route can never be
#: mistaken for the depth store's or the quote hub's.
ETAG_NAMESPACE = "select"


def canonical_request(request: SelectionRequest) -> dict[str, Any]:
    """The request as data: every default applied, every clamp resolved.

    This is the document the ETag hashes, so it has to be the *question* rather
    than the bytes the caller sent. ``{"criteria": {}}`` and a payload that spells
    out ``limit: 100``, ``sort: market_cap``, ``offset: 0`` and
    ``common_only: false`` select exactly the same rows; they must therefore
    produce one tag, or the panel's cache misses on a client that is merely more
    explicit. Conversely nothing that changes the answer may be absent from it:
    ``limit: 500`` and ``limit: 101`` both clamp to 100 and are the same page,
    which is why the clamped value is hashed and not the requested one.

    ``json.dumps(sort_keys=True)`` fixes key order, and the field list is stated
    here in one place so a new knob has to be added deliberately.
    """

    return {
        "mode": request.mode,
        "region": request.region,
        "asset_class": request.asset_class,
        "signal": request.signal,
        "filters": [criterion.as_dict() for criterion in request.filters],
        "sort": request.sort,
        "limit": request.limit,
        "offset": request.offset,
        "symbols": list(request.symbols),
        "source": request.source,
        "snapshot_id": request.snapshot_id,
        "view": request.view,
        "columns": list(request.columns),
        "common_only": request.common_only,
        "fill_wide": request.fill_wide,
    }


def request_digest(request: SelectionRequest) -> str:
    """A 128-bit digest of :func:`canonical_request` (32 hex characters).

    Hashed instead of the response: the request is a few hundred bytes while a
    twelve-column panel is tens of kilobytes, and a body hash would also have to
    include ``as_of`` — the read moment — which changes on every call and would
    make every tag unique. Measured cost of the canonical form + hash: ~10 µs.
    """

    encoded = json.dumps(
        canonical_request(request), sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:32]


def selection_etag(*, request: SelectionRequest, revision: str) -> str:
    """A strong, quoted ETag over ``(question, data revision)``.

    ``revision`` is the newest store stamp the answer is built on — the depth
    store's cycle ``as_of`` for a criteria query, the stored snapshot's ``as_of``
    for a ticker lookup — and never ``now()``. A validator that moved with the
    clock would answer one 304 per microsecond and none per client.

    Strong (unquoted ``W/`` absent) because the representation really is
    byte-identical for a given tag: only the ``as_of`` field of a *new* body
    would differ, and a 304 never carries a body at all.
    """

    stamp = hashlib.sha1(str(revision).encode("utf-8")).hexdigest()[:8]
    return f'"{ETAG_NAMESPACE}-{stamp}-{request_digest(request)}"'


# ---------------------------------------------------------------------------
# The published request schema
# ---------------------------------------------------------------------------


def _filter_key_extension() -> dict[str, dict[str, object]]:
    """All 82 keys, in panel order: the column, or the reason it is refused.

    A key whose column declares a closed value vocabulary also publishes it —
    ``values`` (the canonical codes) and ``aliases`` (the spellings the compiler
    resolves to them). ``exch`` is the only such key today: without the block a
    schema-driven picker can only offer ``XNYS``, because the four human
    spellings lived in the compiler and in the published contract nowhere.
    Derived from the tables the validator itself reads, so the two cannot drift.
    """

    extension: dict[str, dict[str, object]] = {}
    for entry in CATALOGUE:
        block = entry.as_dict()
        field = entry.field
        if field is not None and field in VALUE_ALIASES:
            block["values"] = list(US_VALUES.get(field, ()))
            block["aliases"] = dict(VALUE_ALIASES[field])
        extension[entry.key] = block
    return extension


def _filter_alias_extension() -> dict[str, dict[str, object]]:
    """Every catalogue spelling a filter may name, and the column it means.

    The validator resolves a ``key`` in one order (:func:`_criterion`): the 82
    Finviz keys first — a legacy name is never shadowed — then a catalogue key
    through :func:`_alias_column`. Only the first half was published, so a client
    that generated its filter picker from ``x-filter-keys`` could never offer
    ``pe``, ``roic`` or ``market_cap``: half of one vocabulary was invisible in
    the contract (F7).

    Derived from the two tables the validator itself reads, so it cannot drift:
    a key is published here exactly when ``binding()`` refuses it *and*
    :func:`_alias_column` resolves it to a row column. A catalogue column that no
    row can filter on resolves to ``None`` and stays out, because the validator
    refuses it by name (``unavailable_filter_key``).
    """

    aliases: dict[str, dict[str, object]] = {}
    for key in sorted(set(CATALOG_BY_KEY) | set(FIELDS)):
        try:
            binding(key)
        except FilterKeyError:
            column = _alias_column(key)
        else:
            continue  # a Finviz key: already published, never shadowed
        if column is None:
            continue
        entry: dict[str, object] = {"field": column, "kind": FIELDS[column]}
        spec = CATALOG_BY_KEY.get(key)
        if spec is not None:
            entry["label"] = spec.label
        aliases[key] = entry
    return aliases


#: The keys that are **only** a bucket (``idx``, ``news_keywords``,
#: ``etf_heldby``): no column, no option vocabulary. Derived so the published
#: disposition cannot outlive the table it counts; the dynamic controls are
#: included because a client reading this sentence must see every key whose value
#: is a bucket token.
_BUCKET_ONLY: tuple[str, ...] = tuple(
    key for key in (*buckets.KEYS, *buckets.DYNAMIC_KEYS) if key not in ROW_FIELDS
)

#: The alias half of the published vocabulary, built once at import: it is a pure
#: function of the tables above, and the schema below reads it twice (count and
#: table) — two spellings of one fact would be one too many.
_FILTER_ALIASES: dict[str, dict[str, object]] = _filter_alias_extension()


def _sort_vocabulary() -> list[str]:
    """Every spelling ``criteria.sort`` accepts, sorted.

    Both directions of every numeric catalog field (``pe_ttm`` descending,
    ``-pe_ttm`` ascending — Finviz's ``o=-marketcap``) plus the three legacy
    names the browse route and every bookmark still use. Derived from
    :mod:`app.screen.rank`, so the published enum cannot outlive the validator.
    """

    names = set(ranking.ORDER_BY) | set(ranking.ALIASES)
    return sorted(names | {f"-{name}" for name in names})


def _filter_value_schema() -> dict[str, object]:
    scalar: dict[str, object] = {"type": ["number", "string", "boolean"]}
    return {
        "description": (
            "The comparison value. `in` and `between` take an array (`between` "
            "exactly two numbers); every other operator takes one scalar. For an "
            f"option-driven key ({', '.join(CHOICE_FIELDS)}) the value is the "
            "source-panel option token itself (`1w10u`, `dup`, `wo2`), and the "
            "predicate "
            "each token compiles to is published under "
            "`x-filter-keys.<key>.choice.options`. For a key whose entry carries "
            "`bucket: true` the value is one of the bucket ids that key publishes "
            "(`price:u5`, `cap:mega`, `idx:sp500`), with `op: eq`; the predicate "
            "each id runs is in `GET /screens/filters` and in the key's own "
            "`x-filter-keys` entry. For `exch` the value is a MIC "
            "(`XNAS`, `XNYS`, `XASE`, `XCBO`) or one of the spellings published "
            "under `x-filter-keys.exch.aliases`; any other value on the US tabs "
            "is a 422 `unknown_value` whose message lists the accepted codes, "
            "while a non-US region (`global`) accepts a provider code matching "
            "`[A-Z0-9]{1,8}` (e.g. `SIX`, `XLON`) and answers with the rows it "
            "names — zero when it names none. Everywhere else the source panel's "
            "option "
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
        "`limit` caps the page at "
        f"{PANEL_LIMIT} rows. `symbols` is an explicit ticker list resolved "
        "inside the universe addressed by `snapshot_id`, in request order, with "
        "its own `limit`. The response carries `total` (every row that matched) "
        "separately from `returned` (the page), so a 100-row panel never claims "
        "to be a 100-stock market. A filter key may be one of the 82 filter "
        "keys (`x-filter-keys`) or one of the catalogue spellings in "
        "`x-filter-aliases` (`pe`, `roic`, `market_cap`); both are resolved to "
        "one row column, and a key no column can answer is a 422 that names it. "
        "The two tables together are the vocabulary a filter picker is built "
        "from. `criteria.sort` orders by any numeric catalog field (`-field` "
        "ascends), and the top-level `signal` narrows the whole query to one of "
        "the signal populations. `GET /screens/filters` publishes the "
        "Descriptive group itself — every control, its options, its availability "
        "and the predicate each option compiles to — so a panel never hardcodes "
        "a vocabulary the server owns."
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
        "signal": {
            "enum": [item.id for item in descriptive.SIGNALS],
            "description": (
                "Criteria only: one of the "
                f"{len(descriptive.SIGNALS)} signals. "
                f"{len(descriptive.AVAILABLE)} can run here "
                f"({', '.join(descriptive.AVAILABLE)}); the other "
                f"{len(descriptive.SIGNALS) - len(descriptive.AVAILABLE)} are a 422 "
                "`unavailable_signal` naming the metric they would need "
                "(analyst revisions, an earnings session stamp, the news store, "
                "chart-pattern detection). A signal sets the **population**, and "
                "when `criteria.sort` is absent its own measure becomes the "
                "order (`top-losers` ascends); an explicit sort always wins. A "
                "`symbols` selection does not take it."
            ),
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
            "enum": list(screen_views.SCREEN_VIEW_NAMES),
            "description": (
                "Both modes: shape every response row as one of the six "
                "views. A viewed row carries `rank`/`symbol`/`as_of` plus one "
                "cell per declared view column, filled from the parity metrics "
                "the bulk scan carries (missing columns stay null, never 0); "
                "without `view` the row is the light screen row."
            ),
        },
        "columns": {
            "type": "array",
            "maxItems": COLUMN_LIMIT,
            "uniqueItems": True,
            "items": {"type": "string"},
            "description": (
                "Both modes: the exact cells each row returns, in order. "
                "`symbol` and `as_of` are always present; a requested cell the "
                "row does not carry is null. Omit it for the historical light "
                "row. `logo` is an accepted column name (a vendored asset path "
                "or an offline monogram data URI) and is the one cell that is "
                "opt-in: a 100-row panel that did not ask for it does not carry "
                "100 base64 marks. Mutually exclusive with `view` — two ways to "
                "name the same output is a question with two answers."
            ),
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "default": PANEL_LIMIT,
            "description": (
                "Symbols only: the page size for a ticker lookup, which has no "
                "`criteria` block to carry it. Clamped to "
                f"{PANEL_LIMIT}, exactly like `criteria.limit`. A criteria "
                "query sends its limit inside `criteria`; a second answer at "
                "the top level is a 422."
            ),
        },
        "fill_wide": {
            "type": "boolean",
            "default": False,
            "description": (
                "Both modes: opt-in display enrichment for the wide table. "
                "Reads page-only cells such as employees, IPO and insider "
                "transactions from the stock page after the selection has been "
                "answered. A missing cell stays null; this never changes which "
                "rows match."
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
                    "enum": _sort_vocabulary(),
                    "default": DEFAULT_SORT,
                    "description": (
                        "The one metric the page is ordered by: any numeric "
                        "catalog field, descending, or `-<field>` for ascending "
                        "(the panel's own `o=-marketcap`). The three legacy names "
                        "`market_cap`, `turnover` and `gainers` still resolve. A "
                        "text column is not an order — `sector` is a 422 "
                        "`unknown_sort` naming the accepted fields. When `signal` "
                        "is named and this is absent, the signal's own measure "
                        "becomes the order; an explicit sort always wins."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "default": PANEL_LIMIT,
                    "description": (
                        "Page size. Clamped to "
                        f"{PANEL_LIMIT}: a larger value is not an error, it "
                        "simply cannot widen the panel, and the response reports "
                        "the limit that was applied."
                    ),
                },
                "offset": {
                    "type": "integer",
                    "minimum": 0,
                    "default": 0,
                    "description": (
                        "Zero-based row offset after filtering and sorting. The "
                        "response still reports the full post-filter total, so "
                        "20-row pages can walk the whole universe."
                    ),
                },
                "common_only": {
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "Display-only switch: keep the common-stock universe and "
                        "leave preferred/warrant lines out of the page."
                    ),
                },
                "entitlement": {
                    "enum": list(entitlements.TIERS),
                    "description": (
                        "The tier this request asks to be judged at. It can only "
                        "**narrow** the caller's plan — resolved from the "
                        f"`{entitlements.PLAN_HEADER}` header or from the plan the "
                        f"`{entitlements.API_KEY_HEADER}` key carries, with "
                        f"`{entitlements.DEFAULT_PLAN}` for an anonymous caller. A "
                        "request naming a tier above its plan is a 403 "
                        "`premium_required`, and so is any filter key whose tier in "
                        "app/screen/data/entitlements.json is `premium` while the "
                        "request's entitlement is `free`: refused by name, never "
                        "answered with an empty page and never silently downgraded. "
                        "Omit it to be judged at the plan."
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
                        {"required": ["limit"]},
                    ]
                },
            },
        },
        {
            "if": {"required": ["mode"], "properties": {"mode": {"const": "symbols"}}},
            "then": {
                "required": ["symbols", "source", "snapshot_id"],
                "not": {
                    "anyOf": [
                        {"required": ["region"]},
                        {"required": ["criteria"]},
                        {"required": ["signal"]},
                    ]
                },
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
                "One predicate: exactly one of `key` (a filter key, "
                "resolved to its row column) or `field` (the column itself), plus "
                "an operator and a value. A key whose entry in `x-filter-keys` "
                "carries `choice` takes `op: eq` and an option token as `value`; "
                "a key whose entry carries `bucket` also takes one of its named "
                "bucket ids that way (`price:u5`, `cap:mega`, `idx:sp500`), and "
                "`GET /screens/filters` publishes every id with the predicate it "
                "compiles to."
            ),
            "properties": {
                "key": {
                    "type": "string",
                    "description": (
                        f"One of the {len(CATALOGUE)} filter keys. The "
                        f"{len(ROW_FIELDS)} that resolve to one row column, the "
                        f"{len(CHOICE_FIELDS)} that filter through their option "
                        f"vocabulary, the {len(_BUCKET_ONLY)} bucket-only key "
                        f"({', '.join(_BUCKET_ONLY)}) that filters through a "
                        f"named bucket, and the "
                        f"{len(unavailable_keys())} that are refused with their "
                        "reason, are listed in x-filter-keys. "
                        f"A filter may also name one of the {len(_FILTER_ALIASES)} "
                        "catalogue spellings in x-filter-aliases (`pe`, `roic`, "
                        "`market_cap`), each published with the row column it "
                        "means; the two tables together are the "
                        f"{len(CATALOGUE) + len(_FILTER_ALIASES)} keys a filter "
                        "may name, and any other key is a 422 that names it."
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
    "x-filter-alias-count": len(_FILTER_ALIASES),
    "x-filter-aliases": _FILTER_ALIASES,
    # The tier policy, beside the key tables: a client can say *why* a key is
    # locked without a second request, and the same table says which keys a
    # `free` plan may ask. `criteria.entitlement` is the field a request narrows
    # its plan with; the two headers are how the plan itself is presented.
    "x-entitlement": {
        "tiers": list(entitlements.TIERS),
        "default_plan": entitlements.DEFAULT_PLAN,
        "plan_header": entitlements.PLAN_HEADER,
        "api_key_header": entitlements.API_KEY_HEADER,
        "request_field": "criteria.entitlement",
        "features": dict(entitlements.feature_tiers()),
        "note": (
            "Tier is configured in app/screen/data/entitlements.json, not in code: "
            "a feature set to `free` there is answered to every caller on the next "
            "request. A premium feature the deployment cannot source is still "
            "refused by its own reason (x-filter-keys.<key>.reason) — tier and "
            "availability are different facts."
        ),
    },
    # The view catalogue travels with the document itself, not in a copy the
    # route makes on the way out. `schemas/stock_selection_request_v1.json` is
    # this object, and a key that existed only in the route's return value was
    # F3: the served document and the published artifact differed by exactly
    # this key, so "the contract" was two documents with one name. It is an
    # annotation, not an input — `properties` does not name it, so a body that
    # carries `views` is still refused, and a JSON Schema validator ignores a
    # keyword it does not know (it sits beside `x-filter-keys`, which is how
    # this document already publishes non-instance metadata). The client builds
    # its tab strip and its column lists from it instead of hardcoding names the
    # server had already renamed (Phase 0's F1).
    "views": screen_views.view_catalogue(),
}

__all__ = [
    "CRITERIA_KEYS",
    "DEFAULT_SORT",
    "DISPLAY_LIMIT",
    "ETAG_NAMESPACE",
    "FILTER_KEYS",
    "MODES",
    "PANEL_LIMIT",
    "SELECTION_KEYS",
    "SELECTION_REQUEST_SCHEMA",
    "SOURCES",
    "VIEW_FIELDS",
    "VIEW_FACT_FIELDS",
    "Criterion",
    "PremiumRequired",
    "Selection",
    "SelectionError",
    "SelectionRequest",
    "canonical_request",
    "parse_selection",
    "request_digest",
    "select_rows",
    "selection_etag",
    "view_row",
]
