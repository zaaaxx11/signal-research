from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests._support import api_client

from app.engine.model import Cell, CellStatus, Provenance, Unit
from app.engine.screener import ScreenError
from app.engine.service import EngineService, EngineUnavailable


def cell(value: Any, unit: Unit = Unit.USD, *, source: str = "test") -> Cell:
    return Cell(
        value=value,
        unit=unit,
        provenance=Provenance(source=source, as_of="2026-10-26", status=CellStatus.OK),
    )


class FakeRows:
    def __init__(self, rows: tuple[dict[str, Cell], ...]) -> None:
        self._rows = rows
        self.calls = 0

    def rows(self) -> tuple[dict[str, Cell], ...]:
        self.calls += 1
        return self._rows


def test_screen_requires_a_row_provider() -> None:
    service = EngineService()

    with pytest.raises(EngineUnavailable) as error:
        service.screen({"filters": [], "limit": 20, "offset": 0})
    assert error.value.code == "screen_unavailable"


def test_screen_filters_sorts_and_pages_rows() -> None:
    rows = (
        {"ticker": cell("AAPL", Unit.TEXT), "price": cell(100.0)},
        {"ticker": cell("MSFT", Unit.TEXT), "price": cell(300.0)},
        {"ticker": cell("NVDA", Unit.TEXT), "price": cell(150.0)},
    )
    provider = FakeRows(rows)
    service = EngineService(rows=provider)

    result = service.screen(
        {
            "filters": [{"field": "price", "op": "gte", "value": 100}],
            "sort": {"field": "price", "descending": True},
            "limit": 2,
            "offset": 0,
        }
    )

    assert provider.calls == 1
    assert result.total == 3
    assert result.returned == 2
    assert [row["ticker"].value for row in result.rows] == ["MSFT", "NVDA"]
    assert result.limit == 2


def test_screen_unknown_field_is_a_typed_error() -> None:
    service = EngineService(rows=FakeRows(({"ticker": cell("AAPL", Unit.TEXT)},)))

    with pytest.raises(ScreenError) as error:
        service.screen({"filters": [{"field": "not_a_field", "op": "eq", "value": 1}]})
    assert error.value.code == "unknown_field"


def test_screen_document_keeps_values_units_and_provenance() -> None:
    service = EngineService(
        rows=FakeRows(
            (
                {
                    "ticker": cell("AAPL", Unit.TEXT),
                    "price": cell(100.0),
                },
            )
        )
    )
    result = service.screen({"filters": [], "limit": 20, "offset": 0})

    document = EngineService.screen_document(result)
    assert document["total"] == 1
    assert document["rows"][0]["ticker"]["value"] == "AAPL"
    assert document["rows"][0]["price"] == {
        "value": 100.0,
        "unit": "USD",
        "provenance": {
            "source": "test",
            "as_of": "2026-10-26",
            "status": "ok",
            "url": None,
            "inputs": [],
        },
    }


async def test_screen_route_is_503_when_no_provider_is_loaded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with api_client(tmp_path, monkeypatch) as client:
        response = await client.post(
            "/engine/screen",
            json={"filters": [], "limit": 20, "offset": 0},
        )

    assert response.status_code == 503
    assert response.json()["error"] == "screen_unavailable"


async def test_screen_route_rejects_a_bad_filter_with_422(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with api_client(tmp_path, monkeypatch) as client:
        response = await client.post(
            "/engine/screen",
            json={"filters": [{"field": "nope", "op": "eq", "value": 1}]},
        )

    assert response.status_code == 422
    assert response.json()["error"] == "unknown_field"


async def test_screen_route_returns_filtered_rows_with_a_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from httpx2 import ASGITransport, AsyncClient

    from app.main import create_app

    monkeypatch.setenv("MEE_DB_PATH", str(tmp_path / "mee.db"))
    monkeypatch.setenv("MEE_EXPORT_DIR", str(tmp_path / "exports"))
    rows = (
        {"ticker": cell("AAPL", Unit.TEXT), "price": cell(100.0)},
        {"ticker": cell("MSFT", Unit.TEXT), "price": cell(300.0)},
    )
    application = create_app()
    application.state.engine = EngineService(rows=FakeRows(rows))

    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://mee.test"
    ) as client:
        response = await client.post(
            "/engine/screen",
            json={
                "filters": [{"field": "price", "op": "gte", "value": 200}],
                "sort": {"field": "price", "descending": True},
            },
        )

    assert response.status_code == 200
    document = response.json()
    assert document["total"] == 1
    assert document["rows"][0]["ticker"]["value"] == "MSFT"
    assert document["rows"][0]["price"]["value"] == 300.0


async def test_screen_route_rejects_invalid_json_with_422(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with api_client(tmp_path, monkeypatch) as client:
        response = await client.post(
            "/engine/screen",
            content=b"not-json",
            headers={"content-type": "application/json"},
        )

    assert response.status_code == 422
    assert response.json()["error"] == "invalid_json"
