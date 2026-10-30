"""Contract for the depth per-source health ledger.

``DepthCollector.refresh()`` already logs one line per source; a log is not an
answer to "which rung is sick" at 03:00, and nobody reads yesterday's line. The
ledger makes that question a document: every source records a success (duration,
field count) or a failure (the error text), the counts survive a restart, the
write is atomic, and ``GET /market/depth/stats`` republishes the same view
without changing a single pre-existing key.

The collector tests use tiny fake rungs rather than a mocked collector, so the
real refresh loop — chunking, failure isolation, per-source timing — is what
fills the ledger.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Sequence

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.api import market as market_api
from app.auth import ApiError
from app.market import health_ledger
from app.market.depth import DepthCollector, DepthObservation
from app.market.health_ledger import HealthLedger, SourceHealth

T0 = datetime(2026, 10, 30, 3, 0, tzinfo=UTC)


def _clock() -> datetime:
    return T0


# ---------------------------------------------------------------------------
# The record and the ledger
# ---------------------------------------------------------------------------


def test_a_new_ledger_is_empty_and_writes_nothing(tmp_path: Path) -> None:
    """No source has run yet: the document is empty, not a row of zeros."""

    ledger = HealthLedger(tmp_path / "depth.health.json", clock=_clock)

    assert ledger.summary() == {}
    assert ledger.document() == {}
    assert len(ledger) == 0


def test_a_ledger_without_a_path_keeps_its_counts_in_memory_only() -> None:
    """``store_path=None`` collectors (tests, dry runs) must not write files."""

    ledger = HealthLedger(None, clock=_clock)
    ledger.record_ok("sec", duration_s=1.25, fields=3)

    assert ledger.summary()["sec"]["ok"] is True
    assert ledger.persist() is False


def test_recording_a_success_publishes_a_healthy_entry(tmp_path: Path) -> None:
    """The document is the plan's shape: ok, last_ok, last_error, streak, seconds."""

    ledger = HealthLedger(tmp_path / "depth.health.json", clock=_clock)

    ledger.record_ok("sec_bulk", duration_s=270.25, fields=78_323)

    assert ledger.summary()["sec_bulk"] == {
        "ok": True,
        "last_ok": T0.isoformat(),
        "last_error": None,
        "consecutive_errors": 0,
        "last_duration_s": 270.25,
    }
    record = ledger.get("sec_bulk")
    assert record is not None
    assert record.ok_count == 1
    assert record.error_count == 0
    assert record.last_fields == 78_323
    assert record.last_duration_s == 270.25


def test_recording_failures_counts_them_and_keeps_the_message(tmp_path: Path) -> None:
    """A failing source is not ok, has no last_ok, and carries the error text."""

    ledger = HealthLedger(tmp_path / "depth.health.json", clock=_clock)

    ledger.record_error("finra", error="finra: TimeoutError: deadline")
    ledger.record_error("finra", error="finra: TimeoutError: deadline")

    entry = ledger.summary()["finra"]
    assert entry["ok"] is False
    assert entry["last_ok"] is None
    assert entry["last_error"] == "finra: TimeoutError: deadline"
    assert entry["consecutive_errors"] == 2
    record = ledger.get("finra")
    assert record is not None
    assert record.error_count == 2
    assert record.ok_count == 0


def test_a_success_after_failures_resets_the_streak_and_clears_the_error(
    tmp_path: Path,
) -> None:
    """Recovery is visible: the streak goes to zero, the old error is not current."""

    ledger = HealthLedger(tmp_path / "depth.health.json", clock=_clock)
    ledger.record_error("sec", error="sec: RuntimeError: boom")
    ledger.record_error("sec", error="sec: RuntimeError: boom")

    ledger.record_ok("sec", duration_s=1.5, fields=7)

    entry = ledger.summary()["sec"]
    assert entry["consecutive_errors"] == 0
    assert entry["last_error"] is None
    assert entry["ok"] is True
    record = ledger.get("sec")
    assert record is not None
    # The lifetime counters still tell the story of the bad night.
    assert record.ok_count == 1
    assert record.error_count == 2


