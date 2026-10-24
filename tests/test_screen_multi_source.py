"""P10 RED — the multi-source display: one row per stock, medians, honest fallback.

Contract (PLAN-screen.md §4 and NEXT-ACTIONS P10):

* ``GET /screens`` is a **service**, not a sample file: the twenty candidates of
  the five-minute snapshot are re-verified against Yahoo + Nasdaq + Finviz +
  Google on top of TradingView, and each cell becomes the **median** of what
  those sources published.
* **One row per stock.** There is no ``sources`` column group, no ``yahoo`` key,
  no ``nasdaq`` key: a client reads ``price`` and ``source_summary``, never one
  cell per provider. A table whose shape depends on which verifier answered is a
  schema that changes with the weather.
* ``source_summary`` is exactly
  ``{method, source_count, agreement, spread_pp, confidence, verified_at}``.
  ``source_count`` counts the sources that actually answered, ``agreement`` the
  share of change observations inside the tolerance of the median, ``spread_pp``
  the max−min change in percentage points.
* **Missing stays null.** A source with no previous close contributes no change;
  a cell nobody published is ``None``, never ``0``.
* **Cache 300 s per (region, sort, screen_id).** Two reads inside the window call
  the verifiers once and answer the same document; a read after the window (or
  against a new snapshot) verifies again.
* **Total verifier failure is not a consensus.** The document falls back to the
  raw TradingView snapshot: ``source="tradingview"``, ``degraded=true`` and a
  ``verification_error``. It must not say ``source="multi"``.

These tests drive the service directly with injected verifiers and a seeded
snapshot: no HTTP, no scanner, one property per test.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from app.config import Settings
from app.screen.multi_source import (
    AGREEMENT_TOLERANCE_PP,
    CACHE_TTL_S,
    CONSENSUS_METHOD,
    MULTI_SOURCE,
    SELECTION_SOURCE,
    CallableVerifier,
    MultiSourceScreen,
    Observation,
    build_multi_source_verifiers,
    consensus_row,
    yahoo_symbol,
)
from app.screen.repository import SqliteScreenRepository

ROWS = 20
SUMMARY_KEYS = frozenset(
    {"method", "source_count", "agreement", "spread_pp", "confidence", "verified_at"}
)
#: Fields the P10 row must carry, plus the ones the pre-P10 display already sold.
ROW_KEYS = frozenset(
    {
        "rank",
        "symbol",
        "name",
        "exchange",
        "country",
        "sector",
        "price",
        "change_pct",
        "volume",
        "turnover",
        "turnover_usd",
        "market_cap_usd",
        "is_primary",
        "as_of",
        "source_summary",
    }
)


# ---------------------------------------------------------------------------
# Helpers shared with tests/test_screen_api_multi.py
# ---------------------------------------------------------------------------


def snapshot_rows(count: int = ROWS) -> list[dict[str, Any]]:
    """Twenty screen rows, ordered by market cap, all US listings."""

    return [
        {
            "rank": index + 1,
            "symbol": f"S{index:02d}",
            "name": f"Stock {index:02d} Holdings Inc.",
            "exchange": "XNAS",
            "country": "US",
            "sector": "Technology",
            "price": 100.0 + index,
            "change_pct": 1.0,
            "volume": 1_000.0 + index,
            "turnover": 100_000.0 + index,
            "turnover_usd": 100_000.0 + index,
            "market_cap_usd": 10_000_000_000.0 - index * 100_000_000.0,
            "is_primary": True,
        }
        for index in range(count)
    ]


class Clock:
    """A settable clock: the cache TTL must be provable without sleeping."""

    def __init__(self, moment: datetime | None = None) -> None:
        self.moment = moment or datetime.now(UTC).replace(microsecond=0)

    def __call__(self) -> datetime:
        return self.moment

    def advance(self, seconds: float) -> None:
        self.moment = self.moment + timedelta(seconds=seconds)


class FakeVerifier:
    """A verifier that answers from a per-row factory and counts its calls."""

    def __init__(
        self,
        name: str,
        factory: Callable[[Mapping[str, Any]], Observation] | None = None,
        *,
        answers: Mapping[str, Observation | None] | None = None,
    ) -> None:
        self.name = name
        self.calls = 0
        self.symbols: list[str] = []
        self._factory = factory
        self._answers = dict(answers) if answers is not None else None

    async def observe(
        self, rows: Sequence[Mapping[str, Any]]
    ) -> Mapping[str, Observation | None]:
        self.calls += 1
        self.symbols = [str(row["symbol"]) for row in rows]
        if self._answers is not None:
            return {symbol: self._answers.get(symbol) for symbol in self.symbols}
        assert self._factory is not None
        return {str(row["symbol"]): self._factory(row) for row in rows}


class FailingVerifier:
    """A source that is down: it raises instead of inventing a null price."""

    def __init__(self, name: str, message: str = "connection refused") -> None:
        self.name = name
        self.message = message
        self.calls = 0

    async def observe(
        self, rows: Sequence[Mapping[str, Any]]
    ) -> Mapping[str, Observation | None]:
        self.calls += 1
        raise RuntimeError(self.message)


class EmptyVerifier:
    """A source that answers nothing: it knows none of the symbols."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.calls = 0

    async def observe(
        self, rows: Sequence[Mapping[str, Any]]
    ) -> Mapping[str, Observation | None]:
        self.calls += 1
        return {}


