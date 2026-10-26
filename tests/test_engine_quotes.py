from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.engine.quotes import quote_fields
from app.engine.rows import build_row
from app.engine.security import SecurityMaster
from app.market.quote import QuoteTick

FIXTURES = Path(__file__).with_name("fixtures")


def _security():
    master = SecurityMaster.from_company_tickers(
        json.loads((FIXTURES / "engine_sec_company_tickers_sample.json").read_text()),
        as_of="2026-10-26",
    )
    return master.require("AAPL")


def _tick(*, last: float = 110.0, prev: float = 100.0, volume: float = 1_000) -> QuoteTick:
    return QuoteTick(
        symbol="AAPL",
        last=last,
        prev_close=prev,
        volume=volume,
        ts_exchange=datetime(2026, 10, 26, 14, 30, tzinfo=UTC),
        ts_received=datetime(2026, 10, 26, 14, 30, 1, tzinfo=UTC),
        source="yahoo",
    )


def test_quote_fields_maps_change_percent_not_absolute_change() -> None:
    fields = quote_fields(_tick(last=110.0, prev=100.0))

    assert fields["symbol"] == "AAPL"
    assert fields["price"] == 110.0
    assert fields["prev_close"] == 100.0
    assert fields["change"] == pytest.approx(10.0), (
        "the row's change column is a percent, not the tick's absolute move"
    )
    assert fields["volume"] == 1000.0
    assert fields["as_of"] == "2026-10-26T14:30:00+00:00"


def test_quote_fields_builds_a_row_with_the_catalog_units() -> None:
    row = build_row(_security(), {}, quote_fields(_tick()), as_of="2026-10-26")

    assert row["ticker"].value == "AAPL"
    assert row["price"].value == 110.0
    assert row["price"].unit.value == "USD"
    assert row["change"].value == pytest.approx(10.0)
    assert row["change"].unit.value == "pct"
    assert row["volume"].value == 1000.0
    assert row["volume"].unit.value == "shares"
    assert row["price"].provenance.as_of == "2026-10-26T14:30:00+00:00"


def test_quote_fields_keeps_missing_volume_missing() -> None:
    tick = QuoteTick(
        symbol="AAPL",
        last=110.0,
        prev_close=None,
        volume=None,
        ts_exchange=datetime(2026, 10, 26, 14, 30, tzinfo=UTC),
        ts_received=datetime(2026, 10, 26, 14, 30, 1, tzinfo=UTC),
        source="yahoo",
    )

    row = build_row(_security(), {}, quote_fields(tick), as_of="2026-10-26")

    assert "prev_close" not in row
    assert "change" not in row
    assert "volume" not in row


def test_quote_fields_rejects_a_foreign_symbol_at_row_build() -> None:
    tick = _tick()
    fields = quote_fields(tick)
    fields["symbol"] = "MSFT"

    with pytest.raises(Exception) as error:
        build_row(_security(), {}, fields, as_of="2026-10-26")
    assert getattr(error.value, "code", "") == "unknown_symbol"
