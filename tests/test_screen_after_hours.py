"""After-hours close/change: the provider's post-market print, and its absence.

Two claims are tested here, because they are the two ways this feature could
lie:

* the **shape** really carries the post-market numbers — ``SourceQuote``,
  ``QuoteTick`` and the v7 batch parser all keep ``postMarketPrice`` /
  ``postMarketChangePercent`` when the provider sends them, and leave them
  ``None`` (never ``0``) when it does not;
* a row the snapshot did not price matches **no** bucket, and a snapshot
  captured during the regular session says so instead of answering from the
  previous evening's numbers.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from app.market.quote import QuoteTick, SourceQuote
from app.market.sources import parse_yahoo_quotes
from app.screen import after_hours, buckets, descriptive
from tests import _screen as S

STAMP = datetime(2026, 11, 1, 21, 0, tzinfo=UTC)


def snapshot_document(rows: dict[str, Any], *, session: str = "post") -> dict[str, Any]:
    return {
        "as_of": "2026-11-01",
        "captured_at": "2026-11-01T21:00:00+00:00",
        "session": session,
        "source": {"name": "yahoo", "symbols": len(rows) + 2, "requests": 1},
        "rows": rows,
    }


def use_snapshot(tmp_path: Path, monkeypatch: Any, document: dict[str, Any]) -> None:
    path = tmp_path / "after_hours.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    monkeypatch.setenv(after_hours.PATH_ENV, str(path))
    after_hours.clear()


@pytest.fixture(autouse=True)
def _clear_cache() -> Any:
    after_hours.clear()
    yield
    after_hours.clear()


# ---------------------------------------------------------------------------
# The quote shape
# ---------------------------------------------------------------------------


def test_the_source_quote_carries_the_post_market_print() -> None:
    quote = SourceQuote(
        last=100.0,
        ts_exchange=STAMP,
        prev_close=99.0,
        post_market_price=101.5,
        post_market_change_pct=1.52,
    )
    assert quote.post_market_price == 101.5
    assert quote.post_market_change_pct == 1.52


def test_a_zero_post_market_price_is_not_a_quote() -> None:
    with pytest.raises(ValueError):
        SourceQuote(last=100.0, ts_exchange=STAMP, post_market_price=0.0)


def test_the_tick_keeps_the_print_through_the_engine() -> None:
    tick = QuoteTick(
        symbol="AAPL",
        last=100.0,
        prev_close=99.0,
        volume=1_000.0,
        ts_exchange=STAMP,
        ts_received=STAMP,
        source="yahoo",
        post_market_price=101.5,
        post_market_change_pct=1.52,
    )
    assert tick.post_market_price == 101.5
    assert tick.post_market_change_pct == 1.52
    # The regular-session fields keep their own meaning: the change is against
    # the previous close, not against the post-market print.
    assert tick.change_pct == pytest.approx((100.0 / 99.0 - 1.0) * 100.0)


def test_yahoo_quotes_parse_the_post_market_fields() -> None:
    payload = {
        "quoteResponse": {
            "result": [
                {
                    "symbol": "AAPL",
                    "regularMarketPrice": 100.0,
                    "regularMarketPreviousClose": 99.0,
                    "regularMarketTime": 1_760_000_000,
                    "postMarketPrice": 101.5,
                    "postMarketChangePercent": 1.52,
                },
                {
                    "symbol": "MSFT",
                    "regularMarketPrice": 200.0,
                    "regularMarketTime": 1_760_000_000,
                },
            ]
        }
    }
    quotes = parse_yahoo_quotes(payload, ("AAPL", "MSFT"))
    apple, microsoft = quotes["AAPL"], quotes["MSFT"]
    assert apple is not None and microsoft is not None
    assert apple.post_market_price == 101.5
    assert apple.post_market_change_pct == 1.52
    assert microsoft.post_market_price is None, "absent is absent, never zero"
    assert microsoft.post_market_change_pct is None


def test_the_v7_batch_row_maps_the_cells_the_filter_reads() -> None:
    from app.market.yahoo_batch import quote_row

    row = quote_row(
        {
            "symbol": "AAPL",
            "regularMarketPrice": 100.0,
            "postMarketPrice": 101.5,
            "postMarketChangePercent": 1.52,
        }
    )
    assert row["post_market_price"] == 101.5
    assert row["post_market_change_pct"] == 1.52


# ---------------------------------------------------------------------------
# The snapshot and its reasons
# ---------------------------------------------------------------------------


def test_the_snapshot_attaches_two_cells_only_where_it_priced(
    tmp_path: Path, monkeypatch: Any
) -> None:
    use_snapshot(
        tmp_path,
        monkeypatch,
        snapshot_document({"AAPL": {"close": 101.5, "change": 1.52}, "BRK-B": {"close": 470.0}}),
    )
    rows = [
        {"symbol": "AAPL", "price": 100.0},
        {"symbol": "BRK.B", "price": 468.0},
        {"symbol": "ZZZZ", "price": 5.0},
    ]
    enriched = after_hours.enrich_rows(rows)

    assert enriched[0][after_hours.CLOSE_CELL] == 101.5
    assert enriched[0][after_hours.CHANGE_CELL] == 1.52
    assert enriched[1][after_hours.CLOSE_CELL] == 470.0, "BRK-B folds to BRK.B"
    assert after_hours.CHANGE_CELL not in enriched[1]
    assert after_hours.CLOSE_CELL not in enriched[2], "no print, no cell"
    assert rows[0] == {"symbol": "AAPL", "price": 100.0}, "the input is not mutated"


def test_a_regular_session_capture_refuses_and_says_why() -> None:
    snapshot = after_hours.parse(snapshot_document({"AAPL": {"close": 101.5}}, session="regular"))
    assert snapshot.available is False
    reason = snapshot.reason()
    assert reason and "regular session" in reason
    assert "postMarketPrice" in reason


def test_a_capture_with_no_print_refuses_with_the_measured_count() -> None:
    snapshot = after_hours.parse(snapshot_document({}))
    assert snapshot.available is False
    reason = snapshot.reason()
    assert reason and "0 answered with postMarketPrice" in reason


def test_the_session_clock_names_the_extended_day() -> None:
    def at(text: str) -> str:
        return after_hours.session_at(datetime.fromisoformat(text))

    assert at("2026-10-30T13:00:00+00:00") == "pre"  # 09:00 New York, before the bell
    assert at("2026-10-30T14:00:00+00:00") == "regular"  # 10:00 New York
    assert at("2026-10-30T21:00:00+00:00") == "post"  # 17:00 New York
    assert at("2026-10-30T23:30:00+00:00") == "post"  # 19:30 New York
    assert at("2026-10-31T00:30:00+00:00") == "closed"  # 20:30 New York
    assert at("2026-10-31T15:00:00+00:00") == "closed", "a Saturday is closed"


# ---------------------------------------------------------------------------
# The predicates
# ---------------------------------------------------------------------------


def test_ah_close_bands_read_the_close_cell() -> None:
    row = {"symbol": "AAPL", after_hours.CLOSE_CELL: 4.0}
    assert buckets.bucket("ah_close:u5").predicate(row, buckets.Context.at("2026-11-01"))
    assert not buckets.bucket("ah_close:o5").predicate(row, buckets.Context.at("2026-11-01"))
    assert buckets.bucket("ah_close:1to5").predicate(row, buckets.Context.at("2026-11-01"))


def test_ah_change_reads_direction_not_range() -> None:
    up = {"symbol": "AAPL", after_hours.CHANGE_CELL: 7.5}
    down = {"symbol": "MSFT", after_hours.CHANGE_CELL: -6.0}
    context = buckets.Context.at("2026-11-01")

    assert buckets.bucket("ah_change:u").predicate(up, context), "up at all"
    assert buckets.bucket("ah_change:u5").predicate(up, context), "up at least 5%"
    assert buckets.bucket("ah_change:u10").predicate(up, context) is False
    assert buckets.bucket("ah_change:d5").predicate(down, context), "down at least 5%"
    assert buckets.bucket("ah_change:d5").predicate(up, context) is False
    assert buckets.bucket("ah_change:u").predicate(down, context) is False


def test_a_symbol_without_a_print_matches_no_bucket(
    tmp_path: Path, monkeypatch: Any
) -> None:
    use_snapshot(tmp_path, monkeypatch, snapshot_document({"AAPL": {"close": 4.0, "change": 1.0}}))
    context = buckets.Context.at("2026-11-01")
    for identifier in ("ah_close:u5", "ah_close:o1", "ah_change:u", "ah_close:u10"):
        bucket = buckets.bucket(identifier)
        assert bucket.predicate({"symbol": "ZZZZ"}, context) is False, identifier
    # And a row the snapshot did price matches on the same predicate.
    assert buckets.bucket("ah_close:u5").predicate(
        {"symbol": "AAPL", after_hours.CLOSE_CELL: 4.0}, context
    )


def test_the_document_publishes_the_measured_coverage() -> None:
    document = descriptive.document()
    block = document["after_hours"]
    assert block["symbols"] > 0 and block["priced"] > 0
    assert block["priced"] <= block["symbols"]
    assert block["session"] in after_hours.SESSIONS
    assert block["as_of"], "a coverage count without a stamp is a claim"


# ---------------------------------------------------------------------------
# Over HTTP: the same filters, on the plan
# ---------------------------------------------------------------------------


async def test_the_after_hours_filter_answers_on_the_premium_plan(
    tmp_path: Path, monkeypatch: Any
) -> None:
    priced = after_hours.load().rows
    symbols = sorted(priced)[:5]
    rows = [{"symbol": symbol, "price": 100.0, "market_cap_usd": 1e9} for symbol in symbols]
    expected = sum(
        1
        for symbol in symbols
        if priced[symbol].change is not None and float(priced[symbol].change or 0.0) > 0
    )

    async with S.screen_client(tmp_path, monkeypatch) as harness:
        from app.api import screens as screens_api

        monkeypatch.setattr(screens_api, "build_universe", lambda _rows, **_: list(rows))
        denied = await harness.client.post(
            "/screens/select",
            json={
                "mode": "criteria",
                "region": "us",
                "criteria": {
                    "filters": [{"key": "ah_change", "op": "eq", "value": "ah_change:u"}],
                    "limit": 100,
                },
            },
        )
        allowed = await harness.client.post(
            "/screens/select",
            json={
                "mode": "criteria",
                "region": "us",
                "criteria": {
                    "filters": [{"key": "ah_change", "op": "eq", "value": "ah_change:u"}],
                    "limit": 100,
                },
            },
            headers={"X-Plan": "premium"},
        )

    assert denied.status_code == 403
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["total"] == expected