async def seeded_repository(
    tmp_path: Path,
    rows: Sequence[Mapping[str, Any]] | None = None,
    *,
    now: datetime | None = None,
    ttl_s: int = 3_600,
    region: str = "us",
    sort: str = "market_cap",
    total_count: int = 23,
) -> tuple[SqliteScreenRepository, dict[str, Any]]:
    """A repository holding one fresh snapshot (no scanner request).

    The TTL is an hour, not the product's 300 s: the cache tests advance the
    *service* clock past the verification window and must not accidentally expire
    the snapshot too (that would refresh it and prove a different property).
    """

    repository = SqliteScreenRepository(tmp_path / "screen-multi.db")
    moment = now or datetime.now(UTC).replace(microsecond=0)
    record = await repository.save(
        region=region,
        sort=sort,
        as_of=moment,
        total_count=total_count,
        rows=list(rows if rows is not None else snapshot_rows()),
        ttl_s=ttl_s,
        now=moment,
    )
    return repository, record


def price_verifier(name: str, delta: float = 0.0, change: float = 1.0) -> FakeVerifier:
    return FakeVerifier(
        name,
        lambda row: Observation(
            source=name,
            price=float(row["price"]) + delta,
            change_pct=change,
            volume=float(row["volume"]),
        ),
    )


# ---------------------------------------------------------------------------
# One row per stock
# ---------------------------------------------------------------------------


async def test_the_document_is_one_row_per_stock_with_no_per_source_columns(
    tmp_path: Path,
) -> None:
    repository, _screen = await seeded_repository(tmp_path)
    try:
        service = MultiSourceScreen(
            repository=repository,
            verifiers=[price_verifier("yahoo", 2.0), price_verifier("nasdaq", 4.0)],
            clock=Clock(),
        )
        document = await service.load("us", "market_cap")
    finally:
        await repository.aclose()

    assert document["source"] == MULTI_SOURCE
    assert document["selection_source"] == SELECTION_SOURCE
    assert document["degraded"] is False
    assert document["returned"] == ROWS and len(document["rows"]) == ROWS
    assert document["total"] == 23, "the universe count belongs to the snapshot"

    for row in document["rows"]:
        missing = ROW_KEYS - set(row)
        assert not missing, f"row {row.get('symbol')!r} is missing {sorted(missing)}"
        assert set(row["source_summary"]) == SUMMARY_KEYS, (
            "source_summary is a fixed six-key contract, got "
            f"{sorted(row['source_summary'])}"
        )
        assert not {"sources", "yahoo", "nasdaq", "tradingview"} & set(row), (
            "per-source columns are the thing P10 removes; a row must not carry "
            f"provider names as cells: {sorted(row)}"
        )
        nested = {
            key
            for key, value in row.items()
            if isinstance(value, Mapping) and key != "source_summary"
        }
        assert nested == set(), f"a row may only nest source_summary, got {sorted(nested)}"
        assert row["as_of"] == document["as_of"], (
            "a consensus row is dated by the snapshot it selected from; "
            "source_summary.verified_at dates the verification"
        )

    symbols = [row["symbol"] for row in document["rows"]]
    assert symbols == [f"S{index:02d}" for index in range(ROWS)], (
        "verification must not reshuffle the displayed twenty"
    )
    assert [row["rank"] for row in document["rows"]] == list(range(1, ROWS + 1))


