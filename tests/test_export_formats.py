"""Fase 5 gate 1 and 4 — four formats, every cell, zero formulas.

The table is written once and read back four times, with an oracle written
independently of the writer (``tests/_phase5.py``). A writer that "works" by
writing the same string into every format fails: CSV cells are strings, JSONL
keeps the JSON types, XLSX keeps numbers as numbers and blanks as blanks, and
Parquet has a declared type per column.

Excel is a program, not a viewer: a cell whose text starts with ``=`` is a
formula. The fixture carries ``=cmd|' /C calc'!A0`` and friends, so "zero
formula cells in the workbook" is measured, not promised.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests._phase5 import (
    COLUMNS,
    DATA_FORMATS,
    JOB_ID,
    bulk_rows,
    csv_cell,
    csv_row,
    data_file_names,
    export_job,
    json_row,
    parquet_row,
    parquet_type,
    read_csv_table,
    read_jsonl,
    read_parquet,
    read_xlsx_sheet,
    report_field,
    source_rows,
    write_export,
    writer_module,
    xlsx_cell,
    xlsx_row,
    xlsx_sheet_names,
)

NAME = "name"
SECTOR = "sector"


# ---------------------------------------------------------------------------
# One run, four data files
# ---------------------------------------------------------------------------


def test_one_run_writes_all_four_formats(tmp_path: Path) -> None:
    rows = source_rows()

    report = export_job(rows, directory=tmp_path)

    files = report_field(report, "files")
    assert set(files) == set(DATA_FORMATS), (
        f"one run must produce {sorted(DATA_FORMATS)}; got {sorted(files)}"
    )
    for fmt, path in files.items():
        path = Path(path)
        assert path.exists(), f"{fmt} was reported but {path} does not exist"
        assert path.parent == tmp_path, f"{fmt} landed outside the export directory: {path}"
        assert path.stat().st_size > 0, f"{fmt} is empty"
    assert report_field(report, "row_count") == len(rows)
    assert Path(report_field(report, "manifest_path")).name == f"{JOB_ID}.manifest.json"


def test_the_default_formats_are_the_four_data_formats() -> None:
    from tests._phase5 import pipeline_module

    assert tuple(pipeline_module().DEFAULT_FORMATS) == DATA_FORMATS


# ---------------------------------------------------------------------------
# CSV: the header is the contract and every cell survives a re-import
# ---------------------------------------------------------------------------


def test_csv_reimports_to_the_source_table(tmp_path: Path) -> None:
    rows = source_rows()
    report = export_job(rows, directory=tmp_path)

    table = read_csv_table(report_field(report, "files")["csv"])

    assert table[0] == COLUMNS, "the CSV header must be CANONICAL_COLUMNS_V1, in order"
    assert len(table) == len(rows) + 1
    assert all(len(line) == len(COLUMNS) for line in table), "a CSV row has the wrong width"
    for index, (source, written) in enumerate(zip(rows, table[1:])):
        assert written == csv_row(source), f"CSV row {index} does not match the source row"


def test_csv_neutralises_formula_injection_and_round_trips_as_text(tmp_path: Path) -> None:
    rows = source_rows()
    report = export_job(rows, directory=tmp_path)

    table = read_csv_table(report_field(report, "files")["csv"])
    row_index = next(
        index for index, row in enumerate(rows) if str(row.get(NAME, "")).startswith("=")
    )
    name_column = COLUMNS.index(NAME)

    written = table[row_index + 1][name_column]
    original = str(rows[row_index][NAME])
    assert written == "'" + original, (
        f"a leading '=' must be neutralised with an apostrophe; got {written!r}"
    )
    assert written[1:] == original, "the original text must survive after the prefix"


def test_a_real_zero_is_not_an_empty_cell(tmp_path: Path) -> None:
    rows = source_rows()
    report = export_job(rows, directory=tmp_path)

    zero_row = next(index for index, row in enumerate(rows) if row.get("last_price") == 0.0)
    table = read_csv_table(report_field(report, "files")["csv"])
    cell = table[zero_row + 1][COLUMNS.index("last_price")]
    assert cell == "0.0", f"0.0 is a value, not a missing cell; got {cell!r}"

    jsonl = read_jsonl(report_field(report, "files")["jsonl"])
    assert jsonl[zero_row]["last_price"] == 0.0, "JSONL turned a real zero into null"
    parquet = read_parquet(report_field(report, "files")["parquet"])
    assert parquet.to_pylist()[zero_row]["last_price"] == 0.0


def test_unicode_commas_quotes_and_newlines_survive_csv(tmp_path: Path) -> None:
    rows = source_rows()
    report = export_job(rows, directory=tmp_path)

    table = read_csv_table(report_field(report, "files")["csv"])
    index = next(
        index for index, row in enumerate(rows) if "银行" in str(row.get(NAME, ""))
    )
    assert table[index + 1][COLUMNS.index(NAME)] == csv_cell(rows[index][NAME])
    assert "银行" in table[index + 1][COLUMNS.index(NAME)]
    assert table[index + 1][COLUMNS.index(SECTOR)] == csv_cell(rows[index][SECTOR])
    assert "\n" in table[index + 1][COLUMNS.index(SECTOR)], (
        "an embedded newline must round-trip through RFC4180 quoting, not be stripped"
    )


# ---------------------------------------------------------------------------
# JSONL: one line per row, canonical key order, JSON types preserved
# ---------------------------------------------------------------------------


def test_jsonl_has_exactly_one_line_per_row(tmp_path: Path) -> None:
    rows = source_rows()
    report = export_job(rows, directory=tmp_path)

    path = report_field(report, "files")["jsonl"]
    text = Path(path).read_text(encoding="utf-8")
    lines = text.splitlines()
    assert len(lines) == len(rows), f"JSONL has {len(lines)} lines for {len(rows)} rows"
    assert text.endswith("\n"), "the last JSONL record must be newline-terminated"

    records = read_jsonl(path)
    for index, (source, record) in enumerate(zip(rows, records)):
        assert list(record) == COLUMNS, (
            f"line {index} keys are not the canonical order: {list(record)[:3]}..."
        )
        assert record == json_row(source), f"JSONL line {index} does not match the source row"


def test_jsonl_keeps_lists_as_json_instead_of_python_repr(tmp_path: Path) -> None:
    rows = source_rows()
    report = export_job(rows, directory=tmp_path)

    records = read_jsonl(report_field(report, "files")["jsonl"])
    memberships = records[0]["index_memberships"]
    assert memberships == ["SPX", "NDX"], (
        f"a list column must stay a JSON array; got {memberships!r}"
    )


# ---------------------------------------------------------------------------
# XLSX: no formula anywhere, numbers as numbers, three sheets
# ---------------------------------------------------------------------------


def test_xlsx_has_no_formula_cells_at_all(tmp_path: Path) -> None:
    from openpyxl import load_workbook

    rows = source_rows()
    report = export_job(rows, directory=tmp_path)
    path = report_field(report, "files")["xlsx"]

    workbook = load_workbook(filename=str(path), data_only=False)
    try:
        formulas = [
            (sheet.title, cell.coordinate, cell.value)
            for sheet in workbook.worksheets
            for row in sheet.iter_rows()
            for cell in row
            if cell.data_type == "f"
        ]
    finally:
        workbook.close()

    assert not formulas, f"an XLSX cell is a formula, not text: {formulas[:3]}"


def test_xlsx_data_sheet_cells_match_the_source_row(tmp_path: Path) -> None:
    rows = source_rows()
    report = export_job(rows, directory=tmp_path)

    table = read_xlsx_sheet(report_field(report, "files")["xlsx"], "data")
    assert list(table[0]) == COLUMNS
    assert len(table) == len(rows) + 1

    for index, (source, written) in enumerate(zip(rows, table[1:])):
        expected = xlsx_row(source)
        assert len(written) == len(COLUMNS)
        for column, want, got in zip(COLUMNS, expected, written):
            assert got == want, (
                f"XLSX row {index} column {column}: expected {want!r}, got {got!r}"
            )
            if isinstance(source.get(column), bool):
                assert isinstance(got, bool), (
                    f"{column} was written as {type(got).__name__}, not a boolean"
                )
            if source.get(column) is None:
                assert got is None, f"{column} must stay blank, got {got!r}"


def test_xlsx_formula_injection_is_text_not_a_formula(tmp_path: Path) -> None:
    from openpyxl import load_workbook

    rows = source_rows()
    report = export_job(rows, directory=tmp_path)
    path = report_field(report, "files")["xlsx"]

    row_index = next(
        index for index, row in enumerate(rows) if str(row.get(NAME, "")).startswith("=")
    )
    original = str(rows[row_index][NAME])

    workbook = load_workbook(filename=str(path), data_only=False)
    try:
        cell = workbook["data"].cell(row=row_index + 2, column=COLUMNS.index(NAME) + 1)
        assert cell.data_type == "s", f"formula cell type {cell.data_type!r} on {cell.coordinate}"
        assert cell.value == "'" + original
    finally:
        workbook.close()


def test_xlsx_has_data_quality_and_providers_sheets(tmp_path: Path) -> None:
    rows = source_rows()
    report = export_job(rows, directory=tmp_path)

    table = read_xlsx_sheet(report_field(report, "files")["xlsx"], "quality")
    assert list(table[0]) == ["column", "filled", "empty", "verified", "rejected", "disputed"]
    assert len(table) == len(COLUMNS) + 1, "the quality sheet needs one row per canonical column"
    by_column = {row[0]: row for row in table[1:]}
    for column in COLUMNS:
        filled, empty = int(by_column[column][1]), int(by_column[column][2])
        assert filled + empty == len(rows), (
            f"quality sheet {column}: filled {filled} + empty {empty} != {len(rows)}"
        )
    filled_last_price = sum(1 for row in rows if row.get("last_price") is not None)
    assert int(by_column["last_price"][1]) == filled_last_price

    providers = read_xlsx_sheet(report_field(report, "files")["xlsx"], "providers")
    assert list(providers[0]) == ["provider", "capability", "requests", "verdicts"]
    assert len(providers) == 2, "one provider row plus the header"
    assert providers[1][0] == "fake"
    assert providers[1][1] == "quote"
    assert int(providers[1][2]) == 6
    assert json.loads(providers[1][3]) == {"ok": 6}


# ---------------------------------------------------------------------------
# Parquet: explicit, stable schema
# ---------------------------------------------------------------------------


def test_parquet_schema_is_explicit_and_complete(tmp_path: Path) -> None:
    rows = source_rows()
    report = export_job(rows, directory=tmp_path)

    table = read_parquet(report_field(report, "files")["parquet"])
    assert table.column_names == COLUMNS, "parquet columns must be the canonical 92, in order"
    assert table.num_rows == len(rows)

    metadata = table.schema.metadata or {}
    assert metadata.get(b"mee.columns_version") == b"v1", (
        "the parquet schema must carry mee.columns_version='v1' for audit"
    )

    for column in COLUMNS:
        expected = parquet_type(row.get(column) for row in rows)
        actual = str(table.schema.field(column).type)
        assert actual == expected, (
            f"parquet column {column}: expected {expected}, got {actual}"
        )


def test_parquet_returns_every_cell(tmp_path: Path) -> None:
    rows = source_rows()
    report = export_job(rows, directory=tmp_path)

    table = read_parquet(report_field(report, "files")["parquet"])
    written = table.to_pylist()
    assert len(written) == len(rows)
    for index, (source, record) in enumerate(zip(rows, written)):
        assert record == parquet_row(source), f"parquet row {index} does not match the source"


def test_parquet_mixed_type_column_becomes_string(tmp_path: Path) -> None:
    rows = source_rows()
    mixed = [dict(rows[0]), dict(rows[1])]
    mixed[0]["bars_count"] = 2
    mixed[1]["bars_count"] = "unknown"
    for row in mixed:
        row["float_shares"] = None

    target = tmp_path / "mixed.parquet"
    write_export(mixed, "parquet", target)

    table = read_parquet(target)
    assert str(table.schema.field("bars_count").type) == "string"
    assert table.column("bars_count").to_pylist() == ["2", "unknown"]
    assert str(table.schema.field("float_shares").type) == "string", (
        "an all-null column is a string column, never a null type"
    )


# ---------------------------------------------------------------------------
# XLSX row limit: 50.000 rows per sheet, split instead of truncating
# ---------------------------------------------------------------------------


def test_the_xlsx_sheet_limit_is_fifty_thousand_rows() -> None:
    assert writer_module().XLSX_SHEET_ROW_LIMIT == 50_000


def test_xlsx_sheet_names_split_a_large_table() -> None:
    assert xlsx_sheet_names(0) == ("data",)
    assert xlsx_sheet_names(1) == ("data",)
    assert xlsx_sheet_names(50_000) == ("data",)
    assert xlsx_sheet_names(50_001) == ("data", "data_2")
    assert xlsx_sheet_names(150_001) == ("data", "data_2", "data_3", "data_4")


def test_xlsx_workbook_really_splits_at_the_configured_limit(tmp_path: Path) -> None:
    from openpyxl import load_workbook

    rows = bulk_rows(250)
    report = export_job(rows, directory=tmp_path, xlsx_sheet_rows=100)
    path = report_field(report, "files")["xlsx"]

    workbook = load_workbook(filename=str(path), data_only=False)
    try:
        data_sheets = [name for name in workbook.sheetnames if name == "data" or name.startswith("data_")]
        assert data_sheets == ["data", "data_2", "data_3"], workbook.sheetnames
        per_sheet: list[int] = []
        ids: list[str] = []
        for name in data_sheets:
            sheet_rows = list(workbook[name].iter_rows(values_only=True))
            assert list(sheet_rows[0]) == COLUMNS, f"{name} must repeat the header"
            per_sheet.append(len(sheet_rows) - 1)
            ids.extend(str(row[0]) for row in sheet_rows[1:])
    finally:
        workbook.close()

    assert per_sheet == [100, 100, 50], per_sheet
    assert ids == [row["instrument_id"] for row in rows], "splitting must not reorder rows"
    assert len(set(ids)) == len(rows)


@pytest.mark.parametrize("rows_per_sheet", [0, -1])
def test_xlsx_sheet_names_reject_a_non_positive_limit(rows_per_sheet: int) -> None:
    with pytest.raises(ValueError):
        xlsx_sheet_names(10, rows_per_sheet=rows_per_sheet)
