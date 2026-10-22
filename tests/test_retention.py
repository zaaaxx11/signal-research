"""Fase 5 gate 3 — retention that deletes state in the right order, or not at all.

Three rules, each of which has a cheap wrong implementation:

1. **30 days by default.** A job that expired yesterday at seven days is still
   inside the default window. Deleting it because ``expires_at`` passed makes
   the sweeper a data-loss bug with a cron entry.
2. **Only expired jobs.** A 90-day-old job whose expiry is in the future is not
   garbage; age alone is not authority to delete.
3. **Files first, rows second, one transaction.** The 02 §4.8 order is asserted
   with an injected ``unlink``; the all-or-nothing rule is asserted by making one
   unlink fail and checking that every row survives. A sweeper that deletes the
   job row before the file leaves an artifact nobody can find.

Purges are appended to ``retention.custody.jsonl`` with the hash of every file
that was removed, because "we deleted it" without "what it was" is not an audit.
"""

from __future__ import annotations

from pathlib import Path

from app.jobs.repository import SqliteJobRepository
from tests._phase5 import (
    NOW,
    PurgeSpy,
    RecordingUnlink,
    backdate,
    cleanup_module,
    job_row,
    purge_jobs,
    read_custody,
    report_field,
    seed_job,
    sha256_file,
    sweep,
    table_rows,
)


async def _repository(db_path: Path) -> SqliteJobRepository:
    return SqliteJobRepository(db_path)


# ---------------------------------------------------------------------------
# The policy
# ---------------------------------------------------------------------------


def test_the_default_retention_policy_is_thirty_days() -> None:
    assert cleanup_module().DEFAULT_RETENTION_DAYS == 30


async def test_default_retention_keeps_a_ten_day_old_expired_job(tmp_path: Path) -> None:
    db, exports = tmp_path / "mee.db", tmp_path / "exports"
    seeded = await seed_job(
        db, exports, job_id="job-young", created_days_ago=10, expires_in_days=-3
    )
    repository = await _repository(db)
    try:
        report = await sweep(repository, directory=exports)
    finally:
        await repository.close()

    assert tuple(report_field(report, "purged")) == (), (
        "the default 30-day window must protect a 10-day-old job, expired or not"
    )
    assert seeded.files[0].exists()
    assert job_row(db, seeded.job_id) is not None


async def test_default_retention_purges_a_job_past_thirty_days(tmp_path: Path) -> None:
    db, exports = tmp_path / "mee.db", tmp_path / "exports"
    seeded = await seed_job(
        db, exports, job_id="job-old", created_days_ago=31, expires_in_days=-24
    )
    repository = await _repository(db)
    try:
        report = await sweep(repository, directory=exports)
    finally:
        await repository.close()

    assert tuple(report_field(report, "purged")) == (seeded.job_id,)
    assert not seeded.files[0].exists(), "the artifact file must be gone"
    assert job_row(db, seeded.job_id) is None
    assert table_rows(db, "job_artifacts", seeded.job_id) == []


async def test_an_unexpired_job_is_never_purged_even_when_old(tmp_path: Path) -> None:
    db, exports = tmp_path / "mee.db", tmp_path / "exports"
    seeded = await seed_job(
        db, exports, job_id="job-extended", created_days_ago=90, expires_in_days=30
    )
    repository = await _repository(db)
    try:
        report = await sweep(repository, directory=exports, retention_days=7)
    finally:
        await repository.close()

    assert tuple(report_field(report, "purged")) == ()
    assert seeded.files[0].exists()
    assert job_row(db, seeded.job_id) is not None


async def test_an_explicit_policy_purges_only_the_expired_job(tmp_path: Path) -> None:
    db, exports = tmp_path / "mee.db", tmp_path / "exports"
    expired = await seed_job(
        db, exports, job_id="job-expired", created_days_ago=10, expires_in_days=-3
    )
    live = await seed_job(
        db, exports, job_id="job-live", created_days_ago=10, expires_in_days=5
    )
    repository = await _repository(db)
    try:
        report = await sweep(repository, directory=exports, retention_days=7)
    finally:
        await repository.close()

    assert tuple(report_field(report, "purged")) == (expired.job_id,)
    assert job_row(db, expired.job_id) is None
    assert job_row(db, live.job_id) is not None
    assert not expired.files[0].exists()
    assert live.files[0].exists(), "the live job's artifact was collateral damage"


# ---------------------------------------------------------------------------
# Order and all-or-nothing
# ---------------------------------------------------------------------------


