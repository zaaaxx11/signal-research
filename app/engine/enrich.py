"""Bulk submissions-profile enrichment for one snapshot.

R3 made venues universal in one request, but `industry`/`country` only exist on
each issuer's ``submissions`` document. Fetching ten thousand of those at SEC's
10-request/second ceiling is a ~17-minute batch, not a startup step and not
something a screen request may trigger.

This module is that batch: bounded, resumable, cache-backed, and honest about
what it did. It selects the records still missing a profile, reads each
submission (cache first, see :func:`app.engine.ingest.fetch_submissions`), merges
the identity fields through :class:`~app.engine.security.SecurityMaster`, and
reports attempted / enriched / skipped / conflicts / failed / requests /
deferred.

Why it is shaped this way
-------------------------
* **Selection before work.** ``limit`` bounds *eligible* rows, not rows seen:
  a complete record or a record with no CIK must not consume the one slot an
  operator asked for, and it must never cost a request. ``skipped`` reports
  those rows separately so they cannot be mistaken for work done.
* **One bad issuer is one failed row.** A 500, a parse failure or a document
  whose venue contradicts the bulk index is counted and the pass continues.
  Ten thousand issuers means the pass must survive its worst document; aborting
  on the first one would make a long crawl unusable.
* **The cache is the resumability.** ``requests`` counts only attempts whose
  cache entry was absent beforehand, so a second run over a warm cache reports
  ``requests=0`` and finishes in seconds. That is the difference between a
  crawl and a job an operator can interrupt.
* **No sleeping here.** The batch measures time (``time_budget_s``) but never
  waits; pacing belongs to the caller that owns the loop, which for the CLI is
  :class:`_PacedClient`. Keeping the wait out of the module is what lets the
  suite run the whole selection logic with a stepping clock and no wall time.
* **Mutating, by design.** The CLI loads one snapshot, enriches it and saves it;
  copying ten thousand records to return a dataset nothing else reads would be
  ceremony, not purity. This function is documented as in-place on
  ``dataset.security_master``.

Contract: ``briefs/market-engine/R3.5-CONTRACT.md``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.engine.ingest import (
    EngineDataset,
    fetch_submissions,
    submissions_cache_path,
)
from app.engine.model import SecurityRecord
from app.engine.security import SecurityConflict
from app.engine.store import EngineStore, SnapshotError
from app.providers import http as provider_http


@dataclass(frozen=True, slots=True)
class EnrichmentReport:
    """One bounded enrichment pass, as counts an operator can reconcile.

    The arithmetic is exact and deliberately split in two:

    * ``requested == attempted + deferred`` — ``deferred`` is only what the time
      budget stopped from starting; a row excluded by ``limit`` was never
      requested, so it is neither attempted nor deferred and does not appear in
      ``skipped`` either (``skipped`` is eligibility, not selection);
    * ``attempted == enriched + conflicts + failed + unchanged``, where
      ``unchanged`` is the remainder: an issuer whose document was read but
      states no industry/country, so the row keeps its nulls. There is no field
      for it because it is not an anomaly — a document that states nothing is a
      legitimate answer — but the remainder is what makes the four visible
      counters add up instead of quietly overlapping.

    ``requests`` is a transport count, not an attempt count: an attempt answered
    by the cache costs zero, and one attempt can be one request. ``skipped``
    counts every record that was never eligible (already complete, or no CIK),
    including rows beyond ``limit``.
    """

    requested: int = 0
    attempted: int = 0
    enriched: int = 0
    skipped: int = 0
    conflicts: int = 0
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
            "enriched": self.enriched,
            "skipped": self.skipped,
            "conflicts": self.conflicts,
            "failed": self.failed,
            "requests": self.requests,
            "deferred": self.deferred,
        }


# ---------------------------------------------------------------------------
# Selection helpers
# ---------------------------------------------------------------------------


def _eligible_cik(record: SecurityRecord) -> str | None:
    """The CIK to fetch for ``record``, or ``None`` when the row is not eligible.

    A row is eligible when it names an issuer **and** is missing a profile
    field: a record with both ``industry`` and ``country`` is already answered,
    and a record with no CIK has no document to ask for. Both are skipped, not
    attempted, so neither can spend a request or a ``limit`` slot.
    """

    if record.cik is None:
        return None
    if record.industry is not None and record.country is not None:
        return None
    return record.cik


def _gained_profile(
    before: SecurityRecord | None, after: SecurityRecord | None
) -> bool:
    """Whether the merge stated a profile field the row did not have.

    ``SecurityMaster`` never overwrites a known field, so a change across one
    merge can only be a null becoming a value. Both nulls are not enrichment,
    and neither is a document that only added feed classes: the row a caller
    selected is the row that has to change.
    """

    if before is None or after is None:
        return False
    return (
        (before.industry is None and after.industry is not None)
        or (before.country is None and after.country is not None)
    )


def _observation_date(fetched_at: str) -> str:
    """The date part of the dataset's observation instant.

    Provenance is dated, not timestamped: ``2026-10-26T12:00:00+00:00`` is
    evidence observed on ``2026-10-26``. Splitting rather than parsing keeps a
    body this module did not write (a bare date, an odd offset) from raising in
    the middle of a batch.
    """

    text = fetched_at.strip()
    for separator in ("T", " "):
        head = text.split(separator, 1)[0].strip()
        if head:
            return head
    return text


# ---------------------------------------------------------------------------
# The pass
# ---------------------------------------------------------------------------


async def enrich_profiles(
    dataset: EngineDataset,
    *,
    client: Any = None,
    cache_dir: Path | None = None,
    limit: int | None = None,
    time_budget_s: float | None = None,
    clock: Callable[[], float] = time.monotonic,
    on_progress: Callable[[int, int], None] | None = None,
) -> EnrichmentReport:
    """Fill missing ``industry``/``country`` from each issuer's submissions body.

    Mutates ``dataset.security_master`` in place: the CLI loads one snapshot,
    enriches it and saves it, and the master's merge policy (fill unknowns,
    refuse disagreements) is the only writer. Nothing else on the dataset
    changes.

    ``cache_dir`` is passed straight to
    :func:`app.engine.ingest.fetch_submissions`, so a warm entry costs no
    request; ``requests`` therefore counts attempts whose entry was absent
    *before* the attempt, which is the number an operator reconciles against
    EDGAR's rate limit. ``clock`` is read once to start the budget and then only
    when ``time_budget_s`` is set, so a running pass cannot be shortened by a
    clock that advances on every read. ``on_progress`` is called after each
    attempt with ``(attempted, requested)``; it observes, it cannot select.
    """

    if not isinstance(dataset, EngineDataset):
        raise TypeError(
            "enrich_profiles needs an EngineDataset, got "
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

    master = dataset.security_master
    as_of = _observation_date(dataset.fetched_at)

    set_aside = 0
    eligible: list[tuple[str, str]] = []
    for record in master.records():
        cik = _eligible_cik(record)
        if cik is None:
            set_aside += 1
            continue
        eligible.append((record.symbol, cik))

    selected = eligible if limit is None else eligible[:limit]
    requested = len(selected)

    started = clock() if time_budget_s is not None else 0.0
    attempted = enriched = conflicts = failed = requests = 0

    for symbol, cik in selected:
        if time_budget_s is not None and clock() - started >= time_budget_s:
            break

        attempted += 1
        before = master.get(symbol)
        entry = (
            None if cache_dir is None else submissions_cache_path(cache_dir, cik)
        )
        # A request happens exactly when there was no entry to read: the CLI
        # paces on this, and a warm rerun must report zero.
        if entry is None or not entry.exists():
            requests += 1

        try:
            payload = await fetch_submissions(
                cik, client=client, cache_dir=cache_dir
            )
            master.add_submissions(payload, as_of=as_of)
        except SecurityConflict:
            # Two SEC documents disagree. The row is left exactly as the bulk
            # index stated it: a document that contradicts an existing field is
            # evidence of a problem, not permission to overwrite.
            conflicts += 1
        except Exception:  # noqa: BLE001 - one bad issuer is one failed row
            failed += 1
        else:
            if _gained_profile(before, master.get(symbol)):
                enriched += 1

        if on_progress is not None:
            on_progress(attempted, requested)

    return EnrichmentReport(
        requested=requested,
        attempted=attempted,
        enriched=enriched,
        skipped=set_aside,
        conflicts=conflicts,
        failed=failed,
        requests=requests,
        deferred=requested - attempted,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class _PacedClient:
    """A client wrapper that spaces requests at ``rate`` per second.

    ``enrich_profiles`` never sleeps, so the CLI owns pacing — but pacing
    *attempts* would wait for work that never happened: a cache hit makes no
    request at all. Wrapping the client instead sleeps only between transport
    calls, which is exactly EDGAR's 10 req/s ceiling, and leaves a warm rerun
    instant. Only ``get`` is forwarded because that is the whole surface the
    ingest boundary uses.
    """

    def __init__(self, client: Any, *, interval_s: float) -> None:
        self._client = client
        self._interval_s = interval_s
        self._calls = 0

    async def get(self, url: str, **kwargs: Any) -> Any:
        """Spacing before the second and later requests; then delegate."""

        if self._calls and self._interval_s > 0:
            await asyncio.sleep(self._interval_s)
        self._calls += 1
        return await self._client.get(url, **kwargs)


async def _run(
    dataset: EngineDataset,
    *,
    cache_dir: Path | None,
    limit: int | None,
    time_budget_s: float | None,
    rate: float,
) -> EnrichmentReport:
    """Enrich ``dataset`` with one client, paced at ``rate`` requests/second."""

    async with provider_http.build_client() as transport:
        paced = _PacedClient(transport, interval_s=1.0 / rate)
        return await enrich_profiles(
            dataset,
            client=paced,
            cache_dir=cache_dir,
            limit=limit,
            time_budget_s=time_budget_s,
        )


def _positive_int(text: str) -> int:
    """``--limit``: at least one row, because zero is not a batch."""

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
        prog="python -m app.engine.enrich",
        description=(
            "Enrich industry/country from SEC submissions for one engine "
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
            "directory of per-CIK submissions bodies; strongly recommended, "
            "and the reason a rerun makes zero requests"
        ),
    )
    parser.add_argument(
        "--limit",
        type=_positive_int,
        default=None,
        help="maximum eligible symbols to attempt (applied after gating)",
    )
    parser.add_argument(
        "--time-budget",
        dest="time_budget",
        type=_seconds,
        default=None,
        help="stop starting new fetches after this many seconds",
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
    """Run the enrichment CLI; return the process exit code.

    Exit ``2`` with ``{"error": code, "errorMessage": ...}`` on stderr when the
    snapshot cannot be verified: a bad file is an operator problem with a
    stable code, and a traceback would bury it. Any other outcome is ``0`` with
    the report document on stdout, whether or not individual issuers failed —
    those are counted in the report, not turned into an exit status that hides
    the work that did happen.
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

    report = asyncio.run(
        _run(
            dataset,
            cache_dir=args.cache_dir,
            limit=args.limit,
            time_budget_s=args.time_budget,
            rate=args.rate,
        )
    )

    if not args.report_only:
        store.save(dataset)

    print(json.dumps(report.document(), indent=2))
    return 0


__all__ = ["EnrichmentReport", "enrich_profiles", "main"]


if __name__ == "__main__":  # pragma: no cover - the module's own entry point
    raise SystemExit(main())
