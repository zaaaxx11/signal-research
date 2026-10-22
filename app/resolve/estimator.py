"""Plan and estimate: a projection that is never exact, never target-shaped.

Why the numbers look the way they do (02 §5 Fase 1 gate 5-6):

* ``exact`` is literally ``False``. A projection is not a measurement, and a
  boolean that sometimes flips to ``True`` is a promise the engine cannot keep.
* the estimate is a function of **(instruments × requested columns)** only.
  ``target_rows`` is echoed in ``plan.target_rows`` because the client asked for
  it, but it never moves ``low/mid/high``: an estimate that grows when the
  customer asks for more rows is a quote that depends on optimism.
* every batch is materialised (``universe_slice.symbols``), ordered by the
  documented key and cut at exactly ``MAX_BATCH_SYMBOLS``. A filter-only slice
  cannot be frozen by ``QuotePlanV1`` — ``universe.symbols`` is required — so a
  plan without symbols would be a quote that cannot be honoured.

``build_quote_document`` computes ``plan_hash`` **over the serialised document
minus the hash**, so the hash can be recomputed from what is stored. That is the
only way "two identical plans share a hash" (gate 5) can be checked by anyone
who has the row, not just by the process that wrote it.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Mapping

from app.contracts import (
    CANONICAL_COLUMNS_V1,
    MarketPlan,
    MarketRequest,
    QuotePlanV1,
)
from app.resolve.universe import Resolution

#: Hard rule from 02 §3.3: a batch never exceeds 100 symbols.
MAX_BATCH_SYMBOLS = 100

#: Placeholder so the frozen plan can be validated before its own hash exists.
_PLACEHOLDER_HASH = "sha256:" + "0" * 64

#: Rows a single instrument contributes are bounded by the columns asked for.
_MIN_ROWS = 1


def canonical_json(value: Any) -> str:
    """Canonical bytes for a JSON document (sorted keys, no spaces)."""

    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def select_fields(request: MarketRequest) -> tuple[str, ...]:
    """The columns this request will fill, in canonical order.

    ``fields=None`` means "the whole v1 contract". When a subset is asked for,
    it is stored in canonical order rather than caller order, so two callers
    who ask for the same set freeze the same bytes (and share a ``plan_hash``).
    """

    if request.fields is None:
        return CANONICAL_COLUMNS_V1
    wanted = set(request.fields)
    if not wanted:
        return CANONICAL_COLUMNS_V1
    return tuple(column for column in CANONICAL_COLUMNS_V1 if column in wanted)


def build_plan(resolution: Resolution, request: MarketRequest) -> dict[str, Any]:
    """Materialise the execution plan; validated by the frozen contracts."""

    if not resolution.instruments:
        # ``MarketPlan`` would accept ``batches=[]``; a plan that covers nothing
        # must not exist, because the quote built from it would promise nothing.
        raise ValueError("a plan over zero instruments cannot be quoted")

    fields = select_fields(request)
    rows = resolution.instruments
    symbols = [row.symbol for row in rows]

    batches: list[dict[str, Any]] = []
    for batch_index, start in enumerate(range(0, len(symbols), MAX_BATCH_SYMBOLS)):
        chunk_rows = rows[start : start + MAX_BATCH_SYMBOLS]
        chunk = [row.symbol for row in chunk_rows]
        batches.append(
            {
                "batch_index": batch_index,
                "universe_slice": {
                    "symbols": chunk,
                    "venue": _common_mic(chunk_rows),
                    "sector": _common_sector(chunk_rows),
                    "offset": start,
                    "limit": len(chunk),
                },
                "symbols_expected": len(chunk),
                "max_rows": max(len(chunk) * len(fields), _MIN_ROWS),
            }
        )

    document = MarketPlan.model_validate(
        {
            "batches": batches,
            "mode": request.mode,
            "target_rows": request.target_rows or 0,
        }
    ).model_dump(mode="json")
    return document


def build_estimate(
    resolution: Resolution, plan: Mapping[str, Any], request: MarketRequest
) -> dict[str, Any]:
    """A row-range projection with ordered bounds and an auditable basis."""

    fields = select_fields(request)
    instruments = len(resolution.instruments)
    batches = len(plan.get("batches") or [])

    mid = max(_MIN_ROWS, instruments * len(fields))
    low = max(_MIN_ROWS, int(mid * 0.85))
    high = max(mid, int(math.ceil(mid * 1.15)))
    confidence = round(min(0.99, max(0.05, resolution.confidence)), 4)

    basis = [
        f"{instruments} instruments resolved from the reference master ({resolution.label})",
        f"{len(fields)} canonical columns requested; rows are projected as instruments × columns",
        f"{batches} batch(es) of at most {MAX_BATCH_SYMBOLS} symbols, no provider called",
        "a projection is not a measurement: exact is always false (02 §5 Fase 1 gate 6)",
    ]
    if resolution.unresolved:
        basis.append(f"{len(resolution.unresolved)} input(s) unresolved and excluded")

    return {
        "low": low,
        "mid": mid,
        "high": high,
        "confidence": confidence,
        "basis": basis,
        "exact": False,
    }


def build_eta(
    resolution: Resolution, plan: Mapping[str, Any], request: MarketRequest
) -> tuple[int | None, str | None]:
    """A deterministic ETA projection; ``None`` when there is nothing to run."""

    if resolution.needs_narrowing or not plan.get("batches"):
        return None, None
    batches = len(plan["batches"])
    instruments = len(resolution.instruments)
    per_batch = math.ceil(instruments / max(1, batches))
    # 3 s fixed cost per batch + 1 s per 25 instruments; a projection, not a promise.
    seconds = 3 * batches + max(1, math.ceil(per_batch / 25))
    return seconds, (
        "projected from batch count and instrument count at the Phase-2 baseline; "
        "no provider has been called, so this is an estimate (02 §3.2)"
    )


def build_quote_document(
    resolution: Resolution, plan: Mapping[str, Any], request: MarketRequest
) -> dict[str, Any]:
    """The ``QuotePlanV1`` a quote freezes, including a recomputable hash.

    All plan symbols are frozen as one ordered list. Batches are an execution
    detail (a retry may re-chunk); the quote is about *which instruments* and
    *which columns*, so a later re-batch cannot change what was sold.
    """

    fields = list(select_fields(request))
    symbols = [symbol for batch in plan.get("batches") or [] for symbol in batch["universe_slice"]["symbols"]]
    if not symbols:
        raise ValueError("a quote without symbols cannot be honoured")
    mic = _common_mic(resolution.instruments)

    draft = QuotePlanV1.model_validate(
        {
            "universe": {
                "symbols": symbols,
                "mic": mic,
                "venue": resolution.spec.venue,
                "sector": resolution.spec.sector,
                "index": resolution.spec.index,
            },
            "fields": fields,
            "freshness": {
                "as_of": request.as_of.isoformat(),
                "max_staleness_s": request.max_staleness_s,
                "allow_delayed": request.allow_delayed,
            },
            "budget": request.budget.model_dump(mode="json"),
            "retention_days": request.retention_days,
            "columns_version": "v1",
            "plan_hash": _PLACEHOLDER_HASH,
        }
    ).model_dump(mode="json")

    body = {key: value for key, value in draft.items() if key != "plan_hash"}
    digest = "sha256:" + hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()
    document = QuotePlanV1.model_validate({**draft, "plan_hash": digest}).model_dump(mode="json")
    return document


def plan_hash_of(document: Mapping[str, Any]) -> str:
    """Recompute the hash of a stored quote plan (used by tests and operators)."""

    body = {key: value for key, value in document.items() if key != "plan_hash"}
    return "sha256:" + hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


def _common_mic(rows: Any) -> str | None:
    mics = {row.mic for row in rows}
    return next(iter(mics)) if len(mics) == 1 else None


def _common_sector(rows: Any) -> str | None:
    sectors = {row.sector for row in rows}
    if len(sectors) != 1:
        return None
    value = next(iter(sectors))
    return value if isinstance(value, str) else None


__all__ = [
    "MAX_BATCH_SYMBOLS",
    "build_estimate",
    "build_eta",
    "build_plan",
    "build_quote_document",
    "canonical_json",
    "plan_hash_of",
    "select_fields",
]
