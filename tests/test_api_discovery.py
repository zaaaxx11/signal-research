"""Contract for the two market-wide reads the interface needs (plan §2, 0.4/0.5).

``GET /market/breadth`` answers "how is the market doing" from the *universe*
rather than from the <= 20 rows a page happens to show, and ``GET
/symbols/search`` answers "jump me to this ticker" from the identity snapshot.

Everything here is hermetic: a handwritten six-row universe and a handwritten
identity master. No scan, no dataset load, no socket — the suite-wide egress
guard in ``tests/conftest.py`` would fail the test if either route reached out.

The tests pin the properties the interface depends on:

* the three buckets **partition** the universe (advancing + declining +
  unchanged == universe_total), so a progress bar can be drawn without a
  residual bucket;
* an exactly-flat row is *unchanged*, never declining (the float trap);
* sector buckets partition the rows and each average is the mean of that
  sector's own rows, with a missing change excluded from the mean and still
  counted in ``count`` — and the document says so, rather than leaving a
  caller to guess;
* ``new_highs``/``new_lows`` publish the denominator they were measurable over,
  so "zero" is distinguishable from "not measured";
* search is ranked exact-symbol, symbol-prefix, name-prefix, name-contains,
  case-insensitively, bounded by ``limit`` (max 20) and honest about a missing
  identity master (503) and an unknown symbol (empty list, not 404);
* a cold scanner outage costs **one** scan per five-second window — not one per
  request, and not one per concurrent waiter — and the window expires on its
  own, so a recovered scanner is picked up rather than cached into a permanent
  503;
* every 4xx on both routes is the house ``{error, errorMessage}`` envelope, with
  a code that names the parameter, and never FastAPI's ``{"detail": …}``;
* the breadth loader is resolved per request: swapping
  ``app.state.universe_loader`` changes the answer on the next call, including
  over an entry the previous loader built;
* neither route mutates the universe or the master it read, and the second
  call does not re-scan (counted, not assumed).
"""

from __future__ import annotations

import asyncio
import copy
import time
from collections.abc import AsyncIterator, Iterable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest

from app.api import discovery
from app.api.discovery import (
    BREADTH_COLUMNS,
    IdentityIndex,
    UniverseCache,
    UniverseSnapshot,
    breadth_of,
)
from app.engine.model import Provenance, SecurityRecord
from app.engine.security import SecurityMaster
from app.engine.service import EngineService
from app.screen.scanner import FULL_SCAN_ROWS, ScanBlocked, ScanPage
from app.screen.universe import PARITY_COLUMNS

#: The one timestamp every fixture snapshot carries.
AS_OF = "2026-10-30T14:30:00+00:00"

#: Keys a breadth document must always answer, whatever else it adds.
_BREADTH_KEYS = frozenset(
    {
        "as_of",
        "universe_total",
        "advancing",
        "declining",
        "unchanged",
        "new_highs",
        "new_lows",
        "sectors",
    }
)
#: Keys one sector bucket must carry.
_SECTOR_KEYS = frozenset({"name", "avg_change_pct", "count", "advancing", "declining"})
#: Keys one search hit must carry — exactly the three the jump box renders.
_HIT_KEYS = frozenset({"symbol", "name", "exchange"})
#: The one envelope every failure on these routers speaks (02 §3.2).
_ENVELOPE_KEYS = frozenset({"error", "errorMessage"})


# ---------------------------------------------------------------------------
# Fixtures: a small universe and a small identity master, both handwritten
# ---------------------------------------------------------------------------


def _row(
    symbol: str,
    change: float | None,
    sector: str | None,
    price: float | None = None,
    high: float | None = None,
    low: float | None = None,
) -> dict[str, Any]:
    """One universe row, shaped like ``app.screen.universe.build_universe``."""

    return {
        "symbol": symbol,
        "name": f"{symbol} Corp",
        "exchange": "XNAS",
        "country": "United States",
        "sector": sector,
        "price": price,
        "change_pct": change,
        "volume": 1_000_000.0,
        "turnover": 1_000_000.0,
        "turnover_usd": 1_000_000.0,
        "market_cap_usd": 1_000_000_000.0,
        "currency": "USD",
        "is_primary": True,
        "high_52w": high,
        "low_52w": low,
    }


def _universe(rows: Iterable[tuple[Any, ...]]) -> list[dict[str, Any]]:
    """Turn compact tuples into rows; keeps each fixture readable in one screen."""

    return [_row(*row) for row in rows]


def _snapshot(rows: Sequence[Mapping[str, Any]], source: str = "fixture") -> UniverseSnapshot:
    return UniverseSnapshot(rows=tuple(rows), as_of=AS_OF, source=source)


#: 3 up / 2 down / 1 flat across two sectors, with both 52-week extremes touched.
_BASE_ROWS: tuple[tuple[Any, ...], ...] = (
    ("AAA", 2.5, "Technology", 110.0, 110.0, 40.0),
    ("BBB", 1.0, "Technology", 50.0, 80.0, 10.0),
    ("CCC", 0.5, "Finance", 20.0, 25.0, 20.0),
    ("DDD", -1.5, "Finance", 30.0, 40.0, 12.0),
    ("EEE", -0.25, "Finance", 15.0, 20.0, 5.0),
    ("FFF", 0.0, "Technology", 60.0, 70.0, 10.0),
)
_UNIVERSE = _universe(_BASE_ROWS)


def _loader(rows: Sequence[Mapping[str, Any]]):
    """A loader that records every call it receives (the scan counter)."""

    calls: list[tuple[str, str]] = []

    async def load(region: str, asset_class: str) -> UniverseSnapshot:
        calls.append((region, asset_class))
        return _snapshot(rows)

    return load, calls


