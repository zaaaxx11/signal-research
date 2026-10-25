"""The wide table's page-only cells have one job: fill a null, never a guess."""

from __future__ import annotations

import asyncio
from pathlib import Path

from app.providers.adapters.finviz import parse_wide_metrics
from app.screen.wide_fill import WideFiller


def _page(*pairs: tuple[str, str]) -> str:
    cells = "".join(
        '<div class="snapshot-td-label">'
        f"{label}"
        '</div><div class="snapshot-td-content">'
        f"{value}"
        "</div>"
        for label, value in pairs
    )
    return f"<html><body>{cells}</body></html>"


def test_parse_wide_metrics_reads_display_cells_without_guessing() -> None:
    body = _page(
        ("Employees", "166000"),
        ("IPO", "Dec 12, 1980"),
        ("Index", "DJIA, NDX, S&P 500"),
        ("Option/Short", "Yes / Yes"),
        ("LT Debt/Eq", "0.66"),
        ("EPS this Y", "18.43%"),
        ("EPS next 5Y", "12.58%"),
        ("EPS past 3/5Y", "6.89% 17.91%"),
        ("Sales past 3/5Y", "1.81% 8.71%"),
        ("EPS Y/Y TTM", "32.57%"),
        ("Sales Y/Y TTM", "14.24%"),
        ("EPS Q/Q", "29.13%"),
        ("Sales Q/Q", "16.36%"),
        ("Insider Trans", "-3.26%"),
        ("Inst Trans", "0.24%"),
    )

    values = parse_wide_metrics(body)

    assert values["employees"] == 166000.0
    assert values["ipo"] == "Dec 12, 1980"
    assert values["index"] == "DJIA, NDX, S&P 500"
    assert values["option_short"] == "Yes / Yes"
    assert values["lt_debt_to_equity"] == 0.66
    assert values["eps_this_y"] == 18.43
    assert values["eps_next_5y"] == 12.58
    assert values["eps_past_3_5y"] == "6.89% 17.91%"
    assert values["sales_past_3_5y"] == "1.81% 8.71%"
    assert values["eps_yoy_ttm"] == 32.57
    assert values["sales_yoy_ttm"] == 14.24
    assert values["eps_qoq"] == 29.13
    assert values["sales_qoq"] == 16.36
    assert values["insider_trans"] == -3.26
    assert values["inst_trans"] == 0.24


def test_parse_wide_metrics_skips_dashes_and_bad_numbers() -> None:
    body = _page(("Employees", "-"), ("LT Debt/Eq", "N/A"), ("IPO", ""))
    assert parse_wide_metrics(body) == {}


def test_wide_filler_fills_only_nulls_and_caches_one_page() -> None:
    body = _page(("Employees", "166000"), ("EPS Q/Q", "29.13%"))
    calls = 0

    class FakeAdapter:
        async def fetch(self, capability, instrument, fields):  # noqa: ANN001
            nonlocal calls
            calls += 1
            return {"status": 200, "body": body.encode(), "headers": {}}

    async def run() -> list[dict]:
        filler = WideFiller(adapter=FakeAdapter(), concurrency=2)
        rows = [{"symbol": "AAPL", "employees": None, "eps_qoq": None}]
        result = await filler.fill(rows)
        again = await filler.fill([{"symbol": "AAPL"}])
        assert calls == 1
        return result + again

    result = asyncio.run(run())
    assert result[0]["employees"] == 166000.0
    assert result[0]["eps_qoq"] == 29.13
    assert result[1]["employees"] == 166000.0


def test_wide_filler_does_not_overwrite_an_existing_value() -> None:
    body = _page(("Employees", "166000"))

    class FakeAdapter:
        async def fetch(self, capability, instrument, fields):  # noqa: ANN001
            return {"status": 200, "body": body.encode(), "headers": {}}

    async def run() -> list[dict]:
        return await WideFiller(adapter=FakeAdapter()).fill(
            [{"symbol": "AAPL", "employees": 12}]
        )

    result = asyncio.run(run())
    assert result[0]["employees"] == 12
