"""The canonical stock catalog: 116 columns, frozen from Finviz evidence.

The catalog answers one question — *which fields does the engine promise for a
stock row, and in what unit* — and it answers it with evidence instead of
taste. Finviz's screener returns exactly 116 stock columns; both
``evidence/column_api_116.json`` (apiName/name/urlSort/category/premium/sample)
and ``evidence/api/screener-all-columns.json`` (one live row carrying exactly
those 116 keys) are the oracle. ``app/engine/data/finviz_columns_116.json`` is
that oracle, transcribed once and machine-checked against ``CATALOG`` by the
test suite: same order, same 116 apiNames, no invented column, no dropped one.

Each data entry carries ``apiName`` (the provider's name, kept verbatim),
``key`` (the snake_case canonical name the engine and UI speak), ``label`` (the
provider's display label, blank ones filled honestly), ``unit`` (the
:class:`~app.engine.model.Unit` *value*: ``usd``, ``shares``, ``pct``,
``ratio``, ``count``, ``date``, ``text`` or ``unknown``), ``category`` (the
provider's grouping; the
four entries the evidence left blank get one), ``views`` (where the field
appears) and ``source_keys`` (at least the apiName). ``MetricSpec`` has no
category slot, so the category stays in the data file; :data:`CATALOG` is the
runtime projection of the same rows.

Units are canonical, and the adapter owes the scaling
----------------------------------------------------
``Unit.USD`` always means absolute dollars and ``Unit.SHARES`` always means a
share count — never "millions". The screener endpoint prints several columns
in millions anyway, so the Finviz adapter MUST multiply before it builds a
``Cell``:

* money in millions → multiply by ``1e6``: ``marketCap`` (48793.6 means
  $48,793,627,164), ``enterpriseValue``, ``income``, ``sales``;
* share counts in millions → multiply by ``1e6``: ``sharesOutstanding2``
  (281.97 means 281,970,000 shares), ``sharesFloat``, ``shortInterest``;
* ``volume`` and ``averageVolume`` are already raw share counts, and
  ``employees`` is already a raw count — do **not** scale them;
* ``price``/``open``/``high``/``low``/``prevClose``/``targetPrice``/``book``/
  ``cashPerShare``/``eps``/``dividend``/``averageTrueRange`` are dollars as
  printed; ``book``, ``cashPerShare``, ``eps``, ``dividend`` and ``estQ1`` are
  per-share amounts and stay ``USD``;
* percentages are percentage *points* as printed (``grossMargin`` 53.72 means
  53.72%), not fractions — ``Unit.PERCENT`` never re-scales them;
* the technical distance columns (``sma20``/``sma50``/``sma200``,
  ``high50d``/``low50d``, ``high52w``/``low52w``, ``highAt``/``lowAt``) are
  percentages from the moving average / extreme, not prices, so they are
  ``PERCENT``; ``averageTrueRange`` is the one technical column in dollars;
* ``UNKNOWN`` is reserved for the two fields that are genuinely not scalars:
  ``candlestick`` (a numeric pattern code) and ``52Wrange`` (a
  ``{low, high}`` object). Nothing is guessed into a unit it does not have.

Scope: stocks only
------------------
This is the 116-column **stock** slice. The 74 ETF columns are deferred: an
absent ETF field is an honest gap, and inventing one would put a made-up label
or unit in front of a user. When ETF support lands it arrives as its own
overlay with its own evidence — not as a patch to these 116.

No provider call happens here: the module reads a checked-in JSON file and
builds frozen value objects. It is imported at process start, so a broken data
file fails loudly and immediately instead of producing a half catalog.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from app.engine.model import MetricSpec, Unit

#: Version of the frozen slice; bumped when the data file changes meaning.
CATALOG_VERSION = "2026-10-26"

#: The evidence set is exactly this wide. A drift here is a broken data file.
EXPECTED_COLUMNS = 116

#: Keys a stock screen cannot work without, asserted by the test suite too.
CORE_KEYS: tuple[str, ...] = (
    "ticker",
    "company",
    "market_cap",
    "pe",
    "forward_pe",
    "eps",
    "sales",
    "price",
    "change",
    "volume",
)

_REQUIRED_FIELDS = frozenset(
    {"apiName", "key", "label", "unit", "category", "views", "source_keys"}
)

_DATA_PATH = Path(__file__).with_name("data") / "finviz_columns_116.json"


@dataclass(frozen=True, slots=True)
class _Row:
    """One validated data-file row, in the order the evidence printed it."""

    api_name: str
    key: str
    label: str
    unit: str
    category: str
    views: tuple[str, ...]
    source_keys: tuple[str, ...]


def _text(entry: Mapping[str, object], field: str, position: int) -> str:
    """Read a required non-blank string from one data-file row."""

    value = entry[field]
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(
            f"catalog data entry {position} needs a non-blank string {field!r}, "
            f"got {value!r}"
        )
    return value


def _strings(
    entry: Mapping[str, object],
    field: str,
    position: int,
    *,
    allow_empty: bool = False,
) -> tuple[str, ...]:
    """Read a required list of non-blank strings from one data-file row."""

    value = entry[field]
    if not isinstance(value, list) or (not value and not allow_empty):
        raise RuntimeError(
            f"catalog data entry {position} needs a "
            f"{'list' if allow_empty else 'non-empty list'} {field!r}"
        )
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise RuntimeError(
                f"catalog data entry {position} has a non-text item in {field!r}"
            )
    return tuple(value)


def _load_entries() -> tuple[_Row, ...]:
    """Read the frozen slice, refusing anything that is not a 116-column list."""

    try:
        raw = json.loads(_DATA_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:  # pragma: no cover - a broken checkout
        raise RuntimeError(f"catalog data file is missing: {_DATA_PATH}") from exc
    if not isinstance(raw, list):
        raise RuntimeError(
            f"catalog data file must hold a list of entries, got {type(raw).__name__}"
        )
    if len(raw) != EXPECTED_COLUMNS:
        raise RuntimeError(
            f"catalog data file must hold {EXPECTED_COLUMNS} entries, got {len(raw)}"
        )
    rows: list[_Row] = []
    for position, entry in enumerate(raw):
        if not isinstance(entry, dict) or set(entry) != _REQUIRED_FIELDS:
            raise RuntimeError(
                f"catalog data entry {position} must carry exactly "
                f"{sorted(_REQUIRED_FIELDS)}"
            )
        api_name = _text(entry, "apiName", position)
        source_keys = _strings(entry, "source_keys", position)
        if source_keys[0] != api_name:
            raise RuntimeError(
                f"catalog data entry {position} must name {api_name!r} as its first "
                f"source key, got {source_keys[0]!r}"
            )
        rows.append(
            _Row(
                api_name=api_name,
                key=_text(entry, "key", position),
                label=_text(entry, "label", position),
                unit=_text(entry, "unit", position),
                category=_text(entry, "category", position),
                views=_strings(entry, "views", position, allow_empty=True),
                source_keys=source_keys,
            )
        )
    return tuple(rows)


def _build_catalog(rows: tuple[_Row, ...]) -> tuple[MetricSpec, ...]:
    specs: list[MetricSpec] = []
    for row in rows:
        try:
            unit = Unit(row.unit)
        except ValueError as exc:
            raise RuntimeError(
                f"catalog entry {row.api_name!r} has an unknown unit {row.unit!r}"
            ) from exc
        specs.append(
            MetricSpec(
                key=row.key,
                label=row.label,
                unit=unit,
                source_keys=row.source_keys,
                views=row.views,
            )
        )
    keys = [spec.key for spec in specs]
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    if duplicates:
        raise RuntimeError(f"catalog keys must be unique, duplicated: {duplicates}")
    missing = [key for key in CORE_KEYS if key not in set(keys)]
    if missing:
        raise RuntimeError(f"catalog is missing core keys: {missing}")
    return tuple(specs)


#: The 116 canonical stock metrics, in provider (probe) order.
CATALOG: tuple[MetricSpec, ...] = _build_catalog(_load_entries())

#: Canonical key → spec. The same objects as :data:`CATALOG`, never copies.
CATALOG_BY_KEY: Mapping[str, MetricSpec] = MappingProxyType(
    {spec.key: spec for spec in CATALOG}
)


def get_metric(key: str) -> MetricSpec:
    """Return the spec for a canonical key; :class:`KeyError` if it is not one.

    Lookup is exact on purpose: the catalog speaks one spelling, and silently
    accepting ``"Market_Cap"`` would let a typo become a second vocabulary.
    """

    return CATALOG_BY_KEY[key]


def catalog_hash() -> str:
    """Stable sha256 over the sorted canonical keys.

    The digest covers *keys only*, so renaming a label or re-mapping a unit
    does not move it; adding, dropping or renaming a field does. That is the
    contract downstream caches pin against.
    """

    joined = "\n".join(sorted(CATALOG_BY_KEY))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


__all__ = [
    "CATALOG",
    "CATALOG_BY_KEY",
    "CATALOG_VERSION",
    "CORE_KEYS",
    "EXPECTED_COLUMNS",
    "catalog_hash",
    "get_metric",
]