async def test_the_summary_names_the_consensus_method_and_the_pass(
    tmp_path: Path,
) -> None:
    repository, _screen = await seeded_repository(tmp_path)
    try:
        service = MultiSourceScreen(
            repository=repository, verifiers=[price_verifier("yahoo", 2.0)], clock=Clock()
        )
        document = await service.load("us", "market_cap")
    finally:
        await repository.aclose()

    summary = document["rows"][0]["source_summary"]
    assert summary["method"] == CONSENSUS_METHOD == "multi_source_consensus"
    assert summary["verified_at"].endswith("+00:00")
    assert summary["verified_at"] == document["rows"][-1]["source_summary"]["verified_at"], (
        "one verification pass dates every row it produced"
    )


# ---------------------------------------------------------------------------
# Median and nulls
# ---------------------------------------------------------------------------


async def test_every_cell_is_the_median_of_the_answering_sources(
    tmp_path: Path,
) -> None:
    repository, _screen = await seeded_repository(tmp_path)
    try:
        service = MultiSourceScreen(
            repository=repository,
            verifiers=[price_verifier("yahoo", 2.0), price_verifier("nasdaq", 4.0)],
            clock=Clock(),
        )
        document = await service.load("us", "market_cap")
    finally:
        await repository.aclose()

    row = document["rows"][0]
    # TradingView 100, Yahoo 102, Nasdaq 104 -> the middle value, not the mean.
    assert row["price"] == pytest.approx(102.0), (
        "a median of 100/102/104 is 102; a mean (102.0) is a coincidence here — "
        "the next assertion pins a case where they differ"
    )

    skewed = FakeVerifier(
        "nasdaq",
        lambda row: Observation(source="nasdaq", price=float(row["price"]) + 20.0),
    )
    repository2, _screen2 = await seeded_repository(tmp_path / "skew")
    try:
        service2 = MultiSourceScreen(
            repository=repository2,
            verifiers=[price_verifier("yahoo", 2.0), skewed],
            clock=Clock(),
        )
        document2 = await service2.load("us", "market_cap")
    finally:
        await repository2.aclose()

    assert document2["rows"][0]["price"] == pytest.approx(102.0), (
        "100/102/120 has median 102; a mean would be 107.33 and one wild "
        "provider would move the displayed price"
    )


async def test_a_cell_no_source_published_stays_null(
    tmp_path: Path,
) -> None:
    rows = snapshot_rows()
    for row in rows:
        row["change_pct"] = None
        row["volume"] = None
        row["market_cap_usd"] = None

    repository, _screen = await seeded_repository(tmp_path, rows)
    try:
        service = MultiSourceScreen(
            repository=repository,
            verifiers=[
                FakeVerifier(
                    "yahoo", lambda row: Observation(source="yahoo", price=row["price"])
                )
            ],
            clock=Clock(),
        )
        document = await service.load("us", "market_cap")
    finally:
        await repository.aclose()

    row = document["rows"][0]
    assert row["price"] is not None
    assert row["change_pct"] is None, "a missing change is not 0%"
    assert row["volume"] is None, "a missing volume is not 0"
    assert row["market_cap_usd"] is None, "a missing cap is not a cap of zero"
    assert row["source_summary"]["source_count"] == 2


# ---------------------------------------------------------------------------
# source_count / agreement / spread
# ---------------------------------------------------------------------------


