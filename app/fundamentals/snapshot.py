"""Fundamentals snapshots — one row per symbol, 24 hours, nulls stay null.

This is the *foundation* the fundamentals filters will read (P11A), not the
forty-three-filter catalogue. A snapshot answers exactly one question — "what do
the fundamentals of this symbol look like as of now, and who said so" — and it
answers it with a fixed vocabulary:

* :data:`FUNDAMENTAL_FIELDS` is the contract's own ``Fundamentals`` +
  ``Statements`` groups (22 columns, ``app.contracts``), not a second list that
  could drift from the 92-column export. The production rung can serve all 22
  (``YahooAdapter.FUNDAMENTAL_SOURCES``), which a test pins.
* **One row per symbol.** ``symbol`` is the primary key and the write is an
  ``INSERT … ON CONFLICT DO UPDATE``, so a rebuild replaces the row instead of
  appending a second one. The upsert refuses an *older* build: ``fetched_at``
  only moves forward, because two workers racing on one symbol must not be able
  to wind the cache back.
* **``as_of`` and ``expires_at`` are different clocks.** ``as_of`` dates the
  data (the provider's own session stamp, taken from its observations) and
  ``expires_at`` is ``fetched_at + TTL_S``. A row built from yesterday's
  fundamentals is old data, not an old *fetch*: measuring the window from
  ``as_of`` would expire a fresh fetch on arrival.
* **An absent cell is ``null``, never ``0``.** The stored JSON is full width
  (all 22 keys, missing ones ``null``) so a reader can tell "the provider has no
  P/E for this ETF" from "this key is not part of the vocabulary". A non-finite
  number (``NaN``/``inf``) is absent for the same reason a blank is.
* **A failed build writes nothing.** An all-rungs-failed walk, an empty body, or
  a payload that served none of the 22 fields leaves the previous row (if any)
  standing and reports ``failed``/``empty`` with the ladder verdict. Writing a
  row of nulls would claim an answer nobody gave, and deleting the old row would
  turn a transient outage into data loss.
* **Coverage is measured, never claimed.** :func:`coverage_report` counts the
  filled cells of the rows that are still fresh, per field, and reports the
  source histogram and the fresh/expired split. A schema with 22 keys is not a
  coverage claim; this function is what can be quoted.
* **A batch can be bounded by time.** ``build_many(..., time_budget_s=...)``
  stops *starting* walks when the budget expires; the untouched tail is
  ``deferred`` — not failed, not written — and ``skip_fresh`` resumes it on the
  next run. Walks already in flight are drained, so ``requests`` counts what the
  provider was really asked for. This is the P11C bulk mode's stop line; the
  symbols themselves come from :mod:`app.fundamentals.universe`, which scans the
  screen engine's own US universe instead of keeping a second list.

The ladder is injected (a :class:`FundamentalsLadder`, i.e. anything with
``async fetch(capability, instrument, fields)``). The builder never constructs a
provider, never reads a credential and never retries on its own — that policy
belongs to :class:`app.providers.ladder.Ladder`, which the composition root
(``Runtime.ladder``) builds.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence

from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.contracts import CANONICAL_COLUMN_GROUPS, Capability
from app.db import build_engine, ensure_schema_async
from app.providers.base import InstrumentRef

logger = logging.getLogger("mee.fundamentals")

#: The 22 cells a fundamentals snapshot holds, in contract order. Derived from
#: the one column table so the snapshot cannot drift from the export contract.
_GROUPS: dict[str, tuple[str, ...]] = dict(CANONICAL_COLUMN_GROUPS)
FUNDAMENTAL_FIELDS: tuple[str, ...] = _GROUPS["Fundamentals"] + _GROUPS["Statements"]
_FIELD_SET: frozenset[str] = frozenset(FUNDAMENTAL_FIELDS)

if len(FUNDAMENTAL_FIELDS) != 22 or len(_FIELD_SET) != 22:  # pragma: no cover - contract guard
    raise RuntimeError(
        "the fundamentals vocabulary is 22 unique columns, got "
        f"{len(FUNDAMENTAL_FIELDS)} entries / {len(_FIELD_SET)} unique"
    )

#: A fundamentals picture is a slow picture: 24 hours, matching the provider
#: cache's own fundamentals TTL so the two layers cannot disagree.
TTL_S = 86_400

#: Build outcomes. ``cached`` is a fresh row that answered without a walk.
COMPLETE = "complete"
PARTIAL = "partial"
CACHED = "cached"
EMPTY = "empty"
FAILED = "failed"
STATUSES: frozenset[str] = frozenset({COMPLETE, PARTIAL, CACHED, EMPTY, FAILED})

#: Verdict kinds that mean "the schema answered, the data is absent" — a
#: symbol with no fundamentals, not an outage. Anything else is a failure.
EMPTY_VERDICTS: frozenset[str] = frozenset({"empty", "not_found"})

#: SQLite is single-writer. A second connection waits (``busy_timeout``) and a
#: wait that overruns is retried, exactly like the screen store.
_LOCK_RETRIES = 8
_LOCK_BACKOFF_S = 0.02

#: ``_parse_stamp``'s answer for a row whose timestamp cannot be read. A row
#: that cannot prove its age is expired, which is the safe direction.
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

#: The one table this module owns (``0003_fundamentals``).
TABLE = "fundamentals_snapshots"


class FundamentalsSchemaError(RuntimeError):
    """The database has no ``fundamentals_snapshots`` table (not at 0003 yet).

    ``ensure_schema`` bootstraps an *empty* file and deliberately does nothing to
    a database that already has the job tables — a long-lived service must not
    migrate itself on a read. An existing database is therefore upgraded through
    Alembic, by the CLI (``apply_migrations``) or by an operator.
    """


def migration_hint(db_path: str | Path) -> str:
    """The exact command that brings ``db_path`` to head."""

    return (
        "alembic -x db="
        f"{Path(db_path).resolve()} upgrade head"
        f"  (or: python scripts/fundamentals-snapshot.py --db {Path(db_path).resolve()} --report-only)"
    )


def apply_migrations(db_path: str | Path) -> str:
    """Run ``alembic upgrade head`` on ``db_path``; returns the resulting revision.

    This is the deployment/CLI step, not a runtime one, and it goes through
    Alembic's own script directory — there is no second schema definition here.
    """

    from alembic import command
    from alembic.script import ScriptDirectory

    from app.db import alembic_config

    config = alembic_config(db_path)
    command.upgrade(config, "head")
    head = ScriptDirectory.from_config(config).get_current_head()
    return str(head or "")



class FundamentalsLadder(Protocol):
    """The one collaborator the builder needs: a capability-scoped ladder."""

    async def fetch(
        self, capability: Capability, instrument: Any, fields: set[str]
    ) -> Any:  # pragma: no cover - structural type
        ...


# ---------------------------------------------------------------------------
# Symbols and time
# ---------------------------------------------------------------------------


def utc_now() -> datetime:
    return datetime.now(UTC)


def canonical_symbol(symbol: str) -> str:
    """The storage key: trimmed and upper-cased (``" brk.b "`` → ``"BRK.B"``).

    One symbol is one row, so the key may not depend on how a caller typed it.
    A blank token is a caller error rather than a row keyed on ``""``.
    """

    text_symbol = str(symbol or "").strip().upper()
    if not text_symbol:
        raise ValueError("a fundamentals snapshot needs a non-empty symbol")
    return text_symbol


def provider_symbol(symbol: str) -> str:
    """The provider's spelling: a US class share is ``BRK-B``, not ``BRK.B``.

    Yahoo (and Finviz) write a single-letter dot suffix as a share class with a
    hyphen. Foreign suffixes (``2330.TW``) are left alone — this module does not
    own exchange-suffix mapping, the screen layer does.
    """

    text_symbol = canonical_symbol(symbol)
    if text_symbol.count(".") == 1:
        head, suffix = text_symbol.split(".", 1)
        if len(suffix) == 1 and suffix.isalpha():
            return f"{head}-{suffix}"
    return text_symbol


def instrument_for(symbol: str) -> InstrumentRef:
    """The ladder target for a display symbol (identity decided by ``from_symbol``)."""

    return InstrumentRef.from_symbol(provider_symbol(symbol))


def iso(moment: datetime) -> str:
    """Aware UTC with microseconds: lexical order equals chronological order."""

    if moment.tzinfo is None:
        raise ValueError("naive datetimes are not allowed in the fundamentals store")
    return moment.astimezone(UTC).isoformat(timespec="microseconds")


def _parse_stamp(value: Any, *, fallback: datetime | None = None) -> datetime:
    """Read a stored/observed timestamp as aware UTC; ``fallback`` otherwise."""

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
            # A date-only provider stamp has no zone; UTC midnight is the only
            # reading that is not a guess about the venue's calendar.
            moment = moment.replace(tzinfo=UTC)
        return moment.astimezone(UTC)
    return fallback or _EPOCH


# ---------------------------------------------------------------------------
# The snapshot
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FundamentalsSnapshot:
    """One symbol's fundamentals, with the clocks and the source that own it."""

    symbol: str
    instrument_id: str
    fields: Mapping[str, Any]
    source: str | None
    as_of: datetime
    fetched_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        if not str(self.symbol).strip():
            raise ValueError("a fundamentals snapshot needs a non-empty symbol")
        unknown = sorted(set(self.fields) - _FIELD_SET)
        if unknown:
            raise ValueError(
                f"unknown fundamentals field(s) {unknown}; the vocabulary is "
                f"{len(FUNDAMENTAL_FIELDS)} canonical columns"
            )
        for name, value in self.fields.items():
            if isinstance(value, bool):
                raise ValueError(f"{self.symbol}: {name} is a bool, not a fundamental cell")
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError(f"{self.symbol}: {name} is not a finite number")
            if value is not None and not isinstance(value, (int, float, str)):
                raise ValueError(
                    f"{self.symbol}: {name} is {type(value).__name__}, not a scalar cell"
                )

    @property
    def field_count(self) -> int:
        """How many of the 22 cells carry a value."""

        return sum(1 for name in FUNDAMENTAL_FIELDS if self.fields.get(name) is not None)

    @property
    def missing_fields(self) -> tuple[str, ...]:
        return tuple(name for name in FUNDAMENTAL_FIELDS if self.fields.get(name) is None)

    @property
    def complete(self) -> bool:
        """True only when every vocabulary cell is filled — never inferred."""

        return self.field_count == len(FUNDAMENTAL_FIELDS)

    def is_fresh(self, now: datetime) -> bool:
        return now < self.expires_at

    def is_stale(self, now: datetime) -> bool:
        return not self.is_fresh(now)

    def as_dict(self, *, now: datetime | None = None) -> dict[str, Any]:
        """A JSON-safe view; ``stale`` is only answered when ``now`` is given."""

        document: dict[str, Any] = {
            "symbol": self.symbol,
            "instrument_id": self.instrument_id,
            "source": self.source,
            "as_of": iso(self.as_of),
            "fetched_at": iso(self.fetched_at),
            "expires_at": iso(self.expires_at),
            "field_count": self.field_count,
            "complete": self.complete,
            "fields": {name: self.fields.get(name) for name in FUNDAMENTAL_FIELDS},
        }
        if now is not None:
            document["stale"] = self.is_stale(now)
        return document