async def test_sweep_unlinks_every_file_before_it_deletes_a_row(tmp_path: Path) -> None:
    db, exports = tmp_path / "mee.db", tmp_path / "exports"
    await seed_job(
        db,
        exports,
        job_id="job-order",
        created_days_ago=10,
        expires_in_days=-3,
        formats=("csv", "manifest"),
    )
    events: list[tuple[str, object]] = []
    unlink = RecordingUnlink()
    unlink.events = events  # type: ignore[assignment]
    repository = await _repository(db)
    spy = PurgeSpy(repository, events)
    try:
        report = await sweep(spy, directory=exports, retention_days=7, unlink=unlink)
    finally:
        await repository.close()

    kinds = [kind for kind, _payload in events]
    assert kinds.count("unlink") == 2, kinds
    assert kinds.count("purge") == 1, kinds
    assert kinds[-1] == "purge", f"a row was deleted before every file: {kinds}"
    assert report_field(report, "aborted") is False
    assert len(report_field(report, "purged")) == 1


async def test_a_failed_unlink_leaves_every_row_in_place(tmp_path: Path) -> None:
    db, exports = tmp_path / "mee.db", tmp_path / "exports"
    seeded = await seed_job(
        db, exports, job_id="job-stuck", created_days_ago=10, expires_in_days=-3
    )
    unlink = RecordingUnlink(fail_on=seeded.files[0])
    repository = await _repository(db)
    spy = PurgeSpy(repository, [])
    try:
        report = await sweep(spy, directory=exports, retention_days=7, unlink=unlink)
    finally:
        await repository.close()

    assert report_field(report, "aborted") is True
    assert tuple(report_field(report, "purged")) == (), (
        "one failed unlink must abort the whole purge, not delete half of it"
    )
    assert job_row(db, seeded.job_id) is not None, "the job row was deleted anyway"
    assert len(table_rows(db, "job_artifacts", seeded.job_id)) == 1
    assert seeded.files[0].exists(), "the unlink that failed must not have removed the file"


async def test_a_failed_unlink_is_named_in_the_report(tmp_path: Path) -> None:
    db, exports = tmp_path / "mee.db", tmp_path / "exports"
    seeded = await seed_job(
        db, exports, job_id="job-stuck2", created_days_ago=10, expires_in_days=-3
    )
    repository = await _repository(db)
    spy = PurgeSpy(repository, [])
    try:
        report = await sweep(
            spy,
            directory=exports,
            retention_days=7,
            unlink=RecordingUnlink(fail_on=seeded.files[0]),
        )
    finally:
        await repository.close()

    failed = list(report_field(report, "failed"))
    assert failed, "an aborted sweep must say which file it could not delete"
    paths = {str(entry["path"]) for entry in failed}
    assert str(seeded.files[0]) in paths, failed
    assert all(entry.get("error") for entry in failed)


async def test_sweep_refuses_to_unlink_outside_the_export_root(tmp_path: Path) -> None:
    db = tmp_path / "mee.db"
    exports = tmp_path / "exports"
    exports.mkdir()
    outside = tmp_path / "outside.csv"
    outside.write_bytes(b"not-ours\n")
    repository = await _repository(db)
    try:
        job = await repository.create(
            params={"market": {"kind": "watchlist", "symbols": ["AAPL"]}},
            api_key="client-phase5",
            rows_target=1,
            retention_days=7,
            job_id="job-outside",
        )
        await repository.record_artifact(
            job["id"],
            kind="csv",
            path=outside,
            sha256=sha256_file(outside),
            row_count=1,
            bytes_=outside.stat().st_size,
        )
    finally:
        await repository.close()

    backdate(db, "job-outside", created_days_ago=10)
    repository = await _repository(db)
    try:
        report = await sweep(repository, directory=exports, retention_days=7)
    finally:
        await repository.close()

    assert report_field(report, "aborted") is True
    assert outside.exists(), "a sweep deleted a file outside its own export directory"
    assert job_row(db, "job-outside") is not None


# ---------------------------------------------------------------------------
# Dry run and no-op runs
# ---------------------------------------------------------------------------


async def test_a_dry_run_reports_without_deleting_anything(tmp_path: Path) -> None:
    db, exports = tmp_path / "mee.db", tmp_path / "exports"
    seeded = await seed_job(
        db, exports, job_id="job-dry", created_days_ago=10, expires_in_days=-3
    )
    repository = await _repository(db)
    try:
        report = await sweep(repository, directory=exports, retention_days=7, dry_run=True)
    finally:
        await repository.close()

    assert report_field(report, "dry_run") is True
    assert tuple(report_field(report, "purged")) == (seeded.job_id,)
    assert seeded.files[0].exists()
    assert job_row(db, seeded.job_id) is not None
    assert not (exports / "retention.custody.jsonl").exists()


