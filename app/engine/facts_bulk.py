"""Bounded, resumable ``companyfacts`` enrichment for one snapshot.

The identity universe is 10k+ symbols; facts exist only for the symbols an
operator chose to ingest. A full ``companyfacts`` crawl is tens of gigabytes
and hours of parsing, so it is a **batch**, never a startup step: the per-CIK
cache makes it resumable, ``limit`` and ``time_budget_s`` make each run
bounded, and the report states how many documents were read against how many
symbols they filled.

Why it is shaped this way
-------------------------
* **Selection is by document, not by symbol.** ``companyfacts`` is one document
  per issuer, so eligible symbols are grouped by CIK before anything is
  fetched: Berkshire's ``BRK-A`` and ``BRK-B`` are one read, one request and
  one row in ``documents``, while ``symbols`` counts the two fact rows it
  filled. ``limit`` bounds documents for the same reason — an operator asks for
  N issuers, not N tickers of one issuer.
* **Gating happens before selection.** A symbol that already carries facts, or
  that has no CIK, is never eligible: it cannot spend a request and it cannot
  consume a ``limit`` slot. It is counted in ``skipped`` so a gated universe is
  visible rather than silently empty.
* **The result is a new dataset.** ``EngineDataset.facts`` is a read-only
  mapping, and a completed observation is a value: enrichment merges into a
  fresh mapping and returns a new dataset that shares the master and the error
  rows unchanged. A caller that wants to discard the pass simply does not save
  it, which is what ``--report-only`` relies on.
* **A failed issuer is a count.** A 500, a proxy error page refused by the
  ingest boundary, or a body that will not parse increments ``failed`` and the
  pass continues: ten thousand documents means a run must survive its worst
  one. ``SecurityConflict`` cannot happen here — this pass adds facts, not
  identity — so there is no conflict counter.
* **``requests`` is a transport count, not an attempt count.** It increments
  only when the CIK's cache entry did not exist *before* the attempt, so a warm
  rerun reports zero and the number is what an operator reconciles against
  EDGAR's ceiling. Cache reads and writes belong to
  :func:`app.engine.ingest.fetch_companyfacts`; this module never touches the
  files.
* **No sleeping.** The pass measures the budget but never waits; pacing belongs
  to the CLI, which wraps the transport. Keeping the wait out of the module is
  what lets the suite exercise the whole selection with a stepping clock.

Contract: ``briefs/market-engine/R4.5-CONTRACT.md``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.engine.enrich import _PacedClient
from app.engine.facts import fact_ttm_summary
from app.engine.ingest import (
    EngineDataset,
    facts_cache_path,
    fetch_companyfacts,
)
from app.engine.model import Cell
from app.engine.store import EngineStore, SnapshotError
from app.providers import http as provider_http

#: The document kind this batch reads; the same word the cache filename uses.
FACTS_KIND = "companyfacts"


@dataclass(frozen=True, slots=True)
class FactsReport:
    """One bounded facts pass, as counts an operator can reconcile.

    The arithmetic is exact over **CIK documents**:

    * ``requested == attempted + deferred`` — ``deferred`` is only what the time
      budget stopped from starting. A document excluded by ``limit`` was never
      requested, so it is neither attempted nor deferred;
    * ``attempted == documents + failed`` — one attempted document either read
      and parsed, or did not;
    * ``symbols`` is not ``documents``: it counts the fact rows actually added,
      and a CIK with several share classes fills several rows from one read.
    * ``skipped`` counts symbols that were never eligible (already carrying
      facts, or no CIK), including any beyond ``limit``.
    * ``requests`` counts the attempts whose cache entry was absent beforehand;
      an attempt answered by a warm cache costs zero.
    """

    requested: int = 0
    attempted: int = 0
    documents: int = 0
    symbols: int = 0
    skipped: int = 0
    failed: int = 0
    requests: int = 0
    deferred: int = 0

    def document(self) -> dict[str, object]:
        """Project the report into its JSON shape.

        The keys are the dataclass fields, in declaration order, and nothing
        else: an operator's script and the CLI's stdout must agree without a
        translation table.
        """

        return {
            "requested": self.requested,
            "attempted": self.attempted,
            "documents": self.documents,
            "symbols": self.symbols,
            "skipped": self.skipped,
            "failed": self.failed,
            "requests": self.requests,
            "deferred": self.deferred,
        }


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


def _select_documents(
    dataset: EngineDataset,
) -> tuple[list[tuple[str, tuple[str, ...]]], int]:
    """Eligible CIK documents in symbol order, plus the ineligible symbol count.

    Iterating the master (which is symbol-ordered) and bucketing on first sight
    gives a deterministic selection: the CLI attempts AAPL before MSFT, and two
    classes of one issuer land in the same bucket. A symbol is ineligible when
    it has no CIK — there is no document to ask for — or when ``facts`` already
    holds an entry for it, which includes an entry an earlier pass filled
    (possibly with no usable cell). Both are skipped rather than attempted, so
    neither can spend a request or a ``limit`` slot.
    """

    buckets: dict[str, list[str]] = {}
    skipped = 0
    for record in dataset.security_master.records():
        if record.cik is None or record.symbol in dataset.facts:
            skipped += 1
            continue
        buckets.setdefault(record.cik, []).append(record.symbol)
    return [(cik, tuple(symbols)) for cik, symbols in buckets.items()], skipped


# ---------------------------------------------------------------------------
# The pass
# ---------------------------------------------------------------------------


async def enrich_facts(
    dataset: EngineDataset,
    *,
    client: Any = None,
    cache_dir: Path | None = None,
    limit: int | None = None,
    time_budget_s: float | None = None,
    clock: Callable[[], float] = time.monotonic,
    on_progress: Callable[[int, int], None] | None = None,
) -> tuple[EngineDataset, FactsReport]:
    """Fill missing facts for a bounded set of CIK documents.

    Returns a **new** :class:`EngineDataset`; ``dataset`` is not modified. The
    master and the error mapping are shared unchanged — this pass reads
    documents and adds facts, and a per-CIK failure is a report count, not an
    ``errors`` row the caller did not ask for.

    ``cache_dir`` is passed straight to
    :func:`app.engine.ingest.fetch_companyfacts`, so a warm entry costs no
    request; ``requests`` therefore counts attempts whose entry was absent
    *before* the attempt, and ``cache_dir=None`` counts every attempt because
    there is no entry to answer it. ``clock`` is read once to start the budget
    and then once per document, and only while a budget is set, so a running
    pass cannot be shortened by a clock that advances on every read.
    ``on_progress`` is called after each document with
    ``(attempted, requested)``; it observes, it cannot select.
    """

    if not isinstance(dataset, EngineDataset):
        raise TypeError(
            "enrich_facts needs an EngineDataset, got "
            f"{type(dataset).__name__}"
        )
    if limit is not None:
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise TypeError(f"limit must be an int or None, got {limit!r}")
        if limit < 1:
            raise ValueError(f"limit must be at least 1, got {limit}")
    if time_budget_s is not None:
        if isinstance(time_budget_s, bool) or not isinstance(
            time_budget_s, (int, float)
        ):
            raise TypeError(
                f"time_budget_s must be a number or None, got {time_budget_s!r}"
            )
        if time_budget_s < 0:
            raise ValueError(
                f"time_budget_s must not be negative, got {time_budget_s}"
            )
    if not callable(clock):
        raise TypeError("clock must be callable")

    documents, skipped = _select_documents(dataset)
    selected = documents if limit is None else documents[:limit]
    requested = len(selected)

    started = clock() if time_budget_s is not None else 0.0
    attempted = documents_read = symbols_added = failed = requests = 0
    merged: dict[str, Mapping[str, Cell]] = dict(dataset.facts)

    for cik, symbols in selected:
        if time_budget_s is not None and clock() - started >= time_budget_s:
            break

        attempted += 1
        entry = None if cache_dir is None else facts_cache_path(cache_dir, cik)
        # A request happens exactly when there was no entry to read: the CLI
        # paces on this, and a warm rerun must report zero.
        if entry is None or not entry.exists():
            requests += 1

        try:
            payload = await fetch_companyfacts(
                cik, client=client, cache_dir=cache_dir
            )
            cells = fact_ttm_summary(payload)
        except Exception:  # noqa: BLE001 - one bad issuer is one failed document
            failed += 1
        else:
            documents_read += 1
            if cells:
                # The same cells object serves every class of this issuer: the
                # document is one, and copying it per symbol would pretend the
                # classes were read separately.
                for symbol in symbols:
                    merged[symbol] = cells
                symbols_added += len(symbols)
            # A document that parses to no usable cell fills nothing: an empty
            # facts entry would claim a fact row that does not exist and would
            # gate the symbol out of a later pass that could have done better.

        if on_progress is not None:
            on_progress(attempted, requested)

    enriched = EngineDataset(
        fetched_at=dataset.fetched_at,
        security_master=dataset.security_master,
        facts=merged,
        errors=dataset.errors,
    )
    return enriched, FactsReport(
        requested=requested,
        attempted=attempted,
        documents=documents_read,
        symbols=symbols_added,
        skipped=skipped,
        failed=failed,
        requests=requests,
        deferred=requested - attempted,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


async def _run(
    dataset: EngineDataset,
    *,
    cache_dir: Path | None,
    limit: int | None,
    time_budget_s: float | None,
    rate: float,
) -> tuple[EngineDataset, FactsReport]:
    """Enrich ``dataset`` with one client, paced at ``rate`` requests/second.

    ``_PacedClient`` is imported rather than reimplemented: it spaces transport
    calls (never attempts), so a cache hit never waits, and two batches with
    two pacing policies would eventually disagree about EDGAR's ceiling.
    """

    async with provider_http.build_client() as transport:
        paced = _PacedClient(transport, interval_s=1.0 / rate)
        return await enrich_facts(
            dataset,
            client=paced,
            cache_dir=cache_dir,
            limit=limit,
            time_budget_s=time_budget_s,
        )


def _positive_int(text: str) -> int:
    """``--limit``: at least one document, because zero is not a batch."""

    try:
        value = int(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"expected an integer, got {text!r}"
        ) from exc
    if value < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return value


def _seconds(text: str) -> float:
    """``--time-budget``: a non-negative number of seconds; 0 measures only."""

    try:
        value = float(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"expected a number of seconds, got {text!r}"
        ) from exc
    if value < 0:
        raise argparse.ArgumentTypeError("must not be negative")
    return value


def _positive_float(text: str) -> float:
    """``--rate``: a positive requests/second ceiling."""

    try:
        value = float(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"expected a number, got {text!r}"
        ) from exc
    if value <= 0:
        raise argparse.ArgumentTypeError("must be greater than 0")
    return value


def _build_parser() -> argparse.ArgumentParser:
    """The CLI surface, as documented in the contract's §3."""

    parser = argparse.ArgumentParser(
        prog="python -m app.engine.facts_bulk",
        description=(
            "Fill missing XBRL facts from SEC companyfacts for one engine "
            "snapshot. Bounded by --limit/--time-budget and resumable through "
            "--cache-dir."
        ),
    )
    parser.add_argument(
        "--snapshot",
        required=True,
        type=Path,
        help="snapshot file to enrich (rewritten unless --report-only)",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=None,
        help=(
            "directory of per-CIK companyfacts bodies; strongly recommended, "
            "and the reason a rerun makes zero requests"
        ),
    )
    parser.add_argument(
        "--limit",
        type=_positive_int,
        default=None,
        help="maximum CIK documents to attempt (applied after gating)",
    )
    parser.add_argument(
        "--time-budget",
        dest="time_budget",
        type=_seconds,
        default=None,
        help="stop starting new documents after this many seconds",
    )
    parser.add_argument(
        "--report-only",
        dest="report_only",
        action="store_true",
        help="measure the pass without writing the snapshot",
    )
    parser.add_argument(
        "--rate",
        type=_positive_float,
        default=10.0,
        help="requests per second between cache misses (default 10, EDGAR's "
        "fair-access ceiling)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the facts CLI; return the process exit code.

    Exit ``2`` with ``{"error": code, "errorMessage": ...}`` on stderr when the
    snapshot cannot be verified: a bad file is an operator problem with a
    stable code, and a traceback would bury it. Any other outcome is ``0`` with
    the report document on stdout, whether or not individual issuers failed —
    those are counted in the report, not turned into an exit status that hides
    the work that did happen.

    ``--report-only`` runs the same pass and discards the returned dataset: the
    snapshot is evidence, and a measurement is not a write.
    """

    parser = _build_parser()
    args = parser.parse_args(argv)

    store = EngineStore(args.snapshot)
    try:
        dataset = store.load()
    except SnapshotError as exc:
        print(
            json.dumps({"error": exc.code, "errorMessage": str(exc)}),
            file=sys.stderr,
        )
        return 2

    enriched, report = asyncio.run(
        _run(
            dataset,
            cache_dir=args.cache_dir,
            limit=args.limit,
            time_budget_s=args.time_budget,
            rate=args.rate,
        )
    )

    if not args.report_only:
        store.save(enriched)

    print(json.dumps(report.document(), indent=2))
    return 0


__all__ = ["FACTS_KIND", "FactsReport", "enrich_facts", "main"]


if __name__ == "__main__":  # pragma: no cover - the module's own entry point
    raise SystemExit(main())