async def test_source_count_and_agreement_reflect_the_sources_that_answered(
    tmp_path: Path,
) -> None:
    repository, _screen = await seeded_repository(tmp_path)
    try:
        agreeing = MultiSourceScreen(
            repository=repository,
            verifiers=[price_verifier("yahoo", 2.0), price_verifier("nasdaq", 4.0)],
            clock=Clock(),
        )
        document = await agreeing.load("us", "market_cap")
    finally:
        await repository.aclose()

    summary = document["rows"][0]["source_summary"]
    assert summary["source_count"] == 3, (
        "TradingView + Yahoo + Nasdaq all answered, so the row stands on three "
        f"sources; got {summary['source_count']}"
    )
    assert summary["agreement"] == pytest.approx(1.0)
    assert summary["spread_pp"] == pytest.approx(0.0)
    assert summary["confidence"] == pytest.approx(3 / 5), (
        "confidence is agreement discounted by coverage of the five documented "
        "sources (TradingView + four verifiers), so three in perfect agreement "
        "is 0.6, not 1.0"
    )


async def test_a_dissenting_source_lowers_agreement_and_widens_the_spread(
    tmp_path: Path,
) -> None:
    repository, _screen = await seeded_repository(tmp_path)
    try:
        service = MultiSourceScreen(
            repository=repository,
            # TradingView 1.0%, Nasdaq 1.0%, Yahoo 5.0% -> median 1.0, spread 4 pp.
            verifiers=[price_verifier("yahoo", 2.0, change=5.0), price_verifier("nasdaq")],
            clock=Clock(),
        )
        document = await service.load("us", "market_cap")
    finally:
        await repository.aclose()

    summary = document["rows"][0]["source_summary"]
    assert summary["source_count"] == 3
    assert summary["spread_pp"] == pytest.approx(4.0), (
        "spread_pp is the max−min change in percentage points"
    )
    assert summary["agreement"] == pytest.approx(2 / 3, abs=1e-4), (
        "two of three change observations sit on the median; the dissenter is "
        f"outside {AGREEMENT_TOLERANCE_PP} pp"
    )
    assert summary["confidence"] == pytest.approx(2 / 5, abs=1e-4), (
        "two of three change observations agree, and three of the five "
        "documented sources answered: 2/3 x 3/5 = 0.4"
    )
    assert document["rows"][0]["change_pct"] == pytest.approx(1.0), (
        "the consensus change is still the median, not the dissenter"
    )


async def test_a_source_that_answers_nothing_is_not_counted(
    tmp_path: Path,
) -> None:
    repository, _screen = await seeded_repository(tmp_path)
    try:
        service = MultiSourceScreen(
            repository=repository,
            verifiers=[
                price_verifier("yahoo", 2.0),
                FakeVerifier(
                    "empty", lambda row: Observation(source="empty")  # all nulls
                ),
            ],
            clock=Clock(),
        )
        document = await service.load("us", "market_cap")
    finally:
        await repository.aclose()

    summary = document["rows"][0]["source_summary"]
    assert summary["source_count"] == 2, (
        "a source that published no number did not stand behind the row; "
        f"source_count={summary['source_count']}"
    )
    assert document["source"] == MULTI_SOURCE


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


async def test_two_reads_inside_the_window_call_the_verifiers_once(
    tmp_path: Path,
) -> None:
    clock = Clock()
    repository, screen = await seeded_repository(tmp_path, now=clock())
    yahoo, nasdaq = price_verifier("yahoo", 2.0), price_verifier("nasdaq", 4.0)
    try:
        service = MultiSourceScreen(
            repository=repository, verifiers=[yahoo, nasdaq], clock=clock
        )
        first = await service.load("us", "market_cap")
        second = await service.load("us", "market_cap")
    finally:
        await repository.aclose()

    assert yahoo.calls == 1 and nasdaq.calls == 1, (
        "the second read inside the 300 s window must send zero verifier calls, "
        f"got yahoo={yahoo.calls} nasdaq={nasdaq.calls}"
    )
    assert second["screen_id"] == screen["id"] == first["screen_id"]
    assert second["rows"] == first["rows"], "the cached document is served as-is"


async def test_the_cache_expires_after_the_window(tmp_path: Path) -> None:
    clock = Clock()
    repository, _screen = await seeded_repository(tmp_path, now=clock())
    yahoo = price_verifier("yahoo", 2.0)
    try:
        service = MultiSourceScreen(
            repository=repository, verifiers=[yahoo], clock=clock
        )
        await service.load("us", "market_cap")
        assert yahoo.calls == 1

        clock.advance(CACHE_TTL_S + 1)
        # The snapshot itself lives an hour in this fixture: only the cache expires.
        refreshed = await service.load("us", "market_cap")
    finally:
        await repository.aclose()

    assert yahoo.calls == 2, (
        f"a read {CACHE_TTL_S + 1} s later must verify again, got {yahoo.calls} call(s)"
    )
    assert refreshed["source"] == MULTI_SOURCE


