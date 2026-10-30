"""Screen S0 RED — ``GET /screens``: a 20-row display of one 5-minute snapshot.

Contract (PLAN-screen.md §4, revised by the user):

* no cursor and no second page — every read returns exactly the top 20 of the
  region/sort snapshot, while ``total`` stays the full post-filter/post-dedupe
  universe size;
* a snapshot lives **300 seconds**: two reads inside the window share it and
  send zero provider requests, a read after the window refreshes it;
* ``screen_rows`` stores the 20 displayed rows only (the deep export has its
  own on-demand fetch — see ``test_screen_export.py``);
* sorting is transparent: ``market_cap``, ``turnover``, ``gainers`` each order
  the whole universe before the display cut.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tests import _screen as S


# ---------------------------------------------------------------------------
# Shape of the display
# ---------------------------------------------------------------------------


async def test_browse_returns_exactly_twenty_rows_of_the_us_tab(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        page = await harness.create_screen()

    assert page["region"] == "us"
    assert page["sort"] == "market_cap"
    assert page["screen_id"], "a browse response must name the snapshot it came from"
    S.parse_dt(page["as_of"])
    assert page["matched"] == S.US_TOTAL, (
        f"matched is the universe after filter+dedupe ({S.US_TOTAL}), not the raw "
        f"provider count ({S.US_RAW}); got {page['matched']!r}"
    )
    assert page["universe_total"] == S.US_TOTAL, (
        "browse applies no filter of its own, so its universe is what it matched"
    )
    assert "total" not in page, (
        "the ambiguous name is gone: /screens/select uses `total` for a query "
        "over the whole market, so browse speaks `matched`"
    )
    assert page["returned"] == S.DISPLAY_ROWS
    assert len(page["rows"]) == S.DISPLAY_ROWS, (
        f"the display is the top {S.DISPLAY_ROWS}, got {len(page['rows'])} rows"
    )
    assert page.get("next_cursor") is None, (
        "the screen is a display, not a search: there is no page 2 and no cursor"
    )

    assert [row["rank"] for row in page["rows"]] == list(range(1, S.DISPLAY_ROWS + 1))
    for row in page["rows"]:
        missing = S.SCREEN_ROW_KEYS - set(row)
        assert not missing, f"row {row.get('symbol')!r} is missing {sorted(missing)}"
        assert row["as_of"] == page["as_of"], (
            "every displayed row must carry the snapshot timestamp, got "
            f"{row['as_of']!r} vs {page['as_of']!r}"
        )


async def test_a_limit_above_the_display_size_does_not_widen_the_page(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        page = await harness.create_screen(limit=100)

    assert len(page["rows"]) == S.DISPLAY_ROWS, (
        "the display is fixed at 20 rows; limit=100 must not turn it into a "
        f"23-row table (got {len(page['rows'])})"
    )
    assert page["returned"] == S.DISPLAY_ROWS
    assert page["matched"] == S.US_TOTAL


# ---------------------------------------------------------------------------
# Snapshot lifetime — 5 minutes
# ---------------------------------------------------------------------------


async def test_snapshot_constants_are_the_revised_contract() -> None:
    assert S.snapshot().SNAPSHOT_TTL_S == 300, (
        "the display snapshot lives five minutes (user revision), got "
        f"{S.snapshot().SNAPSHOT_TTL_S!r}"
    )
    assert S.snapshot().DISPLAY_ROWS == 20


async def test_two_reads_inside_the_ttl_share_one_snapshot_and_send_no_request(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        first = await harness.create_screen()
        assert S.provider_requests(harness.web) == 1

        second = await harness.create_screen()

    assert second["screen_id"] == first["screen_id"], (
        "a second read 0 seconds later must reuse the stored snapshot, got a new "
        f"screen_id ({first['screen_id']} -> {second['screen_id']})"
    )
    assert second["as_of"] == first["as_of"]
    assert S.provider_requests(harness.web) == 1, (
        "the second read inside the 300 s window must send zero provider requests, "
        f"sent {S.provider_requests(harness.web) - 1}"
    )
    assert [row["symbol"] for row in second["rows"]] == [
        row["symbol"] for row in first["rows"]
    ], "the same snapshot must render the same top 20"


async def test_a_read_after_three_hundred_seconds_refreshes_the_snapshot(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        first = await harness.create_screen()
        S.age_screen(harness.db_path, first["screen_id"])

        refreshed = await harness.create_screen()

    assert refreshed["screen_id"] != first["screen_id"], (
        "a snapshot older than 300 s must be replaced, not served with a stale as_of"
    )
    assert S.provider_requests(harness.web) == 2, (
        "the refresh is one new scan; got "
        f"{S.provider_requests(harness.web)} requests in total"
    )
    assert S.parse_dt(refreshed["as_of"]) >= S.parse_dt(first["as_of"])
    assert refreshed["matched"] == S.US_TOTAL


# ---------------------------------------------------------------------------
# Sorting
# ---------------------------------------------------------------------------


async def test_market_cap_sort_orders_the_universe_by_size(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        page = await harness.create_screen(sort="market_cap")

    caps = [row["market_cap_usd"] for row in page["rows"]]
    assert caps == sorted(caps, reverse=True), f"market_cap is not descending: {caps[:5]}"
    assert [row["symbol"] for row in page["rows"][:3]] == ["AAPL", "NVDA", "MSFT"]


async def test_turnover_sort_orders_by_traded_value(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        page = await harness.create_screen(sort="turnover")

    values = [row["turnover"] for row in page["rows"]]
    assert all(isinstance(value, (int, float)) for value in values), values[:3]
    assert values == sorted(values, reverse=True), f"turnover is not descending: {values[:5]}"
    assert [row["symbol"] for row in page["rows"][:3]] == ["NVDA", "AAPL", "TSLA"]


async def test_gainers_sort_orders_by_change_percent(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        page = await harness.create_screen(sort="gainers")

    changes = [row["change_pct"] for row in page["rows"]]
    assert changes == sorted(changes, reverse=True), f"gainers is not descending: {changes[:5]}"
    assert [row["symbol"] for row in page["rows"][:3]] == ["NVDA", "ASML", "AAPL"]


# ---------------------------------------------------------------------------
# Tabs and storage
# ---------------------------------------------------------------------------


async def test_global_tab_serves_ex_us_issuers_only(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        page = await harness.create_screen(region="global")

    assert page["region"] == "global"
    assert page["matched"] == S.GLOBAL_TOTAL
    assert len(page["rows"]) == S.DISPLAY_ROWS
    symbols = {row["symbol"] for row in page["rows"]}
    assert "NVDA" not in symbols and "ASML" not in symbols, (
        f"Global must not repeat a US-listed issuer: {sorted(symbols)}"
    )
    assert not {row["exchange"] for row in page["rows"]} & S.US_EXCHANGES


async def test_us_and_global_displays_share_no_symbol(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        us = await harness.create_screen(region="us")
        glob = await harness.create_screen(region="global")

    overlap = {row["symbol"] for row in us["rows"]} & {
        row["symbol"] for row in glob["rows"]
    }
    assert overlap == set(), f"the two tabs overlap: {sorted(overlap)}"


async def test_screen_rows_stores_only_the_twenty_displayed_rows(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        page = await harness.create_screen()
        stored = S.stored_rows(harness.db_path, page["screen_id"])
        screen_row = S.stored_screen(harness.db_path, page["screen_id"])

    assert len(stored) == S.DISPLAY_ROWS, (
        "screen_rows is the display table: it holds the 20 rows on screen, not the "
        f"whole universe; got {len(stored)} rows"
    )
    assert [row["rank"] for row in stored] == list(range(1, S.DISPLAY_ROWS + 1))
    assert screen_row["total_count"] == S.US_TOTAL, (
        "the snapshot still has to remember the full universe size so the display "
        f"can say total={S.US_TOTAL} while serving 20 rows"
    )
    assert screen_row["region"] == "us"
    assert screen_row["sort"] == "market_cap"
    assert screen_row["source"] == "tradingview"


async def test_an_unknown_region_is_rejected(tmp_path: Path, monkeypatch: Any) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        response = await harness.client.get(
            "/screens", params={"region": "idx", "sort": "market_cap"}
        )

    assert response.status_code == 422, (
        f"region is a closed vocabulary (us|global), got {response.status_code}: "
        f"{response.text}"
    )
