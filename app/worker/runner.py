"""``process_job`` — the ONE execution path (02 §3.1, 05 Langkah 5).

Two provider modes, one pipeline:

* ``MEE_PROVIDER_MODE=fake`` (default) — the deterministic in-process provider
  from Phase 0. No sockets, so the frozen Phase-0/1 suites stay offline.
* ``MEE_PROVIDER_MODE=live`` — the registry ladder from
  ``Runtime.ladder(budget=..., as_of=...)``. The *same* construction path the
  Phase-2 suite uses, so "the worker walks a ladder only tests can build" cannot
  happen.

Everything after the provider call is shared: conflation into canonical rows,
the core-identity gate, export, artifacts, ``usage``, ``complete``. There is
deliberately no second code path for "small" jobs: a job that runs inline and a
job that runs in a worker must produce the same artifacts, or the export
contract means nothing.

Failure taxonomy (02 §3.9) is applied here, once:

* ``throttled`` / ``blocked`` / ``5xx`` / ``timeout`` → retryable (requeue while
  attempts remain, then terminal Failure with the attempt count);
* ``not_found`` / ``schema_drift`` / ``authwall`` / ``mismatch`` /
  ``unit_anomaly`` → terminal, because retrying does not change a fact and a
  retry storm on schema drift hides the bug;
* anything unrecognised → terminal: an unknown exception is a bug, not weather.

Budget exhaustion is **not** in that list: it is a property of the result
(``result_mode="partial"`` + ``skipped_by_budget``), never a failure.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from app.config import get_settings
from app.contracts import CANONICAL_COLUMNS_V1, Capability, MarketKind, MarketRequest
from app.enrich.quality import aggregate, finalize_row, missing_core_columns
from app.export.writer import write_export
from app.jobs.errors import JobError, JobNotFound
from app.jobs.ports import JobRecord, JobRepository
from app.jobs.repository import PENDING, PROCESSING
from app.providers.adapters.fake import FAKE_NAME, FakeProvider
from app.providers.base import InstrumentRef
from app.providers.budget import Budget
from app.providers.cache import session_date

logger = logging.getLogger("mee.worker")

FAKE_MODE = "fake"
LIVE_MODE = "live"

#: Verdict kinds that a second attempt can plausibly change (02 §3.9).
RETRYABLE_KINDS: tuple[str, ...] = ("throttled", "blocked", "5xx", "timeout", "circuit_open")
#: Verdict kinds where a retry only adds load (02 §3.9).
TERMINAL_KINDS: tuple[str, ...] = (
    "not_found",
    "schema_drift",
    "authwall",
    "mismatch",
    "unit_anomaly",
)


def classify_error(exc: BaseException) -> tuple[str, bool]:
    """Return ``(kind, retryable)`` for an exception, using the 02 §3.9 taxonomy."""

    if isinstance(exc, TimeoutError):
        return "timeout", True
    message = f"{type(exc).__name__}: {exc}".lower()
    for kind in RETRYABLE_KINDS:
        if kind in message:
            return kind, True
    for kind in TERMINAL_KINDS:
        if kind in message:
            return kind, False
    return "error", False


def _symbols(request: MarketRequest) -> list[str]:
    """Phase 0/1 only know an explicit watchlist; other kinds are resolved upstream."""

    if request.market.kind is MarketKind.WATCHLIST and request.market.symbols:
        return list(request.market.symbols)
    raise JobError(
        f"no resolver for market.kind={request.market.kind.value!r} yet (Phase 1); "
        "use market.kind='watchlist' with symbols[]"
    )


def _export_dir(export_dir: str | Path | None) -> Path:
    directory = Path(export_dir) if export_dir is not None else Path(get_settings().export_dir)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


async def process_job(
    job_id: str,
    *,
    repository: JobRepository,
    export_dir: str | Path | None = None,
    worker_id: str | None = None,
    ladder_factory: Callable[..., Any] | None = None,
    provider_mode: str = FAKE_MODE,
) -> JobRecord:
    """Claim, enrich, export, complete. Returns the final job record."""

    job = await repository.get(job_id)
    if job is None:
        raise JobNotFound(f"job {job_id} does not exist")

    if job["status"] == PENDING:
        claimed = await repository.claim_job(job_id, worker_id or _worker_id())
        if claimed is None:
            # Another worker won the CAS; its result is the authoritative one.
            current = await repository.get(job_id)
            if current is None:  # pragma: no cover - row cannot vanish mid-flight
                raise JobNotFound(f"job {job_id} disappeared")
            return current
        job = claimed
    elif job["status"] != PROCESSING:
        return job

    try:
        return await _run(
            job,
            repository=repository,
            export_dir=export_dir,
            ladder_factory=ladder_factory,
            provider_mode=provider_mode,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - the taxonomy decides, not the type
        kind, retryable = classify_error(exc)
        return await repository.fail(job_id, error=f"{kind}: {exc}", retryable=retryable)


async def _run(
    job: JobRecord,
    *,
    repository: JobRepository,
    export_dir: str | Path | None,
    ladder_factory: Callable[..., Any] | None = None,
    provider_mode: str = FAKE_MODE,
) -> JobRecord:
    job_id = job["id"]
    params = json.loads(job["params"])
    request = MarketRequest.model_validate(params)
    symbols = _symbols(request)
    fields = set(request.fields or CANONICAL_COLUMNS_V1)

    logger.info(
        "job start job_id=%s mode=%s symbols=%d fields=%d",
        job_id,
        provider_mode,
        len(symbols),
        len(fields),
    )

    async with asyncio.timeout(request.budget.seconds):
        if provider_mode == LIVE_MODE:
            outcome = await _fetch_live(request, symbols, fields, ladder_factory)
        elif provider_mode == FAKE_MODE:
            outcome = await _fetch_fake(request, symbols, fields)
        else:  # pragma: no cover - Settings validates the literal
            raise JobError(f"unknown MEE_PROVIDER_MODE {provider_mode!r}; use fake|live")

    # A row without core identity is not exported: it would mislead the reader
    # of the table (02 §3.8.1). Rejected rows are counted, not silently dropped.
    kept: list[dict[str, Any]] = []
    rejected = 0
    for row in outcome.rows:
        if missing_core_columns(row):
            rejected += 1
            continue
        kept.append(row)

    if not kept:
        raise JobError(_no_rows_reason(outcome))

    # Cancellation is honoured at a safe boundary: never mid-write.
    current = await repository.get(job_id)
    if current is not None and current["cancel_requested"]:
        return await repository.fail(job_id, error="cancelled by client", retryable=False)

    directory = _export_dir(export_dir)
    written: dict[str, dict[str, Any]] = {}
    for fmt in request.format:
        result = write_export(kept, fmt, directory / f"{job_id}.{fmt}", columns=CANONICAL_COLUMNS_V1)
        await repository.record_artifact(
            job_id,
            kind=fmt,
            path=result.path,
            sha256=result.sha256,
            row_count=result.row_count,
            bytes_=result.bytes,
        )
        written[fmt] = {"bytes": result.bytes, "sha256": result.sha256}

    result_mode = "partial" if (outcome.unresolved or outcome.skipped_by_budget) else "full"
    usage = _usage(
        request=request,
        kept=kept,
        rejected=rejected,
        unresolved=outcome.unresolved,
        verdicts=outcome.verdicts,
        written=written,
        requests_total=outcome.requests,
        bytes_total=outcome.bytes,
        cache_hits=outcome.cache_hits,
        providers=outcome.providers,
        skipped_by_budget=outcome.skipped_by_budget,
        result_mode=result_mode,
        reference_status="ok",
        quote_status="partial" if outcome.unresolved else "ok",
    )
    logger.info(
        "job done job_id=%s rows=%d unresolved=%d requests=%d bytes=%d cache_hits=%d skipped_by_budget=%d",
        job_id,
        len(kept),
        len(outcome.unresolved),
        outcome.requests,
        outcome.bytes,
        outcome.cache_hits,
        outcome.skipped_by_budget,
    )
    return await repository.complete(
        job_id,
        result_count=len(kept),
        result_mode=result_mode,
        rows_unresolved=len(outcome.unresolved),
        usage=usage,
    )


# ---------------------------------------------------------------------------
# Provider paths
# ---------------------------------------------------------------------------


@dataclass
class _Outcome:
    """What one provider path produced: rows, why not, and what it cost."""

    rows: list[dict[str, Any]] = field(default_factory=list)
    unresolved: list[dict[str, str]] = field(default_factory=list)
    verdicts: dict[str, int] = field(default_factory=dict)
    providers: dict[str, dict[str, Any]] = field(default_factory=dict)
    requests: int = 0
    bytes: int = 0
    cache_hits: int = 0
    skipped_by_budget: int = 0

    def serve(self, symbol: str, kind: str) -> None:
        self.verdicts[kind] = self.verdicts.get(kind, 0) + 1

    def reject(self, symbol: str, kind: str) -> None:
        self.serve(symbol, kind)
        self.unresolved.append({"input": symbol, "reason": kind})

    def meter(self, provider: str, *, requests: int, bytes_: int, verdict: str) -> None:
        """Per-provider counters are a sibling of ``usage``, not a copy of it."""

        bucket = self.providers.setdefault(
            provider, {"requests": 0, "bytes": 0, "verdicts": {}}
        )
        bucket["requests"] += int(requests)
        bucket["bytes"] += int(bytes_)
        counts = bucket["verdicts"]
        counts[verdict] = counts.get(verdict, 0) + 1


async def _fetch_fake(
    request: MarketRequest, symbols: list[str], fields: set[str]
) -> _Outcome:
    """Phase 0/1 behaviour, byte-identical to the frozen contracts."""

    provider = FakeProvider()
    outcome = _Outcome()
    raw = await provider.fetch(Capability.QUOTE, symbols, fields)
    body_bytes = int(raw.get("bytes", 0)) if isinstance(raw, dict) else 0
    outcome.requests = len(symbols)
    outcome.bytes = body_bytes
    outcome.providers[FAKE_NAME] = {
        "requests": len(symbols),
        "bytes": body_bytes,
        "verdicts": {},
    }
    for symbol in symbols:
        verdict = provider.verdict(raw, symbol, Capability.QUOTE)
        outcome.serve(symbol, verdict.kind)
        count = outcome.providers[FAKE_NAME]["verdicts"]
        count[verdict.kind] = count.get(verdict.kind, 0) + 1
        if not verdict.ok:
            outcome.unresolved.append({"input": symbol, "reason": verdict.kind})
            continue
        outcome.rows.extend(finalize_row(row) for row in provider.parse(raw, symbol))
    return outcome


async def _fetch_live(
    request: MarketRequest,
    symbols: list[str],
    fields: set[str],
    ladder_factory: Callable[..., Any] | None,
) -> _Outcome:
    """Walk the registry ladder once per symbol, on one per-job budget."""

    if ladder_factory is None:
        raise JobError(
            "MEE_PROVIDER_MODE=live requires a ladder factory; the runtime must pass "
            "Runtime.ladder so the worker and the tests walk the same rungs"
        )
    unsupported = [
        capability.value
        for capability in request.enrichment
        if capability is not Capability.QUOTE
    ]
    if unsupported:
        # Silently producing a quote-only row for a job that asked for
        # fundamentals is the "Success with empty columns" lie 02 §3.9 exists to
        # prevent. Fase 2 wires the QUOTE ladder only; say so instead.
        raise JobError(
            "live provider mode implements the QUOTE capability only in Fase 2; "
            "requested: " + ", ".join(sorted(unsupported))
        )
    budget = Budget(
        requests=request.budget.requests,
        credits=request.budget.credits,
        seconds=request.budget.seconds,
    )
    ladder = ladder_factory(budget=budget, as_of=request.as_of)
    outcome = _Outcome()
    rows: dict[str, dict[str, Any]] = {}

    for symbol in symbols:
        instrument = InstrumentRef.from_symbol(symbol, venue=request.market.venue)
        result = await ladder.fetch(Capability.QUOTE, instrument, fields)
        outcome.requests += result.requests
        outcome.bytes += result.bytes
        outcome.cache_hits += result.cache_hits
        outcome.skipped_by_budget += result.skipped_by_budget
        for attempt in result.attempts:
            outcome.meter(
                str(attempt["provider"]),
                requests=int(attempt.get("requests") or 0),
                bytes_=int(attempt.get("bytes") or 0),
                verdict=str(attempt.get("verdict") or "unknown"),
            )

        if result.verdict.ok and result.observations:
            outcome.serve(symbol, "ok")
            rows[instrument.instrument_id] = _row_from(
                instrument, result.observations, request=request, source=result.rung
            )
        else:
            # An "ok" whose parser produced nothing is an empty answer, not a
            # served row — the reason must say so rather than echoing "ok".
            reason = result.verdict.kind if not result.verdict.ok else "empty"
            outcome.reject(symbol, reason)

    outcome.rows = list(rows.values())
    return outcome


def _row_from(
    instrument: InstrumentRef,
    observations: list[dict[str, Any]],
    *,
    request: MarketRequest,
    source: str | None,
) -> dict[str, Any]:
    """One canonical row: identity from the reference, cells from the observations.

    ``as_of_achieved`` comes from the provider's ``quote_as_of`` — the time of the
    **data**. When a provider could not say when its price was true, the row is
    left without it and the core-identity gate rejects it: an undated price in an
    exported table is exactly the class of lie this engine exists to avoid.
    """

    row: dict[str, Any] = {
        "instrument_id": instrument.instrument_id,
        "symbol": instrument.symbol,
        "mic": instrument.mic,
        "asset_class": instrument.asset_class,
        "currency": instrument.currency,
        "session_date": session_date(request.as_of),
        "primary_source": source,
    }
    as_of: str | None = None
    for item in observations:
        name = str(item.get("field") or "")
        if not name:
            continue
        value = item["value_num"] if item.get("value_num") is not None else item.get("value_text")
        if value is None:
            continue
        row[name] = value
        if name == "quote_as_of":
            as_of = str(value)
    if as_of:
        row["as_of_achieved"] = as_of
    return finalize_row(row)


def _no_rows_reason(outcome: _Outcome) -> str:
    """An empty result is a sentence, not a shrug."""

    if outcome.skipped_by_budget:
        return (
            "budget exhausted before any row could be served "
            f"(skipped_by_budget={outcome.skipped_by_budget})"
        )
    if outcome.unresolved:
        reasons = sorted({entry["reason"] for entry in outcome.unresolved})
        return "no row survived: " + ", ".join(reasons)
    return "no row survived the core-column gate (identity)"


def _usage(
    *,
    request: MarketRequest,
    kept: list[dict[str, Any]],
    rejected: int,
    unresolved: list[dict[str, str]],
    verdicts: dict[str, int],
    written: dict[str, dict[str, Any]],
    requests_total: int,
    bytes_total: int,
    cache_hits: int,
    providers: dict[str, dict[str, Any]],
    skipped_by_budget: int,
    result_mode: str,
    reference_status: str = "ok",
    quote_status: str = "ok",
) -> dict[str, Any]:
    """Everything ``GET /requests/{id}`` reports, frozen into ``jobs.usage``."""

    stats = aggregate(kept)
    as_of = sorted(
        str(row["quote_as_of"]) for row in kept if row.get("quote_as_of")
    )
    return {
        "requests_total": requests_total,
        "bytes_total": bytes_total,
        "credits_total": 0,
        "cache_hits": cache_hits,
        "skipped_by_budget": skipped_by_budget,
        "phases": {
            "reference": {"status": reference_status, "rows": len(kept)},
            "quote": {
                "status": quote_status,
                "rows": len(kept),
                "skipped_by_budget": skipped_by_budget,
            },
            "verify": {"status": "skipped", "reason": "verifier lands in Phase 4"},
            "export": {"status": "ok", "files": sorted(written)},
        },
        "providers": providers,
        "as_of_achieved": {"min": as_of[0], "max": as_of[-1]} if as_of else None,
        "coverage": stats["coverage"],
        "quality": stats["quality"],
        "rows_by_quality": stats["rows_by_quality"],
        "progress": {"batches_done": 1, "batches_total": 1, "rows_unique": len(kept)},
        "rejected_rows": rejected,
        "unresolved": unresolved,
        "files": written,
        "retryable": False,
        "result_mode": result_mode,
        "as_of_requested": request.as_of.isoformat(),
        "finished_at": datetime.now(UTC).isoformat(timespec="microseconds"),
        "verdicts": verdicts,
    }


def _worker_id() -> str:
    return f"inproc-{os.getpid()}"


__all__ = ["classify_error", "process_job"]
