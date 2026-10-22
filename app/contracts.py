"""The single vocabulary of the Market Enrichment Engine.

Architecture rule (02 §3.1): this module imports **no** other ``app.*`` module.
It is the only place where the 92-column table, the request contract, the
capability list and the observation envelope are declared.

Everything here is frozen and ``extra="forbid"``: an unknown knob is a client
bug, not a feature toggle, and a plan that a quote froze may never be mutated.
The executable spec is ``tests/test_contracts.py``; the column oracle is
``tests/spec/columns_v1.json`` (extracted mechanically from 02 §3.8.1).
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# 92 canonical columns — ordered, grouped exactly as 02 §3.8.1 declares them
# ---------------------------------------------------------------------------

#: ``(group, columns)`` in export order. Group sizes: 12+6+13+2+12+9+14+8+10+6.
CANONICAL_COLUMN_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "Identity",
        (
            "instrument_id",
            "symbol",
            "isin",
            "figi",
            "name",
            "asset_class",
            "instrument_type",
            "exchange",
            "mic",
            "currency",
            "country",
            "instrument_status",
        ),
    ),
    (
        "Classification",
        (
            "sector",
            "industry",
            "size_bucket",
            "index_memberships",
            "listing_date",
            "is_primary_listing",
        ),
    ),
    (
        "Quote",
        (
            "last_price",
            "prev_close",
            "change_abs",
            "change_pct",
            "bid",
            "ask",
            "day_open",
            "day_high",
            "day_low",
            "day_volume",
            "turnover_value",
            "quote_as_of",
            "quote_delay",
        ),
    ),
    ("Market/session", ("market_state", "session_date")),
    (
        "EOD & technical",
        (
            "bars_count",
            "first_bar_date",
            "last_bar_date",
            "ma_20",
            "ma_50",
            "ma_200",
            "ema_12",
            "ema_26",
            "rsi_14",
            "atr_14",
            "volatility_20d",
            "avg_volume_20d",
        ),
    ),
    (
        "Performance",
        (
            "ret_1d",
            "ret_1w",
            "ret_1m",
            "ret_3m",
            "ret_6m",
            "ret_ytd",
            "ret_1y",
            "ret_3y",
            "ret_5y",
        ),
    ),
    (
        "Fundamentals",
        (
            "market_cap",
            "market_cap_usd",
            "shares_outstanding",
            "float_shares",
            "pe_ttm",
            "pe_forward",
            "peg",
            "pb",
            "ps",
            "ev_ebitda",
            "eps_ttm",
            "eps_forward",
            "dividend_yield",
            "payout_ratio",
        ),
    ),
    (
        "Statements",
        (
            "revenue_ttm",
            "ebitda_ttm",
            "net_income_ttm",
            "fcf_ttm",
            "total_debt",
            "cash_and_equivalents",
            "roe",
            "debt_to_equity",
        ),
    ),
    (
        "Options",
        (
            "opt_expiry",
            "opt_dte",
            "opt_strike_atm",
            "opt_iv_atm",
            "opt_delta_atm",
            "opt_gamma_atm",
            "opt_theta_atm",
            "opt_vega_atm",
            "opt_open_interest_atm",
            "opt_volume_atm",
        ),
    ),
    (
        "Quality",
        (
            "data_quality",
            "filled_columns_count",
            "verified_columns_count",
            "primary_source",
            "as_of_achieved",
            "data_freshness_s",
        ),
    ),
)

#: Ordered, frozen v1 export contract — 92 names, 10 groups (02 §3.8.1).
CANONICAL_COLUMNS_V1: tuple[str, ...] = tuple(
    column for _group, columns in CANONICAL_COLUMN_GROUPS for column in columns
)

if len(CANONICAL_COLUMNS_V1) != 92 or len(set(CANONICAL_COLUMNS_V1)) != 92:
    raise RuntimeError(
        "CANONICAL_COLUMNS_V1 must be exactly 92 unique names, got "
        f"{len(CANONICAL_COLUMNS_V1)} entries / {len(set(CANONICAL_COLUMNS_V1))} unique"
    )

_COLUMN_SET: frozenset[str] = frozenset(CANONICAL_COLUMNS_V1)
_GROUP_OF: dict[str, str] = {
    column: group for group, columns in CANONICAL_COLUMN_GROUPS for column in columns
}

#: Rows missing any of these are rejected before export (02 §3.8.1): a row
#: without identity is noise that misleads the reader of a table.
CORE_COLUMNS: frozenset[str] = frozenset(
    {"instrument_id", "symbol", "mic", "currency", "as_of_achieved"}
)

#: Allowed values of the ``data_quality`` column (enum, never free text).
DATA_QUALITY_VALUES: tuple[str, ...] = ("identity_only", "quote_only", "partial", "complete")

#: Columns computed by the quality phase, never supplied by a provider.
DERIVED_QUALITY_COLUMNS: tuple[str, ...] = (
    "data_quality",
    "filled_columns_count",
    "verified_columns_count",
)


def columns_in_group(group: str) -> tuple[str, ...]:
    """Return the columns of one canonical group (raises for unknown groups)."""

    for name, columns in CANONICAL_COLUMN_GROUPS:
        if name == group:
            return columns
    raise KeyError(f"unknown canonical column group: {group!r}")


def group_of(column: str) -> str:
    """Return the canonical group a column belongs to."""

    return _GROUP_OF[column]


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class Capability(str, Enum):
    """Provider capability (02 §3.4). Eight members, no free-form strings."""

    REFERENCE = "reference"
    QUOTE = "quote"
    EOD_BARS = "eod_bars"
    FUNDAMENTALS = "fundamentals"
    OPTIONS = "options"
    CONSTITUENTS = "constituents"
    CORPORATE_ACTIONS = "corporate_actions"
    FX = "fx"


class MarketKind(str, Enum):
    """How the caller named the universe (02 §3.3).

    ``text`` needs the resolver (Phase 1); the other four are explicit.
    """

    TEXT = "text"
    INDEX = "index"
    EXCHANGE = "exchange"
    SCREEN = "screen"
    WATCHLIST = "watchlist"


class VerdictKind(str, Enum):
    """Content-based provider verdicts (02 §3.4, "Tabel verdict market")."""

    OK = "ok"
    EMPTY = "empty"
    NOT_FOUND = "not_found"
    SCHEMA_DRIFT = "schema_drift"
    STALE = "stale"
    UNIT_ANOMALY = "unit_anomaly"
    THROTTLED = "throttled"
    AUTHWALL = "authwall"
    BLOCKED = "blocked"
    MISMATCH = "mismatch"


OutputFormat = Literal["csv", "json", "xlsx", "parquet"]
PlanMode = Literal["balanced", "fast", "cheap"]

#: ``Observation.unit`` — a unit is a closed vocabulary, not a free string.
#: Prices are ``price:<ISO-4217>``; everything else is a scalar class.
_UNIT_PATTERN = re.compile(
    r"^(price:[A-Z]{3}|pct|ratio|shares|count|date|timestamp|enum|text|bytes)$"
)


def _check_canonical_columns(values: list[str] | None) -> list[str] | None:
    """Reject any field name that is not one of the 92 canonical columns."""

    if values is None:
        return None
    unknown = [value for value in values if value not in _COLUMN_SET]
    if unknown:
        raise ValueError(
            "unknown canonical column(s): "
            + ", ".join(sorted(unknown))
            + "; the contract is v1 (92 columns)"
        )
    return values


class _Frozen(BaseModel):
    """Base for every contract: immutable and closed to unknown knobs."""

    model_config = ConfigDict(extra="forbid", frozen=True)


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------


class MarketSpec(_Frozen):
    """The universe the caller asked for (02 §3.2/§3.3)."""

    kind: MarketKind
    symbols: list[str] | None = Field(default=None, max_length=5000)
    index: str | None = None
    venue: str | None = None
    text: str | None = None
    sector: str | None = None
    countries: list[str] | None = None
    limit: int | None = Field(default=None, ge=1, le=5000)

    @field_validator("symbols")
    @classmethod
    def _symbols_are_not_blank(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        cleaned = [symbol.strip() for symbol in value]
        if any(not symbol for symbol in cleaned):
            raise ValueError("symbols may not contain blank entries")
        return cleaned

    @model_validator(mode="after")
    def _kind_requirements(self) -> MarketSpec:
        # A kind without its anchor is not a request, it is a guess.
        if self.kind is MarketKind.WATCHLIST and not self.symbols:
            raise ValueError("market.kind='watchlist' requires a non-empty symbols[]")
        if self.kind is MarketKind.INDEX and not (self.index or self.venue):
            raise ValueError("market.kind='index' requires 'index' (or at least 'venue')")
        if self.kind is MarketKind.EXCHANGE and not self.venue:
            raise ValueError("market.kind='exchange' requires 'venue'")
        if self.kind is MarketKind.TEXT and not (self.text or "").strip():
            raise ValueError("market.kind='text' requires 'text'")
        return self


class BudgetSpec(_Frozen):
    """Per-job spending limits (02 §3.2). Exhaustion means skip, not stop."""

    requests: int = Field(default=5000, ge=0)
    credits: int = Field(default=0, ge=0)
    seconds: int = Field(default=1800, ge=1)


class MarketRequest(_Frozen):
    """Frozen client request for resolve/estimate/enrich (02 §3.2)."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    market: MarketSpec
    fields: list[str] | None = None
    enrichment: list[Capability] = Field(default_factory=lambda: [Capability.QUOTE])
    as_of: datetime
    max_staleness_s: int = Field(default=900, ge=0)
    allow_delayed: bool = True
    budget: BudgetSpec = Field(default_factory=BudgetSpec)
    format: list[OutputFormat] = Field(default_factory=lambda: ["csv"])
    retention_days: int = Field(default=7, ge=1, le=30)
    quote_id: str | None = None
    target_rows: int | None = Field(default=None, ge=1)
    mode: PlanMode = "balanced"
    #: ``async`` is a Python keyword, so the field is ``async_`` with an alias.
    async_: bool = Field(default=True, alias="async")

    @field_validator("fields")
    @classmethod
    def _fields_are_canonical(cls, value: list[str] | None) -> list[str] | None:
        return _check_canonical_columns(value)

    @field_validator("format")
    @classmethod
    def _format_is_not_empty(cls, value: list[OutputFormat]) -> list[OutputFormat]:
        if not value:
            raise ValueError("format[] may not be empty")
        return value


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------


