"""Fase 3 gate 4 — provenance is exact: one entry per filled cell, no more, no less.

The assertion is **set equality**, never a subset. A row claiming 40 filled cells
with 39 provenance records has one cell of unknown origin; a row with 41 records
has provenance for a value it does not carry. Either one breaks the audit the
export sidecar is supposed to make possible.

The job-level test runs through ``process_job`` (the production entrypoint), so
"provenance is attached by the orchestrator" is asserted, not assumed.
"""

from __future__ import annotations

import csv
import json

from app.contracts import CANONICAL_COLUMNS_V1, DERIVED_QUALITY_COLUMNS
from tests._phase3 import (
    PROVENANCE_FIELDS,
    PROVENANCE_STATUSES,
    blank,
    empty_row,
    expected_filled_cells,
    is_iso_timestamp,
    probe_job,
    provenance_module,
    rows_from,
)

PROVENANCE_JOB_SYMBOLS = ("AAPL", "BBCA.JK", "BTC-USD")


def test_provenance_key_is_not_an_export_column() -> None:
    provenance = provenance_module()

    assert provenance.PROVENANCE_KEY not in CANONICAL_COLUMNS_V1, (
        "provenance must travel beside the 92 cells, never as column 93"
    )
    assert provenance.provenance(empty_row()) == {}
    assert provenance.filled_cells(empty_row()) == set()


def test_filled_cells_ignores_the_derived_quality_columns() -> None:
    provenance = provenance_module()
    row = empty_row(
        last_price=231.5,
        data_quality="quote_only",
        filled_columns_count=1,
        verified_columns_count=0,
    )

    assert provenance.filled_cells(row) == {"last_price"}
    assert provenance.filled_cells(row) == expected_filled_cells(row)
    for column in DERIVED_QUALITY_COLUMNS:
        assert column not in provenance.filled_cells(row)


async def test_every_row_has_provenance_for_every_filled_cell(
    tmp_path, monkeypatch
) -> None:
    _record, probe = await probe_job(tmp_path, monkeypatch, symbols=PROVENANCE_JOB_SYMBOLS)
    rows = rows_from(probe.result)
    assert len(rows) == len(PROVENANCE_JOB_SYMBOLS), rows

    provenance = provenance_module()
    for row in rows:
        filled = expected_filled_cells(row)
        record = provenance.provenance(row)

        assert {"last_price", "quote_as_of", "as_of_achieved"} <= filled, (
            f"{row.get('instrument_id')}: fixture row lost its quote cells"
        )
        assert len(filled) >= 10, filled
        assert set(record) == filled, (
            f"{row.get('instrument_id')}: provenance and cells disagree — "
            f"missing {sorted(filled - set(record))}, "
            f"orphan {sorted(set(record) - filled)}"
        )
        assert provenance.filled_cells(row) == filled


async def test_provenance_entries_name_source_time_and_status(tmp_path, monkeypatch) -> None:
    _record, probe = await probe_job(tmp_path, monkeypatch, symbols=PROVENANCE_JOB_SYMBOLS)

    provenance = provenance_module()
    for row in rows_from(probe.result):
        for column, entry in provenance.provenance(row).items():
            assert set(entry) >= PROVENANCE_FIELDS, f"{column}: {entry}"
            assert isinstance(entry["source"], str) and entry["source"].strip(), (
                f"{column}: a value without a named source is unattributable"
            )
            assert is_iso_timestamp(entry["as_of"]), (
                f"{column}: as_of must be a timezone-aware ISO timestamp, got {entry['as_of']!r}"
            )
            assert entry["status"] in PROVENANCE_STATUSES, f"{column}: {entry['status']!r}"
            assert isinstance(entry["trust"], int) and 1 <= entry["trust"] <= 4, (
                f"{column}: trust is the 1..4 scale of 02 §4.6, got {entry['trust']!r}"
            )
            assert isinstance(entry["verified"], bool), f"{column}: {entry['verified']!r}"


async def test_empty_cells_have_no_provenance_entry(tmp_path, monkeypatch) -> None:
    _record, probe = await probe_job(tmp_path, monkeypatch, symbols=("AAPL",))
    (row,) = rows_from(probe.result)

    record = provenance_module().provenance(row)
    empty = [
        column
        for column in CANONICAL_COLUMNS_V1
        if column not in DERIVED_QUALITY_COLUMNS and blank(row.get(column))
    ]
    assert empty, "the fixture must leave some optional cells empty or this test is vacuous"
    assert set(record).isdisjoint(empty), sorted(set(record) & set(empty))
    assert row["data_quality"] == "quote_only"
    assert row["filled_columns_count"] == len(expected_filled_cells(row))


async def test_export_keeps_provenance_out_of_the_table(tmp_path, monkeypatch) -> None:
    record, probe = await probe_job(
        tmp_path, monkeypatch, symbols=("AAPL",), format=["csv", "json"]
    )
    rows_from(probe.result)  # the probe ran the orchestrator, not the fake shortcut

    job_id = record["id"]
    csv_path = tmp_path / "exports" / f"{job_id}.csv"
    json_path = tmp_path / "exports" / f"{job_id}.json"

    with open(csv_path, newline="", encoding="utf-8") as handle:
        header = next(csv.reader(handle))
    assert header == list(CANONICAL_COLUMNS_V1), "the export header is the frozen contract"
    assert provenance_module().PROVENANCE_KEY not in header

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert len(payload) == 1
    assert list(payload[0]) == list(CANONICAL_COLUMNS_V1)
    assert provenance_module().PROVENANCE_KEY not in payload[0]
