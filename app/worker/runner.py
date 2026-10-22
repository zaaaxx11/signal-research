"""``process_job`` — the ONE execution path (02 §3.1, 05 Langkah 5).

Phase 0 executes exactly one phase, ``quote``, through the fake provider. The
shape is the shape the later phases keep: claim → fetch through the ladder →
conflate into canonical rows → drop rows without core identity → export →
record artifacts → complete. There is deliberately no second code path for
"small" jobs: a job that runs inline and a job that runs in a worker must
produce the same artifacts, or the export contract means nothing.

Failure taxonomy (02 §3.9) is applied here, once:

* ``throttled`` / ``blocked`` / ``5xx`` / ``timeout`` → retryable (requeue while
  attempts remain, then terminal Failure with the attempt count);
* ``not_found`` / ``schema_drift`` / ``authwall`` / ``mismatch`` /
  ``unit_anomaly`` → terminal, because retrying does not change a fact and a
  retry storm on schema drift hides the bug;
* anything unrecognised → terminal: an unknown exception is a bug, not weather.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.contracts import CANONICAL_COLUMNS_V1, Capability, MarketKind, MarketRequest
from app.enrich.quality import aggregate, finalize_row, missing_core_columns
from app.export.writer import write_export
from app.jobs.errors import JobError, JobNotFound
from app.jobs.ports import JobRecord, JobRepository
from app.jobs.repository import PENDING, PROCESSING
from app.providers.adapters.fake import FAKE_NAME, FakeProvider

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
    """Phase 0 only knows an explicit watchlist (the resolver is Phase 1)."""

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
        return await _run(job, repository=repository, export_dir=export_dir)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - the taxonomy decides, not the type
        kind, retryable = classify_error(exc)
        return await repository.fail(job_id, error=f"{kind}: {exc}", retryable=retryable)


async def _run(
    job: JobRecord, *, repository: JobRepository, export_dir: str | Path | None
) -> JobRecord:
    job_id = job["id"]
    params = json.loads(job["params"])
    request = MarketRequest.model_validate(params)
    symbols = _symbols(request)

    provider = FakeProvider()
    fields = set(request.fields or CANONICAL_COLUMNS_V1)

    async with asyncio.timeout(request.budget.seconds):
        raw = await provider.fetch(Capability.QUOTE, symbols, fields)

    verdicts: dict[str, int] = {}
    rows: list[dict[str, Any]] = []
    unresolved: list[dict[str, str]] = []
    for symbol in symbols:
        verdict = provider.verdict(raw, symbol, Capability.QUOTE)
        verdicts[verdict.kind] = verdicts.get(verdict.kind, 0) + 1
        if not verdict.ok:
            unresolved.append({"input": symbol, "reason": verdict.kind})
            continue
        rows.extend(finalize_row(row) for row in provider.parse(raw, symbol))

    # A row without core identity is not exported: it would mislead the reader
    # of the table (02 §3.8.1). Rejected rows are counted, not silently dropped.
    kept: list[dict[str, Any]] = []
    rejected = 0
    for row in rows:
        if missing_core_columns(row):
            rejected += 1
            continue
        kept.append(row)

    if not kept:
        raise JobError("no row survived the core-column gate (identity)")

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

    usage = _usage(
        request=request,
        symbols=symbols,
        raw=raw,
        kept=kept,
        rejected=rejected,
        unresolved=unresolved,
        verdicts=verdicts,
        written=written,
    )
    return await repository.complete(
        job_id,
        result_count=len(kept),
        result_mode="full" if not unresolved else "partial",
        rows_unresolved=len(unresolved),
        usage=usage,
    )


def _usage(
    *,
    request: MarketRequest,
    symbols: list[str],
    raw: Any,
    kept: list[dict[str, Any]],
    rejected: int,
    unresolved: list[dict[str, str]],
    verdicts: dict[str, int],
    written: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Everything ``GET /requests/{id}`` reports, frozen into ``jobs.usage``."""

    stats = aggregate(kept)
    as_of = sorted(
        str(row["quote_as_of"]) for row in kept if row.get("quote_as_of")
    )
    body_bytes = int(raw.get("bytes", 0)) if isinstance(raw, dict) else 0
    return {
        "requests_total": len(symbols),
        "bytes_total": body_bytes,
        "credits_total": 0,
        "cache_hits": 0,
        "phases": {
            "reference": {"status": "ok", "rows": len(kept)},
            "quote": {"status": "ok", "rows": len(kept)},
            "verify": {"status": "skipped", "reason": "verifier lands in Phase 4"},
            "export": {"status": "ok", "files": sorted(written)},
        },
        "providers": {
            FAKE_NAME: {"requests": len(symbols), "bytes": body_bytes, "verdicts": verdicts}
        },
        "as_of_achieved": {"min": as_of[0], "max": as_of[-1]} if as_of else None,
        "coverage": stats["coverage"],
        "quality": stats["quality"],
        "rows_by_quality": stats["rows_by_quality"],
        "progress": {"batches_done": 1, "batches_total": 1, "rows_unique": len(kept)},
        "rejected_rows": rejected,
        "unresolved": unresolved,
        "files": written,
        "retryable": False,
        "result_mode": "full" if not unresolved else "partial",
        "as_of_requested": request.as_of.isoformat(),
        "finished_at": datetime.now(UTC).isoformat(timespec="microseconds"),
    }


def _worker_id() -> str:
    return f"inproc-{os.getpid()}"


__all__ = ["classify_error", "process_job"]
