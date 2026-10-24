"""P11C RED — bulk universe mode: scan the US universe, then one snapshot each.

The bulk mode is the *universe*, not the display list: the symbols come from the
same TradingView scan the screen engine already trusts (``fetch_all`` with
``PARITY_COLUMNS``, then ``build_universe`` for the tab's gates), are deduped on
the canonical storage key, and only then handed to
:meth:`FundamentalsSnapshotBuilder.build_many`.

What this suite pins:

* the scan is asked for the parity columns (one vocabulary for screen and
  fundamentals) and the region is ``us`` — never a second symbol list;
* rows the universe gate drops (OTC, sub-floor cap, funds, foreign listings)
  never reach the builder, and ``--limit`` applies to *symbols after the gate*,
  not to raw scan rows;
* duplicate symbols collapse to one row (the store is keyed on the symbol);
* a time budget stops the batch, reports the untouched tail as ``deferred``, and
  a deferred symbol is **not** written (the next run resumes it through
  ``skip_fresh``);
* progress reports ``completed/total``, elapsed and ETA as each result lands;
* the CLI's ``--universe us`` mode builds the limited universe end to end, prints
  a summary (requested/built/written/cached/failed/requests/elapsed/ETA) and a
  bounded symbol table, and refuses an unbounded walk or >8 in-flight walks;
* ``--report-only`` never constructs the scanner.

No network: the scanner page and the ladder are doubles, and the store is a temp
SQLite file. No test here may issue thousands of provider requests: the ``us``
page is a handful of rows.
"""

from __future__ import annotations

import asyncio
import dataclasses
import importlib.util
import json
from datetime import timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping

import pytest

from app.fundamentals.snapshot import (
    FAILED,
    BuildReport,
    BuildResult,
    FundamentalsSnapshotBuilder,
    SqliteFundamentalsStore,
)
from app.fundamentals.universe import (
    PARITY_COLUMNS,
    UniverseError,
    UniversePlan,
    fetch_us_symbols,
)
from app.screen.scanner import DEFAULT_FLOOR, ScanError, ScanPage
from tests.test_fundamentals_snapshot import NOW, FakeLadder, full_fields

CLI_PATH = Path(__file__).resolve().parents[1] / "scripts" / "fundamentals-snapshot.py"


def _load_cli() -> Any:
    """Import ``scripts/fundamentals-snapshot.py`` without making it a package."""

    spec = importlib.util.spec_from_file_location("fundamentals_snapshot_cli", CLI_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CLI = _load_cli()


# ---------------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------------


def scan_row(
    symbol: str,
    *,
    description: str | None = None,
    exchange: str = "NASDAQ",
    country: str = "United States",
    market_cap: float | None = 5e9,
    instrument: str = "stock",
    subtype: str = "common",
) -> dict[str, Any]:
    """One row in the shape ``scanner.parse_scan`` emits."""

    return {
        "symbol": symbol,
        "description": description if description is not None else f"{symbol} Inc",
        "exchange": exchange,
        "country": country,
        "sector": "Technology",
        "type": instrument,
        "subtype": subtype,
        "currency": "USD",
        "market_cap_basic": market_cap,
        "close": 100.0,
        "change": 0.5,
        "volume": 1_000_000.0,
        "Value.Traded": 90_000_000.0,
    }


class FakeScan:
    """A scripted scanner page; records the request it was asked for."""

    def __init__(
        self,
        rows: Iterable[Mapping[str, Any]],
        *,
        total_count: int | None = None,
    ) -> None:
        self.rows = [dict(row) for row in rows]
        self.total_count = len(self.rows) if total_count is None else total_count
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, region: str, **kwargs: Any) -> ScanPage:
        self.calls.append({"region": region, **kwargs})
        return ScanPage(
            total_count=self.total_count,
            rows=[dict(row) for row in self.rows],
            region=region,
            received=len(self.rows),
        )


class NeverScan:
    """A scanner that fails the test if anything asks it for a page."""

    async def __call__(self, region: str, **kwargs: Any) -> ScanPage:  # pragma: no cover
        raise AssertionError("the scanner must not be called in this mode")


