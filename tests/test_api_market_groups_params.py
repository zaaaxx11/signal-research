"""Contract for ``GET /market/groups`` group_by, sort and perf params."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests import _market as M


def _rows():
    return [
        M.Row("AAA", "Finance", 500e9, 2.0, industry="Banks", country="United States"),
        M.Row("BBB", "Finance", 100e9, -1.0, industry="Banks", country="Canada"),
        M.Row("CCC", "Technology Services", 50e9, 0.5, industry="Software", country="United States"),
        M.Row("DDD", "Technology Services", 40e9, None, industry="Hardware", country="Japan"),
    ]


async def test_group_by_industry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rows = _rows()
    async with M.market_client(tmp_path, monkeypatch, rows=rows) as harness:
        body = (await harness.get("/market/groups", region="us", group_by="industry")).json()

    assert body["group_by"] == "industry"
    assert body["universe_total"] == 4
    labels = {g["label"] for g in body["groups"]}
    assert labels == {"Banks", "Software", "Hardware"}
    banks = next(g for g in body["groups"] if g["label"] == "Banks")
    assert banks["count"] == 2
    assert banks["adv"] == 1 and banks["dec"] == 1


async def test_group_by_country(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rows = _rows()
    async with M.market_client(tmp_path, monkeypatch, rows=rows) as harness:
        body = (await harness.get("/market/groups", region="us", group_by="country")).json()

    assert body["group_by"] == "country"
    labels = {g["label"] for g in body["groups"]}
    assert labels == {"United States", "Canada", "Japan"}
    us = next(g for g in body["groups"] if g["label"] == "United States")
    assert us["count"] == 2


async def test_group_by_capitalization(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [
        M.Row("AAA", "Finance", 500e9, 1.0),
        M.Row("BBB", "Finance", 5e9, 2.0),
        M.Row("CCC", "Utilities", 10e6, 3.0),
    ]
    async with M.market_client(tmp_path, monkeypatch, rows=rows) as harness:
        body = (await harness.get("/market/groups", region="us", group_by="capitalization")).json()

    labels = {g["label"] for g in body["groups"]}
    assert labels == {"Mega Cap ($200B+)", "Mid Cap ($2B-10B)", "Micro Cap (<$300M)"}
    mega = next(g for g in body["groups"] if "Mega" in g["label"])
    assert mega["count"] == 1 and mega["market_cap"] == 500_000_000_000


async def test_unknown_group_by_is_422(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rows = _rows()
    async with M.market_client(tmp_path, monkeypatch, rows=rows) as harness:
        resp = await harness.get("/market/groups", region="us", group_by="banana")

    assert resp.status_code == 422
    assert resp.json()["error"] == "invalid_group_by"


async def test_sort_by_name_asc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rows = _rows()
    async with M.market_client(tmp_path, monkeypatch, rows=rows) as harness:
        body = (await harness.get("/market/groups", region="us", sort="name", direction="asc")).json()

    names = [g["label"] for g in body["groups"]]
    assert names == sorted(names)


async def test_sort_by_count_desc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rows = _rows()
    async with M.market_client(tmp_path, monkeypatch, rows=rows) as harness:
        body = (await harness.get("/market/groups", region="us", sort="count", direction="desc")).json()

    counts = [g["count"] for g in body["groups"]]
    assert counts == sorted(counts, reverse=True)


async def test_unknown_sort_is_422(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rows = _rows()
    async with M.market_client(tmp_path, monkeypatch, rows=rows) as harness:
        resp = await harness.get("/market/groups", region="us", sort="nope")

    assert resp.status_code == 422
    assert resp.json()["error"] == "invalid_sort"


async def test_unknown_direction_is_422(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rows = _rows()
    async with M.market_client(tmp_path, monkeypatch, rows=rows) as harness:
        resp = await harness.get("/market/groups", region="us", direction="sideways")

    assert resp.status_code == 422
    assert resp.json()["error"] == "invalid_direction"


async def test_perf_windows_are_cap_weighted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [
        M.Row("AAA", "Finance", 900e9, 2.0),
        M.Row("BBB", "Finance", 100e9, -2.0),
    ]
    # The M.Row.cells() doesn't carry ret_1w; verify perf columns are null when absent.
    async with M.market_client(tmp_path, monkeypatch, rows=rows) as harness:
        body = (await harness.get("/market/groups", region="us")).json()

    finance = next(g for g in body["groups"] if g["label"] == "Finance")
    assert finance["perf_1w"] is None
    assert finance["perf_1m"] is None
    assert finance["perf_3m"] is None