"""P6 — ``asset_class`` and the six-view row shape of ``POST /screens/select``.

The selection contract used to answer only the light browse row. This module
pins the two additions:

* **``asset_class``** (``stock`` default) chooses the universe. ``etf`` answers
  over ``type=fund``/``subtype=etf`` with no cap floor, so the same criteria
  language addresses the ETF market instead of the stock market; the stock
  default is byte-identical to the pre-P6 response.
* **``view``** (one of the six Finviz tabs) shapes every response row as that
  view: ``rank``/``symbol``/``as_of`` plus one cell per declared view column,
  filled from the parity metrics the bulk scan carries. It is **not** the light
  row — a valuation selection carries ``pe``, not ``market_cap_usd`` — and a
  column the bulk scan cannot answer stays ``None`` instead of being invented.
  An unknown view is a 422 before any provider request.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.screen import selection as selection_module
from app.screen.fundamentals import FUNDAMENTALS_ROW_FIELDS, VIEW_FILL_FIELDS
from app.screen import views as V
from app.screen.selection import VIEW_FACT_FIELDS, VIEW_FIELDS, parse_selection, select_rows
from tests import _screen as S
from tests.test_screen_etf import ETF_UNIVERSE_TOTAL, TOP20, etf_web

#: The moment the executor stamps; fixed so two runs are comparable.
AS_OF = "2026-10-23T12:00:00.000000+00:00"

ENVELOPE_KEYS = {
    "mode",
    "region",
    "source",
    "as_of",
    "snapshot_id",
    "sort",
    "limit",
    "universe_total",
    "total",
    "returned",
    "filters",
    "symbols",
    "missing",
    "rows",
    "columns",
    # The panel budget report (F9): bytes shipped, whether the body was
    # truncated, which fields went and how many rows were cut from the tail.
    "budget",
}


def provider_row(symbol: str, cap: float, **overrides: Any) -> dict[str, Any]:
    """One provider-shaped stock row carrying the parity columns a view reads."""

    row: dict[str, Any] = {
        "name": symbol,
        "symbol": symbol,
        "description": f"{symbol} Holdings Inc.",
        "exchange": "NASDAQ",
        "country": "United States",
        "sector": "Technology",
        "type": "stock",
        "subtype": "common",
        "currency": "USD",
        "close": 50.0,
        "change": 1.5,
        "volume": 1_000_000,
        "Value.Traded": 50_000_000.0,
        "market_cap_basic": cap,
        "RSI": 55.0,
        "beta_1_year": 1.1,
        "price_52_week_high": 120.0,
        "price_52_week_low": 30.0,
        "price_earnings_ttm": 25.5,
        "price_earnings_forward": 20.0,
        "price_earnings_growth_ttm": 1.4,
        "price_sales_ttm": 6.2,
        "price_book_fq": 8.5,
        "price_free_cash_flow_ttm": 30.0,
        "enterprise_value_ebitda_ttm": 15.2,
        "return_on_equity": 18.0,
        "return_on_assets": 9.0,
        "return_on_invested_capital": 12.0,
        "current_ratio": 1.6,
        "quick_ratio": 1.3,
        "debt_to_equity": 0.55,
        "gross_margin": 62.0,
        "operating_margin": 28.0,
        "net_margin": 21.0,
        "total_shares_outstanding": 1_000_000_000,
        "float_shares_outstanding": 800_000_000,
        "average_volume_30d_calc": 900_000,
        "relative_volume_10d_calc": 1.05,
        "earnings_per_share_forecast_next_fy": 7.75,
        "earnings_per_share_forecast_next_fq": 1.9,
        "SMA20": 100.5,
        "SMA50": 98.0,
        "SMA200": 90.0,
        "Perf.W": 1.2,
        "Perf.1M": 3.4,
        "Perf.3M": 6.7,
        "Perf.6M": 9.8,
        "Perf.YTD": 12.5,
        "Perf.Y": 22.0,
        "Perf.5Y": 80.0,
        "Perf.10Y": 150.0,
        "Volatility.W": 2.1,
        "Volatility.M": 3.2,
    }
    row.update(overrides)
    return row


ROWS = [
    provider_row("AAA", 900_000_000_000),
    provider_row("BBB", 800_000_000_000),
    provider_row("CCC", 700_000_000_000),
    provider_row("DDD", 600_000_000_000),
    provider_row("EEE", 500_000_000_000),
    provider_row("FFF", 400_000_000_000),
]


def parity_web(rows: list[dict[str, Any]] | None = None) -> Any:
    """Answer the scanner by echoing the columns the client asked for."""

    table = {row["symbol"]: row for row in (rows or ROWS)}
    seen: list[dict[str, Any]] = []

    def respond(request: Any) -> dict[str, Any]:
        import json

        body = json.loads((request.content or b"{}").decode("utf-8"))
        seen.append(body)
        columns = body["columns"]
        return {
            "totalCount": len(table),
            "data": [
                {
                    "s": f"{row['exchange']}:{row['symbol']}",
                    "d": [row.get(column) for column in columns],
                }
                for row in table.values()
            ],
        }

    web = S.MockWeb()
    web.json("tradingview", respond)
    web.seen = seen  # type: ignore[attr-defined]
    return web


def selection_body(
    *, view: str | None = None, asset_class: str | None = None, **criteria: Any
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "mode": "criteria",
        "region": "us",
        "criteria": {"filters": [], "sort": "market_cap", "limit": 20, **criteria},
    }
    if view is not None:
        body["view"] = view
    if asset_class is not None:
        body["asset_class"] = asset_class
    return body


# ---------------------------------------------------------------------------
# The six views
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("view", list(V.VIEW_NAMES))
async def test_each_view_shapes_the_row_with_its_own_columns(
    view: str, tmp_path: Any, monkeypatch: Any
) -> None:
    async with S.screen_client(tmp_path, monkeypatch, web=parity_web()) as harness:
        response = await harness.client.post("/screens/select", json=selection_body(view=view))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == len(ROWS) and body["returned"] == len(ROWS)
    expected = {"rank", "symbol", "name", "as_of", "logo"} | set(V.VIEW_COLUMNS[view])
    for row in body["rows"]:
        assert set(row) == expected, (
            f"a {view} row must be identity + exactly the declared columns; "
            f"extra={sorted(set(row) - expected)}, missing={sorted(expected - set(row))}"
        )
    assert body["rows"][0]["symbol"] == "AAA"


async def test_all_columns_view_returns_the_whole_metric_table(
    tmp_path: Any, monkeypatch: Any
) -> None:
    async with S.screen_client(tmp_path, monkeypatch, web=parity_web()) as harness:
        response = await harness.client.post("/screens/select", json=selection_body(view="all"))

    assert response.status_code == 200, response.text
    body = response.json()
    expected = {"rank", "symbol", "name", "as_of", "logo"} | set(V.ALL_COLUMNS)
    for row in body["rows"]:
        assert set(row) == expected
    # `all` is the whole catalogue now (116 keys), not the old 71-metric quote
    # vocabulary: the depth store fills 106 of the 116, so a 71-key "all" was
    # hiding 45 columns the engine already carries.
    assert len(V.ALL_COLUMNS) == 116


def test_a_view_row_is_not_the_light_row() -> None:
    request = parse_selection(selection_body(view="valuation"))
    rows = [
        {
            "symbol": row["symbol"],
            "name": row["name"],
            "exchange": "XNAS",
            "country": "US",
            "sector": "Technology",
            "price": 50.0,
            "change_pct": 1.5,
            "volume": 1_000_000,
            "turnover": 50_000_000.0,
            "turnover_usd": 50_000_000.0,
            "market_cap_usd": row["market_cap_basic"],
            "is_primary": True,
            "pe_ttm": 25.5,
            "pb": 8.5,
        }
        for row in ROWS[:1]
    ]
    document = select_rows(rows, request, region="us", as_of=AS_OF).as_document()
    row = document["rows"][0]

    assert set(row) == {"rank", "symbol", "name", "as_of"} | set(V.VIEW_COLUMNS["valuation"])
    assert row["pe"] == 25.5 and row["pb"] == 8.5
    assert "market_cap_usd" not in row and "turnover_usd" not in row, (
        "the light row's own column names must not leak into a view row"
    )
    assert row["enterprise_value"] is None, (
        "a view column the bulk scan cannot answer is null, not 0 and not dropped"
    )


@pytest.mark.parametrize(
    "view, expected",
    [
        ("overview", {"pe": 25.5, "market_cap": 900_000_000_000.0, "rsi_14": 55.0}),
        ("valuation", {"pe": 25.5, "pb": 8.5, "peg": 1.4, "ev_ebitda": 15.2}),
        ("ownership", {"shares_outstanding": 1_000_000_000, "shares_float": 800_000_000}),
        ("performance", {"perf_week": 1.2, "perf_ytd": 12.5, "perf_5y": 80.0}),
        ("financial", {"roe": 18.0, "roa": 9.0, "debt_to_equity": 0.55, "oper_margin": 28.0}),
        ("technical", {"sma20": 100.5, "beta": 1.1, "rsi_14": 55.0, "price": 50.0}),
    ],
)
async def test_view_cells_come_from_the_parity_metrics(
    view: str, expected: dict[str, Any], tmp_path: Any, monkeypatch: Any
) -> None:
    async with S.screen_client(tmp_path, monkeypatch, web=parity_web()) as harness:
        response = await harness.client.post("/screens/select", json=selection_body(view=view))

    row = response.json()["rows"][0]
    for column, value in expected.items():
        assert row[column] == value, (
            f"{view}.{column} should be the parity metric value ({value}), got {row[column]!r}"
        )


async def test_the_view_scan_asks_for_the_parity_columns(
    tmp_path: Any, monkeypatch: Any
) -> None:
    web = parity_web()
    async with S.screen_client(tmp_path, monkeypatch, web=web) as harness:
        await harness.client.post("/screens/select", json=selection_body(view="overview"))

    from app.screen.universe import PARITY_COLUMNS

    assert list(web.seen[0]["columns"]) == list(PARITY_COLUMNS)


async def test_an_unknown_view_is_a_422_before_the_scan(
    tmp_path: Any, monkeypatch: Any
) -> None:
    async with S.screen_client(tmp_path, monkeypatch, web=parity_web()) as harness:
        response = await harness.client.post("/screens/select", json=selection_body(view="summary"))
        requests = S.provider_requests(harness.web)

    assert response.status_code == 422, response.text
    body = response.json()
    assert set(body) == {"error", "errorMessage"}
    assert body["error"] == "unknown_view"
    assert "summary" in body["errorMessage"]
    for name in V.VIEW_NAMES:
        assert name in body["errorMessage"]
    assert requests == 0, "a view typo must not buy a 6,000-row scan"


def test_an_invalid_view_is_rejected_by_the_validator() -> None:
    with pytest.raises(selection_module.SelectionError) as caught:
        parse_selection(selection_body(view="overview2"))
    assert caught.value.status_code == 422
    assert caught.value.code == "unknown_view"


# ---------------------------------------------------------------------------
# asset_class
# ---------------------------------------------------------------------------


async def test_an_etf_selection_answers_over_the_etf_universe(
    tmp_path: Any, monkeypatch: Any
) -> None:
    seen: list[dict[str, Any]] = []
    async with S.screen_client(tmp_path, monkeypatch, web=etf_web(seen)) as harness:
        response = await harness.client.post(
            "/screens/select", json=selection_body(asset_class="etf")
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["universe_total"] == ETF_UNIVERSE_TOTAL == 6_123
    assert body["total"] == 6_123
    assert body["returned"] == 20 and body["limit"] == 20
    assert [row["symbol"] for row in body["rows"]] == TOP20
    assert "market_cap_usd" in body["rows"][0], "no view means the light row"

    filters = {item["left"]: item["right"] for item in seen[0]["filter"]}
    assert filters["type"] == "fund" and filters["subtype"] == "etf"
    assert "market_cap_basic" not in filters


async def test_the_us_etf_region_implies_the_etf_family(
    tmp_path: Any, monkeypatch: Any
) -> None:
    async with S.screen_client(tmp_path, monkeypatch, web=etf_web()) as harness:
        response = await harness.client.post(
            "/screens/select",
            json={
                "mode": "criteria",
                "region": "us_etf",
                "criteria": {"filters": [], "sort": "market_cap"},
            },
        )

    body = response.json()
    assert response.status_code == 200, response.text
    assert body["region"] == "us_etf"
    assert body["universe_total"] == 6_123


async def test_an_etf_view_row_reads_etf_parity_metrics(
    tmp_path: Any, monkeypatch: Any
) -> None:
    async with S.screen_client(tmp_path, monkeypatch, web=etf_web()) as harness:
        response = await harness.client.post(
            "/screens/select", json=selection_body(asset_class="etf", view="technical")
        )

    body = response.json()
    assert response.status_code == 200, response.text
    row = body["rows"][0]
    assert row["symbol"] == "SPY"
    assert row["rsi_14"] == 30.0, "the fixture's first ETF carries RSI 30"
    assert row["beta"] == 0.6
    assert set(row) == {"rank", "symbol", "name", "as_of", "logo"} | set(V.VIEW_COLUMNS["technical"])


async def test_an_unknown_asset_class_is_a_422(tmp_path: Any, monkeypatch: Any) -> None:
    async with S.screen_client(tmp_path, monkeypatch, web=parity_web()) as harness:
        response = await harness.client.post(
            "/screens/select", json=selection_body(asset_class="bond")
        )
        requests = S.provider_requests(harness.web)

    assert response.status_code == 422, response.text
    body = response.json()
    assert body["error"] == "invalid_selection"
    assert "bond" in body["errorMessage"] and "stock" in body["errorMessage"]
    assert requests == 0


async def test_stock_and_etf_selections_do_not_mix(
    tmp_path: Any, monkeypatch: Any
) -> None:
    async with S.screen_client(tmp_path, monkeypatch, web=etf_web()) as harness:
        response = await harness.client.post(
            "/screens/select",
            json={
                "mode": "criteria",
                "region": "global",
                "asset_class": "etf",
                "criteria": {},
            },
        )

    assert response.status_code == 422, response.text
    assert "US-only" in response.json()["errorMessage"]


# ---------------------------------------------------------------------------
# Defaults: the pre-P6 response is unchanged
# ---------------------------------------------------------------------------


async def test_a_stock_selection_without_view_is_still_the_light_row(
    tmp_path: Any, monkeypatch: Any
) -> None:
    async with S.screen_client(tmp_path, monkeypatch, web=parity_web()) as harness:
        response = await harness.client.post("/screens/select", json=selection_body())

    body = response.json()
    assert response.status_code == 200, response.text
    assert set(body) == ENVELOPE_KEYS
    assert body["universe_total"] == len(ROWS)
    row = body["rows"][0]
    assert "pe" not in row and "market_cap_usd" in row
    assert row["turnover_usd"] == 50_000_000.0
    assert set(row) == {
        "rank",
        "symbol",
        "logo",
        "name",
        "exchange",
        "country",
        "sector",
        "price",
        "change_pct",
        "volume",
        "turnover",
        "turnover_usd",
        "market_cap_usd",
        "is_primary",
        "as_of",
    }


async def test_symbols_mode_still_resolves_in_request_order_without_a_view(
    tmp_path: Any, monkeypatch: Any
) -> None:
    async with S.screen_client(tmp_path, monkeypatch, web=parity_web()) as harness:
        screen = await harness.create_screen()
        response = await harness.client.post(
            "/screens/select",
            json={
                "mode": "symbols",
                "symbols": ["CCC", "AAA", "NOPE"],
                "source": "tradingview",
                "snapshot_id": screen["screen_id"],
            },
        )

    body = response.json()
    assert response.status_code == 200, response.text
    assert [row["symbol"] for row in body["rows"]] == ["CCC", "AAA"]
    assert body["missing"] == ["NOPE"]
    assert "market_cap_usd" in body["rows"][0]


async def test_symbols_mode_can_ask_for_a_view(
    tmp_path: Any, monkeypatch: Any
) -> None:
    async with S.screen_client(tmp_path, monkeypatch, web=etf_web()) as harness:
        screen = await harness.create_screen(region="us_etf")
        response = await harness.client.post(
            "/screens/select",
            json={
                "mode": "symbols",
                "symbols": ["QQQ", "SPY"],
                "source": "tradingview",
                "snapshot_id": screen["screen_id"],
                "view": "overview",
            },
        )

    body = response.json()
    assert response.status_code == 200, response.text
    assert [row["symbol"] for row in body["rows"]] == ["QQQ", "SPY"]
    assert [row["rank"] for row in body["rows"]] == [1, 2]
    assert set(body["rows"][0]) == {"rank", "symbol", "name", "as_of", "logo"} | set(
        V.VIEW_COLUMNS["overview"]
    )
    assert body["rows"][0]["market_cap"] == 330_000_000_000


def test_view_fields_only_name_columns_a_row_or_a_view_has() -> None:
    """Every view target is a column the row layer really carries.

    ``VIEW_FIELDS`` is a projection of :mod:`app.screen.columns` now, so the
    universe it must stay inside is that module's row-column table (parity
    copies, derived cells, the two joins, the display fill, the light row and the
    catalogue keys the depth overlay copies) rather than four of those tables
    named one by one — which is how the projection used to miss 62 columns.
    """

    from app.screen import columns as column_map

    declared = set().union(*(set(columns) for columns in V.VIEW_COLUMNS.values()))
    assert set(VIEW_FIELDS) <= declared
    assert set(VIEW_FIELDS.values()) <= column_map.ROW_FIELD_COLUMNS