def _cell_value(observation: Mapping[str, Any]) -> Any:
    """One observation's value: a finite number, else non-empty text, else ``None``."""

    number = observation.get("value_num")
    if isinstance(number, bool):
        number = None
    if isinstance(number, (int, float)):
        value = float(number)
        if math.isfinite(value):
            return value
    text_value = observation.get("value_text")
    if isinstance(text_value, str):
        stripped = text_value.strip()
        if stripped:
            return stripped
    return None


def snapshot_from_observations(
    observations: Iterable[Mapping[str, Any]],
    *,
    symbol: str,
    instrument_id: str,
    source: str | None,
    fetched_at: datetime,
    ttl_s: int = TTL_S,
) -> FundamentalsSnapshot:
    """Merge a walk's observations into one full-width snapshot.

    Only the 22 vocabulary fields are kept; the rest of the provider payload
    (``quote_as_of``, ``currency``, technicals) belongs to other capabilities and
    must not be smuggled in. ``as_of`` is the newest observation clock — the
    provider's own stamp for the cells this row holds — and falls back to
    ``fetched_at`` when no observation carried one.
    """

    if ttl_s <= 0:
        raise ValueError("ttl_s must be positive")
    cells: dict[str, Any] = {}
    stamps: list[datetime] = []
    for observation in observations:
        if not isinstance(observation, Mapping):
            continue
        name = str(observation.get("field") or "")
        if name not in _FIELD_SET:
            continue
        value = _cell_value(observation)
        if value is None:
            continue
        cells[name] = value
        # A missing/unreadable stamp contributes nothing: falling back to the
        # *fetch* clock here would let a provider omission make old data look new.
        stamp = _parse_stamp(observation.get("as_of"))
        if stamp != _EPOCH:
            stamps.append(stamp)
    as_of = max(stamps) if stamps else fetched_at
    fields = {name: cells.get(name) for name in FUNDAMENTAL_FIELDS}
    return FundamentalsSnapshot(
        symbol=canonical_symbol(symbol),
        instrument_id=instrument_id,
        fields=fields,
        source=source,
        as_of=as_of,
        fetched_at=fetched_at,
        expires_at=fetched_at + timedelta(seconds=int(ttl_s)),
    )


