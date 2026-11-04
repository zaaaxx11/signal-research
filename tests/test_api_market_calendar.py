"""Contract for ``GET /market/calendar`` — earnings dates inside a window.

What is pinned here:

* **the window is exact** — every bucket's date is inside ``[from, to]``, buckets
  are ordered by date, and ``to`` is ``from + days``;
* **the two stored spellings parse** — ISO (``2026-11-05``) and the page's own
  (``Nov 5 BMO``), while a null, a blank, an impossible day and a past date
  contribute nothing (a null can never land on today);
* **``days`` clamps** — ``0`` is one day, ``999`` is sixty, ``abc`` is a 422;
* **a bucket truncates at 50 and its ``count`` does not** — the true total rides
  beside the fifty largest names, ordered by market cap;
* **a region with no date answers ``buckets: []``** — a statement, not a 404;
* **the validator is the store's file version plus the window** — no scan, so
  ``X-Scan-Cache: depth``, a new file version is a new tag, and a matching
  ``If-None-Match`` is a ``304`` with no parse at all (proved by making the
  reader raise).

The suite is hermetic: the depth store is a real JSON file in ``tmp_path``, named
before the app is built, and nothing reaches the network.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from tests import _market as M

_DOCUMENT_KEYS = frozenset({"as_of", "region", "from", "to", "buckets"})
_BUCKET_KEYS = frozenset({"date", "count", "symbols"})
_SYMBOL_KEYS = frozenset({"s", "n"})

#: A fixed "today" so the expectations do not depend on when the suite runs.
TODAY = date(2026, 10, 31)


def _freeze(monkeypatch: pytest.MonkeyPatch, day: date = TODAY) -> None:
    """Pin the route's clock. Read per request, so when this runs does not matter."""

    from app.api import calendar as calendar_api

    monkeypatch.setattr(calendar_api, "_today", lambda: day)


def _row(
    symbol: str,
    when: Any,
    *,
    cap: float | None = 1e9,
    name: str | None = None,
    country: str = "United States",
) -> dict[str, Any]:
    """One depth-store row, in the cells the calendar reads."""

    row: dict[str, Any] = {
        "company": name or f"{symbol} Incorporated",
        "country": country,
        "exchange": "XNAS",
    }
    if cap is not None:
        row["market_cap"] = cap
    if when is not None:
        row["earnings_date"] = when
    return row


# ---------------------------------------------------------------------------
# The window
# ---------------------------------------------------------------------------


async def test_buckets_are_sorted_and_inside_the_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    depth = {
        "AAA": _row("AAA", "2026-11-05"),
        "BBB": _row("BBB", "Nov 3 BMO"),
        "CCC": _row("CCC", "2026-10-31"),
        "DDD": _row("DDD", "2026-11-25"),
        "EEE": _row("EEE", "2026-10-30"),
    }
    async with M.market_client(tmp_path, monkeypatch, rows=[], depth=depth) as harness:
        _freeze(monkeypatch)
        body = (await harness.get("/market/calendar", region="us", days="21")).json()

    assert set(body) == _DOCUMENT_KEYS, sorted(body)
    assert body["region"] == "us"
    assert body["from"] == "2026-10-31"
    assert body["to"] == "2026-11-21"
    assert [bucket["date"] for bucket in body["buckets"]] == ["2026-10-31", "2026-11-03", "2026-11-05"]
    assert all(set(bucket) == _BUCKET_KEYS for bucket in body["buckets"])
    for bucket in body["buckets"]:
        assert body["from"] <= bucket["date"] <= body["to"]
        assert all(set(symbol) == _SYMBOL_KEYS for symbol in bucket["symbols"])
    dates = [bucket["date"] for bucket in body["buckets"]]
    assert dates == sorted(dates)


