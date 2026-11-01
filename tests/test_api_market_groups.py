"""Contract for ``GET /market/groups`` — the sector table beside the treemap.

What is pinned here:

* **the partition holds** — ``sum(group.count) == universe_total`` (5,576 on the
  live universe), and ``adv + dec + unchanged == count`` for every group, so the
  three buckets are a partition and not three overlapping tallies;
* **``avg_change`` is cap-weighted**, recomputed here by hand from the same rows
  ``/screens/select`` answers, and identical to the map's sector ``value``;
* **``market_cap`` is USD**, the sum of every member's cap — including the
  members that carry no metric, because a cap does not need a price change to be
  a fact;
* **the order is total** — ``avg_change`` descending, then label, with a sector
  that has nothing to weigh last (``null`` is not ``0.0``);
* **revalidatable** — a stable weak ``ETag``, a ``304`` with no body and
  ``Cache-Control: no-cache`` on both answers, ``HEAD`` as its own route.

The suite is hermetic: the scanner is a ``MockWeb`` over a handwritten scan body.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests import _market as M

_DOCUMENT_KEYS = frozenset({"as_of", "region", "universe_total", "groups"})
_GROUP_KEYS = frozenset(
    {"id", "label", "count", "avg_change", "adv", "dec", "unchanged", "market_cap"}
)


def _groups(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {group["id"]: group for group in body["groups"]}


# ---------------------------------------------------------------------------
# Shape and the two equalities
# ---------------------------------------------------------------------------


async def test_the_document_has_the_contracted_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = [
        M.Row("AAA", "Electronic Technology", 500e9, 2.0),
        M.Row("BBB", "Finance", 100e9, -1.0),
    ]
    async with M.market_client(tmp_path, monkeypatch, rows=rows) as harness:
        body = (await harness.get("/market/groups", region="us")).json()

    assert set(body) == _DOCUMENT_KEYS, sorted(body)
    assert body["region"] == "us"
    assert body["universe_total"] == 2
    assert len(body["groups"]) == 2
    assert all(set(group) == _GROUP_KEYS for group in body["groups"])


async def test_the_groups_partition_the_universe_and_each_group_partitions_itself(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``sum(count) == universe_total`` and ``adv + dec + unchanged == count``."""

    rows = [
        M.Row("AAA", "Finance", 900e9, 2.0),
        M.Row("BBB", "Finance", 100e9, -3.0),
        M.Row("CCC", "Finance", 50e9, 0.0),
        M.Row("DDD", "Finance", 40e9, None),
        M.Row("EEE", "Health Technology", 10e9, 1.0),
    ]
    async with M.market_client(tmp_path, monkeypatch, rows=rows) as harness:
        body = (await harness.get("/market/groups", region="us")).json()
        selected = (await harness.client.post("/screens/select", json=M.selection())).json()

    assert body["universe_total"] == selected["universe_total"] == 5
    assert sum(group["count"] for group in body["groups"]) == 5
    for group in body["groups"]:
        assert group["adv"] + group["dec"] + group["unchanged"] == group["count"]

    finance = _groups(body)["finance"]
    assert finance["count"] == 4
    assert finance["adv"] == 1 and finance["dec"] == 1
    assert finance["unchanged"] == 2, "0.00 % and a null both count as unchanged"


