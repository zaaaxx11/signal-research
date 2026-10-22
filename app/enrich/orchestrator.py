"""Enrichment orchestrator: phases in order, provenance, checkpoints (02 §3.5).

Two entrypoints, both driven by the production worker — never by a test-only
harness:

``run_phases(phases, ...)``
    Executes the phase table over the rows an earlier fetch produced. Each phase
    stamps provenance on the cells it owns, conflation decides who may write a
    cell, and every phase returns a report so ``skipped_by_budget`` is auditable
    per phase instead of being a single number in a log line.

``run_batches(job_id, batches, ...)``
    The checkpointed batch loop: ``ensure_batches`` (idempotent on
    ``UNIQUE(job_id, batch_index)``), one engine call per executed batch, a sink
    per batch, ``row_count`` written with **set** semantics. A batch already
    marked ``Success`` is not bought twice; a sink failure marks the batch
    ``Failure`` and propagates, so the next run resumes there.

Design rules this module enforces rather than documents:

* **Only P0 and P7 are fatal.** A phase that is not fatal degrades to empty
  cells and reports ``partial``/``failed``; it never takes the job down.
* **Provenance is exact by construction.** Each phase attributes the filled
  cells it owns; ``reconcile`` then proves ``set(provenance) == filled_cells`
  for every row, and drops records for cells a later phase emptied.
* **A phase that did not run says so.** ``skipped`` is a report status, not a
  silent success: an unwired capability must not look like "no data exists".
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime
from typing import Any, Awaitable, Callable, Iterable, Mapping, Sequence

from app.contracts import (
    CANONICAL_COLUMNS_V1,
    DERIVED_QUALITY_COLUMNS,
    MarketRequest,
)
from app.enrich import conflate, quality
from app.enrich.phases import Phase
from app.enrich.provenance import (
    PROVENANCE_KEY,
    coerce_as_of,
    drop_entry,
    make_entry,
    provenance,
    reconcile,
    set_entry,
)
from app.jobs.ports import BatchRepository
from app.jobs.repository import BATCH_FAILURE, BATCH_PROCESSING, BATCH_SUCCESS
from app.verify import quality as verify_quality
from app.verify import rules as verify_rules

logger = logging.getLogger("mee.enrich")

#: Statuses a phase report may carry (02 §3.5 + Fase 3 RED contract).
REPORT_STATUSES: frozenset[str] = frozenset({"ok", "partial", "skipped", "failed"})

#: The engine derives this cell from data it already holds; it is an aggregator
#: of its inputs, never an official source, so it sits at trust 3.
ENGINE_SOURCE = "engine:quality"
ENGINE_TRUST = 3

#: Sources that are not registry providers. Synthetic data is deliberately at the
#: bottom of the trust scale: a fixture that calls itself "fake" must never
#: outrank a real vendor, whatever its ProviderSpec says.
SYNTHETIC_SOURCES: frozenset[str] = frozenset({"fake", "engine", "synthetic"})

#: Rows per chunk when a consumer streams the table (02 §5 Fase 3 gate 6).
EXPORT_CHUNK_SIZE = 5000

_CANONICAL: frozenset[str] = frozenset(CANONICAL_COLUMNS_V1)

EngineCall = Callable[[Sequence[str]], Awaitable[Iterable[Mapping[str, Any]]]]
SinkCall = Callable[[int, Sequence[Mapping[str, Any]]], Awaitable[Any]]
ExportCall = Callable[[list[dict[str, Any]], Mapping[str, Any]], Awaitable[None]]


# ---------------------------------------------------------------------------
# Attribution helpers
# ---------------------------------------------------------------------------


def source_trust(source: str | None) -> int:
    """Trust (1..4) for a source name, from the registry, defaulting to scrape.

    An unknown source is not assumed to be good: unlisted means unvetted, and
    unvetted means 4.
    """

    if not source or not str(source).strip():
        return 4
    name = str(source).strip()
    if name in SYNTHETIC_SOURCES:
        return 4
    try:
        from app.providers.registry import provider_named

        spec = provider_named(name)
    except Exception:  # noqa: BLE001 - an unknown name must not fail a job
        spec = None
    trust = getattr(spec, "trust", None)
    if isinstance(trust, int) and not isinstance(trust, bool) and 1 <= trust <= 4:
        return trust
    return 4


def _row_source(row: Mapping[str, Any]) -> str:
    """The provider that produced this row, or ``engine`` when it is unnamed."""

    source = row.get("primary_source")
    if isinstance(source, str) and source.strip():
        return source.strip()
    return "engine"


def _as_of_text(value: Any) -> str | None:
    """Normalise a cell value into an aware ISO timestamp, or ``None``."""

    if value is None:
        return None
    if isinstance(value, datetime | date):
        try:
            return coerce_as_of(value)
        except ValueError:
            return None
    if isinstance(value, str) and value.strip():
        text = value.strip()
        # A date has no zone; midnight UTC is the only reading that is not a guess.
        if len(text) == 10 and text[4] == "-" and text[7] == "-":
            text = text + "T00:00:00+00:00"
        try:
            return coerce_as_of(text)
        except ValueError:
            return None
    return None


def row_as_of(
    row: Mapping[str, Any],
    *,
    request: MarketRequest | None = None,
    clock: Callable[[], datetime] | None = None,
) -> str:
    """The time of the data in this row, preferring observation time over clock."""

    for column in ("as_of_achieved", "quote_as_of", "last_bar_date", "session_date"):
        text = _as_of_text(row.get(column))
        if text is not None:
            return text
    if request is not None:
        return coerce_as_of(request.as_of)
    return coerce_as_of((clock or _utcnow)())


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _stamp(
    row: dict[str, Any],
    columns: Sequence[str],
    *,
    source: str,
    as_of: str,
    trust: int,
    status: str = "ok",
) -> int:
    """Attach provenance for the filled ``columns``; return how many were added.

    A blank cell loses its record: provenance for a value the row does not carry
    is worse than no record, because it makes the audit pass while lying.
    """

    record = provenance(row)
    added = 0
    for column in columns:
        if column in DERIVED_QUALITY_COLUMNS:
            continue
        if not quality.is_filled(row.get(column)):
            if column in record:
                drop_entry(row, column)
            continue
        if column in record:
            continue
        set_entry(
            row,
            column,
            make_entry(source=source, as_of=as_of, status=status, trust=trust),
        )
        added += 1
    return added


def _copy_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Copy a row plus a private copy of its provenance mapping.

    ``run_phases`` must not mutate the caller's dict (or its nested provenance)
    behind its back: the worker still holds the list it passed in.
    """

    copied = dict(row)
    record = row.get(PROVENANCE_KEY)
    if isinstance(record, dict):
        copied[PROVENANCE_KEY] = {column: dict(entry) for column, entry in record.items()}
    return copied