async def test_the_cache_is_keyed_by_the_snapshot_id(tmp_path: Path) -> None:
    clock = Clock()
    repository, _first = await seeded_repository(tmp_path, now=clock())
    yahoo = price_verifier("yahoo", 2.0)
    try:
        service = MultiSourceScreen(
            repository=repository, verifiers=[yahoo], clock=clock
        )
        await service.load("us", "market_cap")
        assert yahoo.calls == 1

        # A new snapshot (new ids, new prices) inside the old verification window.
        second = await repository.save(
            region="us",
            sort="market_cap",
            as_of=clock().replace(microsecond=0),
            total_count=23,
            rows=snapshot_rows(),
            ttl_s=3_600,
            now=clock().replace(microsecond=0) + timedelta(seconds=1),
        )
        clock.advance(1)
        document = await service.load("us", "market_cap")
    finally:
        await repository.aclose()

    assert yahoo.calls == 2, (
        "a refreshed snapshot is different rows and must be verified, not "
        "answered from the previous screen's cached document"
    )
    assert document["screen_id"] == second["id"]


# ---------------------------------------------------------------------------
# Degraded fallback
# ---------------------------------------------------------------------------


async def test_total_verifier_failure_falls_back_to_the_raw_snapshot(
    tmp_path: Path,
) -> None:
    repository, screen = await seeded_repository(tmp_path)
    try:
        service = MultiSourceScreen(
            repository=repository,
            verifiers=[
                FailingVerifier("yahoo", "yahoo answered 503"),
                FailingVerifier("nasdaq", "nasdaq answered 429"),
            ],
            clock=Clock(),
        )
        document = await service.load("us", "market_cap")
    finally:
        await repository.aclose()

    assert document["source"] == "tradingview", (
        "an unverified page must not call itself multi-source, got "
        f"source={document['source']!r}"
    )
    assert document["source"] != MULTI_SOURCE
    assert document["selection_source"] == SELECTION_SOURCE
    assert document["degraded"] is True
    assert document["verification_error"], "a degraded page must say why"
    assert "yahoo" in document["verification_error"]
    assert "nasdaq" in document["verification_error"]
    assert document["screen_id"] == screen["id"]
    assert document["total"] == 23 and document["returned"] == ROWS
    assert "source_summary" not in document["rows"][0], (
        "the fallback is the raw snapshot: no consensus summary exists"
    )


async def test_verifiers_that_all_answer_nothing_are_also_a_failure(
    tmp_path: Path,
) -> None:
    repository, _screen = await seeded_repository(tmp_path)
    try:
        service = MultiSourceScreen(
            repository=repository,
            verifiers=[EmptyVerifier("yahoo"), EmptyVerifier("nasdaq")],
            clock=Clock(),
        )
        document = await service.load("us", "market_cap")
    finally:
        await repository.aclose()

    assert document["source"] == "tradingview"
    assert document["degraded"] is True
    assert "answered nothing" in document["verification_error"]


async def test_a_partial_outage_still_produces_a_consensus(
    tmp_path: Path,
) -> None:
    repository, _screen = await seeded_repository(tmp_path)
    try:
        service = MultiSourceScreen(
            repository=repository,
            verifiers=[FailingVerifier("nasdaq"), price_verifier("yahoo", 2.0)],
            clock=Clock(),
        )
        document = await service.load("us", "market_cap")
    finally:
        await repository.aclose()

    assert document["source"] == MULTI_SOURCE
    assert document["degraded"] is False
    assert document["verification_error"] is None
    assert document["rows"][0]["source_summary"]["source_count"] == 2, (
        "one dead source does not erase the source that did answer"
    )


# ---------------------------------------------------------------------------
# Small units: verifier contract and Yahoo symbol mapping
# ---------------------------------------------------------------------------