def test_a_huge_error_message_is_truncated(tmp_path: Path) -> None:
    """A provider that puts a page in its exception must not grow the file."""

    ledger = HealthLedger(tmp_path / "depth.health.json", clock=_clock)
    ledger.record_error("news", error="z" * (health_ledger.MAX_ERROR_CHARS * 4))

    record = ledger.get("news")
    assert record is not None
    assert record.last_error is not None
    assert len(record.last_error) == health_ledger.MAX_ERROR_CHARS


def test_persist_round_trips_every_recorded_field(tmp_path: Path) -> None:
    """What lands on disk is the full record, and a new ledger reads it back."""

    path = tmp_path / "depth.health.json"
    ledger = HealthLedger(path, clock=_clock)
    ledger.record_ok("sec", duration_s=1.0, fields=2)
    ledger.record_error("finra", error="finra: RuntimeError: boom")

    assert ledger.persist() is True

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["finra"] == {
        "name": "finra",
        "last_ok": None,
        "last_error": "finra: RuntimeError: boom",
        "ok_count": 0,
        "error_count": 1,
        "consecutive_errors": 1,
        "last_duration_s": None,
        "last_fields": None,
    }
    reloaded = HealthLedger(path, clock=_clock)
    assert reloaded.summary() == ledger.summary()


def test_the_ledger_path_is_derived_beside_the_store() -> None:
    """No new config key: the ledger sits where the cursors already do."""

    assert health_ledger.health_path_for(Path("data/depth.json")) == Path(
        "data/depth.health.json"
    )
    assert health_ledger.health_path_for("/srv/mee/nightly.json") == Path(
        "/srv/mee/nightly.health.json"
    )
    assert health_ledger.health_path_for(None) is None
    # A last segment that cannot take a suffix must not stop a collector build.
    odd = health_ledger.health_path_for("data/depth.")
    assert odd is not None and odd.name.endswith("health.json")


@pytest.mark.parametrize(
    "payload",
    [
        "",  # truncated to nothing
        '{"sec": {"ok_count": 1',  # truncated mid-object
        "not json at all",
        "[]",  # valid JSON, wrong shape
        "null",
        '{"sec": 5}',  # valid JSON, entry is not an object
        '{"sec": {"ok_count": "many", "last_ok": 12}}',  # wrong field types
        # json.loads accepts bare Infinity/NaN tokens; a count of Infinity is
        # corruption, not a number that can be compared.
        '{"sec": {"ok_count": Infinity, "last_duration_s": NaN}}',
    ],
)
def test_a_broken_ledger_loads_as_an_empty_ledger(tmp_path: Path, payload: str) -> None:
    """Corruption is a monitoring artifact, not a reason to fail a cycle."""

    path = tmp_path / "depth.health.json"
    path.write_text(payload, encoding="utf-8")

    ledger = HealthLedger(path, clock=_clock)

    assert ledger.summary() == {}
    # And it can be rewritten: the next cycle repairs the file.
    ledger.record_ok("sec", duration_s=1.0, fields=1)
    assert ledger.persist() is True
    assert json.loads(path.read_text(encoding="utf-8"))["sec"]["ok_count"] == 1


def test_a_missing_ledger_is_an_empty_ledger(tmp_path: Path) -> None:
    ledger = HealthLedger(tmp_path / "absent.health.json", clock=_clock)

    assert ledger.summary() == {}


def test_a_ledger_of_binary_garbage_is_an_empty_ledger(tmp_path: Path) -> None:
    """Not even valid UTF-8: the read must degrade, not raise."""

    path = tmp_path / "depth.health.json"
    path.write_bytes(b"\xff\xfe\x00\x01garbage")

    ledger = HealthLedger(path, clock=_clock)

    assert ledger.summary() == {}