class UniverseSlice(_Frozen):
    """What one batch will fetch: explicit symbols or a replayable filter."""

    symbols: list[str] | None = Field(default=None, max_length=100)
    venue: str | None = None
    sector: str | None = None
    offset: int | None = Field(default=None, ge=0)
    limit: int | None = Field(default=None, ge=0)


class PlanBatch(_Frozen):
    """One deterministic batch. ``symbols_expected ≤ 100`` is a hard rule."""

    batch_index: int = Field(ge=0)
    universe_slice: UniverseSlice
    symbols_expected: int = Field(ge=0, le=100)
    max_rows: int = Field(ge=0)

    @model_validator(mode="after")
    def _expected_matches_slice(self) -> PlanBatch:
        if (
            self.universe_slice.symbols is not None
            and len(self.universe_slice.symbols) != self.symbols_expected
        ):
            raise ValueError(
                "symbols_expected must equal len(universe_slice.symbols): "
                f"{self.symbols_expected} != {len(self.universe_slice.symbols)}"
            )
        return self


class MarketPlan(_Frozen):
    """Deterministic, quotable execution plan (02 §3.2/§3.3)."""

    batches: list[PlanBatch]
    mode: PlanMode = "balanced"
    target_rows: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _batch_indexes_are_contiguous(self) -> MarketPlan:
        indexes = [batch.batch_index for batch in self.batches]
        if indexes != list(range(len(indexes))):
            raise ValueError(f"batch_index must be 0..n-1 in order, got {indexes}")
        return self