# ---------------------------------------------------------------------------
# Build results
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BuildResult:
    """What one symbol's build did, in the vocabulary a log line can carry."""

    symbol: str
    status: str
    instrument_id: str
    snapshot: FundamentalsSnapshot | None = None
    written: bool = False
    rung: str | None = None
    verdict: str = "exhausted"
    filled: int = 0
    missing: int = len(FUNDAMENTAL_FIELDS)
    requests: int = 0
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.status not in STATUSES:  # pragma: no cover - programmer error
            raise ValueError(f"unknown build status {self.status!r}")

    @property
    def ok(self) -> bool:
        """True when a snapshot exists for this symbol (fresh or just built)."""

        return self.status in {COMPLETE, PARTIAL, CACHED}

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "status": self.status,
            "instrument_id": self.instrument_id,
            "written": self.written,
            "rung": self.rung,
            "verdict": self.verdict,
            "filled": self.filled,
            "missing": self.missing,
            "requests": self.requests,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class BuildReport:
    """One batch build: every result plus the counters that make it auditable.

    ``deferred`` is the tail a time budget did not start: those symbols have no
    result, were not written, and are exactly what the next run picks up. The
    arithmetic is closed by construction — ``requested == attempted +
    len(deferred)`` — so a bulk run can say "1,200 of 4,000 built, 2,800
    deferred" without pretending the untouched tail failed or succeeded.
    """

    results: tuple[BuildResult, ...]
    ttl_s: int
    dropped: tuple[str, ...] = ()
    #: Symbols the batch never started (time budget exhausted). No row was
    #: written for them, and no provider request was spent on them.
    deferred: tuple[str, ...] = ()
    started_at: datetime | None = None
    finished_at: datetime | None = None

    def _count(self, status: str) -> int:
        return sum(1 for item in self.results if item.status == status)

    @property
    def requested(self) -> int:
        """Everything the batch was asked to build: attempts plus the deferred tail."""

        return len(self.results) + len(self.deferred)

    @property
    def attempted(self) -> int:
        """Symbols a walk was actually started for."""

        return len(self.results)

    @property
    def complete(self) -> int:
        return self._count(COMPLETE)

    @property
    def partial(self) -> int:
        return self._count(PARTIAL)

    @property
    def cached(self) -> int:
        return self._count(CACHED)

    @property
    def empty(self) -> int:
        return self._count(EMPTY)

    @property
    def failed(self) -> int:
        return self._count(FAILED)

    @property
    def built(self) -> int:
        """Symbols a provider actually answered (complete or partial)."""

        return self.complete + self.partial

    @property
    def written(self) -> int:
        return sum(1 for item in self.results if item.written)

    @property
    def filled_cells(self) -> int:
        return sum(item.filled for item in self.results)

    @property
    def requests(self) -> int:
        """Transport requests the batch cost (``sum`` of the ladder's own count)."""

        return sum(item.requests for item in self.results)

    @property
    def elapsed_s(self) -> float | None:
        """Wall seconds the batch took, when both clocks were recorded."""

        if self.started_at is None or self.finished_at is None:
            return None
        return max(0.0, (self.finished_at - self.started_at).total_seconds())

    @property
    def eta_s(self) -> float | None:
        """Seconds the deferred tail would still need at this batch's own rate.

        A projection, not a promise: it divides the elapsed time by the symbols
        that were attempted and multiplies by the ones that were not. ``0`` when
        nothing was deferred, ``None`` when there is no rate to project from
        (nothing attempted, or no clocks).
        """

        if not self.deferred:
            return 0.0 if self.finished_at is not None else None
        elapsed = self.elapsed_s
        if elapsed is None or self.attempted <= 0:
            return None
        return (elapsed / self.attempted) * len(self.deferred)

    def as_dict(self) -> dict[str, Any]:
        document: dict[str, Any] = {
            "requested": self.requested,
            "attempted": self.attempted,
            "built": self.built,
            "written": self.written,
            "complete": self.complete,
            "partial": self.partial,
            "cached": self.cached,
            "empty": self.empty,
            "failed": self.failed,
            "filled_cells": self.filled_cells,
            "requests": self.requests,
            "ttl_s": self.ttl_s,
            "dropped": list(self.dropped),
            "deferred": list(self.deferred),
            "results": [item.as_dict() for item in self.results],
        }
        if self.started_at is not None:
            document["started_at"] = iso(self.started_at)
        if self.finished_at is not None:
            document["finished_at"] = iso(self.finished_at)
        if self.elapsed_s is not None:
            document["elapsed_s"] = self.elapsed_s
            document["eta_s"] = self.eta_s
        return document


