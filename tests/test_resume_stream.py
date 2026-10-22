"""Fase 3 gates 5 and 6 — streaming rows and resumable, idempotent batches.

Streaming gate: 5.000 instruments must not become ``list(rows)``. The source in
``tests/_phase3.py`` is iterable-only and counts pulls, so the assertion is
structural: while the consumer holds batch *k*, the source may have been pulled
at most ``k * chunk_size`` times. A ``list(rows)`` implementation shows 5.000 on
the first batch and fails on the spot. A ``tracemalloc`` ceiling backs it up.

Resume gate: a sink failure at batch 2 must not re-buy batch 1 on the next run
(``engine_calls == 1``) and must not duplicate a row. Idempotency gate: replaying
a batch leaves ``row_count`` and the row set unchanged.
"""

from __future__ import annotations

import json
import tracemalloc

import pytest

from app.jobs.repository import SqliteJobRepository
from tests._phase3 import (
    CHUNK_SIZE,
    ROW_PAYLOAD_BYTES,
    STREAM_ROWS,
    BatchFileSink,
    CountingEngine,
    CountingSource,
    batch,
    batch_api,
    create_job,
    row_stream_class,
    run_batches_fn,
    stored_rows,
)


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------


def test_row_stream_never_reads_more_than_one_chunk_ahead() -> None:
    stream_class = row_stream_class()
    source = CountingSource(STREAM_ROWS, ROW_PAYLOAD_BYTES)

    assert not hasattr(source, "__len__") and not hasattr(source, "__getitem__"), (
        "the source must be iterator-only; otherwise the test cannot prove "
        "RowStream avoids list(rows)"
    )

    stream = stream_class(source, chunk_size=CHUNK_SIZE)
    seen = 0
    batches = 0
    for chunk in stream:
        batches += 1
        assert isinstance(chunk, list), type(chunk)
        assert 0 < len(chunk) <= CHUNK_SIZE, len(chunk)
        assert source.pulled <= batches * CHUNK_SIZE, (
            f"after {batches} batch(es) the source was pulled {source.pulled} times; "
            f"peak must stay <= chunk_size ({CHUNK_SIZE}) — this is list(rows) behaviour"
        )
        seen += len(chunk)

    assert batches == STREAM_ROWS // CHUNK_SIZE == 50
    assert seen == STREAM_ROWS
    assert stream.count == STREAM_ROWS


def test_row_stream_peak_memory_stays_bounded_by_the_chunk() -> None:
    stream_class = row_stream_class()
    total_bytes = STREAM_ROWS * ROW_PAYLOAD_BYTES
    budget = 8 * CHUNK_SIZE * ROW_PAYLOAD_BYTES  # one chunk plus allocator headroom

    tracemalloc.start()
    try:
        stream = stream_class(CountingSource(STREAM_ROWS, ROW_PAYLOAD_BYTES), chunk_size=CHUNK_SIZE)
        total = sum(len(chunk) for chunk in stream)
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert total == STREAM_ROWS
    assert peak <= budget, (
        f"peak {peak} bytes for {total_bytes} bytes of rows; the stream materialised "
        f"the input instead of keeping one {CHUNK_SIZE}-row chunk"
    )


def test_row_stream_last_chunk_may_be_shorter() -> None:
    stream_class = row_stream_class()
    stream = stream_class(({"instrument_id": f"XNAS:S{index}"} for index in range(250)), chunk_size=100)

    assert [len(chunk) for chunk in stream] == [100, 100, 50]
    assert stream.count == 250


def test_row_stream_handles_an_empty_source() -> None:
    stream = row_stream_class()([], chunk_size=10)

    assert list(stream) == []
    assert stream.count == 0


@pytest.mark.parametrize("chunk_size", [0, -1])
def test_row_stream_rejects_a_non_positive_chunk_size(chunk_size) -> None:
    with pytest.raises(ValueError):
        row_stream_class()(iter(()), chunk_size=chunk_size)


# ---------------------------------------------------------------------------
# Checkpoints
# ---------------------------------------------------------------------------


