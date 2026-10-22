"""Fase 4 wiring — P6 runs the verifier, and the job view tells the truth.

What these tests refuse to accept:

* a ``verify`` phase that reports ``partial`` because "V1..V16 land in Fase 4";
* a rejection that exists only in a log line — 02 §4.7 exists so that "empty"
  and "the provider had no data" stay distinguishable;
* a quality block whose counters do not reconcile;
* a ``REJECT`` that still writes an export file (04 §5.8) or a ``REJECT`` that
  loses its audit trail on the way to ``Failure``.

The job rows come from ``_fetch_fake`` replaced by a stub, so ``process_job``
still walks its one and only pipeline: fetch -> conflate -> verify -> export.
"""

from __future__ import annotations

import csv
import json

import pytest

from app.jobs.repository import SqliteJobRepository
from app.worker.runner import process_job
from tests._phase4 import (
    assert_rejection_shape,
    column_of,
    create_job,
    default_phases,
    install_fetch_stub,
    phase_reports,
    phase_usage,
    quote_row,
    reason_of,
    reconcile,
    record_rejections,
    rejection_rows,
    report_named,
    result_rejections,
    result_verdict,
    rows_from,
    run_phases_fn,
    usage_verification,
)


@pytest.fixture
async def repository(tmp_path):
    """One SQLite store per test, closed even when an assertion fails."""

    store = SqliteJobRepository(tmp_path / "mee.db")
    try:
        yield store
    finally:
        await store.close()


async def _run_job(
    repository: SqliteJobRepository,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    rows,
    symbols,
) -> tuple[dict, dict]:
    job = await create_job(repository, symbols)
    install_fetch_stub(monkeypatch, rows)
    record = await process_job(
        job["id"], repository=repository, export_dir=tmp_path / "exports"
    )
    return job, record


