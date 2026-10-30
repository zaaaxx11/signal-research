"""P10 RED — ``GET /screens`` through the API: multi by default, raw on demand.

The service-level contract lives in ``test_screen_multi_source.py``. This module
pins the **route**:

* the **default** response is the verified consensus: ``source="multi"``,
  ``selection_source="tradingview"``, one row per stock with ``source_summary``
  and no per-source cells;
* two requests inside the 300 s window send **zero** verifier calls (the cache is
  a property of the process, held by the runtime, not of the request);
* a verifier outage answers the raw snapshot honestly: ``source="tradingview"``,
  ``degraded=true``, ``verification_error``;
* ``?source=tradingview`` still serves the raw snapshot (and never calls a
  verifier) — the audit view;
* an unknown ``source`` is a 422 before any scan.

The TradingView scanner, Yahoo and Nasdaq all answer through the one HTTP seam
(``tests._phase2.MockWeb``), so the built-in verifiers are exercised for real —
the injection tests only replace them when the point is call counting.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import unquote

import httpx2

from app.screen.multi_source import Observation
from tests import _screen as S
from tests._phase2 import MockWeb
from tests.test_screen_multi_source import FailingVerifier, FakeVerifier

#: The Yahoo quote clock. Fixed so two runs parse the same timestamps.
QUOTE_TS = 1761236880
#: TradingView exchange codes that are US listings (the fixtures' `exchange`).
US_TV_EXCHANGES = frozenset({"NASDAQ", "NYSE", "AMEX", "CBOE"})

SUMMARY_KEYS = frozenset(
    {"method", "source_count", "agreement", "spread_pp", "confidence", "verified_at"}
)
PER_SOURCE_KEYS = frozenset({"sources", "yahoo", "nasdaq", "tradingview"})


# ---------------------------------------------------------------------------
# Fixtures: the scanner plus live verifier payloads over the same MockWeb
# ---------------------------------------------------------------------------


def _tv_cells(region: str = "us") -> tuple[dict[str, float], dict[str, float | None]]:
    """Price/change by symbol, preferring the US listing of each ticker."""

    prices: dict[str, float] = {}
    changes: dict[str, float | None] = {}
    for row in S.scan_rows(region):
        venue = str(row.get("exchange") or "").upper()
        symbol = str(row.get("symbol") or "")
        price = row.get("close")
        if not symbol or not isinstance(price, (int, float)):
            continue
        if venue in US_TV_EXCHANGES or symbol not in prices:
            prices[symbol] = float(price)
            change = row.get("change")
            changes[symbol] = float(change) if isinstance(change, (int, float)) else None
    return prices, changes


PRICES, CHANGES = _tv_cells("us")


def _previous_close(symbol: str) -> float:
    price = PRICES[symbol]
    change = CHANGES.get(symbol)
    if change is None or change <= -100:
        return price
    return price / (1.0 + change / 100.0)


def _yahoo_payload(request: httpx2.Request) -> dict[str, Any]:
    raw = request.url.params.get("symbols") or ""
    results: list[dict[str, Any]] = []
    for symbol in (token.strip().upper() for token in raw.split(",")):
        if not symbol or symbol not in PRICES:
            continue
        results.append(
            {
                "symbol": symbol,
                "regularMarketPrice": PRICES[symbol],
                "regularMarketPreviousClose": _previous_close(symbol),
                "regularMarketVolume": 1_000_000,
                "regularMarketTime": QUOTE_TS,
                "currency": "USD",
            }
        )
    return {"quoteResponse": {"result": results, "error": None}}


def _nasdaq_response(request: httpx2.Request) -> httpx2.Response:
    path = unquote(request.url.path)
    marker = "/api/quote/"
    symbol = path.split(marker, 1)[1].split("/", 1)[0].upper() if marker in path else ""
    price = PRICES.get(symbol)
    if price is None:
        return httpx2.Response(200, json={"data": None})
    return httpx2.Response(
        200,
        json={
            "data": {
                "primaryData": {
                    "lastSalePrice": f"${price:,.2f}",
                    "volume": "1,000,000",
                    "lastTradeTimestamp": "Oct 23, 2026 3:48 PM ET",
                    "isRealTime": True,
                }
            }
        },
    )


def verified_web() -> MockWeb:
    """The scanner fixture plus Yahoo and Nasdaq answering for the same names."""

    web = S.scanner_web()
    web.json("yahoo", _yahoo_payload)
    web.route("nasdaq", _nasdaq_response)
    return web


def counting_verifiers() -> list[FakeVerifier]:
    """Two verifiers that mirror the snapshot and count how often they are asked."""

    def observe(name: str) -> FakeVerifier:
        return FakeVerifier(
            name,
            lambda row: Observation(
                source=name,
                price=float(row["price"]),
                change_pct=float(row["change_pct"]) if row.get("change_pct") is not None else None,
                volume=float(row["volume"]) if row.get("volume") is not None else None,
            ),
        )

    return [observe("yahoo"), observe("nasdaq")]


# ---------------------------------------------------------------------------
# Default = multi
# ---------------------------------------------------------------------------


async def test_the_default_browse_is_a_verified_multi_source_document(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with S.screen_client(tmp_path, monkeypatch, web=verified_web()) as harness:
        page = await harness.create_screen()

    assert S.provider_requests(harness.web) == 1, (
        "the TradingView leg is the snapshot scan itself: a consensus must not "
        f"cost a second scan, got {S.provider_requests(harness.web)} requests"
    )

    assert page["source"] == "multi", (
        "the default display is the multi-source consensus (P10), got "
        f"source={page['source']!r}"
    )
    assert page["selection_source"] == "tradingview", (
        "the universe is still selected from TradingView, and the document says so"
    )
    assert page["degraded"] is False
    assert page["verification_error"] is None
    assert page["returned"] == S.DISPLAY_ROWS and len(page["rows"]) == S.DISPLAY_ROWS
    assert page["matched"] == S.US_TOTAL

    for row in page["rows"]:
        assert "source_summary" in row, (
            f"row {row.get('symbol')!r} must say how its cells were produced"
        )
        assert set(row["source_summary"]) == SUMMARY_KEYS, sorted(row["source_summary"])
        assert row["source_summary"]["method"] == "multi_source_consensus"
        assert not PER_SOURCE_KEYS & set(row), (
            "there must be no per-source columns: "
            f"{sorted(PER_SOURCE_KEYS & set(row))}"
        )
        assert isinstance(row["source_summary"]["source_count"], int)
        assert 2 <= row["source_summary"]["source_count"] <= 3, (
            "the fixture has TradingView + Yahoo/Nasdaq; the optional HTML legs "
            "are absent, got "
            f"{row['source_summary']['source_count']}"
        )
        assert row["source_summary"]["agreement"] == 1.0, (
            "Yahoo mirrors the snapshot change in this fixture, so the sources agree"
        )
        assert row["source_summary"]["spread_pp"] in (None, 0.0), (
            "spread is null when only one change source answered; otherwise "
            f"the fixture's mirrored change is 0.0, got {row['source_summary']['spread_pp']}"
        )
        assert row["as_of"] == page["as_of"]


async def test_the_consensus_price_is_the_median_not_one_providers_number(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with S.screen_client(tmp_path, monkeypatch, web=verified_web()) as harness:
        page = await harness.create_screen()
        raw = await harness.create_screen(source="tradingview")

    by_symbol = {row["symbol"]: row for row in raw["rows"]}
    for row in page["rows"]:
        snapshot = by_symbol[row["symbol"]]
        assert row["price"] == snapshot["price"], (
            "with Yahoo echoing the snapshot and Nasdaq rounding to cents, the "
            f"median stays the snapshot price for {row['symbol']}"
        )
        assert row["market_cap_usd"] == snapshot["market_cap_usd"], (
            "only the selection source publishes a market cap, so the median of "
            "one is that value"
        )


# ---------------------------------------------------------------------------
# Cache: zero verifier calls inside the window
# ---------------------------------------------------------------------------


async def test_two_requests_inside_the_window_call_no_verifier_again(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        verifiers = counting_verifiers()
        harness.runtime.multi_source_screen = _service(harness, verifiers)

        first = await harness.create_screen()
        calls_after_first = [verifier.calls for verifier in verifiers]
        second = await harness.create_screen()

    assert calls_after_first == [1, 1], (
        "the first request verifies once, got " f"{calls_after_first}"
    )
    assert [verifier.calls for verifier in verifiers] == [1, 1], (
        "the second read inside the 300 s window must send zero verifier calls, "
        f"got {[verifier.calls for verifier in verifiers]}"
    )
    assert second["screen_id"] == first["screen_id"]
    assert second["rows"] == first["rows"]
    assert (
        second["rows"][0]["source_summary"]["verified_at"]
        == first["rows"][0]["source_summary"]["verified_at"]
    )


async def test_a_refreshed_snapshot_is_verified_again(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        verifiers = counting_verifiers()
        harness.runtime.multi_source_screen = _service(harness, verifiers)

        first = await harness.create_screen()
        S.age_screen(harness.db_path, first["screen_id"])
        refreshed = await harness.create_screen()

    assert refreshed["screen_id"] != first["screen_id"], (
        "the snapshot expired, so the display must be rebuilt"
    )
    assert [verifier.calls for verifier in verifiers] == [2, 2], (
        "a new snapshot is new rows and must be verified, not answered from the "
        f"previous screen's cache: {[verifier.calls for verifier in verifiers]}"
    )
    assert S.provider_requests(harness.web) == 2, "one scan per snapshot refresh"


# ---------------------------------------------------------------------------
# Degraded fallback
# ---------------------------------------------------------------------------


async def test_a_verifier_outage_answers_the_raw_snapshot_honestly(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        harness.runtime.multi_source_screen = _service(
            harness,
            [FailingVerifier("yahoo", "yahoo answered 503"), FailingVerifier("nasdaq", "nasdaq timeout")],
        )
        page = await harness.create_screen()

    assert page["source"] == "tradingview", (
        "an unverified page must not claim source=multi, got "
        f"{page['source']!r}"
    )
    assert page["degraded"] is True
    assert page["verification_error"], "the degraded page must name the reason"
    assert "failed" in page["verification_error"]
    assert page["selection_source"] == "tradingview"
    assert len(page["rows"]) == S.DISPLAY_ROWS
    assert page["matched"] == S.US_TOTAL
    assert "source_summary" not in page["rows"][0], (
        "the fallback is the raw snapshot, not a consensus with missing evidence"
    )


# ---------------------------------------------------------------------------
# The raw view and the closed vocabulary
# ---------------------------------------------------------------------------


async def test_the_raw_tradingview_source_is_still_available(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        verifiers = counting_verifiers()
        harness.runtime.multi_source_screen = _service(harness, verifiers)
        page = await harness.create_screen(source="tradingview")

    assert page["source"] == "tradingview"
    assert "selection_source" not in page, (
        "the raw view is the pre-P10 document, unchanged"
    )
    assert "source_summary" not in page["rows"][0]
    assert [verifier.calls for verifier in verifiers] == [0, 0], (
        "the audit view must not spend provider requests on verification"
    )
    assert S.provider_requests(harness.web) == 1, "one snapshot scan, nothing else"


async def test_an_unknown_source_is_rejected_before_any_scan(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        response = await harness.client.get(
            "/screens", params={"region": "us", "sort": "market_cap", "source": "bloomberg"}
        )

    assert response.status_code == 422, (
        f"source is a closed vocabulary (multi|tradingview), got {response.status_code}: "
        f"{response.text}"
    )
    assert S.provider_requests(harness.web) == 0, "a 422 must not spend a scan"


async def test_a_multi_browse_snapshot_still_resolves_a_symbols_selection(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """The stored snapshot stays TradingView's: ``multi`` is presentation only."""

    async with S.screen_client(tmp_path, monkeypatch) as harness:
        page = await harness.create_screen()
        answer = await harness.client.post(
            "/screens/select",
            json={
                "mode": "symbols",
                "symbols": [page["rows"][0]["symbol"]],
                "source": "tradingview",
                "snapshot_id": page["screen_id"],
            },
        )

    assert answer.status_code == 200, (
        "a selection resolves against the snapshot's own source; if the browse "
        f"call relabelled it 'multi' this becomes a 422: {answer.text}"
    )
    body = answer.json()
    assert [row["symbol"] for row in body["rows"]] == [page["rows"][0]["symbol"]]