async def test_a_second_sweep_is_a_no_op(tmp_path: Path) -> None:
    db, exports = tmp_path / "mee.db", tmp_path / "exports"
    await seed_job(db, exports, job_id="job-once", created_days_ago=10, expires_in_days=-3)
    repository = await _repository(db)
    try:
        first = await sweep(repository, directory=exports, retention_days=7)
        custody = exports / "retention.custody.jsonl"
        lines_after_first = len(custody.read_text(encoding="utf-8").splitlines())
        second = await sweep(repository, directory=exports, retention_days=7)
        lines_after_second = len(custody.read_text(encoding="utf-8").splitlines())
    finally:
        await repository.close()

    assert len(report_field(first, "purged")) == 1
    assert report_field(second, "aborted") is False
    assert tuple(report_field(second, "purged")) == ()
    assert lines_after_second == lines_after_first, "an empty sweep wrote a duplicate record"


# ---------------------------------------------------------------------------
# Chain of custody and children
# ---------------------------------------------------------------------------


async def test_a_purge_is_written_to_the_chain_of_custody(tmp_path: Path) -> None:
    db, exports = tmp_path / "mee.db", tmp_path / "exports"
    seeded = await seed_job(
        db,
        exports,
        job_id="job-audit",
        created_days_ago=10,
        expires_in_days=-3,
        formats=("csv", "manifest"),
    )
    digests = {path.name: sha256_file(path) for path in seeded.files}

    repository = await _repository(db)
    try:
        report = await sweep(repository, directory=exports, retention_days=7)
    finally:
        await repository.close()

    custody = Path(report_field(report, "custody_path"))
    assert custody == exports / "retention.custody.jsonl"
    records = read_custody(custody)
    assert len(records) == 1, records
    record = records[0]
    assert record["seq"] == 1
    assert record["event"] == "retention_purged"
    assert record["job_id"] == seeded.job_id
    written = {entry["name"]: entry["sha256"] for entry in record["files"]}
    assert written == digests, "the audit record must hash what it destroyed"
    assert record["bytes_freed"] > 0


async def test_purge_jobs_removes_the_children_in_one_transaction(tmp_path: Path) -> None:
    db, exports = tmp_path / "mee.db", tmp_path / "exports"
    exports.mkdir()
    repository = await _repository(db)
    try:
        job = await repository.create(
            params={"market": {"kind": "watchlist", "symbols": ["AAPL"]}},
            api_key="client-phase5",
            rows_target=1,
            retention_days=7,
            job_id="job-children",
        )
        artifact = exports / "job-children.csv"
        artifact.write_bytes(b"x\n")
        await repository.record_artifact(
            job["id"],
            kind="csv",
            path=artifact,
            sha256=sha256_file(artifact),
            row_count=1,
            bytes_=artifact.stat().st_size,
        )
        await repository.record_rejections(
            job["id"],
            [
                {
                    "instrument_id": "XNAS:AAPL",
                    "column": "opt_iv_atm",
                    "rule": "V9",
                    "reason": "parity_violation",
                    "value_text": "0.31",
                }
            ],
        )
        await repository.ensure_batches(job["id"], [{"batch_index": 0, "symbols": ["AAPL"]}])
        assert table_rows(db, "verification_rejections", job["id"])
        assert table_rows(db, "job_batches", job["id"])

        deleted = await purge_jobs(repository, [job["id"]])
    finally:
        await repository.close()

    assert deleted == 1
    assert job_row(db, job["id"]) is None
    assert table_rows(db, "job_artifacts", job["id"]) == []
    assert table_rows(db, "verification_rejections", job["id"]) == []
    assert table_rows(db, "job_batches", job["id"]) == []


async def test_sweep_never_touches_a_custody_log_from_another_job(tmp_path: Path) -> None:
    import json

    db, exports = tmp_path / "mee.db", tmp_path / "exports"
    await seed_job(db, exports, job_id="job-keep", created_days_ago=2, expires_in_days=5)
    keeper = exports / "retention.custody.jsonl"
    prior = {"seq": 1, "at": NOW.isoformat(), "job_id": "prior", "event": "retention_purged"}
    keeper.write_text(json.dumps(prior) + "\n", encoding="utf-8")
    await seed_job(db, exports, job_id="job-drop", created_days_ago=10, expires_in_days=-3)

    repository = await _repository(db)
    try:
        await sweep(repository, directory=exports, retention_days=7)
    finally:
        await repository.close()

    assert keeper.exists(), "the shared custody log is not an artifact to sweep"
    first = json.loads(keeper.read_text(encoding="utf-8").splitlines()[0])
    assert first == prior, "an existing custody record was rewritten"
