"""Shared helpers and fixtures for the Phase-3 RED suite (02 §3.5-§3.7, §4.2, §4.5, §4.6, §5).

Not a test module (leading underscore), so pytest never collects it. Every
contract this module pins is expanded in ``briefs/market-engine/14-phase3-red.md``.

Contracts pinned here
---------------------

* **Phase data lives in ``app.enrich.phases``.** ``Phase`` is a frozen record with
  ``name``, ``capability``, ``columns`` and ``fatal``; ``DEFAULT_PHASES`` is the
  ordered tuple P0..P7 from 02 §3.5. The names are the eight documented ones and
  the phase columns partition the 92 canonical columns without overlap.
* **The production entrypoint drives the orchestrator.** ``process_job`` must
  call ``app.enrich.orchestrator.run_phases`` and hand it ``DEFAULT_PHASES``
  explicitly; a default argument would make the production order unobservable
  (the ``FallbackLadder`` lesson, 02 §5 Fase 3 gate 1).
* **Orchestrator result shape.** ``run_phases`` returns a mapping (or an object
  with the same attributes) holding ``rows`` (the enriched rows, provenance
  included) and ``phase_reports`` (one report per phase, in order, each with
  ``name``, ``capability``, ``status``, ``skipped_by_budget``).
* **Conflation.** ``app.enrich.conflate.write_if_empty(row, column, value, obs)``
  writes a cell only when it is empty, or when the incoming observation has a
  strictly better trust (lower number), or when it is verified and the previous
  value was not. It returns ``True`` only when the row changed.
* **Provenance.** Every written cell gets an entry in
  ``row[PROVENANCE_KEY]`` carrying ``source``, ``as_of``, ``status``, ``trust``
  and ``verified``. ``app.enrich.provenance.provenance(row)`` returns that
  mapping; ``filled_cells(row)`` returns the canonical data columns that hold a
  value (the derived quality columns are computed, not observed).
* **Checkpoints.** ``SqliteJobRepository`` grows ``ensure_batches`` /
  ``mark_batch`` / ``batches``. ``mark_batch`` is an upsert with *set* semantics:
  replaying a batch replaces ``row_count`` instead of adding to it.
* **Batch execution.** ``app.enrich.orchestrator.run_batches(job_id, batches,
  *, repository, engine, sink, resume=True)`` skips batches already marked
  ``Success`` (when ``resume`` is on), calls ``engine(symbols)`` once per
  executed batch, hands the rows to ``sink(batch_index, rows)``, and records the
  unique ``row_count``. A sink failure marks the batch ``Failure`` and
  propagates; the next run resumes from the first non-``Success`` batch.
* **Streaming.** ``app.enrich.stream.RowStream(rows, chunk_size=...)`` yields
  lists of at most ``chunk_size`` rows, reads no further than one chunk ahead,
  and exposes ``.count``. It never calls ``list(rows)``.
"""

from __future__ import annotations

import importlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence

import pytest

from app.contracts import (
    CANONICAL_COLUMNS_V1,
    DERIVED_QUALITY_COLUMNS,
    Capability,
    columns_in_group,
)
from app.jobs.repository import SqliteJobRepository
from app.worker.runner import process_job

ROOT = Path(__file__).resolve().parents[1]
CLIENT_KEY = "phase3-client-key-4b7e2d91"

#: Phase order from 02 §3.5 (P0..P7).
PHASE_NAMES: tuple[str, ...] = (
    "reference",
    "quote",
    "eod",
    "performance",
    "fundamentals",
    "options",
    "verify",
    "export",
)

#: Capabilities per phase; ``None`` means "derived, no provider call".
PHASE_CAPABILITIES: tuple[Capability | None, ...] = (
    Capability.REFERENCE,
    Capability.QUOTE,
    Capability.EOD_BARS,
    None,
    Capability.FUNDAMENTALS,
    Capability.OPTIONS,
    None,
    None,
)

#: Phase that makes a row fail (reference) and the one that fails the job (export).
FATAL_PHASES: frozenset[str] = frozenset({"reference", "export"})

#: Provenance statuses a record may carry. ``ok`` is the normal one; ``stale``,
#: ``disputed`` and ``rejected`` are produced by the verifier (Phase 4).
PROVENANCE_STATUSES: frozenset[str] = frozenset(
    {"ok", "partial", "stale", "disputed", "rejected", "estimated"}
)

#: Keys a provenance record must expose (02 §4.6 + §3.5 trust ordering).
PROVENANCE_FIELDS: frozenset[str] = frozenset(
    {"source", "as_of", "status", "trust", "verified"}
)

