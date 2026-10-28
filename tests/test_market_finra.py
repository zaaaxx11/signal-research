"""FINRA consolidated short interest: parser, source, cache and HTTP contract.

Every test is offline. The parser is exercised on the live JSON shape (a list of
records with integer quantities) and on the CSV the endpoint returns when
``Accept`` is left at ``*/*``. The source is driven twice: through an injected
``fetcher`` (no HTTP object at all) and through the one HTTP seam
(``httpx2.MockTransport``), so the request body, headers and pagination offsets
are pinned without a socket.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import Any, Mapping, Sequence

import httpx2
import pytest

from app.market.depth import DepthCollector, DepthObservation
from app.market.finra import (
    PAGE_LIMIT,
    FinraPage,
    FinraParseError,
    FinraShortSource,
    FinraSourceError,
    parse_consolidated_short_interest,
    settlement_candidates,
)
from tests._phase2 import MockWeb, install_web

SETTLEMENT = "2026-10-15"
AS_OF = datetime(2026, 10, 15, tzinfo=UTC)


def finra_row(
    symbol: Any,
    *,
    current: Any = 1_000_000,
    previous: Any = 900_000,
    average_volume: Any = 2_000_000,
    days_to_cover: Any = 2.5,
    settlement: Any = SETTLEMENT,
    issue: str | None = None,
    market: str = "NNM",
) -> dict[str, Any]:
    """One record as the live JSON endpoint prints it (quantities numeric)."""

    return {
        "accountingYearMonthNumber": 20261015,
        "symbolCode": symbol,
        "issueName": issue or f"{symbol} Corporation",
        "issuerServicesGroupExchangeCode": "A",
        "marketClassCode": market,
        "currentShortPositionQuantity": current,
        "previousShortPositionQuantity": previous,
        "stockSplitFlag": None,
        "averageDailyVolumeQuantity": average_volume,
        "daysToCoverQuantity": days_to_cover,
        "revisionFlag": None,
        "changePercent": -2.14,
        "changePreviousNumber": -21_400,
        "settlementDate": settlement,
    }


class FakeFinra:
    """Injectable fetcher: slices one synthetic file by ``offset``/``limit``."""

    def __init__(
        self,
        rows: Sequence[Mapping[str, Any]],
        *,
        empty_dates: Sequence[str] = (),
    ) -> None:
        self.rows = list(rows)
        self.empty_dates = set(empty_dates)
        self.calls: list[tuple[str, int, int]] = []

    async def __call__(
        self, *, settlement: str, limit: int, offset: int
    ) -> FinraPage:
        self.calls.append((settlement, limit, offset))
        if settlement in self.empty_dates:
            return FinraPage((), total=0)
        return FinraPage(tuple(self.rows[offset : offset + limit]), len(self.rows))


class EndlessFinra:
    """A broken server that always returns a full page and no total."""

    def __init__(self, rows: Sequence[Mapping[str, Any]]) -> None:
        self.rows = tuple(rows)
        self.calls = 0

    async def __call__(
        self, *, settlement: str, limit: int, offset: int
    ) -> FinraPage:
        self.calls += 1
        return FinraPage(self.rows[:limit], None)


# ---------------------------------------------------------------------------
# Pure parser
# ---------------------------------------------------------------------------


def test_parse_reads_the_real_finra_fields() -> None:
    parsed = parse_consolidated_short_interest(
        [
            finra_row(
                "AAPL",
                current=137_000_000,
                previous=140_000_000,
                average_volume=52_000_000,
                days_to_cover=2.63,
                issue="Apple Inc.",
                market="NNM",
            ),
            finra_row("JPM", current="12,345,678", previous="12,000,000"),
        ]
    )

    assert set(parsed) == {"AAPL", "JPM"}
    aapl = parsed["AAPL"]
    assert aapl["short_interest"] == 137_000_000
    assert isinstance(aapl["short_interest"], int)
    assert aapl["previous_short_interest"] == 140_000_000
    assert aapl["average_daily_volume"] == 52_000_000
    assert aapl["days_to_cover"] == pytest.approx(2.63)
    assert aapl["issue_name"] == "Apple Inc."
    assert aapl["market_class"] == "NNM"
    assert aapl["settlement_date"] == SETTLEMENT
    # CSV-style thousands separators are still shares, not a string.
    assert parsed["JPM"]["short_interest"] == 12_345_678


def test_parse_normalises_class_share_symbols() -> None:
    parsed = parse_consolidated_short_interest(
        [
            finra_row("brk.b", current=1),
            finra_row("BRK-B", current=2),
            finra_row("BRKB", current=3),
            finra_row("bf.b", current=4),
        ]
    )

    assert set(parsed) == {"BRK-B", "BRKB", "BF-B"}


def test_parse_skips_a_record_without_a_symbol() -> None:
    parsed = parse_consolidated_short_interest(
        [
            {"issueName": "No ticker here", "currentShortPositionQuantity": 10},
            {"symbolCode": "   ", "currentShortPositionQuantity": 20},
            finra_row("AAPL"),
        ]
    )

    assert set(parsed) == {"AAPL"}


def test_parse_keeps_null_interest_as_absent_not_zero() -> None:
    parsed = parse_consolidated_short_interest([finra_row("AAPL", current=None)])

    assert parsed["AAPL"]["short_interest"] is None


def test_parse_duplicate_symbol_takes_the_last_non_null_value() -> None:
    parsed = parse_consolidated_short_interest(
        [
            finra_row("AAPL", current=100),
            finra_row("AAPL", current=120, previous=110),
        ]
    )

    assert len(parsed) == 1
    assert parsed["AAPL"]["short_interest"] == 120
    assert parsed["AAPL"]["previous_short_interest"] == 110


def test_parse_duplicate_null_does_not_erase_a_reported_position() -> None:
    parsed = parse_consolidated_short_interest(
        [finra_row("AAPL", current=100), finra_row("AAPL", current=None)]
    )

    assert parsed["AAPL"]["short_interest"] == 100


def test_parse_uses_record_settlement_before_top_level() -> None:
    parsed = parse_consolidated_short_interest(
        {
            "settlementDate": SETTLEMENT,
            "records": [
                {
                    "symbolCode": "AAPL",
                    "currentShortPositionQuantity": 1,
                },
                {
                    "symbolCode": "JPM",
                    "currentShortPositionQuantity": 2,
                    "settlementDate": "2026-09-30",
                },
            ],
        }
    )

    assert parsed["AAPL"]["settlement_date"] == SETTLEMENT
    assert parsed["JPM"]["settlement_date"] == "2026-09-30"


def test_parse_accepts_the_csv_shape_the_endpoint_prints() -> None:
    body = (
        '"accountingYearMonthNumber","symbolCode","issueName","marketClassCode",'
        '"currentShortPositionQuantity","previousShortPositionQuantity",'
        '"averageDailyVolumeQuantity","daysToCoverQuantity","changePercent",'
        '"settlementDate"\n'
        '"20261015","AAPL","Apple, Inc.","NNM","137,000,000","140,000,000",'
        '"52,000,000","2.63","-2.14","2026-10-15"\n'
    )

    parsed = parse_consolidated_short_interest(body)

    assert parsed["AAPL"]["short_interest"] == 137_000_000
    assert parsed["AAPL"]["issue_name"] == "Apple, Inc."
    assert parsed["AAPL"]["average_daily_volume"] == 52_000_000
    assert parsed["AAPL"]["settlement_date"] == SETTLEMENT


@pytest.mark.parametrize("payload", [None, [], (), {"records": []}, {"items": []}])
def test_parse_empty_payloads_are_empty(payload: Any) -> None:
    assert parse_consolidated_short_interest(payload) == {}


def test_parse_rejects_broken_json() -> None:
    with pytest.raises(FinraParseError, match="invalid FINRA JSON"):
        parse_consolidated_short_interest("{not json")


def test_parse_rejects_an_unsupported_payload() -> None:
    with pytest.raises(FinraParseError, match="unsupported"):
        parse_consolidated_short_interest(42)


# ---------------------------------------------------------------------------
# Source: symbols, absence, formulas
# ---------------------------------------------------------------------------


async def test_fetch_many_returns_only_published_symbols() -> None:
    fake = FakeFinra(
        [
            finra_row("AAPL"),
            finra_row("JPM", current="12,345,678"),
            finra_row("BRKB", current=13_769_255),
        ]
    )
    source = FinraShortSource(settlement_date=SETTLEMENT, fetcher=fake)

    result = await source.fetch_many(["AAPL", "JPM", "BRKB", "TSLA"])

    assert set(result) == {"AAPL", "JPM", "BRKB"}
    assert "TSLA" not in result
    assert all(isinstance(obs, DepthObservation) for obs in result.values())
    assert result["AAPL"].source == "finra"
    assert result["AAPL"].symbol == "AAPL"
    assert result["AAPL"].as_of == AS_OF
    assert all(obs.fields["short_interest"] != 0 for obs in result.values())


async def test_fetch_many_matches_brk_b_to_finra_brkb() -> None:
    fake = FakeFinra([finra_row("BRKB", current=13_769_255)])
    source = FinraShortSource(settlement_date=SETTLEMENT, fetcher=fake)

    for requested in ("BRK.B", "BRK-B", "brk.b"):
        result = await source.fetch_many([requested])
        assert list(result) == ["BRK-B"]
        assert result["BRK-B"].fields["short_interest"] == 13_769_255


async def test_fetch_many_without_symbols_makes_no_request() -> None:
    fake = FakeFinra([finra_row("AAPL")])
    source = FinraShortSource(settlement_date=SETTLEMENT, fetcher=fake)

    assert await source.fetch_many([]) == {}
    assert fake.calls == []


async def test_short_ratio_is_short_interest_over_average_volume() -> None:
    fake = FakeFinra([finra_row("AAPL", current=1_000_000, average_volume=9_999_999)])
    source = FinraShortSource(
        settlement_date=SETTLEMENT,
        fetcher=fake,
        average_volume={"AAPL": 2_000_000},
    )

    result = await source.fetch_many(["AAPL"])

    assert result["AAPL"].fields["short_ratio"] == pytest.approx(0.5)


async def test_short_ratio_uses_the_map_not_finra_days_to_cover_or_adv() -> None:
    # The record's own averageDailyVolumeQuantity must not silently become the
    # divisor: the brief's 97/98 parity is against the caller's 3m average.
    fake = FakeFinra([finra_row("AAPL", current=1_000_000, average_volume=9_999_999)])
    source = FinraShortSource(settlement_date=SETTLEMENT, fetcher=fake)

    result = await source.fetch_many(["AAPL"])

    assert "short_ratio" not in result["AAPL"].fields


@pytest.mark.parametrize("volume", [0, 0.0, "0", None, "n/a", -5])
async def test_short_ratio_is_absent_for_an_unusable_volume(volume: Any) -> None:
    fake = FakeFinra([finra_row("AAPL", current=1_000_000)])
    source = FinraShortSource(
        settlement_date=SETTLEMENT,
        fetcher=fake,
        average_volume={"AAPL": volume},
    )

    result = await source.fetch_many(["AAPL"])

    assert "short_ratio" not in result["AAPL"].fields


async def test_short_float_is_percent_points_not_a_fraction() -> None:
    fake = FakeFinra([finra_row("AAPL", current=1_000_000)])
    source = FinraShortSource(
        settlement_date=SETTLEMENT,
        fetcher=fake,
        shares_float=lambda: {"AAPL": "50,000,000"},
    )

    result = await source.fetch_many(["AAPL"])
    value = result["AAPL"].fields["short_float"]

    assert value == pytest.approx(2.0)
    assert value != pytest.approx(0.02)


@pytest.mark.parametrize("shares", [0, 0.0, "0", None, "n/a", -1])
async def test_short_float_is_absent_for_an_unusable_float(shares: Any) -> None:
    fake = FakeFinra([finra_row("AAPL", current=1_000_000)])
    source = FinraShortSource(
        settlement_date=SETTLEMENT,
        fetcher=fake,
        shares_float={"AAPL": shares},
    )

    result = await source.fetch_many(["AAPL"])

    assert "short_float" not in result["AAPL"].fields


async def test_only_the_three_short_fields_are_emitted() -> None:
    fake = FakeFinra(
        [finra_row("AAPL", current=1_000_000, average_volume=8_000_000)]
    )
    source = FinraShortSource(
        settlement_date=SETTLEMENT,
        fetcher=fake,
        average_volume={"AAPL": 2_000_000},
        shares_float={"AAPL": 50_000_000},
    )

    fields = (await source.fetch_many(["AAPL"]))["AAPL"].fields

    assert set(fields) == {"short_interest", "short_ratio", "short_float"}
    # Shares stay shares: no /1e6 conversion without a test that demands it.
    assert fields["short_interest"] == 1_000_000
    assert fields["short_ratio"] == pytest.approx(0.5)
    assert fields["short_float"] == pytest.approx(2.0)


async def test_null_interest_yields_no_observation() -> None:
    fake = FakeFinra([finra_row("AAPL", current=None)])
    source = FinraShortSource(settlement_date=SETTLEMENT, fetcher=fake)

    assert await source.fetch_many(["AAPL"]) == {}


async def test_reported_zero_is_kept_but_a_missing_symbol_is_not_filled() -> None:
    fake = FakeFinra([finra_row("AAPL", current=0)])
    source = FinraShortSource(settlement_date=SETTLEMENT, fetcher=fake)

    result = await source.fetch_many(["AAPL", "TSLA"])

    assert result["AAPL"].fields["short_interest"] == 0
    assert "TSLA" not in result


# ---------------------------------------------------------------------------
# Pagination, settlement and cache
# ---------------------------------------------------------------------------


async def test_pagination_reads_two_pages() -> None:
    symbols = ("AAPL", "JPM", "BRKB", "MSFT", "XOM")
    fake = FakeFinra([finra_row(symbol) for symbol in symbols])
    source = FinraShortSource(
        settlement_date=SETTLEMENT, fetcher=fake, page_limit=3
    )

    result = await source.fetch_many(symbols)

    assert set(result) == set(symbols)
    assert [(call[1], call[2]) for call in fake.calls] == [(3, 0), (3, 3)]


async def test_pagination_stops_when_the_total_is_reached() -> None:
    fake = FakeFinra([finra_row("AAPL"), finra_row("JPM")])
    source = FinraShortSource(settlement_date=SETTLEMENT, fetcher=fake, page_limit=2)

    result = await source.fetch_many(["AAPL", "JPM"])

    assert set(result) == {"AAPL", "JPM"}
    assert len(fake.calls) == 1


async def test_pagination_guard_stops_an_endless_full_page() -> None:
    fake = EndlessFinra([finra_row("AAPL"), finra_row("JPM")])
    source = FinraShortSource(
        settlement_date=SETTLEMENT,
        fetcher=fake,
        page_limit=2,
        max_pages=2,
    )

    with pytest.raises(FinraSourceError, match="did not terminate"):
        await source.fetch_many(["AAPL"])
    assert fake.calls == 2


async def test_latest_settlement_is_probed_newest_first_and_cached() -> None:
    fake = FakeFinra(
        [finra_row("AAPL", settlement="2026-09-30")],
        empty_dates=["2026-10-15"],
    )
    source = FinraShortSource(
        fetcher=fake,
        clock=lambda: datetime(2026, 10, 28, 12, 0, tzinfo=UTC),
    )

    first = await source.fetch_many(["AAPL"])

    assert first["AAPL"].as_of == datetime(2026, 9, 30, tzinfo=UTC)
    assert fake.calls == [
        ("2026-10-15", 1, 0),
        ("2026-09-30", 1, 0),
        ("2026-09-30", PAGE_LIMIT, 0),
    ]

    await source.fetch_many(["AAPL"])
    assert len(fake.calls) == 3


def test_settlement_candidates_are_newest_first_and_never_future() -> None:
    candidates = settlement_candidates(date(2026, 10, 28), months=3)

    assert candidates[0] == "2026-10-15"
    assert "2026-09-30" in candidates
    assert "2026-09-15" in candidates
    assert all(candidate <= "2026-10-28" for candidate in candidates)
    assert candidates == tuple(sorted(candidates, reverse=True))


def test_settlement_candidates_cover_a_holiday_month_end() -> None:
    # 2026-10-30 is a Friday; the two weekdays before it are the fallback when
    # the last trading day is a holiday and the Friday file does not exist.
    candidates = settlement_candidates(date(2026, 10, 30), months=1)

    assert candidates[:3] == ("2026-10-30", "2026-10-29", "2026-10-28")


async def test_cache_is_per_settlement_date() -> None:
    fake = FakeFinra([finra_row("AAPL")])
    source = FinraShortSource(settlement_date=SETTLEMENT, fetcher=fake)

    await source.fetch_many(["AAPL"])
    await source.fetch_many(["AAPL"])
    assert len(fake.calls) == 1

    source.settlement_date = "2026-09-30"
    await source.fetch_many(["AAPL"])
    assert len(fake.calls) == 2
    assert fake.calls[1][0] == "2026-09-30"


async def test_clear_cache_forces_a_refetch() -> None:
    fake = FakeFinra([finra_row("AAPL")])
    source = FinraShortSource(settlement_date=SETTLEMENT, fetcher=fake)

    await source.fetch_many(["AAPL"])
    source.clear_cache()
    await source.fetch_many(["AAPL"])

    assert len(fake.calls) == 2


# ---------------------------------------------------------------------------
# HTTP seam
# ---------------------------------------------------------------------------


async def test_http_request_is_json_post_with_a_settlement_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    web = MockWeb()
    seen: list[dict[str, Any]] = []

    def responder(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        seen.append(
            {
                "method": request.method,
                "url": str(request.url),
                "headers": dict(request.headers),
                "body": body,
            }
        )
        return httpx2.Response(
            200,
            json=[finra_row("AAPL")],
            headers={"record-total": "1"},
        )

    web.route("finra", responder)
    install_web(monkeypatch, web)
    source = FinraShortSource(settlement_date=SETTLEMENT)

    result = await source.fetch_many(["AAPL"])

    assert set(result) == {"AAPL"}
    assert len(seen) == 1
    request = seen[0]
    assert request["method"] == "POST"
    assert request["url"] == "https://api.finra.org/data/group/otcMarket/name/consolidatedShortInterest"
    assert request["headers"]["accept"] == "application/json"
    assert request["headers"]["content-type"] == "application/json"
    assert request["headers"]["user-agent"].startswith("Mozilla/5.0")
    assert request["body"] == {
        "limit": PAGE_LIMIT,
        "offset": 0,
        "compareFilters": [
            {
                "compareType": "EQUAL",
                "fieldName": "settlementDate",
                "fieldValue": SETTLEMENT,
            }
        ],
    }


async def test_http_pagination_advances_the_offset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    web = MockWeb()
    offsets: list[int] = []
    rows = [finra_row("AAPL"), finra_row("JPM")]

    def responder(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        offsets.append(body["offset"])
        page = rows[body["offset"] : body["offset"] + body["limit"]]
        return httpx2.Response(
            200, json=page, headers={"record-total": str(len(rows))}
        )

    web.route("finra", responder)
    install_web(monkeypatch, web)
    source = FinraShortSource(settlement_date=SETTLEMENT, page_limit=1)

    result = await source.fetch_many(["AAPL", "JPM"])

    assert set(result) == {"AAPL", "JPM"}
    assert offsets == [0, 1]


@pytest.mark.parametrize("status", [429, 500])
async def test_http_error_raises_a_source_error(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    web = MockWeb()
    web.status("finra", status)
    install_web(monkeypatch, web)
    source = FinraShortSource(settlement_date=SETTLEMENT)

    with pytest.raises(FinraSourceError, match=str(status)):
        await source.fetch_many(["AAPL"])


async def test_http_204_is_empty_not_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    web = MockWeb()
    web.route("finra", lambda _request: httpx2.Response(204))
    install_web(monkeypatch, web)
    source = FinraShortSource(settlement_date=SETTLEMENT)

    assert await source.fetch_many(["AAPL"]) == {}


async def test_http_offset_past_the_end_stops_pagination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    web = MockWeb()
    offsets: list[int] = []
    rows = [finra_row("AAPL"), finra_row("JPM")]

    def responder(request: httpx2.Request) -> httpx2.Response:
        offset = json.loads(request.content)["offset"]
        offsets.append(offset)
        if offset >= len(rows):
            return httpx2.Response(
                400,
                json={
                    "message": (
                        f"Offset of {offset} is larger than the number of "
                        f"records for the query ({len(rows)})."
                    )
                },
            )
        # No `record-total` header: the 400 is the only end-of-file signal.
        return httpx2.Response(200, json=[rows[offset]])

    web.route("finra", responder)
    install_web(monkeypatch, web)
    source = FinraShortSource(settlement_date=SETTLEMENT, page_limit=1)

    result = await source.fetch_many(["AAPL", "JPM"])

    assert set(result) == {"AAPL", "JPM"}
    assert offsets == [0, 1, 2]


async def test_a_finra_source_error_does_not_kill_the_depth_batch() -> None:
    class BrokenFinra:
        name = "finra"

        async def fetch_many(
            self, symbols: Sequence[str]
        ) -> dict[str, DepthObservation]:
            raise FinraSourceError("FINRA short interest HTTP 500")

    class GoodSource:
        name = "other"

        async def fetch_many(
            self, symbols: Sequence[str]
        ) -> dict[str, DepthObservation]:
            return {
                symbol: DepthObservation(
                    symbol=symbol,
                    fields={"pe": 30.0},
                    as_of=AS_OF,
                    source=self.name,
                )
                for symbol in symbols
            }

    collector = DepthCollector(
        symbols=["AAPL"],
        sources=[BrokenFinra(), GoodSource()],
        store_path=None,
    )

    result = await collector.refresh()

    assert result.errors == 1
    assert result.returned == 1
    assert collector.store.document(["AAPL"])["symbols"]["AAPL"] == {"pe": 30.0}
