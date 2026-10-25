"""Screen facts — the per-symbol store behind the short and ownership filters.

Three Finviz filter keys had no column anywhere in the product: ``sh_short``
(Short Float), ``sh_insiderown`` (Insider Ownership) and ``sh_instown``
(Institutional Ownership). TradingView's ``short_float`` answered ``null`` for
every row of both live probes (0 of 5,720 US, 0 of 96,278 global) and the scan
carries no ownership metric at all, so a filter on any of the three could only
ever return an empty screen. The Finviz **stock page** publishes all three as
snapshot labels — live 2026-10-25 for AAPL: ``Short Float 0.83%``,
``Insider Own 0.12%``, ``Inst Own 68.93%`` — and
:func:`app.providers.adapters.finviz.parse_facts` reads exactly those cells.
This module owns the one table they live in (``screen_facts``,
``0005_screen_facts``) and the one join that copies them onto a selection
universe.

Why a separate table, and not ``fundamentals_snapshots``
-------------------------------------------------------
The fundamentals store is the 22-cell contract vocabulary
(``app.contracts``). ``short_float``/``insider_own``/``inst_own`` are not in it,
and widening a published contract to hold a scraped cell would let a provider's
label table decide what the export means. One table per source shape keeps the
two vocabularies honest: fundamentals is a 24-hour picture of the balance
sheet, facts is a 24-hour picture of the float and who holds it, and neither
can silently erase the other's cells. The keys are also a different kind of
metric — a percentage of shares, not a currency amount or a multiple — and the
freshness policy is the only thing they share.

Percent means percent
---------------------
Every cell is stored in the provider's own percentage points: ``0.83`` is
0.83%, ``68.93`` is 68.93%. There is **no** fraction conversion and no scaling
anywhere in the path — scrape → store → row → filter all carry the same number,
so ``{"key": "sh_short", "op": "lt", "value": 5}`` means \"short float below
5%\", exactly what Finviz's own ``u5`` bucket means. A ``0.83%`` value read as
a fraction would publish 83%, and a filter at 5 would then match nothing.

A missing value is no observation
---------------------------------
``-``, ``N/A``, an empty cell and a label that is absent all produce **no**
cell (zero rows from the adapter) and therefore ``None`` on the row. The stored
JSON is full width — every vocabulary key present, missing ones ``null`` — so a
reader can tell \"the provider has no short float for this ETF\" from \"this key
is not part of the vocabulary\". ``None`` matches **no** filter: unknown is not
zero, and a null never erases a value the scan itself carried.

Four rules the code has to earn
-------------------------------
* **One read per selection, never one per symbol.** The store's public read is
  :meth:`SqliteFactsStore.rows` — a single ``SELECT`` over the table, filtered
  to the universe in memory. A per-symbol ``get()`` would send 5,700 queries for
  a US criteria selection; the join sends one.
* **Fresh only.** A row past ``expires_at`` is not evidence of anything this
  second; a stale row is skipped exactly like a missing one, so the response
  never quotes yesterday's float as today's.
* **Null stays null, and a null never erases a value.** A symbol the store does
  not hold, or a cell the snapshot did not fill, keeps ``None`` — which matches
  no filter. When the scan *did* carry a number for the same column, the store's
  null does not overwrite it; when the store has a number, it wins, because it
  is the owner of these three columns.
* **No mutation.** Each row is copied once, so the caller's universe (and the
  cached scan page behind it) keeps exactly the cells it arrived with.

A store that cannot answer — not configured, or a database that has not run the
``0005_screen_facts`` migration — is **not** a failed selection: the join logs
the reason and returns the rows unenriched, so the endpoint keeps its 200 and
the three cells stay ``None`` (and their filters match nothing, which is the
same answer an all-null column gives).
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Protocol, Sequence

from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db import build_engine, ensure_schema_async

logger = logging.getLogger("mee.screen.facts")

#: The vocabulary, in Finviz panel order. Three cells, one per filter key —
#: no label the product cannot filter is stored just because the page prints it
#: (``Short Ratio`` and ``Shs Float`` are on the live page and stay out).
FACTS_FIELDS: tuple[str, ...] = ("short_float", "insider_own", "inst_own")
_FIELD_SET: frozenset[str] = frozenset(FACTS_FIELDS)

if len(FACTS_FIELDS) != 3 or len(_FIELD_SET) != 3:  # pragma: no cover - contract guard
    raise RuntimeError(
        "the facts vocabulary is 3 unique columns, got "
        f"{len(FACTS_FIELDS)} entries / {len(_FIELD_SET)} unique"
    )

#: The row columns this module owns. :mod:`app.screen.filter_fields` checks a
#: filter key against ``CARRIED_FIELDS`` plus this tuple, so a key can never
#: point at a column no row carries.
FACTS_ROW_FIELDS: tuple[str, ...] = FACTS_FIELDS

#: Finviz key → the row column this store fills. Published for the disposition
#: tests; :mod:`app.screen.filters` remains the one place a key gets its field
#: (``FILTER_CATALOG``), so this table can never become a second source of truth.
FACTS_FILTER_KEYS: Mapping[str, str] = MappingProxyType(
    {
        "sh_short": "short_float",
        "sh_insiderown": "insider_own",
        "sh_instown": "inst_own",
    }
)

#: A facts picture is a slow picture: 24 hours, like the fundamentals snapshot.
TTL_S = 86_400

#: The page that serves these cells. One source today; the column exists so a
#: second one can be told apart instead of overwriting the label.
SOURCE = "finviz"

#: The one table this module owns (``0005_screen_facts``).
TABLE = "screen_facts"

#: ``_parse_stamp``'s answer for a row whose timestamp cannot be read. A row
#: that cannot prove its age is expired, which is the safe direction.
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

#: SQLite is single-writer. A second connection waits (``busy_timeout``) and a
#: wait that overruns is retried, exactly like the screen and fundamentals stores.
_LOCK_RETRIES = 8
_LOCK_BACKOFF_S = 0.02


class FactsSchemaError(RuntimeError):
    """The database is behind this build: no ``screen_facts`` table (``0005``).

    ``ensure_schema`` bootstraps an *empty* file and deliberately does nothing to
    a database that already has the job tables — a long-lived service must not
    migrate itself on a read. An existing database is therefore upgraded through
    Alembic, by the CLI or by an operator, and this error names the command.
    """


def migration_hint(db_path: str | Path) -> str:
    """The exact command that brings ``db_path`` to head."""

    return f"alembic -x db={Path(db_path).resolve()} upgrade head"


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso(moment: datetime) -> str:
    """Aware UTC with microseconds: lexical order equals chronological order."""

    if moment.tzinfo is None:
        raise ValueError("naive datetimes are not allowed in the facts store")
    return moment.astimezone(UTC).isoformat(timespec="microseconds")


def _parse_stamp(value: Any, *, fallback: datetime | None = None) -> datetime:
    """Read a stored timestamp as aware UTC; ``fallback`` otherwise."""

    if isinstance(value, datetime):
        moment = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return moment.astimezone(UTC)
    if isinstance(value, str) and value.strip():
        candidate = value.strip()
        if len(candidate) == 10 and candidate[4] == "-" and candidate[7] == "-":
            candidate = f"{candidate}T00:00:00+00:00"
        try:
            moment = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
        except ValueError:
            return fallback or _EPOCH
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        return moment.astimezone(UTC)
    return fallback or _EPOCH


def canonical_symbol(symbol: str) -> str:
    """The storage key: trimmed and upper-cased (``" aapl "`` → ``"AAPL"``).

    One symbol is one row, so the key may not depend on how a caller typed it.
    A blank token is a caller error rather than a row keyed on ``""``.
    """

    key = str(symbol or "").strip().upper()
    if not key:
        raise ValueError("a screen facts row needs a non-empty symbol")
    return key


def _cell(value: Any) -> float | None:
    """One facts cell: a finite float, or ``None``.

    The vocabulary is numeric (percent points), so a string, a bool, ``NaN`` or
    ``inf`` was never a measurement and stays unknown instead of being coerced
    into one.
    """

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


@dataclass(frozen=True, slots=True)
class ScreenFacts:
    """One symbol's short/ownership cells, with the clocks that date them."""

    symbol: str
    fields: Mapping[str, Any]
    source: str | None = SOURCE
    as_of: datetime = field(default_factory=utc_now)
    fetched_at: datetime = field(default_factory=utc_now)
    expires_at: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", canonical_symbol(self.symbol))
        unknown = sorted(set(self.fields) - _FIELD_SET)
        if unknown:
            raise ValueError(
                f"unknown facts field(s) {unknown}; the vocabulary is "
                f"{len(FACTS_FIELDS)} columns: {', '.join(FACTS_FIELDS)}"
            )
        for name, value in self.fields.items():
            if value is None:
                continue
            if isinstance(value, bool):
                raise ValueError(f"{self.symbol}: {name} is a bool, not a facts cell")
            if not isinstance(value, (int, float)):
                raise ValueError(
                    f"{self.symbol}: {name} is {type(value).__name__}, not a number"
                )
            if not math.isfinite(float(value)):
                raise ValueError(f"{self.symbol}: {name} is not a finite number")
        if self.expires_at is None:
            object.__setattr__(self, "expires_at", self.fetched_at + timedelta(seconds=TTL_S))

    @property
    def cells(self) -> dict[str, float | None]:
        """The full-width vocabulary: every key, missing ones ``None``."""

        return {name: _cell(self.fields.get(name)) for name in FACTS_FIELDS}

    @property
    def field_count(self) -> int:
        """How many of the three cells carry a value."""

        return sum(1 for value in self.cells.values() if value is not None)

    @property
    def complete(self) -> bool:
        """True only when every vocabulary cell is filled — never inferred."""

        return self.field_count == len(FACTS_FIELDS)

    @property
    def expiry(self) -> datetime:
        """``expires_at``, resolved: a row built without one lives one TTL."""

        return self.expires_at or self.fetched_at + timedelta(seconds=TTL_S)

    def is_fresh(self, now: datetime) -> bool:
        return now < self.expiry

    def is_stale(self, now: datetime) -> bool:
        return not self.is_fresh(now)

    def as_dict(self, *, now: datetime | None = None) -> dict[str, Any]:
        """A JSON-safe view; ``stale`` is only answered when ``now`` is given."""

        document: dict[str, Any] = {
            "symbol": self.symbol,
            "source": self.source,
            "as_of": iso(self.as_of),
            "fetched_at": iso(self.fetched_at),
            "expires_at": iso(self.expiry),
            "field_count": self.field_count,
            "complete": self.complete,
            "fields": self.cells,
        }
        if now is not None:
            document["stale"] = self.is_stale(now)
        return document