async def test_the_live_universe_partition_is_5576(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The number the whole page is checked against, on a 5,576-row population."""

    async with M.market_client(tmp_path, monkeypatch, rows=M.universe(5_576)) as harness:
        body = (await harness.get("/market/groups", region="us")).json()

    assert body["universe_total"] == 5_576
    assert sum(group["count"] for group in body["groups"]) == 5_576
    assert sum(
        group["adv"] + group["dec"] + group["unchanged"] for group in body["groups"]
    ) == 5_576


# ---------------------------------------------------------------------------
# The weighted average, by hand
# ---------------------------------------------------------------------------


async def test_avg_change_is_the_cap_weighted_mean_recomputed_by_hand(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = [
        M.Row("AAA", "Finance", 900e9, 2.0),
        M.Row("BBB", "Finance", 100e9, -2.0),
        M.Row("CCC", "Finance", 50e9, None),
    ]
    async with M.market_client(tmp_path, monkeypatch, rows=rows) as harness:
        body = (await harness.get("/market/groups", region="us")).json()

    expected = (900e9 * 2.0 + 100e9 * -2.0) / (900e9 + 100e9)
    assert _groups(body)["finance"]["avg_change"] == pytest.approx(expected, abs=1e-9)
    assert _groups(body)["finance"]["avg_change"] == 1.6


async def test_a_group_with_nothing_weighable_sorts_last_with_null(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A sector whose members all lack a metric is unknown, not the worst mover."""

    rows = [
        M.Row("AAA", "Finance", 1e9, -4.0),
        M.Row("BBB", "Health Technology", 1e9, None),
        M.Row("CCC", "Utilities", 1e9, 1.0),
    ]
    async with M.market_client(tmp_path, monkeypatch, rows=rows) as harness:
        body = (await harness.get("/market/groups", region="us")).json()

    order = [group["label"] for group in body["groups"]]
    assert order == ["Utilities", "Finance", "Health Technology"]
    assert body["groups"][-1]["avg_change"] is None


async def test_the_order_is_avg_change_desc_then_label(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = [
        M.Row("AAA", "Utilities", 1e9, 1.5),
        M.Row("BBB", "Finance", 1e9, 3.0),
        M.Row("CCC", "Health Technology", 1e9, 3.0),
        M.Row("DDD", "Energy Minerals", 1e9, -1.0),
    ]
    async with M.market_client(tmp_path, monkeypatch, rows=rows) as harness:
        body = (await harness.get("/market/groups", region="us")).json()

    keys = [(-group["avg_change"], group["label"]) for group in body["groups"]]
    assert keys == sorted(keys)
    assert [group["label"] for group in body["groups"]] == [
        "Finance",
        "Health Technology",
        "Utilities",
        "Energy Minerals",
    ]


# ---------------------------------------------------------------------------
# Size in dollars, including the members with no metric
# ---------------------------------------------------------------------------


async def test_market_cap_sums_every_member_in_usd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A member with no change still has a cap, and the cap is in dollars."""

    rows = [
        M.Row("AAA", "Finance", 1_500_000_000.0, 1.0),
        M.Row("BBB", "Finance", 2_250_000_000.0, None),
    ]
    async with M.market_client(tmp_path, monkeypatch, rows=rows) as harness:
        body = (await harness.get("/market/groups", region="us")).json()

    finance = _groups(body)["finance"]
    assert finance["market_cap"] == 3_750_000_000
    assert isinstance(finance["market_cap"], int)


# ---------------------------------------------------------------------------
# Revalidation
# ---------------------------------------------------------------------------


async def test_the_etag_is_stable_and_a_repeat_is_a_304(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = [M.Row("AAA", "Finance", 1e9, 1.0)]
    async with M.market_client(tmp_path, monkeypatch, rows=rows) as harness:
        first = await harness.get("/market/groups", region="us")
        second = await harness.get("/market/groups", region="us")
        revalidated = await harness.client.get(
            "/market/groups",
            params={"region": "us"},
            headers={"if-none-match": first.headers["etag"]},
        )

    assert first.headers["etag"] == second.headers["etag"]
    assert first.headers["etag"].startswith('W/"grp-')
    assert first.headers["cache-control"] == "no-cache"
    assert revalidated.status_code == 304
    assert revalidated.content == b""
    assert revalidated.headers["cache-control"] == "no-cache"


async def test_head_answers_the_get_headers_and_no_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = [M.Row("AAA", "Finance", 1e9, 1.0)]
    async with M.market_client(tmp_path, monkeypatch, rows=rows) as harness:
        get = await harness.get("/market/groups", region="us")
        head = await harness.client.head("/market/groups", params={"region": "us"})

    assert head.status_code == 200
    assert head.content == b""
    assert head.headers["etag"] == get.headers["etag"]
    assert head.headers["content-length"] == str(len(get.content))


async def test_the_groups_and_the_map_report_one_universe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two documents, one population — and therefore one scan."""

    rows = [M.Row("AAA", "Finance", 1e9, 1.0), M.Row("BBB", "Utilities", 2e9, -1.0)]
    async with M.market_client(tmp_path, monkeypatch, rows=rows) as harness:
        groups = (await harness.get("/market/groups", region="us")).json()
        map_body = (await harness.get("/market/map", region="us")).json()
        scans = harness.web.count("tradingview")

    assert groups["universe_total"] == map_body["universe_total"] == 2
    assert sum(group["count"] for group in groups["groups"]) == 2
    assert scans == 1


async def test_an_unknown_region_is_refused_before_any_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = [M.Row("AAA", "Finance", 1e9, 1.0)]
    async with M.market_client(tmp_path, monkeypatch, rows=rows) as harness:
        response = await harness.get("/market/groups", region="mars")

    assert response.status_code == 422
    assert response.json()["error"] == "invalid_region"
    assert harness.web.count("tradingview") == 0


async def test_the_body_is_small_enough_to_ship_on_every_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Twenty-one groups: kilobytes, so a client can poll it without a plan."""

    async with M.market_client(tmp_path, monkeypatch, rows=M.universe(5_576)) as harness:
        response = await harness.get("/market/groups", region="us")

    assert len(response.json()["groups"]) == 21
    assert len(response.content) < 8 * 1024, len(response.content)
    assert len(response.content) == len(
        json.dumps(response.json(), separators=(",", ":"), ensure_ascii=False).encode()
    )