def _csv_rows(path) -> list[dict[str, str]]:
    with open(path, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


# ---------------------------------------------------------------------------
# P6 in the orchestrator
# ---------------------------------------------------------------------------


async def test_p6_runs_the_rules_and_reports_ok_not_partial() -> None:
    rows = [quote_row("AAA"), quote_row("BAD", change_pct=0.0)]
    result = await run_phases_fn()(
        default_phases(), rows=rows, job_id="job-verify", request=None
    )

    report = report_named(phase_reports(result), "verify")
    assert report["status"] == "ok", (
        "P6 now has a verifier; 'partial because Fase 4 has not landed' is no longer true"
    )

    applied = {row["instrument_id"]: row for row in rows_from(result)}
    assert applied["XNAS:AAA"]["change_pct"] == pytest.approx(11.1111)
    assert applied["XNAS:BAD"]["change_pct"] is None, (
        "the verified row must be repaired before the export sees it"
    )
    assert report["filled"] > 0
    assert "Fase 4" not in str(report.get("reason") or "")
    assert "verified_columns_count stays 0" not in str(report.get("reason") or "")

    clean = applied["XNAS:AAA"]
    assert clean["verified_columns_count"] > 0, "a verified row cannot claim zero verified cells"
    assert clean["verified_columns_count"] == clean["filled_columns_count"]
    broken = applied["XNAS:BAD"]
    assert broken["verified_columns_count"] == broken["filled_columns_count"]
    assert broken["filled_columns_count"] == clean["filled_columns_count"] - 1, (
        "the rejected change_pct cell must be gone from the filled count, not merely hidden"
    )


async def test_p6_returns_the_verdict_and_the_rejections() -> None:
    rows = [quote_row("AAA"), quote_row("BAD", change_pct=0.0)]
    result = await run_phases_fn()(
        default_phases(), rows=rows, job_id="job-verify", request=None
    )

    assert result_verdict(result) == "DEGRADED", (
        "one major rejection degrades the job; it does not pass and it does not reject"
    )
    rejections = result_rejections(result)
    assert len(rejections) == 1, rejections
    record = rejections[0]
    assert_rejection_shape(record, rule="V1", column="change_pct", reason="change_pct_mismatch")
    assert record.get("instrument_id") == "XNAS:BAD"


async def test_a_clean_pipeline_is_a_pass() -> None:
    rows = [quote_row("AAA"), quote_row("BBB")]
    result = await run_phases_fn()(
        default_phases(), rows=rows, job_id="job-verify", request=None
    )
    assert result_verdict(result) == "PASS"
    assert result_rejections(result) == []


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


async def test_repository_persists_rejections_once_per_write(repository, tmp_path) -> None:
    job = await create_job(repository, ["TEST"])
    rejection = {
        "instrument_id": "XNAS:TEST",
        "column": "change_pct",
        "rule": "V1",
        "reason": "change_pct_mismatch",
        "value_text": "0.0",
    }

    await record_rejections(repository, job["id"], [rejection])
    await record_rejections(repository, job["id"], [rejection])

    rows = rejection_rows(tmp_path / "mee.db")
    assert len(rows) == 1, "a retry replaces the rejection set; it does not stack duplicates"
    row = rows[0]
    assert row["job_id"] == job["id"]
    assert row["instrument_id"] == "XNAS:TEST"
    assert column_of(row) == "change_pct"
    assert row["rule"] == "V1"
    assert reason_of(row) == "change_pct_mismatch"
    assert row["created_at"]


# ---------------------------------------------------------------------------
# The production entrypoint
# ---------------------------------------------------------------------------


async def test_process_job_persists_rejections_and_real_quality(repository, tmp_path, monkeypatch) -> None:
    rows = [quote_row("OKAY"), quote_row("BAD", change_pct=0.0)]
    job, record = await _run_job(repository, tmp_path, monkeypatch, rows, ["OKAY", "BAD"])
    assert record["status"] == "Success", record.get("error")
    usage = json.loads(record["usage"])
    artifacts = await repository.artifacts(job["id"])

    verification = usage_verification(usage)
    assert verification["verdict"] == "DEGRADED"
    assert verification["rejections"] == 1

    blocks = usage["quality"]["by_column"]
    assert len(blocks) == 92
    reconcile(blocks, row_count=2)
    change = blocks["change_pct"]
    assert (change["filled"], change["verified"], change["rejected"], change["disputed"], change["empty"]) == (
        2,
        1,
        1,
        0,
        0,
    ), change
    assert blocks["last_price"]["verified"] == 2, "the untouched quote cells are verified"
    assert usage["coverage"]["verified_ratio"] > 0, (
        "a job that verified cells must not report a zero verified ratio"
    )

    report = phase_usage(usage, "verify")
    assert report["status"] == "ok", report
    assert report["rows"] == 2, report

    stored = rejection_rows(tmp_path / "mee.db")
    assert len(stored) == 1, stored
    row = stored[0]
    assert row["job_id"] == job["id"]
    assert row["instrument_id"] == "XNAS:BAD"
    assert column_of(row) == "change_pct"
    assert row["rule"] == "V1"
    assert reason_of(row) == "change_pct_mismatch"

    assert {artifact["kind"] for artifact in artifacts} == {"csv"}
    exported = _csv_rows(tmp_path / "exports" / f"{job['id']}.csv")
    by_instrument = {row["instrument_id"]: row for row in exported}
    assert "change_pct" in by_instrument["XNAS:BAD"]
    assert by_instrument["XNAS:BAD"]["change_pct"] == "", (
        "a rejected cell is empty in the table, never 0 and never 'N/A'"
    )
    assert by_instrument["XNAS:OKAY"]["change_pct"] == "11.1111"


async def test_a_clean_job_reports_pass_and_writes_no_rejection(repository, tmp_path, monkeypatch) -> None:
    job, record = await _run_job(repository, tmp_path, monkeypatch, [quote_row("CLEAN")], ["CLEAN"])
    assert record["status"] == "Success"
    usage = json.loads(record["usage"])

    verification = usage_verification(usage)
    assert verification["verdict"] == "PASS"
    assert verification["rejections"] == 0
    assert rejection_rows(tmp_path / "mee.db") == []
    assert phase_usage(usage, "verify")["status"] == "ok"
    assert usage["quality"]["by_column"]["last_price"]["verified"] == 1


async def test_a_critical_rejection_refuses_the_export_and_fails_the_job(repository, tmp_path, monkeypatch) -> None:
    future = "2099-01-01T00:00:00+00:00"
    rows = [
        quote_row(
            "FUTR",
            quote_as_of=future,
            as_of_achieved=future,
        )
    ]
    job, record = await _run_job(repository, tmp_path, monkeypatch, rows, ["FUTR"])

    assert record["status"] == "Failure", (
        "a quote dated 2099 is a REJECT; a Success with an export would be the lie 02 §3.9 forbids"
    )
    assert "REJECT" in str(record["error"]).upper(), record["error"]
    assert await repository.artifacts(job["id"]) == [], "no artifact may be recorded under REJECT"

    assert list((tmp_path / "exports").glob(f"{job['id']}.*")) == [], (
        "04 §5.8: exporting while the verdict is REJECT is forbidden"
    )
    stored = rejection_rows(tmp_path / "mee.db")
    assert len(stored) == 1, "the audit trail must survive the failure"
    assert stored[0]["job_id"] == job["id"]
    assert stored[0]["rule"] == "V16"
    assert reason_of(stored[0]) == "future_as_of"