def test_a_source_entry_survives_a_partially_garbage_document(tmp_path: Path) -> None:
    """One unreadable entry must not throw away the readable ones."""

    path = tmp_path / "depth.health.json"
    path.write_text(
        json.dumps(
            {
                "sec": {
                    "last_ok": T0.isoformat(),
                    "ok_count": 4,
                    "error_count": 1,
                    "consecutive_errors": 1,
                    "last_duration_s": "12.5",
                    "last_fields": "9",
                },
                "finra": "not an object",
            }
        ),
        encoding="utf-8",
    )

    ledger = HealthLedger(path, clock=_clock)

    assert set(ledger.summary()) == {"sec"}
    record = ledger.get("sec")
    assert record is not None
    assert record.ok_count == 4
    assert record.consecutive_errors == 1
    assert record.last_duration_s == 12.5
    assert record.last_fields == 9


def test_persist_leaves_no_temporary_file_behind(tmp_path: Path) -> None:
    """``tmp + os.replace`` means one visible name, never a half file."""

    ledger = HealthLedger(tmp_path / "depth.health.json", clock=_clock)
    ledger.record_ok("sec", duration_s=1.0, fields=1)

    assert ledger.persist() is True

    assert sorted(path.name for path in tmp_path.iterdir()) == ["depth.health.json"]


