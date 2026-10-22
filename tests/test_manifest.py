"""Fase 5 gate 2 — the manifest is auditable or it is decoration.

Every assertion here recomputes something: hashes with ``hashlib``, coverage
from the files on disk, the manifest's own name from ``files``. A manifest that
merely repeats what the writer believed would pass none of it.

The manifest also never describes itself: it is written last, it is not a data
file, and a list that contains its own checksum cannot be verified by the person
who receives it.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from tests._phase5 import (
    COLUMNS,
    DATA_FORMATS,
    DEFAULT_METADATA,
    JOB_ID,
    check_field,
    content_sha256,
    coverage_error,
    data_file_names,
    export_job,
    read_custody,
    read_csv_table,
    read_manifest,
    report_field,
    sha256_file,
    source_rows,
    verify_manifest,
)


def _export(tmp_path: Path, **kwargs):
    rows = source_rows()
    report = export_job(rows, directory=tmp_path, **kwargs)
    return rows, report, read_manifest(report_field(report, "manifest_path"))


# ---------------------------------------------------------------------------
# What the manifest says about the files
# ---------------------------------------------------------------------------


def test_manifest_describes_every_data_file(tmp_path: Path) -> None:
    rows, report, manifest = _export(tmp_path)

    files = manifest["files"]
    assert isinstance(files, list)
    names = [entry["name"] for entry in files]
    expected = sorted(data_file_names().values())
    assert sorted(names) == expected, f"manifest files {sorted(names)} != {expected}"
    assert manifest["files_count"] == len(DATA_FORMATS)

    for entry in files:
        path = tmp_path / entry["name"]
        assert path.exists(), f"manifest names a file that is not there: {entry['name']}"
        assert entry["sha256"] == sha256_file(path), (
            f"{entry['name']}: manifest sha256 does not match the bytes on disk"
        )
        assert entry["bytes"] == path.stat().st_size
        assert entry["row_count"] == len(rows), (
            f"{entry['name']} claims {entry['row_count']} rows, table has {len(rows)}"
        )


def test_manifest_does_not_count_itself_or_the_custody_log(tmp_path: Path) -> None:
    _rows, report, manifest = _export(tmp_path)

    manifest_path = Path(report_field(report, "manifest_path"))
    custody_path = Path(report_field(report, "custody_path"))
    names = {entry["name"] for entry in manifest["files"]}

    assert manifest_path.name not in names, "the manifest listed itself"
    assert custody_path.name not in names, "the custody log is not a data file"
    assert manifest["files_count"] == len(manifest["files"]) == len(DATA_FORMATS)
    assert sha256_file(manifest_path) not in {entry["sha256"] for entry in manifest["files"]}


def test_manifest_row_coverage_is_one_hundred_percent(tmp_path: Path) -> None:
    rows, _report, manifest = _export(tmp_path)

    coverage = manifest["coverage"]
    assert manifest["row_count"] == len(rows)
    assert coverage["rows_expected"] == len(rows)
    assert coverage["rows_written"] == len(rows)
    assert coverage["ratio"] == 1.0
    assert set(coverage["by_format"]) == set(DATA_FORMATS)
    for fmt, count in coverage["by_format"].items():
        assert count == len(rows), f"coverage.by_format[{fmt}] = {count}, expected {len(rows)}"


def test_manifest_carries_the_frozen_metadata(tmp_path: Path) -> None:
    rows, report, manifest = _export(tmp_path)

    assert manifest["job_id"] == JOB_ID
    assert manifest["columns_version"] == "v1"
    assert manifest["request_hash"] == DEFAULT_METADATA["request_hash"]
    assert manifest["plan_hash"] == DEFAULT_METADATA["plan_hash"]
    assert manifest["as_of_requested"] == DEFAULT_METADATA["as_of_requested"]
    assert manifest["providers"] == DEFAULT_METADATA["providers"]
    assert manifest["usage"] == DEFAULT_METADATA["usage"]
    assert manifest["quality"]["filled_ratio"] == DEFAULT_METADATA["quality"]["filled_ratio"]

    created = datetime.fromisoformat(manifest["created_at"])
    assert created.tzinfo is not None, "created_at must be timezone-aware UTC"

    achieved = manifest["as_of_achieved"]
    wanted = sorted(
        row["as_of_achieved"] for row in rows if row.get("as_of_achieved") is not None
    )
    assert datetime.fromisoformat(achieved["min"]) == wanted[0]
    assert datetime.fromisoformat(achieved["max"]) == wanted[-1]

    options = manifest["options"]
    assert options["formats"] == list(DATA_FORMATS)
    assert options["rows_per_shard"] > 0

    assert manifest["content_sha256"] == content_sha256(rows)
    assert report_field(report, "content_sha256") == manifest["content_sha256"]
    assert manifest["columns"] == COLUMNS, (
        "the manifest must name the table it describes, not just a version string"
    )


# ---------------------------------------------------------------------------
# verify_manifest: it must be able to fail
# ---------------------------------------------------------------------------


def test_verify_manifest_accepts_a_clean_manifest(tmp_path: Path) -> None:
    _rows, report, _manifest = _export(tmp_path)

    check = verify_manifest(report_field(report, "manifest_path"))

    assert check_field(check, "ok") is True, check_field(check, "problems")
    assert not check_field(check, "problems")
    assert check_field(check, "files_checked") == len(DATA_FORMATS)


def test_verify_manifest_detects_a_tampered_file(tmp_path: Path) -> None:
    _rows, report, _manifest = _export(tmp_path)
    csv_path = tmp_path / data_file_names()["csv"]
    with open(csv_path, "ab") as handle:
        handle.write(b"tampered\n")

    check = verify_manifest(report_field(report, "manifest_path"))

    assert check_field(check, "ok") is False
    problems = " ".join(str(problem).lower() for problem in check_field(check, "problems"))
    assert "csv" in problems, f"tampering with the CSV was not reported: {problems}"


def test_verify_manifest_detects_a_missing_file(tmp_path: Path) -> None:
    _rows, report, _manifest = _export(tmp_path)
    (tmp_path / data_file_names()["parquet"]).unlink()

    check = verify_manifest(report_field(report, "manifest_path"))

    assert check_field(check, "ok") is False
    problems = " ".join(str(problem).lower() for problem in check_field(check, "problems"))
    assert "parquet" in problems, f"a missing parquet file was not reported: {problems}"


def test_verify_manifest_detects_a_coverage_lie(tmp_path: Path) -> None:
    rows, report, manifest = _export(tmp_path)
    manifest_path = Path(report_field(report, "manifest_path"))

    manifest["coverage"]["rows_written"] = len(rows) - 1
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    check = verify_manifest(manifest_path)

    assert check_field(check, "ok") is False
    problems = " ".join(str(problem).lower() for problem in check_field(check, "problems"))
    assert "coverage" in problems or "row" in problems, problems


def test_export_refuses_to_claim_coverage_it_did_not_write(tmp_path: Path) -> None:
    rows = source_rows()

    with pytest.raises(coverage_error()) as excinfo:
        export_job(rows[:5], directory=tmp_path, expected_rows=len(rows))

    message = str(excinfo.value)
    assert "5" in message and str(len(rows)) in message, message
    assert not (tmp_path / f"{JOB_ID}.manifest.json").exists(), (
        "a manifest must never be written for an incomplete table"
    )


# ---------------------------------------------------------------------------
# Chain of custody: append-only, ordered, and tied to the real bytes
# ---------------------------------------------------------------------------


def test_custody_is_append_only_and_records_every_step(tmp_path: Path) -> None:
    rows, report, _manifest = _export(tmp_path, rows_per_shard=2)
    custody_path = Path(report_field(report, "custody_path"))

    records = read_custody(custody_path)
    events = [record["event"] for record in records]

    assert events[0] == "export_started"
    assert events[-2:] == ["manifest_written", "export_completed"], events
    assert events.count("format_written") == len(DATA_FORMATS)
    assert events.count("shard_written") == 3, (
        f"6 rows at 2 rows per shard is 3 shards; events={events}"
    )
    assert [record["seq"] for record in records] == list(range(1, len(records) + 1))
    assert {record["job_id"] for record in records} == {JOB_ID}
    for record in records:
        assert datetime.fromisoformat(record["at"]).tzinfo is not None

    shard_indexes = [record["index"] for record in records if record["event"] == "shard_written"]
    assert shard_indexes == [0, 1, 2]
    assert sum(record["row_count"] for record in records if record["event"] == "shard_written") == len(rows)


def test_custody_format_records_match_the_manifest_hashes(tmp_path: Path) -> None:
    _rows, report, manifest = _export(tmp_path)
    records = read_custody(report_field(report, "custody_path"))

    by_name = {entry["name"]: entry for entry in manifest["files"]}
    written = [record for record in records if record["event"] == "format_written"]
    assert len(written) == len(DATA_FORMATS)
    for record in written:
        entry = by_name[record["name"]]
        assert record["sha256"] == entry["sha256"], record["name"]
        assert record["bytes"] == entry["bytes"], record["name"]
        assert record["row_count"] == entry["row_count"], record["name"]

    manifest_record = next(record for record in records if record["event"] == "manifest_written")
    assert manifest_record["sha256"] == sha256_file(Path(report_field(report, "manifest_path")))


def test_custody_is_not_rewritten_by_a_second_export(tmp_path: Path) -> None:
    rows = source_rows()
    first = export_job(rows, directory=tmp_path)
    custody_path = Path(report_field(first, "custody_path"))
    before = read_custody(custody_path)

    second = export_job(rows, directory=tmp_path)
    after = read_custody(custody_path)

    assert after[: len(before)] == before, "an append-only log cannot rewrite its own history"
    assert len(after) > len(before), "a second run must still record that it happened"
    assert after[-1]["event"] == "export_completed"
    assert report_field(second, "content_sha256") == report_field(first, "content_sha256")


def test_the_exported_csv_really_holds_every_row(tmp_path: Path) -> None:
    rows, report, manifest = _export(tmp_path)
    table = read_csv_table(report_field(report, "files")["csv"])
    assert len(table) - 1 == manifest["row_count"] == len(rows)
