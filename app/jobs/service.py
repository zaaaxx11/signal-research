"""Job service: submit / cancel / retry / view use-cases (02 §3.2, §3.9).

The service is where three product decisions live, and none of them belong in a
route handler:

* **``rows_target`` is derived from the request, not invented.** A watchlist of
  3 symbols targets 3 rows; a filter-based universe has no target until the
  resolver exists, so it stays ``null`` rather than ``0``.
* **``wire_status`` maps ``Processing`` to ``Pending``.** The DB has four states
  (02 §3.9) but the API contract exposes three: a client that sees
  "Processing" starts optimising for a state it cannot act on.
* **Ownership is checked before anything else** and answers ``404`` exactly like
  an unknown id: a foreign job id must not become an existence oracle.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from app.contracts import MarketKind, MarketRequest, QuotePlanV1
from app.export.writer import IMPLEMENTED_FORMATS
from app.jobs.errors import JobNotFound, JobTerminal, RequestNotServable
from app.jobs.ports import JobRecord, JobRepository, QuoteRepository
from app.jobs.repository import FAILURE, PROCESSING
from app.providers.registry import REGISTRY, ladder_for


def rows_target_for(request: MarketRequest) -> int | None:
    """How many rows the caller asked for, when that number is knowable."""

    if request.target_rows is not None:
        return request.target_rows
    if request.market.kind is MarketKind.WATCHLIST and request.market.symbols:
        return len(request.market.symbols)
    return None


def wire_status(status: str) -> str:
    """DB status → API status (``Processing`` is reported as ``Pending``)."""

    return "Pending" if status == PROCESSING else status


async def submit_enrich(
    request: MarketRequest,
    *,
    api_key: str,
    repository: JobRepository,
    quotes: QuoteRepository | None = None,
) -> JobRecord:
    """Freeze the request into ``jobs.params`` and return the created job.

    Preflight checks run **before** anything is spent, because a job that cannot
    honour the request is a ``Success``-shaped lie waiting to happen:

    * a capability with no rung in ``REGISTRY`` (Phase 0 has ``quote`` only);
    * an output format whose writer is a later phase (``xlsx``, ``parquet``);

    Then, when the caller presents a ``quote_id``, the order is fixed:

    1. **consume** the quote (409 replay/foreign, 410 expired) — the single-use
       CAS is the only thing standing between two jobs and one sold plan;
    2. build ``params`` from the **frozen plan**, not from the caller's
       ``market`` / ``fields`` / ``as_of`` (gate 4: the plan wins);
    3. create the job, then write ``quotes.job_id`` — the column is metadata
       *after* the job exists, never a reservation before it.

    A refusal therefore leaves no job row and does not spend the quote.
    """

    missing = [capability.value for capability in request.enrichment if not ladder_for(capability)]
    if missing:
        raise RequestNotServable(
            "no provider rung for capability: "
            + ", ".join(sorted(missing))
            + f"; this deployment serves: {', '.join(sorted(c.value for c in {cap for spec in REGISTRY for cap in spec.capabilities}))}"
        )

    unsupported = [fmt for fmt in request.format if fmt not in IMPLEMENTED_FORMATS]
    if unsupported:
        raise RequestNotServable(
            "export writer not implemented for: "
            + ", ".join(sorted(unsupported))
            + f"; available: {', '.join(IMPLEMENTED_FORMATS)}"
        )

    if request.quote_id is not None:
        if quotes is None:
            # Never silently ignore the id: an unhonoured quote is a plan the
            # client thinks it bought (02 §3.2).
            raise RequestNotServable(
                "quote_id was presented but no quote store is configured for this deployment"
            )
        return await _submit_from_quote(
            request, api_key=api_key, repository=repository, quotes=quotes
        )

    params = request.model_dump(mode="json")
    return await repository.create(
        params=params,
        api_key=api_key,
        service="market_enrich",
        rows_target=rows_target_for(request),
        retention_days=request.retention_days,
    )


async def _submit_from_quote(
    request: MarketRequest,
    *,
    api_key: str,
    repository: JobRepository,
    quotes: QuoteRepository,
) -> JobRecord:
    """Spend the quote, then create the job its frozen plan describes."""

    assert request.quote_id is not None  # narrowed by the caller
    consumed = await quotes.consume(request.quote_id, api_key=api_key, now=datetime.now(UTC))
    plan = QuotePlanV1.model_validate(consumed["plan"])

    job = await repository.create(
        params=frozen_params(request, plan, quote_id=consumed["id"]),
        api_key=api_key,
        service="market_enrich",
        # A quote freezes the universe: the row target is the frozen symbol
        # count, not whatever target_rows the caller's optimism requested.
        rows_target=len(plan.universe.symbols),
        retention_days=plan.retention_days,
    )
    await quotes.attach_job(consumed["id"], job["id"])
    return job


def frozen_params(
    request: MarketRequest, plan: QuotePlanV1, *, quote_id: str
) -> dict[str, Any]:
    """``jobs.params`` as the quote froze it (02 §4.1, gate 4).

    ``market`` becomes a watchlist of the frozen symbols even when the caller
    asked with a filter: the worker must execute a materialised universe, and a
    filter re-evaluated tomorrow is a different plan. Keys stay inside
    ``MarketSpec`` so ``MarketRequest.model_validate`` accepts the document in
    the worker (``extra="forbid"``).
    """

    params = request.model_dump(mode="json")
    params["market"] = {
        "kind": MarketKind.WATCHLIST.value,
        "symbols": list(plan.universe.symbols),
        "venue": plan.universe.venue or plan.universe.mic,
        "sector": plan.universe.sector,
    }
    params["fields"] = list(plan.fields)
    params["as_of"] = plan.freshness.as_of.isoformat()
    params["max_staleness_s"] = plan.freshness.max_staleness_s
    params["allow_delayed"] = plan.freshness.allow_delayed
    params["budget"] = plan.budget.model_dump(mode="json")
    params["retention_days"] = plan.retention_days
    params["quote_id"] = quote_id
    return params


async def cancel_job(job_id: str, *, repository: JobRepository) -> JobRecord:
    """Pending → immediate Failure; Processing → flag; terminal → 409."""

    return await repository.cancel(job_id)


async def retry_job(job_id: str, *, repository: JobRepository) -> JobRecord:
    """Requeue a ``Failure`` with a fresh attempt budget.

    Only a Failure can be retried: retrying a Success would replace a good
    artifact with a new guess, and retrying a Pending/Processing job would run
    the same work twice.
    """

    job = await repository.get(job_id)
    if job is None:
        raise JobNotFound(f"job {job_id} does not exist")
    if job["status"] != FAILURE:
        raise JobTerminal(f"job {job_id} is {job['status']}; only Failure can be retried")
    return await repository.retry(job_id)


async def owned_job(
    job_id: str, *, repository: JobRepository, api_key: str | None = None
) -> JobRecord:
    """Fetch a job, enforcing ownership **before** expiry or artifact lookup.

    A foreign job answers exactly like a missing one (``404``): otherwise the id
    becomes an existence oracle (02 §3.11).
    """

    job = await repository.get(job_id)
    if job is None or (api_key is not None and job["api_key"] != api_key):
        raise JobNotFound(f"job {job_id} does not exist")
    return job


async def job_view(
    job_id: str, *, repository: JobRepository, api_key: str | None = None
) -> dict[str, Any]:
    """The ``GET /requests/{id}`` document (02 §3.2)."""

    job = await owned_job(job_id, repository=repository, api_key=api_key)
    params = _json(job.get("params")) or {}
    usage = _json(job.get("usage")) or {}
    artifacts = await repository.artifacts(job_id)
    files = {
        artifact["kind"]: f"/requests/{job_id}/export/{artifact['kind']}"
        for artifact in artifacts
    }
    market = params.get("market") or {}

    return {
        "id": job["id"],
        "status": wire_status(job["status"]),
        "service": job["service"],
        "market": {"label": _market_label(market), "kind": market.get("kind")},
        "result_count": job["result_count"],
        "rows_target": job["rows_target"],
        "rows_unresolved": job["rows_unresolved"],
        "result_mode": job["result_mode"],
        "as_of_requested": params.get("as_of"),
        "as_of_achieved": usage.get("as_of_achieved"),
        "coverage": usage.get("coverage", {"filled_ratio": 0.0, "verified_ratio": 0.0}),
        "quality": usage.get("quality", {"by_column": {}}),
        "phases": usage.get("phases", {}),
        "providers": usage.get("providers", {}),
        "usage": {
            "requests_total": usage.get("requests_total", 0),
            "bytes_total": usage.get("bytes_total", 0),
            "credits_total": usage.get("credits_total", 0),
            "cache_hits": usage.get("cache_hits", 0),
        },
        "cost_usd": None,  # null until a PriceBook exists; never 0.0 (02 §3.2)
        "progress": usage.get("progress", {"batches_done": 0, "batches_total": 0, "rows_unique": 0}),
        "preview": [],  # Phase 5: rows stream to an artifact, not into the job row
        "files": files,
        "unresolved": usage.get("unresolved", []),
        "rejected_rows": usage.get("rejected_rows", 0),
        "expires_at": job["expires_at"],
        "error": job["error"],
        "retryable": bool(usage.get("retryable", False)) if job["status"] == FAILURE else False,
        "attempts": job["attempts"],
        "cancel_requested": bool(job["cancel_requested"]),
        "created_at": job["created_at"],
        "updated_at": job["updated_at"],
    }


def _json(raw: str | None) -> Any:
    if not raw:
        return None
    return json.loads(raw)


def _market_label(market: dict[str, Any]) -> str:
    """A human label for the job list; never used as an identifier."""

    kind = market.get("kind") or "market"
    symbols = market.get("symbols") or []
    if symbols:
        return f"{kind}: {', '.join(str(symbol) for symbol in symbols[:8])}"
    for key in ("index", "venue", "text"):
        if market.get(key):
            return f"{kind}: {market[key]}"
    return str(kind)


__all__ = [
    "FAILURE",
    "RequestNotServable",
    "cancel_job",
    "frozen_params",
    "job_view",
    "owned_job",
    "retry_job",
    "rows_target_for",
    "submit_enrich",
    "wire_status",
]