def _report(
    phase: Phase,
    *,
    status: str,
    skipped_by_budget: int = 0,
    rows: int = 0,
    filled: int = 0,
    reason: str | None = None,
) -> dict[str, Any]:
    """One phase report. Every report carries the four keys the view renders."""

    if status not in REPORT_STATUSES:  # pragma: no cover - programmer error
        raise ValueError(f"unknown phase status {status!r}")
    report: dict[str, Any] = {
        "name": phase.name,
        "capability": phase.capability.value if phase.capability is not None else None,
        "status": status,
        "skipped_by_budget": int(skipped_by_budget),
        "rows": int(rows),
        "filled": int(filled),
        "fatal": bool(phase.fatal),
    }
    if reason:
        report["reason"] = reason
    return report


def _has_any(row: Mapping[str, Any], columns: Sequence[str]) -> bool:
    return any(quality.is_filled(row.get(column)) for column in columns)


def _freshness_seconds(row: Mapping[str, Any], now: datetime) -> int | None:
    """Age of the quote in whole seconds; ``None`` when it is unreadable/future."""

    observed = _as_of_text(row.get("quote_as_of"))
    if observed is None:
        return None
    try:
        moment = datetime.fromisoformat(observed)
    except ValueError:  # pragma: no cover - _as_of_text already parsed it
        return None
    age = int((now - moment).total_seconds())
    return age if age >= 0 else None


#: Regular-session close V13 measures "off session" against. The row carries a
#: session date, not an exchange calendar, so one documented default beats an
#: invented per-venue table; the request's ``max_staleness_s`` overrides it.
DEFAULT_SESSION_CLOSE_UTC = "20:00:00+00:00"


