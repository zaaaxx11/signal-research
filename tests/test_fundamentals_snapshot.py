"""P11A RED — the fundamentals snapshot store and its ladder-fed builder.

The snapshot is the *foundation* of the fundamentals filters, not the 46-filter
catalogue: one row per symbol holding the 22 canonical fundamentals/statement
cells, the rung that served them, the data clock (``as_of``) and a 24 h expiry.
What this suite pins:

* the vocabulary is the contract's own ``Fundamentals`` + ``Statements`` groups
  (22 columns) and the production rung can serve every one of them;
* the builder walks ``Capability.FUNDAMENTALS`` through an **injected** ladder,
  asks for the whole vocabulary once, and never builds a provider itself;
* a row is written per symbol through an idempotent upsert; a rebuild replaces
  it, an *older* build cannot overwrite a newer one;
* missing cells are ``null`` — never ``0``, never invented — and a partial row
  says ``partial`` out loud;
* an all-rungs-failed build writes **nothing** and does not erase the last good
  snapshot; an empty provider answer is ``empty``, a distinct status;
* a snapshot is served for 24 h and not one second longer;
* a fresh row is reused with zero provider requests;
* twenty symbols can be built concurrently without a lock error, one row each;
* the coverage report is measured from stored rows (per-field fill, source
  histogram, fresh/expired split), not asserted.

No network: the ladder is a scripted double and the store is a temp SQLite file.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping

import pytest

from app.contracts import CANONICAL_COLUMN_GROUPS, Capability
from app.fundamentals.snapshot import (
    FUNDAMENTAL_FIELDS,
    TTL_S,
    BuildReport,
    BuildResult,
    FundamentalsSchemaError,
    FundamentalsSnapshot,
    FundamentalsSnapshotBuilder,
    SqliteFundamentalsStore,
    apply_migrations,
    canonical_symbol,
    coverage_report,
    provider_symbol,
    snapshot_from_observations,
)
from app.providers.base import Verdict
from app.providers.ladder import LadderResult

NOW = datetime(2026, 10, 24, 15, 0, tzinfo=UTC)
DEFAULT_AS_OF = "2026-10-24T00:00:00+00:00"


# ---------------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------------


class Clock:
    """A settable clock, so a 24 h TTL is a one-line assertion."""

    def __init__(self, moment: datetime = NOW) -> None:
        self.moment = moment

    def __call__(self) -> datetime:
        return self.moment

    def advance(self, **kwargs: Any) -> datetime:
        self.moment = self.moment + timedelta(**kwargs)
        return self.moment


def observation(
    instrument: Any,
    field: str,
    value: Any,
    *,
    as_of: str = DEFAULT_AS_OF,
) -> dict[str, Any]:
    """One observation document in the shape the production adapters emit."""

    numeric = isinstance(value, (int, float)) and not isinstance(value, bool)
    return {
        "instrument_id": instrument.instrument_id,
        "field": field,
        "value_num": float(value) if numeric else None,
        "value_text": value if isinstance(value, str) else None,
        "unit": "ratio",
        "source": "fake",
        "source_id": None,
        "as_of": as_of,
        "fetched_at": as_of,
        "confidence": 1.0,
        "verdict": "ok",
        "trust": 3,
        "raw_sha256": None,
        "job_id": None,
    }


class FakeLadder:
    """A scripted ladder double: records every walk, serves one dict per symbol.

    ``script[symbol]`` is either a ``{field: value}`` mapping (a served body), the
    string ``"empty"`` (a correct schema with no data), or an ``Exception``
    (a transport failure). A symbol missing from the script is ``exhausted`` —
    every rung skipped, which is what an unwired capability looks like.
    """

    def __init__(
        self,
        script: Mapping[str, Any] | None = None,
        *,
        as_of: str = DEFAULT_AS_OF,
        hook: Any | None = None,
    ) -> None:
        self.script: dict[str, Any] = dict(script or {})
        self.calls: list[dict[str, Any]] = []
        self.as_of = as_of
        self.hook = hook

    async def fetch(self, capability: Capability, instrument: Any, fields: set[str]) -> LadderResult:
        self.calls.append(
            {"capability": capability, "instrument": instrument, "fields": set(fields)}
        )
        if self.hook is not None:
            await self.hook(instrument.symbol)
        plan = self.script.get(instrument.symbol, "exhausted")
        if isinstance(plan, BaseException):
            raise plan
        if plan == "exhausted":
            return LadderResult(
                verdict=Verdict(False, "exhausted", "every rung was skipped"),
                attempts=[
                    {
                        "rung": "L1",
                        "provider": "sec_edgar",
                        "capability": capability.value,
                        "verdict": "skipped",
                        "attempts": 0,
                        "requests": 0,
                        "bytes": 0,
                    }
                ],
            )
        if plan == "empty":
            return LadderResult(
                verdict=Verdict(False, "empty", "quoteSummary.result is null"),
                attempts=[
                    {
                        "rung": "L2",
                        "provider": "yahoo",
                        "capability": capability.value,
                        "verdict": "empty",
                        "attempts": 1,
                        "requests": 1,
                        "bytes": 40,
                    }
                ],
            )
        return LadderResult(
            observations=[observation(instrument, name, value, as_of=self.as_of) for name, value in plan.items()],
            rung="fake",
            verdict=Verdict(True, "ok"),
            attempts=[
                {
                    "rung": "L2",
                    "provider": "fake",
                    "capability": capability.value,
                    "verdict": "ok",
                    "attempts": 1,
                    "requests": 1,
                    "bytes": 900,
                }
            ],
        )


def full_fields(**overrides: Any) -> dict[str, Any]:
    """Every vocabulary field filled with a distinct, obviously synthetic value."""

    values: dict[str, Any] = {
        field: float(index + 1) * 1.5 for index, field in enumerate(FUNDAMENTAL_FIELDS)
    }
    values.update({"pe_ttm": 28.5, "market_cap": 3.4e12})
    values.update(overrides)
    return values


def partial_fields() -> dict[str, Any]:
    """Three served cells out of twenty-two: a ``partial`` snapshot."""

    return {"market_cap": 3.4e12, "pe_ttm": 28.5, "eps_ttm": 6.1}


async def store_for(tmp_path: Path) -> SqliteFundamentalsStore:
    return SqliteFundamentalsStore(tmp_path / "fundamentals.db")


# ---------------------------------------------------------------------------
# The vocabulary is the contract's, not a second list
# ---------------------------------------------------------------------------


def test_vocabulary_is_the_22_canonical_fundamental_columns() -> None:
    groups = dict(CANONICAL_COLUMN_GROUPS)
    assert FUNDAMENTAL_FIELDS == groups["Fundamentals"] + groups["Statements"]
    assert len(FUNDAMENTAL_FIELDS) == 22
    assert len(set(FUNDAMENTAL_FIELDS)) == 22


def test_the_production_fundamentals_rung_can_serve_the_vocabulary() -> None:
    from app.providers.adapters.yahoo import FUNDAMENTAL_SOURCES

    assert set(FUNDAMENTAL_SOURCES) == set(FUNDAMENTAL_FIELDS)


def test_the_ttl_is_twenty_four_hours() -> None:
    assert TTL_S == 24 * 3600


def test_provider_symbol_maps_us_class_shares_like_the_screen_engine() -> None:
    from app.screen.deep import _provider_ticker

    assert canonical_symbol(" brk.b ") == "BRK.B"
    assert provider_symbol("brk.b") == "BRK-B"
    for symbol in ("AAPL", "BRK.B", "BF.B", "2330.TW", "BRK", "IWM"):
        assert provider_symbol(symbol) == _provider_ticker(symbol)


def test_a_blank_symbol_is_a_caller_error() -> None:
    with pytest.raises(ValueError):
        canonical_symbol("   ")


def test_a_snapshot_refuses_a_field_outside_the_vocabulary() -> None:
    with pytest.raises(ValueError):
        FundamentalsSnapshot(
            symbol="AAPL",
            instrument_id="XNAS:AAPL",
            fields={"quote_as_of": "2026-10-24"},
            source="fake",
            as_of=NOW,
            fetched_at=NOW,
            expires_at=NOW + timedelta(seconds=TTL_S),
        )


# ---------------------------------------------------------------------------
# Build: the ladder is injected, the vocabulary is asked for once
# ---------------------------------------------------------------------------


async def test_builder_walks_the_fundamentals_capability_with_the_vocabulary() -> None:
    ladder = FakeLadder({"AAPL": full_fields()})
    builder = FundamentalsSnapshotBuilder(ladder=ladder, clock=Clock())

    result = await builder.build("aapl")

    assert len(ladder.calls) == 1
    walk = ladder.calls[0]
    assert walk["capability"] is Capability.FUNDAMENTALS
    assert walk["fields"] == set(FUNDAMENTAL_FIELDS)
    assert walk["instrument"].symbol == "AAPL"
    assert walk["instrument"].mic == "XNAS"
    assert result.symbol == "AAPL"
    assert result.status == "complete"
    assert result.written is False, "no store was injected: the builder is pure"
    assert result.snapshot is not None and result.snapshot.complete


async def test_builder_maps_a_us_class_share_to_the_provider_ticker() -> None:
    ladder = FakeLadder({"BRK-B": full_fields()})
    builder = FundamentalsSnapshotBuilder(ladder=ladder, clock=Clock())

    result = await builder.build("BRK.B")

    assert ladder.calls[0]["instrument"].symbol == "BRK-B"
    assert result.symbol == "BRK.B", "the stored/displayed symbol keeps the caller's spelling"
    assert result.instrument_id == "XNAS:BRK-B"


# ---------------------------------------------------------------------------
# Store: one row per symbol, JSON fields, nulls are null
# ---------------------------------------------------------------------------


async def test_build_writes_one_row_per_symbol_with_json_fields(tmp_path: Path) -> None:
    store = await store_for(tmp_path)
    clock = Clock()
    ladder = FakeLadder({"AAPL": full_fields(), "MSFT": partial_fields()})
    builder = FundamentalsSnapshotBuilder(ladder=ladder, store=store, clock=clock)

    results = await builder.build_many(["AAPL", "MSFT"])

    assert [item.status for item in results.results] == ["complete", "partial"]
    assert [item.written for item in results.results] == [True, True]

    aapl = await store.get("AAPL", now=clock())
    assert isinstance(aapl, FundamentalsSnapshot)
    assert aapl.source == "fake"
    assert aapl.field_count == 22
    assert aapl.fields["pe_ttm"] == pytest.approx(28.5)
    assert aapl.expires_at == NOW + timedelta(seconds=TTL_S)

    with closing(sqlite3.connect(tmp_path / "fundamentals.db")) as connection:
        rows = connection.execute(
            "SELECT symbol, fields, source, as_of, expires_at FROM fundamentals_snapshots ORDER BY symbol"
        ).fetchall()
    assert [row[0] for row in rows] == ["AAPL", "MSFT"], "one row per symbol"
    parsed = json.loads(rows[1][1])
    assert parsed["revenue_ttm"] is None, "an unserved cell is null, never 0"
    assert parsed["pe_ttm"] == pytest.approx(28.5)
    assert rows[1][2] == "fake"
    await store.aclose()


async def test_as_of_is_the_observation_clock_and_the_ttl_starts_at_the_build(
    tmp_path: Path,
) -> None:
    store = await store_for(tmp_path)
    clock = Clock()
    ladder = FakeLadder({"AAPL": full_fields()}, as_of="2026-10-23T20:00:00+00:00")
    builder = FundamentalsSnapshotBuilder(ladder=ladder, store=store, clock=clock)

    result = await builder.build("AAPL")

    assert result.snapshot is not None
    assert result.snapshot.as_of == datetime(2026, 10, 23, 20, 0, tzinfo=UTC)
    assert result.snapshot.fetched_at == NOW
    assert result.snapshot.expires_at == NOW + timedelta(seconds=TTL_S), (
        "the window starts when the row was fetched, not when the provider's data is dated"
    )
    await store.aclose()


def test_as_of_is_the_newest_cell_clock_and_never_a_provider_omission() -> None:
    """A missing stamp must not borrow the fetch clock and make old data look new."""

    from app.providers.base import InstrumentRef

    instrument = InstrumentRef.from_symbol("AAPL")
    common = {
        "symbol": "AAPL",
        "instrument_id": "XNAS:AAPL",
        "source": "fake",
        "fetched_at": NOW,
    }

    mixed = snapshot_from_observations(
        [
            observation(instrument, "pe_ttm", 28.5, as_of="2026-10-20T00:00:00+00:00"),
            {**observation(instrument, "pb", 40.0), "as_of": None},
            observation(instrument, "ps", 8.1, as_of="2026-10-23T20:03:16+00:00"),
        ],
        **common,
    )
    assert mixed.as_of == datetime(2026, 10, 23, 20, 3, 16, tzinfo=UTC), "newest stamp wins"

    undated = snapshot_from_observations(
        [{**observation(instrument, "pe_ttm", 28.5), "as_of": ""}], **common
    )
    assert undated.as_of == NOW, "no dated cell at all -> the fetch clock, not epoch"
    assert undated.field_count == 1


async def test_get_of_an_unknown_symbol_is_none_not_an_error(tmp_path: Path) -> None:
    store = await store_for(tmp_path)

    assert await store.get("NOPE", now=NOW) is None
    assert await store.count() == 0
    assert (await store.coverage(now=NOW))["symbols"] == 0
    await store.aclose()


async def test_partial_fill_keeps_nulls_and_says_partial(tmp_path: Path) -> None:
    store = await store_for(tmp_path)
    ladder = FakeLadder({"AAPL": partial_fields()})
    builder = FundamentalsSnapshotBuilder(ladder=ladder, store=store, clock=Clock())

    result = await builder.build("AAPL")

    assert result.status == "partial"
    assert result.filled == 3
    snapshot = await store.get("AAPL", now=NOW)
    assert snapshot is not None
    assert set(snapshot.fields) == set(FUNDAMENTAL_FIELDS), "the row is full width"
    assert snapshot.field_count == 3
    assert set(snapshot.missing_fields) == set(FUNDAMENTAL_FIELDS) - set(partial_fields())
    assert snapshot.complete is False
    await store.aclose()


async def test_only_fundamental_fields_reach_the_snapshot(tmp_path: Path) -> None:
    store = await store_for(tmp_path)
    ladder = FakeLadder(
        {
            "AAPL": {
                **partial_fields(),
                "quote_as_of": "2026-10-24T00:00:00+00:00",
                "ma_20": 190.5,
                "pe_ttm": float("nan"),
            }
        }
    )
    builder = FundamentalsSnapshotBuilder(ladder=ladder, store=store, clock=Clock())

    result = await builder.build("AAPL")

    snapshot = result.snapshot
    assert snapshot is not None
    assert "quote_as_of" not in snapshot.fields and "ma_20" not in snapshot.fields
    assert snapshot.fields["pe_ttm"] is None, "a non-finite number is absent, never stored"
    assert result.filled == 2
    await store.aclose()


# ---------------------------------------------------------------------------
# TTL
# ---------------------------------------------------------------------------


async def test_a_snapshot_is_served_for_24h_and_not_one_second_longer(tmp_path: Path) -> None:
    store = await store_for(tmp_path)
    clock = Clock()
    builder = FundamentalsSnapshotBuilder(
        ladder=FakeLadder({"AAPL": full_fields()}), store=store, clock=clock
    )
    await builder.build("AAPL")

    clock.advance(hours=23, minutes=59)
    fresh = await store.get("AAPL", now=clock())
    assert fresh is not None and fresh.is_fresh(clock())

    clock.advance(seconds=61)
    assert await store.get("AAPL", now=clock()) is None
    expired = await store.get("AAPL", now=clock(), include_expired=True)
    assert expired is not None and expired.is_stale(clock())

    report = await store.coverage(now=clock())
    assert report["fresh"] == 0 and report["expired"] == 1
    assert report["filled_cells"] == 0, "an expired row is not coverage"
    await store.aclose()


async def test_purge_expired_removes_only_the_dead_rows(tmp_path: Path) -> None:
    store = await store_for(tmp_path)
    clock = Clock()
    builder = FundamentalsSnapshotBuilder(
        ladder=FakeLadder({"AAPL": full_fields(), "MSFT": full_fields()}),
        store=store,
        clock=clock,
    )
    await builder.build("AAPL")
    clock.advance(hours=12)
    await builder.build("MSFT")
    clock.advance(hours=13)

    assert await store.purge_expired(now=clock()) == 1
    assert await store.get("AAPL", now=clock(), include_expired=True) is None
    assert await store.get("MSFT", now=clock()) is not None
    await store.aclose()


# ---------------------------------------------------------------------------
# Upsert: idempotent, newest wins, empty never overwrites good
# ---------------------------------------------------------------------------


async def test_rebuilding_a_symbol_upserts_one_row(tmp_path: Path) -> None:
    store = await store_for(tmp_path)
    clock = Clock()
    ladder = FakeLadder({"AAPL": full_fields()})
    builder = FundamentalsSnapshotBuilder(ladder=ladder, store=store, clock=clock)

    first = await builder.build("AAPL")
    clock.advance(hours=1)
    ladder.script["AAPL"] = full_fields(pe_ttm=31.25, market_cap=3.6e12)
    second = await builder.build("AAPL")

    assert first.written is True and second.written is True
    assert await store.count() == 1, "an upsert, not an append"
    row = await store.get("AAPL", now=clock())
    assert row is not None
    assert row.fields["pe_ttm"] == pytest.approx(31.25)
    assert row.expires_at == clock() + timedelta(seconds=TTL_S)
    await store.aclose()


async def test_an_older_build_cannot_overwrite_a_newer_row(tmp_path: Path) -> None:
    store = await store_for(tmp_path)
    clock = Clock()
    ladder = FakeLadder({"AAPL": full_fields(pe_ttm=40.0)})
    builder = FundamentalsSnapshotBuilder(ladder=ladder, store=store, clock=clock)

    clock.advance(hours=2)
    await builder.build("AAPL")
    newer_expiry = (await store.get("AAPL", now=clock())).expires_at  # type: ignore[union-attr]

    clock.moment = NOW
    ladder.script["AAPL"] = full_fields(pe_ttm=11.0)
    stale = await builder.build("AAPL")

    row = await store.get("AAPL", now=clock(), include_expired=True)
    assert row is not None
    assert row.fields["pe_ttm"] == pytest.approx(40.0), "the newer row survived"
    assert row.expires_at == newer_expiry
    assert stale.written is False
    await store.aclose()


async def test_all_rungs_failing_writes_no_row(tmp_path: Path) -> None:
    store = await store_for(tmp_path)
    builder = FundamentalsSnapshotBuilder(
        ladder=FakeLadder({}), store=store, clock=Clock()
    )

    result = await builder.build("AAPL")

    assert isinstance(result, BuildResult)
    assert result.status == "failed"
    assert result.written is False
    assert result.snapshot is None
    assert result.verdict == "exhausted"
    assert result.reason and "exhausted" in result.reason
    assert await store.count() == 0, "no data is not a row"
    await store.aclose()


async def test_an_empty_provider_answer_is_empty_not_failed(tmp_path: Path) -> None:
    store = await store_for(tmp_path)
    builder = FundamentalsSnapshotBuilder(
        ladder=FakeLadder({"AAPL": "empty"}), store=store, clock=Clock()
    )

    result = await builder.build("AAPL")

    assert result.status == "empty"
    assert result.written is False and result.snapshot is None
    assert result.verdict == "empty"
    assert await store.count() == 0
    await store.aclose()


async def test_a_raising_ladder_is_a_failed_symbol_not_a_crashed_run(tmp_path: Path) -> None:
    store = await store_for(tmp_path)
    builder = FundamentalsSnapshotBuilder(
        ladder=FakeLadder({"AAPL": RuntimeError("boom")}), store=store, clock=Clock()
    )

    result = await builder.build("AAPL")

    assert result.status == "failed"
    assert result.reason and "boom" in result.reason
    assert await store.count() == 0
    await store.aclose()


async def test_a_failed_rebuild_does_not_erase_the_last_good_snapshot(tmp_path: Path) -> None:
    store = await store_for(tmp_path)
    clock = Clock()
    ladder = FakeLadder({"AAPL": full_fields()})
    builder = FundamentalsSnapshotBuilder(ladder=ladder, store=store, clock=clock)
    await builder.build("AAPL")
    good = await store.get("AAPL", now=clock())
    assert good is not None

    clock.advance(hours=1)
    ladder.script["AAPL"] = "exhausted"
    failed = await builder.build("AAPL")

    assert failed.status == "failed"
    kept = await store.get("AAPL", now=clock())
    assert kept is not None
    assert kept.fetched_at == good.fetched_at
    assert kept.fields["pe_ttm"] == pytest.approx(28.5)
    await store.aclose()


async def test_a_fresh_row_is_reused_with_zero_provider_requests(tmp_path: Path) -> None:
    store = await store_for(tmp_path)
    ladder = FakeLadder({"AAPL": full_fields(), "MSFT": partial_fields()})
    builder = FundamentalsSnapshotBuilder(ladder=ladder, store=store, clock=Clock())

    await builder.build_many(["AAPL", "MSFT"])
    assert len(ladder.calls) == 2

    again = await builder.build_many(["AAPL", "MSFT"], skip_fresh=True)

    assert len(ladder.calls) == 2, "a fresh row inside its 24 h window costs nothing"
    assert [item.status for item in again.results] == ["cached", "cached"]
    assert again.requests == 0

    refreshed = await builder.build_many(["AAPL"], skip_fresh=False)
    assert len(ladder.calls) == 3
    assert refreshed.results[0].status in {"complete", "partial"}
    await store.aclose()


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------


async def test_concurrent_builds_of_one_symbol_leave_exactly_one_row(tmp_path: Path) -> None:
    store = await store_for(tmp_path)
    clock = Clock()
    ladder = FakeLadder({})
    builder = FundamentalsSnapshotBuilder(ladder=ladder, store=store, clock=clock)

    async def build_one(index: int) -> BuildResult:
        ladder.script["AAPL"] = full_fields(pe_ttm=10.0 + index)
        return await builder.build("AAPL")

    results = await asyncio.gather(*(build_one(index) for index in range(8)))

    assert all(item.status == "complete" for item in results)
    assert await store.count() == 1
    row = await store.get("AAPL", now=clock())
    assert row is not None
    assert row.fields["pe_ttm"] in {10.0 + index for index in range(8)}
    await store.aclose()


async def test_build_many_bounds_concurrency_and_covers_every_symbol(tmp_path: Path) -> None:
    symbols = [f"SYM{index:02d}" for index in range(20)]
    state = {"in_flight": 0, "peak": 0}

    async def hook(_symbol: str) -> None:
        state["in_flight"] += 1
        state["peak"] = max(state["peak"], state["in_flight"])
        await asyncio.sleep(0.005)
        state["in_flight"] -= 1

    ladder = FakeLadder({symbol: full_fields() for symbol in symbols}, hook=hook)
    store = await store_for(tmp_path)
    builder = FundamentalsSnapshotBuilder(ladder=ladder, store=store, clock=Clock())

    report = await builder.build_many(symbols, concurrency=4)

    assert isinstance(report, BuildReport)
    assert report.requested == 20 and report.complete == 20
    assert state["peak"] <= 4, "the semaphore bounds the fan-out"
    assert await store.count() == 20
    assert [item.symbol for item in report.results] == symbols, "input order survives"
    await store.aclose()


async def test_build_many_dedupes_and_canonicalises(tmp_path: Path) -> None:
    ladder = FakeLadder({"AAPL": full_fields(), "MSFT": full_fields()})
    builder = FundamentalsSnapshotBuilder(ladder=ladder, clock=Clock())

    report = await builder.build_many(["aapl", "AAPL", " msft ", ""])

    assert [item.symbol for item in report.results] == ["AAPL", "MSFT"]
    assert len(ladder.calls) == 2
    assert report.requested == 2
    assert report.dropped == ("",), "a blank line in a symbol list is reported, not guessed at"


async def test_build_many_of_nothing_is_an_empty_report() -> None:
    report = await FundamentalsSnapshotBuilder(
        ladder=FakeLadder({}), clock=Clock()
    ).build_many([])

    assert report.requested == 0
    assert report.results == ()
    assert report.as_dict()["requested"] == 0


# ---------------------------------------------------------------------------
# Coverage: measured, per field, with the source histogram
# ---------------------------------------------------------------------------


async def test_coverage_reports_measured_field_fill_not_a_claim(tmp_path: Path) -> None:
    store = await store_for(tmp_path)
    clock = Clock()
    ladder = FakeLadder(
        {
            "AAPL": full_fields(),
            "MSFT": {"pe_ttm": 28.5},
            "GOOGL": "exhausted",
        }
    )
    builder = FundamentalsSnapshotBuilder(ladder=ladder, store=store, clock=clock)
    await builder.build_many(["AAPL", "MSFT", "GOOGL"])

    report = await store.coverage(now=clock())

    assert report["symbols"] == 2, "the failed symbol has no row and is not counted"
    assert report["fresh"] == 2 and report["expired"] == 0
    assert report["complete"] == 1 and report["partial"] == 1 and report["empty"] == 0
    assert report["filled_cells"] == 22 + 1
    assert report["total_cells"] == 2 * 22
    assert report["fill_rate"] == pytest.approx(23 / 44)
    assert report["sources"] == {"fake": 2}
    assert report["ttl_s"] == TTL_S

    by_field = {row["field"]: row for row in report["fields"]}
    assert list(by_field) == list(FUNDAMENTAL_FIELDS), "declared order, so a diff is readable"
    assert by_field["pe_ttm"] == {"field": "pe_ttm", "filled": 2, "total": 2, "coverage": 1.0}
    assert by_field["revenue_ttm"]["filled"] == 1
    assert by_field["revenue_ttm"]["coverage"] == pytest.approx(0.5)
    assert by_field["revenue_ttm"]["total"] == 2
    await store.aclose()


def test_coverage_of_no_rows_is_zero_and_not_an_error() -> None:
    report = coverage_report([], now=NOW)

    assert report["symbols"] == 0 and report["fresh"] == 0
    assert report["fill_rate"] == 0.0
    assert report["fields"] and all(row["filled"] == 0 for row in report["fields"])
    assert report["sources"] == {}
    assert report["generated_at"] == NOW.isoformat(timespec="microseconds")


async def test_build_report_separates_every_status(tmp_path: Path) -> None:
    store = await store_for(tmp_path)
    ladder = FakeLadder(
        {
            "AAPL": full_fields(),
            "MSFT": partial_fields(),
            "GOOGL": "empty",
            "AMZN": "exhausted",
        }
    )
    builder = FundamentalsSnapshotBuilder(ladder=ladder, store=store, clock=Clock())

    report = await builder.build_many(["AAPL", "MSFT", "GOOGL", "AMZN"])
    document = report.as_dict()

    assert document["requested"] == 4
    assert document["complete"] == 1 and document["partial"] == 1
    assert document["empty"] == 1 and document["failed"] == 1
    assert document["built"] == 2 and document["written"] == 2
    assert document["cached"] == 0
    assert document["requests"] == 3, "the empty answer was a real request; the exhausted walk sent none"
    assert [row["status"] for row in document["results"]] == [
        "complete",
        "partial",
        "empty",
        "failed",
    ]
    assert document["results"][3]["reason"]
    await store.aclose()


async def test_migration_0005_is_the_head_and_the_table_exists(tmp_path: Path) -> None:
    store = await store_for(tmp_path)
    await store.ensure_ready()  # bootstraps a fresh file: one migration path only

    with closing(sqlite3.connect(tmp_path / "fundamentals.db")) as connection:
        head = connection.execute("SELECT version_num FROM alembic_version").fetchall()
        columns = {
            row[1]: row[2]
            for row in connection.execute("PRAGMA table_info(fundamentals_snapshots)").fetchall()
        }
        indexes = {
            row[1] for row in connection.execute("PRAGMA index_list(fundamentals_snapshots)").fetchall()
        }
    assert [row[0] for row in head] == ["0005"]
    assert set(columns) == {
        "symbol",
        "instrument_id",
        "fields",
        "field_sources",
        "source",
        "as_of",
        "fetched_at",
        "expires_at",
        "field_count",
        "complete",
        "updated_at",
    }
    assert columns["fields"] == "TEXT"
    assert columns["field_sources"] == "TEXT"
    assert "ix_fundamentals_expiry" in indexes
    await store.aclose()


async def test_an_existing_database_at_0002_is_refused_with_the_migration_command(
    tmp_path: Path,
) -> None:
    """The live database is at 0002: the store must say so, not fail on a write."""

    from alembic import command

    from app.db import alembic_config

    db = tmp_path / "existing.db"
    command.upgrade(alembic_config(db), "0002")
    store = SqliteFundamentalsStore(db)

    with pytest.raises(FundamentalsSchemaError) as error:
        await store.count()
    assert "fundamentals_snapshots" in str(error.value)
    assert "upgrade head" in str(error.value)

    builder = FundamentalsSnapshotBuilder(
        ladder=FakeLadder({"AAPL": full_fields()}), store=store, clock=Clock()
    )
    with pytest.raises(FundamentalsSchemaError):
        await builder.build("AAPL")
    await store.aclose()


async def test_apply_migrations_brings_an_existing_database_to_head(tmp_path: Path) -> None:
    from alembic import command

    from app.db import alembic_config

    db = tmp_path / "existing.db"
    command.upgrade(alembic_config(db), "0002")

    assert apply_migrations(db) == "0005"

    store = SqliteFundamentalsStore(db)
    builder = FundamentalsSnapshotBuilder(
        ladder=FakeLadder({"AAPL": full_fields()}), store=store, clock=Clock()
    )
    result = await builder.build("AAPL")

    assert result.status == "complete" and result.written is True
    assert await store.count() == 1
    with closing(sqlite3.connect(db)) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchall() == [
            ("0005",)
        ]
    await store.aclose()