def test_a_failed_replace_keeps_the_previous_file_readable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A crash between write and replace must leave the old ledger intact."""

    path = tmp_path / "depth.health.json"
    ledger = HealthLedger(path, clock=_clock)
    ledger.record_ok("sec", duration_s=1.0, fields=1)
    assert ledger.persist() is True
    before = path.read_text(encoding="utf-8")

    ledger.record_ok("sec", duration_s=2.0, fields=5)

    def refuse(src: Any, dst: Any) -> None:
        raise OSError("simulated crash between write and replace")

    monkeypatch.setattr(health_ledger.os, "replace", refuse)

    assert ledger.persist() is False
    assert path.read_text(encoding="utf-8") == before
    assert not (tmp_path / ".depth.health.json.tmp").exists()


def test_an_unwritable_directory_reports_false_instead_of_raising(
    tmp_path: Path,
) -> None:
    """A read-only volume must not turn a monitoring write into an exception."""

    blocked = tmp_path / "blocked"
    blocked.mkdir()
    ledger = HealthLedger(blocked / "depth.health.json", clock=_clock)
    ledger.record_ok("sec", duration_s=1.0, fields=1)

    blocked.chmod(0o500)
    try:
        writable = os.access(blocked, os.W_OK)
        wrote = ledger.persist()
    finally:
        blocked.chmod(0o700)

    # Root ignores the mode bits; either way the answer is a bool, not a raise.
    assert wrote is writable


def test_a_success_after_a_gap_keeps_the_success_streak_meaningful(
    tmp_path: Path,
) -> None:
    """``ok`` describes the last outcome; ``ok_count`` describes the lifetime."""

    ledger = HealthLedger(tmp_path / "depth.health.json", clock=_clock)
    ledger.record_ok("sec", duration_s=1.0, fields=1)
    ledger.record_ok("sec", duration_s=1.0, fields=1)

    assert ledger.summary()["sec"]["ok"] is True
    assert ledger.get("sec").ok_count == 2  # type: ignore[union-attr]


def test_retain_drops_entries_for_sources_that_are_no_longer_configured(
    tmp_path: Path,
) -> None:
    """A removed rung must not be reported as permanently sick."""

    ledger = HealthLedger(tmp_path / "depth.health.json", clock=_clock)
    ledger.record_ok("sec", duration_s=1.0, fields=1)
    ledger.record_ok("stockanalysis", duration_s=1.0, fields=1)

    dropped = ledger.retain(["sec"])

    assert dropped == ["stockanalysis"]
    assert ledger.names() == ["sec"]


# ---------------------------------------------------------------------------
# Restart semantics (adversarial a)
# ---------------------------------------------------------------------------


def test_a_restart_never_resets_a_failure_streak(tmp_path: Path) -> None:
    """The streak is evidence about the source, not about this process.

    Loading must not reset ``consecutive_errors``: a rung that failed three
    nights in a row must not look healthy because the service was deployed.
    """

    path = tmp_path / "depth.health.json"
    first = HealthLedger(path, clock=_clock)
    first.record_error("finra", error="finra: TimeoutError: deadline")
    first.record_error("finra", error="finra: TimeoutError: deadline")
    assert first.persist() is True

    restarted = HealthLedger(path, clock=_clock)
    entry = restarted.summary()["finra"]
    assert entry["consecutive_errors"] == 2
    assert entry["ok"] is False
    assert entry["last_error"] == "finra: TimeoutError: deadline"

    # The next failure in the new process continues the same streak.
    restarted.record_error("finra", error="finra: TimeoutError: deadline")
    assert restarted.summary()["finra"]["consecutive_errors"] == 3


def test_a_stale_success_is_visible_by_its_own_last_ok_stamp(tmp_path: Path) -> None:
    """A loaded ok is "last outcome", not "alive now": ``last_ok`` carries the age.

    The ledger deliberately does not invent a freshness budget — the caller
    compares ``last_ok`` with its own cadence (the same rule ``/health`` uses
    for the store). What it must never do is hide a streak.
    """

    old = T0 - timedelta(hours=26)
    path = tmp_path / "depth.health.json"
    path.write_text(
        json.dumps(
            {
                "sec": {
                    "last_ok": old.isoformat(),
                    "ok_count": 5,
                    "error_count": 0,
                    "consecutive_errors": 0,
                }
            }
        ),
        encoding="utf-8",
    )

    ledger = HealthLedger(path, clock=_clock)
    entry = ledger.summary()["sec"]

    assert entry["ok"] is True
    assert datetime.fromisoformat(entry["last_ok"]) == old


# ---------------------------------------------------------------------------
# The refresh loop fills the ledger — tiny fake rungs, real collector
# ---------------------------------------------------------------------------


class FakeRung:
    """A depth source the real refresh loop drives; fails on demand.

    ``fail_all`` makes every chunk raise; ``fail_chunks`` selects chunk indexes
    (0 = first). ``calls`` proves the loop still asked for every chunk.
    """

    def __init__(
        self,
        name: str,
        *,
        fields: dict[str, Any] | None = None,
        fail_all: bool = False,
        fail_chunks: set[int] | None = None,
        message: str = "upstream said no",
    ) -> None:
        self.name = name
        self.fields = fields if fields is not None else {"pe": 10.0}
        self.fail_all = fail_all
        self.fail_chunks = set(fail_chunks or ())
        self.message = message
        self.calls: list[tuple[str, ...]] = []

    async def fetch_many(self, symbols: Sequence[str]) -> dict[str, DepthObservation]:
        index = len(self.calls)
        self.calls.append(tuple(symbols))
        if self.fail_all or index in self.fail_chunks:
            raise RuntimeError(self.message)
        return {
            symbol: DepthObservation(
                symbol=symbol, fields=dict(self.fields), as_of=T0, source=self.name
            )
            for symbol in symbols
        }


def _collector(
    tmp_path: Path,
    sources: Sequence[FakeRung],
    *,
    symbols: Sequence[str] = ("AAPL", "MSFT"),
    chunk_size: int = 250,
) -> DepthCollector:
    """A real collector over a real store path; the ledger path is derived."""

    return DepthCollector(
        symbols=list(symbols),
        sources=list(sources),
        store_path=tmp_path / "depth.json",
        source_chunk_size=chunk_size,
    )


def _on_disk(tmp_path: Path) -> dict[str, dict[str, Any]]:
    return json.loads((tmp_path / "depth.health.json").read_text(encoding="utf-8"))


def test_a_cycle_records_two_healthy_sources_and_one_sick_one(tmp_path: Path) -> None:
    """The plan's measurable goal: ok_count=2 for good, streak=1 for the bad."""

    sec = FakeRung("sec")
    yahoo_chart = FakeRung("yahoo_chart")
    finra = FakeRung("finra", fail_all=True, message="429 Too Many Requests")
    collector = _collector(tmp_path, [sec, yahoo_chart, finra])

    result = asyncio.run(collector.refresh())

    assert result.errors == 1
    assert result.returned == 2, "the healthy rungs still filled the store"

    payload = _on_disk(tmp_path)
    assert set(payload) == {"sec", "yahoo_chart", "finra"}
    for name in ("sec", "yahoo_chart"):
        assert payload[name]["ok_count"] == 1
        assert payload[name]["error_count"] == 0
        assert payload[name]["consecutive_errors"] == 0
        assert payload[name]["last_error"] is None
        assert datetime.fromisoformat(payload[name]["last_ok"]).tzinfo is not None
        assert payload[name]["last_fields"] == 2, "two symbols x one field"
        assert payload[name]["last_duration_s"] >= 0.0

    assert payload["finra"]["ok_count"] == 0
    assert payload["finra"]["error_count"] == 1
    assert payload["finra"]["consecutive_errors"] == 1
    assert payload["finra"]["last_ok"] is None
    assert payload["finra"]["last_error"] == "finra: RuntimeError: 429 Too Many Requests"