def _session_close(row: Mapping[str, Any]) -> str | None:
    """``session_date`` + the documented close, as an aware ISO timestamp."""

    text = str(row.get("session_date") or "").strip()
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return f"{text[:10]}T{DEFAULT_SESSION_CLOSE_UTC}"
    return None


def _verification_context(
    row: Mapping[str, Any], *, now: str, request: MarketRequest | None
) -> dict[str, Any]:
    """The explicit context V1..V16 may read (02 §3.7). No rule guesses one."""

    return {
        "now": now,
        "asset_class": row.get("asset_class") or "equity",
        "venue": row.get("mic"),
        "market_state": row.get("market_state") or "closed",
        "session_date": row.get("session_date"),
        "session_close": _session_close(row),
        "max_staleness_s": getattr(request, "max_staleness_s", None) if request is not None else None,
        "allow_delayed": bool(getattr(request, "allow_delayed", False)) if request is not None else False,
        # A row carries no multi-class marker and no index weight vector; the
        # context says so instead of letting a rule assume the permissive case.
        "multi_class": False,
        "quote_currency": row.get("quote_currency") or row.get("currency"),
        "risk_free_rate": 0.0,
        "candidates": {},
        "index": {},
    }


def _verify_rows(
    rows: Sequence[dict[str, Any]], *, now: str, request: MarketRequest | None, job_id: str | None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Run V1..V16 over ``rows`` in place; return ``(rejections, disputed cells)``.

    A rejected cell is empty afterwards, so ``verified_columns_count`` equals the
    cells that survived — never a claim of verification for a repaired value.
    """

    rejections: list[dict[str, Any]] = []
    disputed: list[dict[str, Any]] = []
    for row in rows:
        applied, found = verify_rules.apply(
            row, context=_verification_context(row, now=now, request=request)
        )
        row.clear()
        row.update(applied)
        verified = quality.filled_columns(row)
        row["filled_columns_count"] = verified
        row["verified_columns_count"] = verified
        for column, entry in provenance(row).items():
            if isinstance(entry, dict) and quality.is_filled(row.get(column)):
                # The verifier examined this cell and left it standing.
                entry["verified"] = True
        for item in found:
            rejections.append(dict(item))
            if str(item.get("rule")) == "V14":
                # A disputed cell keeps its (highest-trust) value: it is not a
                # rejected cell, so the quality report must not count it twice.
                disputed.append(
                    {"instrument_id": item.get("instrument_id"), "column": item.get("column")}
                )
    logger.info(
        "verify done job_id=%s rows=%d rejections=%d disputed=%d",
        job_id,
        len(rows),
        len(rejections),
        len(disputed),
    )
    return rejections, disputed


# ---------------------------------------------------------------------------
# run_phases
# ---------------------------------------------------------------------------


async def run_phases(
    phases: Sequence[Phase],
    *,
    rows: Iterable[Mapping[str, Any]] | None = None,
    observations: Mapping[str, Sequence[Any]] | None = None,
    job_id: str | None = None,
    request: MarketRequest | None = None,
    export: ExportCall | None = None,
    skipped_by_budget: Mapping[str, int] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """Run ``phases`` in order and return the rows plus one report per phase."""

    plan = tuple(phases)
    if not plan:
        raise ValueError("run_phases requires at least one phase")
    for phase in plan:
        if not isinstance(phase, Phase):
            raise TypeError(f"run_phases expects Phase records, got {type(phase).__name__}")

    now = clock or _utcnow
    skips = {name: max(0, int(count or 0)) for name, count in (skipped_by_budget or {}).items()}
    per_row_observations = dict(observations or {})
    materialised = [_copy_row(row) for row in (rows or ())]
    reports: list[dict[str, Any]] = []
    rejected = 0
    #: P6 artefacts. They exist even when the plan has no ``verify`` phase, so a
    #: caller never has to guess whether "no rejections" means "clean" or "not run".
    rejections_all: list[dict[str, Any]] = []
    disputed_all: list[dict[str, Any]] = []
    quality_blocks: dict[str, dict[str, Any]] = {}
    reconciled = True
    verdict: str | None = None

    for phase in plan:
        skip = skips.get(phase.name, 0)
        report: dict[str, Any]

        if phase.name == "reference":
            kept: list[dict[str, Any]] = []
            rejected = 0
            for row in materialised:
                if quality.missing_core_columns(row):
                    rejected += 1
                    continue
                _stamp(
                    row,
                    phase.columns,
                    source=_row_source(row),
                    as_of=row_as_of(row, request=request, clock=now),
                    trust=source_trust(_row_source(row)),
                )
                kept.append(row)
            materialised = kept
            report = _report(
                phase,
                status="ok" if rejected == 0 else "partial",
                skipped_by_budget=skip,
                rows=len(kept),
                filled=sum(len(provenance(row)) for row in kept),
                reason=(
                    None
                    if rejected == 0
                    else f"{rejected} row(s) rejected: no core identity (02 §3.8.1)"
                ),
            )

        elif phase.name == "quote":
            quoted = 0
            filled = 0
            for row in materialised:
                documents = per_row_observations.get(str(row.get("instrument_id") or ""))
                if documents:
                    # Conflation, not assignment: fill-only-empty plus trust
                    # ordering decides who may write a quote cell.
                    conflate.merge(row, documents)
                source = _row_source(row)
                filled += _stamp(
                    row,
                    phase.columns,
                    source=source,
                    as_of=row_as_of(row, request=request, clock=now),
                    trust=source_trust(source),
                )
                if _has_any(row, phase.columns):
                    quoted += 1
            if not materialised:
                status, reason = "skipped", "no row to quote"
            elif quoted == 0:
                status, reason = "failed", "no quote cell was observed for any row"
            elif quoted < len(materialised):
                status = "partial"
                reason = f"{len(materialised) - quoted} row(s) carry no quote cell"
            elif skip:
                status, reason = "partial", f"{skip} rung(s) skipped by budget"
            else:
                status, reason = "ok", None
            report = _report(
                phase,
                status=status,
                skipped_by_budget=skip,
                rows=quoted,
                filled=filled,
                reason=reason,
            )

        elif phase.name == "verify":
            # P6: V1..V16 run here, once, on rows the earlier phases produced.
            # A rejected cell is nulled (or its documented series/flag effect is
            # applied), so nothing unverified can reach P7.
            moment = now()
            for row in materialised:
                row["data_freshness_s"] = _freshness_seconds(row, moment)
            found, disputed = _verify_rows(
                materialised, now=coerce_as_of(moment), request=request, job_id=job_id
            )
            rejections_all.extend(found)
            disputed_all.extend(disputed)
            quality_blocks = verify_quality.column_blocks(
                materialised,
                columns=CANONICAL_COLUMNS_V1,
                rejections=[item for item in found if str(item.get("rule")) != "V14"],
                disputed=disputed,
            )
            try:
                verify_quality.reconcile(
                    quality_blocks, row_count=len(materialised), columns=CANONICAL_COLUMNS_V1
                )
                reconciled = True
            except verify_quality.QualityError as exc:
                # A report that does not reconcile is a Failure, not a Success
                # with interesting numbers (Fase 4 gate 5).
                reconciled = False
                logger.error("quality reconciliation failed job_id=%s: %s", job_id, exc)
            verdict = verify_quality.verdict(
                quality_blocks, rejections=rejections_all, reconciled=reconciled
            )
            for row in materialised:
                row["data_quality"] = quality.classify(row)
            filled = 0
            for row in materialised:
                source = _row_source(row)
                filled += _stamp(
                    row,
                    ("primary_source", "as_of_achieved"),
                    source=source,
                    as_of=row_as_of(row, request=request, clock=now),
                    trust=source_trust(source),
                )
                filled += _stamp(
                    row,
                    ("data_freshness_s",),
                    source=ENGINE_SOURCE,
                    as_of=coerce_as_of(moment),
                    trust=ENGINE_TRUST,
                )
            if not materialised:
                status, reason = "skipped", "no row to verify"
            elif not reconciled:
                status = "ok"
                reason = f"quality report does not reconcile; verdict {verdict}"
            elif found:
                status = "ok"
                rules_hit = sorted({str(item.get("rule")) for item in found})
                reason = (
                    f"V1..V16: {len(found)} rejection(s) in {len(materialised)} row(s) "
                    f"({', '.join(rules_hit)}); verdict {verdict}"
                )
            else:
                status, reason = "ok", f"V1..V16: all {len(materialised)} row(s) clean; verdict {verdict}"
            report = _report(
                phase,
                status=status,
                skipped_by_budget=skip,
                rows=len(materialised),
                filled=sum(int(row.get("verified_columns_count") or 0) for row in materialised),
                reason=reason,
            )

        elif phase.name == "export":
            if verdict == "REJECT":
                # 04 §5.8: exporting under REJECT is forbidden. The job fails in
                # the runner *after* the rejections are persisted, so the audit
                # survives the failure instead of vanishing with the file.
                report = _report(
                    phase,
                    status="skipped",
                    skipped_by_budget=skip,
                    rows=len(materialised),
                    reason=(
                        "export refused: verification verdict is REJECT "
                        "(04 §5.8); no artifact was written"
                    ),
                )
            elif export is None:
                report = _report(
                    phase,
                    status="skipped",
                    skipped_by_budget=skip,
                    rows=len(materialised),
                    reason="no export sink was supplied to the orchestrator",
                )
            else:
                try:
                    # The export sink receives the *verified* table and the
                    # quality/rejection report the verify phase just produced.
                    # A manifest that claims a quality block has to be handed the
                    # real numbers instead of re-deriving them from the rows
                    # (a re-derivation would go blind to the rules that fired).
                    await export(
                        materialised,
                        {
                            "verdict": verdict,
                            "reconciled": reconciled,
                            "rejections": [dict(item) for item in rejections_all],
                            "quality": {str(name): dict(block) for name, block in quality_blocks.items()},
                        },
                    )
                except Exception as exc:
                    reports.append(
                        _report(
                            phase,
                            status="failed",
                            skipped_by_budget=skip,
                            rows=len(materialised),
                            reason=f"{type(exc).__name__}: {exc}",
                        )
                    )
                    if phase.fatal:
                        raise
                    continue
                report = _report(
                    phase,
                    status="ok",
                    skipped_by_budget=skip,
                    rows=len(materialised),
                )

        else:
            # A capability phase with no adapter wired into the worker yet. It
            # still attributes any cell a row already carries (a richer fixture
            # or a future adapter), and otherwise reports `skipped` — never `ok`.
            filled = 0
            for row in materialised:
                source = _row_source(row)
                filled += _stamp(
                    row,
                    phase.columns,
                    source=source,
                    as_of=row_as_of(row, request=request, clock=now),
                    trust=source_trust(source),
                )
            report = _report(
                phase,
                status="ok" if filled else "skipped",
                skipped_by_budget=skip,
                rows=len(materialised),
                filled=filled,
                reason=None if filled else _unwired_reason(phase),
            )

        reports.append(report)

    _audit_provenance(materialised, plan, request=request, clock=now, job_id=job_id)
    logger.info(
        "phases done job_id=%s rows=%d phases=%s",
        job_id,
        len(materialised),
        ",".join(f"{report['name']}:{report['status']}" for report in reports),
    )
    return {
        "job_id": job_id,
        "rows": materialised,
        "phase_reports": reports,
        "rejected_rows": rejected,
        "rejections": rejections_all,
        "disputed": disputed_all,
        "quality": quality_blocks,
        "verdict": verdict,
        "quality_reconciled": reconciled,
    }


def _unwired_reason(phase: Phase) -> str:
    if phase.name == "performance":
        return "derived from EOD bars; no bars were observed"
    if phase.capability is None:
        return f"phase {phase.name!r} has no implementation wired yet"
    return (
        f"no {phase.capability.value} adapter is wired into the worker yet; "
        "the phase is declared and reported, not silently successful"
    )


def _audit_provenance(
    rows: Sequence[dict[str, Any]],
    phases: Sequence[Phase],
    *,
    request: MarketRequest | None,
    clock: Callable[[], datetime],
    job_id: str | None,
) -> None:
    """Make ``set(provenance) == filled_cells`` exact for every row.

    A filled cell with no record would have an unknown origin, so it is
    attributed to the phase that owns its column — using that row's real source
    and time, never a fabricated "unknown". Records for cells that were emptied
    are dropped, because a value the row does not carry cannot have a source.
    """

    owners: dict[str, Phase] = {
        column: phase for phase in phases for column in phase.columns if column in _CANONICAL
    }
    problems: list[str] = []
    for row in rows:
        _orphans, missing = reconcile(row)
        for column in sorted(missing):
            phase = owners.get(column)
            if column == "data_freshness_s":
                source, trust = ENGINE_SOURCE, ENGINE_TRUST
            elif phase is not None:
                source, trust = _row_source(row), source_trust(_row_source(row))
            else:  # pragma: no cover - every canonical column has an owner
                source, trust = "engine", 4
                problems.append(f"{row.get('instrument_id')}: {column} has no owning phase")
            set_entry(
                row,
                column,
                make_entry(
                    source=source,
                    as_of=row_as_of(row, request=request, clock=clock),
                    trust=trust,
                ),
            )
    if problems:  # pragma: no cover - the partition invariant makes this unreachable
        logger.warning("provenance audit job_id=%s %s", job_id, "; ".join(problems))


# ---------------------------------------------------------------------------
# run_batches
# ---------------------------------------------------------------------------


def _json_slice(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, Mapping) else {}
    return {}


def batch_symbols(batch: Mapping[str, Any]) -> list[str]:
    """The symbols a batch covers, from either plan shape."""

    symbols = batch.get("symbols")
    if symbols is None:
        symbols = _json_slice(batch.get("universe_slice")).get("symbols")
    return [str(symbol) for symbol in (symbols or [])]


def _unique_rows(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate by ``instrument_id`` — the only identity this engine has.

    A duplicated row from an engine (or two batches covering the same symbol)
    must not become a duplicated row in the table: ``row_count`` is unique rows
    per job (02 §3.5 idempotency).
    """

    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for row in rows:
        identifier = row.get("instrument_id")
        if isinstance(identifier, str) and identifier:
            if identifier in seen:
                continue
            seen.add(identifier)
        unique.append(dict(row))
    return unique


async def run_batches(
    job_id: str,
    batches: Sequence[Mapping[str, Any]],
    *,
    repository: BatchRepository,
    engine: EngineCall,
    sink: SinkCall,
    resume: bool = True,
) -> dict[str, Any]:
    """Execute the batch plan with checkpoints; skip ``Success`` batches on resume."""

    plan = list(batches)
    stored = {
        int(entry["batch_index"]): entry
        for entry in await repository.ensure_batches(job_id, plan)
    }

    skipped = 0
    executed = 0
    rows_unique = 0

    for entry in plan:
        index = int(entry["batch_index"])
        record = stored.get(index)
        if resume and record is not None and record["status"] == BATCH_SUCCESS:
            # Already bought and paid for: re-running it would spend a second
            # request for a row set the artifact still holds.
            skipped += 1
            rows_unique += int(record["row_count"] or 0)
            continue

        symbols = batch_symbols(entry)
        await repository.mark_batch(job_id, index, status=BATCH_PROCESSING)
        try:
            produced = await engine(symbols)
            unique = _unique_rows(produced)
            await sink(index, unique)
        except Exception as exc:
            await repository.mark_batch(
                job_id,
                index,
                status=BATCH_FAILURE,
                row_count=0,
                error=f"{type(exc).__name__}: {exc}",
            )
            raise

        await repository.mark_batch(job_id, index, status=BATCH_SUCCESS, row_count=len(unique))
        executed += 1
        rows_unique += len(unique)

    outcome = {
        "job_id": job_id,
        "batches_total": len(plan),
        "batches_done": executed + skipped,
        "batches_executed": executed,
        "batches_skipped": skipped,
        "rows_unique": rows_unique,
    }
    logger.info(
        "batches done job_id=%s total=%d executed=%d skipped=%d rows_unique=%d",
        job_id,
        outcome["batches_total"],
        executed,
        skipped,
        rows_unique,
    )
    return outcome


__all__ = [
    "ENGINE_SOURCE",
    "ENGINE_TRUST",
    "EXPORT_CHUNK_SIZE",
    "REPORT_STATUSES",
    "batch_symbols",
    "row_as_of",
    "run_batches",
    "run_phases",
    "source_trust",
]
