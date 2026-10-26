"""Contract for the point-in-time fundamentals endpoint (R4 §4).

``GET /engine/fundamentals/{symbol}`` answers one instrument: every fact cell the
dataset holds for it plus every cell the row assembler derived, dated by the
newest provenance in that row. It is a projection of
:meth:`~app.engine.service.EngineService.fundamentals`, so the tests below pin
the document shape, the date, the derived provenance, the two house error
envelopes, and the one thing the response must *not* add: a provider label.

The dataset is built by hand instead of from a fixture payload so the numbers
are arithmetic a reader can check in one line (``215.6 / 466.823 * 100`` is the
gross margin) and the dates are fixed: no network, no clock.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest

from app.engine.ingest import EngineDataset
from app.engine.model import Cell, CellStatus, Provenance, Unit
from app.engine.security import SecurityMaster
from app.engine.service import EngineService

FIXTURES = Path(__file__).with_name("fixtures")

#: The dataset is loaded *before* the period it reports, so the row's newest
#: provenance can only be a filing's date — never the load timestamp and never
#: the wall clock.
FETCHED_AT = "2026-06-01T00:00:00+00:00"
#: Period end of the statement slice.
PERIOD_END = "2026-06-27"
#: The share count's own cover date, deliberately newer than the period end so
#: ``as_of`` proves it is the maximum and not the first cell's date.
LATEST = "2026-07-15"

REVENUE = 466_823_000_000.0
GROSS_PROFIT = 215_600_000_000.0

#: Every key the document may carry inside a metric.
_METRIC_KEYS = {"value", "unit", "provenance"}
#: Every key the document may carry at the top level.
_DOCUMENT_KEYS = {"symbol", "as_of", "metrics"}


def _cell(
    value: float,
    *,
    unit: Unit = Unit.USD,
    as_of: str = PERIOD_END,
    tag: str = "R4Test",
) -> Cell:
    """One SEC cell shaped exactly like ``fact_summary`` emits it."""

    return Cell(
        value=value,
        unit=unit,
        provenance=Provenance(
            source="sec",
            as_of=as_of,
            status=CellStatus.OK,
            inputs=(tag, "0000320193-26-000001"),
        ),
    )


def _aapl_facts() -> dict[str, Cell]:
    """A statement slice covering every R4 derivation input, one period end."""

    return {
        "revenue": _cell(
            REVENUE, tag="RevenueFromContractWithCustomerExcludingAssessedTax"
        ),
        "gross_profit": _cell(GROSS_PROFIT, tag="GrossProfit"),
        "operating_income": _cell(130_000_000_000.0, tag="OperatingIncomeLoss"),
        "net_income": _cell(112_000_000_000.0, tag="NetIncomeLoss"),
        "assets": _cell(365_000_000_000.0, tag="Assets"),
        "equity": _cell(75_000_000_000.0, tag="StockholdersEquity"),
        "current_assets": _cell(150_000_000_000.0, tag="AssetsCurrent"),
        "current_liabilities": _cell(140_000_000_000.0, tag="LiabilitiesCurrent"),
        "total_debt": _cell(100_000_000_000.0, tag="LongTermDebt"),
        "cash_and_equivalents": _cell(
            50_000_000_000.0, tag="CashAndCashEquivalentsAtCarryingValue"
        ),
        "shares_outstanding": _cell(
            15_000_000_000.0,
            unit=Unit.SHARES,
            as_of=LATEST,
            tag="CommonStockSharesOutstanding",
        ),
    }


def _dataset() -> EngineDataset:
    """The loaded universe: four identities, facts for Apple only."""

    master = SecurityMaster.from_company_tickers(
        json.loads(
            (
                FIXTURES / "engine_sec_company_tickers_sample.json"
            ).read_text(encoding="utf-8")
        ),
        as_of="2026-05-01",
    )
    return EngineDataset(
        fetched_at=FETCHED_AT,
        security_master=master,
        facts={"AAPL": _aapl_facts()},
        errors={},
    )


@asynccontextmanager
async def _client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    service: EngineService,
) -> AsyncIterator[Any]:
    """An ASGI client over the real app, wired to exactly one engine service."""

    monkeypatch.setenv("MEE_DB_PATH", str(tmp_path / "mee.db"))
    monkeypatch.setenv("MEE_EXPORT_DIR", str(tmp_path / "exports"))

    from httpx2 import ASGITransport, AsyncClient

    from app.main import create_app

    application = create_app()
    application.state.engine = service
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://mee.test"
    ) as client:
        yield client


# ---------------------------------------------------------------------------
# The document
# ---------------------------------------------------------------------------


async def test_fundamentals_serves_the_fact_cells_and_the_derived_cells(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _client(
        tmp_path, monkeypatch, EngineService.from_dataset(_dataset())
    ) as client:
        response = await client.get("/engine/fundamentals/AAPL")

    assert response.status_code == 200, response.text
    body = response.json()

    assert set(body) == _DOCUMENT_KEYS
    assert body["symbol"] == "AAPL"

    metrics = body["metrics"]
    assert isinstance(metrics, dict) and metrics

    # A fact cell: the observed value, its unit and the filing behind it.
    sales = metrics["sales"]
    assert sales["value"] == pytest.approx(REVENUE)
    assert sales["unit"] == "USD"
    assert sales["provenance"]["source"] == "sec"
    assert sales["provenance"]["status"] == "ok"
    assert sales["provenance"]["as_of"] == PERIOD_END

    # A derived cell: the §2 scale, its unit and the inputs it divided.
    margin = metrics["gross_margin"]
    assert margin["value"] == pytest.approx(GROSS_PROFIT / REVENUE * 100.0)
    assert margin["unit"] == "pct"
    assert margin["provenance"]["source"] == "derived"
    assert margin["provenance"]["status"] == "derived"
    assert margin["provenance"]["inputs"] == ["gross_profit", "revenue"]

    # Every metric is the same three-key projection, and no gap is published as
    # a null. Identity cells (a ticker is text, not a measurement) may appear;
    # this asserts the projection, not that every value is numeric.
    for key, metric in metrics.items():
        assert set(metric) == _METRIC_KEYS, key
        assert metric["value"] is not None, key
        assert isinstance(metric["unit"], str) and metric["unit"], key
        assert isinstance(metric["provenance"], dict), key
        assert metric["provenance"]["source"], key
        assert metric["provenance"]["status"], key

    # No quote was loaded, so no price may appear, and no metric the dataset
    # cannot support (no diluted EPS fact) may be invented.
    assert "price" not in metrics
    assert "eps" not in metrics


async def test_fundamentals_dates_the_document_by_its_newest_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _client(
        tmp_path, monkeypatch, EngineService.from_dataset(_dataset())
    ) as client:
        response = await client.get("/engine/fundamentals/AAPL")

    assert response.status_code == 200, response.text
    body = response.json()

    # The share count was filed on LATEST, which is newer than the period end
    # and newer than the load; the max is the only date that can answer this.
    assert body["as_of"] == LATEST
    assert body["as_of"] != FETCHED_AT
    assert body["as_of"] != PERIOD_END


async def test_fundamentals_adds_no_provider_label(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The audit ``source`` lives in provenance, nowhere else in the response."""

    async with _client(
        tmp_path, monkeypatch, EngineService.from_dataset(_dataset())
    ) as client:
        response = await client.get("/engine/fundamentals/AAPL")

    assert response.status_code == 200, response.text
    body = response.json()
    encoded = json.dumps(body)

    assert "provider" not in encoded
    assert "DatasetRows" not in encoded
    assert "quote_snapshot" not in body
    assert "source" not in body


