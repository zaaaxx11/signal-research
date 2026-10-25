"""Merge the live sources into the row the store already holds (P11F-C).

The store answers *one* question per symbol today: whichever walk finished last
owns the row. P11D's scan fills sixteen of the twenty-two cells, the Yahoo walk
can fill all twenty-two but is rate-limited, SEC has the eight statement cells
nobody else derives, and Finviz has the forward-looking multiples the scan never
serves. Each is right about a different half of the row, and a store with room
for one row per symbol has to *combine* them instead of choosing between them —
which is exactly what :func:`app.fundamentals.provenance.merge_snapshots` does,
and what this module drives.

The rules this module exists to keep:

* **Four sources, one row.** For every symbol the stored row (``tradingview``
  after a scan, ``yahoo`` after a walk) is read first, then ``sec_edgar``,
  ``yahoo`` and ``finviz`` are fetched through **injected adapters** — never
  constructed here, never read from a credential, never retried on our own. Only
  a source whose verdict says ``ok`` and whose parse yields a cell is merged; a
  blocked/throttled/empty answer contributes nothing instead of raising.
* **The stored row is the base, not a casualty.** A fresh stored row keeps the
  cells it already filled: the merge buys the *holes* the second, third and
  fourth source can serve, and does not churn sixteen TradingView values into
  near-identical Yahoo ones (the row a screen already shows must not change
  value because a merge ran). A fresh, named base therefore outranks every live
  source for its own cells; the live sources are then walked in
  :data:`DEFAULT_PRIORITY` order — SEC, Yahoo, Finviz — to fill what is left.
  An *expired* base is still read (its cells are the only answer some symbols
  have) but it ranks **last**: stale data may fill a hole, never win a cell a
  live source also served.
* **``field_sources`` is the point.** Every filled cell of the merged row names
  the source that supplied it (``tradingview``/``sec_edgar``/``yahoo``/
  ``finviz``), so the row says ``source='multi'`` and *which number came from
  where*: the claim a single-source screener cannot make. The provenance comes
  from :mod:`app.fundamentals.provenance`; this module never labels a cell
  itself.
* **Holes stay holes.** A cell no source served stays ``None`` — never ``0``,
  never a copy of a neighbouring cell, never a default. A symbol where nothing
  at all was merged is not written: a row of nulls would claim an answer nobody
  gave, and the previous row would be lost for nothing.
* **A failed build writes nothing.** Every adapter error, every refusal and
  every empty page is recorded per source in :class:`SourceAttempt` and the
  stored row keeps standing. That is why the result carries ``before`` and
  ``added``: a run says how many cells existed, how many the merge added, and
  which source supplied each one, instead of only the final total.
* **Only an actual improvement is written.** A merge that says exactly what the
  row already said is reported ``kept`` and *not* written: rewriting an identical
  row would extend its 24 h window by one fetch with no evidence behind it. The
  fetch clock moves when the answer moved.
* **``skip_fresh`` is the 24 h window.** A fresh stored row is an answer and
  costs zero requests; ``--refresh`` is what re-merges a fresh row. The window
  itself is the snapshot contract's (``fetched_at + TTL_S``), not a second one.

Nothing here opens a socket: the adapters are the injected seam (the CLI passes
``Runtime.adapters()``), and the only clock is the one the caller injects, so a
test is arithmetic rather than a sleep.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Callable, Mapping, Protocol, Sequence

from app.contracts import Capability
from app.fundamentals.provenance import (
    DEFAULT_PRIORITY,
    field_source_counts,
    merge_snapshots,
    source_label,
)
from app.fundamentals.snapshot import (
    CACHED,
    COMPLETE,
    EMPTY,
    EMPTY_VERDICTS,
    FAILED,
    FUNDAMENTAL_FIELDS,
    PARTIAL,
    TTL_S,
    FundamentalsSnapshot,
    SqliteFundamentalsStore,
    canonical_symbol,
    coverage_report,
    instrument_for,
    iso,
    snapshot_from_observations,
    utc_now,
)
from app.providers.base import InstrumentRef

logger = logging.getLogger("mee.fundamentals.merged")

#: The sources fetched live, in the order they fill the base's holes. It is
#: :data:`DEFAULT_PRIORITY` minus ``tradingview``: TradingView is not fetched
#: here — it is the row the store already holds (P11D's scan wrote it), and it
#: enters the merge as the base.
MERGED_SOURCES: tuple[str, ...] = ("sec_edgar", "yahoo", "finviz")

#: The label of the row this mode *extends*. Documentation only: the base is
#: whatever the store holds, whatever its ``source`` says.
STORED_SOURCE = "tradingview"

#: One attempt's status. ``ok`` served a cell, ``empty``/``failed`` did not (the
#: snapshot statuses are reused so a report has one vocabulary), ``skipped``
#: means no adapter was wired for that name — a configuration gap, not an
#: outage, and therefore never counted as a failure.
OK = "ok"
SKIPPED = "skipped"
ATTEMPT_STATUSES: frozenset[str] = frozenset({OK, EMPTY, FAILED, SKIPPED})

#: A merged row that already answered and gained nothing: the stored row stands,
#: nothing was written. Distinct from ``cached`` (a fresh row that cost zero
#: requests because ``skip_fresh`` was on) — here every source *was* asked and
#: the answer was already in the row. ``scan`` mode calls the same idea ``kept``.
KEPT = "kept"


class FundamentalsAdapter(Protocol):
    """The three calls a provider adapter must answer (``app.providers`` shape).

    Structural on purpose: the builder never imports an adapter class, so a test
    double and ``SecEdgarAdapter`` are the same collaborator.
    """

    name: str

    async def fetch(
        self, capability: Capability, instrument: InstrumentRef, fields: set[str]
    ) -> Mapping[str, Any]:  # pragma: no cover - structural type
        ...

    def verdict(
        self, raw: Mapping[str, Any], instrument: InstrumentRef
    ) -> Any:  # pragma: no cover - structural type
        ...

    def parse(
        self, raw: Mapping[str, Any], instrument: InstrumentRef
    ) -> Sequence[Mapping[str, Any]]:  # pragma: no cover - structural type
        ...


# ---------------------------------------------------------------------------
# One source, one symbol
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SourceAttempt:
    """What one source did for one symbol, in the vocabulary a log line carries."""

    source: str
    status: str
    verdict: str = "exhausted"
    filled: int = 0
    requests: int = 0
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.status not in ATTEMPT_STATUSES:  # pragma: no cover - programmer error
            raise ValueError(f"unknown attempt status {self.status!r}")

    @property
    def served(self) -> bool:
        """True when this source put at least one cell into the merge."""

        return self.status == OK and self.filled > 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "status": self.status,
            "verdict": self.verdict,
            "filled": self.filled,
            "requests": self.requests,
            "reason": self.reason,
        }


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MergedResult:
    """One symbol's merge: the row, the numbers behind it and every attempt.

    ``before`` is the stored row's fill and ``added`` the cells the merge put in
    that the row did not have — the two numbers a run has to quote to prove it
    added something rather than just rewrote the row. ``kept`` says a stored row
    survived this build: ``cached`` (``skip_fresh`` answered), ``kept`` (every
    source was asked and the row already said it all) or a failure that wrote
    nothing. ``snapshot`` is then the old row, not a new one.
    """

    symbol: str
    status: str
    instrument_id: str = ""
    snapshot: FundamentalsSnapshot | None = None
    written: bool = False
    filled: int = 0
    missing: int = len(FUNDAMENTAL_FIELDS)
    #: Cells the stored row already carried when this build started.
    before: int = 0
    #: Cells this build added that the stored row did not carry.
    added: int = 0
    kept: bool = False
    sources: tuple[SourceAttempt, ...] = ()
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.status not in {COMPLETE, PARTIAL, CACHED, KEPT, EMPTY, FAILED}:
            raise ValueError(f"unknown merged status {self.status!r}")  # pragma: no cover

    @property
    def ok(self) -> bool:
        """True when a row answers for this symbol (built, cached or kept)."""

        return self.status in {COMPLETE, PARTIAL, CACHED, KEPT}

    @property
    def requests(self) -> int:
        return sum(attempt.requests for attempt in self.sources)

    @property
    def cell_sources(self) -> dict[str, str]:
        """``{field: source}`` for the filled cells of this row — no nulls, no guesses."""

        if self.snapshot is None:
            return {}
        return {
            name: label
            for name in FUNDAMENTAL_FIELDS
            if self.snapshot.fields.get(name) is not None
            and (label := source_label(self.snapshot.field_sources.get(name))) is not None
        }

    @property
    def cells_by_source(self) -> dict[str, int]:
        """``{source: cells}`` for this row: the source mix of one symbol."""

        counts: dict[str, int] = {}
        for label in self.cell_sources.values():
            counts[label] = counts.get(label, 0) + 1
        return dict(sorted(counts.items()))

    def as_dict(self) -> dict[str, Any]:
        document: dict[str, Any] = {
            "symbol": self.symbol,
            "status": self.status,
            "instrument_id": self.instrument_id,
            "written": self.written,
            "filled": self.filled,
            "missing": self.missing,
            "before": self.before,
            "added": self.added,
            "kept": self.kept,
            "requests": self.requests,
            "sources": [attempt.as_dict() for attempt in self.sources],
            "cells_by_source": self.cells_by_source,
            "reason": self.reason,
        }
        if self.snapshot is not None:
            document["source"] = self.snapshot.source
            document["as_of"] = iso(self.snapshot.as_of)
            document["fetched_at"] = iso(self.snapshot.fetched_at)
            document["fields"] = {
                name: self.snapshot.fields.get(name) for name in FUNDAMENTAL_FIELDS
            }
            document["field_sources"] = {
                name: self.snapshot.field_sources.get(name) for name in FUNDAMENTAL_FIELDS
            }
        return document


@dataclass(frozen=True, slots=True)
class MergedReport:
    """One merged batch: every result plus the counters that make it auditable.

    ``added_cells`` is the headline the mode exists for: cells the merge put in
    that the row did not already carry. ``cell_sources`` is the same population
    counted by **source** (cells, not rows — one merged row holds several), so
    "how much did we fill" and "who filled it" always describe one set of rows.
    """

    results: tuple[MergedResult, ...]
    ttl_s: int
    dropped: tuple[str, ...] = ()
    #: Symbols a time budget never started: no request, no write, resumed later.
    deferred: tuple[str, ...] = ()
    started_at: datetime | None = None
    finished_at: datetime | None = None

    def _count(self, status: str) -> int:
        return sum(1 for item in self.results if item.status == status)

    @property
    def requested(self) -> int:
        return len(self.results) + len(self.deferred)

    @property
    def attempted(self) -> int:
        return len(self.results)

    @property
    def built(self) -> int:
        return self.complete + self.partial

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
    def kept(self) -> int:
        return self._count(KEPT)

    @property
    def empty(self) -> int:
        return self._count(EMPTY)

    @property
    def failed(self) -> int:
        return self._count(FAILED)

    @property
    def written(self) -> int:
        return sum(1 for item in self.results if item.written)

    @property
    def filled_cells(self) -> int:
        return sum(item.filled for item in self.results)

    @property
    def before_cells(self) -> int:
        return sum(item.before for item in self.results)

    @property
    def added_cells(self) -> int:
        return sum(item.added for item in self.results)

    @property
    def requests(self) -> int:
        return sum(item.requests for item in self.results)

    @property
    def elapsed_s(self) -> float | None:
        if self.started_at is None or self.finished_at is None:
            return None
        return max(0.0, (self.finished_at - self.started_at).total_seconds())

    @property
    def eta_s(self) -> float | None:
        """Seconds the deferred tail would still need at this batch's own rate."""

        if not self.deferred:
            return 0.0 if self.finished_at is not None else None
        elapsed = self.elapsed_s
        if elapsed is None or self.attempted <= 0:
            return None
        return (elapsed / self.attempted) * len(self.deferred)

    def attempt_statuses(self) -> dict[str, dict[str, int]]:
        """``{source: {status: count}}`` over every attempt in the batch."""

        out: dict[str, dict[str, int]] = {}
        for item in self.results:
            for attempt in item.sources:
                row = out.setdefault(attempt.source, {})
                row[attempt.status] = row.get(attempt.status, 0) + 1
        return {source: dict(sorted(row.items())) for source, row in sorted(out.items())}

    def cell_sources(self) -> dict[str, int]:
        """Cells per source over the rows this batch **wrote** (not the stored rows)."""

        snapshots = [
            item.snapshot
            for item in self.results
            if item.written and item.snapshot is not None
        ]
        counts = field_source_counts(
            snapshot for snapshot in snapshots if snapshot is not None
        )
        return dict(counts["cells"])

    def coverage(self) -> dict[str, Any]:
        """Coverage of the rows this batch wrote — measured, not the store's."""

        written: list[FundamentalsSnapshot] = [
            item.snapshot
            for item in self.results
            if item.written and item.snapshot is not None
        ]
        now = self.finished_at or utc_now()
        return coverage_report(written, now=now, ttl_s=self.ttl_s)

    def as_dict(self) -> dict[str, Any]:
        document: dict[str, Any] = {
            "requested": self.requested,
            "attempted": self.attempted,
            "built": self.built,
            "written": self.written,
            "complete": self.complete,
            "partial": self.partial,
            "cached": self.cached,
            "kept": self.kept,
            "empty": self.empty,
            "failed": self.failed,
            "filled_cells": self.filled_cells,
            "before_cells": self.before_cells,
            "added_cells": self.added_cells,
            "requests": self.requests,
            "ttl_s": self.ttl_s,
            "dropped": list(self.dropped),
            "deferred": list(self.deferred),
            "attempts": self.attempt_statuses(),
            "cell_sources": self.cell_sources(),
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
# The builder
# ---------------------------------------------------------------------------


class MergedFundamentalsBuilder:
    """Read the stored row, fetch the live sources, merge cell by cell, upsert."""

    def __init__(
        self,
        adapters: Mapping[str, Any],
        *,
        store: SqliteFundamentalsStore | None = None,
        ttl_s: int = TTL_S,
        clock: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] | None = None,
        sources: Sequence[str] = MERGED_SOURCES,
        priority: Sequence[str] = DEFAULT_PRIORITY,
    ) -> None:
        if ttl_s <= 0:
            raise ValueError("ttl_s must be positive")
        self.adapters: dict[str, Any] = dict(adapters)
        self.store = store
        self.ttl_s = int(ttl_s)
        self.sources: tuple[str, ...] = tuple(
            name for name in (source_label(item) for item in sources) if name
        )
        self.priority: tuple[str, ...] = tuple(
            name for name in (source_label(item) for item in priority) if name
        )
        self._clock = clock or utc_now
        #: A time budget is elapsed real time, so it is measured on a monotonic
        #: clock; tests inject one so the assertion is arithmetic.
        self._monotonic = monotonic or time.monotonic

    # -- one source --------------------------------------------------------

    async def _fetch_source(
        self,
        name: str,
        symbol: str,
        instrument: InstrumentRef,
        moment: datetime,
    ) -> tuple[SourceAttempt, FundamentalsSnapshot | None]:
        """Fetch, judge and parse one source; never raises for a provider's fault.

        The verdict is checked **before** the parse: a ``throttled`` or ``blocked``
        body is not a smaller answer, it is no answer, and parsing it would turn a
        refusal into a row of coincidences.
        """

        adapter = self.adapters.get(name)
        if adapter is None:
            return (
                SourceAttempt(
                    source=name,
                    status=SKIPPED,
                    verdict="unavailable",
                    reason=f"no adapter is wired for {name!r}",
                ),
                None,
            )
        try:
            raw = await adapter.fetch(
                Capability.FUNDAMENTALS, instrument, set(FUNDAMENTAL_FIELDS)
            )
        except Exception as exc:  # noqa: BLE001 - one source must not sink a symbol
            return (
                SourceAttempt(
                    source=name,
                    status=FAILED,
                    verdict="error",
                    requests=1,
                    reason=f"{type(exc).__name__}: {exc}",
                ),
                None,
            )

        try:
            verdict = adapter.verdict(raw, instrument)
        except Exception as exc:  # noqa: BLE001 - a broken judge is a failed source
            return (
                SourceAttempt(
                    source=name,
                    status=FAILED,
                    verdict="error",
                    requests=1,
                    reason=f"verdict raised {type(exc).__name__}: {exc}",
                ),
                None,
            )
        detail = getattr(verdict, "detail", None)
        kind = str(getattr(verdict, "kind", "") or "exhausted")
        if not bool(getattr(verdict, "ok", False)):
            status = EMPTY if kind in EMPTY_VERDICTS else FAILED
            return (
                SourceAttempt(
                    source=name,
                    status=status,
                    verdict=kind,
                    requests=1,
                    reason=f"{kind}: {detail}" if detail else kind,
                ),
                None,
            )

        try:
            observations = list(adapter.parse(raw, instrument) or ())
        except Exception as exc:  # noqa: BLE001 - a schema surprise is a failed source
            return (
                SourceAttempt(
                    source=name,
                    status=FAILED,
                    verdict="error",
                    requests=1,
                    reason=f"parse raised {type(exc).__name__}: {exc}",
                ),
                None,
            )

        try:
            snapshot = snapshot_from_observations(
                observations,
                symbol=symbol,
                instrument_id=instrument.instrument_id,
                # The adapter's own name, so the row label and the per-cell
                # labels agree; the map key is the registry name they share.
                source=source_label(getattr(adapter, "name", None)) or name,
                fetched_at=moment,
                ttl_s=self.ttl_s,
            )
        except Exception as exc:  # noqa: BLE001 - a bad cell is that source's fault
            return (
                SourceAttempt(
                    source=name,
                    status=FAILED,
                    verdict="error",
                    requests=1,
                    reason=f"{type(exc).__name__}: {exc}",
                ),
                None,
            )

        if snapshot.field_count == 0:
            return (
                SourceAttempt(
                    source=name,
                    status=EMPTY,
                    verdict=kind,
                    requests=1,
                    reason="answered but served none of the 22 cells",
                ),
                None,
            )
        return (
            SourceAttempt(
                source=name,
                status=OK,
                verdict=kind,
                filled=snapshot.field_count,
                requests=1,
            ),
            snapshot,
        )

    # -- the merge order ---------------------------------------------------

    def _order(self, stored: FundamentalsSnapshot | None, moment: datetime) -> tuple[str, ...]:
        """The priority list for this symbol: the base first, or last when stale.

        A fresh stored row ranks **above** every live source for the cells it
        already filled (this mode fills holes; it does not re-litigate a value
        the screen is already showing). An expired one ranks after every live
        source so stale data can only fill a hole. An unlabelled stored row
        cannot outrank a named source — there is nothing to rank — and is left to
        the merge's own "unnamed sources last" rule.
        """

        return merge_priority(stored, now=moment, priority=self.priority)

    # -- one symbol --------------------------------------------------------

    async def build(self, symbol: str, *, skip_fresh: bool = False) -> MergedResult:
        """Merge one symbol and store the result; a provider failure is a status.

        ``skip_fresh`` returns a fresh stored row as ``cached`` with **zero**
        requests — the 24 h window in action. ``--refresh`` (``skip_fresh=False``)
        is what re-merges a fresh row, and even then the stored row's cells are
        the base, so a refresh can only add cells.
        """

        key = canonical_symbol(symbol)
        instrument = instrument_for(key)
        moment = self._clock()
        stored = (
            await self.store.get(key, now=moment, include_expired=True)
            if self.store is not None
            else None
        )

        if skip_fresh and stored is not None and stored.is_fresh(moment):
            return MergedResult(
                symbol=key,
                status=CACHED,
                instrument_id=stored.instrument_id,
                snapshot=stored,
                filled=stored.field_count,
                missing=len(FUNDAMENTAL_FIELDS) - stored.field_count,
                before=stored.field_count,
                kept=True,
                reason=(
                    f"fresh {stored.source or 'unknown'} row from "
                    f"{iso(stored.fetched_at)} is inside its {self.ttl_s}s window"
                ),
            )

        attempts: list[SourceAttempt] = []
        candidates: list[FundamentalsSnapshot] = []
        for name in self.sources:
            attempt, snapshot = await self._fetch_source(name, key, instrument, moment)
            attempts.append(attempt)
            if snapshot is not None:
                candidates.append(snapshot)

        merged = merge_snapshots(
            [stored, *candidates] if stored is not None else candidates,
            self._order(stored, moment),
            ttl_s=self.ttl_s,
        )

        if merged is None:
            # Nothing served a cell. Writing would claim an answer nobody gave
            # and the stored row (if any) is still the best one available.
            fresh_base = stored is not None and stored.is_fresh(moment)
            if fresh_base:
                status = KEPT
                reason = "no source served a cell; the fresh stored row stands"
            else:
                status = FAILED if any(item.status == FAILED for item in attempts) else EMPTY
                reason = (
                    "no source served a cell"
                    if stored is None
                    else "no source served a cell and the stored row is expired"
                )
            return MergedResult(
                symbol=key,
                status=status,
                instrument_id=instrument.instrument_id,
                snapshot=stored if fresh_base else None,
                filled=stored.field_count if fresh_base and stored is not None else 0,
                missing=(
                    len(FUNDAMENTAL_FIELDS) - stored.field_count
                    if fresh_base and stored is not None
                    else len(FUNDAMENTAL_FIELDS)
                ),
                before=stored.field_count if stored is not None else 0,
                kept=stored is not None,
                sources=tuple(attempts),
                reason=reason,
            )

        base_fields = stored.fields if stored is not None else {}
        added = sum(
            1
            for name in FUNDAMENTAL_FIELDS
            if merged.fields.get(name) is not None and base_fields.get(name) is None
        )
        if stored is not None and not _differs(merged, stored):
            # Every source was asked and the row already said all of it: nothing
            # was learned, so the fetch clock is not advanced. Rewriting an
            # identical row would only extend its 24 h window without evidence.
            fresh_base = stored.is_fresh(moment)
            return MergedResult(
                symbol=key,
                status=KEPT if fresh_base else FAILED,
                instrument_id=stored.instrument_id,
                snapshot=stored if fresh_base else None,
                filled=stored.field_count if fresh_base else 0,
                missing=len(FUNDAMENTAL_FIELDS) - (stored.field_count if fresh_base else 0),
                before=stored.field_count,
                added=0,
                kept=True,
                sources=tuple(attempts),
                reason=(
                    "the sources served no cell the row did not already have; "
                    "the stored row stands"
                    if fresh_base
                    else "the stored row is expired and no source added a cell"
                ),
            )

        written = await self.store.upsert(merged) if self.store is not None else False
        reason: str | None = None
        if self.store is not None and not written:
            reason = "stored row is newer than this merge; it was not overwritten"
        return MergedResult(
            symbol=key,
            status=COMPLETE if merged.complete else PARTIAL,
            instrument_id=merged.instrument_id,
            snapshot=merged,
            written=written,
            filled=merged.field_count,
            missing=len(FUNDAMENTAL_FIELDS) - merged.field_count,
            before=stored.field_count if stored is not None else 0,
            added=added,
            sources=tuple(attempts),
            reason=reason,
        )

    # -- a batch -----------------------------------------------------------

    async def build_many(
        self,
        symbols: Sequence[str],
        *,
        concurrency: int = 4,
        skip_fresh: bool = True,
        time_budget_s: float | None = None,
        on_result: Callable[[MergedResult, int, int], None] | None = None,
    ) -> MergedReport:
        """Merge a bounded batch, one row per distinct symbol, input order kept.

        ``time_budget_s`` stops **starting** symbols when it expires; the
        untouched tail is ``deferred`` (no request, no write) and the next run
        resumes it, exactly like the snapshot builder's bulk mode. Walks already
        in flight are drained rather than cancelled — their requests were spent.
        """

        if concurrency < 1:
            raise ValueError("concurrency must be at least 1")
        if time_budget_s is not None and float(time_budget_s) < 0:
            raise ValueError(f"time_budget_s must be non-negative, got {time_budget_s!r}")

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
        deadline = self._monotonic() + float(time_budget_s) if time_budget_s else None
        limiter = asyncio.Semaphore(concurrency)

        async def one(symbol: str) -> MergedResult | None:
            async with limiter:
                if deadline is not None and self._monotonic() > deadline:
                    return None
                try:
                    return await self.build(symbol, skip_fresh=skip_fresh)
                except Exception as exc:  # noqa: BLE001 - one symbol must not sink the batch
                    logger.warning("merged fundamentals failed for %s: %r", symbol, exc)
                    return MergedResult(
                        symbol=symbol,
                        status=FAILED,
                        reason=f"{type(exc).__name__}: {exc}",
                    )

        tasks: dict[asyncio.Task[MergedResult | None], str] = {
            asyncio.create_task(one(symbol)): symbol for symbol in wanted
        }
        outcomes: dict[str, MergedResult | None] = {}
        completed = 0
        pending = set(tasks)
        while pending:
            done, pending = await asyncio.wait(
                pending, return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                outcome = task.result()
                outcomes[tasks[task]] = outcome
                completed += 1
                if outcome is not None and on_result is not None:
                    on_result(outcome, completed, len(wanted))

        results: list[MergedResult] = []
        deferred: list[str] = []
        for symbol in wanted:
            outcome = outcomes.get(symbol)
            if isinstance(outcome, MergedResult):
                results.append(outcome)
            else:
                deferred.append(symbol)
        return MergedReport(
            results=tuple(results),
            ttl_s=self.ttl_s,
            dropped=tuple(dropped),
            deferred=tuple(deferred),
            started_at=started,
            finished_at=self._clock(),
        )


async def build_merged_snapshots(
    store: SqliteFundamentalsStore,
    *,
    symbols: Sequence[str],
    adapters: Mapping[str, Any],
    concurrency: int = 4,
    skip_fresh: bool = True,
    time_budget_s: float | None = None,
    clock: Callable[[], datetime] | None = None,
    on_result: Callable[[MergedResult, int, int], None] | None = None,
    ttl_s: int = TTL_S,
) -> MergedReport:
    """The one call a CLI needs: merge ``symbols`` into ``store`` and report.

    A thin wrapper rather than a second implementation: the builder owns every
    rule, and the store is passed in so nothing here opens a database of its own.
    """

    builder = MergedFundamentalsBuilder(
        adapters, store=store, ttl_s=ttl_s, clock=clock
    )
    return await builder.build_many(
        symbols,
        concurrency=concurrency,
        skip_fresh=skip_fresh,
        time_budget_s=time_budget_s,
        on_result=on_result,
    )


def _differs(merged: FundamentalsSnapshot, stored: FundamentalsSnapshot) -> bool:
    """True when ``merged`` says anything the stored row does not already say.

    Value *and* attribution are compared: a cell that keeps its number but gains
    a source is a better row (the provenance claim is the product), so it counts
    as a change. A row that differs in nothing is not rewritten — see
    :meth:`MergedFundamentalsBuilder.build`.
    """

    for name in FUNDAMENTAL_FIELDS:
        if merged.fields.get(name) != stored.fields.get(name):
            return True
        if source_label(merged.field_sources.get(name)) != source_label(
            stored.field_sources.get(name)
        ):
            return True
    return False


def merge_priority(
    stored: FundamentalsSnapshot | None,
    *,
    now: datetime,
    priority: Sequence[str] = DEFAULT_PRIORITY,
) -> tuple[str, ...]:
    """The priority list for a stored row, exposed for reports and tests.

    Same rule :meth:`MergedFundamentalsBuilder._order` applies: a fresh base
    first (its own cells are kept), an expired one last (holes only). The base's
    label is de-duplicated out of the named rungs so a ``yahoo`` base does not
    leave a second ``yahoo`` slot behind it.
    """

    order = tuple(name for name in (source_label(item) for item in priority) if name)
    label = source_label(stored.source) if stored is not None else None
    if stored is None or label is None:
        return order
    if stored.is_fresh(now):
        return (label,) + tuple(name for name in order if name != label)
    return order + (label,)


__all__ = [
    "ATTEMPT_STATUSES",
    "KEPT",
    "MERGED_SOURCES",
    "MergedFundamentalsBuilder",
    "MergedReport",
    "MergedResult",
    "OK",
    "SKIPPED",
    "STORED_SOURCE",
    "SourceAttempt",
    "build_merged_snapshots",
    "merge_priority",
]