# ---------------------------------------------------------------------------
# Coverage
# ---------------------------------------------------------------------------


def coverage_report(
    snapshots: Iterable[FundamentalsSnapshot], *, now: datetime, ttl_s: int = TTL_S
) -> dict[str, Any]:
    """Measure fill over the **fresh** rows; expired rows are counted, not claimed.

    ``fill_rate`` is ``filled_cells / (fresh rows × 22)`` and every per-field row
    carries its own denominator, so a report over three symbols and one over
    three thousand cannot be confused. Zero fresh rows is ``0.0`` with
    ``total=0``, not a division error and not an invented percentage.
    """

    rows = list(snapshots)
    fresh = [snapshot for snapshot in rows if snapshot.is_fresh(now)]
    fields: list[dict[str, Any]] = []
    for name in FUNDAMENTAL_FIELDS:
        filled = sum(1 for snapshot in fresh if snapshot.fields.get(name) is not None)
        fields.append(
            {
                "field": name,
                "filled": filled,
                "total": len(fresh),
                "coverage": (filled / len(fresh)) if fresh else 0.0,
            }
        )
    filled_cells = sum(snapshot.field_count for snapshot in fresh)
    total_cells = len(fresh) * len(FUNDAMENTAL_FIELDS)

    sources: dict[str, int] = {}
    for snapshot in fresh:
        if snapshot.source:
            sources[snapshot.source] = sources.get(snapshot.source, 0) + 1

    moments = [snapshot.as_of for snapshot in fresh]

    return {
        "generated_at": iso(now),
        "ttl_s": int(ttl_s),
        "symbols": len(rows),
        "fresh": len(fresh),
        "expired": len(rows) - len(fresh),
        "complete": sum(1 for snapshot in fresh if snapshot.complete),
        "partial": sum(1 for snapshot in fresh if 0 < snapshot.field_count < len(FUNDAMENTAL_FIELDS)),
        "empty": sum(1 for snapshot in fresh if snapshot.field_count == 0),
        "filled_cells": filled_cells,
        "total_cells": total_cells,
        "fill_rate": (filled_cells / total_cells) if total_cells else 0.0,
        "fields": fields,
        "sources": dict(sorted(sources.items())),
        "oldest_as_of": iso(min(moments)) if moments else None,
        "newest_as_of": iso(max(moments)) if moments else None,
    }