def facts_from_values(
    symbol: str,
    values: Mapping[str, Any],
    *,
    source: str | None = SOURCE,
    as_of: datetime | None = None,
    fetched_at: datetime | None = None,
    ttl_s: int = TTL_S,
) -> ScreenFacts:
    """Build one row from ``{field: number}``; absent cells become ``None``.

    This is the seam the Finviz scrape writes through: the adapter returns only
    the cells it really read, and this function turns them into the full-width
    row the store persists. A non-numeric cell is dropped (unknown), never
    coerced — the adapter already guarantees that, and this is the second gate.
    """

    if ttl_s <= 0:
        raise ValueError("ttl_s must be positive")
    unknown = sorted(set(values) - _FIELD_SET)
    if unknown:
        raise ValueError(
            f"unknown facts field(s) {unknown}; the vocabulary is "
            f"{len(FACTS_FIELDS)} columns: {', '.join(FACTS_FIELDS)}"
        )
    moment = fetched_at or utc_now()
    stamp = as_of or moment
    return ScreenFacts(
        symbol=symbol,
        fields={name: _cell(values.get(name)) for name in FACTS_FIELDS},
        source=source,
        as_of=stamp,
        fetched_at=moment,
        expires_at=moment + timedelta(seconds=ttl_s),
    )