@asynccontextmanager
async def _client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple[Any, Any]]:
    """An ASGI client over the real app, plus the app for state injection."""

    monkeypatch.setenv("MEE_DB_PATH", str(tmp_path / "mee.db"))
    monkeypatch.setenv("MEE_EXPORT_DIR", str(tmp_path / "exports"))

    from httpx2 import ASGITransport, AsyncClient

    from app.main import create_app

    application = create_app()
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://mee.test") as client:
        yield client, application


def _sector(body: Mapping[str, Any], name: str | None) -> Mapping[str, Any]:
    """The one sector bucket named ``name`` (``None`` is the unlabelled bucket)."""

    buckets = [bucket for bucket in body["sectors"] if bucket["name"] == name]
    assert len(buckets) == 1, f"expected exactly one {name!r} bucket, got {buckets!r}"
    return buckets[0]


def _house(body: Mapping[str, Any]) -> None:
    """Assert ``body`` is the one envelope this API speaks, and nothing else.

    A framework-shaped ``{"detail": [...]}`` is the failure mode: it is a 422
    that a client written against ``{error, errorMessage}`` cannot read.
    """

    assert set(body) == _ENVELOPE_KEYS, f"not the house envelope: {body!r}"
    assert isinstance(body["error"], str) and body["error"]
    assert isinstance(body["errorMessage"], str) and body["errorMessage"].strip()


# ---------------------------------------------------------------------------
# §1 Breadth: the counts
# ---------------------------------------------------------------------------


def test_breadth_partitions_a_six_row_universe() -> None:
    """3 up, 2 down, 1 flat — and the three buckets add up to the universe."""

    body = breadth_of(
        _UNIVERSE, as_of=AS_OF, source="fixture", region="us", asset_class="stock"
    )

    assert body["universe_total"] == 6
    assert (body["advancing"], body["declining"], body["unchanged"]) == (3, 2, 1)
    assert body["advancing"] + body["declining"] + body["unchanged"] == body["universe_total"]


@pytest.mark.parametrize("flat", [0.0, -0.0, 0, "0.00"])
def test_breadth_counts_an_exactly_flat_row_as_unchanged(flat: object) -> None:
    """The float trap: ``0.0`` is not ``< 0``; ``-0.0`` and ``"0.00"`` are not either."""

    rows = [
        _row("UP", 1.25, "Technology"),
        _row("DOWN", -0.5, "Technology"),
        _row("FLAT", flat, "Technology"),  # type: ignore[arg-type]
    ]

    body = breadth_of(rows, as_of=AS_OF, source="fixture", region="us", asset_class="stock")

    assert body["unchanged"] == 1
    assert body["declining"] == 1
    assert body["advancing"] == 1
    assert body["unchanged_reported"] == 1
    assert body["unchanged_missing"] == 0


def test_breadth_counts_a_missing_change_as_unchanged_and_says_so() -> None:
    """A row with no ``change_pct`` stays in the partition, never as a decline."""

    rows = [
        _row("A", None, "Technology"),
        _row("B", None, "Technology"),
        _row("C", 2.0, "Technology"),
    ]

    body = breadth_of(rows, as_of=AS_OF, source="fixture", region="us", asset_class="stock")

    assert (body["advancing"], body["declining"], body["unchanged"]) == (1, 0, 2)
    assert body["unchanged_missing"] == 2
    assert body["unchanged_reported"] == 0
    assert "absent" in body["method"]["unchanged"]


def test_breadth_sectors_partition_the_rows_and_average_their_own_rows() -> None:
    """Sector counts sum to the universe; each mean is over that sector's rows."""

    body = breadth_of(
        _UNIVERSE, as_of=AS_OF, source="fixture", region="us", asset_class="stock"
    )

    assert sum(bucket["count"] for bucket in body["sectors"]) == body["universe_total"]

    technology = _sector(body, "Technology")
    finance = _sector(body, "Finance")
    assert technology["count"] == 3
    assert technology["avg_change_pct"] == pytest.approx((2.5 + 1.0 + 0.0) / 3)
    assert (technology["advancing"], technology["declining"]) == (2, 0)
    assert finance["count"] == 3
    assert finance["avg_change_pct"] == pytest.approx((0.5 - 1.5 - 0.25) / 3)
    assert (finance["advancing"], finance["declining"]) == (1, 2)


def test_breadth_sector_average_excludes_a_null_change_instead_of_poisoning_it() -> None:
    """A null change is excluded from the mean, kept in ``count``, and stated."""

    rows = [
        _row("A", 2.0, "Energy"),
        _row("B", None, "Energy"),
        _row("C", None, "Utilities"),
    ]

    body = breadth_of(rows, as_of=AS_OF, source="fixture", region="us", asset_class="stock")

    energy = _sector(body, "Energy")
    assert energy["count"] == 2
    assert energy["avg_change_pct"] == pytest.approx(2.0)
    utilities = _sector(body, "Utilities")
    assert utilities["count"] == 1
    assert utilities["avg_change_pct"] is None
    assert "excluded" in body["method"]["sector_avg_change_pct"]


def test_breadth_publishes_the_denominator_behind_the_52_week_counts() -> None:
    """Only rows carrying both a price and the extreme are measurable."""

    rows = [
        _row("AT_HIGH", 1.0, "Technology", price=100.0, high=100.0, low=50.0),
        _row("BELOW_HIGH", 1.0, "Technology", price=99.0, high=100.0, low=50.0),
        _row("AT_LOW", -1.0, "Technology", price=50.0, high=100.0, low=50.0),
        _row("NO_EXTREMES", 1.0, "Technology", price=10.0),
        _row("NO_PRICE", 1.0, "Technology", high=5.0, low=1.0),
    ]

    body = breadth_of(rows, as_of=AS_OF, source="fixture", region="us", asset_class="stock")

    assert body["new_highs"] == 1
    assert body["new_lows"] == 1
    assert body["new_highs_denominator"] == 3
    assert body["new_lows_denominator"] == 3
    assert body["universe_total"] == 5


