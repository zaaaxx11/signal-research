"""Finviz parity — 71 quote metrics, their units, and the cells they may fill.

PLAN-finviz-parity.md §1 measures Finviz's quote page at **71 metrics** and six
view tabs; §6 gate 2 turns that measurement into a contract: every metric is
available for a US symbol, and a missing value is published as ``null`` with a
reason — never as ``0``. This module is the vocabulary of that contract:

* :data:`METRICS` — the 71 cells in the order Finviz prints them, each with a
  unique snake_case ``key``, the **unit it is printed in**, the canonical column
  it may fill (or ``None``) and, where a lookalike exists, the reason the two
  must not be confused.
* :func:`parse_cell` — one printed cell → one :class:`ParsedCell`. ``B``/``M``/
  ``K`` scale, ``%`` keeps its magnitude (``18.43%`` is ``18.43`` percentage
  points, not ``0.1843``), ``$`` is stripped, ``N/A`` and friends are ``None``.
  A value that does not fit its declared unit is ``None`` with
  ``reason="unparsed"``: a parser that guesses is a parser that fabricates.
* :func:`parse_snapshot` — a whole quote page (the fixture's
  ``[{"name": ..., "raw": ...}]`` shape) → ``{key: ParsedCell}``, with the one
  duplicated label (``EPS next Y`` is printed twice: a currency estimate and a
  growth rate) disambiguated by occurrence, never overwritten.

Two rules matter more than the parsing itself:

**A unit is not a decoration.** ``SMA20`` is the *distance* from the 20-day
average in percent, so it must not fill ``ma_20``; ``Avg Volume`` has no
declared averaging window, so it must not fill ``avg_volume_20d``; ``52W High``
is not ``day_high``. Those cells carry ``canonical=None`` and a note, because a
wrong column is worse than an empty one (gate 5, "no fabrication").

**Absence is data.** A sentinel becomes ``None``; ``0`` only ever comes from a
real zero. A pair cell with one half missing keeps the half it has and is
marked ``partial`` rather than being thrown away.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Iterable, Mapping, Sequence

from app.contracts import CANONICAL_COLUMNS_V1
from app.verify.units import SENTINELS, parse_number

#: The one sentinel vocabulary of the engine lives in ``app.verify.units``; a
#: second copy here would drift, and ``"N/A"`` becoming a number is exactly the
#: bug class 04 §1 D4 describes.


class Unit(str, Enum):
    """How a Finviz cell is printed — which is how it has to be read."""

    USD = "usd"
    PERCENT = "pct"
    MULTIPLE = "multiple"
    SHARES = "shares"
    COUNT = "count"
    DATE = "date"
    TEXT = "text"

    @property
    def observation_unit(self) -> str:
        """The ``Observation.unit`` member that corresponds to this display unit."""

        return _OBSERVATION_UNITS[self.value]


#: Display unit → the closed unit vocabulary of ``app.contracts.Observation``.
_OBSERVATION_UNITS: dict[str, str] = {
    "usd": "price:USD",
    "pct": "pct",
    "multiple": "ratio",
    "shares": "shares",
    "count": "count",
    "date": "date",
    "text": "text",
}


class MissingReason(str, Enum):
    """Why a cell carries no value — the reason half of "null + reason"."""

    #: The provider printed nothing, or printed a sentinel (``N/A``, ``-``).
    ABSENT = "absent"
    #: The provider printed something that is not the declared shape or unit.
    UNPARSED = "unparsed"
    #: A multi-part cell where at least one part is missing.
    PARTIAL = "partial"


ScalarValue = float | date | str | None
CellValue = ScalarValue | tuple[ScalarValue, ...]

#: Finviz's magnitude suffixes. ``5075.67B`` is a market cap in dollars, not a
#: string, and ``44.06M`` is 44 million shares.
SCALES: dict[str, float] = {"": 1.0, "K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}

_SCALED = re.compile(
    r"^(?P<body>[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)\s*(?P<suffix>[KMBT])?$",
    re.IGNORECASE,
)
_KEY = re.compile(r"^[a-z][a-z0-9_]*$")
_DATE_FORMATS: tuple[str, ...] = (
    "%b %d, %Y",   # "Dec 12, 1980" — Finviz's IPO cell
    "%B %d, %Y",
    "%b %d %Y",
    "%Y-%m-%d",
    "%Y/%m/%d",
)

_CANONICAL: frozenset[str] = frozenset(CANONICAL_COLUMNS_V1)


@dataclass(frozen=True, slots=True)
class QuoteMetric:
    """One cell of Finviz's quote page.

    ``units`` has one entry per printed part: most cells are one number, but
    ``52W High`` prints a price *and* a distance in percent, and ``Index``
    prints a comma-separated list. A metric is therefore never "a float"; it is
    "a value printed this way", which is what keeps the units honest.
    """

    key: str
    label: str
    units: tuple[Unit, ...]
    canonical: str | None = None
    occurrence: int = 0
    separator: str = " "
    note: str = ""

    @property
    def unit(self) -> Unit:
        return self.units[0]

    @property
    def parts(self) -> int:
        return len(self.units)

    @property
    def is_multi(self) -> bool:
        return len(self.units) > 1


@dataclass(frozen=True, slots=True)
class ParsedCell:
    """One printed cell, read. ``raw`` is kept verbatim for provenance."""

    key: str
    value: CellValue = None
    reason: MissingReason | None = None
    raw: str | None = None

    @property
    def present(self) -> bool:
        """True when a value exists — even a partial or unparsed one."""

        return self.value is not None

    @property
    def filled(self) -> bool:
        """True only for a value with no caveat; a partial cell is not filled."""

        return self.value is not None and self.reason is None


def _read_date(text: str) -> date | None:
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        return None


def _read_scaled(text: str) -> float | None:
    """``"5075.67B"`` → ``5075670000000.0``; a bare number scales by one."""

    match = _SCALED.fullmatch(text)
    if match is None:
        return None
    body = parse_number(match.group("body"), unit="number")
    if body is None:  # pragma: no cover - the regex already proved it numeric
        return None
    return body * SCALES[(match.group("suffix") or "").upper()]


def parse_scalar(raw: object, unit: Unit) -> ScalarValue:
    """Read one printed cell as ``unit`` says it is printed."""

    if raw is None or isinstance(raw, bool):
        return None
    # ``datetime`` is a ``date`` subclass, so it is answered first.
    if isinstance(raw, datetime):
        return raw.date() if unit is Unit.DATE else None
    if isinstance(raw, date):
        return raw if unit is Unit.DATE else None
    if isinstance(raw, (int, float)):
        if unit in (Unit.DATE, Unit.TEXT):
            return None
        value = float(raw)
        return value if math.isfinite(value) else None
    if not isinstance(raw, str):
        return None

    text = raw.strip()
    if not text or text.casefold() in SENTINELS:
        return None
    if unit is Unit.DATE:
        return _read_date(text)
    if unit is Unit.TEXT:
        return text
    if unit is Unit.PERCENT:
        # ``parse_number`` strips a trailing ``%`` without rescaling: the number
        # keeps its magnitude and the unit carries the meaning.
        return parse_number(text, unit="percent")

    # From here on the cell is a currency amount, a share count or a multiple.
    # A trailing percent sign is a different unit, not a suffix to ignore:
    # storing "39.87%" as P/E 39.87 would mix two definitions.
    if text.endswith("%"):
        return None
    if unit is Unit.USD:
        text = text.removeprefix("$").strip()
        if not text:
            return None
    if unit in (Unit.USD, Unit.SHARES, Unit.COUNT):
        return _read_scaled(text)
    return parse_number(text, unit="number")


def _split(raw: object, separator: str) -> list[str] | None:
    """Split a multi-part cell; ``None`` when the whole cell is a sentinel."""

    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)):
        parts = [str(part).strip() for part in raw]
        return parts or None
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text or text.casefold() in SENTINELS:
        return None
    pieces = text.split() if separator == " " else text.split(separator)
    parts = [piece.strip() for piece in pieces]
    parts = [part for part in parts if part]
    return parts or None


def _reason(raw: object) -> MissingReason:
    if raw is None:
        return MissingReason.ABSENT
    if isinstance(raw, str) and (
        not raw.strip() or raw.strip().casefold() in SENTINELS
    ):
        return MissingReason.ABSENT
    return MissingReason.UNPARSED


def parse_cell(key: str | QuoteMetric, raw: object) -> ParsedCell:
    """Read one printed cell for one metric.

    A sentinel is ``None``/``absent``; a value that cannot be read as the
    declared unit is ``None``/``unparsed``; a pair with one half missing keeps
    the half it has and is ``partial``.
    """

    metric = metric_for(key) if isinstance(key, str) else key
    printed = None if raw is None else (raw if isinstance(raw, str) else str(raw))

    if not metric.is_multi:
        value = parse_scalar(raw, metric.unit)
        reason = None if value is not None else _reason(raw)
        return ParsedCell(metric.key, value, reason, printed)

    parts = _split(raw, metric.separator)
    if parts is None:
        return ParsedCell(metric.key, None, MissingReason.ABSENT, printed)
    if len(parts) > metric.parts:
        # More parts than the cell declares is provider drift, not a value.
        return ParsedCell(metric.key, None, MissingReason.UNPARSED, printed)

    values: list[ScalarValue] = [
        parse_scalar(part, unit) for part, unit in zip(parts, metric.units)
    ]
    values.extend([None] * (metric.parts - len(values)))
    if all(value is None for value in values):
        return ParsedCell(metric.key, None, MissingReason.ABSENT, printed)
    reason = None if all(value is not None for value in values) else MissingReason.PARTIAL
    return ParsedCell(metric.key, tuple(values), reason, printed)


def _row_pair(row: Mapping[str, object] | Sequence[object]) -> tuple[str, object]:
    if isinstance(row, Mapping):
        label = row.get("name", row.get("label"))
        if label is None:
            raise ValueError("a snapshot row needs a 'name' (the printed label)")
        return str(label).strip(), row.get("raw")
    if isinstance(row, Sequence) and not isinstance(row, (str, bytes, bytearray)):
        if len(row) != 2:
            raise ValueError("a snapshot row is a (label, raw) pair")
        return str(row[0]).strip(), row[1]
    raise TypeError("a snapshot row is a mapping or a (label, raw) pair")


def parse_snapshot(
    rows: Iterable[Mapping[str, object] | Sequence[object]], *, strict: bool = True
) -> dict[str, ParsedCell]:
    """A whole quote page → ``{metric key: ParsedCell}`` in printed order.

    ``strict`` (the default) refuses a label the table does not know: Finviz
    adding a cell is schema drift worth seeing, not a row to drop silently. A
    label printed more often than the model declares is always an error — the
    second ``EPS next Y`` has its own metric, a third would have nothing to
    fill.
    """

    occurrences: dict[str, int] = {}
    cells: dict[str, ParsedCell] = {}
    for row in rows:
        label, raw = _row_pair(row)
        occurrence = occurrences.get(label, 0)
        occurrences[label] = occurrence + 1

        metrics = METRICS_BY_LABEL.get(label)
        if metrics is None:
            if strict:
                raise ValueError(
                    f"unknown Finviz label {label!r}; the table declares "
                    f"{len(METRICS)} quote metrics and this label is not one of them"
                )
            continue
        if occurrence >= len(metrics):
            raise ValueError(
                f"{label!r} appears {occurrence + 1} times but the table declares "
                f"{len(metrics)} metric(s) for that label"
            )
        metric = metrics[occurrence]
        cells[metric.key] = parse_cell(metric, raw)
    return cells


def metric_for(key: str) -> QuoteMetric:
    """The metric named ``key``; an unknown key is a caller error."""

    try:
        return METRIC_BY_KEY[key]
    except KeyError:
        raise ValueError(
            f"unknown quote metric {key!r}; {len(METRICS)} metrics are declared "
            "in app.market.fields"
        ) from None


def canonical_for(key: str) -> str | None:
    """The canonical column ``key`` may fill, or ``None`` when it must not."""

    return metric_for(key).canonical


# ---------------------------------------------------------------------------
# The 71 metrics, in the order Finviz prints them
# ---------------------------------------------------------------------------


def _metric(
    key: str,
    label: str,
    unit: Unit | tuple[Unit, ...],
    *,
    canonical: str | None = None,
    occurrence: int = 0,
    separator: str = " ",
    note: str = "",
) -> QuoteMetric:
    units = (unit,) if isinstance(unit, Unit) else unit
    return QuoteMetric(
        key=key,
        label=label,
        units=units,
        canonical=canonical,
        occurrence=occurrence,
        separator=separator,
        note=note,
    )


METRICS: tuple[QuoteMetric, ...] = (
    _metric(
        "index",
        "Index",
        (Unit.TEXT, Unit.TEXT, Unit.TEXT),
        canonical="index_memberships",
        separator=",",
        note=(
            "Finviz prints membership only, never weights; join the parts with "
            "', ' to fill the canonical text column."
        ),
    ),
    _metric(
        "market_cap",
        "Market Cap",
        Unit.USD,
        canonical="market_cap",
        note="Finviz does not print the currency; filling market_cap_usd would assume USD.",
    ),
    _metric("enterprise_value", "Enterprise Value", Unit.USD),
    _metric(
        "income",
        "Income",
        Unit.USD,
        canonical="net_income_ttm",
        note="Finviz labels net income (ttm) simply 'Income'.",
    ),
    _metric(
        "sales",
        "Sales",
        Unit.USD,
        canonical="revenue_ttm",
        note="Finviz 'Sales' is revenue (ttm).",
    ),
    _metric("book_per_share", "Book/sh", Unit.USD),
    _metric("cash_per_share", "Cash/sh", Unit.USD),
    _metric("employees", "Employees", Unit.COUNT),
    _metric("ipo", "IPO", Unit.DATE, canonical="listing_date"),
    _metric("pe", "P/E", Unit.MULTIPLE, canonical="pe_ttm"),
    _metric("forward_pe", "Forward P/E", Unit.MULTIPLE, canonical="pe_forward"),
    _metric("peg", "PEG", Unit.MULTIPLE, canonical="peg"),
    _metric("ps", "P/S", Unit.MULTIPLE, canonical="ps"),
    _metric("pb", "P/B", Unit.MULTIPLE, canonical="pb"),
    _metric("pc", "P/C", Unit.MULTIPLE),
    _metric("pfcf", "P/FCF", Unit.MULTIPLE),
    _metric("ev_ebitda", "EV/EBITDA", Unit.MULTIPLE, canonical="ev_ebitda"),
    _metric("ev_sales", "EV/Sales", Unit.MULTIPLE),
    _metric("quick_ratio", "Quick Ratio", Unit.MULTIPLE),
    _metric("current_ratio", "Current Ratio", Unit.MULTIPLE),
    _metric("debt_to_equity", "Debt/Eq", Unit.MULTIPLE, canonical="debt_to_equity"),
    _metric(
        "lt_debt_to_equity",
        "LT Debt/Eq",
        Unit.MULTIPLE,
        note="Long-term debt/equity is not the canonical debt_to_equity.",
    ),
    _metric(
        "option_short",
        "Option/Short",
        (Unit.TEXT, Unit.TEXT),
        separator="/",
        note="Availability flags, not a position.",
    ),
    _metric("eps_ttm", "EPS (ttm)", Unit.USD, canonical="eps_ttm"),
    _metric(
        "eps_next_y",
        "EPS next Y",
        Unit.USD,
        canonical="eps_forward",
        note="The analyst estimate in currency — the first of two cells with this label.",
    ),
    _metric("eps_next_q", "EPS next Q", Unit.USD),
    _metric("eps_this_y", "EPS this Y", Unit.PERCENT),
    _metric(
        "eps_next_y_growth",
        "EPS next Y",
        Unit.PERCENT,
        occurrence=1,
        note="Same printed label as eps_next_y, a growth rate instead of an estimate.",
    ),
    _metric("eps_next_5y", "EPS next 5Y", Unit.PERCENT),
    _metric(
        "eps_past_3_5y",
        "EPS past 3/5Y",
        (Unit.PERCENT, Unit.PERCENT),
        note="Two windows in one cell: three-year, then five-year.",
    ),
    _metric(
        "sales_past_3_5y",
        "Sales past 3/5Y",
        (Unit.PERCENT, Unit.PERCENT),
        note="Two windows in one cell: three-year, then five-year.",
    ),
    _metric("eps_yoy_ttm", "EPS Y/Y TTM", Unit.PERCENT),
    _metric("sales_yoy_ttm", "Sales Y/Y TTM", Unit.PERCENT),
    _metric("eps_qoq", "EPS Q/Q", Unit.PERCENT),
    _metric("sales_qoq", "Sales Q/Q", Unit.PERCENT),
    _metric("insider_own", "Insider Own", Unit.PERCENT),
    _metric("insider_trans", "Insider Trans", Unit.PERCENT),
    _metric("inst_own", "Inst Own", Unit.PERCENT),
    _metric("inst_trans", "Inst Trans", Unit.PERCENT),
    _metric("roa", "ROA", Unit.PERCENT),
    _metric("roe", "ROE", Unit.PERCENT, canonical="roe"),
    _metric("roic", "ROIC", Unit.PERCENT),
    _metric("gross_margin", "Gross Margin", Unit.PERCENT),
    _metric("oper_margin", "Oper. Margin", Unit.PERCENT),
    _metric("profit_margin", "Profit Margin", Unit.PERCENT),
    _metric(
        "sma20",
        "SMA20",
        Unit.PERCENT,
        note="Distance from the 20-day average in percent, not the average itself.",
    ),
    _metric(
        "sma50",
        "SMA50",
        Unit.PERCENT,
        note="Distance from the 50-day average in percent, not the average itself.",
    ),
    _metric(
        "sma200",
        "SMA200",
        Unit.PERCENT,
        note="Distance from the 200-day average in percent, not the average itself.",
    ),
    _metric("shares_outstanding", "Shs Outstand", Unit.SHARES, canonical="shares_outstanding"),
    _metric("shares_float", "Shs Float", Unit.SHARES, canonical="float_shares"),
    _metric(
        "week52_high",
        "52W High",
        (Unit.USD, Unit.PERCENT),
        note="Price and distance to the high; neither is day_high.",
    ),
    _metric(
        "week52_low",
        "52W Low",
        (Unit.USD, Unit.PERCENT),
        note="Price and distance to the low; neither is day_low.",
    ),
    _metric(
        "volatility",
        "Volatility",
        (Unit.PERCENT, Unit.PERCENT),
        note="Week and month volatility; not volatility_20d.",
    ),
    _metric("atr_14", "ATR (14)", Unit.USD, canonical="atr_14"),
    _metric("rsi_14", "RSI (14)", Unit.MULTIPLE, canonical="rsi_14"),
    _metric("beta", "Beta", Unit.MULTIPLE),
    _metric("rel_volume", "Rel Volume", Unit.MULTIPLE),
    _metric(
        "avg_volume",
        "Avg Volume",
        Unit.COUNT,
        note="Finviz does not publish the averaging window; it is not avg_volume_20d.",
    ),
    _metric("volume", "Volume", Unit.COUNT, canonical="day_volume"),
    _metric("perf_week", "Perf Week", Unit.PERCENT, canonical="ret_1w"),
    _metric("perf_month", "Perf Month", Unit.PERCENT, canonical="ret_1m"),
    _metric("perf_quarter", "Perf Quarter", Unit.PERCENT, canonical="ret_3m"),
    _metric("perf_half_y", "Perf Half Y", Unit.PERCENT, canonical="ret_6m"),
    _metric("perf_ytd", "Perf YTD", Unit.PERCENT, canonical="ret_ytd"),
    _metric("perf_year", "Perf Year", Unit.PERCENT, canonical="ret_1y"),
    _metric("perf_3y", "Perf 3Y", Unit.PERCENT, canonical="ret_3y"),
    _metric("perf_5y", "Perf 5Y", Unit.PERCENT, canonical="ret_5y"),
    _metric(
        "perf_10y",
        "Perf 10Y",
        Unit.PERCENT,
        note="The canonical table stops at ret_5y.",
    ),
    _metric("prev_close", "Prev Close", Unit.USD, canonical="prev_close"),
    _metric("price", "Price", Unit.USD, canonical="last_price"),
    _metric("change_pct", "Change %", Unit.PERCENT, canonical="change_pct"),
)


def _validate(metrics: tuple[QuoteMetric, ...]) -> None:
    """Fail at import: a broken vocabulary must not boot a service."""

    if len(metrics) != 71:
        raise RuntimeError(f"the Finviz quote table is 71 metrics, got {len(metrics)}")

    keys = [metric.key for metric in metrics]
    if len(set(keys)) != len(keys):
        raise RuntimeError("quote metric keys must be unique")

    by_label: dict[str, list[QuoteMetric]] = {}
    for metric in metrics:
        if not _KEY.fullmatch(metric.key):
            raise RuntimeError(f"metric key {metric.key!r} is not snake_case")
        if not metric.units:
            raise RuntimeError(f"{metric.key!r} declares no unit")
        if metric.is_multi and not metric.separator:
            raise RuntimeError(f"{metric.key!r} is multi-part without a separator")
        if metric.canonical is not None and metric.canonical not in _CANONICAL:
            raise RuntimeError(
                f"{metric.key!r} names canonical column {metric.canonical!r}, "
                "which is not one of the 92 columns"
            )
        by_label.setdefault(metric.label, []).append(metric)

    for label, group in by_label.items():
        if sorted(metric.occurrence for metric in group) != list(range(len(group))):
            raise RuntimeError(
                f"occurrences for {label!r} must be 0..n-1, got "
                f"{sorted(metric.occurrence for metric in group)}"
            )


_validate(METRICS)

#: ``key → metric``.
METRIC_BY_KEY: dict[str, QuoteMetric] = {metric.key: metric for metric in METRICS}

def _group_by_label(
    metrics: tuple[QuoteMetric, ...],
) -> dict[str, tuple[QuoteMetric, ...]]:
    """Group the table by printed label, keeping print order inside a group."""

    grouped: dict[str, list[QuoteMetric]] = {}
    for metric in metrics:
        grouped.setdefault(metric.label, []).append(metric)
    return {label: tuple(group) for label, group in grouped.items()}


#: ``label → metrics``, in printed order. One label has two metrics
#: (``EPS next Y``); everything else has one.
METRICS_BY_LABEL: dict[str, tuple[QuoteMetric, ...]] = _group_by_label(METRICS)

__all__ = [
    "METRICS",
    "METRICS_BY_LABEL",
    "METRIC_BY_KEY",
    "SCALES",
    "SENTINELS",
    "CellValue",
    "MissingReason",
    "ParsedCell",
    "QuoteMetric",
    "ScalarValue",
    "Unit",
    "canonical_for",
    "metric_for",
    "parse_cell",
    "parse_scalar",
    "parse_snapshot",
]