def test_a_second_failing_cycle_raises_the_streak_to_two(tmp_path: Path) -> None:
    good = FakeRung("sec")
    bad = FakeRung("finra", fail_all=True)
    collector = _collector(tmp_path, [good, bad])

    asyncio.run(collector.refresh())
    asyncio.run(collector.refresh())

    payload = _on_disk(tmp_path)
    assert payload["finra"]["consecutive_errors"] == 2
    assert payload["finra"]["error_count"] == 2
    assert payload["sec"]["ok_count"] == 2
    assert payload["sec"]["consecutive_errors"] == 0


def test_a_success_resets_the_streak_on_disk(tmp_path: Path) -> None:
    sec = FakeRung("sec")
    finra = FakeRung("finra", fail_all=True)
    collector = _collector(tmp_path, [sec, finra])

    asyncio.run(collector.refresh())
    assert collector.health.summary()["finra"]["consecutive_errors"] == 1

    finra.fail_all = False
    asyncio.run(collector.refresh())

    entry = collector.health.summary()["finra"]
    assert entry["consecutive_errors"] == 0
    assert entry["last_error"] is None
    assert entry["ok"] is True
    assert _on_disk(tmp_path)["finra"]["consecutive_errors"] == 0
    assert _on_disk(tmp_path)["finra"]["ok_count"] == 1


def test_a_source_that_raises_on_its_first_chunk_is_recorded_and_the_rest_run(
    tmp_path: Path,
) -> None:
    """Adversarial b: a failure before any field is still a recorded failure."""

    symbols = [f"S{index}" for index in range(6)]
    flaky = FakeRung("flaky", fail_chunks={0})
    good = FakeRung("good")
    collector = _collector(tmp_path, [flaky, good], symbols=symbols, chunk_size=2)

    result = asyncio.run(collector.refresh())

    assert flaky.calls == [("S0", "S1"), ("S2", "S3"), ("S4", "S5")]
    assert result.errors == 1
    assert result.returned == 6, "later chunks and the other source still deliver"
    assert collector.document(symbols)["symbols"]["S2"]["pe"] == 10.0

    entry = collector.health.summary()["flaky"]
    assert entry["consecutive_errors"] == 1
    assert entry["ok"] is False
    assert entry["last_error"] is not None
    assert "RuntimeError" in entry["last_error"]
    assert _on_disk(tmp_path)["flaky"]["consecutive_errors"] == 1


def test_a_source_that_raises_on_its_last_chunk_is_recorded(tmp_path: Path) -> None:
    """Adversarial b: a late failure must not be mistaken for a healthy cycle."""

    symbols = [f"S{index}" for index in range(6)]
    flaky = FakeRung("flaky", fail_chunks={2})
    good = FakeRung("good")
    collector = _collector(tmp_path, [flaky, good], symbols=symbols, chunk_size=2)

    result = asyncio.run(collector.refresh())

    assert len(flaky.calls) == 3
    assert result.errors == 1
    entry = collector.health.summary()["flaky"]
    assert entry["consecutive_errors"] == 1
    assert entry["ok"] is False
    assert entry["last_error"] is not None
    assert "RuntimeError" in entry["last_error"]
    assert entry["last_duration_s"] is None, "a cycle with a failure is not a success"