# ---------------------------------------------------------------------------
# Quote (single-use, frozen plan)
# ---------------------------------------------------------------------------

_PLAN_HASH_PATTERN = r"^sha256:[0-9a-f]{64}$"


class QuoteUniverse(_Frozen):
    """The universe frozen by a quote. Symbols are stored in plan order."""

    symbols: list[str]
    mic: str | None = None
    venue: str | None = None
    sector: str | None = None
    index: str | None = None


class QuoteFreshness(_Frozen):
    as_of: datetime
    max_staleness_s: int = Field(default=900, ge=0)
    allow_delayed: bool = True


class QuotePlanV1(_Frozen):
    """The plan frozen by a single-use quote (02 §4.3).

    ``plan_hash`` is the sha256 of this document, so two jobs with the same
    plan share a hash and data drift can be told apart from code drift.
    """

    universe: QuoteUniverse
    fields: list[str]
    freshness: QuoteFreshness
    budget: BudgetSpec
    retention_days: int = Field(default=7, ge=1, le=30)
    columns_version: Literal["v1"] = "v1"
    plan_hash: str = Field(pattern=_PLAN_HASH_PATTERN)

    @field_validator("fields")
    @classmethod
    def _fields_are_canonical(cls, value: list[str]) -> list[str]:
        checked = _check_canonical_columns(value)
        if not checked:
            raise ValueError("fields[] may not be empty")
        return checked