def ladder(script: Mapping[str, Any] | None = None, *, hook: Any | None = None) -> FakeLadder:
    return FakeLadder(script or {}, hook=hook)


def builder_for(
    provider: Any,
    store: SqliteFundamentalsStore,
    *,
    monotonic: Any | None = None,
) -> FundamentalsSnapshotBuilder:
    return FundamentalsSnapshotBuilder(provider, store=store, monotonic=monotonic)


async def store_for(tmp_path: Path) -> SqliteFundamentalsStore:
    return SqliteFundamentalsStore(tmp_path / "fundamentals.db")


class Monotonic:
    """A settable monotonic clock, so a time budget is arithmetic, not a sleep."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> float:
        self.now += seconds
        return self.now


def ticking_hook(clock: Monotonic, seconds_per_symbol: float = 10.0) -> Any:
    """Charge each walk a fixed slice of the budget clock."""

    async def hook(symbol: str) -> None:
        clock.advance(seconds_per_symbol)

    return hook


# ---------------------------------------------------------------------------
# The plan: which symbols, and what the scan had to say
# ---------------------------------------------------------------------------


async def test_us_symbols_ask_the_scanner_for_the_parity_columns() -> None:
    scan = FakeScan([scan_row("AAPL"), scan_row("MSFT")])
    await fetch_us_symbols(fetch=scan)

    assert len(scan.calls) == 1
    call = scan.calls[0]
    assert call["region"] == "us"
    assert tuple(call["columns"]) == tuple(PARITY_COLUMNS)
    assert len(PARITY_COLUMNS) > 13
    assert call["min_market_cap"] is DEFAULT_FLOOR


async def test_symbols_are_canonical_and_keep_the_scan_order() -> None:
    plan = await fetch_us_symbols(
        fetch=FakeScan([scan_row("nvda"), scan_row(" aapl "), scan_row("BRK.B")])
    )

    assert plan.symbols == ("NVDA", "AAPL", "BRK.B")
    assert plan.region == "us"


async def test_the_universe_gate_drops_rows_a_fundamentals_snapshot_must_not_hold() -> None:
    scan = FakeScan(
        [
            scan_row("AAPL"),
            scan_row("PINKY", exchange="OTC"),  # OTC: out of scope
            scan_row("TINY", market_cap=50_000_000.0),  # below the USD 100M floor
            scan_row("NOCAP", market_cap=None),  # unknown cap is not large enough
            scan_row("SPY", instrument="fund", subtype="etf"),  # a fund is not a stock
            scan_row("TOKYO", exchange="TSE", country="Japan"),  # not a US listing
            scan_row("MSFT"),
        ]
    )
    plan = await fetch_us_symbols(fetch=scan)

    assert plan.symbols == ("AAPL", "MSFT")
    assert plan.rows == 7
    assert plan.issuers == 2
    assert plan.dropped == 5


async def test_limit_applies_to_symbols_after_the_gate_not_to_raw_rows() -> None:
    scan = FakeScan(
        [
            scan_row("PINKY", exchange="OTC"),
            scan_row("TINY", market_cap=10_000_000.0),
            scan_row("AAPL"),
            scan_row("MSFT"),
            scan_row("NVDA"),
        ]
    )
    plan = await fetch_us_symbols(fetch=scan, limit=2)

    assert plan.symbols == ("AAPL", "MSFT")
    assert plan.limit == 2
    assert plan.truncated is True


async def test_a_limit_of_zero_or_none_keeps_every_symbol() -> None:
    scan = FakeScan([scan_row("AAPL"), scan_row("MSFT")])
    for limit in (None, 0):
        plan = await fetch_us_symbols(fetch=scan, limit=limit)
        assert plan.symbols == ("AAPL", "MSFT")
        assert plan.limit is None


async def test_duplicate_symbols_collapse_to_one_row() -> None:
    # Different issuer descriptions survive build_universe's issuer dedupe, but
    # the fundamentals store is keyed on the symbol, so the plan dedupes again.
    scan = FakeScan(
        [
            scan_row("AAPL", description="Apple Inc", market_cap=3.4e12),
            scan_row("AAPL", description="Apple Hospitality", market_cap=3.0e9),
            scan_row("MSFT"),
        ]
    )
    plan = await fetch_us_symbols(fetch=scan)

    assert plan.symbols == ("AAPL", "MSFT")
    assert plan.duplicates == 1
    assert plan.issuers == 3


async def test_a_scan_that_answers_zero_rows_is_an_empty_plan_not_an_error() -> None:
    plan = await fetch_us_symbols(fetch=FakeScan([]))

    assert plan.symbols == ()
    assert plan.total_count == 0
    assert plan.dropped == 0


async def test_a_scan_that_promises_rows_but_sends_none_is_an_error() -> None:
    with pytest.raises(UniverseError, match="promised 5,000 rows"):
        await fetch_us_symbols(fetch=FakeScan([], total_count=5_000))


def test_an_unknown_universe_region_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown universe region"):
        asyncio.run(fetch_us_symbols(region="global", fetch=FakeScan([])))


async def test_the_plan_documents_the_scan_it_consumed() -> None:
    scan = FakeScan([scan_row("AAPL"), scan_row("PINKY", exchange="OTC")])
    plan = await fetch_us_symbols(fetch=scan, limit=1)
    document = plan.as_dict()

    assert document["region"] == "us"
    assert document["rows"] == 2
    assert document["symbols"] == 1
    assert document["dropped"] == 1
    assert document["limit"] == 1
    assert document["sample"] == ["AAPL"]
    assert document["columns"] == list(PARITY_COLUMNS)


def test_a_universe_plan_is_frozen_and_counts_its_own_dropped_rows() -> None:
    plan = UniversePlan(
        region="us",
        symbols=("AAPL",),
        rows=4,
        total_count=4,
        issuers=2,
        duplicates=1,
        limit=None,
        columns=PARITY_COLUMNS,
    )
    assert plan.dropped == 2
    assert plan.truncated is False
    with pytest.raises(dataclasses.FrozenInstanceError):
        plan.symbols = ()  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Time budget and progress inside build_many
# ---------------------------------------------------------------------------


async def test_a_time_budget_defers_the_symbols_it_could_not_start(tmp_path: Path) -> None:
    symbols = ["AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL"]
    clock = Monotonic()
    builder = builder_for(
        ladder({symbol: full_fields() for symbol in symbols}, hook=ticking_hook(clock)),
        await store_for(tmp_path),
        monotonic=clock,
    )

    # 10 s per walk, a 25 s budget: the third walk ends at 30 s, so the batch
    # stops starting new ones and the rest are deferred.
    report = await builder.build_many(
        symbols, concurrency=1, skip_fresh=False, time_budget_s=25.0
    )

    assert report.attempted == 3
    assert report.deferred == ("AMZN", "META", "GOOGL")
    assert report.requested == len(symbols)
    assert set(report.deferred).isdisjoint({item.symbol for item in report.results})
    assert len(report.results) + len(report.deferred) == len(symbols)
    assert report.eta_s is not None and report.eta_s >= 0


async def test_a_deferred_symbol_is_not_written_and_the_next_run_resumes_it(
    tmp_path: Path,
) -> None:
    symbols = ["AAPL", "MSFT", "NVDA", "AMZN", "META"]
    store = await store_for(tmp_path)
    clock = Monotonic()
    fake = ladder({symbol: full_fields() for symbol in symbols}, hook=ticking_hook(clock))
    builder = builder_for(fake, store, monotonic=clock)

    first = await builder.build_many(
        symbols, concurrency=1, skip_fresh=False, time_budget_s=25.0
    )
    assert first.deferred == ("AMZN", "META")
    assert first.written == 3
    assert await store.count() == first.written == len(first.results)

    resumed = await builder.build_many(
        list(first.deferred), concurrency=1, skip_fresh=True, time_budget_s=60.0
    )

    assert resumed.requested == len(first.deferred)
    assert resumed.deferred == ()
    assert await store.count() == len(symbols)
    # One row per symbol; every symbol was walked exactly once (the drain keeps
    # an in-flight walk's result instead of cancelling a request already spent).
    assert len(fake.calls) == len(symbols)


async def test_a_time_budget_of_zero_means_no_budget(tmp_path: Path) -> None:
    symbols = ["AAPL", "MSFT", "NVDA"]
    builder = builder_for(
        ladder({symbol: full_fields() for symbol in symbols}), await store_for(tmp_path)
    )

    report = await builder.build_many(
        symbols, concurrency=2, skip_fresh=False, time_budget_s=0
    )

    assert report.deferred == ()
    assert report.requested == 3


async def test_a_negative_time_budget_is_refused(tmp_path: Path) -> None:
    builder = builder_for(ladder({"AAPL": full_fields()}), await store_for(tmp_path))
    with pytest.raises(ValueError, match="time_budget_s"):
        await builder.build_many(["AAPL"], time_budget_s=-1)


async def test_progress_sees_every_completed_result_exactly_once(tmp_path: Path) -> None:
    symbols = ["AAPL", "MSFT", "NVDA", "AMZN", "META"]
    builder = builder_for(
        ladder({symbol: full_fields() for symbol in symbols}), await store_for(tmp_path)
    )
    seen: list[tuple[str, int, int]] = []

    report = await builder.build_many(
        symbols,
        concurrency=2,
        skip_fresh=False,
        on_result=lambda result, completed, total: seen.append(
            (result.symbol, completed, total)
        ),
    )

    assert [item[1] for item in seen] == [1, 2, 3, 4, 5]
    assert {item[2] for item in seen} == {5}
    assert {item[0] for item in seen} == set(symbols)
    assert report.deferred == ()


# ---------------------------------------------------------------------------
# The CLI: --universe us
# ---------------------------------------------------------------------------


def universe_args(tmp_path: Path, *extra: str) -> Any:
    return CLI.parse_args(
        ["--universe", "us", "--db", str(tmp_path / "cli.db"), *extra]
    )


async def test_cli_universe_mode_builds_the_limited_universe(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    scan = FakeScan(
        [scan_row("AAPL"), scan_row("MSFT"), scan_row("NVDA"), scan_row("AMZN")]
    )
    fake = ladder({symbol: full_fields() for symbol in ("AAPL", "MSFT", "NVDA")})
    args = universe_args(tmp_path, "--limit", "3", "--progress-every", "1")

    code = await CLI.run(args, ladder=fake, universe_fetch=scan)
    out = capsys.readouterr()

    assert code == 0
    assert [call["region"] for call in scan.calls] == ["us"]
    assert "universe: us" in out.out
    assert "symbols=3" in out.out
    assert "build: requested=3 built=3 written=3 cached=0 empty=0 failed=0" in out.out
    assert "elapsed=" in out.out and "eta=" in out.out
    assert "coverage:" in out.out
    # Progress lines are diagnostics: stderr, never stdout.
    assert "1/3" in out.err and "eta=" in out.err
    assert "1/3" not in out.out
    store = SqliteFundamentalsStore(tmp_path / "cli.db")
    assert await store.count() == 3
    await store.aclose()


async def test_cli_universe_mode_json_is_a_summary_not_a_full_symbol_dump(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    scan = FakeScan([scan_row(symbol) for symbol in ("AAPL", "MSFT", "NVDA")])
    fake = ladder({symbol: full_fields() for symbol in ("AAPL", "MSFT", "NVDA")})
    args = universe_args(tmp_path, "--limit", "3", "--json")

    assert await CLI.run(args, ladder=fake, universe_fetch=scan) == 0
    document = json.loads(capsys.readouterr().out)

    assert document["mode"] == "universe"
    assert document["universe"]["region"] == "us"
    assert document["universe"]["symbols"] == 3
    assert document["universe"]["sample"] == ["AAPL", "MSFT", "NVDA"]
    assert document["symbols"] is None
    assert document["build"]["requested"] == 3
    assert document["elapsed_s"] is not None
    assert document["coverage"]["fresh"] == 3


async def test_cli_universe_mode_resumes_from_fresh_rows_with_no_requests(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    scan = FakeScan([scan_row("AAPL"), scan_row("MSFT")])
    fake = ladder({symbol: full_fields() for symbol in ("AAPL", "MSFT")})
    args = universe_args(tmp_path, "--limit", "2")

    assert await CLI.run(args, ladder=fake, universe_fetch=scan) == 0
    capsys.readouterr()

    assert await CLI.run(args, ladder=fake, universe_fetch=scan) == 0
    out = capsys.readouterr()
    assert "cached=2" in out.out

    store = SqliteFundamentalsStore(tmp_path / "cli.db")
    rows = await store.rows()
    await store.aclose()
    assert len(rows) == 2
    # Two walks total: the second run answered from the 24 h rows.
    assert len(fake.calls) == 2


class BrokenScan:
    """A scanner that answers with a terminal error (blocked / unusable body)."""

    async def __call__(self, region: str, **kwargs: Any) -> ScanPage:
        raise ScanError(f"scanner answered nothing for {region}")


async def test_cli_universe_mode_refuses_an_empty_universe(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = universe_args(tmp_path, "--limit", "5")

    code = await CLI.run(args, ladder=ladder({}), universe_fetch=FakeScan([]))

    assert code == 2
    assert "answered no symbols" in capsys.readouterr().err


async def test_cli_universe_scan_failure_is_reported_not_raised(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = universe_args(tmp_path, "--limit", "5")

    code = await CLI.run(args, ladder=ladder({}), universe_fetch=BrokenScan())

    assert code == 2
    assert "could not be read" in capsys.readouterr().err


async def test_cli_report_only_never_calls_the_scanner(tmp_path: Path) -> None:
    args = universe_args(tmp_path, "--report-only")

    code = await CLI.run(args, ladder=ladder({}), universe_fetch=NeverScan())

    assert code == 2  # an empty store has no fresh coverage to report


def test_cli_universe_plus_symbols_is_refused(tmp_path: Path) -> None:
    code = CLI.main(
        [
            "--universe",
            "us",
            "--symbols",
            "AAPL",
            "--limit",
            "5",
            "--db",
            str(tmp_path / "cli.db"),
        ]
    )
    assert code == 2


def test_cli_refuses_an_unbounded_universe_walk(tmp_path: Path) -> None:
    args = universe_args(tmp_path)  # no --limit, no --time-budget, no --all
    message = CLI.universe_guard(args)
    assert message is not None and "--limit" in message and "--time-budget" in message
    assert CLI.main(["--universe", "us", "--db", str(tmp_path / "cli.db")]) == 2


def test_cli_universe_concurrency_above_eight_is_refused(tmp_path: Path) -> None:
    args = universe_args(tmp_path, "--limit", "10", "--concurrency", "9")
    message = CLI.universe_guard(args)
    assert message is not None and "8" in message
    assert (
        CLI.main(
            [
                "--universe",
                "us",
                "--limit",
                "10",
                "--concurrency",
                "9",
                "--db",
                str(tmp_path / "cli.db"),
            ]
        )
        == 2
    )


def test_cli_universe_concurrency_up_to_eight_is_allowed(tmp_path: Path) -> None:
    args = universe_args(tmp_path, "--limit", "10", "--concurrency", "8")
    assert CLI.universe_guard(args) is None


def test_cli_an_unknown_universe_region_is_an_argparse_error() -> None:
    with pytest.raises(SystemExit):
        CLI.parse_args(["--universe", "global"])


# ---------------------------------------------------------------------------
# Report surface
# ---------------------------------------------------------------------------


def test_the_build_report_closes_the_arithmetic_with_deferred() -> None:
    report = BuildReport(
        results=(
            BuildResult(
                symbol="AAPL", status="complete", instrument_id="US-XNAS-AAPL", filled=22
            ),
            BuildResult(symbol="MSFT", status=FAILED, instrument_id="US-XNAS-MSFT"),
        ),
        ttl_s=86_400,
        deferred=("NVDA", "AMZN"),
        started_at=NOW,
        finished_at=NOW + timedelta(seconds=10),
    )

    assert report.requested == 4
    assert report.attempted == 2
    assert report.deferred == ("NVDA", "AMZN")
    assert report.elapsed_s == pytest.approx(10.0)
    assert report.eta_s == pytest.approx(10.0)
    document = report.as_dict()
    assert document["requested"] == 4
    assert document["attempted"] == 2
    assert document["deferred"] == ["NVDA", "AMZN"]
    assert document["elapsed_s"] == pytest.approx(10.0)