async def test_the_default_window_is_twenty_one_days(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    depth = {"AAA": _row("AAA", "2026-11-20"), "BBB": _row("BBB", "2026-11-21")}
    async with M.market_client(tmp_path, monkeypatch, rows=[], depth=depth) as harness:
        _freeze(monkeypatch)
        body = (await harness.get("/market/calendar", region="us")).json()

    assert body["from"] == "2026-10-31"
    assert body["to"] == "2026-11-21"
    assert [bucket["date"] for bucket in body["buckets"]] == ["2026-11-20", "2026-11-21"]


# ---------------------------------------------------------------------------
# The two spellings, and what is not a date
# ---------------------------------------------------------------------------


async def test_both_stored_spellings_land_on_their_day(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ISO and the page's ``Nov 5 BMO`` are one calendar, not two sources."""

    depth = {
        "AAA": _row("AAA", "2026-11-05"),
        "BBB": _row("BBB", "Nov 5 BMO"),
        "CCC": _row("CCC", "November 5 AMC"),
        "DDD": _row("DDD", "Nov 5"),
    }
    async with M.market_client(tmp_path, monkeypatch, rows=[], depth=depth) as harness:
        _freeze(monkeypatch)
        body = (await harness.get("/market/calendar", region="us", days="21")).json()

    assert len(body["buckets"]) == 1
    assert body["buckets"][0]["date"] == "2026-11-05"
    assert body["buckets"][0]["count"] == 4
    assert {symbol["s"] for symbol in body["buckets"][0]["symbols"]} == {
        "AAA",
        "BBB",
        "CCC",
        "DDD",
    }


async def test_a_null_earnings_date_never_lands_on_today(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A null, a blank and a missing cell are absence — never today's date."""

    depth = {
        "AAA": _row("AAA", None),
        "BBB": {"company": "B", "earnings_date": "", "market_cap": 1e9},
        "CCC": {"company": "C", "earnings_date": None, "market_cap": 1e9},
        "DDD": {"company": "D", "market_cap": 1e9},
    }
    async with M.market_client(tmp_path, monkeypatch, rows=[], depth=depth) as harness:
        _freeze(monkeypatch)
        body = (await harness.get("/market/calendar", region="us", days="21")).json()

    assert body["buckets"] == []


async def test_a_cell_that_is_not_a_date_contributes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    depth = {
        "AAA": _row("AAA", "Feb 30"),  # impossible day
        "BBB": _row("BBB", "2026-13-01"),  # impossible month
        "CCC": _row("CCC", 20261105),  # a number, not text
        "DDD": _row("DDD", "next Tuesday"),
        "EEE": _row("EEE", "2026-11-05T00:00"),
        "FFF": _row("FFF", "2026-11-05"),  # the one real date
    }
    async with M.market_client(tmp_path, monkeypatch, rows=[], depth=depth) as harness:
        _freeze(monkeypatch)
        body = (await harness.get("/market/calendar", region="us", days="21")).json()

    assert len(body["buckets"]) == 1
    assert [symbol["s"] for symbol in body["buckets"][0]["symbols"]] == ["FFF"]


async def test_a_year_less_date_is_the_next_occurrence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``Jan 5`` asked on 30 December is next year's, and lands in the window."""

    depth = {"AAA": _row("AAA", "Jan 5")}
    async with M.market_client(tmp_path, monkeypatch, rows=[], depth=depth) as harness:
        _freeze(monkeypatch, date(2026, 12, 30))
        body = (await harness.get("/market/calendar", region="us", days="21")).json()

    assert body["from"] == "2026-12-30"
    assert body["to"] == "2027-01-20"
    assert [bucket["date"] for bucket in body["buckets"]] == ["2027-01-05"]


async def test_a_past_date_is_not_published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    depth = {"AAA": _row("AAA", "2026-10-30"), "BBB": _row("BBB", "Sep 28")}
    async with M.market_client(tmp_path, monkeypatch, rows=[], depth=depth) as harness:
        _freeze(monkeypatch)
        body = (await harness.get("/market/calendar", region="us", days="21")).json()

    assert body["buckets"] == []


# ---------------------------------------------------------------------------
# The clamp on days (adversarial (b))
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("asked", "expected"),
    [("0", 1), ("-5", 1), ("1", 1), ("21", 21), ("60", 60), ("999", 60)],
)
async def test_days_is_clamped_to_one_and_sixty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, asked: str, expected: int
) -> None:
    """``days=0`` is today; ``days=999`` is two months. Both are answers."""

    async with M.market_client(tmp_path, monkeypatch, rows=[], depth={}) as harness:
        _freeze(monkeypatch)
        response = await harness.get("/market/calendar", region="us", days=asked)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["from"] == "2026-10-31"
    assert body["to"] == (date(2026, 10, 31) + timedelta(days=expected)).isoformat()
    assert body["buckets"] == []


async def test_a_days_that_is_not_a_number_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with M.market_client(tmp_path, monkeypatch, rows=[], depth={}) as harness:
        _freeze(monkeypatch)
        response = await harness.get("/market/calendar", region="us", days="soon")

    assert response.status_code == 422
    assert response.json()["error"] == "invalid_days"


# ---------------------------------------------------------------------------
# Truncation and order inside a bucket
# ---------------------------------------------------------------------------


async def test_a_bucket_ships_fifty_names_and_counts_them_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``count`` is the true total; ``symbols`` is the fifty largest."""

    depth = {
        f"S{index:03d}": _row(f"S{index:03d}", "2026-11-05", cap=1e9 * (index + 1))
        for index in range(60)
    }
    async with M.market_client(tmp_path, monkeypatch, rows=[], depth=depth) as harness:
        _freeze(monkeypatch)
        body = (await harness.get("/market/calendar", region="us", days="21")).json()

    bucket = body["buckets"][0]
    assert bucket["count"] == 60
    assert len(bucket["symbols"]) == 50
    assert [symbol["s"] for symbol in bucket["symbols"]] == [
        f"S{index:03d}" for index in range(59, 9, -1)
    ], "the fifty published must be the fifty largest, largest first"


async def test_a_name_travels_with_its_symbol(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    depth = {"AAA": _row("AAA", "2026-11-05", name="Acme Holdings")}
    async with M.market_client(tmp_path, monkeypatch, rows=[], depth=depth) as harness:
        _freeze(monkeypatch)
        body = (await harness.get("/market/calendar", region="us", days="21")).json()

    assert body["buckets"][0]["symbols"] == [{"s": "AAA", "n": "Acme Holdings"}]


# ---------------------------------------------------------------------------
# Region
# ---------------------------------------------------------------------------


async def test_a_region_with_no_date_answers_an_empty_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``global`` over a store of US rows is an empty calendar, not a 404."""

    depth = {"AAA": _row("AAA", "2026-11-05")}
    async with M.market_client(tmp_path, monkeypatch, rows=[], depth=depth) as harness:
        _freeze(monkeypatch)
        global_body = (await harness.get("/market/calendar", region="global")).json()
        us_body = (await harness.get("/market/calendar", region="us")).json()

    assert global_body["buckets"] == []
    assert global_body["region"] == "global"
    assert us_body["buckets"], "the US tab still has its date"


async def test_a_foreign_row_belongs_to_the_global_tab(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    depth = {
        "AAA": _row("AAA", "2026-11-05"),
        "BBB": _row("BBB", "2026-11-05", country="Japan"),
    }
    async with M.market_client(tmp_path, monkeypatch, rows=[], depth=depth) as harness:
        _freeze(monkeypatch)
        us_body = (await harness.get("/market/calendar", region="us")).json()
        global_body = (await harness.get("/market/calendar", region="global")).json()

    assert [s["s"] for s in us_body["buckets"][0]["symbols"]] == ["AAA"]
    assert [s["s"] for s in global_body["buckets"][0]["symbols"]] == ["BBB"]


async def test_an_unknown_region_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with M.market_client(tmp_path, monkeypatch, rows=[], depth={}) as harness:
        response = await harness.get("/market/calendar", region="mars")

    assert response.status_code == 422
    assert response.json()["error"] == "invalid_region"


# ---------------------------------------------------------------------------
# Revalidation: the store's version and the window
# ---------------------------------------------------------------------------


async def test_the_tag_moves_with_the_store_and_a_repeat_is_a_304(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with M.market_client(
        tmp_path, monkeypatch, rows=[], depth={"AAA": _row("AAA", "2026-11-05")}
    ) as harness:
        _freeze(monkeypatch)
        first = await harness.get("/market/calendar", region="us", days="21")
        second = await harness.get("/market/calendar", region="us", days="21")
        revalidated = await harness.client.get(
            "/market/calendar",
            params={"region": "us", "days": "21"},
            headers={"if-none-match": first.headers["etag"]},
        )
        harness.write_depth({"AAA": _row("AAA", "2026-11-06")})
        rewritten = await harness.get("/market/calendar", region="us", days="21")

    assert first.headers["etag"] == second.headers["etag"]
    assert first.headers["etag"].startswith('W/"cal-')
    assert first.headers["cache-control"] == "no-cache"
    assert first.headers["x-scan-cache"] == "depth"
    assert revalidated.status_code == 304
    assert revalidated.content == b""
    assert revalidated.headers["x-scan-cache"] == "depth"
    assert rewritten.headers["etag"] != first.headers["etag"], (
        "a new store version is a new entity"
    )
    assert rewritten.json()["buckets"][0]["date"] == "2026-11-06"


async def test_a_304_is_decided_without_reading_the_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reader raises, and the revalidation still answers 304: one stat, no parse."""

    from app.api import calendar as calendar_api

    async with M.market_client(
        tmp_path, monkeypatch, rows=[], depth={"AAA": _row("AAA", "2026-11-05")}
    ) as harness:
        _freeze(monkeypatch)
        first = await harness.get("/market/calendar", region="us", days="21")

        def explode(*_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError("a 304 must not read the depth store")

        monkeypatch.setattr(calendar_api, "current_depth", explode)
        second = await harness.client.get(
            "/market/calendar",
            params={"region": "us", "days": "21"},
            headers={"if-none-match": first.headers["etag"]},
        )

    assert second.status_code == 304, second.text


async def test_head_answers_the_get_headers_and_no_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with M.market_client(
        tmp_path, monkeypatch, rows=[], depth={"AAA": _row("AAA", "2026-11-05")}
    ) as harness:
        _freeze(monkeypatch)
        get = await harness.get("/market/calendar", region="us", days="21")
        head = await harness.client.head(
            "/market/calendar", params={"region": "us", "days": "21"}
        )
        revalidated = await harness.client.head(
            "/market/calendar",
            params={"region": "us", "days": "21"},
            headers={"if-none-match": head.headers["etag"]},
        )

    assert head.status_code == 200
    assert head.content == b""
    assert head.headers["etag"] == get.headers["etag"]
    assert head.headers["content-length"] == str(len(get.content))
    assert revalidated.status_code == 304


async def test_the_moment_of_the_store_is_the_as_of(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Earnings dates do not change when the request happened to be served."""

    async with M.market_client(
        tmp_path, monkeypatch, rows=[], depth={"AAA": _row("AAA", "2026-11-05")}
    ) as harness:
        harness.write_depth(
            {"AAA": _row("AAA", "2026-11-05")}, as_of="2026-10-30T22:15:00+00:00"
        )
        _freeze(monkeypatch)
        body = (await harness.get("/market/calendar", region="us", days="21")).json()

    assert body["as_of"] == "2026-10-30T22:15:00+00:00"


async def test_a_store_with_no_dates_is_an_empty_calendar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No depth store at all: ``buckets: []`` and a 200, never a 500."""

    async with M.market_client(tmp_path, monkeypatch, rows=[], depth=None) as harness:
        _freeze(monkeypatch)
        response = await harness.get("/market/calendar", region="us", days="21")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["buckets"] == []
    assert set(body) == _DOCUMENT_KEYS
    assert len(json.dumps(body)) < 400


# ---------------------------------------------------------------------------
# Kinds: dividends and economic
# ---------------------------------------------------------------------------


async def test_dividends_answers_dividend_ex_dates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``kind=dividends`` reads ``dividend_ex_date``, not ``earnings_date``."""

    depth = {
        "AAA": {**_row("AAA", "2026-11-05"), "dividend_ex_date": "2026-11-12"},
        "BBB": {**_row("BBB", "2026-11-06"), "dividend_ex_date": "2026-11-13"},
        "CCC": {**_row("CCC", "2026-11-07")},  # no dividend date
    }
    async with M.market_client(tmp_path, monkeypatch, rows=[], depth=depth) as harness:
        _freeze(monkeypatch)
        body = (await harness.get("/market/calendar", region="us", days="21", kind="dividends")).json()

    assert body["from"] == "2026-10-31"
    assert body["to"] == "2026-11-21"
    assert [bucket["date"] for bucket in body["buckets"]] == ["2026-11-12", "2026-11-13"]
    assert [s["s"] for s in body["buckets"][0]["symbols"]] == ["AAA"]
    assert [s["s"] for s in body["buckets"][1]["symbols"]] == ["BBB"]


async def test_dividends_and_earnings_share_no_memo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The two kinds are separate documents, not the same cached body."""

    depth = {
        "AAA": {**_row("AAA", "2026-11-05"), "dividend_ex_date": "2026-11-05"},
    }
    async with M.market_client(tmp_path, monkeypatch, rows=[], depth=depth) as harness:
        _freeze(monkeypatch)
        earnings = (await harness.get("/market/calendar", region="us", days="21", kind="earnings")).json()
        dividends = (await harness.get("/market/calendar", region="us", days="21", kind="dividends")).json()

    assert [s["s"] for s in earnings["buckets"][0]["symbols"]] == ["AAA"]
    assert [s["s"] for s in dividends["buckets"][0]["symbols"]] == ["AAA"]


async def test_economic_is_honest_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``kind=economic`` answers ``available: false`` with a named reason, not an error."""

    async with M.market_client(tmp_path, monkeypatch, rows=[], depth={}) as harness:
        _freeze(monkeypatch)
        response = await harness.get("/market/calendar", region="us", days="21", kind="economic")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["available"] is False
    assert body["reason"] == "no economic calendar source"
    assert body["from"] == "2026-10-31"
    assert body["to"] == "2026-11-21"
    assert body["buckets"] == []


async def test_an_unknown_kind_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async with M.market_client(tmp_path, monkeypatch, rows=[], depth={}) as harness:
        response = await harness.get("/market/calendar", region="us", kind="weather")

    assert response.status_code == 422
    assert response.json()["error"] == "invalid_kind"
