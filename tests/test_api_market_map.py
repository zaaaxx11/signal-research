"""Contract for ``GET /market/map`` — the treemap's whole input, in one payload.

What is pinned here, in the order the module docstring argues it:

* **the population is the selector's** — ``universe_total`` equals
  ``POST /screens/select``'s, ``sum(sector.count)`` equals ``universe_total``, and
  the same scan serves both routes;
* **a tile needs both numbers** — a row without a cap (or without a change) is
  still in the universe and in its sector's ``count``, and is not in ``symbols``;
  ``mapped`` is the number of rows that made it;
* **a sector's ``value``** is the cap-weighted mean change of its members, and it
  is the same number ``/market/groups`` publishes for that sector;
* **the cap unit is USD billions, one decimal, with 0.1 as the floor** — a
  USD 40 M company still owns a tile, and a 1.2 vs 1.25 billion pair ties;
* **a null sector is ``_unclassified``**, published, never folded into "Other";
* **the payload fits** — under 250 KB raw and under 40 KB brotli over 5,576
  rows, asserted on the serialised bytes;
* **revalidatable from its inputs** — a matching ``If-None-Match`` is a ``304``
  with no build (proved by making the builder raise), ``Cache-Control: no-cache``
  on both answers, and ``HEAD`` as its own route with the GET's length and none
  of its bytes.

The suite is hermetic: the scanner is a ``MockWeb`` over a handwritten scan body
and nothing reaches the network (``tests/conftest.py`` fails the test if it does).
"""

from __future__ import annotations

import json
import statistics
import time
import zlib
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from tests import _market as M

#: Every key the document carries — nothing more, nothing less.
_DOCUMENT_KEYS = frozenset(
    {"as_of", "region", "metric", "universe_total", "mapped", "sectors"}
)
#: Every key one sector carries.
_SECTOR_KEYS = frozenset({"id", "label", "count", "value", "adv", "dec", "symbols"})
#: Every key one tile carries.
_SYMBOL_KEYS = frozenset({"s", "c", "v"})

#: The budget the route promises: raw bytes and brotli bytes over 5,576 rows.
_RAW_BUDGET = 250 * 1024
_BROTLI_BUDGET = 40 * 1024
#: A zlib-level-9 payload must stay under this, as a proxy for the brotli figure.
_ZLIB_BUDGET = 45 * 1024


