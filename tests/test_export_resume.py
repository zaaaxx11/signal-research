"""Fase 5 gate 3 — idempotent re-export and resumable shards.

Two properties that only exist if the writer is told about them:

* **Idempotent.** The same rows and options produce the same bytes and the same
  ``content_sha256``; a second run reuses the staged shards instead of writing a
  second copy. A directory with ``export.csv`` and ``export (1).csv`` is a
  duplicate, not a backup.
* **Resumable.** A failure at shard 3 leaves shards 0–2 finalised; the next run
  writes only 3..n, and the merged table still has every row exactly once. The
  failure is injected through the public ``on_shard`` progress seam, not by
  monkeypatching a private function.

The last shard is the interesting one: it is short, and a resume that assumes
fixed-size shards silently drops or duplicates its tail.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

import pytest

from tests._phase5 import (
    COLUMNS,
    JOB_ID,
    content_sha256,
    data_file_names,
    export_job,
    parts_dir,
    plan_shards,
    read_csv_table,
    read_jsonl,
    read_manifest,
    read_parquet,
    report_field,
    resume_rows,
    sha256_file,
    source_rows,
    stage_files,
    temp_files,
)

ID_COLUMN = COLUMNS.index("instrument_id")


def _instrument_ids(path: Path) -> list[str]:
    table = read_csv_table(path)
    return [row[ID_COLUMN] for row in table[1:]]


def _first_run_fails_at_shard_three(
    rows: list[dict[str, Any]], directory: Path, *, rows_per_shard: int = 5
) -> list[int]:
    from tests._phase5 import pipeline_module

    pipeline_module()  # RED: fail as NotImplementedError before pytest.raises
    calls: list[int] = []

    def hook(index: int, _row_count: int) -> None:
        calls.append(index)
        if index == 3:
            raise RuntimeError("injected failure at shard 3")

    with pytest.raises(RuntimeError, match="injected failure at shard 3"):
        export_job(
            rows,
            directory=directory,
            rows_per_shard=rows_per_shard,
            on_shard=hook,
        )
    return calls


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_a_rerun_with_the_same_options_is_byte_identical(tmp_path: Path) -> None:
    rows = source_rows()
    first = export_job(rows, directory=tmp_path)
    first_digests = {
        fmt: sha256_file(path) for fmt, path in report_field(first, "files").items()
    }

    second = export_job(rows, directory=tmp_path)

    assert report_field(second, "content_sha256") == report_field(first, "content_sha256")
    assert report_field(second, "content_sha256") == content_sha256(rows)
    assert tuple(report_field(second, "shards_written")) == (), (
        "a completed export must not rewrite its shards"
    )
    for fmt, path in report_field(second, "files").items():
        assert sha256_file(path) == first_digests[fmt], f"{fmt} changed bytes on a re-export"


def test_a_rerun_does_not_duplicate_files_or_leave_temporaries(tmp_path: Path) -> None:
    rows = source_rows()
    first = export_job(rows, directory=tmp_path, rows_per_shard=2)
    before = sorted(path.name for path in tmp_path.iterdir())
    stage_before = sorted(stage_files(tmp_path))

    export_job(rows, directory=tmp_path, rows_per_shard=2)

    after = sorted(path.name for path in tmp_path.iterdir())
    assert after == before, f"a re-export added or removed entries: {set(after) ^ set(before)}"
    assert sorted(stage_files(tmp_path)) == stage_before, "a re-export duplicated a shard file"
    assert len(stage_before) == 3
    assert not temp_files(tmp_path), f"leftover temporary files: {temp_files(tmp_path)}"
    for name in data_file_names().values():
        assert after.count(name) == 1


def test_content_sha256_depends_on_content_not_shard_size(tmp_path: Path) -> None:
    rows = source_rows()
    small = export_job(rows, directory=tmp_path / "small", rows_per_shard=2)
    large = export_job(rows, directory=tmp_path / "large", rows_per_shard=5)

    assert report_field(small, "content_sha256") == report_field(large, "content_sha256")
    assert sha256_file(report_field(small, "files")["csv"]) == sha256_file(
        report_field(large, "files")["csv"]
    ), "shard layout must not leak into the artifact bytes"


def test_a_missing_output_file_is_rebuilt_on_the_next_run(tmp_path: Path) -> None:
    rows = source_rows()
    first = export_job(rows, directory=tmp_path)
    parquet = Path(report_field(first, "files")["parquet"])
    expected = sha256_file(parquet)
    parquet.unlink()

    second = export_job(rows, directory=tmp_path)

    assert parquet.exists(), "a missing artifact must be rebuilt from the staged shards"
    assert sha256_file(parquet) == expected
    assert report_field(second, "content_sha256") == report_field(first, "content_sha256")


# ---------------------------------------------------------------------------
# Shard plan
# ---------------------------------------------------------------------------


def test_the_shard_plan_covers_every_row_exactly_once() -> None:
    assert plan_shards(0, 5) == ()
    assert plan_shards(5, 5) == ((0, 0, 5),)
    assert plan_shards(11, 5) == ((0, 0, 5), (1, 5, 10), (2, 10, 11))

    shards = plan_shards(23, 5)
    assert [triple[0] for triple in shards] == [0, 1, 2, 3, 4]
    covered: list[int] = []
    for _index, start, stop in shards:
        assert stop > start
        covered.extend(range(start, stop))
    assert covered == list(range(23)), "shards must be contiguous, ordered and non-overlapping"


@pytest.mark.parametrize("rows_per_shard", [0, -1])
def test_the_shard_plan_rejects_a_non_positive_size(rows_per_shard: int) -> None:
    with pytest.raises(ValueError):
        plan_shards(10, rows_per_shard)


# ---------------------------------------------------------------------------
# Resume after a failure at shard 3
# ---------------------------------------------------------------------------


def test_a_failure_at_shard_three_finalises_the_earlier_shards(tmp_path: Path) -> None:
    rows = resume_rows()

    calls = _first_run_fails_at_shard_three(rows, tmp_path)

    assert calls == [0, 1, 2, 3], "the hook must fire for the shard that failed, and only it"
    assert not (tmp_path / data_file_names()["csv"]).exists(), (
        "a partial merged table must not exist after a shard failure"
    )
    assert not (tmp_path / f"{JOB_ID}.manifest.json").exists(), (
        "no manifest for an export that did not finish"
    )
    assert [path.name for path in stage_files(tmp_path)] == [
        "shard-000.jsonl",
        "shard-001.jsonl",
        "shard-002.jsonl",
    ]


def test_resume_only_writes_the_remaining_shards(tmp_path: Path) -> None:
    rows = resume_rows()
    _first_run_fails_at_shard_three(rows, tmp_path)
    stage_before = {path.name: sha256_file(path) for path in stage_files(tmp_path)}

    calls: list[int] = []
    report = export_job(
        resume_rows(),
        directory=tmp_path,
        rows_per_shard=5,
        on_shard=lambda index, _count: calls.append(index),
    )

    assert calls == [3, 4], f"resume rewrote finished shards: {calls}"
    assert tuple(report_field(report, "shards_written")) == (3, 4)
    assert tuple(report_field(report, "shards_skipped")) == (0, 1, 2)
    assert report_field(report, "resumed") is True

    for name, digest in stage_before.items():
        assert sha256_file(parts_dir(tmp_path) / name) == digest, (
            f"{name} was rewritten by a resumed run"
        )


def test_resume_finishes_with_every_row_exactly_once(tmp_path: Path) -> None:
    rows = resume_rows()
    _first_run_fails_at_shard_three(rows, tmp_path)

    report = export_job(resume_rows(), directory=tmp_path, rows_per_shard=5)

    csv_path = Path(report_field(report, "files")["csv"])
    ids = _instrument_ids(csv_path)
    assert ids == [row["instrument_id"] for row in rows], (
        "the merged table must keep source order and contain every row once"
    )
    assert len(set(ids)) == len(rows), "resume duplicated a row"
    assert len(read_jsonl(report_field(report, "files")["jsonl"])) == len(rows)
    assert read_parquet(report_field(report, "files")["parquet"]).num_rows == len(rows)

    manifest = read_manifest(report_field(report, "manifest_path"))
    assert manifest["row_count"] == len(rows)
    assert manifest["coverage"]["ratio"] == 1.0
    assert manifest["coverage"]["rows_written"] == len(rows)


def test_resume_is_recorded_in_the_custody_log(tmp_path: Path) -> None:
    from tests._phase5 import read_custody

    rows = resume_rows()
    _first_run_fails_at_shard_three(rows, tmp_path)

    report = export_job(resume_rows(), directory=tmp_path, rows_per_shard=5)
    records = read_custody(report_field(report, "custody_path"))

    resumed = [record for record in records if record["event"] == "export_resumed"]
    assert len(resumed) == 1, [record["event"] for record in records]
    assert list(resumed[0]["skipped_shards"]) == [0, 1, 2]


def test_a_failed_export_is_recorded_before_the_error_is_raised(tmp_path: Path) -> None:
    from tests._phase5 import read_custody

    rows = resume_rows()
    _first_run_fails_at_shard_three(rows, tmp_path)

    records = read_custody(tmp_path / f"{JOB_ID}.custody.jsonl")
    assert records[-1]["event"] == "export_failed", records[-1]
    assert "injected failure at shard 3" in str(records[-1]["error"])
    assert [record["event"] for record in records].count("shard_written") == 3


# ---------------------------------------------------------------------------
# Streaming: a shard is read when it is written, not before
# ---------------------------------------------------------------------------


class CountingRows:
    """An iterator-only source that records how many rows were pulled."""

    def __init__(self, rows: Sequence[Mapping[str, Any]]) -> None:
        self._rows = rows
        self.pulled = 0

    def __iter__(self) -> Iterator[Mapping[str, Any]]:
        for row in self._rows:
            self.pulled += 1
            yield row


def test_export_reads_the_source_one_shard_at_a_time(tmp_path: Path) -> None:
    rows = resume_rows()
    source = CountingRows(rows)
    checks: list[int] = []

    def hook(index: int, _row_count: int) -> None:
        checks.append(source.pulled)
        assert source.pulled <= (index + 1) * 5, (
            f"before shard {index} the source had been pulled {source.pulled} times; "
            "that is list(rows) behaviour, not a stream"
        )

    export_job(source, directory=tmp_path, rows_per_shard=5, on_shard=hook)

    assert source.pulled == len(rows), "the export must read every row exactly once"
    assert len(checks) == 5