def test_breadth_keeps_unlabelled_rows_in_a_null_sector_bucket() -> None:
    """A missing or blank sector is one bucket named ``None``, not a dropped row."""

    rows = [
        _row("A", 1.0, None),
        _row("B", 1.0, "   "),
        _row("C", 1.0, "Technology"),
    ]

    body = breadth_of(rows, as_of=AS_OF, source="fixture", region="us", asset_class="stock")

    assert sum(bucket["count"] for bucket in body["sectors"]) == 3
    unlabelled = _sector(body, None)
    assert unlabelled["count"] == 2
    assert unlabelled["avg_change_pct"] == pytest.approx(1.0)
    assert body["sector_missing"] == 2
    # The unlabelled bucket sorts last so a UI can render it as the tail.
    assert body["sectors"][-1]["name"] is None


def test_breadth_document_states_its_own_shape_and_provenance() -> None:
    """Required keys always exist, the echo is honest, and sectors are complete."""

    body = breadth_of(
        _UNIVERSE, as_of=AS_OF, source="scan:us:stock", region="us", asset_class="stock"
    )

    assert _BREADTH_KEYS <= set(body)
    assert body["as_of"] == AS_OF
    assert body["source"] == "scan:us:stock"
    assert body["region"] == "us"
    assert body["asset_class"] == "stock"
    assert body["stale"] is False
    assert body["sectors"]
    for bucket in body["sectors"]:
        assert set(bucket) == _SECTOR_KEYS


# ---------------------------------------------------------------------------
# §2 Breadth: the route, its cache and its failure modes
# ---------------------------------------------------------------------------


def test_breadth_scan_columns_ask_for_the_extremes_and_not_the_parity_payload() -> None:
    """The scan asks for the 17 cells breadth reads, not the 70-column parity set.

    ``build_universe`` copies a parity field only when its *provider* column was
    requested, so the extremes have to be named here or every row would answer
    ``null`` for them and new highs would silently be zero.
    """

    assert "price_52_week_high" in BREADTH_COLUMNS
    assert "price_52_week_low" in BREADTH_COLUMNS
    assert "sector" in BREADTH_COLUMNS
    assert len(BREADTH_COLUMNS) < len(PARITY_COLUMNS)


async def test_breadth_scans_one_wide_page_with_no_cap_or_subtype_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One request, no floor, no ``subtype=common`` gate: the screener's universe."""

    calls: list[tuple[str, Mapping[str, Any]]] = []

    async def fake_fetch(region: str, **kwargs: Any) -> ScanPage:
        calls.append((region, kwargs))
        return ScanPage(
            total_count=1,
            received=1,
            rows=[
                {
                    "symbol": "AAPL",
                    "name": "Apple Inc.",
                    "description": "APPLE INC.",
                    "exchange": "NASDAQ",
                    "country": "United States",
                    "sector": "Electronic Technology",
                    "type": "stock",
                    "subtype": "common",
                    "currency": "USD",
                    "close": 180.0,
                    "change": 1.2,
                    "volume": 50_000_000,
                    "Value.Traded": 9e9,
                    "market_cap_basic": 2.8e12,
                    "price_52_week_high": 200.0,
                    "price_52_week_low": 120.0,
                }
            ],
        )

    monkeypatch.setattr(discovery, "fetch_scan", fake_fetch)
    snapshot = await discovery.scan_universe("us", "stock")

    assert len(calls) == 1, f"the loader scanned {len(calls)} times"
    region, kwargs = calls[0]
    assert region == "us"
    assert kwargs["min_market_cap"] is None
    assert kwargs["subtype"] is None
    assert kwargs["limit"] == FULL_SCAN_ROWS
    assert kwargs["asset_class"] == "stock"

    (row,) = snapshot.rows
    assert row["symbol"] == "AAPL"
    assert row["exchange"] == "XNAS"
    assert row["sector"] == "Electronic Technology"
    assert row["high_52w"] == 200.0
    assert row["low_52w"] == 120.0
    assert snapshot.source == "scan:us:stock"
    assert snapshot.as_of


async def test_breadth_route_measures_once_and_caches_the_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two requests, one scan: the counter is the proof, not the latency."""

    load, calls = _loader(_UNIVERSE)
    async with _client(tmp_path, monkeypatch) as (client, application):
        application.state.universe_loader = load
        first = await client.get("/market/breadth")
        second = await client.get("/market/breadth")

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert len(calls) == 1, f"the scan ran {len(calls)} times: {calls}"
    assert calls == [("us", "stock")]
    assert first.json() == second.json()


async def test_breadth_route_does_not_mutate_the_universe_it_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The rows the loader owns are byte-identical after two measurements."""

    rows = _universe(_BASE_ROWS)
    load, _calls = _loader(rows)
    before = copy.deepcopy(rows)
    async with _client(tmp_path, monkeypatch) as (client, application):
        application.state.universe_loader = load
        first = await client.get("/market/breadth")
        second = await client.get("/market/breadth")

    assert first.json() == second.json()
    assert rows == before, "breadth wrote into the universe rows it was given"