def test_several_failed_chunks_are_one_failure_per_cycle(tmp_path: Path) -> None:
    """The streak counts cycles, not chunks; the message says how many chunks."""

    symbols = [f"S{index}" for index in range(6)]
    dead = FakeRung("dead", fail_all=True)
    collector = _collector(tmp_path, [dead], symbols=symbols, chunk_size=2)

    asyncio.run(collector.refresh())

    entry = collector.health.summary()["dead"]
    assert entry["consecutive_errors"] == 1
    assert entry["last_error"] is not None
    assert "+2 more chunk failures" in entry["last_error"]


def test_the_ledger_is_persisted_once_per_cycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One write per cycle, not one per source and not one per chunk."""

    collector = _collector(
        tmp_path, [FakeRung("sec"), FakeRung("sec_bulk"), FakeRung("finra")]
    )
    writes: list[int] = []
    real_persist = collector.health.persist

    def counting_persist() -> bool:
        writes.append(1)
        return real_persist()

    monkeypatch.setattr(collector.health, "persist", counting_persist)

    asyncio.run(collector.refresh())

    assert len(writes) == 1


def test_a_corrupt_ledger_does_not_stop_the_cycle(tmp_path: Path) -> None:
    """Adversarial c: a truncated file is an empty ledger, and the cycle heals it."""

    (tmp_path / "depth.health.json").write_text(
        '{"sec": {"ok_count": 4', encoding="utf-8"
    )
    collector = _collector(tmp_path, [FakeRung("sec")])

    result = asyncio.run(collector.refresh())

    assert result.errors == 0
    record = collector.health.get("sec")
    assert record is not None and record.ok_count == 1
    assert json.loads((tmp_path / "depth.health.json").read_text("utf-8"))["sec"][
        "ok_count"
    ] == 1


def test_a_ledger_that_cannot_be_written_does_not_stop_the_cycle(
    tmp_path: Path,
) -> None:
    """Adversarial c: an unwritable ledger path is a warning, never a failed cycle.

    A *directory* squatting on the ledger's name makes ``os.replace`` fail on a
    real filesystem, without touching the store write next to it.
    """

    (tmp_path / "depth.health.json").mkdir()
    sec = FakeRung("sec")
    finra = FakeRung("finra", fail_all=True)
    collector = _collector(tmp_path, [sec, finra])

    result = asyncio.run(collector.refresh())

    assert result.errors == 1, "only the failing source counts; the ledger does not"
    assert result.returned == 2
    record = collector.health.get("sec")
    assert record is not None and record.ok_count == 1
    assert json.loads((tmp_path / "depth.json").read_text("utf-8"))["count"] == 2


def test_the_collector_drops_ledger_entries_for_removed_sources(tmp_path: Path) -> None:
    """A rung that is no longer configured must not be reported as sick forever."""

    seed = HealthLedger(tmp_path / "depth.health.json", clock=_clock)
    seed.record_ok("sec", duration_s=1.0, fields=1)
    seed.record_error("stockanalysis", error="stockanalysis: RuntimeError: gone")
    assert seed.persist() is True

    collector = _collector(tmp_path, [FakeRung("sec")])

    assert collector.health.names() == ["sec"]


def test_a_restart_continues_the_streak_through_the_collector(tmp_path: Path) -> None:
    """Adversarial a: the first cycle after a deploy must not look healthy."""

    seed = HealthLedger(tmp_path / "depth.health.json", clock=_clock)
    seed.record_error("finra", error="finra: TimeoutError: deadline")
    assert seed.persist() is True

    collector = _collector(tmp_path, [FakeRung("finra", fail_all=True)])
    asyncio.run(collector.refresh())

    assert collector.health.summary()["finra"]["consecutive_errors"] == 2


# ---------------------------------------------------------------------------
# The stats route publishes the same view
# ---------------------------------------------------------------------------


def _app(collector: DepthCollector | None) -> FastAPI:
    application = FastAPI()
    application.include_router(market_api.router)

    @application.exception_handler(ApiError)
    async def _api_error(_request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.error, "errorMessage": exc.message},
        )

    application.state.runtime = SimpleNamespace(
        settings=SimpleNamespace(depth_coverage_floor=60)
    )
    application.state.depth = collector
    return application


def test_stats_reports_each_source_and_keeps_every_existing_key(tmp_path: Path) -> None:
    """``sources`` is additive: the seven coverage keys keep their exact values."""

    sec = FakeRung("sec")
    finra = FakeRung("finra", fail_all=True, message="429 Too Many Requests")
    collector = _collector(tmp_path, [sec, finra])
    asyncio.run(collector.refresh())

    response = TestClient(_app(collector)).get("/market/depth/stats")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["as_of"] == T0.isoformat()
    assert body["symbols"] == 2
    assert body["fields_filled"] == 1
    assert body["median_fields_per_symbol"] == 1
    assert body["min_fields"] == 1
    assert body["max_fields"] == 1
    assert body["by_source"] == {"sec": 2}
    assert set(body) == {
        "as_of",
        "symbols",
        "fields_filled",
        "median_fields_per_symbol",
        "min_fields",
        "max_fields",
        "by_source",
        "sources",
    }

    assert set(body["sources"]) == {"sec", "finra"}
    assert set(body["sources"]["sec"]) == {
        "ok",
        "last_ok",
        "last_error",
        "consecutive_errors",
        "last_duration_s",
    }
    assert body["sources"]["sec"]["ok"] is True
    assert body["sources"]["sec"]["last_error"] is None
    assert body["sources"]["sec"]["consecutive_errors"] == 0
    assert body["sources"]["sec"]["last_duration_s"] >= 0.0
    assert body["sources"]["finra"]["ok"] is False
    assert body["sources"]["finra"]["last_ok"] is None
    assert body["sources"]["finra"]["consecutive_errors"] == 1
    assert body["sources"]["finra"]["last_error"] == (
        "finra: RuntimeError: 429 Too Many Requests"
    )


def test_stats_sources_is_an_empty_object_without_a_ledger() -> None:
    """A collector built without a store path has no file, but the key still exists."""

    collector = DepthCollector(
        symbols=["AAPL"], sources=[FakeRung("sec")], store_path=None
    )
    collector.store.merge(
        DepthObservation(symbol="AAPL", fields={"pe": 1.0}, as_of=T0, source="sec"),
        priority=0,
    )

    body = TestClient(_app(collector)).get("/market/depth/stats").json()

    assert body["sources"] == {}
    assert body["fields_filled"] == 1


def test_stats_still_answers_503_when_the_collector_is_not_ready(
    tmp_path: Path,
) -> None:
    """Adversarial d: a ledger with entries must not turn 503 into a 200."""

    collector = _collector(tmp_path, [FakeRung("sec")])
    collector.health.record_error("sec", error="sec: RuntimeError: boom")

    response = TestClient(_app(collector)).get("/market/depth/stats")

    assert response.status_code == 503
    assert response.json() == {
        "error": "depth_unavailable",
        "errorMessage": "depth collector is not running or has not completed a refresh",
    }


def test_stats_answers_503_when_no_collector_is_installed() -> None:
    response = TestClient(_app(None)).get("/market/depth/stats")

    assert response.status_code == 503
    assert response.json()["error"] == "depth_unavailable"


def test_the_summary_is_sorted_and_json_safe(tmp_path: Path) -> None:
    """A stable order keeps a scraped document diffable against yesterday's."""

    ledger = HealthLedger(tmp_path / "depth.health.json", clock=_clock)
    ledger.record_ok("yahoo_chart", duration_s=0.5, fields=2)
    ledger.record_ok("sec", duration_s=0.25, fields=1)

    assert list(ledger.summary()) == ["sec", "yahoo_chart"]
    json.dumps(ledger.summary())


def test_source_health_record_declares_the_documented_fields() -> None:
    """The record is the unit of the ledger; its field set is a contract."""

    record = SourceHealth(name="sec")

    assert record.last_ok is None
    assert record.last_error is None
    assert record.ok_count == 0
    assert record.error_count == 0
    assert record.consecutive_errors == 0
    assert record.last_duration_s is None
    assert record.last_fields is None