def _sectors(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {sector["id"]: sector for sector in body["sectors"]}


def _tile(sector: dict[str, Any], symbol: str) -> dict[str, Any]:
    return next(tile for tile in sector["symbols"] if tile["s"] == symbol)


# ---------------------------------------------------------------------------
# Shape, totals and the equality with the selector
# ---------------------------------------------------------------------------


async def test_the_document_and_its_rows_have_the_contracted_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keys are the contract: a client draws tiles without looking anything up."""

    rows = [
        M.Row("AAA", "Electronic Technology", 500e9, 2.5),
        M.Row("BBB", "Finance", 100e9, -1.5),
    ]
    async with M.market_client(tmp_path, monkeypatch, rows=rows) as harness:
        response = await harness.get("/market/map", region="us", metric="change")

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == _DOCUMENT_KEYS, sorted(body)
    assert body["region"] == "us"
    assert body["metric"] == "change"
    assert body["as_of"]
    assert body["universe_total"] == 2
    assert body["mapped"] == 2
    assert all(set(sector) == _SECTOR_KEYS for sector in body["sectors"])
    for sector in body["sectors"]:
        assert all(set(tile) == _SYMBOL_KEYS for tile in sector["symbols"])
        assert sector["symbols"], "a published sector with no tile would draw an empty box"


async def test_the_totals_equal_the_selector_and_its_own_parts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``universe_total`` is the selector's, and the sectors partition it."""

    rows = [
        M.Row("AAA", "Electronic Technology", 500e9, 2.0),
        M.Row("BBB", "Electronic Technology", 100e9, -1.0),
        M.Row("CCC", "Finance", 800e9, 0.5),
        M.Row("DDD", "Health Technology", 40e9, None),
    ]
    async with M.market_client(tmp_path, monkeypatch, rows=rows) as harness:
        body = (await harness.get("/market/map", region="us")).json()
        selected = await harness.client.post("/screens/select", json=M.selection())

    assert selected.status_code == 200, selected.text
    assert body["universe_total"] == selected.json()["universe_total"] == 4
    assert sum(sector["count"] for sector in body["sectors"]) == body["universe_total"]
    assert sum(sector["adv"] for sector in body["sectors"]) == 2
    assert sum(sector["dec"] for sector in body["sectors"]) == 1
    assert body["mapped"] == 3, "the row with no change cannot become a tile"


async def test_one_scan_serves_the_map_and_the_selector(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The two routes key the scan cache identically: one provider request."""

    rows = [M.Row("AAA", "Finance", 1e9, 1.0), M.Row("BBB", "Finance", 2e9, -1.0)]
    async with M.market_client(tmp_path, monkeypatch, rows=rows) as harness:
        assert (await harness.get("/market/map", region="us")).status_code == 200
        after_map = harness.web.count("tradingview")
        assert after_map == 1
        answer = await harness.client.post("/screens/select", json=M.selection())
        assert answer.status_code == 200, answer.text
        assert harness.web.count("tradingview") == after_map, (
            "the selection paid a second scan: the map keys the cache differently"
        )


async def test_an_empty_scan_is_an_empty_map_not_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A region with no rows is a statement about the scan: 200 and no sectors."""

    async with M.market_client(tmp_path, monkeypatch, rows=[]) as harness:
        response = await harness.get("/market/map", region="us")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["universe_total"] == 0
    assert body["mapped"] == 0
    assert body["sectors"] == []
    assert body["as_of"]


# ---------------------------------------------------------------------------
# A tile needs a cap and a metric (adversarial: the null-cap row)
# ---------------------------------------------------------------------------


async def test_a_cap_less_etf_is_in_the_universe_and_not_in_the_tiles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On the ETF tab a null cap is kept by the universe builder but cannot tile.

    The stock tab never sees this row at all: its floor is ``min_market_cap=0``,
    and a row with no cap cannot prove it fits. On the ETF tab the floor is
    ``None``, so the row is in ``universe_total`` and in its sector's ``count`` —
    and out of ``symbols``, which is what ``mapped`` exists to say.
    """

    rows = [
        M.Row("SPY", "Finance", None, 1.0, type="fund", subtype="etf", name="SPDR Fund"),
        M.Row("QQQ", "Finance", 200e9, 2.0, type="fund", subtype="etf", name="Invesco QQQ"),
    ]
    async with M.market_client(tmp_path, monkeypatch, rows=rows) as harness:
        body = (await harness.get("/market/map", region="us_etf")).json()

    assert body["universe_total"] == 2
    assert body["mapped"] == 1
    finance = _sectors(body)["finance"]
    assert finance["count"] == 2
    assert [tile["s"] for tile in finance["symbols"]] == ["QQQ"]
    assert _tile(finance, "QQQ")["c"] == 200.0


async def test_a_stock_without_a_cap_is_not_in_the_stock_universe_at_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The 5,678-vs-5,576 trap, stated on the map's own population."""

    rows = [
        M.Row("AAA", "Finance", 1e9, 1.0),
        M.Row("BBB", "Finance", None, 1.0),
    ]
    async with M.market_client(tmp_path, monkeypatch, rows=rows) as harness:
        body = (await harness.get("/market/map", region="us")).json()
        selected = (await harness.client.post("/screens/select", json=M.selection())).json()

    assert body["universe_total"] == selected["universe_total"] == 1
    assert body["mapped"] == 1


async def test_a_zero_cap_is_placed_with_no_size_rather_than_dropped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cap the provider reported as exactly zero is an observation, not a gap."""

    rows = [M.Row("AAA", "Finance", 0.0, 3.0), M.Row("BBB", "Finance", 1e9, 1.0)]
    async with M.market_client(tmp_path, monkeypatch, rows=rows) as harness:
        body = (await harness.get("/market/map", region="us")).json()

    finance = _sectors(body)["finance"]
    assert body["mapped"] == 2
    assert _tile(finance, "AAA")["c"] == 0.0
    # A zero weight moves nothing: the sector's value is BBB's change alone.
    assert finance["value"] == 1.0


# ---------------------------------------------------------------------------
# The unit and the rounding (adversarial (a))
# ---------------------------------------------------------------------------


async def test_caps_are_billions_with_one_decimal_and_a_floor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """USD billions, one decimal, and a positive cap never rounds to zero.

    A 1.2 vs 1.25 billion pair ties at 1.2 (one decimal is the resolution the
    contract promises), while a USD 40 M company publishes the 0.1 floor instead
    of a zero-area tile: 991 names in the live universe sit under USD 50 M, so
    this is the common case, not a corner.
    """

    rows = [
        M.Row("BIG", "Finance", 1.2e9, 1.0),
        M.Row("TIE", "Finance", 1.25e9, 1.0),
        M.Row("MICRO", "Finance", 40e6, 1.0),
        M.Row("HALF", "Finance", 49e6, 1.0),
    ]
    async with M.market_client(tmp_path, monkeypatch, rows=rows) as harness:
        finance = _sectors((await harness.get("/market/map", region="us")).json())["finance"]

    assert _tile(finance, "BIG")["c"] == 1.2
    assert _tile(finance, "TIE")["c"] == 1.2, "1.25 B at one decimal is 1.2, not 1.3"
    assert _tile(finance, "MICRO")["c"] == 0.1, "0.04 B must not vanish into a zero-area tile"
    assert _tile(finance, "HALF")["c"] == 0.1
    assert all(tile["c"] > 0 for tile in finance["symbols"])


async def test_the_metric_is_percent_with_two_decimals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``v`` is the scan's own percent, rounded to two decimals, never ``-0.0``."""

    rows = [
        M.Row("AAA", "Finance", 1e9, 1.23456),
        M.Row("BBB", "Finance", 1e9, -0.001),
        M.Row("CCC", "Finance", 1e9, -2.5),
    ]
    async with M.market_client(tmp_path, monkeypatch, rows=rows) as harness:
        finance = _sectors((await harness.get("/market/map", region="us")).json())["finance"]

    assert _tile(finance, "AAA")["v"] == 1.23
    assert _tile(finance, "BBB")["v"] == 0.0
    assert _tile(finance, "CCC")["v"] == -2.5


# ---------------------------------------------------------------------------
# The weighted value
# ---------------------------------------------------------------------------


async def test_a_sector_value_is_the_cap_weighted_mean_by_hand(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Recomputed from the tiles' own inputs: the weights are the exact caps."""

    rows = [
        M.Row("AAA", "Finance", 900e9, 2.0),
        M.Row("BBB", "Finance", 100e9, -2.0),
        M.Row("CCC", "Finance", 50e9, None),
    ]
    async with M.market_client(tmp_path, monkeypatch, rows=rows) as harness:
        body = (await harness.get("/market/map", region="us")).json()

    finance = _sectors(body)["finance"]
    expected = (900e9 * 2.0 + 100e9 * -2.0) / (900e9 + 100e9)
    assert finance["value"] == pytest.approx(expected, abs=1e-4)
    assert finance["value"] == 1.6
    assert finance["count"] == 3
    assert finance["adv"] == 1 and finance["dec"] == 1


async def test_a_sector_with_nothing_weighable_publishes_null_not_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No member carries a metric: the sector moved *unknown*, not 0.00 %."""

    rows = [M.Row("AAA", "Finance", 1e9, None)]
    async with M.market_client(tmp_path, monkeypatch, rows=rows) as harness:
        body = (await harness.get("/market/map", region="us")).json()

    assert body["sectors"][0]["value"] is None
    assert body["sectors"][0]["symbols"] == []
    assert body["mapped"] == 0


# ---------------------------------------------------------------------------
# A null sector (adversarial (c))
# ---------------------------------------------------------------------------


async def test_a_null_sector_is_published_as_unclassified_and_never_as_other(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unclassified row gets its own reserved bucket, beside a real "Other".

    Folding it into "Other" would merge "the provider sent no sector" with "the
    provider called this Miscellaneous"; dropping it would break the partition.
    """

    rows = [
        M.Row("AAA", "Other", 1e9, 1.0),
        M.Row("BBB", None, 2e9, -1.0),
        M.Row("CCC", "", 3e9, 1.0),
    ]
    async with M.market_client(tmp_path, monkeypatch, rows=rows) as harness:
        body = (await harness.get("/market/map", region="us")).json()

    sectors = _sectors(body)
    assert set(sectors) == {"other", "_unclassified"}
    assert sectors["other"]["label"] == "Other"
    assert sectors["other"]["count"] == 1
    assert sectors["_unclassified"]["label"] == "Unclassified"
    assert sectors["_unclassified"]["count"] == 2, "a blank sector is not a sector"
    assert sum(sector["count"] for sector in body["sectors"]) == body["universe_total"] == 3


# ---------------------------------------------------------------------------
# Size (the 250 KB / 40 KB budget)
# ---------------------------------------------------------------------------


async def test_the_payload_for_5576_rows_fits_its_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The serialised body over 5,576 rows, measured — not estimated."""

    rows = M.universe(5_576)
    async with M.market_client(tmp_path, monkeypatch, rows=rows) as harness:
        response = await harness.get("/market/map", region="us", metric="change")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["universe_total"] == 5_576
    assert sum(sector["count"] for sector in body["sectors"]) == 5_576

    raw = response.content
    # Exactly what the route writes (separators, no ASCII escapes), so the
    # measurement is of the payload a client receives.
    assert len(raw) == len(json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode())
    assert len(raw) < _RAW_BUDGET, f"{len(raw)} bytes raw exceeds {_RAW_BUDGET}"

    # A dependency-free proxy for the transport budget: zlib level 9 compresses
    # JSON roughly 15 % worse than brotli (40,079 against 33,392 bytes on the
    # live payload), so a body this size is comfortably inside 40 KB brotli.
    assert len(zlib.compress(raw, 9)) < _ZLIB_BUDGET

    brotli = pytest.importorskip("brotli")
    compressed = brotli.compress(raw, quality=11)
    assert len(compressed) < _BROTLI_BUDGET, (
        f"{len(compressed)} bytes brotli exceeds {_BROTLI_BUDGET}"
    )


# ---------------------------------------------------------------------------
# Revalidation: ETag, 304, HEAD
# ---------------------------------------------------------------------------


async def test_the_etag_is_stable_and_a_repeat_is_a_304(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same generation, same tag, no body — and ``no-cache`` on both answers."""

    rows = [M.Row("AAA", "Finance", 1e9, 1.0)]
    async with M.market_client(tmp_path, monkeypatch, rows=rows) as harness:
        first = await harness.get("/market/map", region="us")
        second = await harness.get("/market/map", region="us")
        assert first.headers["etag"] == second.headers["etag"]
        assert first.headers["etag"].startswith('W/"map-')
        assert first.headers["cache-control"] == "no-cache"

        revalidated = await harness.client.get(
            "/market/map",
            params={"region": "us"},
            headers={"if-none-match": first.headers["etag"]},
        )

    assert revalidated.status_code == 304
    assert revalidated.content == b""
    assert revalidated.headers["etag"] == first.headers["etag"]
    assert revalidated.headers["cache-control"] == "no-cache"
    assert revalidated.headers["x-scan-cache"] == "hit"


async def test_a_304_is_decided_before_the_document_is_built(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The builder raises, and the revalidation still answers 304: no work."""

    from app.api import map as map_api

    rows = [M.Row("AAA", "Finance", 1e9, 1.0)]
    async with M.market_client(tmp_path, monkeypatch, rows=rows) as harness:
        first = await harness.get("/market/map", region="us")

        def explode(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            raise AssertionError("a 304 must not build the document")

        monkeypatch.setattr(map_api, "build_map_document", explode)
        second = await harness.client.get(
            "/market/map",
            params={"region": "us"},
            headers={"if-none-match": first.headers["etag"]},
        )

    assert second.status_code == 304, second.text


async def test_head_answers_the_get_headers_and_no_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``HEAD`` is its own route: the GET's length, none of its bytes, same 304."""

    rows = [M.Row("AAA", "Finance", 1e9, 1.0)]
    async with M.market_client(tmp_path, monkeypatch, rows=rows) as harness:
        get = await harness.get("/market/map", region="us")
        head = await harness.client.head("/market/map", params={"region": "us"})
        revalidated = await harness.client.head(
            "/market/map",
            params={"region": "us"},
            headers={"if-none-match": head.headers["etag"]},
        )

    assert head.status_code == 200
    assert head.content == b""
    assert head.headers["etag"] == get.headers["etag"]
    assert head.headers["content-length"] == str(len(get.content))
    assert head.headers["cache-control"] == "no-cache"
    assert revalidated.status_code == 304
    assert revalidated.content == b""


async def test_a_rescan_moves_the_tag_even_when_the_rows_are_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A new generation is a new entity: the memo and the tag both move."""

    rows = [M.Row("AAA", "Finance", 1e9, 1.0)]
    async with M.market_client(tmp_path, monkeypatch, rows=rows) as harness:
        first = await harness.get("/market/map", region="us")
        harness.clock.advance(6.0)  # past the scan TTL → a second scan
        second = await harness.get("/market/map", region="us")
        stale = await harness.client.get(
            "/market/map",
            params={"region": "us"},
            headers={"if-none-match": first.headers["etag"]},
        )

    assert second.headers["etag"] != first.headers["etag"]
    assert stale.status_code == 200, "a stale tag must not be revalidated"
    assert harness.web.count("tradingview") == 2


# ---------------------------------------------------------------------------
# Timings (warm read, 304)
# ---------------------------------------------------------------------------


async def test_a_warm_read_and_a_304_are_fast(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Warm < 30 ms and 304 < 5 ms, measured over five requests each."""

    rows = M.universe(5_576)
    async with M.market_client(tmp_path, monkeypatch, rows=rows) as harness:
        first = await harness.get("/market/map", region="us")
        warm: list[float] = []
        for _ in range(5):
            started = time.perf_counter()
            assert (await harness.get("/market/map", region="us")).status_code == 200
            warm.append((time.perf_counter() - started) * 1000)
        conditional: list[float] = []
        for _ in range(5):
            started = time.perf_counter()
            answer = await harness.client.get(
                "/market/map",
                params={"region": "us"},
                headers={"if-none-match": first.headers["etag"]},
            )
            conditional.append((time.perf_counter() - started) * 1000)
            assert answer.status_code == 304

    assert statistics.median(warm) < 30.0, f"warm read {statistics.median(warm):.1f} ms"
    assert statistics.median(conditional) < 5.0, (
        f"304 took {statistics.median(conditional):.1f} ms"
    )


# ---------------------------------------------------------------------------
# Honest refusals
# ---------------------------------------------------------------------------


async def test_a_metric_the_route_cannot_publish_is_refused_before_any_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``metric=volume`` is a 422 in the house envelope, not a silent percent."""

    rows = [M.Row("AAA", "Finance", 1e9, 1.0)]
    async with M.market_client(tmp_path, monkeypatch, rows=rows) as harness:
        response = await harness.get("/market/map", region="us", metric="volume")

    assert response.status_code == 422
    body = response.json()
    assert body["error"] == "invalid_metric"
    assert "change" in body["errorMessage"]
    assert harness.web.count("tradingview") == 0


async def test_an_unknown_region_is_refused_before_any_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The refusal names the accepted tabs and costs nothing."""

    rows = [M.Row("AAA", "Finance", 1e9, 1.0)]
    async with M.market_client(tmp_path, monkeypatch, rows=rows) as harness:
        response = await harness.get("/market/map", region="mars")

    assert response.status_code == 422
    body = response.json()
    assert body["error"] == "invalid_region"
    assert "us" in body["errorMessage"]
    assert harness.web.count("tradingview") == 0


# ---------------------------------------------------------------------------
# The unit contract between the two documents
# ---------------------------------------------------------------------------


async def test_the_map_and_the_groups_publish_one_value_per_sector(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One pass, one definition: the legend and the table cannot disagree."""

    rows = [
        M.Row("AAA", "Finance", 900e9, 2.0),
        M.Row("BBB", "Finance", 100e9, -3.0),
        M.Row("CCC", "Electronic Technology", 50e9, 0.5),
        M.Row("DDD", "Electronic Technology", 50e9, None),
    ]
    async with M.market_client(tmp_path, monkeypatch, rows=rows) as harness:
        map_body = (await harness.get("/market/map", region="us")).json()
        groups_body = (await harness.get("/market/groups", region="us")).json()

    values = {sector["id"]: sector["value"] for sector in map_body["sectors"]}
    groups = {group["id"]: group["avg_change"] for group in groups_body["groups"]}
    assert values == groups
    assert map_body["universe_total"] == groups_body["universe_total"] == 4
    assert sum(group["count"] for group in groups_body["groups"]) == 4


def test_the_metric_names_one_cell() -> None:
    """The route's vocabulary is explicit: one metric, one percent cell."""

    from app.api import map as map_api

    assert map_api.METRICS == frozenset({"change"})
    assert map_api.METRIC_DEFAULT == "change"