#: Streaming gate: 5.000 instruments, 100-row chunks, ~2 KiB per row.
STREAM_ROWS = 5000
CHUNK_SIZE = 100
ROW_PAYLOAD_BYTES = 2048


# ---------------------------------------------------------------------------
# Module loading with RED-shaped failures
# ---------------------------------------------------------------------------


def _load_module(module_name: str) -> Any:
    """Import a Phase-3 module, turning a missing module into a RED failure."""

    try:
        return importlib.import_module(module_name)
    except ModuleNotFoundError as exc:  # pragma: no cover - RED path
        raise NotImplementedError(
            f"{module_name} is not implemented yet (Fase 3 RED): {exc}"
        ) from exc


def _attribute(module: Any, *names: str) -> Any:
    values: list[Any] = []
    for name in names:
        try:
            values.append(getattr(module, name))
        except AttributeError as exc:  # pragma: no cover - RED path
            raise NotImplementedError(
                f"{module.__name__}.{name} is missing (Fase 3 RED)"
            ) from exc
    return values[0] if len(values) == 1 else tuple(values)


def phase_class() -> type:
    return _attribute(_load_module("app.enrich.phases"), "Phase")


def default_phases() -> tuple[Any, ...]:
    phases = _attribute(_load_module("app.enrich.phases"), "DEFAULT_PHASES")
    if not isinstance(phases, (tuple, list)):
        raise NotImplementedError("DEFAULT_PHASES must be an ordered sequence of Phase records")
    return tuple(phases)


def conflate_module() -> Any:
    module = _load_module("app.enrich.conflate")
    _attribute(module, "write_if_empty")
    return module


def provenance_module() -> Any:
    module = _load_module("app.enrich.provenance")
    _attribute(module, "PROVENANCE_KEY", "provenance", "filled_cells")
    return module


def row_stream_class() -> type:
    return _attribute(_load_module("app.enrich.stream"), "RowStream")


def run_batches_fn() -> Callable[..., Any]:
    return _attribute(_load_module("app.enrich.orchestrator"), "run_batches")


def batch_api(repository: Any) -> Any:
    """Return the repository once it exposes the Phase-3 checkpoint surface."""

    missing = [
        name
        for name in ("ensure_batches", "mark_batch", "batches")
        if not hasattr(repository, name)
    ]
    if missing:
        raise NotImplementedError(
            "SqliteJobRepository must expose "
            + ", ".join(missing)
            + " for per-batch checkpoints (02 §4.2, Fase 3 RED)"
        )
    return repository


# ---------------------------------------------------------------------------
# Canonical partitions and small predicates
# ---------------------------------------------------------------------------


def expected_phase_columns() -> dict[str, tuple[str, ...]]:
    """The 92 columns split by the phase that fills them (02 §3.5 table)."""

    return {
        "reference": columns_in_group("Identity") + columns_in_group("Classification"),
        "quote": columns_in_group("Quote") + columns_in_group("Market/session"),
        "eod": columns_in_group("EOD & technical"),
        "performance": columns_in_group("Performance"),
        "fundamentals": columns_in_group("Fundamentals") + columns_in_group("Statements"),
        "options": columns_in_group("Options"),
        "verify": columns_in_group("Quality"),
        "export": (),
    }


def cap_value(value: Any) -> Any:
    """Compare ``Capability.QUOTE`` and ``"quote"`` without caring which is used."""

    return getattr(value, "value", value)


def blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    return False


def expected_filled_cells(row: Mapping[str, Any]) -> set[str]:
    """Canonical data columns holding a value; the derived three are excluded."""

    return {
        column
        for column in CANONICAL_COLUMNS_V1
        if column not in DERIVED_QUALITY_COLUMNS and not blank(row.get(column))
    }


def is_iso_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def observation(
    field: str = "pe_ttm",
    value: Any = 20.0,
    *,
    source: str = "L1",
    trust: int = 1,
    as_of: str = "2026-10-21T20:00:00+00:00",
    instrument_id: str = "XNAS:AAPL",
    status: str = "ok",
    verified: bool = False,
) -> dict[str, Any]:
    """One observation document, including the conflate ``verified`` flag."""

    return {
        "instrument_id": instrument_id,
        "field": field,
        "value_num": value if isinstance(value, (int, float)) else None,
        "value_text": value if isinstance(value, str) else None,
        "unit": "ratio",
        "source": source,
        "source_id": None,
        "as_of": as_of,
        "fetched_at": as_of,
        "confidence": 1.0,
        "verdict": status,
        "trust": trust,
        "verified": verified,
        "raw_sha256": None,
        "job_id": None,
    }


