"""P11D RED — the bulk fallback: one TradingView scan fills the US universe.

The Yahoo rung is the only per-symbol source that serves all 22 cells, and it is
also the one that throttles: the live P11C run stopped at 1,266 rows with HTTP
429. This suite pins the fallback that finishes the job:

* the scan is asked for the sixteen fundamental columns (plus the gate columns
  ``build_universe`` needs), in **one** page-walk — no per-symbol walk, and the
  ladder is never even constructed;
* the mapping is exact: one provider column → one canonical cell, the provider's
  numbers copied verbatim (``debt_to_equity`` is the one stated scale), and the
  six cells ``tradingview`` never serves stay ``null`` — never ``0``;
* a row that maps to nothing is ``empty`` and **not written**;
* a fresh row is never overwritten: the default keeps any fresh row, and even
  ``--refresh`` keeps a fresh **Yahoo** row (22 cells beat 16), while an expired
  Yahoo row is replaced because an expired answer is not an answer;
* duplicate symbols collapse to one row on the store's own key;
* the report is per source (written vs kept, and which source kept each symbol),
  carries the scan's own measured coverage, and names the fields it cannot fill.

No network: the scanner page is a scripted double and the store is a temp SQLite
file.
"""

from __future__ import annotations

import importlib.util
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping

import pytest

from app.fundamentals.scan import (
    EMPTY,
    KEPT,
    MISSING,
    PROTECTED_SOURCES,
    SCAN_COLUMNS,
    SCAN_FIELD_MAP,
    SCAN_SOURCE,
    UNSERVED_FIELDS,
    WRITTEN,
    build_scan_snapshots,
    mapped_fields,
    scan_cell,
    snapshot_from_scan_row,
)
from app.fundamentals.snapshot import (
    FUNDAMENTAL_FIELDS,
    TTL_S,
    FundamentalsSnapshot,
    SqliteFundamentalsStore,
    canonical_symbol,
    instrument_for,
)
from app.screen.scanner import DEFAULT_FLOOR, ScanError, ScanPage
from app.screen.universe import PARITY_COLUMNS
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

#: One raw scan row's sixteen cells, in the shape the live probe returned for
#: AAPL (2026-10-24). Numbers are the provider's, so the expectations below are
#: the provider's numbers, not invented ones.
FUNDAMENTAL_CELLS: dict[str, Any] = {
    "total_revenue": 416_161_000_000.0,
    "ebitda": 167_959_000_000.0,
    "net_income": 112_010_000_000.0,
    "free_cash_flow": 136_683_000_000.0,
    "total_debt": 84_344_000_000.0,
    "earnings_per_share_diluted_ttm": 8.7233,
    "market_cap_basic": 5_074_761_220_912.0,
    "total_shares_outstanding": 14_594_200_000.0,
    "float_shares_outstanding": 14_576_745_336.8,
    "price_earnings_ttm": 39.86163493173455,
    "price_earnings_growth_ttm": 1.2241437886722224,
    "price_book_fq": 47.24607907476627,
    "enterprise_value_ebitda_ttm": 30.344942640239584,
    "dividends_yield": 0.3105902652958516,
    "return_on_equity": 148.751081626767,
    "debt_to_equity": 0.784449404761905,
}

#: Provider column → canonical cell, written out independently of the module so
#: a rename on either side fails here rather than shipping.
EXPECTED_MAP: dict[str, str] = {
    "total_revenue": "revenue_ttm",
    "ebitda": "ebitda_ttm",
    "net_income": "net_income_ttm",
    "free_cash_flow": "fcf_ttm",
    "total_debt": "total_debt",
    "earnings_per_share_diluted_ttm": "eps_ttm",
    "market_cap_basic": "market_cap",
    "total_shares_outstanding": "shares_outstanding",
    "float_shares_outstanding": "float_shares",
    "price_earnings_ttm": "pe_ttm",
    "price_earnings_growth_ttm": "peg",
    "price_book_fq": "pb",
    "enterprise_value_ebitda_ttm": "ev_ebitda",
    "dividends_yield": "dividend_yield",
    "return_on_equity": "roe",
    "debt_to_equity": "debt_to_equity",
}


