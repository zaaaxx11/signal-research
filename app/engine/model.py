"""Shared value objects for the market engine.

The engine's first rule is that a value is never detached from its origin. A
number without a source, an as-of date and a status is not evidence; it is a
guess. These objects make that rule structural instead of a convention:

* :class:`Provenance` describes where one value came from and whether it is a
  real observation, a derivation or an honest absence.
* :class:`Cell` pairs a value with its provenance. A non-null value without
  provenance is rejected, and a null value must carry a non-``OK`` status.
* :class:`MetricSpec` is the catalog entry for one engine field; it carries the
  unit and the provider keys that may fill it, but never a provider name in a
  presentation row.
* :class:`SecurityRecord` is the identity half of a row.

The module deliberately uses dataclasses instead of pydantic: these objects are
constructed by adapters thousands of times per screen, and the validation here
is about invariants, not HTTP parsing. The API layer can serialise them with a
small projection when a contract is added.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class LicenseClass(str, Enum):
    """Whether a source may be shown to the public.

    ``PUBLIC`` is not the same as “the endpoint answered 200”. A source is
    public only when its licence permits redistribution; ``INTERNAL`` is for
    development and private research, and ``REFERENCE`` is a frozen oracle
    that must never appear in a response.
    """

    PUBLIC = "public"
    INTERNAL = "internal"
    REFERENCE = "reference"


class Unit(str, Enum):
    """The canonical unit of a metric.

    Scales are normalised at the adapter boundary: ``USD`` always means absolute
    US dollars, never “millions of dollars”. A provider that prints millions is
    converted before it becomes a :class:`Cell`.
    """

    USD = "USD"
    SHARES = "shares"
    PERCENT = "pct"
    RATIO = "ratio"
    COUNT = "count"
    DATE = "date"
    TEXT = "text"
    UNKNOWN = "unknown"


class CellStatus(str, Enum):
    """Why a cell has the value it has."""

    OK = "ok"
    DERIVED = "derived"
    ABSENT = "absent"
    NOT_REPORTED = "not_reported"
    NO_ESTIMATE = "no_estimate"
    UNIT_MISMATCH = "unit_mismatch"
    LICENSE_BLOCKED = "license_blocked"


@dataclass(frozen=True, slots=True)
class Provenance:
    """Origin of one cell.

    ``source`` is an adapter key (``sec``, ``finviz``, ``vendor``), not a display
    label. ``as_of`` is the observation date/time as text so adapters can keep
    the provider's precision; the engine never invents a timestamp for a value.
    """

    source: str
    as_of: str
    status: CellStatus = CellStatus.OK
    url: str | None = None
    inputs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        source = self.source.strip()
        as_of = self.as_of.strip()
        if not source:
            raise ValueError("provenance source must not be blank")
        if not as_of:
            raise ValueError("provenance as_of must not be blank")
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "as_of", as_of)
        object.__setattr__(self, "inputs", tuple(self.inputs))

    @property
    def ok(self) -> bool:
        return self.status is CellStatus.OK


@dataclass(frozen=True, slots=True)
class Cell:
    """A canonical value plus the evidence that produced it."""

    value: Any = None
    unit: Unit = Unit.UNKNOWN
    provenance: Provenance | None = None

    def __post_init__(self) -> None:
        if self.value is None:
            if self.provenance is None:
                raise ValueError("a null cell requires provenance with a missing reason")
            if self.provenance.status is CellStatus.OK:
                raise ValueError("a null cell cannot carry an OK status")
            return
        if self.provenance is None:
            raise ValueError("a non-null cell requires provenance")
        if self.provenance.status in {
            CellStatus.ABSENT,
            CellStatus.NOT_REPORTED,
            CellStatus.NO_ESTIMATE,
            CellStatus.UNIT_MISMATCH,
            CellStatus.LICENSE_BLOCKED,
        }:
            raise ValueError(
                f"a cell with status {self.provenance.status.value!r} must be null"
            )


@dataclass(frozen=True, slots=True)
class MetricSpec:
    """One catalog field.

    ``source_keys`` are the provider-side names that may fill this field. They
    are intentionally plural: one canonical metric can be answered by SEC,
    a vendor, or a frozen oracle, and the adapter decides which rung to use.
    """

    key: str
    label: str
    unit: Unit
    source_keys: tuple[str, ...] = ()
    views: tuple[str, ...] = ()
    derivation: str | None = None
    precision: int | None = None

    def __post_init__(self) -> None:
        key = self.key.strip().lower()
        label = self.label.strip()
        if not key:
            raise ValueError("metric key must not be blank")
        if not label:
            raise ValueError("metric label must not be blank")
        views = tuple(view.strip().lower() for view in self.views)
        if len(set(views)) != len(views):
            raise ValueError("metric views must be unique")
        if any(not view for view in views):
            raise ValueError("metric views must not contain blank names")
        object.__setattr__(self, "key", key)
        object.__setattr__(self, "label", label)
        object.__setattr__(self, "views", views)
        object.__setattr__(
            self, "source_keys", tuple(key.strip() for key in self.source_keys)
        )
        if self.derivation is not None:
            object.__setattr__(self, "derivation", self.derivation.strip() or None)


@dataclass(frozen=True, slots=True)
class SecurityRecord:
    """The identity of one listed instrument."""

    symbol: str
    name: str
    cik: str | None
    exchange: str | None
    mic: str | None
    asset_class: str
    currency: str
    country: str | None
    sector: str | None
    industry: str | None
    active: bool = True

    def __post_init__(self) -> None:
        symbol = self.symbol.strip().upper()
        name = self.name.strip()
        if not symbol:
            raise ValueError("security symbol must not be blank")
        if not name:
            raise ValueError("security name must not be blank")
        currency = self.currency.strip().upper()
        if not currency:
            raise ValueError("security currency must not be blank")
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "currency", currency)
        if self.cik is not None:
            object.__setattr__(self, "cik", self.cik.strip() or None)
        if self.exchange is not None:
            object.__setattr__(self, "exchange", self.exchange.strip() or None)
        if self.mic is not None:
            object.__setattr__(self, "mic", self.mic.strip().upper() or None)
        if self.country is not None:
            object.__setattr__(self, "country", self.country.strip() or None)
        if self.sector is not None:
            object.__setattr__(self, "sector", self.sector.strip() or None)
        if self.industry is not None:
            object.__setattr__(self, "industry", self.industry.strip() or None)


@dataclass(frozen=True, slots=True)
class SourceSpec:
    """Static, non-secret description of an upstream source."""

    name: str
    license_class: LicenseClass
    redistribution: bool
    attribution: str | None
    terms_url: str
    features: frozenset[str] = field(default_factory=frozenset)
    rate_limit: tuple[int, float] = (1, 1.0)

    def __post_init__(self) -> None:
        name = self.name.strip().lower()
        if not name:
            raise ValueError("source name must not be blank")
        terms_url = self.terms_url.strip()
        if not terms_url:
            raise ValueError("source terms_url must not be blank")
        limit, window = self.rate_limit
        if limit < 0:
            raise ValueError("source rate limit must not be negative")
        if window <= 0:
            raise ValueError("source rate window must be positive")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "terms_url", terms_url)
        object.__setattr__(self, "features", frozenset(self.features))

    @property
    def allows_public(self) -> bool:
        """True only for a source that is both public-licensed and redistributable."""

        return self.license_class is LicenseClass.PUBLIC and self.redistribution


__all__ = [
    "Cell",
    "CellStatus",
    "LicenseClass",
    "MetricSpec",
    "Provenance",
    "SecurityRecord",
    "SourceSpec",
    "Unit",
]