# ---------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------


class SqliteFactsStore:
    """``screen_facts``: one row per symbol on the service's SQLite file."""

    def __init__(
        self, db_path: str | Path, *, clock: Any | None = None, ttl_s: int = TTL_S
    ) -> None:
        if ttl_s <= 0:
            raise ValueError("ttl_s must be positive")
        self.db_path = Path(db_path)
        self.ttl_s = int(ttl_s)
        self._clock = clock or utc_now
        self._engine: AsyncEngine | None = None
        self._ready = asyncio.Lock()
        self._verified = False

    # -- infrastructure ----------------------------------------------------

    @property
    def engine(self) -> AsyncEngine:
        if self._engine is None:
            self._engine = build_engine(self.db_path)
        return self._engine

    async def ensure_ready(self) -> None:
        """Bootstrap an empty file, then prove the table exists (once per store)."""

        async with self._ready:
            if self._verified:
                return
            await ensure_schema_async(self.db_path)
            if not await self._table_exists():
                raise FactsSchemaError(
                    f"{self.db_path} has no {TABLE} table; run: {migration_hint(self.db_path)}"
                )
            self._verified = True

    async def _table_exists(self) -> bool:
        async with self.engine.begin() as connection:
            result = await connection.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' AND name = :name"),
                {"name": TABLE},
            )
            return result.first() is not None

    async def _execute(self, statement: str, params: Mapping[str, Any] | None = None) -> int:
        engine = await self._ready_engine()
        for attempt in range(_LOCK_RETRIES):
            try:
                async with engine.begin() as connection:
                    result = await connection.execute(text(statement), dict(params or {}))
                    return int(result.rowcount or 0)
            except OperationalError as exc:
                if not _is_lock_error(exc):
                    raise
                if attempt == _LOCK_RETRIES - 1:
                    raise
                await asyncio.sleep(_LOCK_BACKOFF_S * (attempt + 1))
        return 0  # pragma: no cover - the loop returns or raises

    async def _all(
        self, statement: str, params: Mapping[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        engine = await self._ready_engine()
        for attempt in range(_LOCK_RETRIES):
            try:
                async with engine.begin() as connection:
                    result = await connection.execute(text(statement), dict(params or {}))
                    return [dict(row) for row in result.mappings().all()]
            except OperationalError as exc:
                if not _is_lock_error(exc):
                    raise
                if attempt == _LOCK_RETRIES - 1:
                    raise
                await asyncio.sleep(_LOCK_BACKOFF_S * (attempt + 1))
        return []  # pragma: no cover - the loop returns or raises

    async def _ready_engine(self) -> AsyncEngine:
        await self.ensure_ready()
        return self.engine

    async def aclose(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None
            self._verified = False

    # -- writes ------------------------------------------------------------

    async def upsert(self, facts: ScreenFacts) -> bool:
        """Insert or replace one symbol's row; ``False`` when it was older.

        The ``WHERE`` clause is the concurrency rule: ``fetched_at`` is monotonic
        per symbol, so a scrape that started earlier cannot overwrite a row a
        later scrape already committed. Two identical writes are idempotent.
        """

        moment = self._clock()
        payload = {
            "symbol": facts.symbol,
            "source": facts.source,
            "as_of": iso(facts.as_of),
            "fetched_at": iso(facts.fetched_at),
            "expires_at": iso(facts.expiry),
            "fields": json.dumps(
                facts.cells, sort_keys=True, ensure_ascii=False, allow_nan=False
            ),
            "field_count": facts.field_count,
            "updated_at": iso(moment),
        }
        return (
            await self._execute(
                """
                INSERT INTO screen_facts (
                    symbol, source, as_of, fetched_at, expires_at, fields,
                    field_count, updated_at
                ) VALUES (
                    :symbol, :source, :as_of, :fetched_at, :expires_at, :fields,
                    :field_count, :updated_at
                )
                ON CONFLICT(symbol) DO UPDATE SET
                    source = excluded.source,
                    as_of = excluded.as_of,
                    fetched_at = excluded.fetched_at,
                    expires_at = excluded.expires_at,
                    fields = excluded.fields,
                    field_count = excluded.field_count,
                    updated_at = excluded.updated_at
                 WHERE excluded.fetched_at >= screen_facts.fetched_at
                """,
                payload,
            )
            > 0
        )

    async def purge_expired(self, *, now: datetime | None = None) -> int:
        """Delete rows past their window; returns how many went."""

        return await self._execute(
            "DELETE FROM screen_facts WHERE expires_at <= :now",
            {"now": iso(now or self._clock())},
        )

    # -- reads -------------------------------------------------------------

    async def get(
        self,
        symbol: str,
        *,
        now: datetime | None = None,
        include_expired: bool = False,
    ) -> ScreenFacts | None:
        """The row for ``symbol``, or ``None`` when it is absent or expired."""

        key = canonical_symbol(symbol)
        row = await self._one(
            "SELECT * FROM screen_facts WHERE symbol = :symbol", {"symbol": key}
        )
        if row is None:
            return None
        facts = _facts_from_row(row)
        if include_expired:
            return facts
        return facts if facts.is_fresh(now or self._clock()) else None

    async def rows(self) -> list[ScreenFacts]:
        """Every stored row, whatever its age, ordered by symbol."""

        return [
            _facts_from_row(row)
            for row in await self._all("SELECT * FROM screen_facts ORDER BY symbol")
        ]

    async def count(self) -> int:
        row = await self._one("SELECT COUNT(*) AS n FROM screen_facts")
        return int((row or {}).get("n") or 0)

    async def _one(
        self, statement: str, params: Mapping[str, Any] | None = None
    ) -> dict[str, Any] | None:
        rows = await self._all(statement, params)
        return rows[0] if rows else None


def _facts_from_row(row: Mapping[str, Any]) -> ScreenFacts:
    """Decode one table row; a corrupt JSON cell becomes ``null``, not a crash."""

    raw = row.get("fields")
    cells: Mapping[str, Any] = {}
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:  # pragma: no cover - only a hand-edited row
            logger.warning("unreadable screen facts JSON symbol=%s", row.get("symbol"))
            parsed = {}
        if isinstance(parsed, Mapping):
            cells = parsed
    fetched_at = _parse_stamp(row.get("fetched_at"), fallback=_EPOCH)
    expires_at = _parse_stamp(row.get("expires_at"), fallback=_EPOCH)
    return ScreenFacts(
        symbol=str(row.get("symbol") or ""),
        fields={name: _cell(cells.get(name)) for name in FACTS_FIELDS},
        source=(str(row["source"]) if row.get("source") is not None else None),
        as_of=_parse_stamp(row.get("as_of"), fallback=fetched_at),
        fetched_at=fetched_at,
        expires_at=expires_at,
    )


def _is_lock_error(exc: OperationalError) -> bool:
    message = str(exc).lower()
    return "locked" in message or "busy" in message


# ---------------------------------------------------------------------------
# The join
# ---------------------------------------------------------------------------


class FactsReader(Protocol):
    """The one read this module needs: every stored row, in one call."""

    async def rows(self) -> Sequence[ScreenFacts]:  # pragma: no cover - protocol
        ...


def _row_key(row: Mapping[str, Any]) -> str | None:
    """The join key of a universe row, canonicalised the way the store stores it."""

    symbol = row.get("symbol")
    if not isinstance(symbol, str) or not symbol.strip():
        return None
    try:
        return canonical_symbol(symbol)
    except ValueError:
        return None


def fresh_facts(
    facts: Iterable[ScreenFacts], *, now: datetime | None = None
) -> dict[str, ScreenFacts]:
    """``symbol → facts`` for the rows still inside their window.

    Staleness is decided by ``expires_at`` (the fetch clock), never by ``as_of``:
    a row built this morning dates the data, and a row fetched four days ago is
    not fresh, whatever date its cells carry.
    """

    moment = now or utc_now()
    fresh: dict[str, ScreenFacts] = {}
    for row in facts:
        if row.is_fresh(moment):
            fresh[row.symbol] = row
    return fresh


async def load_facts(
    store: FactsReader | None,
    symbols: Iterable[str],
    *,
    now: datetime | None = None,
) -> dict[str, ScreenFacts]:
    """Read the store once and keep the fresh rows the universe asks for.

    ``store=None`` and an unmigrated database are both \"no enrichment\", not
    errors: an optional table an operator has not created yet must not turn a
    selection into a 500. The reason is logged once per call.
    """

    wanted = set()
    for symbol in symbols:
        if isinstance(symbol, str) and symbol.strip():
            try:
                wanted.add(canonical_symbol(symbol))
            except ValueError:  # pragma: no cover - blank guarded above
                continue
    if store is None or not wanted:
        return {}

    try:
        stored = await store.rows()
    except (FactsSchemaError, OperationalError) as exc:
        logger.warning("screen facts enrichment disabled for this selection: %s", exc)
        return {}

    return {
        symbol: row
        for symbol, row in fresh_facts(stored, now=now).items()
        if symbol in wanted
    }


def apply_facts(
    rows: Sequence[Mapping[str, Any]], facts: Mapping[str, ScreenFacts]
) -> list[dict[str, Any]]:
    """Join the three cells onto **copies** of ``rows``.

    A row whose symbol the store does not hold keeps its own cells and gains no
    value. The three columns are always present in the result, so a consumer can
    tell \"the store has no short float for this symbol\" from \"this row predates
    the join\". A store ``null`` never overwrites a value the scan carried; no
    row and no stored row is ever modified.
    """

    enriched: list[dict[str, Any]] = []
    for row in rows:
        joined = dict(row)
        key = _row_key(row)
        stored = facts.get(key) if key is not None else None
        cells = stored.cells if stored is not None else {}
        for name in FACTS_ROW_FIELDS:
            current = joined.get(name)
            value = cells.get(name)
            joined[name] = current if value is None else value
        enriched.append(joined)
    return enriched


async def enrich_rows(
    rows: Sequence[Mapping[str, Any]],
    store: FactsReader | None = None,
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Join the facts store onto a selection universe (the public path).

    One batch read, then a pure copy. ``store`` is the runtime's store
    (``Runtime.facts_store``); a test injects a double with the same one-method
    surface.
    """

    if not rows:
        return []
    facts = await load_facts(store, (_row_key(row) or "" for row in rows), now=now)
    return apply_facts(rows, facts)


__all__ = [
    "FACTS_FIELDS",
    "FACTS_FILTER_KEYS",
    "FACTS_ROW_FIELDS",
    "FactsReader",
    "FactsSchemaError",
    "SOURCE",
    "ScreenFacts",
    "SqliteFactsStore",
    "TABLE",
    "TTL_S",
    "apply_facts",
    "canonical_symbol",
    "enrich_rows",
    "facts_from_values",
    "fresh_facts",
    "iso",
    "load_facts",
    "migration_hint",
    "utc_now",
]