def scan_row(
    symbol: str,
    *,
    description: str | None = None,
    exchange: str = "NASDAQ",
    country: str = "United States",
    market_cap: float | None = 5e9,
    instrument: str = "stock",
    subtype: str = "common",
    **cells: Any,
) -> dict[str, Any]:
    """One raw scanner row: the gate columns plus the sixteen fundamental cells."""

    values = dict(FUNDAMENTAL_CELLS)
    values.update(cells)
    if market_cap is not None:
        values["market_cap_basic"] = market_cap
    return {
        "symbol": symbol,
        "name": symbol,
        "description": description if description is not None else f"{symbol} Inc",
        "exchange": exchange,
        "country": country,
        "type": instrument,
        "subtype": subtype,
        "currency": "USD",
        **values,
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


class BrokenScan:
    """A scanner that answers with a terminal error (blocked / unusable body)."""

    async def __call__(self, region: str, **kwargs: Any) -> ScanPage:
        raise ScanError(f"scanner answered nothing for {region}")


class NeverLadder:
    """The provider ladder: constructing a walk in scan mode is the bug."""

    async def fetch(self, capability: Any, instrument: Any, fields: Any) -> Any:  # pragma: no cover
        raise AssertionError("--source scan must not walk the provider ladder")


class Clock:
    """A settable clock, so a 24 h TTL is a one-line assertion."""

    def __init__(self, moment: Any = NOW) -> None:
        self.moment = moment

    def __call__(self) -> Any:
        return self.moment

    def advance(self, **kwargs: Any) -> Any:
        self.moment = self.moment + timedelta(**kwargs)
        return self.moment


def yahoo_snapshot(symbol: str, *, fetched_at: Any = NOW, fields: Any = None) -> FundamentalsSnapshot:
    """A stored row as the Yahoo rung would have written it (22 cells)."""

    key = canonical_symbol(symbol)
    return FundamentalsSnapshot(
        symbol=key,
        instrument_id=instrument_for(key).instrument_id,
        fields=fields if fields is not None else full_fields(),
        source="yahoo",
        as_of=fetched_at,
        fetched_at=fetched_at,
        expires_at=fetched_at + timedelta(seconds=TTL_S),
    )


async def store_for(tmp_path: Path) -> SqliteFundamentalsStore:
    return SqliteFundamentalsStore(tmp_path / "fundamentals.db")


# ---------------------------------------------------------------------------
# The request and the mapping
# ---------------------------------------------------------------------------


async def test_one_scan_asks_for_the_gate_and_fundamental_columns(tmp_path: Path) -> None:
    scan = FakeScan([scan_row("AAPL"), scan_row("MSFT")])
    store = await store_for(tmp_path)

    report = await build_scan_snapshots(store, fetch=scan, clock=Clock())
    await store.aclose()

    assert len(scan.calls) == 1, "one page-walk, never one request per symbol"
    call = scan.calls[0]
    assert call["region"] == "us"
    assert tuple(call["columns"]) == SCAN_COLUMNS
    assert call["min_market_cap"] is DEFAULT_FLOOR
    for column in EXPECTED_MAP:
        assert column in call["columns"]
    for gate in ("name", "description", "exchange", "country", "type", "subtype", "currency"):
        assert gate in call["columns"]
    assert report.rows == 2 and report.requested == 2


def test_the_mapping_is_the_sixteen_proven_columns_and_nothing_else() -> None:
    assert dict(SCAN_FIELD_MAP) == EXPECTED_MAP
    assert len(set(SCAN_FIELD_MAP.values())) == 16
    assert set(UNSERVED_FIELDS) == set(FUNDAMENTAL_FIELDS) - set(EXPECTED_MAP.values())
    assert set(UNSERVED_FIELDS) == {
        "market_cap_usd",
        "pe_forward",
        "ps",
        "eps_forward",
        "payout_ratio",
        "cash_and_equivalents",
    }


def test_a_scan_row_maps_cell_for_cell_with_one_stated_scale() -> None:
    row = scan_row("AAPL", market_cap=FUNDAMENTAL_CELLS["market_cap_basic"])
    snapshot = snapshot_from_scan_row(row, fetched_at=NOW)

    expected: dict[str, Any] = {name: None for name in FUNDAMENTAL_FIELDS}
    for column, name in EXPECTED_MAP.items():
        expected[name] = FUNDAMENTAL_CELLS[column]
    # The one unit the two rungs disagree on: TradingView publishes a ratio
    # (0.784), the store's Yahoo rows publish a percent (78.445).
    expected["debt_to_equity"] = pytest.approx(78.4449404761905, rel=1e-12)

    assert snapshot.field_count == 16
    for name in FUNDAMENTAL_FIELDS:
        if name == "debt_to_equity":
            assert snapshot.fields[name] == expected[name]
        else:
            assert snapshot.fields[name] == expected[name], name
    assert snapshot.source == SCAN_SOURCE
    assert snapshot.symbol == "AAPL"
    assert snapshot.instrument_id == instrument_for("AAPL").instrument_id
    assert snapshot.as_of == NOW and snapshot.fetched_at == NOW
    assert snapshot.expires_at == NOW + timedelta(seconds=TTL_S)
    assert snapshot.missing_fields == UNSERVED_FIELDS


def test_a_missing_cell_is_null_never_zero() -> None:
    row = scan_row(
        "MSFT",
        ebitda=None,
        free_cash_flow="n/a",
        price_earnings_ttm=float("nan"),
        return_on_equity="",
        total_debt=0.0,
        dividends_yield=False,
    )

    fields = mapped_fields(row)

    assert fields["ebitda_ttm"] is None
    assert fields["fcf_ttm"] is None
    assert fields["pe_ttm"] is None
    assert fields["roe"] is None
    assert fields["dividend_yield"] is None
    # A real zero is a number and survives: no debt is not "unknown".
    assert fields["total_debt"] == 0.0
    assert fields["revenue_ttm"] == FUNDAMENTAL_CELLS["total_revenue"]


def test_a_numeric_string_is_read_and_a_bool_is_not_a_number() -> None:
    assert scan_cell(" 12.5 ") == 12.5
    assert scan_cell("1,234") is None  # the provider never formats, so neither do we
    assert scan_cell(True) is None
    assert scan_cell(float("inf")) is None
    assert scan_cell(None) is None
    assert scan_cell(0) == 0.0


# ---------------------------------------------------------------------------
# Writing: fresh rows, protected sources, dedupe, gates
# ---------------------------------------------------------------------------


async def test_a_row_with_no_fundamentals_is_empty_and_not_written(tmp_path: Path) -> None:
    # The USD 100M floor is a *cap* gate, so under the default floor a row with
    # no cells is dropped before it can be mapped (a missing cap cannot prove
    # size). The all-null row therefore only reaches the mapper with no floor —
    # the explicit nano-cap view — and even there it is not written.
    hole = scan_row("HOLE", market_cap=None)
    for column in EXPECTED_MAP:
        hole[column] = None
    scan = FakeScan([hole])
    store = await store_for(tmp_path)

    report = await build_scan_snapshots(
        store, fetch=scan, clock=Clock(), min_market_cap=None
    )
    rows = await store.rows()
    await store.aclose()

    assert report.empty == 1 and report.written == 0
    assert report.results[0].status == EMPTY
    assert rows == [], "a row of nulls would claim an answer nobody gave"


async def test_a_fresh_yahoo_row_is_kept_not_replaced(tmp_path: Path) -> None:
    store = await store_for(tmp_path)
    stored = yahoo_snapshot("AAPL")
    await store.upsert(stored)

    scan = FakeScan([scan_row("AAPL", total_revenue=1.0), scan_row("MSFT")])
    report = await build_scan_snapshots(store, fetch=scan, clock=Clock())
    rows = {row.symbol: row for row in await store.rows()}
    await store.aclose()

    assert report.written == 1 and report.kept == 1
    assert report.kept_sources == {"yahoo": 1}
    kept = next(item for item in report.results if item.symbol == "AAPL")
    assert kept.status == KEPT and kept.kept_source == "yahoo"
    assert kept.filled == len(FUNDAMENTAL_FIELDS)
    # The 22-cell row survived, cell for cell.
    assert rows["AAPL"].source == "yahoo"
    assert rows["AAPL"].field_count == len(FUNDAMENTAL_FIELDS)
    assert rows["AAPL"].fields == stored.fields
    assert rows["MSFT"].source == SCAN_SOURCE
    assert rows["MSFT"].field_count == 16


async def test_refresh_still_keeps_a_fresh_yahoo_row(tmp_path: Path) -> None:
    store = await store_for(tmp_path)
    await store.upsert(yahoo_snapshot("AAPL", fetched_at=datetime.now(UTC).replace(microsecond=0)))
    scan = FakeScan([scan_row("AAPL")])
    clock = Clock()

    report = await build_scan_snapshots(store, fetch=scan, clock=clock, skip_fresh=False)
    rows = await store.rows()
    await store.aclose()

    assert report.kept == 1 and report.written == 0
    assert rows[0].source == "yahoo"
    assert "yahoo" in PROTECTED_SOURCES


async def test_refresh_rewrites_a_scan_row_but_not_a_stale_gap(tmp_path: Path) -> None:
    store = await store_for(tmp_path)
    clock = Clock()
    scan = FakeScan([scan_row("AAPL", total_revenue=1.0)])

    first = await build_scan_snapshots(store, fetch=scan, clock=clock)
    clock.advance(minutes=5)
    second = await build_scan_snapshots(store, fetch=scan, clock=clock, skip_fresh=False)
    rows = await store.rows()
    await store.aclose()

    assert first.written == 1 and second.written == 1
    assert rows[0].source == SCAN_SOURCE
    assert rows[0].fetched_at == clock.moment


async def test_an_expired_yahoo_row_is_replaced_by_the_scan(tmp_path: Path) -> None:
    store = await store_for(tmp_path)
    stale = NOW - timedelta(days=2)
    await store.upsert(yahoo_snapshot("AAPL", fetched_at=stale))

    report = await build_scan_snapshots(
        store, fetch=FakeScan([scan_row("AAPL")]), clock=Clock()
    )
    rows = await store.rows()
    await store.aclose()

    assert report.written == 1 and report.kept == 0
    assert rows[0].source == SCAN_SOURCE
    assert rows[0].field_count == 16
    assert rows[0].fetched_at == NOW


async def test_a_second_run_over_the_same_scan_writes_nothing(tmp_path: Path) -> None:
    store = await store_for(tmp_path)
    scan = FakeScan([scan_row("AAPL"), scan_row("MSFT")])
    clock = Clock()

    first = await build_scan_snapshots(store, fetch=scan, clock=clock)
    second = await build_scan_snapshots(store, fetch=scan, clock=clock)
    rows = await store.rows()
    await store.aclose()

    assert first.written == 2
    assert second.written == 0 and second.kept == 2
    assert second.kept_sources == {SCAN_SOURCE: 2}
    assert len(rows) == 2


async def test_duplicate_symbols_collapse_to_one_row(tmp_path: Path) -> None:
    scan = FakeScan(
        [
            scan_row("AAPL", description="Apple Inc", market_cap=3.4e12),
            scan_row("AAPL", description="Apple Hospitality", market_cap=3.0e9),
            scan_row("MSFT"),
        ]
    )
    store = await store_for(tmp_path)

    report = await build_scan_snapshots(store, fetch=scan, clock=Clock())
    rows = await store.rows()
    await store.aclose()

    assert report.requested == 2
    assert report.duplicates == 1
    assert sorted(row.symbol for row in rows) == ["AAPL", "MSFT"]


async def test_the_universe_gate_runs_before_the_limit(tmp_path: Path) -> None:
    scan = FakeScan(
        [
            scan_row("PINKY", exchange="OTC"),
            scan_row("TINY", market_cap=50_000_000.0),
            scan_row("SPY", instrument="fund", subtype="etf"),
            scan_row("AAPL", market_cap=3.4e12),
            scan_row("MSFT", market_cap=3.0e12),
            scan_row("NVDA", market_cap=2.8e12),
        ]
    )
    store = await store_for(tmp_path)

    report = await build_scan_snapshots(store, fetch=scan, limit=1, clock=Clock())
    rows = await store.rows()
    await store.aclose()

    assert report.issuers == 3
    assert report.requested == 1
    assert report.truncated is True
    assert [row.symbol for row in rows] == ["AAPL"]


async def test_a_scan_that_promises_rows_but_sends_none_is_an_error(tmp_path: Path) -> None:
    from app.fundamentals.universe import UniverseError

    store = await store_for(tmp_path)
    try:
        with pytest.raises(UniverseError):
            await build_scan_snapshots(
                store, fetch=FakeScan([], total_count=5_720), clock=Clock()
            )
    finally:
        await store.aclose()


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------


async def test_the_report_is_per_source_and_names_the_missing_fields(
    tmp_path: Path,
) -> None:
    store = await store_for(tmp_path)
    await store.upsert(yahoo_snapshot("AAPL", fetched_at=datetime.now(UTC).replace(microsecond=0)))
    scan = FakeScan([scan_row("AAPL"), scan_row("MSFT"), scan_row("NVDA")])

    report = await build_scan_snapshots(store, fetch=scan, clock=Clock())
    await store.aclose()

    document = report.as_dict()
    assert document["source"] == SCAN_SOURCE
    assert document["requested"] == 3
    assert document["written"] == 2
    assert document["kept"] == 1
    assert document["kept_sources"] == {"yahoo": 1}
    assert document["missing_fields"] == list(UNSERVED_FIELDS)
    assert document["sample"] == ["AAPL", "MSFT", "NVDA"]
    assert document["anomalies"] == []
    assert document["coverage"]["sources"] == {SCAN_SOURCE: 2}
    assert document["coverage"]["symbols"] == 2


async def test_the_reports_coverage_is_measured_over_the_rows_it_wrote(
    tmp_path: Path,
) -> None:
    store = await store_for(tmp_path)

    report = await build_scan_snapshots(
        store, fetch=FakeScan([scan_row("AAPL")]), clock=Clock()
    )
    await store.aclose()

    coverage = report.coverage()
    assert coverage["symbols"] == 1 and coverage["fresh"] == 1
    filled = {row["field"]: row["filled"] for row in coverage["fields"]}
    assert filled["revenue_ttm"] == 1
    assert filled["debt_to_equity"] == 1
    for name in UNSERVED_FIELDS:
        assert filled[name] == 0, name
    assert coverage["complete"] == 0 and coverage["partial"] == 1
    # 16 filled cells of a 22-cell vocabulary.
    assert coverage["filled_cells"] == 16
    assert coverage["fill_rate"] == pytest.approx(16 / 22)


# ---------------------------------------------------------------------------
# The CLI: --universe us --source scan
# ---------------------------------------------------------------------------


def scan_args(tmp_path: Path, *extra: str) -> Any:
    return CLI.parse_args(
        ["--universe", "us", "--source", "scan", "--db", str(tmp_path / "cli.db"), *extra]
    )


async def test_cli_scan_mode_builds_the_universe_from_one_scan(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    scan = FakeScan([scan_row("AAPL"), scan_row("MSFT"), scan_row("NVDA")])

    code = await CLI.run(scan_args(tmp_path), ladder=NeverLadder(), scan_fetch=scan)
    out = capsys.readouterr()

    assert code == 0
    assert [call["region"] for call in scan.calls] == ["us"]
    assert tuple(scan.calls[0]["columns"]) == SCAN_COLUMNS
    assert f"source={SCAN_SOURCE}" in out.out
    assert "scan: us" in out.out
    assert "build: requested=3 written=3 kept=0 empty=0 missing=0" in out.out
    assert "per source: written(tradingview)=3 kept={}" in out.out
    assert "not served by tradingview (6):" in out.out
    assert "sources: {'tradingview': 3}" in out.out
    store = SqliteFundamentalsStore(tmp_path / "cli.db")
    rows = await store.rows()
    await store.aclose()
    assert len(rows) == 3
    assert {row.source for row in rows} == {SCAN_SOURCE}
    assert {row.field_count for row in rows} == {16}


async def test_cli_scan_mode_json_reports_per_source_coverage_and_holes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    scan = FakeScan([scan_row("AAPL"), scan_row("MSFT")])

    assert await CLI.run(scan_args(tmp_path, "--json"), scan_fetch=scan) == 0
    document = json.loads(capsys.readouterr().out)

    assert document["mode"] == "universe"
    assert document["source"] == "scan"
    assert document["symbols"] is None
    assert document["universe"] is None
    assert "build" not in document
    scan_document = document["scan"]
    assert scan_document["written"] == 2
    assert scan_document["kept_sources"] == {}
    assert scan_document["missing_fields"] == list(UNSERVED_FIELDS)
    assert scan_document["coverage"]["fields"][0]["total"] == 2
    assert scan_document["scan_elapsed_s"] is not None
    assert document["coverage"]["sources"] == {SCAN_SOURCE: 2}


async def test_cli_scan_mode_keeps_a_fresh_yahoo_row_end_to_end(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = SqliteFundamentalsStore(tmp_path / "cli.db")
    await store.upsert(yahoo_snapshot("AAPL", fetched_at=datetime.now(UTC).replace(microsecond=0)))
    await store.aclose()

    scan = FakeScan([scan_row("AAPL", total_revenue=1.0), scan_row("MSFT")])
    assert await CLI.run(scan_args(tmp_path), scan_fetch=scan) == 0
    out = capsys.readouterr()

    assert "kept=1" in out.out
    assert "kept={'yahoo': 1}" in out.out
    store = SqliteFundamentalsStore(tmp_path / "cli.db")
    rows = {row.symbol: row for row in await store.rows()}
    await store.aclose()
    assert rows["AAPL"].source == "yahoo" and rows["AAPL"].field_count == 22
    assert rows["MSFT"].source == SCAN_SOURCE and rows["MSFT"].field_count == 16


async def test_cli_scan_failure_is_reported_not_raised(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = await CLI.run(scan_args(tmp_path), scan_fetch=BrokenScan())

    assert code == 2
    assert "could not be read" in capsys.readouterr().err


async def test_cli_scan_mode_refuses_an_empty_universe(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = await CLI.run(scan_args(tmp_path), scan_fetch=FakeScan([]))

    assert code == 2
    assert "answered no symbols" in capsys.readouterr().err


def test_cli_scan_mode_needs_a_universe_and_refuses_a_symbol_list(tmp_path: Path) -> None:
    no_universe = CLI.parse_args(["--source", "scan", "--db", str(tmp_path / "cli.db")])
    message = CLI.universe_guard(no_universe)
    assert message is not None and "--universe" in message

    with_symbols = CLI.parse_args(
        [
            "--universe",
            "us",
            "--source",
            "scan",
            "--symbols",
            "AAPL",
            "--db",
            str(tmp_path / "cli.db"),
        ]
    )
    refusal = CLI.universe_guard(with_symbols)
    assert refusal is not None and "--symbols" in refusal


def test_cli_scan_mode_is_not_an_unbounded_walk(tmp_path: Path) -> None:
    # One scanner page is not one walk per symbol, so no --limit/--all is needed.
    assert CLI.universe_guard(scan_args(tmp_path)) is None
    assert CLI.universe_guard(scan_args(tmp_path, "--limit", "100")) is None


def test_cli_source_yahoo_is_the_ladder_and_an_unknown_source_is_refused() -> None:
    assert CLI.normalize_source("yahoo") == "ladder"
    assert CLI.normalize_source("scan") == "scan"
    assert CLI.normalize_source(None) == "ladder"
    with pytest.raises(SystemExit):
        CLI.parse_args(["--universe", "us", "--source", "vibes"])


async def test_cli_source_yahoo_still_uses_the_parity_universe(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    scan = FakeScan([scan_row("AAPL"), scan_row("MSFT")])
    fake = FakeLadder({symbol: full_fields() for symbol in ("AAPL", "MSFT")})
    args = CLI.parse_args(
        [
            "--universe",
            "us",
            "--source",
            "yahoo",
            "--limit",
            "2",
            "--db",
            str(tmp_path / "cli.db"),
        ]
    )

    assert await CLI.run(args, ladder=fake, universe_fetch=scan) == 0
    out = capsys.readouterr()

    assert tuple(scan.calls[0]["columns"]) == tuple(PARITY_COLUMNS)
    assert "build: requested=2 built=2 written=2 cached=0 empty=0 failed=0" in out.out
    store = SqliteFundamentalsStore(tmp_path / "cli.db")
    rows = await store.rows()
    await store.aclose()
    assert {row.field_count for row in rows} == {len(FUNDAMENTAL_FIELDS)}


def test_the_scan_result_statuses_are_the_documented_four() -> None:
    assert {WRITTEN, KEPT, EMPTY, MISSING} == {
        "written",
        "kept",
        "empty",
        "missing",
    }
