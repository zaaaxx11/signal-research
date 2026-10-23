"""The five-minute refresh loop: every pair, and one failure does not stop the rest."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.screen import refresh as refresh_mod
from app.screen.refresh import REGIONS, SORTS, refresh_all_screens


@dataclass
class _FakeSnapshot:
    screen_id: str
    screen: dict[str, Any]
    rows: list[dict[str, Any]]


def _snapshot(region: str, sort: str) -> _FakeSnapshot:
    return _FakeSnapshot(
        screen_id=f"scr_{region}_{sort}",
        screen={"total_count": 23},
        rows=[{"rank": 1, "symbol": "AAPL"}],
    )


async def test_refresh_covers_both_tabs_and_both_sorts() -> None:
    calls: list[tuple[str, str]] = []

    async def fake(region: str, sort: str, **_kwargs: Any) -> _FakeSnapshot:
        calls.append((region, sort))
        return _snapshot(region, sort)

    report = await refresh_all_screens(object(), refresh=fake)  # type: ignore[arg-type]

    assert calls == [(region, sort) for region in REGIONS for sort in SORTS]
    assert report["ok"] == 4
    assert report["failed"] == 0
    assert report["interval_s"] == 300
    assert [row["screen_id"] for row in report["results"]] == [
        f"scr_{region}_{sort}" for region in REGIONS for sort in SORTS
    ]


async def test_one_tab_failure_does_not_skip_the_next_pair() -> None:
    calls: list[tuple[str, str]] = []

    async def fake(region: str, sort: str, **_kwargs: Any) -> _FakeSnapshot:
        calls.append((region, sort))
        if (region, sort) == ("us", "turnover"):
            raise RuntimeError("scanner blocked")
        return _snapshot(region, sort)

    report = await refresh_all_screens(object(), refresh=fake)  # type: ignore[arg-type]

    assert calls == [(region, sort) for region in REGIONS for sort in SORTS]
    assert report["ok"] == 3
    assert report["failed"] == 1
    failed = [row for row in report["results"] if row["status"] == "failed"]
    assert failed and failed[0]["region"] == "us" and failed[0]["sort"] == "turnover"
    assert "scanner blocked" in failed[0]["error"]


async def test_the_interval_matches_the_display_ttl() -> None:
    from app.screen.snapshot import SNAPSHOT_TTL_S

    assert refresh_mod.REFRESH_INTERVAL_S == 300
    assert refresh_mod.REFRESH_INTERVAL_S == SNAPSHOT_TTL_S