async def test_checkpoint_rows_are_upserts_not_accumulators(tmp_path) -> None:
    repository = SqliteJobRepository(tmp_path / "mee.db")
    try:
        job = await create_job(repository, ["AAPL", "MSFT"])
        store = batch_api(repository)
        plan = [batch(0, ["AAPL"]), batch(1, ["MSFT"])]

        await store.ensure_batches(job["id"], plan)
        await store.ensure_batches(job["id"], plan)
        rows = await store.batches(job["id"])

        assert [row["batch_index"] for row in rows] == [0, 1], (
            "ensure_batches must be idempotent on UNIQUE(job_id, batch_index)"
        )
        assert len(rows) == 2
        assert json.loads(rows[0]["universe_slice"])["symbols"] == ["AAPL"]
        assert rows[0]["symbols_expected"] == 1
        assert rows[1]["status"] == "Pending"

        await store.mark_batch(job["id"], 0, status="Success", row_count=3)
        await store.mark_batch(job["id"], 0, status="Success", row_count=3)
        (first, _second) = await store.batches(job["id"])
        assert first["row_count"] == 3, "mark_batch must SET row_count, never add to it"

        await store.mark_batch(job["id"], 0, status="Success", row_count=5)
        (first, _second) = await store.batches(job["id"])
        assert first["row_count"] == 5, "the last execution is the truth, not the sum of runs"
        assert len(await store.batches(job["id"])) == 2
    finally:
        await repository.close()


async def test_resume_skips_successful_batches_without_duplicating_rows(tmp_path) -> None:
    run_batches = run_batches_fn()
    repository = SqliteJobRepository(tmp_path / "mee.db")
    try:
        job = await create_job(repository, ["AAPL", "MSFT", "NVDA", "AMD"])
        store = batch_api(repository)
        engine = CountingEngine()
        sink = BatchFileSink(tmp_path / "artifacts", fail_on=1)
        plan = [batch(0, ["AAPL", "MSFT"]), batch(1, ["NVDA", "AMD"])]

        with pytest.raises(RuntimeError):
            await run_batches(
                job["id"], plan, repository=repository, engine=engine, sink=sink
            )

        rows = await store.batches(job["id"])
        assert [row["status"] for row in rows] == ["Success", "Failure"], rows
        assert rows[0]["row_count"] == 2
        assert len(engine.calls) == 2

        engine.calls.clear()
        outcome = await run_batches(
            job["id"], plan, repository=repository, engine=engine, sink=sink
        )

        assert engine.calls == [("NVDA", "AMD")], (
            "the successful first batch was re-executed: resume bought it twice"
        )
        assert len(engine.calls) == 1
        assert outcome["batches_skipped"] == 1
        assert outcome["rows_unique"] == 4

        stored = stored_rows(sink.directory)
        identifiers = [row["instrument_id"] for row in stored]
        assert len(identifiers) == 4, stored
        assert len(identifiers) == len(set(identifiers)), (
            f"resume duplicated rows: {sorted(identifiers)}"
        )
        assert set(identifiers) == {"XNAS:AAPL", "XNAS:MSFT", "XNAS:NVDA", "XNAS:AMD"}

        rows = await store.batches(job["id"])
        assert [row["status"] for row in rows] == ["Success", "Success"]
        assert len(rows) == 2, "a retry reuses the batch row, it does not stack a new one"
        assert rows[1]["row_count"] == 2
    finally:
        await repository.close()


async def test_replaying_a_batch_does_not_change_row_count(tmp_path) -> None:
    run_batches = run_batches_fn()
    repository = SqliteJobRepository(tmp_path / "mee.db")
    try:
        job = await create_job(repository, ["AAPL", "MSFT"])
        store = batch_api(repository)
        engine = CountingEngine()
        sink = BatchFileSink(tmp_path / "artifacts")
        plan = [batch(0, ["AAPL", "MSFT"])]

        first = await run_batches(job["id"], plan, repository=repository, engine=engine, sink=sink)
        second = await run_batches(
            job["id"], plan, repository=repository, engine=engine, sink=sink, resume=False
        )

        assert first["rows_unique"] == second["rows_unique"] == 2, (
            "writing the same batch twice must not add rows"
        )
        (row,) = await store.batches(job["id"])
        assert row["row_count"] == 2
        assert len(stored_rows(sink.directory)) == 2, "the artifact was appended to, not replaced"
    finally:
        await repository.close()


async def test_batch_row_count_deduplicates_instrument_ids(tmp_path) -> None:
    run_batches = run_batches_fn()
    repository = SqliteJobRepository(tmp_path / "mee.db")
    try:
        job = await create_job(repository, ["AAPL"])
        store = batch_api(repository)
        engine = CountingEngine(duplicate=True)
        sink = BatchFileSink(tmp_path / "artifacts")

        outcome = await run_batches(
            job["id"],
            [batch(0, ["AAPL"])],
            repository=repository,
            engine=engine,
            sink=sink,
        )

        (row,) = await store.batches(job["id"])
        assert row["row_count"] == 1, "instrument_id is unique per job (02 §3.5 idempotency)"
        assert outcome["rows_unique"] == 1
        assert len(sink.rows[0]) == 1
        assert len(stored_rows(sink.directory)) == 1
    finally:
        await repository.close()
