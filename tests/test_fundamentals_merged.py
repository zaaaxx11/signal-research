"""P11F-C RED — merge SEC + Yahoo + Finviz into the row the store already holds.

P11F-A gave a row per-cell provenance and P11F-B gave it three more sources; this
suite pins the piece that puts them together: one row per symbol, four sources,
each cell owned by the source that supplied it.

What this suite pins:

* the stored row is the **base**: a fresh row keeps the cells it already filled
  (the screen's numbers do not change because a merge ran) and only holes are
  filled; an *expired* row is still read but ranks last, so stale data may fill
  a hole and may never beat a live source;
* the live sources are merged in trust order — SEC, then Yahoo, then Finviz —
  through ``merge_snapshots``, so a shared cell goes to the higher rung and the
  lower ones fill what is left;
* ``field_sources`` is per cell (the row says ``multi`` *and* which number came
  from where) and round-trips through the store;
* a source that raises, is refused (``throttled``/``blocked``) or answers with
  no cell is a **status**, not a crash — a refused body is never parsed, an
  unwired adapter is ``skipped`` rather than ``failed``, and the other sources
  still merge;
* nothing served means **no row written**: no row of nulls, no ``0`` for an
  absent cell, and the stored row (if any) is left exactly as it was;
* a merge that says exactly what the row already said is ``kept`` and not
  rewritten, so the 24 h clock only moves when the answer moved;
* ``skip_fresh`` answers from a fresh row with zero requests; ``--refresh``
  re-merges and still keeps the base's cells;
* the batch report counts ``before``/``added``/``filled`` and the **source mix**
  of the rows it wrote, and the CLI's ``--source merged`` prints them.

No network: the adapters are scripted doubles and the store is a temp SQLite
file.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping

import pytest

from app.contracts import Capability
from app.fundamentals.merged import (
    KEPT,
    MERGED_SOURCES,
    MergedFundamentalsBuilder,
    MergedReport,
    MergedResult,
    SourceAttempt,
    build_merged_snapshots,
    merge_priority,
)
from app.fundamentals.provenance import DEFAULT_PRIORITY
from app.fundamentals.snapshot import (
    CACHED,
    COMPLETE,
    EMPTY,
    FAILED,
    FUNDAMENTAL_FIELDS,
    MULTI_SOURCE,
    PARTIAL,
    TTL_S,
    FundamentalsSnapshot,
    SqliteFundamentalsStore,
    canonical_symbol,
    instrument_for,
)
from app.providers.base import InstrumentRef, Verdict

NOW = datetime(2026, 10, 24, 15, 0, tzinfo=UTC)
AS_OF = "2026-10-24T00:00:00+00:00"

CLI_PATH = Path(__file__).resolve().parents[1] / "scripts" / "fundamentals-snapshot.py"


def _load_cli() -> Any:
    """Import ``scripts/fundamentals-snapshot.py`` without making it a package."""

    spec = importlib.util.spec_from_file_location("fundamentals_snapshot_cli_p11fc", CLI_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


def observation(instrument: InstrumentRef, field: str, value: Any, *, source: str) -> dict[str, Any]:
    """One observation in the shape the production adapters emit."""

    numeric = isinstance(value, (int, float)) and not isinstance(value, bool)
    return {
        "instrument_id": instrument.instrument_id,
        "field": field,
        "value_num": float(value) if numeric else None,
        "value_text": value if isinstance(value, str) else None,
        "unit": "ratio",
        "source": source,
        "as_of": AS_OF,
        "verdict": "ok",
    }


class FakeAdapter:
    """A scripted provider adapter: cells per symbol, a verdict, or an exception.

    ``per_symbol`` wins over ``cells`` when both are given, so one double can
    serve two symbols differently (the batch tests need one symbol to fail).
    ``error``/``verdict`` may also be a per-symbol mapping.
    """

    def __init__(
        self,
        name: str,
        cells: Mapping[str, Any] | None = None,
        *,
        per_symbol: Mapping[str, Mapping[str, Any]] | None = None,
        verdict: Any | None = None,
        error: Any | None = None,
    ) -> None:
        self.name = name
        self.cells = dict(cells or {})
        self.per_symbol = {key: dict(value) for key, value in (per_symbol or {}).items()} or None
        self._verdict = verdict
        self.error = error
        self.fetches: list[dict[str, Any]] = []
        self.parses = 0

    # -- the adapter contract ---------------------------------------------

    def _cells_for(self, instrument: InstrumentRef) -> dict[str, Any]:
        if self.per_symbol is not None:
            return dict(self.per_symbol.get(instrument.symbol, {}))
        return dict(self.cells)

    def _for_symbol(self, value: Any, instrument: InstrumentRef) -> Any:
        if isinstance(value, Mapping):
            return value.get(instrument.symbol)
        return value

    async def fetch(
        self, capability: Capability, instrument: InstrumentRef, fields: set[str]
    ) -> Mapping[str, Any]:
        self.fetches.append(
            {"capability": capability, "instrument": instrument, "fields": set(fields)}
        )
        failure = self._for_symbol(self.error, instrument)
        if failure is not None:
            raise failure
        return {
            "status": 200,
            "headers": {"content-type": "application/json"},
            "body": b"{}",
            "url": f"fake://{self.name}/{instrument.symbol}",
            "capability": capability.value,
        }

    def verdict(self, raw: Mapping[str, Any], instrument: InstrumentRef) -> Verdict:
        answer = self._for_symbol(self._verdict, instrument)
        if answer is not None:
            return answer
        return Verdict(True, "ok")

    def parse(
        self, raw: Mapping[str, Any], instrument: InstrumentRef
    ) -> list[dict[str, Any]]:
        self.parses += 1
        return [
            observation(instrument, field, value, source=self.name)
            for field, value in self._cells_for(instrument).items()
        ]


def adapters(
    sec: Mapping[str, Any] | None = None,
    yahoo: Mapping[str, Any] | None = None,
    finviz: Mapping[str, Any] | None = None,
    **kwargs: Any,
) -> dict[str, FakeAdapter]:
    """The three live sources, each with its scripted cells."""

    return {
        "sec_edgar": FakeAdapter("sec_edgar", sec, **kwargs),
        "yahoo": FakeAdapter("yahoo", yahoo, **kwargs),
        "finviz": FakeAdapter("finviz", finviz, **kwargs),
    }


async def store_for(tmp_path: Path) -> SqliteFundamentalsStore:
    return SqliteFundamentalsStore(tmp_path / "merged.db")


def snapshot(
    symbol: str,
    fields: Mapping[str, Any],
    *,
    source: str | None = "tradingview",
    fetched_at: datetime = NOW - timedelta(hours=2),
    as_of: datetime | None = None,
    ttl_s: int = TTL_S,
    field_sources: Mapping[str, str | None] | None = None,
) -> FundamentalsSnapshot:
    """A stored row as P11D/P11A would have written it."""

    moment = as_of or fetched_at
    return FundamentalsSnapshot(
        symbol=symbol,
        instrument_id=instrument_for(symbol).instrument_id,
        fields=dict(fields),
        source=source,
        field_sources=dict(field_sources or {}),
        as_of=moment,
        fetched_at=fetched_at,
        expires_at=fetched_at + timedelta(seconds=int(ttl_s)),
    )


# ---------------------------------------------------------------------------
# The sources and the trust order
# ---------------------------------------------------------------------------


def test_the_merged_mode_fetches_three_sources_and_not_the_stored_one() -> None:
    assert MERGED_SOURCES == ("sec_edgar", "yahoo", "finviz")
    assert MERGED_SOURCES == DEFAULT_PRIORITY[:-1], (
        "the live sources are the trust order minus tradingview: the stored row "
        "is the base, not a rung to fetch"
    )
    assert "tradingview" not in MERGED_SOURCES


async def test_sec_wins_a_shared_cell_and_the_lower_rungs_fill_the_holes(
    tmp_path: Path,
) -> None:
    store = await store_for(tmp_path)
    builder = MergedFundamentalsBuilder(
        adapters(
            sec={"revenue_ttm": 100.0, "pe_ttm": 10.0},
            yahoo={"revenue_ttm": 200.0, "pe_ttm": 20.0, "ps": 8.0},
            finviz={"revenue_ttm": 300.0, "pe_ttm": 30.0, "ps": 9.0, "peg": 1.5},
        ),
        store=store,
        clock=Clock(),
    )

    result = await builder.build("AAPL")

    assert result.status == PARTIAL
    assert result.written is True
    assert result.snapshot is not None
    assert result.snapshot.fields["revenue_ttm"] == 100.0
    assert result.snapshot.fields["pe_ttm"] == 10.0
    assert result.snapshot.fields["ps"] == 8.0, "yahoo outranks finviz"
    assert result.snapshot.fields["peg"] == 1.5, "a hole only finviz serves is filled"
    assert result.filled == 4
    assert result.requests == 3


async def test_the_row_is_multi_and_every_cell_names_its_own_source(tmp_path: Path) -> None:
    store = await store_for(tmp_path)
    builder = MergedFundamentalsBuilder(
        adapters(
            sec={"revenue_ttm": 100.0},
            yahoo={"pe_ttm": 20.0},
            finviz={"peg": 1.5},
        ),
        store=store,
        clock=Clock(),
    )

    result = await builder.build("AAPL")
    row = result.snapshot
    assert row is not None
    assert row.source == MULTI_SOURCE, "more than one source contributed"
    assert row.field_sources["revenue_ttm"] == "sec_edgar"
    assert row.field_sources["pe_ttm"] == "yahoo"
    assert row.field_sources["peg"] == "finviz"
    # Full width: 22 keys, one per vocabulary cell, None where nothing was served.
    assert set(row.field_sources) == set(FUNDAMENTAL_FIELDS)
    assert {name for name, label in row.field_sources.items() if label} == {
        "revenue_ttm",
        "pe_ttm",
        "peg",
    }
    assert row.fields["ps"] is None, "an unserved cell is null, never 0"
    assert result.cells_by_source == {"finviz": 1, "sec_edgar": 1, "yahoo": 1}
    assert result.cell_sources == {
        "revenue_ttm": "sec_edgar",
        "pe_ttm": "yahoo",
        "peg": "finviz",
    }


# ---------------------------------------------------------------------------
# A source can fail, refuse or be absent
# ---------------------------------------------------------------------------


async def test_a_raising_source_is_a_status_not_a_crashed_symbol(tmp_path: Path) -> None:
    store = await store_for(tmp_path)
    builder = MergedFundamentalsBuilder(
        adapters(
            sec={"revenue_ttm": 1.0},
            yahoo={},
            finviz={"pe_ttm": 5.0},
            error=None,
        ),
        store=store,
        clock=Clock(),
    )
    builder.adapters["yahoo"] = FakeAdapter("yahoo", error=RuntimeError("connection reset"))

    result = await builder.build("AAPL")

    yahoo = next(item for item in result.sources if item.source == "yahoo")
    assert yahoo.status == FAILED
    assert yahoo.reason is not None and "RuntimeError: connection reset" in yahoo.reason
    assert yahoo.requests == 1, "a failed fetch still cost a request"
    assert result.snapshot is not None
    assert result.snapshot.fields["pe_ttm"] == 5.0, "the other sources still merged"
    assert result.filled == 2
    assert result.requests == 3


async def test_a_refused_answer_is_never_parsed(tmp_path: Path) -> None:
    store = await store_for(tmp_path)
    yahoo = FakeAdapter(
        "yahoo",
        {"pe_ttm": 20.0},
        verdict=Verdict(False, "throttled", "status 429"),
    )
    builder = MergedFundamentalsBuilder(
        {"sec_edgar": FakeAdapter("sec_edgar", {"revenue_ttm": 1.0}), "yahoo": yahoo},
        store=store,
        clock=Clock(),
    )

    result = await builder.build("AAPL")

    attempt = next(item for item in result.sources if item.source == "yahoo")
    assert attempt.status == FAILED
    assert attempt.verdict == "throttled"
    assert yahoo.parses == 0, "a refusal is not a smaller answer: it is no answer"
    assert result.snapshot is not None and result.snapshot.fields.get("pe_ttm") is None


async def test_a_symbol_the_source_does_not_know_is_empty_not_failed(tmp_path: Path) -> None:
    store = await store_for(tmp_path)
    builder = MergedFundamentalsBuilder(
        {
            "sec_edgar": FakeAdapter(
                "sec_edgar", verdict=Verdict(False, "not_found", "no CIK")
            )
        },
        store=store,
        clock=Clock(),
    )

    result = await builder.build("ZZZZ")

    attempt = result.sources[0]
    assert attempt.status == EMPTY
    assert attempt.verdict == "not_found"
    assert result.status == EMPTY, "nothing was served and no row existed"
    assert result.snapshot is None
    assert result.written is False


async def test_an_unwired_adapter_is_skipped_not_failed(tmp_path: Path) -> None:
    store = await store_for(tmp_path)
    builder = MergedFundamentalsBuilder(
        {"sec_edgar": FakeAdapter("sec_edgar", {"revenue_ttm": 1.0})},
        store=store,
        clock=Clock(),
    )

    result = await builder.build("AAPL")

    statuses = {item.source: item.status for item in result.sources}
    assert statuses == {"sec_edgar": "ok", "yahoo": "skipped", "finviz": "skipped"}
    assert result.requests == 1, "a skipped source costs no request"
    assert result.snapshot is not None and result.filled == 1
    assert result.sources[1].reason is not None and "no adapter" in result.sources[1].reason


async def test_an_ok_verdict_with_no_cell_is_empty_not_a_null_row(tmp_path: Path) -> None:
    store = await store_for(tmp_path)
    builder = MergedFundamentalsBuilder(
        adapters(sec={}, yahoo={}, finviz={}), store=store, clock=Clock()
    )

    result = await builder.build("AAPL")

    assert {item.status for item in result.sources} == {EMPTY}
    assert result.status == EMPTY
    assert result.snapshot is None
    assert await store.count() == 0, "a row of nulls would claim an answer nobody gave"


# ---------------------------------------------------------------------------
# The stored row is the base
# ---------------------------------------------------------------------------


async def test_the_stored_row_keeps_its_cells_and_only_holes_are_filled(
    tmp_path: Path,
) -> None:
    store = await store_for(tmp_path)
    base = snapshot(
        "PEP",
        {"pe_ttm": 21.0, "market_cap": 2.3e11, "revenue_ttm": 9.1e10},
        source="tradingview",
    )
    await store.upsert(base)
    builder = MergedFundamentalsBuilder(
        adapters(
            sec={"revenue_ttm": 111.0, "net_income_ttm": 222.0},
            yahoo={"pe_ttm": 999.0},
            finviz={"pe_ttm": 888.0, "peg": 2.5},
        ),
        store=store,
        clock=Clock(),
    )

    result = await builder.build("PEP")

    row = result.snapshot
    assert row is not None
    assert result.before == 3, "the row already answered three cells"
    assert result.added == 2, "net_income_ttm (sec) and peg (finviz) were holes"
    assert row.fields["revenue_ttm"] == 9.1e10, "SEC does not overwrite a filled cell"
    assert row.fields["pe_ttm"] == 21.0, "nor does Yahoo or Finviz"
    assert row.fields["net_income_ttm"] == 222.0
    assert row.fields["peg"] == 2.5
    assert row.field_sources["revenue_ttm"] == "tradingview"
    assert row.field_sources["pe_ttm"] == "tradingview"
    assert row.field_sources["net_income_ttm"] == "sec_edgar"
    assert row.field_sources["peg"] == "finviz"


async def test_the_merged_row_round_trips_its_provenance_through_the_store(
    tmp_path: Path,
) -> None:
    store = await store_for(tmp_path)
    await store.upsert(snapshot("PEP", {"pe_ttm": 21.0}, source="tradingview"))
    builder = MergedFundamentalsBuilder(
        adapters(sec={"revenue_ttm": 111.0}, yahoo={}, finviz={"peg": 2.5}),
        store=store,
        clock=Clock(),
    )

    result = await builder.build("PEP")
    stored = await store.get("PEP", now=NOW)
    merged = result.snapshot

    assert merged is not None
    assert stored is not None
    assert stored.source == MULTI_SOURCE
    assert dict(stored.fields) == dict(merged.fields)
    assert dict(stored.field_sources) == dict(merged.field_sources)
    assert stored.field_sources["pe_ttm"] == "tradingview"
    assert stored.field_sources["revenue_ttm"] == "sec_edgar"
    assert stored.field_sources["peg"] == "finviz"
    assert stored.field_count == 3


async def test_an_expired_row_may_fill_a_hole_and_never_beats_a_live_source(
    tmp_path: Path,
) -> None:
    store = await store_for(tmp_path)
    expired = snapshot(
        "PEP",
        {"pe_ttm": 21.0, "market_cap": 2.3e11},
        fetched_at=NOW - timedelta(days=2),
    )
    await store.upsert(expired)
    builder = MergedFundamentalsBuilder(
        adapters(sec={}, yahoo={"pe_ttm": 999.0}, finviz={}),
        store=store,
        clock=Clock(),
    )

    result = await builder.build("PEP")

    row = result.snapshot
    assert row is not None
    assert row.fields["pe_ttm"] == 999.0, "a live source wins over stale data"
    assert row.field_sources["pe_ttm"] == "yahoo"
    assert row.fields["market_cap"] == 2.3e11, "the stale row still fills its own hole"
    assert row.field_sources["market_cap"] == "tradingview"
    assert result.written is True


def test_merge_priority_puts_a_fresh_base_first_and_an_expired_one_last() -> None:
    fresh = snapshot("PEP", {"pe_ttm": 21.0}, source="yahoo")
    stale = snapshot("PEP", {"pe_ttm": 21.0}, fetched_at=NOW - timedelta(days=3))

    assert merge_priority(fresh, now=NOW) == (
        "yahoo",
        "sec_edgar",
        "finviz",
        "tradingview",
    )
    assert merge_priority(stale, now=NOW) == (
        "sec_edgar",
        "yahoo",
        "finviz",
        "tradingview",
        "tradingview",
    ), "an expired base ranks after every live source"
    assert merge_priority(None, now=NOW) == DEFAULT_PRIORITY
    assert merge_priority(snapshot("PEP", {"pe_ttm": 1.0}, source=None), now=NOW) == DEFAULT_PRIORITY


# ---------------------------------------------------------------------------
# The 24 h window
# ---------------------------------------------------------------------------


async def test_a_fresh_row_is_cached_with_zero_requests(tmp_path: Path) -> None:
    store = await store_for(tmp_path)
    base = snapshot("PEP", {"pe_ttm": 21.0, "market_cap": 2.3e11})
    await store.upsert(base)
    live = adapters(sec={"revenue_ttm": 111.0}, yahoo={"pe_ttm": 999.0}, finviz={})
    builder = MergedFundamentalsBuilder(live, store=store, clock=Clock())

    result = await builder.build("PEP", skip_fresh=True)

    assert result.status == CACHED
    assert result.requests == 0
    assert result.written is False
    assert result.kept is True
    assert result.filled == 2
    assert result.added == 0
    assert all(adapter.fetches == [] for adapter in live.values()), (
        "a fresh row is an answer: not one request was spent"
    )


async def test_refresh_re_merges_and_still_keeps_the_base_cells(tmp_path: Path) -> None:
    store = await store_for(tmp_path)
    await store.upsert(snapshot("PEP", {"pe_ttm": 21.0}))
    live = adapters(sec={"revenue_ttm": 111.0}, yahoo={"pe_ttm": 999.0}, finviz={})
    builder = MergedFundamentalsBuilder(live, store=store, clock=Clock())

    result = await builder.build("PEP", skip_fresh=False)

    assert len(live["yahoo"].fetches) == 1
    assert result.status == PARTIAL
    assert result.snapshot is not None
    assert result.snapshot.fields["pe_ttm"] == 21.0
    assert result.snapshot.fields["revenue_ttm"] == 111.0
    assert result.added == 1


async def test_a_merge_that_adds_nothing_does_not_advance_the_clock(tmp_path: Path) -> None:
    store = await store_for(tmp_path)
    base = snapshot("PEP", {"pe_ttm": 21.0})
    await store.upsert(base)
    builder = MergedFundamentalsBuilder(
        adapters(sec={"revenue_ttm": 1.0}, yahoo={"pe_ttm": 999.0}, finviz={}),
        store=store,
        clock=Clock(),
    )
    first = await builder.build("PEP", skip_fresh=False)
    assert first.written is True

    clock = Clock(NOW + timedelta(minutes=5))
    again = MergedFundamentalsBuilder(
        adapters(sec={"revenue_ttm": 1.0}, yahoo={"pe_ttm": 999.0}, finviz={}),
        store=store,
        clock=clock,
    )
    second = await again.build("PEP", skip_fresh=False)
    stored = await store.get("PEP", now=clock())

    assert second.status == KEPT
    assert second.written is False
    assert second.requests == 3, "the sources were asked; they had nothing new"
    assert stored is not None and stored.fetched_at == NOW, (
        "an identical row is not rewritten: the window does not extend for free"
    )


async def test_every_source_failing_leaves_the_stored_row_untouched(tmp_path: Path) -> None:
    store = await store_for(tmp_path)
    base = snapshot("PEP", {"pe_ttm": 21.0, "revenue_ttm": 1.0})
    await store.upsert(base)
    builder = MergedFundamentalsBuilder(
        {
            "sec_edgar": FakeAdapter("sec_edgar", error=RuntimeError("sec down")),
            "yahoo": FakeAdapter("yahoo", error=RuntimeError("yahoo down")),
            "finviz": FakeAdapter("finviz", error=RuntimeError("finviz down")),
        },
        store=store,
        clock=Clock(),
    )

    result = await builder.build("PEP", skip_fresh=False)
    stored = await store.get("PEP", now=NOW)

    assert result.status == KEPT
    assert result.written is False
    assert result.kept is True
    assert {item.status for item in result.sources} == {FAILED}
    assert stored is not None
    assert stored.fetched_at == base.fetched_at
    assert {
        name: value for name, value in stored.fields.items() if value is not None
    } == dict(base.fields)


async def test_no_source_and_no_row_is_reported_as_a_failure_without_a_write(
    tmp_path: Path,
) -> None:
    store = await store_for(tmp_path)
    builder = MergedFundamentalsBuilder(
        {"yahoo": FakeAdapter("yahoo", error=RuntimeError("down"))},
        store=store,
        clock=Clock(),
    )

    result = await builder.build("AAPL")

    assert result.status == FAILED
    assert result.snapshot is None
    assert result.written is False
    assert await store.count() == 0


async def test_an_expired_row_stays_expired_when_nothing_was_served(tmp_path: Path) -> None:
    store = await store_for(tmp_path)
    stale = snapshot("PEP", {"pe_ttm": 21.0}, fetched_at=NOW - timedelta(days=3))
    await store.upsert(stale)
    builder = MergedFundamentalsBuilder(
        adapters(sec={}, yahoo={}, finviz={}), store=store, clock=Clock()
    )

    result = await builder.build("PEP", skip_fresh=False)
    stored = await store.get("PEP", now=NOW, include_expired=True)

    assert result.status == FAILED, "an expired row is not a fresh answer"
    assert result.written is False
    assert result.kept is True
    assert stored is not None and stored.expires_at == stale.expires_at


# ---------------------------------------------------------------------------
# The batch and its report
# ---------------------------------------------------------------------------


async def test_the_report_counts_what_was_added_and_who_added_it(tmp_path: Path) -> None:
    store = await store_for(tmp_path)
    await store.upsert(snapshot("AAPL", {"pe_ttm": 21.0}))
    await store.upsert(snapshot("MSFT", {"pe_ttm": 31.0, "market_cap": 3.0e12}))
    live = {
        "sec_edgar": FakeAdapter(
            "sec_edgar",
            per_symbol={
                "AAPL": {"revenue_ttm": 100.0, "net_income_ttm": 10.0},
                "MSFT": {"revenue_ttm": 200.0},
            },
        ),
        "yahoo": FakeAdapter("yahoo", {"pe_ttm": 999.0, "ps": 8.0}),
        "finviz": FakeAdapter("finviz", {"peg": 1.5}),
    }
    report = await build_merged_snapshots(
        store,
        symbols=["aapl", "MSFT", "AAPL"],
        adapters=live,
        clock=Clock(),
        skip_fresh=False,
    )

    assert isinstance(report, MergedReport)
    assert report.requested == 2, "one row per distinct symbol"
    assert report.attempted == 2
    assert report.built == 2 and report.written == 2
    assert report.before_cells == 3
    assert report.added_cells == 7
    assert report.filled_cells == 10
    assert report.requests == 6
    assert report.cell_sources() == {
        "finviz": 2,
        "sec_edgar": 3,
        "tradingview": 3,
        "yahoo": 2,
    }, "cells, per source, over the rows this run wrote"
    assert report.attempt_statuses()["sec_edgar"] == {"ok": 2}
    # The report is JSON-safe with no NaN and carries the per-symbol detail.
    document = json.loads(json.dumps(report.as_dict(), allow_nan=False))
    assert document["written"] == 2
    assert document["results"][0]["symbol"] == "AAPL"
    assert document["results"][0]["field_sources"]["pe_ttm"] == "tradingview"


async def test_the_report_measures_the_rows_it_wrote_not_the_store(tmp_path: Path) -> None:
    store = await store_for(tmp_path)
    await store.upsert(snapshot("AAPL", {"pe_ttm": 21.0}))
    live = adapters(sec={"revenue_ttm": 100.0}, yahoo={}, finviz={})
    report = await build_merged_snapshots(
        store, symbols=["AAPL"], adapters=live, clock=Clock(), skip_fresh=False
    )

    coverage = report.coverage()

    assert coverage["symbols"] == 1
    assert coverage["filled_cells"] == 2
    assert coverage["total_cells"] == len(FUNDAMENTAL_FIELDS)
    assert coverage["field_sources"]["cells"] == {"sec_edgar": 1, "tradingview": 1}


async def test_build_many_drops_blank_tokens_and_defers_a_budgeted_tail(
    tmp_path: Path,
) -> None:
    store = await store_for(tmp_path)
    ticks = iter([0.0, 0.0, 100.0, 100.0, 100.0])
    builder = MergedFundamentalsBuilder(
        adapters(sec={"revenue_ttm": 1.0}),
        store=store,
        clock=Clock(),
        monotonic=lambda: next(ticks, 100.0),
    )

    report = await builder.build_many(
        ["AAPL", "  ", "MSFT", "NVDA"],
        concurrency=1,
        skip_fresh=False,
        time_budget_s=5.0,
    )

    assert report.dropped == ("  ",)
    assert [item.symbol for item in report.results] == ["AAPL"]
    assert report.deferred == ("MSFT", "NVDA")
    assert report.requested == 3
    assert report.eta_s is not None and report.eta_s >= 0.0


async def test_one_failing_symbol_does_not_sink_the_batch(tmp_path: Path) -> None:
    store = await store_for(tmp_path)
    live = {
        "sec_edgar": FakeAdapter(
            "sec_edgar", per_symbol={"AAPL": {"revenue_ttm": 1.0}, "MSFT": {}}
        ),
        "yahoo": FakeAdapter(
            "yahoo", error={"MSFT": RuntimeError("boom")}
        ),
        "finviz": FakeAdapter("finviz", {}),
    }
    report = await build_merged_snapshots(
        store, symbols=["AAPL", "MSFT"], adapters=live, clock=Clock(), skip_fresh=False
    )

    statuses = {item.symbol: item.status for item in report.results}
    assert statuses == {"AAPL": PARTIAL, "MSFT": FAILED}
    assert report.built == 1 and report.failed == 1


# ---------------------------------------------------------------------------
# The CLI: --source merged
# ---------------------------------------------------------------------------


def test_the_cli_accepts_the_merged_source() -> None:
    cli = _load_cli()

    args = cli.parse_args(["--symbols", "AAPL", "--source", "merged"])

    assert args.source == "merged"
    assert cli.normalize_source("merged") == cli.SOURCE_MERGED
    assert "merged" in cli.SOURCE_CHOICES


async def test_the_cli_merges_and_reports_one_json_document(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cli = _load_cli()
    db_path = tmp_path / "ztockz.db"
    await _seed(db_path, "AAPL", {"pe_ttm": 21.0, "market_cap": 3.0e12})
    args = cli.parse_args(
        [
            "--symbols",
            "AAPL",
            "--source",
            "merged",
            "--db",
            str(db_path),
            "--refresh",
            "--json",
        ]
    )

    code = await cli.run(
        args,
        adapters=adapters(
            sec={"revenue_ttm": 100.0, "net_income_ttm": 10.0},
            yahoo={},
            finviz={"peg": 1.5},
        ),
    )

    document = json.loads(capsys.readouterr().out)
    assert code == 0
    assert document["source"] == "merged"
    merged = document["merged"]
    assert merged["requested"] == 1
    assert merged["written"] == 1
    assert merged["added_cells"] == 3
    assert merged["cell_sources"] == {"finviz": 1, "sec_edgar": 2, "tradingview": 2}
    assert merged["results"][0]["filled"] == 5
    assert document["coverage"]["sources"] == {MULTI_SOURCE: 1}


async def test_the_cli_human_report_names_the_merge_and_the_added_cells(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cli = _load_cli()
    db_path = tmp_path / "ztockz.db"
    await _seed(db_path, "AAPL", {"pe_ttm": 21.0})
    args = cli.parse_args(
        ["--symbols", "AAPL", "--source", "merged", "--db", str(db_path), "--refresh"]
    )

    code = await cli.run(
        args, adapters=adapters(sec={"revenue_ttm": 100.0}, yahoo={}, finviz={"peg": 1.5})
    )

    output = capsys.readouterr().out
    assert code == 0
    assert "P11F-C merged" in output
    assert "cells: before=1 after=3 added=2" in output
    assert "cells by source" in output
    assert "sec_edgar=1" in output and "tradingview=1" in output


async def test_the_cli_exits_one_when_a_symbol_could_not_be_built(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cli = _load_cli()
    db_path = tmp_path / "ztockz.db"
    args = cli.parse_args(
        ["--symbols", "AAPL,MSFT", "--source", "merged", "--db", str(db_path), "--json"]
    )

    code = await cli.run(
        args,
        adapters={
            "sec_edgar": FakeAdapter(
                "sec_edgar", per_symbol={"AAPL": {"revenue_ttm": 1.0}, "MSFT": {}}
            ),
            "yahoo": FakeAdapter("yahoo", error={"MSFT": RuntimeError("down")}),
            "finviz": FakeAdapter("finviz", {}),
        },
    )

    capsys.readouterr()
    assert code == 1


async def test_the_cli_report_only_merged_mode_spends_no_request(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cli = _load_cli()
    db_path = tmp_path / "ztockz.db"
    await _seed(db_path, "AAPL", {"pe_ttm": 21.0, "revenue_ttm": 2.0})
    args = cli.parse_args(
        ["--symbols", "AAPL", "--source", "merged", "--db", str(db_path), "--report-only"]
    )
    live = adapters(sec={"revenue_ttm": 1.0}, yahoo={}, finviz={})

    code = await cli.run(args, adapters=live)

    output = capsys.readouterr().out
    assert code == 0
    assert "P11F-C merged, report only" in output
    assert all(adapter.fetches == [] for adapter in live.values()), (
        "--report-only reads the store and nothing else"
    )


async def _seed(db_path: Path, symbol: str, fields: Mapping[str, Any]) -> None:
    """Write one stored row through the real store, so the CLI finds a base."""

    from app.fundamentals.snapshot import apply_migrations

    apply_migrations(db_path)
    store = SqliteFundamentalsStore(db_path)
    try:
        now = datetime.now(UTC)
        await store.upsert(
            FundamentalsSnapshot(
                symbol=symbol,
                instrument_id=instrument_for(symbol).instrument_id,
                fields=dict(fields),
                source="tradingview",
                as_of=now - timedelta(hours=1),
                fetched_at=now - timedelta(hours=1),
                expires_at=now + timedelta(hours=23),
            )
        )
    finally:
        await store.aclose()


async def test_the_merged_row_is_stored_under_the_canonical_symbol(tmp_path: Path) -> None:
    """A row keyed on the caller's spelling would be a second row for one symbol."""

    store = SqliteFundamentalsStore(tmp_path / "canonical.db")
    builder = MergedFundamentalsBuilder(
        adapters(sec={"revenue_ttm": 1.0}), store=store, clock=Clock()
    )

    result = await builder.build(" brk.b ")
    await store.aclose()

    assert result.symbol == "BRK.B"
    assert result.instrument_id == instrument_for("BRK.B").instrument_id
    with closing(sqlite3.connect(tmp_path / "canonical.db")) as connection:
        rows = connection.execute(
            "SELECT symbol, source, field_count FROM fundamentals_snapshots"
        ).fetchall()
    assert rows == [("BRK.B", "sec_edgar", 1)]