# ---------------------------------------------------------------------------
# Observation — the provenance envelope, one per (instrument, field)
# ---------------------------------------------------------------------------


class Observation(_Frozen):
    """One provider observation for one (instrument, field) cell (02 §4.6).

    An observation without ``as_of`` is not allowed to exist: ``as_of`` is the
    time of the **data**, never the time of the fetch.
    """

    instrument_id: str
    field: str
    value_num: float | None = None
    value_text: str | None = None
    unit: str
    source: str
    source_id: str | None = None
    as_of: datetime
    fetched_at: datetime
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    verdict: VerdictKind = VerdictKind.OK
    #: 1 official, 2 vendor, 3 aggregator, 4 scrape (02 §4.6).
    trust: int = Field(ge=1, le=4)
    raw_sha256: str | None = None
    job_id: str | None = None

    @field_validator("field")
    @classmethod
    def _field_is_a_canonical_column(cls, value: str) -> str:
        if value not in _COLUMN_SET:
            raise ValueError(f"field {value!r} is not one of the 92 canonical columns")
        return value

    @field_validator("unit")
    @classmethod
    def _unit_is_known(cls, value: str) -> str:
        if not _UNIT_PATTERN.fullmatch(value):
            raise ValueError(
                f"unknown unit {value!r}; expected price:<ISO-4217> or one of "
                "pct, ratio, shares, count, date, timestamp, enum, text, bytes"
            )
        return value


__all__ = [
    "CANONICAL_COLUMNS_V1",
    "CANONICAL_COLUMN_GROUPS",
    "CORE_COLUMNS",
    "DATA_QUALITY_VALUES",
    "DERIVED_QUALITY_COLUMNS",
    "BudgetSpec",
    "Capability",
    "MarketKind",
    "MarketPlan",
    "MarketRequest",
    "MarketSpec",
    "Observation",
    "OutputFormat",
    "PlanBatch",
    "PlanMode",
    "QuoteFreshness",
    "QuotePlanV1",
    "QuoteUniverse",
    "UniverseSlice",
    "VerdictKind",
    "columns_in_group",
    "group_of",
]