# ---------------------------------------------------------------------------
# SQLite store
# ---------------------------------------------------------------------------


class SqliteFundamentalsStore:
    """``fundamentals_snapshots``: one row per symbol on the service's SQLite file."""

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
        """Bootstrap an empty file, then prove the table exists (once per store).

        ``ensure_schema`` only migrates a database it created itself; an existing
        service database is upgraded through Alembic by the caller. Rather than
        letting the first write die on ``no such table``, this raises
        :class:`FundamentalsSchemaError` with the exact command to run.
        """

        async with self._ready:
            if self._verified:
                return
            await ensure_schema_async(self.db_path)
            if not await self._table_exists():
                raise FundamentalsSchemaError(
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

    async def _execute(
        self, statement: str, params: Mapping[str, Any] | None = None
    ) -> int:
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

    async def _all(self, statement: str, params: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
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

    # -- writes ------------------------------------------------------------

    async def upsert(self, snapshot: FundamentalsSnapshot) -> bool:
        """Insert or replace one symbol's row; ``False`` when it was older.

        The ``WHERE`` clause is the concurrency rule: ``fetched_at`` is monotonic
        per symbol, so a build that started earlier cannot overwrite a row that a
        later build already committed. Two identical writes are idempotent — the
        second one lands on the same values and the table still holds one row.

        A *newer* build always replaces the row, even when it carries fewer
        filled cells: the row is the current answer, not a high-water mark, and
        merging an old cell into a new snapshot would mix two clocks silently
        (the ``partial`` status and the coverage report make the loss visible).
        """

        moment = self._clock()
        payload = {
            "symbol": canonical_symbol(snapshot.symbol),
            "instrument_id": snapshot.instrument_id,
            "fields": json.dumps(
                {name: snapshot.fields.get(name) for name in FUNDAMENTAL_FIELDS},
                sort_keys=True,
                ensure_ascii=False,
                allow_nan=False,
            ),
            "source": snapshot.source,
            "as_of": iso(snapshot.as_of),
            "fetched_at": iso(snapshot.fetched_at),
            "expires_at": iso(snapshot.expires_at),
            "field_count": snapshot.field_count,
            "complete": 1 if snapshot.complete else 0,
            "updated_at": iso(moment),
        }
        return (
            await self._execute(
                """
                INSERT INTO fundamentals_snapshots (
                    symbol, instrument_id, fields, source, as_of, fetched_at,
                    expires_at, field_count, complete, updated_at
                ) VALUES (
                    :symbol, :instrument_id, :fields, :source, :as_of, :fetched_at,
                    :expires_at, :field_count, :complete, :updated_at
                )
                ON CONFLICT(symbol) DO UPDATE SET
                    instrument_id = excluded.instrument_id,
                    fields = excluded.fields,
                    source = excluded.source,
                    as_of = excluded.as_of,
                    fetched_at = excluded.fetched_at,
                    expires_at = excluded.expires_at,
                    field_count = excluded.field_count,
                    complete = excluded.complete,
                    updated_at = excluded.updated_at
                 WHERE excluded.fetched_at >= fundamentals_snapshots.fetched_at
                """,
                payload,
            )
            > 0
        )

    async def purge_expired(self, *, now: datetime | None = None) -> int:
        """Delete rows past their window; returns how many went."""

        return await self._execute(
            "DELETE FROM fundamentals_snapshots WHERE expires_at <= :now",
            {"now": iso(now or self._clock())},
        )

    # -- reads -------------------------------------------------------------

    async def get(
        self,
        symbol: str,
        *,
        now: datetime | None = None,
        include_expired: bool = False,
    ) -> FundamentalsSnapshot | None:
        """The snapshot for ``symbol``, or ``None`` when it is absent or expired."""

        key = canonical_symbol(symbol)
        row = await self._one("SELECT * FROM fundamentals_snapshots WHERE symbol = :symbol", {"symbol": key})
        if row is None:
            return None
        snapshot = _snapshot_from_row(row)
        if include_expired:
            return snapshot
        moment = now or self._clock()
        return snapshot if snapshot.is_fresh(moment) else None

    async def rows(self) -> list[FundamentalsSnapshot]:
        """Every stored row, whatever its age, ordered by symbol."""

        return [
            _snapshot_from_row(row)
            for row in await self._all("SELECT * FROM fundamentals_snapshots ORDER BY symbol")
        ]

    async def count(self) -> int:
        row = await self._one("SELECT COUNT(*) AS n FROM fundamentals_snapshots")
        return int((row or {}).get("n") or 0)

    async def coverage(self, *, now: datetime | None = None) -> dict[str, Any]:
        """The measured coverage of the stored table (fresh rows decide fill)."""

        return coverage_report(await self.rows(), now=now or self._clock(), ttl_s=self.ttl_s)

    async def _one(self, statement: str, params: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
        rows = await self._all(statement, params)
        return rows[0] if rows else None


def _snapshot_from_row(row: Mapping[str, Any]) -> FundamentalsSnapshot:
    """Decode one table row; a corrupt JSON cell becomes ``null``, not a crash."""

    raw = row.get("fields")
    cells: Mapping[str, Any] = {}
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:  # pragma: no cover - only a hand-edited row
            logger.warning("unreadable fundamentals JSON symbol=%s", row.get("symbol"))
            parsed = {}
        if isinstance(parsed, Mapping):
            cells = parsed
    fetched_at = _parse_stamp(row.get("fetched_at"), fallback=_EPOCH)
    expires_at = _parse_stamp(row.get("expires_at"), fallback=_EPOCH)
    stored = {
        name: cells.get(name)
        for name in FUNDAMENTAL_FIELDS
        if isinstance(cells.get(name), (int, float, str)) and not isinstance(cells.get(name), bool)
    }
    return FundamentalsSnapshot(
        symbol=str(row.get("symbol") or ""),
        instrument_id=str(row.get("instrument_id") or ""),
        fields={name: stored.get(name) for name in FUNDAMENTAL_FIELDS},
        source=(str(row["source"]) if row.get("source") is not None else None),
        as_of=_parse_stamp(row.get("as_of"), fallback=fetched_at),
        fetched_at=fetched_at,
        expires_at=expires_at,
    )


def _is_lock_error(exc: OperationalError) -> bool:
    message = str(exc).lower()
    return "locked" in message or "busy" in message


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


class FundamentalsSnapshotBuilder:
    """Walk ``Capability.FUNDAMENTALS`` per symbol and store one snapshot each."""

    def __init__(
        self,
        ladder: FundamentalsLadder,
        *,
        store: SqliteFundamentalsStore | None = None,
        ttl_s: int = TTL_S,
        clock: Any | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        if ttl_s <= 0:
            raise ValueError("ttl_s must be positive")
        self.ladder = ladder
        self.store = store
        self.ttl_s = int(ttl_s)
        self._clock = clock or utc_now
        #: The budget's clock. Wall clocks are for stamps; a *time budget* is
        #: elapsed real time, so the batch measures it on a monotonic clock.
        #: Tests inject one so a budget assertion is arithmetic, not a sleep.
        self._monotonic = monotonic or time.monotonic

    async def build(self, symbol: str, *, skip_fresh: bool = False) -> BuildResult:
        """Build (and store) one symbol; a provider failure is a status, not a raise.

        ``skip_fresh`` is the 24 h window in action: a row that is still fresh is
        returned as ``cached`` with **zero** provider requests. A blank symbol is
        still a caller error (there is nothing to look up), and a ladder that
        raises is reported as ``failed`` with the exception in ``reason`` rather
        than aborting a twenty-symbol run.
        """

        key = canonical_symbol(symbol)
        provider_ticker = provider_symbol(key)
        instrument = instrument_for(key)
        moment = self._clock()

        if skip_fresh and self.store is not None:
            existing = await self.store.get(key, now=moment)
            if existing is not None:
                return BuildResult(
                    symbol=key,
                    status=CACHED,
                    instrument_id=existing.instrument_id,
                    snapshot=existing,
                    written=False,
                    rung=existing.source,
                    verdict="cached",
                    filled=existing.field_count,
                    missing=len(FUNDAMENTAL_FIELDS) - existing.field_count,
                    requests=0,
                    reason=f"fresh row from {iso(existing.fetched_at)} is inside its {self.ttl_s}s window",
                )

        try:
            walked = await self.ladder.fetch(
                Capability.FUNDAMENTALS, instrument, set(FUNDAMENTAL_FIELDS)
            )
        except Exception as exc:  # noqa: BLE001 - one symbol must not sink the batch
            return BuildResult(
                symbol=key,
                status=FAILED,
                instrument_id=instrument.instrument_id,
                verdict="error",
                reason=f"{type(exc).__name__}: {exc}",
            )

        verdict = getattr(walked, "verdict", None)
        kind = str(getattr(verdict, "kind", "") or "exhausted")
        rung = getattr(walked, "rung", None)
        observations = list(getattr(walked, "observations", None) or [])
        requests = int(getattr(walked, "requests", 0) or 0)
        detail = getattr(verdict, "detail", None)
        ok = bool(getattr(verdict, "ok", False))

        if not ok or not observations:
            status = EMPTY if kind in EMPTY_VERDICTS else FAILED
            return BuildResult(
                symbol=key,
                status=status,
                instrument_id=instrument.instrument_id,
                rung=rung,
                verdict=kind,
                requests=requests,
                reason=(
                    f"{kind}: {detail}"
                    if detail
                    else f"no fundamentals observation ({kind})"
                ),
            )

        snapshot = snapshot_from_observations(
            observations,
            symbol=key,
            instrument_id=instrument.instrument_id,
            source=rung or str(observations[0].get("source") or "") or None,
            fetched_at=moment,
            ttl_s=self.ttl_s,
        )
        if snapshot.field_count == 0:
            # The rungs answered with the right schema but none of the 22 cells:
            # a row of nulls would claim an answer nobody gave.
            return BuildResult(
                symbol=key,
                status=EMPTY,
                instrument_id=instrument.instrument_id,
                rung=rung,
                verdict=kind,
                requests=requests,
                reason="the rungs answered but served none of the 22 fundamental fields",
            )

        written = await self.store.upsert(snapshot) if self.store is not None else False
        status = COMPLETE if snapshot.complete else PARTIAL
        reason = None
        if self.store is not None and not written:
            reason = "stored row is newer than this build; it was not overwritten"
        return BuildResult(
            symbol=key,
            status=status,
            instrument_id=snapshot.instrument_id,
            snapshot=snapshot,
            written=written,
            rung=snapshot.source,
            verdict=kind,
            filled=snapshot.field_count,
            missing=len(FUNDAMENTAL_FIELDS) - snapshot.field_count,
            requests=requests,
            reason=reason,
        )

    async def build_many(
        self,
        symbols: Sequence[str],
        *,
        concurrency: int = 4,
        skip_fresh: bool = True,
        time_budget_s: float | None = None,
        on_result: Callable[[BuildResult, int, int], None] | None = None,
    ) -> BuildReport:
        """Build a bounded batch, one row per distinct symbol, input order kept.

        The batch is the size the caller asked for (twenty symbols, not the whole
        universe): ``concurrency`` caps the in-flight walks because each walk is
        one provider request and the display only needs twenty rows. Blank tokens
        are reported in ``dropped`` instead of being looked up as ``""``.

        ``time_budget_s`` is the bulk mode's stop line, measured on the monotonic
        clock rather than the store's injected clock (a budget is real time). It
        stops **starting** walks: the queued symbols are cancelled (they never
        sent a request), reported in :attr:`BuildReport.deferred`, and never
        written, so the next run resumes them through ``skip_fresh``. Walks
        already in flight are drained rather than cancelled — their request was
        spent, so their result is kept and counted, which costs at most one walk
        per concurrency slot past the budget. ``0``/``None`` is no budget.

        ``on_result`` is called once per completed symbol with
        ``(result, completed, total)``, where ``total`` is the batch size; it is
        the progress seam and must not raise (the CLI's printer never does).
        """

        if concurrency < 1:
            raise ValueError("concurrency must be at least 1")
        if time_budget_s is not None and float(time_budget_s) < 0:
            raise ValueError(
                f"time_budget_s must be non-negative, got {time_budget_s!r}"
            )
        wanted: list[str] = []
        seen: set[str] = set()
        dropped: list[str] = []
        for raw in symbols:
            token = str(raw or "").strip()
            if not token:
                dropped.append(str(raw or ""))
                continue
            key = canonical_symbol(token)
            if key in seen:
                continue
            seen.add(key)
            wanted.append(key)

        started = self._clock()
        limiter = asyncio.Semaphore(concurrency)
        deadline = (
            self._monotonic() + float(time_budget_s) if time_budget_s else None
        )

        started_symbols: set[str] = set()

        async def one(symbol: str) -> BuildResult:
            async with limiter:
                started_symbols.add(symbol)
                return await self.build(symbol, skip_fresh=skip_fresh)

        tasks: dict[asyncio.Task[BuildResult], str] = {
            asyncio.create_task(one(symbol)): symbol for symbol in wanted
        }
        completed: dict[str, BuildResult] = {}
        pending: set[asyncio.Task[BuildResult]] = set(tasks)
        stopped = False

        def record(result: BuildResult) -> None:
            completed[result.symbol] = result
            if on_result is not None:
                on_result(result, len(completed), len(wanted))

        async def drain(*, keep_started: bool) -> None:
            """Unblock the batch: cancel the queued walks, keep the in-flight ones.

            A walk that already started has spent its request; cancelling it
            throws away a result the batch paid for. Draining it instead keeps
            the counters honest (``requests`` is what the provider was actually
            asked for) and costs at most one walk per concurrency slot past the
            budget. A queued walk never started, so cancelling it is free.
            """

            queued = [
                task for task in pending
                if not (keep_started and tasks[task] in started_symbols)
            ]
            held = [task for task in pending if task not in queued]
            for task in queued:
                task.cancel()
            if queued:
                await asyncio.gather(*queued, return_exceptions=True)
            if held:
                outcomes = await asyncio.gather(*held, return_exceptions=True)
                for task, outcome in zip(held, outcomes):
                    if isinstance(outcome, BuildResult):
                        record(outcome)
                    elif not isinstance(outcome, asyncio.CancelledError):
                        logger.warning(
                            "a drained fundamentals walk failed for %s: %r",
                            tasks[task],
                            outcome,
                        )

        try:
            while pending:
                timeout: float | None = None
                if deadline is not None:
                    timeout = deadline - self._monotonic()
                    if timeout <= 0:
                        # The budget expired with walks still queued.
                        stopped = True
                        break
                finished, pending = await asyncio.wait(
                    pending, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
                )
                if not finished:
                    stopped = True
                    break
                for task in finished:
                    record(task.result())
        finally:
            if pending:
                await drain(keep_started=stopped)

        results = tuple(
            completed[symbol] for symbol in wanted if symbol in completed
        )
        deferred = tuple(symbol for symbol in wanted if symbol not in completed)
        return BuildReport(
            results=results,
            ttl_s=self.ttl_s,
            dropped=tuple(dropped),
            deferred=deferred,
            started_at=started,
            finished_at=self._clock(),
        )


__all__ = [
    "CACHED",
    "COMPLETE",
    "EMPTY",
    "EMPTY_VERDICTS",
    "FAILED",
    "FUNDAMENTAL_FIELDS",
    "PARTIAL",
    "STATUSES",
    "TABLE",
    "TTL_S",
    "BuildReport",
    "BuildResult",
    "FundamentalsLadder",
    "FundamentalsSchemaError",
    "FundamentalsSnapshot",
    "FundamentalsSnapshotBuilder",
    "SqliteFundamentalsStore",
    "apply_migrations",
    "canonical_symbol",
    "coverage_report",
    "instrument_for",
    "iso",
    "migration_hint",
    "provider_symbol",
    "snapshot_from_observations",
    "utc_now",
]
