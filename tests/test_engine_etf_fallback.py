"""RED contract for the ETF universe fallback on the holdings route.

The SEC operating-company index does not contain most ETFs: ``IVV`` and ``QQQ``
are absent while ``SPY`` happens to be a registrant. The holdings route must
still answer for a fund that is in SEC's official ticker→series map, without
adding 28k funds to the screener universe.

Four facts are pinned:

1. A symbol absent from the identity master but present in
   ``company_tickers_mf.json`` is collected with the map's CIK — not a 404.
2. A symbol in neither is still ``404 security_not_found``.
3. Without a map file the route keeps its old answer (404), never a crash.
4. The collector is asked exactly once, with the upper-cased ticker.

Everything is offline: no network, literal dates, the collector is patched in
both seams, and ``MEE_ENGINE_CACHE_DIR`` points at ``tmp_path``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
import json

import httpx2
import pytest

from app.engine.ingest import EngineDataset
from app.engine.security import SecurityMaster
from app.engine.service import EngineService

ROOT = Path(__file__).resolve().parents[1]

TICKER_PAYLOAD: dict[str, Any] = {
    "0": {"cik_str": 884394, "ticker": "SPY", "title": "SPDR S&P 500 ETF Trust"}
}

IVV_CIK = "0001100663"

MAP_PAYLOAD: dict[str, Any] = {
    "fields": ["cik", "seriesId", "classId", "symbol"],
    "data": [
        [1100663, "S000004310", "C000012040", "IVV"],
        [1067839, "S000101292", "C000271435", "QQQ"],
    ],
}


def engine_master() -> SecurityMaster:
    """The identity master: SPY only, exactly like the live snapshot."""

    return SecurityMaster.from_company_tickers(TICKER_PAYLOAD, as_of="2026-10-26")


def engine_service(master: SecurityMaster) -> EngineService:
    """A service whose dataset carries ``master`` and no facts."""

    dataset = EngineDataset(
        fetched_at="2026-10-26T20:00:00+00:00",
        security_master=master,
        facts={},
        errors={},
    )
    return EngineService.from_dataset(dataset)


class FakeCollector:
    """Record the route's collector call and answer one canned document."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def __call__(
        self,
        symbol: str,
        cik: str,
        *,
        client: Any = None,
        cache_dir: Path | None = None,
        limit: int = 50,
    ) -> dict[str, object]:
        self.calls.append(
            {
                "symbol": symbol,
                "cik": cik,
                "cache_dir": cache_dir,
                "limit": limit,
                "client": client,
            }
        )
        return {
            "symbol": symbol,
            "as_of": "2026-06-30",
            "filed": "2026-08-25",
            "accession": "0002071691-26-019760",
            "count": 0,
            "holdings": [],
            "provider_errors": [],
        }


def install_collector(
    monkeypatch: pytest.MonkeyPatch, fake: FakeCollector
) -> None:
    """Patch both names the route could hold."""

    monkeypatch.setattr("app.engine.nport.collect_holdings", fake)
    monkeypatch.setattr("app.api.engine.collect_holdings", fake, raising=False)


@asynccontextmanager
async def route_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    service: EngineService,
) -> AsyncIterator[httpx2.AsyncClient]:
    """An ASGI client whose cache dir is ``tmp_path``."""

    monkeypatch.setenv("MEE_DB_PATH", str(tmp_path / "mee.db"))
    monkeypatch.setenv("MEE_EXPORT_DIR", str(tmp_path / "exports"))
    monkeypatch.setenv("MEE_ENGINE_CACHE_DIR", str(tmp_path))
    monkeypatch.delenv("MEE_ENGINE_SNAPSHOT", raising=False)
    monkeypatch.delenv("MEE_ENGINE_SYMBOLS", raising=False)

    from app.main import create_app

    application = create_app()
    application.state.engine = service
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=application), base_url="http://mee.test"
    ) as client:
        yield client


def write_map(tmp_path: Path) -> None:
    """Write SEC's ticker→series map into the route's cache directory."""

    (tmp_path / "company_tickers_mf.json").write_text(
        json.dumps(MAP_PAYLOAD), encoding="utf-8"
    )


async def test_an_etf_outside_the_master_is_served_from_the_fund_map(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """IVV is absent from the master but present in the map → collected."""

    write_map(tmp_path)
    fake = FakeCollector()
    install_collector(monkeypatch, fake)

    async with route_client(tmp_path, monkeypatch, engine_service(engine_master())) as client:
        response = await client.get("/engine/etf/IVV/holdings?limit=5")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["symbol"] == "IVV"
    assert [call["symbol"] for call in fake.calls] == ["IVV"]
    assert [call["cik"] for call in fake.calls] == [IVV_CIK]
    assert [call["limit"] for call in fake.calls] == [5]


async def test_a_lower_case_etf_is_upper_cased_for_the_map_and_collector(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``ivv`` resolves through the upper-case map key and is collected as IVV."""

    write_map(tmp_path)
    fake = FakeCollector()
    install_collector(monkeypatch, fake)

    async with route_client(tmp_path, monkeypatch, engine_service(engine_master())) as client:
        response = await client.get("/engine/etf/ivv/holdings?limit=1")

    assert response.status_code == 200, response.text
    assert [call["symbol"] for call in fake.calls] == ["IVV"]
    assert [call["cik"] for call in fake.calls] == [IVV_CIK]


async def test_a_symbol_in_neither_the_master_nor_the_map_is_still_404(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fallback must not turn every unknown ticker into a fund."""

    write_map(tmp_path)
    fake = FakeCollector()
    install_collector(monkeypatch, fake)

    async with route_client(tmp_path, monkeypatch, engine_service(engine_master())) as client:
        response = await client.get("/engine/etf/NOPE/holdings")

    assert response.status_code == 404
    assert response.json()["error"] == "security_not_found"
    assert fake.calls == []


async def test_without_a_map_file_the_old_404_stands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No map is a normal deployment state: fall back, never crash."""

    fake = FakeCollector()
    install_collector(monkeypatch, fake)

    async with route_client(tmp_path, monkeypatch, engine_service(engine_master())) as client:
        response = await client.get("/engine/etf/IVV/holdings")

    assert response.status_code == 404
    assert response.json()["error"] == "security_not_found"
    assert fake.calls == []


async def test_an_invalid_limit_still_wins_before_the_fund_map(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A typo in the page size is a 422 even for a map-only ETF."""

    write_map(tmp_path)
    fake = FakeCollector()
    install_collector(monkeypatch, fake)

    async with route_client(tmp_path, monkeypatch, engine_service(engine_master())) as client:
        response = await client.get("/engine/etf/IVV/holdings?limit=0")

    assert response.status_code == 422
    assert response.json()["error"] == "invalid_limit"
    assert fake.calls == []