def test_source_attempt_rejects_an_unknown_status() -> None:
    with pytest.raises(ValueError):
        SourceAttempt(source="yahoo", status="maybe")


def test_a_merged_result_without_a_snapshot_reports_no_cells() -> None:
    result = MergedResult(symbol="AAPL", status=EMPTY)

    assert result.ok is False
    assert result.cell_sources == {}
    assert result.cells_by_source == {}
    assert result.requests == 0


def test_a_multi_row_without_a_cell_map_cannot_name_its_cells() -> None:
    fields = {name: float(index + 1) for index, name in enumerate(FUNDAMENTAL_FIELDS)}
    row = snapshot("AAPL", fields, source=MULTI_SOURCE)
    result = MergedResult(symbol="AAPL", status=COMPLETE, snapshot=row, filled=22)

    assert result.ok is True
    assert canonical_symbol(row.symbol) == "AAPL"
    assert result.cell_sources == {}, "'multi' says several sources; it labels no cell"


def test_a_merged_row_with_a_cell_map_reports_its_mix() -> None:
    fields = {name: float(index + 1) for index, name in enumerate(FUNDAMENTAL_FIELDS)}
    cells = {
        name: ("sec_edgar" if index % 2 else "tradingview")
        for index, name in enumerate(FUNDAMENTAL_FIELDS)
    }
    row = snapshot("AAPL", fields, source=MULTI_SOURCE, field_sources=cells)
    result = MergedResult(symbol="AAPL", status=COMPLETE, snapshot=row, filled=22, added=22)

    assert result.ok is True
    assert len(result.cell_sources) == 22
    assert result.cells_by_source == {"sec_edgar": 11, "tradingview": 11}
    assert result.as_dict()["field_sources"]["market_cap"] == "tradingview"
