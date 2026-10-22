"""Reference master: the file the resolver reads instead of memorising a list.

``MEE_REFERENCE_PATH`` (``Settings.reference_path``) points at a JSON document
with ``mee.reference.v1`` shape: venues, jurisdictions, keyword tables and the
instruments themselves. The same document is the test suite's oracle, which is
the point — a resolver with a hardcoded ticker list drifts from the file and
fails the gate.

Design decisions worth keeping:

* **validated on load, not on use.** An instrument whose ``instrument_id`` is
  not ``<MIC>:<symbol>``, a duplicate id or an unknown venue is a
  ``ReferenceError`` naming the row. A silently skipped row would become a
  universe that is quietly smaller than the file.
* **cached by (path, mtime, size).** The resolver runs on every estimate; the
  file is small but it is not re-parsed per request. Touching the file in a
  long-lived process invalidates the cache without a restart.
* **pure.** No database, no network, no clock. Seeding the ``instruments``
  table is a separate, explicit step (``app.resolve.seed``), so reading the
  master can never write anything by accident.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from app.resolve.aliases import normalize

#: Repo-relative default; ``MEE_REFERENCE_PATH`` overrides it.
DEFAULT_REFERENCE_PATH = Path(__file__).resolve().parents[2] / "reference" / "venue_master.json"

#: The only schema this loader understands (02 §4.4 shape, JSON flavour).
SCHEMA = "mee.reference.v1"

_STATUSES = frozenset({"active", "delisted", "suspended"})


class ReferenceError(RuntimeError):
    """The reference master is missing or malformed.

    Raised instead of returning a partial catalogue: "half a master" answers
    resolvable queries as ``not_found``, which is a lie the caller cannot see.
    """


@dataclass(frozen=True)
class Venue:
    mic: str
    name: str
    country: str
    timezone: str | None = None
    default_currency: str | None = None
    is_24_7: bool = False


@dataclass(frozen=True)
class Instrument:
    """One canonical instrument (02 §4.4) plus the fields the resolver needs."""

    instrument_id: str
    symbol: str
    mic: str
    exchange: str | None
    currency: str | None
    name: str
    asset_class: str
    instrument_type: str | None
    sector: str | None
    industry: str | None
    country: str | None
    status: str
    is_primary_listing: bool
    isin: str | None
    adv_usd: float | None
    aliases: tuple[str, ...]
    provider_bindings: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Ambiguity:
    """A known share-class collision, kept for documentation/observability.

    The resolver does **not** consult this list: ambiguity has to fall out of
    the alias index, otherwise the resolver would be reading the answer key.
    """

    query: str
    kind: str
    candidates: tuple[str, ...]
    why: str = ""


@dataclass(frozen=True)
class Reference:
    """A loaded, validated reference master with its lookup indexes."""

    schema: str
    version: str
    path: Path
    fingerprint: str
    venues: tuple[Venue, ...]
    instruments: tuple[Instrument, ...]
    ambiguities: tuple[Ambiguity, ...]
    jurisdictions: Mapping[str, Mapping[str, Any]]
    sector_keywords: Mapping[str, tuple[str, ...]]
    asset_class_keywords: Mapping[str, tuple[str, ...]]
    size_keywords: Mapping[str, Any]
    symbol_index: Mapping[str, tuple[str, ...]]
    alias_index: Mapping[str, tuple[str, ...]]
    by_id: Mapping[str, Instrument] = field(repr=False, compare=False)

    # -- lookups ---------------------------------------------------------//

    def venue(self, mic: str | None) -> Venue | None:
        if not mic:
            return None
        wanted = mic.upper()
        for venue in self.venues:
            if venue.mic == wanted:
                return venue
        return None

    def instrument(self, instrument_id: str) -> Instrument | None:
        return self.by_id.get(instrument_id)

    @property
    def active(self) -> tuple[Instrument, ...]:
        return tuple(row for row in self.instruments if row.status == "active")

    def unique_symbols(self, instruments: tuple[Instrument, ...]) -> bool:
        """True when every symbol in ``instruments`` is distinct."""

        symbols = [row.symbol for row in instruments]
        return len(symbols) == len(set(symbols))


def load_reference(path: str | Path | None = None) -> Reference:
    """Load (or return the cached) reference master at ``path``.

    ``path`` is ``None`` → ``DEFAULT_REFERENCE_PATH``. The error message names
    both the path and the environment variable, because "file not found" with
    no path is the least useful failure in a deployment.
    """

    resolved = Path(path) if path is not None else DEFAULT_REFERENCE_PATH
    try:
        stat = resolved.stat()
    except FileNotFoundError as exc:
        raise ReferenceError(
            f"reference master not found at {resolved}; set MEE_REFERENCE_PATH "
            "or ship reference/venue_master.json"
        ) from exc
    except OSError as exc:  # pragma: no cover - unreadable path, not a data problem
        raise ReferenceError(f"reference master at {resolved} is unreadable: {exc}") from exc

    return _load_cached(str(resolved.resolve()), stat.st_mtime_ns, stat.st_size)


@lru_cache(maxsize=8)
def _load_cached(path: str, mtime_ns: int, size: int) -> Reference:
    resolved = Path(path)
    try:
        document = json.loads(resolved.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ReferenceError(f"reference master at {resolved} is not valid JSON: {exc}") from exc
    except OSError as exc:  # pragma: no cover - raced deletion between stat and read
        raise ReferenceError(f"reference master at {resolved} is unreadable: {exc}") from exc

    if not isinstance(document, dict):
        raise ReferenceError(f"reference master at {resolved} must be a JSON object")
    schema = document.get("schema")
    if schema != SCHEMA:
        raise ReferenceError(
            f"reference master at {resolved} declares schema {schema!r}; expected {SCHEMA!r}"
        )

    venues = _venues(document, resolved)
    instruments = _instruments(document, resolved, {venue.mic for venue in venues})
    symbol_index, alias_index = _indexes(instruments)

    return Reference(
        schema=SCHEMA,
        version=str(document.get("version", "")),
        path=resolved,
        fingerprint=f"{resolved}:{mtime_ns}:{size}",
        venues=venues,
        instruments=instruments,
        ambiguities=_ambiguities(document),
        jurisdictions=_phrase_table(document.get("jurisdictions"), resolved, "jurisdictions"),
        sector_keywords=_keyword_table(document.get("sector_keywords"), resolved, "sector_keywords"),
        asset_class_keywords=_keyword_table(
            document.get("asset_class_keywords"), resolved, "asset_class_keywords"
        ),
        size_keywords=dict(document.get("size_keywords") or {}),
        symbol_index=symbol_index,
        alias_index=alias_index,
        by_id={row.instrument_id: row for row in instruments},
    )


# --------------------------------------------------------------------------- #
# Parsing helpers — each one raises with the path and the offending row
# --------------------------------------------------------------------------- #


def _venues(document: dict[str, Any], path: Path) -> tuple[Venue, ...]:
    rows = document.get("venues")
    if not isinstance(rows, list) or not rows:
        raise ReferenceError(f"reference master at {path} has no venues[]")
    venues: list[Venue] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict) or not row.get("mic"):
            raise ReferenceError(f"reference master at {path}: every venue needs a mic: {row!r}")
        mic = str(row["mic"]).upper()
        if mic in seen:
            raise ReferenceError(f"reference master at {path}: duplicate venue mic {mic}")
        seen.add(mic)
        venues.append(
            Venue(
                mic=mic,
                name=str(row.get("name") or mic),
                country=str(row.get("country") or "").upper(),
                timezone=row.get("timezone"),
                default_currency=row.get("default_currency"),
                is_24_7=bool(row.get("is_24_7", False)),
            )
        )
    return tuple(venues)


def _instruments(
    document: dict[str, Any], path: Path, venue_mics: set[str]
) -> tuple[Instrument, ...]:
    rows = document.get("instruments")
    if not isinstance(rows, list) or not rows:
        raise ReferenceError(f"reference master at {path} has no instruments[]")

    instruments: list[Instrument] = []
    seen: set[str] = set()
    isins: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ReferenceError(f"reference master at {path}: instrument row is not an object")
        instrument_id = str(row.get("instrument_id") or "")
        symbol = str(row.get("symbol") or "")
        mic = str(row.get("mic") or "").upper()
        if not instrument_id or not symbol or not mic:
            raise ReferenceError(
                f"reference master at {path}: instrument needs instrument_id, symbol and mic: {row!r}"
            )
        if instrument_id != f"{mic}:{symbol}":
            raise ReferenceError(
                f"reference master at {path}: instrument_id {instrument_id!r} must be "
                f"'<MIC>:<symbol>' ({mic}:{symbol})"
            )
        if mic not in venue_mics:
            raise ReferenceError(
                f"reference master at {path}: {instrument_id} names unknown venue {mic}"
            )
        if instrument_id in seen:
            raise ReferenceError(f"reference master at {path}: duplicate instrument_id {instrument_id}")
        seen.add(instrument_id)

        isin = str(row.get("isin")).upper() if row.get("isin") else None
        if isin:
            claimed = isins.get(isin)
            if claimed is not None:
                # ``ux_instruments_isin`` is unique (02 §4.4); a duplicate here
                # would fail the seed mid-transaction instead of naming the rows.
                raise ReferenceError(
                    f"reference master at {path}: ISIN {isin} is claimed by both "
                    f"{claimed} and {instrument_id}"
                )
            isins[isin] = instrument_id

        status = str(row.get("status") or "active")
        if status not in _STATUSES:
            raise ReferenceError(
                f"reference master at {path}: {instrument_id} has status {status!r}; "
                f"expected one of {sorted(_STATUSES)}"
            )
        asset_class = str(row.get("asset_class") or "")
        if not asset_class:
            raise ReferenceError(f"reference master at {path}: {instrument_id} has no asset_class")

        name = str(row.get("name") or symbol)
        aliases = tuple(str(alias) for alias in row.get("aliases") or () if str(alias).strip())
        instruments.append(
            Instrument(
                instrument_id=instrument_id,
                symbol=symbol,
                mic=mic,
                exchange=row.get("exchange"),
                currency=row.get("currency"),
                name=name,
                asset_class=asset_class,
                instrument_type=row.get("instrument_type"),
                sector=row.get("sector"),
                industry=row.get("industry"),
                country=str(row.get("country") or "").upper() or None,
                status=status,
                is_primary_listing=bool(row.get("is_primary_listing", False)),
                isin=row.get("isin"),
                adv_usd=float(row["adv_usd"]) if row.get("adv_usd") is not None else None,
                aliases=aliases,
                provider_bindings=dict(row.get("provider_bindings") or {}),
            )
        )
    return tuple(instruments)


def _indexes(
    instruments: tuple[Instrument, ...],
) -> tuple[dict[str, tuple[str, ...]], dict[str, tuple[str, ...]]]:
    """Build the symbol and alias indexes, sorted so lookups are deterministic."""

    symbols: dict[str, set[str]] = {}
    aliases: dict[str, set[str]] = {}
    for instrument in instruments:
        symbols.setdefault(instrument.symbol.upper(), set()).add(instrument.instrument_id)
        keys = {instrument.symbol, instrument.name, *instrument.aliases}
        for key in keys:
            normalized = normalize(key)
            if normalized:
                aliases.setdefault(normalized, set()).add(instrument.instrument_id)
    return (
        {key: tuple(sorted(value)) for key, value in symbols.items()},
        {key: tuple(sorted(value)) for key, value in aliases.items()},
    )


def _ambiguities(document: dict[str, Any]) -> tuple[Ambiguity, ...]:
    cases: list[Ambiguity] = []
    for row in document.get("ambiguous") or ():
        if not isinstance(row, dict) or not row.get("query"):
            continue
        cases.append(
            Ambiguity(
                query=str(row["query"]),
                kind=str(row.get("kind") or "unknown"),
                candidates=tuple(sorted(str(item) for item in row.get("candidates") or ())),
                why=str(row.get("why") or ""),
            )
        )
    return tuple(cases)


def _keyword_table(
    raw: Any, path: Path, field_name: str
) -> dict[str, tuple[str, ...]]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ReferenceError(f"reference master at {path}: {field_name} must be an object")
    table: dict[str, tuple[str, ...]] = {}
    for key, values in raw.items():
        if not isinstance(values, list):
            raise ReferenceError(
                f"reference master at {path}: {field_name}[{key!r}] must be a list of phrases"
            )
        table[str(key)] = tuple(str(value) for value in values)
    return table


def _phrase_table(
    raw: Any, path: Path, field_name: str
) -> dict[str, dict[str, Any]]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ReferenceError(f"reference master at {path}: {field_name} must be an object")
    table: dict[str, dict[str, Any]] = {}
    for key, value in raw.items():
        if not isinstance(value, dict):
            raise ReferenceError(
                f"reference master at {path}: {field_name}[{key!r}] must be an object"
            )
        entry = dict(value)
        entry["aliases"] = tuple(str(alias) for alias in value.get("aliases") or ())
        table[str(key)] = entry
    return table


__all__ = [
    "DEFAULT_REFERENCE_PATH",
    "SCHEMA",
    "Ambiguity",
    "Instrument",
    "Reference",
    "ReferenceError",
    "Venue",
    "load_reference",
]