async def test_breadth_cache_rebuilds_after_its_ttl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The TTL is a clock decision: 59 s reuses, 61 s rescans."""

    load, calls = _loader(_UNIVERSE)
    now = [1000.0]
    async with _client(tmp_path, monkeypatch) as (client, application):
        application.state.universe_loader = load
        application.state.breadth_cache = UniverseCache(ttl_s=60.0, clock=lambda: now[0])
        assert (await client.get("/market/breadth")).status_code == 200
        now[0] += 59.0
        assert (await client.get("/market/breadth")).status_code == 200
        assert len(calls) == 1
        now[0] += 2.0
        assert (await client.get("/market/breadth")).status_code == 200

    assert len(calls) == 2, f"expected a rescan after the TTL, got {len(calls)} scans"


async def test_breadth_cold_requests_share_one_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two panels opening at once must not send two scans to a bot-walled endpoint."""

    calls: list[int] = []
    release = asyncio.Event()

    async def load(region: str, asset_class: str) -> UniverseSnapshot:
        calls.append(1)
        await release.wait()
        return _snapshot(_UNIVERSE)

    async with _client(tmp_path, monkeypatch) as (client, application):
        application.state.universe_loader = load
        first = asyncio.create_task(client.get("/market/breadth"))
        second = asyncio.create_task(client.get("/market/breadth"))
        # Let both requests reach the cache before the first scan is released.
        await asyncio.sleep(0.05)
        release.set()
        responses = await asyncio.gather(first, second)

    assert [response.status_code for response in responses] == [200, 200]
    assert len(calls) == 1, f"a cold entry was scanned {len(calls)} times"


async def test_breadth_serves_the_last_snapshot_when_a_refresh_is_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bot wall must not blank the panel — nor turn every request into a scan."""

    calls: list[int] = []
    now = [1000.0]

    async def load(region: str, asset_class: str) -> UniverseSnapshot:
        calls.append(1)
        if len(calls) > 1:
            raise ScanBlocked("scanner answered 200 with an HTML body")
        return _snapshot(_UNIVERSE)

    async with _client(tmp_path, monkeypatch) as (client, application):
        application.state.universe_loader = load
        application.state.breadth_cache = UniverseCache(
            ttl_s=60.0, clock=lambda: now[0]
        )
        fresh = await client.get("/market/breadth")
        now[0] += 61.0
        blocked = await client.get("/market/breadth")
        again = await client.get("/market/breadth")

    assert fresh.status_code == 200, fresh.text
    assert fresh.json()["stale"] is False
    assert blocked.status_code == 200, blocked.text
    assert blocked.json()["stale"] is True
    assert blocked.json()["as_of"] == fresh.json()["as_of"]
    assert blocked.json()["universe_total"] == fresh.json()["universe_total"]
    # Inside the deferred window the same stale answer is served, with no scan.
    assert again.json()["stale"] is True
    assert len(calls) == 2, f"the outage caused {len(calls)} scans"


async def test_breadth_answers_503_when_the_first_scan_is_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing measured yet is an error, never an empty market."""

    async def load(region: str, asset_class: str) -> UniverseSnapshot:
        raise ScanBlocked("scanner answered 200 with an HTML body")

    async with _client(tmp_path, monkeypatch) as (client, application):
        application.state.universe_loader = load
        response = await client.get("/market/breadth")

    assert response.status_code == 503
    body = response.json()
    assert body["error"] == "scanner_unavailable"
    assert "html" in body["errorMessage"].lower()


@pytest.mark.parametrize(
    ("query", "expected_family"),
    [
        ("", "stock"),
        ("?region=us&asset_class=stock", "stock"),
        ("?region=us_etf", "etf"),
        ("?region=us&asset_class=etf", "etf"),
    ],
)
async def test_breadth_resolves_the_universe_family_before_it_scans(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    query: str,
    expected_family: str,
) -> None:
    """The scan is asked for the family the region implies, not guessed later."""

    load, calls = _loader(_UNIVERSE)
    async with _client(tmp_path, monkeypatch) as (client, application):
        application.state.universe_loader = load
        response = await client.get(f"/market/breadth{query}")

    assert response.status_code == 200, response.text
    assert response.json()["asset_class"] == expected_family
    assert calls[-1][1] == expected_family


