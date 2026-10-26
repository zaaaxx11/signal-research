from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests._support import api_client

from app.engine.catalog import CATALOG_VERSION
from app.engine.license import LicensePolicy
from app.engine.security import SecurityMaster
from app.engine.service import EngineService


def _sample_master() -> SecurityMaster:
    payload = json.loads(
        (
            Path(__file__).with_name("fixtures")
            / "engine_sec_company_tickers_sample.json"
        ).read_text()
    )
    return SecurityMaster.from_company_tickers(payload, as_of="2026-10-26")


def test_service_catalog_is_a_presentation_projection_without_source_keys() -> None:
    service = EngineService()
    document = service.catalog()

    assert document["catalog_version"] == CATALOG_VERSION
    assert document["count"] == 116
    assert document["metrics"][0]["key"] == "ticker"
    assert "source_keys" not in document["metrics"][0]
    assert all("source_keys" not in metric for metric in document["metrics"])


def test_service_metric_returns_one_metric_and_unknown_key_is_none() -> None:
    service = EngineService()

    metric = service.metric("market_cap")
    assert metric is not None
    assert metric["key"] == "market_cap"
    assert metric["unit"] == "USD"
    assert service.metric("market_cap ") is None


def test_service_security_uses_the_master_and_returns_none_when_absent() -> None:
    service = EngineService(security_master=_sample_master())

    record = service.security("aapl")
    assert record is not None
    assert record["symbol"] == "AAPL"
    assert record["cik"] == "0000320193"
    assert service.security("NOPE") is None


def test_service_health_exposes_policy_and_master_state() -> None:
    service = EngineService(policy=LicensePolicy(public_mode=False))

    health = service.health()
    assert health["engine"] == "v2"
    assert health["catalog_version"] == CATALOG_VERSION
    assert health["metrics"] == 116
    assert health["public_mode"] is False
    assert health["security_master"] is False


async def test_engine_routes_are_mounted_and_are_read_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with api_client(tmp_path, monkeypatch) as client:
        health = await client.get("/engine/health")
        assert health.status_code == 200, health.text
        assert health.json()["metrics"] == 116
        assert health.json()["public_mode"] is False

        catalog = await client.get("/engine/catalog")
        assert catalog.status_code == 200, catalog.text
        body = catalog.json()
        assert body["count"] == 116
        assert body["metrics"][0]["key"] == "ticker"
        assert "source_keys" not in body["metrics"][0]

        metric = await client.get("/engine/catalog/market_cap")
        assert metric.status_code == 200, metric.text
        assert metric.json()["unit"] == "USD"

        unknown = await client.get("/engine/catalog/not_a_metric")
        assert unknown.status_code == 404
        assert unknown.json()["error"] == "metric_not_found"

        missing_security = await client.get("/engine/security/AAPL")
        assert missing_security.status_code == 404
        assert missing_security.json()["error"] == "security_not_found"


def test_engine_service_can_be_constructed_with_a_master() -> None:
    service = EngineService(security_master=SecurityMaster())
    assert service.health()["security_master"] is True


def test_engine_security_record_shape_is_json_ready() -> None:
    service = EngineService(security_master=_sample_master())
    record = service.security("MSFT")

    assert isinstance(record, dict)
    assert record == {
        "symbol": "MSFT",
        "name": "MICROSOFT CORP",
        "cik": "0000789019",
        "exchange": None,
        "mic": None,
        "asset_class": "equity",
        "currency": "USD",
        "country": None,
        "sector": None,
        "industry": None,
        "active": True,
    }
    # The sample fixture has no venue, so the service must not invent one.
    assert record["exchange"] is None
