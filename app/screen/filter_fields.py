"""Every one of the 82 Finviz filter keys: a row field, or a refusal by name.

:mod:`app.screen.filters` answers "does Ztockz accept the same filter keys as
Finviz" and leaves ``FilterSpec.field`` as ``None`` for the keys no screen row
can answer. This module answers the next question, and it is the one a selection
endpoint cannot dodge: **what happens when a caller sends a key that has no
column?**

The answer is a refusal that names the key, the Finviz label and the metric that
is missing. Silence is not an option. A selection that accepts ``fa_pe`` and
drops it returns the unfiltered screen, and the caller reads that as "every
stock has a P/E below 10" — a fabricated answer produced by an ignored
parameter. So the contract is: **a filter key either maps to a row column or the
request is rejected (422) with the reason**, never applied as a no-op.

Thirty-six keys map and forty-six are refused:

* **Seven light columns** — exchange, sector, country, market cap, price,
  change %, current volume — have been on the browse row from the start;
* **twenty-nine parity metrics** joined them when the bulk scan started copying
  the provider's P/E, beta, RSI, SMA and ratio columns
  (:data:`app.screen.universe.PARITY_FIELDS`). They are real columns on a real
  row: a null cell means the provider did not report that metric for that
  company, and a filter on it simply does not match (unknown is not zero). The
  last four were added only after a live probe showed the provider really fills
  them: ``industry`` (a label such as ``Semiconductors``), ``dividends_yield``
  (percent: ``0.47`` = 0.47%), ``gap`` (signed percent) and ``ATR`` (price
  units);
* **forty-six are refused**, each with the metric it needs. Four shapes of
  refusal: no column measures it (index membership, theme, dividend growth and
  payout ratio, insider/institutional ownership, news, ETF holdings,
  after-hours, patterns); one key spans several columns (``ta_perf`` covers ten
  performance windows, ``ta_volatility`` two); the provider's unit is not
  Finviz's (``ta_highlow52w`` is a percent distance, the row carries absolute
  52-week prices, and ``Recommend.All`` is a -1..1 score against Finviz's 1..5);
  or the column the provider offers answers **null for every row**
  (``price_earnings_forward``, ``price_sales_ttm``, ``short_float``,
  ``ipo_date`` on both the US and the global scan), where a filter could only
  ever return an empty screen.

Two things this module refuses to invent:

* **No placeholder field.** A key without a column has no entry in
  :data:`ROW_FIELDS`; :func:`binding` raises instead of answering with a column
  that is always null.
* **No option-vocabulary translation.** Finviz's ``mega``, ``u5`` and ``ob70``
  are *display* tokens for range buckets. Turning ``cap: mega`` into
  ``market_cap_usd >= 200e9`` is a metric decision that belongs with the metric,
  not with a key table, so a caller sends an explicit operator and a number
  (``{"key": "cap", "op": "gte", "value": 200000000000}``) and this module only
  resolves the key to its column.

The mapping is not a second source of truth: :data:`ROW_FIELDS` is *derived* from
``FILTER_CATALOG[*].field``, and :func:`_validate` fails the process if the two
ever disagree or if any of the 82 keys is left without a disposition.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from app.screen.filters import FIELDS, FILTER_CATALOG

#: Finviz key → canonical row column, for every key the catalogue gives a field.
#: Derived from ``FILTER_CATALOG`` so the two can never drift: one table says
#: which column a key filters, and this module publishes it under the shape the
#: selection endpoint consumes. Panel order is preserved.
ROW_FIELDS: Mapping[str, str] = MappingProxyType(
    {key: spec.field for key, spec in FILTER_CATALOG.items() if spec.field is not None}
)

#: Why a key cannot filter yet: one sentence per group of keys, naming the metric
#: the scan does not carry, so a 422 can say *what* is absent instead of "invalid".
_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "needs index membership (S&P 500, Nasdaq-100, Dow, Russell): the scan "
        "has no index-constituent column, and a market-cap threshold is not a "
        "membership test",
        ("idx",),
    ),
    (
        "needs theme/sub-theme classification: the scan carries no theme column",
        ("theme", "subtheme"),
    ),
    (
        "needs dividend growth and payout ratio: the scan carries a dividend "
        "yield only (`dividends_yield`), which is neither a growth rate nor the "
        "share of earnings paid out",
        ("fa_divgrowth", "fa_payoutratio"),
    ),
    (
        "needs Finviz's 1-5 analyst score: the scanner's `Recommend.All` is a "
        "-1..+1 mean rating, so a filter written on Finviz's scale (2 = buy) "
        "matches nothing — the column is carried for display, not for this key; "
        "mapping it would mean inventing a derived field with an explicit "
        "`3 - 2 * score` transform, which no row carries today",
        ("an_recom",),
    ),
    (
        "needs a forward P/E column the scanner fills: `price_earnings_forward` "
        "answered null for all 5,720 US rows (and 96,278 global rows) when probed "
        "live, so a filter on it can only return an empty screen",
        ("fa_fpe",),
    ),
    (
        "needs a P/S column the scanner fills: `price_sales_ttm` answered null for "
        "all 5,720 US rows (and 96,278 global rows) when probed live, so a filter "
        "on it can only return an empty screen",
        ("fa_ps",),
    ),
    (
        "needs a short-interest column the scanner fills: `short_float` answered "
        "null for all 5,720 US rows (and 96,278 global rows) when probed live, so "
        "a filter on it can only return an empty screen",
        ("sh_short",),
    ),
    (
        "needs an IPO-date column the scanner fills: `ipo_date` answered null for "
        "all 5,720 US rows (and 96,278 global rows) when probed live, so a filter "
        "on it can only return an empty screen",
        ("ipodate",),
    ),
    (
        "needs an optionable/shortable flag: the scan carries no such column",
        ("sh_opt",),
    ),
    (
        "needs a trade-count column: the scan carries no trades counter",
        ("sh_trades",),
    ),
    (
        "needs cash per share: the scan carries no cash metric, so Price/Cash "
        "cannot be formed without inventing a denominator",
        ("fa_pc",),
    ),
    (
        "needs enterprise value and sales as separate columns: only "
        "EV/EBITDA is copied, so EV/Sales cannot be formed",
        ("fa_evsales",),
    ),
    (
        "needs a growth-rate column (EPS and sales growth this year, next year, "
        "quarterly, 3-5 year and TTM): the scan carries forward EPS and revenue "
        "*levels* for the next FY/FQ, and a level is not a growth rate",
        (
            "fa_epsyoy",
            "fa_epsyoy1",
            "fa_epsqoq",
            "fa_epsyoyttm",
            "fa_eps3years",
            "fa_eps5years",
            "fa_estltgrowth",
            "fa_salesqoq",
            "fa_salesyoyttm",
            "fa_sales3years",
            "fa_sales5years",
        ),
    ),
    (
        "needs an earnings-surprise column: the scan carries no surprise history",
        ("fa_epsrev",),
    ),
    (
        "needs long-term debt separately: the scan carries total debt/equity only",
        ("fa_ltdebteq",),
    ),
    (
        "needs ownership and insider/institutional transaction flows: the scan "
        "carries no ownership column",
        ("sh_insiderown", "sh_insidertrans", "sh_instown", "sh_insttrans"),
    ),
    (
        "spans ten performance windows (day, week, month, quarter, half-year, "
        "YTD, year, 3Y, 5Y, 10Y), while the row carries one column per window "
        "(`ret_1w` … `ret_10y`); a single key is not a single field — send the "
        "column instead",
        ("ta_perf", "ta_perf2"),
    ),
    (
        "spans two windows (weekly and monthly), while the row carries "
        "`volatility_w` and `volatility_m` separately — send the column instead",
        ("ta_volatility",),
    ),
    (
        "needs a change-from-open column: the scan carries a gap (open against "
        "the previous close) but no open price, so the change from the open "
        "cannot be computed",
        ("ta_changeopen",),
    ),
    (
        "needs 20-day and 50-day high/low columns: the scan carries the "
        "52-week extremes only",
        ("ta_highlow20d", "ta_highlow50d"),
    ),
    (
        "is a percent distance from the 52-week high/low, while the row carries "
        "the absolute `high_52w`/`low_52w` prices; the two units are not "
        "interchangeable — filter `high_52w`/`low_52w` directly",
        ("ta_highlow52w",),
    ),
    (
        "needs an all-time high/low column: the scan carries the 52-week "
        "extremes only",
        ("ta_alltime",),
    ),
    (
        "needs chart-pattern detection: the scan returns no pattern "
        "classification",
        ("ta_pattern",),
    ),
    (
        "needs candlestick detection: the scan returns no candlestick "
        "classification",
        ("ta_candlestick",),
    ),
    (
        "needs after-hours prices: the scan covers the regular session only",
        ("ah_close", "ah_change"),
    ),
    (
        "needs the news store: the scan returns no news timestamp or keywords",
        ("news_date", "news_keywords"),
    ),
    (
        "needs ETF holdings data: the scan does not carry a fund's constituent "
        "list",
        ("etf_heldby",),
    ),
)

#: Key → reason, exactly for the keys missing from :data:`ROW_FIELDS`.
UNAVAILABLE: Mapping[str, str] = MappingProxyType(
    {key: reason for reason, keys in _GROUPS for key in keys}
)


class FilterKeyError(ValueError):
    """A filter key that cannot be applied, carrying a stable machine code."""

    code = "invalid_filter_key"

    def __init__(self, key: object, message: str, *, label: str | None = None) -> None:
        super().__init__(message)
        self.key = key
        self.label = label


class UnknownFilterKey(FilterKeyError):
    """The key is not one of the 82 Finviz keys (a typo or an invention)."""

    code = "unknown_filter_key"


class UnavailableFilterKey(FilterKeyError):
    """The key exists in Finviz, but no screen row column can filter on it."""

    code = "unavailable_filter_key"


@dataclass(frozen=True, slots=True)
class Binding:
    """One catalogue key and where it points.

    ``field``/``kind`` are ``None`` for the keys no row can answer; ``reason`` is
    then the sentence a 422 repeats. A binding never carries both a field and a
    reason — that would be a key that is somehow available and unavailable at
    once.
    """

    key: str
    label: str
    tab: str
    field: str | None
    kind: str | None
    reason: str | None

    @property
    def is_available(self) -> bool:
        return self.field is not None

    def as_dict(self) -> dict[str, object]:
        """The published form: what a client reads in the schema.

        ``tab`` and ``kind`` are published because a UI has to group the filters
        the way Finviz does and pick the right input for the value; ``reason``
        appears only for the keys that are refused, exactly when the client needs
        to know why.
        """

        entry: dict[str, object] = {
            "label": self.label,
            "tab": self.tab,
            "field": self.field,
        }
        if self.kind is not None:
            entry["kind"] = self.kind
        if self.reason is not None:
            entry["reason"] = self.reason
        return entry


def _make(spec_key: str, field: str | None) -> Binding:
    spec = FILTER_CATALOG[spec_key]
    return Binding(
        key=spec.key,
        label=spec.label,
        tab=spec.tab,
        field=field,
        kind=FIELDS[field] if field is not None else None,
        reason=UNAVAILABLE.get(spec_key),
    )


#: Panel order, exactly ``FILTER_KEYS``: the same sequence the Finviz probe
#: returned, so a UI that renders the catalogue reads one list.
CATALOGUE: tuple[Binding, ...] = tuple(
    _make(key, ROW_FIELDS.get(key)) for key in FILTER_CATALOG
)
BINDINGS: Mapping[str, Binding] = MappingProxyType(
    {binding_.key: binding_ for binding_ in CATALOGUE}
)


def _validate() -> None:
    """Fail at import: coverage, drift and placeholders are boot errors."""

    keys = tuple(FILTER_CATALOG)
    if len(CATALOGUE) != len(keys) or tuple(b.key for b in CATALOGUE) != keys:
        raise RuntimeError(
            "filter_fields must cover the catalogue in panel order; "
            f"got {len(CATALOGUE)} bindings for {len(keys)} keys"
        )

    mapped = {key: spec.field for key, spec in FILTER_CATALOG.items() if spec.field}
    if dict(ROW_FIELDS) != mapped:
        raise RuntimeError(
            "filter_fields.ROW_FIELDS drifted from filters.FILTER_CATALOG: "
            f"declared={sorted(ROW_FIELDS.items())}, catalogue={sorted(mapped.items())}"
        )

    covered = set(ROW_FIELDS) | set(UNAVAILABLE)
    missing = [key for key in keys if key not in covered]
    unknown = sorted(covered - set(keys))
    overlap = sorted(set(ROW_FIELDS) & set(UNAVAILABLE))
    if missing or unknown or overlap:
        raise RuntimeError(
            "every filter key needs exactly one disposition: "
            f"missing={missing}, unknown={unknown}, both={overlap}"
        )
    if len(keys) != 82:
        raise RuntimeError(f"the Finviz catalogue is 82 keys, got {len(keys)}")
    for key, field in ROW_FIELDS.items():
        if field not in FIELDS:
            raise RuntimeError(f"filter key {key!r} names non-existent row field {field!r}")
    for key, reason in UNAVAILABLE.items():
        if not reason.strip():
            raise RuntimeError(f"filter key {key!r} has an empty unavailability reason")

    # The parity table is the other half of the same claim: a field a key filters
    # must be a field a row can carry, and every parity metric must be readable.
    from app.screen import universe as parity

    unreadable = sorted(
        field for field in mapped.values() if field not in set(parity.PARITY_FIELDS.values())
        and field not in parity.UNIVERSE_COLUMNS
    )
    if unreadable:
        raise RuntimeError(
            "filter keys point at fields no row carries (neither a light column "
            f"nor a parity metric): {unreadable}"
        )


_validate()


def binding(key: object) -> Binding:
    """The binding for ``key``; unknown and unavailable keys raise, never ``None``.

    The two failures are separate types because they mean different things to a
    caller: ``UnknownFilterKey`` is a typo or a key Finviz does not have, while
    ``UnavailableFilterKey`` is a real Finviz filter whose metric no screen row
    carries yet.
    """

    if not isinstance(key, str) or not key:
        raise UnknownFilterKey(key, f"filter key must be a non-empty string, got {key!r}")

    found = BINDINGS.get(key)
    if found is None:
        raise UnknownFilterKey(
            key,
            f"unknown filter key {key!r}: it is not one of the 82 Finviz filter keys. "
            f"Keys that can filter a row today: {', '.join(ROW_FIELDS)}. "
            "The full catalogue, with the reason each other key is refused, is "
            "served at GET /screens/select/schema",
        )
    if found.field is None:
        raise UnavailableFilterKey(
            key,
            f"filter key {key!r} ({found.label}) cannot be applied: it {found.reason}. "
            "A filter that cannot change the result is refused instead of ignored, "
            "because ignoring it would return the unfiltered screen as if it matched",
            label=found.label,
        )
    return found


def field_for(key: object) -> str:
    """The row column ``key`` filters; refuses exactly like :func:`binding`."""

    field = binding(key).field
    assert field is not None  # binding() refuses the unavailable keys
    return field


def is_available(key: object) -> bool:
    """Can a filter on ``key`` run today? (Unknown keys are not available.)"""

    found = BINDINGS.get(key) if isinstance(key, str) else None
    return found is not None and found.is_available


def reason_for(key: str) -> str | None:
    """The refusal sentence for ``key``, or ``None`` when it maps to a column."""

    found = BINDINGS.get(key)
    return found.reason if found is not None else None


def mapped_keys() -> tuple[str, ...]:
    """The keys that filter a row today, in panel order."""

    return tuple(b.key for b in CATALOGUE if b.is_available)


def unavailable_keys() -> tuple[str, ...]:
    """The keys that are refused, in panel order."""

    return tuple(b.key for b in CATALOGUE if not b.is_available)


__all__ = [
    "BINDINGS",
    "CATALOGUE",
    "FilterKeyError",
    "ROW_FIELDS",
    "UNAVAILABLE",
    "UnavailableFilterKey",
    "UnknownFilterKey",
    "Binding",
    "binding",
    "field_for",
    "is_available",
    "mapped_keys",
    "reason_for",
    "unavailable_keys",
]