async def test_breadth_rejects_an_impossible_asset_class(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``global`` is not an ETF universe: 422 with the house envelope."""

    load, calls = _loader(_UNIVERSE)
    async with _client(tmp_path, monkeypatch) as (client, application):
        application.state.universe_loader = load
        response = await client.get("/market/breadth?region=global&asset_class=etf")

    assert response.status_code == 422
    body = response.json()
    _house(body)
    assert body["error"] == "asset_class_mismatch"
    assert "ETF" in body["errorMessage"]
    assert calls == [], "an impossible request must be refused before any scan"


#: Every refusal ``/market/breadth`` can answer, and the code it must carry.
#: A blank or wrongly-cased value is refused, not coerced: the vocabulary is
#: closed, and widening it silently would be a different bug than F12.
_BREADTH_REFUSALS: tuple[tuple[str, str], ...] = (
    ("?region=eu", "invalid_region"),
    ("?region=", "invalid_region"),
    ("?region=US", "invalid_region"),
    ("?asset_class=bond", "invalid_asset_class"),
    ("?asset_class=", "invalid_asset_class"),
    ("?region=global&asset_class=etf", "asset_class_mismatch"),
)


@pytest.mark.parametrize(("query", "code"), _BREADTH_REFUSALS)
async def test_breadth_query_errors_use_the_house_envelope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    query: str,
    code: str,
) -> None:
    """A bad query is ``{error, errorMessage}``, never FastAPI's ``{"detail": …}``."""

    load, calls = _loader(_UNIVERSE)
    async with _client(tmp_path, monkeypatch) as (client, application):
        application.state.universe_loader = load
        response = await client.get(f"/market/breadth{query}")

    assert response.status_code == 422, response.text
    body = response.json()
    _house(body)
    assert body["error"] == code
    assert calls == [], "a refused query must not scan"


# ---------------------------------------------------------------------------
# §2b A cold outage costs one scan, not one per request (F10)
# ---------------------------------------------------------------------------


async def test_breadth_cold_outage_costs_one_scan_for_twenty_requests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """20 requests spread over 4.56 s of a 5 s outage: exactly one scan.

    The window is the shipped default (only the clock is driven, so the test is
    deterministic); each request is 0.24 s of wall time later, so the twenty
    requests really do span the outage rather than arriving in one millisecond.
    Before the negative cache each request bought its own scan of the universe.
    """

    calls: list[tuple[str, str]] = []
    now = [1000.0]

    async def load(region: str, asset_class: str) -> UniverseSnapshot:
        calls.append((region, asset_class))
        raise ScanBlocked("scanner answered 200 with an HTML body")

    async with _client(tmp_path, monkeypatch) as (client, application):
        application.state.universe_loader = load
        application.state.breadth_cache = UniverseCache(clock=lambda: now[0])
        responses = []
        for _ in range(20):
            responses.append(await client.get("/market/breadth"))
            now[0] += 0.24  # 19 steps: 4.56 s of the 5 s window
        scans_for_us = list(calls)
        # A refusal belongs to the universe that failed, not to the route: the
        # other family is still allowed its own single attempt.
        other = await client.get("/market/breadth?region=global")

    assert [response.status_code for response in responses] == [503] * 20
    for response in responses:
        assert response.json()["error"] == "scanner_unavailable"
    assert len(scans_for_us) == 1, f"20 requests in one outage caused {len(scans_for_us)} scans"
    assert scans_for_us == [("us", "stock")]
    assert other.status_code == 503
    assert len(calls) == 2, f"the refusal leaked across universes: {calls}"


async def test_breadth_concurrent_cold_failures_share_one_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Five waiters behind one failed scan: one attempt, five refusals."""

    calls: list[int] = []
    release = asyncio.Event()

    async def load(region: str, asset_class: str) -> UniverseSnapshot:
        calls.append(1)
        await release.wait()
        raise ScanBlocked("scanner answered 200 with an HTML body")

    async with _client(tmp_path, monkeypatch) as (client, application):
        application.state.universe_loader = load
        tasks = [asyncio.create_task(client.get("/market/breadth")) for _ in range(5)]
        # Let all five reach the cache before the first attempt fails.
        await asyncio.sleep(0.05)
        release.set()
        responses = await asyncio.gather(*tasks)

    assert [response.status_code for response in responses] == [503] * 5
    assert len(calls) == 1, f"one outage, {len(calls)} scans for five concurrent callers"


async def test_breadth_backoff_defers_then_lets_a_recovered_scanner_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The window expires on its own: 4.9 s defers, 5.1 s rescans and recovers.

    A negative cache that also caches a transient failure would be worse than
    none, so the window is proved from both sides: inside it no second scan,
    outside it the next request is a real attempt and its success is served.
    """

    calls: list[float] = []
    now = [1000.0]

    async def load(region: str, asset_class: str) -> UniverseSnapshot:
        calls.append(now[0])
        if len(calls) == 1:
            raise ScanBlocked("scanner answered 200 with an HTML body")
        return _snapshot(_UNIVERSE, source="scan:us:stock")

    async with _client(tmp_path, monkeypatch) as (client, application):
        application.state.universe_loader = load
        application.state.breadth_cache = UniverseCache(clock=lambda: now[0])
        first = await client.get("/market/breadth")
        now[0] += 4.9
        deferred = await client.get("/market/breadth")
        now[0] += 0.2  # 1005.1: past the window
        recovered = await client.get("/market/breadth")

    assert first.status_code == 503
    assert deferred.status_code == 503
    assert "scanner" in first.json()["errorMessage"]
    assert "without a new scan" in deferred.json()["errorMessage"]
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["universe_total"] == len(_UNIVERSE)
    assert recovered.json()["stale"] is False
    assert len(calls) == 2, f"expected one attempt per window, got {len(calls)}: {calls}"


async def test_breadth_caches_an_empty_universe_as_a_measurement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A scan that found nothing is a measurement, never a cached failure.

    An empty row set is what a genuine quiet universe looks like, so it is
    stored, served, and later served *stale* when a refresh is blocked — the
    negative cache must not reach for it.
    """

    calls: list[int] = []
    now = [1000.0]
    blocked = [False]

    async def load(region: str, asset_class: str) -> UniverseSnapshot:
        calls.append(1)
        if blocked[0]:
            raise ScanBlocked("scanner answered 200 with an HTML body")
        return _snapshot((), source="scan:us:stock")

    async with _client(tmp_path, monkeypatch) as (client, application):
        application.state.universe_loader = load
        application.state.breadth_cache = UniverseCache(clock=lambda: now[0])
        first = await client.get("/market/breadth")
        second = await client.get("/market/breadth")
        scans_while_warm = len(calls)
        now[0] += 61.0
        blocked[0] = True
        warm = await client.get("/market/breadth")

    assert first.status_code == 200, first.text
    assert first.json()["universe_total"] == 0
    assert second.status_code == 200 and second.json()["universe_total"] == 0
    assert scans_while_warm == 1, f"an empty measurement was rescanned ({scans_while_warm} scans)"
    assert warm.status_code == 200, warm.text
    assert warm.json()["stale"] is True
    assert warm.json()["universe_total"] == 0
    assert len(calls) == 2


async def test_breadth_refusal_does_not_outlive_the_loader_it_belongs_to(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A walled source is not evidence against the source that replaced it.

    The window belongs to ``(universe, loader)``: swapping in a healthy loader
    inside the window must scan at once, or a deploy that fixes the scanner
    would wait out an outage it no longer has.
    """

    healthy, calls_healthy = _loader(_UNIVERSE)
    calls_blocked: list[tuple[str, str]] = []

    async def walled(region: str, asset_class: str) -> UniverseSnapshot:
        calls_blocked.append((region, asset_class))
        raise ScanBlocked("scanner answered 200 with an HTML body")

    async with _client(tmp_path, monkeypatch) as (client, application):
        application.state.universe_loader = walled
        refused = await client.get("/market/breadth")
        application.state.universe_loader = healthy  # same window, new source
        recovered = await client.get("/market/breadth")

    assert refused.status_code == 503
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["universe_total"] == len(_UNIVERSE)
    assert calls_blocked == [("us", "stock")]
    assert calls_healthy == [("us", "stock")]


# ---------------------------------------------------------------------------
# §2c The loader is resolved per request, or the docstring lies (F13)
# ---------------------------------------------------------------------------


async def test_breadth_obeys_a_loader_swapped_on_the_running_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Swap ``app.state.universe_loader`` on a warm cache and the answer changes.

    The first loader has already built an entry, so this is the hard case: a
    cache that captured its loader at construction keeps serving rows the
    swapped source never produced.
    """

    load_a, calls_a = _loader(
        _universe((("AAA", 1.0, "Technology"), ("BBB", -1.0, "Technology")))
    )
    load_b, calls_b = _loader(
        _universe(
            (
                ("CCC", 1.0, "Finance"),
                ("DDD", 2.0, "Finance"),
                ("EEE", -3.0, "Finance"),
            )
        )
    )

    async with _client(tmp_path, monkeypatch) as (client, application):
        application.state.universe_loader = load_a
        before = await client.get("/market/breadth")
        application.state.universe_loader = load_b  # same app, same cache
        after = await client.get("/market/breadth")

    assert before.status_code == 200, before.text
    assert before.json()["universe_total"] == 2
    assert after.status_code == 200, after.text
    assert after.json()["universe_total"] == 3, "the cache served the previous loader's rows"
    assert {bucket["name"] for bucket in after.json()["sectors"]} == {"Finance"}
    assert calls_a == [("us", "stock")]
    assert calls_b == [("us", "stock")]


# ---------------------------------------------------------------------------
# §3 Search: ranking, matching and bounds
# ---------------------------------------------------------------------------

#: ``symbol, name, exchange label, MIC`` — the identity shape, in miniature.
_IDENTITY_ROWS: tuple[tuple[str, str, str, str], ...] = (
    ("AAPL", "Apple Inc.", "Nasdaq", "XNAS"),
    ("APLE", "Apple Hospitality REIT, Inc.", "NYSE", "XNYS"),
    ("APPF", "AppFolio, Inc.", "Nasdaq", "XNAS"),
    ("MSFT", "Microsoft Corp", "Nasdaq", "XNAS"),
    ("OTCX", "OTC Example Corp", "OTC", "OTCM"),
    ("ZZAP", "Zen App Corp", "Nasdaq", "XNAS"),
    ("ZÜR", "Zürich Instruments AG", "NYSE", "XNYS"),
)


def _record(symbol: str, name: str, exchange: str, mic: str) -> SecurityRecord:
    return SecurityRecord(
        symbol=symbol,
        name=name,
        cik=None,
        exchange=exchange,
        mic=mic,
        asset_class="equity",
        currency="USD",
        country="United States",
        sector=None,
        industry=None,
    )


def _master(rows: Sequence[tuple[str, str, str, str]] = _IDENTITY_ROWS) -> SecurityMaster:
    """A handwritten identity master: the authoritative symbol list, no depth."""

    master = SecurityMaster()
    master.load(
        [_record(*row) for row in rows],
        Provenance(source="sec", as_of="2026-10-20"),
    )
    return master


class _CountingMaster(SecurityMaster):
    """A master that counts how often its records are projected for search."""

    def __init__(self) -> None:
        super().__init__()
        self.records_calls = 0

    def records(self):  # type: ignore[no-untyped-def]
        self.records_calls += 1
        return super().records()


def _counting_master(rows: Sequence[tuple[str, str, str, str]] = _IDENTITY_ROWS) -> _CountingMaster:
    master = _CountingMaster()
    master.load([_record(*row) for row in rows], Provenance(source="sec", as_of="2026-10-20"))
    return master


def _service(master: SecurityMaster) -> EngineService:
    return EngineService(security_master=master)


def _symbols(hits: Sequence[Mapping[str, Any]]) -> list[str]:
    return [hit["symbol"] for hit in hits]


async def test_search_ranks_the_exact_symbol_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``q=aapl`` answers AAPL, not the companies whose name merely starts with it."""

    async with _client(tmp_path, monkeypatch) as (client, application):
        application.state.engine = _service(_master())
        response = await client.get("/symbols/search?q=aapl")

    assert response.status_code == 200, response.text
    hits = response.json()
    assert _symbols(hits)[0] == "AAPL"


async def test_search_ranks_symbol_prefix_then_name_prefix_then_name_contains(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One ordered tier pass: APPF (symbol prefix) beats the Apples, ZZAP is last."""

    async with _client(tmp_path, monkeypatch) as (client, application):
        application.state.engine = _service(_master())
        response = await client.get("/symbols/search?q=app")

    assert response.status_code == 200, response.text
    assert _symbols(response.json()) == ["APPF", "AAPL", "APLE", "ZZAP"]


async def test_search_finds_a_partial_company_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A name fragment finds the company whose ticker the user does not know."""

    async with _client(tmp_path, monkeypatch) as (client, application):
        application.state.engine = _service(_master())
        response = await client.get("/symbols/search?q=micros")

    assert response.status_code == 200, response.text
    hits = response.json()
    assert _symbols(hits) == ["MSFT"]
    assert hits[0]["name"] == "Microsoft Corp"


async def test_search_is_case_insensitive_and_matches_non_ascii(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``AaPl`` and ``zür`` both land; case folding is not ASCII-only."""

    async with _client(tmp_path, monkeypatch) as (client, application):
        application.state.engine = _service(_master())
        mixed = await client.get("/symbols/search?q=AaPl")
        folded = await client.get("/symbols/search?q=z%C3%BCr")
        shouted = await client.get("/symbols/search?q=Z%C3%9CR")

    assert _symbols(mixed.json())[0] == "AAPL"
    assert _symbols(folded.json()) == ["ZÜR"]
    assert _symbols(shouted.json()) == ["ZÜR"]


async def test_search_returns_only_the_three_identity_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The jump box renders symbol, name and venue — and nothing it must filter."""

    async with _client(tmp_path, monkeypatch) as (client, application):
        application.state.engine = _service(_master())
        response = await client.get("/symbols/search?q=msft")

    assert response.status_code == 200, response.text
    (hit,) = response.json()
    assert set(hit) == _HIT_KEYS
    assert hit == {"symbol": "MSFT", "name": "Microsoft Corp", "exchange": "XNAS"}


async def test_search_bounds_a_single_character_query_to_the_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One character matches thousands; the answer is still a page, not a dump."""

    async with _client(tmp_path, monkeypatch) as (client, application):
        application.state.engine = _service(_master())
        response = await client.get("/symbols/search?q=a&limit=2")

    assert response.status_code == 200, response.text
    assert len(response.json()) == 2


async def test_search_clamps_the_limit_at_twenty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``limit=999`` cannot turn a lookup into a universe dump."""

    rows = tuple(
        (f"APP{index:02d}", f"App Test {index}", "Nasdaq", "XNAS") for index in range(30)
    )
    async with _client(tmp_path, monkeypatch) as (client, application):
        application.state.engine = _service(_master(rows))
        response = await client.get("/symbols/search?q=app&limit=999")

    assert response.status_code == 200, response.text
    assert len(response.json()) == 20


async def test_search_defaults_to_ten_hits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without ``limit`` the page is ten: enough to choose from, small enough to read."""

    rows = tuple(
        (f"APP{index:02d}", f"App Test {index}", "Nasdaq", "XNAS") for index in range(30)
    )
    async with _client(tmp_path, monkeypatch) as (client, application):
        application.state.engine = _service(_master(rows))
        response = await client.get("/symbols/search?q=app")

    assert response.status_code == 200, response.text
    assert len(response.json()) == 10


async def test_search_returns_an_empty_list_for_an_unknown_symbol(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A symbol nobody lists is ``[]`` with a 200, not a 404 on a search route."""

    async with _client(tmp_path, monkeypatch) as (client, application):
        application.state.engine = _service(_master())
        response = await client.get("/symbols/search?q=NOTATICKER")

    assert response.status_code == 200, response.text
    assert response.json() == []


@pytest.mark.parametrize("query", ["", "?q=", "?q=%20", "?q=%20%20%20", "?q=%09"])
async def test_search_rejects_a_blank_query_with_a_clear_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, query: str
) -> None:
    """Missing, empty, a space or a tab: one 422 that names ``q``."""

    async with _client(tmp_path, monkeypatch) as (client, application):
        application.state.engine = _service(_master())
        response = await client.get(f"/symbols/search{query}")

    assert response.status_code == 422
    body = response.json()
    assert body["error"] == "invalid_query"
    assert "q" in body["errorMessage"]
    assert "character" in body["errorMessage"]


async def test_search_rejects_a_non_positive_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A page of zero rows is a refusal, not a silently empty search."""

    async with _client(tmp_path, monkeypatch) as (client, application):
        application.state.engine = _service(_master())
        response = await client.get("/symbols/search?q=app&limit=0")

    assert response.status_code == 422
    assert response.json()["error"] == "invalid_limit"


#: Every refusal ``/symbols/search`` can answer, and the code it must carry.
#: ``q`` is validated first, so the limit and region rows keep a usable query.
_SEARCH_REFUSALS: tuple[tuple[str, str], ...] = (
    ("", "invalid_query"),
    ("?q=", "invalid_query"),
    ("?q=%20%20", "invalid_query"),
    ("?q=%09", "invalid_query"),
    ("?q=app&limit=abc", "invalid_limit"),
    ("?q=app&limit=0", "invalid_limit"),
    ("?q=app&limit=-3", "invalid_limit"),
    ("?q=app&limit=2.5", "invalid_limit"),
    ("?q=app&limit=", "invalid_limit"),
    ("?q=app&region=eu", "invalid_region"),
    ("?q=app&region=", "invalid_region"),
)


@pytest.mark.parametrize(("query", "code"), _SEARCH_REFUSALS)
async def test_search_query_errors_use_the_house_envelope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    query: str,
    code: str,
) -> None:
    """``?limit=abc`` and ``?region=eu`` are the house 422, not a framework one."""

    async with _client(tmp_path, monkeypatch) as (client, application):
        application.state.engine = _service(_master())
        response = await client.get(f"/symbols/search{query}")

    assert response.status_code == 422, response.text
    body = response.json()
    _house(body)
    assert body["error"] == code


async def test_search_answers_503_without_an_identity_master(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No master loaded is "search is unavailable", not "no such symbol"."""

    async with _client(tmp_path, monkeypatch) as (client, application):
        application.state.engine = EngineService()
        response = await client.get("/symbols/search?q=app")

    assert response.status_code == 503
    assert response.json()["error"] == "identity_unavailable"


async def test_search_scopes_region_us_to_us_venues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The US scope is the screener's venues: an OTC listing is out of it."""

    async with _client(tmp_path, monkeypatch) as (client, application):
        application.state.engine = _service(_master())
        us = await client.get("/symbols/search?q=otcx")
        global_scope = await client.get("/symbols/search?q=otcx&region=global")

    assert _symbols(us.json()) == []
    assert _symbols(global_scope.json()) == ["OTCX"]


async def test_search_degrades_cleanly_on_non_ascii_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A query in another script answers an empty page, never a 500."""

    async with _client(tmp_path, monkeypatch) as (client, application):
        application.state.engine = _service(_master())
        response = await client.get("/symbols/search?q=%F0%9F%9A%80")

    assert response.status_code == 200, response.text
    assert response.json() == []


async def test_search_projects_the_master_once_per_master(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The index is built once, then reused: two requests, one projection."""

    master = _counting_master()
    async with _client(tmp_path, monkeypatch) as (client, application):
        application.state.engine = _service(master)
        first = await client.get("/symbols/search?q=app")
        second = await client.get("/symbols/search?q=aapl")

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert master.records_calls == 1, f"projected {master.records_calls} times"


async def test_search_does_not_mutate_the_identity_master(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The master is read-only to search: symbols, names and venues are unchanged."""

    master = _master()
    before = [asdict(record) for record in master.records()]
    async with _client(tmp_path, monkeypatch) as (client, application):
        application.state.engine = _service(master)
        first = await client.get("/symbols/search?q=app")
        second = await client.get("/symbols/search?q=app")

    assert first.json() == second.json()
    assert [asdict(record) for record in master.records()] == before


async def test_search_never_reads_the_depth_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Depth is an enrichment cache over a subset; a lookup must not consult it.

    The store is booby-trapped: any attribute read raises. A symbol the depth
    store happens not to cover is still a listed security, so identity — not
    coverage — is what answers.
    """

    class _ExplodingDepth:
        def __getattr__(self, name: str) -> Any:
            raise AssertionError(f"search read the depth store ({name})")

    async with _client(tmp_path, monkeypatch) as (client, application):
        application.state.engine = _service(_master())
        application.state.depth = _ExplodingDepth()
        response = await client.get("/symbols/search?q=aapl")

    assert response.status_code == 200, response.text
    assert _symbols(response.json()) == ["AAPL"]


async def test_both_routes_are_mounted_on_the_real_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``create_app`` registers the module: a route nobody mounts is not an API."""

    async with _client(tmp_path, monkeypatch) as (_unused, application):
        paths = {getattr(route, "path", None) for route in application.routes}

    assert {"/market/breadth", "/symbols/search"} <= paths


# ---------------------------------------------------------------------------
# §4 The two performance gates, measured on production-sized inputs
# ---------------------------------------------------------------------------


def _production_universe(count: int) -> list[dict[str, Any]]:
    """``count`` rows across 19 sectors, with realistic gaps in the inputs."""

    sectors = (
        "Finance",
        "Health Technology",
        "Technology Services",
        "Electronic Technology",
        "Producer Manufacturing",
        "Commercial Services",
        "Consumer Services",
        "Non-Energy Minerals",
        "Process Industries",
        "Retail Trade",
        "Industrial Services",
        "Transportation",
        "Consumer Non-Durables",
        "Consumer Durables",
        "Utilities",
        "Miscellaneous",
        "Energy Minerals",
        "Distribution Services",
        "Health Services",
    )
    changes = (1.25, -0.75, 0.0, None, 3.5, -2.25, 0.5, None)
    rows: list[dict[str, Any]] = []
    for index in range(count):
        change = changes[index % len(changes)]
        rows.append(
            _row(
                f"S{index:05d}",
                change,
                sectors[index % len(sectors)],
                price=10.0 + (index % 500) / 10.0,
                high=90.0,
                low=None if index % 97 == 0 else 12.0,
            )
        )
    return rows


def test_breadth_measures_the_whole_universe_under_fifty_milliseconds() -> None:
    """5,600 rows, one pass: the gate in the plan is 50 ms."""

    rows = _production_universe(5_600)
    samples: list[float] = []
    for _ in range(5):
        started = time.perf_counter()
        body = breadth_of(
            rows, as_of=AS_OF, source="scan:us:stock", region="us", asset_class="stock"
        )
        samples.append((time.perf_counter() - started) * 1000.0)

    best = min(samples)
    print(f"\nbreadth 5,600 rows: best {best:.2f} ms of {[round(s, 2) for s in samples]}")
    assert body["universe_total"] == 5_600
    assert body["advancing"] + body["declining"] + body["unchanged"] == 5_600
    assert best < 50.0, f"breadth took {best:.2f} ms for 5,600 rows (gate 50 ms)"


def test_search_ranks_the_whole_identity_snapshot_under_eighty_milliseconds() -> None:
    """10,433 securities, one ranked pass: the gate in the plan is 80 ms."""

    rows = tuple(
        (f"S{index:05d}", f"Company {index} Holdings Inc.", "Nasdaq", "XNAS")
        for index in range(10_433)
    )
    index = IdentityIndex.from_master(_master(rows))

    samples: list[float] = []
    for _ in range(5):
        started = time.perf_counter()
        hits = index.search("company", limit=20, region="us")
        samples.append((time.perf_counter() - started) * 1000.0)

    best = min(samples)
    print(f"\nsearch 10,433 securities: best {best:.2f} ms of {[round(s, 2) for s in samples]}")
    assert len(hits) == 20
    assert best < 80.0, f"search took {best:.2f} ms over 10,433 rows (gate 80 ms)"