def empty_row(**cells: Any) -> dict[str, Any]:
    """A row with every canonical column present but empty, plus ``cells``."""

    row: dict[str, Any] = {column: None for column in CANONICAL_COLUMNS_V1}
    row.update(cells)
    return row


# ---------------------------------------------------------------------------
# Production-entrypoint probe
# ---------------------------------------------------------------------------


class PhaseProbe:
    """Callable stand-in for ``run_phases`` that records the production call.

    It delegates to the real implementation, so a job still runs end to end and
    the probe never turns a broken orchestrator into a passing test.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self._original: Callable[..., Any] | None = None

    async def __call__(self, *args: Any, **kwargs: Any) -> Any:
        record: dict[str, Any] = {"args": args, "kwargs": kwargs, "result": None}
        self.calls.append(record)
        if self._original is None:  # pragma: no cover - RED path
            raise NotImplementedError(
                "run_phases was called but the orchestrator is a Phase-3 stub"
            )
        record["result"] = await self._original(*args, **kwargs)
        return record["result"]

    @property
    def called(self) -> bool:
        return bool(self.calls)

    @property
    def result(self) -> Any:
        if not self.calls:
            raise AssertionError(
                "process_job never called app.enrich.orchestrator.run_phases; the "
                "production entrypoint must drive the orchestrator with DEFAULT_PHASES "
                "(02 §5 Fase 3 gate 1)"
            )
        return self.calls[0]["result"]

    def phases(self) -> tuple[Any, ...]:
        for call in self.calls:
            for value in (*call["args"], *call["kwargs"].values()):
                sequence = _phase_sequence(value)
                if sequence is not None:
                    return sequence
        raise AssertionError(
            "process_job called run_phases without passing DEFAULT_PHASES explicitly; "
            "a default argument hides the production phase order (Fase 3 gate 1)"
        )


def _phase_sequence(value: Any) -> tuple[Any, ...] | None:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        return None
    if not value:
        return None
    for item in value:
        if isinstance(item, Mapping):
            if "name" not in item:
                return None
        elif not hasattr(item, "name"):
            return None
    return tuple(value)


def install_phase_probe(monkeypatch: pytest.MonkeyPatch) -> PhaseProbe:
    """Patch every name ``process_job`` could resolve ``run_phases`` through."""

    runner = _load_module("app.worker.runner")
    orchestrator = _load_module("app.enrich.orchestrator")
    probe = PhaseProbe()
    probe._original = getattr(orchestrator, "run_phases", None)
    monkeypatch.setattr(runner, "run_phases", probe, raising=False)
    monkeypatch.setattr(orchestrator, "run_phases", probe, raising=False)
    return probe


def rows_from(result: Any) -> list[dict[str, Any]]:
    """Extract the enriched rows from a ``run_phases`` result."""

    if isinstance(result, Mapping):
        rows = result.get("rows")
    else:
        rows = getattr(result, "rows", None)
    if rows is None:
        raise AssertionError(
            "run_phases must return the enriched rows (mapping key 'rows' or "
            "attribute .rows) so the worker exports exactly what the pipeline built"
        )
    return [dict(row) for row in rows]


def phase_reports(result: Any) -> list[dict[str, Any]]:
    """Extract the per-phase reports, in execution order."""

    if isinstance(result, Mapping):
        reports = result.get("phase_reports")
    else:
        reports = getattr(result, "phase_reports", None)
    if not reports:
        raise AssertionError(
            "run_phases must return 'phase_reports' (one report per phase, in order) "
            "so 02 §3.5's skipped_by_budget is observable per phase"
        )
    return [dict(report) for report in reports]


# ---------------------------------------------------------------------------
# Jobs through the production entrypoint
# ---------------------------------------------------------------------------


def job_params(symbols: Sequence[str], **overrides: Any) -> dict[str, Any]:
    """A valid ``MarketRequest`` document for a watchlist quote job."""

    from tests._phase2 import enrich_body

    params = enrich_body(symbols, **overrides)
    from app.contracts import MarketRequest

    MarketRequest.model_validate(params)  # fail here, not inside the worker
    return params


async def create_job(
    repository: SqliteJobRepository, symbols: Sequence[str], **overrides: Any
) -> dict[str, Any]:
    return await repository.create(
        params=job_params(symbols, **overrides),
        api_key=CLIENT_KEY,
        rows_target=len(symbols),
        retention_days=7,
    )


async def probe_job(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    symbols: Sequence[str] = ("AAPL", "BBCA.JK", "BTC-USD"),
    **overrides: Any,
) -> tuple[dict[str, Any], PhaseProbe]:
    """Run one job through ``process_job`` with the phase probe installed."""

    repository = SqliteJobRepository(tmp_path / "mee.db")
    try:
        job = await create_job(repository, symbols, **overrides)
        probe = install_phase_probe(monkeypatch)
        record = await process_job(
            job["id"], repository=repository, export_dir=tmp_path / "exports"
        )
    finally:
        await repository.close()
    return record, probe


# ---------------------------------------------------------------------------
# Streaming source with a hard lookahead counter
# ---------------------------------------------------------------------------


class CountingSource:
    """Iterable-only row source; counts pulls and refuses ``len``/indexing.

    The counter is what makes "peak <= chunk size" observable: an implementation
    that materialises the input shows ``pulled == STREAM_ROWS`` while the first
    batch is being handed out.
    """

    def __init__(self, rows: int = STREAM_ROWS, payload_bytes: int = ROW_PAYLOAD_BYTES) -> None:
        self.rows = rows
        self.payload_bytes = payload_bytes
        self.pulled = 0

    def __iter__(self) -> Iterator[dict[str, Any]]:
        for index in range(self.rows):
            self.pulled += 1
            yield {
                "instrument_id": f"XNAS:STREAM{index:05d}",
                "last_price": float(index + 1),
                "payload": "x" * self.payload_bytes,
            }


# ---------------------------------------------------------------------------
# Batch engine / sink doubles
# ---------------------------------------------------------------------------


def engine_row(symbol: str, price: float = 100.0) -> dict[str, Any]:
    return {
        "instrument_id": f"XNAS:{symbol}",
        "symbol": symbol,
        "mic": "XNAS",
        "currency": "USD",
        "last_price": price,
        "quote_as_of": "2026-10-21T20:00:00+00:00",
        "as_of_achieved": "2026-10-21T20:00:00+00:00",
    }


class CountingEngine:
    """Async batch engine that records every call and yields one row per symbol."""

    def __init__(self, duplicate: bool = False) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.duplicate = duplicate

    async def __call__(self, symbols: Sequence[str]) -> list[dict[str, Any]]:
        self.calls.append(tuple(symbols))
        rows = [engine_row(symbol) for symbol in symbols]
        if self.duplicate and rows:
            rows.append(dict(rows[0]))
        return rows


class BatchFileSink:
    """Per-batch artifact sink: one truncated JSONL file per batch index.

    Truncating on open is the artifact contract that makes replaying a batch
    idempotent: the second write replaces the file instead of appending to it.
    ``fail_on`` injects one sink failure (once) after recording the call.
    """

    def __init__(self, directory: Path, *, fail_on: int | None = None) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.fail_on = fail_on
        self.calls: list[int] = []
        self.rows: dict[int, list[dict[str, Any]]] = {}

    async def __call__(self, batch_index: int, rows: Iterable[Mapping[str, Any]]) -> int:
        self.calls.append(batch_index)
        if self.fail_on == batch_index:
            self.fail_on = None
            raise RuntimeError(f"injected sink failure at batch {batch_index}")
        batch = [dict(row) for row in rows]
        path = self.directory / f"batch-{batch_index:04d}.jsonl"
        with open(path, "w", encoding="utf-8") as handle:
            for row in batch:
                handle.write(json.dumps(row, sort_keys=True, default=str) + "\n")
            handle.flush()
        self.rows[batch_index] = batch
        return len(batch)


def stored_rows(directory: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(Path(directory).glob("batch-*.jsonl")):
        with open(path, encoding="utf-8") as handle:
            rows.extend(json.loads(line) for line in handle if line.strip())
    return rows


def batch(batch_index: int, symbols: Sequence[str]) -> dict[str, Any]:
    return {
        "batch_index": batch_index,
        "symbols": list(symbols),
        "symbols_expected": len(symbols),
        "universe_slice": {"symbols": list(symbols)},
    }


__all__ = [
    "CHUNK_SIZE",
    "CLIENT_KEY",
    "CountingEngine",
    "CountingSource",
    "FATAL_PHASES",
    "PHASE_CAPABILITIES",
    "PHASE_NAMES",
    "PROVENANCE_FIELDS",
    "PROVENANCE_STATUSES",
    "BatchFileSink",
    "PhaseProbe",
    "ROW_PAYLOAD_BYTES",
    "STREAM_ROWS",
    "batch",
    "batch_api",
    "blank",
    "cap_value",
    "conflate_module",
    "create_job",
    "default_phases",
    "empty_row",
    "engine_row",
    "expected_filled_cells",
    "expected_phase_columns",
    "install_phase_probe",
    "is_iso_timestamp",
    "job_params",
    "observation",
    "phase_class",
    "phase_reports",
    "probe_job",
    "provenance_module",
    "row_stream_class",
    "rows_from",
    "run_batches_fn",
    "stored_rows",
]