def test_verifier_names_are_unique_and_cannot_shadow_the_selection_leg() -> None:
    repository = object()  # never touched by the constructor
    with pytest.raises(ValueError, match="reserved"):
        MultiSourceScreen(
            repository=repository,  # type: ignore[arg-type]
            verifiers=[CallableVerifier("tradingview", lambda rows: {})],
        )
    with pytest.raises(ValueError, match="duplicate"):
        MultiSourceScreen(
            repository=repository,  # type: ignore[arg-type]
            verifiers=[
                CallableVerifier("yahoo", lambda rows: {}),
                CallableVerifier("yahoo", lambda rows: {}),
            ],
        )


async def test_a_callable_verifier_rejects_a_non_mapping_answer() -> None:
    verifier = CallableVerifier("finviz", lambda rows: [])  # type: ignore[arg-type,return-value]
    with pytest.raises(TypeError, match="must return a mapping"):
        await verifier.observe([])


def test_yahoo_symbols_are_bare_for_us_and_suffixed_for_known_venues() -> None:
    assert yahoo_symbol({"symbol": "aapl", "exchange": "XNAS"}) == "AAPL"
    assert yahoo_symbol({"symbol": "AAPL", "exchange": "NASDAQ"}) == "AAPL", (
        "the scanner prints TradingView codes; a row that bypassed the universe "
        "must still get the bare ticker"
    )
    assert yahoo_symbol({"symbol": "7203", "exchange": "TSE"}) == "7203.T"
    assert yahoo_symbol({"symbol": "2330", "exchange": "TWSE"}) == "2330.TW"
    assert yahoo_symbol({"symbol": "0700", "exchange": "HKEX"}) == "0700.HK"
    assert yahoo_symbol({"symbol": "7203", "exchange": "ZZZZ"}) is None, (
        "an unknown venue is not asked with an invented suffix"
    )
    assert yahoo_symbol({"symbol": "MC", "exchange": "EURONEXT"}) is None, (
        "EURONEXT spans four countries; one suffix could hit another company"
    )
    assert yahoo_symbol({"symbol": "BAD TICKER", "exchange": "XNAS"}) is None


def test_consensus_row_is_a_pure_function_of_its_observations() -> None:
    row = snapshot_rows(1)[0]
    legs = [
        Observation(source="tradingview", price=100.0, change_pct=-1.0, volume=10.0),
        Observation(source="yahoo", price=101.0, change_pct=-1.0, volume=10.0),
        Observation(source="nasdaq", price=99.0, volume=10.0),
    ]
    document = consensus_row(
        row, legs, as_of="2026-10-24T00:00:00+00:00", verified_at="2026-10-24T00:05:00+00:00"
    )

    assert document["price"] == pytest.approx(100.0)
    assert document["change_pct"] == pytest.approx(-1.0)
    assert document["source_summary"]["source_count"] == 3
    assert document["source_summary"]["agreement"] == pytest.approx(1.0), (
        "Nasdaq publishes no change, so agreement is measured over the two that do"
    )
    assert document["as_of"] == "2026-10-24T00:00:00+00:00"
    assert document["source_summary"]["verified_at"] == "2026-10-24T00:05:00+00:00"


# ---------------------------------------------------------------------------
# Configuration wiring
# ---------------------------------------------------------------------------


def test_the_default_verifier_set_is_the_four_live_sources(monkeypatch: Any) -> None:
    monkeypatch.delenv("MEE_SCREEN_MULTI_SOURCE", raising=False)

    verifiers = build_multi_source_verifiers(Settings())

    assert [verifier.name for verifier in verifiers] == [
        "yahoo",
        "nasdaq",
        "finviz",
        "google",
    ], (
        "the default consensus draws on Yahoo (batch), Nasdaq (US per-symbol) "
        "and the two HTML pages (P10.1), got "
        f"{[v.name for v in verifiers]}"
    )


def test_a_deployment_can_turn_the_consensus_off(monkeypatch: Any) -> None:
    monkeypatch.setenv("MEE_SCREEN_MULTI_SOURCE", "false")

    assert build_multi_source_verifiers(Settings()) == (), (
        "MEE_SCREEN_MULTI_SOURCE=false must leave the service with no verifier, "
        "which makes it answer the degraded TradingView document"
    )
