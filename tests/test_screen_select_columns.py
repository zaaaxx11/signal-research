"""`columns` on a selection: ask for the cells you want, get exactly those.

The light row is a published shape — 14 cells that every caller can rely on.
Depth added 41 more available cells, but putting all of them on every row would
bloat every response and change a contract that existing clients build against.

So the caller asks: `"columns": ["symbol", "roic", "pe_ttm"]`. Absent `columns`
keeps the historical light row exactly. A column the rows do not carry (but the
vocabulary knows) answers `null` — the column exists and the value is unknown,
which is different from a column that was never asked for.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.screen.selection import SelectionError, parse_selection, select_rows

ROWS: list[dict[str, Any]] = [
    {
        "rank": 1,
        "symbol": "NVDA",
        "name": "NVIDIA Corporation",
        "exchange": "XNAS",
        "country": "United States",
        "sector": "Technology",
        "price": 230.5,
        "change_pct": 1.2,
        "volume": 100,
        "turnover": 1000.0,
        "turnover_usd": 1000.0,
        "market_cap_usd": 5_000_000_000_000,
        "is_primary": True,
        "roic": 102.4,
        "pe_ttm": 27.0,
        "target_price": 334.4,
    }
]


def _request(**extra: Any) -> Any:
    payload = {
        "mode": "criteria",
        "region": "us",
        "criteria": {"filters": [], "limit": 5},
        **extra,
    }
    return parse_selection(payload)


def _select(request: Any) -> Any:
    return select_rows(
        ROWS, request, region="us", as_of="2026-10-30T00:00:00+00:00"
    )


def test_the_default_row_is_unchanged_without_columns() -> None:
    answer = _select(_request())
    row = answer.rows[0]

    assert set(row) == {
        "rank",
        "symbol",
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


def test_columns_project_exactly_what_was_asked() -> None:
    request = _request(columns=["symbol", "roic", "pe_ttm"])
    answer = _select(request)
    row = answer.rows[0]

    assert row["symbol"] == "NVDA"
    assert row["roic"] == 102.4
    assert row["pe_ttm"] == 27.0
    assert "name" not in row
    assert "as_of" in row, "every row still dates itself"


def test_columns_are_echoed_in_the_document() -> None:
    document = _select(_request(columns=["symbol", "roic"])).as_document()
    assert document["columns"] == ["symbol", "roic"]


def test_a_known_column_absent_from_the_row_is_null() -> None:
    """Unknown is not missing: the column exists, its value does not."""

    row = _select(_request(columns=["symbol", "inst_own"])).rows[0]
    assert "inst_own" in row
    assert row["inst_own"] is None


def test_an_unknown_column_is_rejected_by_name() -> None:
    with pytest.raises(SelectionError) as caught:
        _request(columns=["symbol", "not_a_column"])
    assert "not_a_column" in str(caught.value)


def test_columns_must_be_a_list_of_strings() -> None:
    with pytest.raises(SelectionError):
        _request(columns="symbol")
    with pytest.raises(SelectionError):
        _request(columns=["symbol", 3])


def test_duplicates_collapse_and_order_is_kept() -> None:
    request = _request(columns=["symbol", "roic", "symbol", "pe_ttm"])
    assert request.columns == ("symbol", "roic", "pe_ttm")


def test_the_column_ceiling_is_enforced() -> None:
    with pytest.raises(SelectionError):
        _request(columns=[f"c{index}" for index in range(60)])


def test_columns_and_view_are_mutually_exclusive() -> None:
    """Two ways to name the same output is a question with two answers."""

    with pytest.raises(SelectionError):
        _request(columns=["symbol"], view="quality")
