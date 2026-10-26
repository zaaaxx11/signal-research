from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.engine.dataset import DatasetRows
from app.engine.facts import fact_ttm_summary
from app.engine.ingest import EngineDataset
from app.engine.model import Cell, CellStatus, Provenance, Unit
from app.engine.security import SecurityMaster
from app.engine.service import EngineService

FIXTURES = Path(__file__).with_name("fixtures")


def _master() -> SecurityMaster:
    return SecurityMaster.from_company_tickers(
        json.loads((FIXTURES / "engine_sec_company_tickers_sample.json").read_text()),
        as_of="2026-10-26",
    )


def _aapl_facts() -> dict[str, Cell]:
    return fact_ttm_summary(
        json.loads((FIXTURES / "sec_companyfacts_aapl.json").read_text())
    )


def test_dataset_rows_joins_identity_and_facts_without_inventing_a_quote() -> None:
    dataset = EngineDataset(
        fetched_at="2026-10-26T12:00:00+00:00",
        security_master=_master(),
        facts={"AAPL": _aapl_facts()},
        errors={"MSFT": "not fetched"},
    )

    rows = DatasetRows(dataset).rows()

    assert len(rows) == 4
    aapl = next(row for row in rows if row["ticker"].value == "AAPL")
    assert aapl["company"].value == "Apple Inc."
    assert aapl["sales"].value == 466_823_000_000.0
    assert "price" not in aapl

    msft = next(row for row in rows if row["ticker"].value == "MSFT")
    assert "sales" not in msft

    assert DatasetRows(dataset).errors == {"MSFT": "not fetched"}


def test_dataset_rows_uses_quote_as_of_and_preserves_order() -> None:
    dataset = EngineDataset(
        fetched_at="2026-10-26T12:00:00+00:00",
        security_master=_master(),
        facts={},
        errors={},
    )
    quotes = {
        "AAPL": {
            "price": 100.0,
            "as_of": "2026-10-23T20:00:00Z",
        }
    }

    rows = DatasetRows(dataset, quotes=quotes).rows()
    aapl = next(row for row in rows if row["ticker"].value == "AAPL")

    assert aapl["price"].value == 100.0
    assert aapl["price"].provenance is not None
    assert aapl["price"].provenance.as_of == "2026-10-23T20:00:00Z"
    assert [row["ticker"].value for row in rows] == ["AAPL", "BRK-B", "MSFT", "NVDA"]


def test_dataset_rows_fails_closed_on_a_unit_mismatch() -> None:
    dataset = EngineDataset(
        fetched_at="2026-10-26T12:00:00+00:00",
        security_master=_master(),
        facts={
            "AAPL": {
                "revenue": Cell(
                    value=1.0,
                    unit=Unit.SHARES,
                    provenance=Provenance(
                        source="sec",
                        as_of="2026-06-27",
                        status=CellStatus.OK,
                    ),
                )
            }
        },
        errors={},
    )

    with pytest.raises(Exception) as error:
        DatasetRows(dataset).rows()
    assert "unit_mismatch" in str(getattr(error.value, "code", ""))


def test_dataset_rows_integrates_with_engine_screen() -> None:
    dataset = EngineDataset(
        fetched_at="2026-10-26T12:00:00+00:00",
        security_master=_master(),
        facts={"AAPL": _aapl_facts()},
        errors={},
    )
    service = EngineService.from_dataset(dataset)

    result = service.screen(
        {
            "filters": [{"field": "sales", "op": "gt", "value": 1_000_000}],
            "sort": {"field": "ticker", "descending": False},
            "limit": 20,
            "offset": 0,
        }
    )

    assert result.total == 1
    assert result.rows[0]["ticker"].value == "AAPL"
