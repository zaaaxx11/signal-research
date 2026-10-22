"""Fase 3 gate 1 — the phase order is a property of the production entrypoint.

The lesson this file encodes: a phase table only tests can run is decoration
(``FallbackLadder`` had zero callers). So the order is asserted twice:

1. as data — ``DEFAULT_PHASES`` is the eight phases of 02 §3.5, its columns
   partition the frozen 92-column contract, and only P0/P7 are fatal;
2. from ``process_job`` — the probe in ``tests/_phase3.py`` wraps
   ``app.enrich.orchestrator.run_phases`` and records what the worker passed.
"""

from __future__ import annotations

import pytest

from app.contracts import CANONICAL_COLUMNS_V1, Capability
from tests._phase3 import (
    FATAL_PHASES,
    PHASE_CAPABILITIES,
    PHASE_NAMES,
    cap_value,
    default_phases,
    expected_phase_columns,
    phase_class,
    phase_reports,
    probe_job,
    rows_from,
)

REPORT_STATUSES = frozenset({"ok", "partial", "skipped", "failed"})


def test_default_phases_are_the_eight_documented_phases() -> None:
    phases = default_phases()

    assert [phase.name for phase in phases] == list(PHASE_NAMES), (
        "DEFAULT_PHASES must be P0..P7 of 02 §3.5, in order"
    )
    assert [cap_value(phase.capability) for phase in phases] == [
        cap_value(capability) for capability in PHASE_CAPABILITIES
    ]


def test_phase_columns_partition_the_frozen_92_columns() -> None:
    expected = expected_phase_columns()
    phases = default_phases()

    covered: list[str] = []
    for phase in phases:
        declared = tuple(phase.columns)
        assert declared == expected[phase.name], (
            f"phase {phase.name!r} declares {len(declared)} columns; "
            f"02 §3.5 assigns {len(expected[phase.name])}"
        )
        assert set(declared) <= set(CANONICAL_COLUMNS_V1), (
            f"phase {phase.name!r} declares a non-canonical column"
        )
        covered.extend(declared)

    assert len(covered) == len(set(covered)) == 92, (
        "phase columns must be a partition: no cell may be owned by two phases "
        f"(got {len(covered)} entries, {len(set(covered))} unique)"
    )
    assert set(covered) == set(CANONICAL_COLUMNS_V1)


def test_only_reference_and_export_are_fatal() -> None:
    fatal = {phase.name for phase in default_phases() if phase.fatal}

    assert fatal == FATAL_PHASES, (
        "02 §3.5: a phase may only fail the job when it is P0 (row has no identity) "
        "or P7 (no artifact); everything else degrades to empty cells"
    )


def test_phase_rejects_a_non_canonical_column() -> None:
    phase = phase_class()

    with pytest.raises(ValueError):
        phase(name="quote", capability=Capability.QUOTE, columns=("not_a_column",))


async def test_process_job_builds_the_orchestrator_with_default_phases(
    tmp_path, monkeypatch
) -> None:
    record, probe = await probe_job(tmp_path, monkeypatch)

    assert probe.called, (
        "process_job did not call app.enrich.orchestrator.run_phases; the production "
        "entrypoint must build the orchestrator, not only the unit tests (gate 1)"
    )
    declared = probe.phases()
    assert [phase.name for phase in declared] == list(PHASE_NAMES)
    assert [cap_value(phase.capability) for phase in declared] == [
        cap_value(capability) for capability in PHASE_CAPABILITIES
    ]
    assert record["status"] == "Success", record.get("error")


async def test_process_job_reports_every_phase_in_order(tmp_path, monkeypatch) -> None:
    _record, probe = await probe_job(tmp_path, monkeypatch)

    reports = phase_reports(probe.result)
    assert [report["name"] for report in reports] == list(PHASE_NAMES), (
        "a phase that is not reported did not run, or ran invisibly; both make "
        "skipped_by_budget unauditable"
    )
    assert [cap_value(report["capability"]) for report in reports] == [
        cap_value(capability) for capability in PHASE_CAPABILITIES
    ]
    for report in reports:
        assert report["status"] in REPORT_STATUSES, report
        assert isinstance(report["skipped_by_budget"], int), report
        assert report["skipped_by_budget"] >= 0, report


async def test_orchestrator_returns_the_rows_it_exports(tmp_path, monkeypatch) -> None:
    record, probe = await probe_job(tmp_path, monkeypatch, symbols=("AAPL", "BBCA.JK"))

    rows = rows_from(probe.result)
    assert len(rows) == 2, rows
    assert {row["symbol"] for row in rows} == {"AAPL", "BBCA.JK"}
    assert record["result_count"] == 2