async def test_the_served_schema_declares_every_key_the_route_returns(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """The published contract must describe the document, extras and all."""

    async with S.screen_client(tmp_path, monkeypatch, web=verified_web()) as harness:
        page = await harness.create_screen()
        schema = (await harness.client.get("/screens/schema")).json()

    top_level = set(schema["properties"])
    assert not set(page) - top_level, (
        "the multi document returns keys the schema rejects: "
        f"{sorted(set(page) - top_level)}"
    )
    row_properties = set(schema["$defs"]["row"]["properties"])
    for row in page["rows"]:
        assert not set(row) - row_properties, (
            f"row {row['symbol']} carries keys the schema rejects: "
            f"{sorted(set(row) - row_properties)}"
        )
    summary = schema["$defs"]["sourceSummary"]
    assert set(summary["required"]) == SUMMARY_KEYS
    assert summary["additionalProperties"] is False, (
        "source_summary is a fixed six-key object, not an open bag"
    )
    assert schema["properties"]["source"]["enum"] == ["multi", "tradingview"]


def _service(harness: Any, verifiers: list[Any]) -> Any:
    """Rebuild the runtime service around injected verifiers."""

    from app.screen.multi_source import MultiSourceScreen

    return MultiSourceScreen(
        repository=harness.runtime.screen_repository, verifiers=verifiers
    )
