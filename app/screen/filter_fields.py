"""Every one of the 82 Finviz filter keys: a row field, or a refusal by name.

:mod:`app.screen.filters` answers "does Ztockz accept the same filter keys as
Finviz" and leaves ``FilterSpec.field`` as ``None`` for the keys the light
universe does not carry. This module answers the next question, and it is the
one a selection endpoint cannot dodge: **what happens when a caller sends a key
that has no column?**

The answer is a refusal that names the key, the Finviz label and the metric that
is missing. Silence is not an option. A selection that accepts ``fa_pe`` and
drops it returns the unfiltered screen, and the caller reads that as "every
stock has a P/E below 10" — a fabricated answer produced by an ignored
parameter. So the contract is: **a filter key either maps to a row column or the
request is rejected (422) with the reason**, never applied as a no-op.

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

The mapping is not a second source of truth: :func:`_validate` runs at import
and fails the process if :data:`ROW_FIELDS` drifts from
``FILTER_CATALOG[*].field`` or if any of the 82 keys is left uncovered.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from app.screen.filters import FIELDS, FILTER_CATALOG

#: Finviz key → canonical row column, for the keys the light universe carries.
#: Kept identical to ``FILTER_CATALOG[*].field`` by :func:`_validate`.
ROW_FIELDS: Mapping[str, str] = MappingProxyType(
    {
        "exch": "exchange",
        "sec": "sector",
        "geo": "country",
        "cap": "market_cap_usd",
        "sh_curvol": "volume",
        "sh_price": "price",
        "ta_change": "change_pct",
    }
)

#: Why a key cannot filter yet. One sentence per group of keys: the metric the
#: light row is missing, so a 422 can say *what* is absent instead of "invalid".
_NEEDED = (
    "the light screen row has no such column yet (it arrives with the "
    "quote-depth metrics, app/market/fields.py)"
)

_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("index membership (S&P 500 / Nasdaq-100 / Dow / Russell)", ("idx",)),
    ("an industry column (the light row carries only 'sector')", ("industry",)),
    ("theme and sub-theme classification", ("theme", "subtheme")),
    (
        "dividend metrics (yield, growth, payout ratio)",
        ("fa_div", "fa_divgrowth", "fa_payoutratio"),
    ),
    (
        "share structure, short interest and trading depth (float, shares "
        "outstanding, short float, option/short flags, average and relative "
        "volume, trades)",
        ("sh_short", "sh_opt", "sh_float", "sh_outstanding", "sh_avgvol", "sh_relvol", "sh_trades"),
    ),
    (
        "analyst and event data (rating, target price, surprise, earnings "
        "date, IPO date)",
        ("an_recom", "targetprice", "fa_epsrev", "earningsdate", "ipodate"),
    ),
    (
        "valuation multiples (P/E, forward P/E, PEG, P/S, P/B, P/C, P/FCF, "
        "EV/EBITDA, EV/Sales)",
        ("fa_pe", "fa_fpe", "fa_peg", "fa_ps", "fa_pb", "fa_pc", "fa_pfcf", "fa_evebitda", "fa_evsales"),
    ),
    (
        "growth rates (EPS and sales: this year, next year, quarterly, 3-5 "
        "year, TTM)",
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
        "profitability and leverage ratios (ROA, ROE, ROIC, current and quick "
        "ratio, debt/equity, margins)",
        (
            "fa_roa",
            "fa_roe",
            "fa_roi",
            "fa_curratio",
            "fa_quickratio",
            "fa_ltdebteq",
            "fa_debteq",
            "fa_grossmargin",
            "fa_opermargin",
            "fa_netmargin",
        ),
    ),
    (
        "ownership percentages and insider/institutional transaction flows",
        ("sh_insiderown", "sh_insidertrans", "sh_instown", "sh_insttrans"),
    ),
    (
        "technical indicators (performance windows, volatility, RSI, gap, "
        "moving averages, high/low ranges, patterns, candlesticks, beta, ATR)",
        (
            "ta_perf",
            "ta_perf2",
            "ta_volatility",
            "ta_rsi",
            "ta_gap",
            "ta_sma20",
            "ta_sma50",
            "ta_sma200",
            "ta_changeopen",
            "ta_highlow20d",
            "ta_highlow50d",
            "ta_highlow52w",
            "ta_alltime",
            "ta_pattern",
            "ta_candlestick",
            "ta_beta",
            "ta_averagetruerange",
        ),
    ),
    ("after-hours prices (close and change)", ("ah_close", "ah_change")),
    (
        "the news store (latest-news window and keyword search)",
        ("news_date", "news_keywords"),
    ),
    ("ETF holdings", ("etf_heldby",)),
)

#: Key → reason, exactly for the keys missing from :data:`ROW_FIELDS`.
UNAVAILABLE: Mapping[str, str] = MappingProxyType(
    {
        key: f"needs {need} — {_NEEDED}"
        for need, keys in _GROUPS
        for key in keys
    }
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
    """The key exists in Finviz, but no light row column can filter on it."""

    code = "unavailable_filter_key"


@dataclass(frozen=True, slots=True)
class Binding:
    """One catalogue key and where it points.

    ``field``/``kind`` are ``None`` for the keys the light row cannot answer;
    ``reason`` is then the sentence a 422 repeats. A binding never carries both
    a field and a reason — that would be a key that is somehow available and
    unavailable at once.
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


_validate()


def binding(key: object) -> Binding:
    """The binding for ``key``; unknown and unavailable keys raise, never ``None``.

    The two failures are separate types because they mean different things to a
    caller: ``UnknownFilterKey`` is a typo or a key Finviz does not have, while
    ``UnavailableFilterKey`` is a real Finviz filter whose metric the light
    universe does not carry yet.
    """

    if not isinstance(key, str) or not key:
        raise UnknownFilterKey(key, f"filter key must be a non-empty string, got {key!r}")

    found = BINDINGS.get(key)
    if found is None:
        raise UnknownFilterKey(
            key,
            f"unknown filter key {key!r}: it is not one of the 82 Finviz filter keys. "
            f"Keys that can filter a light row today: {', '.join(ROW_FIELDS)}. "
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
    """The keys that filter a light row today, in panel order."""

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