# ---------------------------------------------------------------------------
# The two house envelopes
# ---------------------------------------------------------------------------


async def test_fundamentals_unknown_symbol_is_a_404_security_not_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _client(
        tmp_path, monkeypatch, EngineService.from_dataset(_dataset())
    ) as client:
        response = await client.get("/engine/fundamentals/ZZZZ")

    assert response.status_code == 404, response.text
    body = response.json()
    assert body.get("error") == "security_not_found"
    assert body.get("errorMessage")


async def test_fundamentals_is_a_503_without_a_dataset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _client(tmp_path, monkeypatch, EngineService()) as client:
        response = await client.get("/engine/fundamentals/AAPL")

    assert response.status_code == 503, response.text
    body = response.json()
    assert body.get("error") == "screen_unavailable"
    assert body.get("errorMessage")


async def test_fundamentals_of_a_listed_symbol_without_facts_is_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A listed company with no facts is a 200 with no invented metric."""

    async with _client(
        tmp_path, monkeypatch, EngineService.from_dataset(_dataset())
    ) as client:
        response = await client.get("/engine/fundamentals/MSFT")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["symbol"] == "MSFT"

    metrics = body["metrics"]
    for key in ("sales", "net_income", "gross_margin", "net_margin", "roe"):
        assert key not in metrics, key
    # Whatever the projection carries for a fact-less symbol, none of it may
    # claim an observation the dataset does not hold.
    for key, metric in metrics.items():
        assert metric["provenance"]["source"] == "identity", key


# ---------------------------------------------------------------------------
# The service entry point the route projects
# ---------------------------------------------------------------------------


def test_the_service_returns_the_document_or_none() -> None:
    service = EngineService.from_dataset(_dataset())
    fundamentals = getattr(service, "fundamentals", None)
    assert callable(fundamentals), "EngineService.fundamentals is the §4 entry point"

    assert fundamentals("ZZZZ") is None

    document = fundamentals("AAPL")
    assert document is not None
    assert isinstance(document, dict)
    assert document["symbol"] == "AAPL"
    assert set(document) == _DOCUMENT_KEYS
    assert "sales" in document["metrics"]
    assert "gross_margin" in document["metrics"]
    assert document["as_of"] == LATEST


async def test_fundamentals_serves_the_raw_fact_vocabulary_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The endpoint is the filing, not the screen: uncatalogued facts must show.

    A row only carries facts that have a catalog column, but the statement
    vocabulary (``gross_profit``, ``assets``, …) is exactly the depth a caller
    opens this endpoint for. Omitting it would make the document a narrower
    projection than the dataset it claims to describe.
    """

    async with _client(
        tmp_path, monkeypatch, EngineService.from_dataset(_dataset())
    ) as client:
        response = await client.get("/engine/fundamentals/AAPL")

    assert response.status_code == 200, response.text
    metrics = response.json()["metrics"]

    assert metrics["gross_profit"]["value"] == GROSS_PROFIT
    assert metrics["gross_profit"]["unit"] == "USD"
    assert metrics["assets"]["value"] == 365_000_000_000.0
    assert metrics["equity"]["value"] == 75_000_000_000.0
    assert metrics["gross_profit"]["provenance"]["source"] == "sec"
    assert metrics["gross_margin"]["provenance"]["source"] == "derived"
