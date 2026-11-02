"""T4 — ``GET /engine/snapshot/{symbol}``: one catalogue row for one symbol.

The ticker page's Valuation and Profile blocks used to read
``GET /engine/fundamentals/{symbol}``, an SEC-facts document that has no P/E,
PEG, P/S, P/B, EV/EBITDA or quick ratio, and whose ``debt_to_equity`` is
``total_debt / equity * 100`` (76.54) labelled a multiple. The page therefore
printed twelve em dashes and one percentage in a ratio slot.

The snapshot route is the fix: **one** call, the same scan row the screener
already builds, the same depth overlay it already merges, every catalogue key
the six-column grid asks for. These tests pin the four things that can silently
regress:

1. **Coverage.** The grid is 6 × 14 = 84 cells; every one of them must be a key
   in ``values`` and every key must declare a unit.
2. **Units.** ``debt_to_equity`` / ``lt_debt_to_equity`` are ratios in
   ``values``; the percentage spelling has its own ``_pct`` key and its own
   ``percent`` unit. A 76.54 can never be read as a ratio.
3. **Flags.** ``optionable`` / ``shortable`` arrive from the depth store as
   ``"Yes"`` / ``"No"`` and are booleans on the wire.
4. **Honesty.** An absent cell is ``null`` and named in ``missing`` — never a
   zero — and the route answers for a symbol that only the depth store knows
   rather than pretending the symbol does not exist.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx2
import pytest

from app.screen.ticker_snapshot import (
    DEPTH_UNIT_OVERRIDES,
    GRID_COLUMNS,
    LEVERAGE_KEYS,
    SNAPSHOT_KEYS,
    SNAPSHOT_UNITS,
    SNAPSHOT_UNIT_VOCABULARY,
    TEXT_KEYS,
    flag_value,
    leverage_pair,
    snapshot_document,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def row(**overrides: Any) -> dict[str, Any]:
    """One ``build_universe`` parity row, spelled the way ``value_for`` reads it.

    ``pe_ttm`` rather than ``pe``, ``sma_50`` rather than ``sma50``,
    ``float_shares`` rather than ``shares_float``: the catalogue's synonyms are
    the point of reading a row through :func:`app.screen.columns.value_for`
    instead of with ``row.get(key)``.
    """

    base: dict[str, Any] = {
        "symbol": "AAPL",
        "name": "Apple Inc.",
        "price": 100.0,
        "change_pct": 2.5,
        "volume": 12_345_678.0,
        "market_cap_usd": 3_000_000_000_000.0,
        "pe_ttm": 39.44,
        "pb": 46.75,
        "peg": 1.22,
        "pfcf": 37.04,
        "ev_ebitda": 20.13,
        "quick_ratio": 0.929,
        "current_ratio": 1.0033,
        "debt_to_equity": 0.7844,
        "beta_1y": 1.23,
        "rsi_14": 56.7,
        "sma_50": 200.0,
        "sma_200": 180.0,
        "ret_1w": 1.2,
        "ret_1m": 3.4,
        "float_shares": 15_000_000_000.0,
        "shares_outstanding": 15_100_000_000.0,
    }
    base.update(overrides)
    return base


def depth_of(
    symbols: Mapping[str, Mapping[str, Any]],
    *,
    as_of: str | None = "2026-11-02T04:30:00+00:00",
):
    from app.screen.depth_source import DepthSnapshot

    return DepthSnapshot(as_of=as_of, symbols=symbols, loaded_at=0.0)


def no_depth():
    from app.screen.depth_source import empty_snapshot

    return empty_snapshot()


# ---------------------------------------------------------------------------
# Coverage — the grid the client draws
# ---------------------------------------------------------------------------


def test_the_grid_is_six_columns_of_fourteen() -> None:
    assert len(GRID_COLUMNS) == 6
    assert [len(keys) for keys in GRID_COLUMNS.values()] == [14] * 6
    assert len(SNAPSHOT_KEYS) == 84 + 6  # 84 grid cells + the documented extras


def test_the_grid_carries_the_valuation_column_verbatim() -> None:
    assert GRID_COLUMNS["valuation"] == (
        "pe",
        "forward_pe",
        "peg",
        "ps",
        "pb",
        "pc",
        "pfcf",
        "ev_ebitda",
        "ev_sales",
        "quick_ratio",
        "current_ratio",
        "debt_to_equity",
        "lt_debt_to_equity",
        "optionable",
    )


def test_every_key_declares_a_unit_from_one_vocabulary() -> None:
    # The vocabulary is the client's formatter contract, so it is exactly five
    # names: a `date`/`text`/`number` unit would be a second formatting language.
    assert SNAPSHOT_UNIT_VOCABULARY == {"ratio", "percent", "usd", "count", "bool"}
    assert set(SNAPSHOT_UNITS.values()) <= SNAPSHOT_UNIT_VOCABULARY
    # Text cells carry their own shape; every other key declares a unit.
    assert set(SNAPSHOT_UNITS) == set(SNAPSHOT_KEYS) - set(TEXT_KEYS)


def test_text_cells_are_values_without_a_formatting_unit() -> None:
    document = snapshot_document(
        "AAPL",
        row(ipo_date="1980-12-12", earnings_date="2026-10-29"),
        index_members=[],
    )

    assert document["values"]["ipo_date"] == "1980-12-12"
    assert document["values"]["earnings_date"] == "2026-10-29"
    assert document["values"]["index_membership"] == []
    for key in TEXT_KEYS:
        assert key in document["values"]
        assert key not in document["units"]


def test_the_document_publishes_every_key_exactly_once() -> None:
    document = snapshot_document("AAPL", row())

    assert list(document["values"]) == list(SNAPSHOT_KEYS)
    assert set(document["units"]) == set(SNAPSHOT_KEYS) - set(TEXT_KEYS)
    assert document["symbol"] == "AAPL"


# ---------------------------------------------------------------------------
# Values — the row's own cells, through the catalogue's synonyms
# ---------------------------------------------------------------------------


def test_values_are_the_rows_own_cells() -> None:
    values = snapshot_document("AAPL", row())["values"]

    # pe_ttm -> pe, sma_50 -> sma50, float_shares -> shares_float.
    assert values["pe"] == 39.44
    assert values["pb"] == 46.75
    assert values["peg"] == 1.22
    assert values["pfcf"] == 37.04
    assert values["ev_ebitda"] == 20.13
    assert values["quick_ratio"] == 0.929
    assert values["current_ratio"] == 1.0033
    assert values["sma50"] == 200.0
    assert values["sma200"] == 180.0
    assert values["shares_float"] == 15_000_000_000.0
    assert values["float_shares"] == 15_000_000_000.0
    assert values["market_cap"] == 3_000_000_000_000.0


def test_the_performance_column_is_percent_points() -> None:
    values = snapshot_document("AAPL", row())["values"]

    assert values["perf_1w"] == 1.2
    assert values["perf_4w"] == 3.4
    assert snapshot_document("AAPL", row())["units"]["perf_4w"] == "percent"


def test_absent_cells_are_null_and_named_in_missing() -> None:
    document = snapshot_document("AAPL", {"symbol": "AAPL"})

    assert document["values"]["pe"] is None
    assert document["values"]["ps"] is None
    assert "pe" in document["missing"]
    assert "ps" in document["missing"]
    assert document["missing"] == [
        key for key in SNAPSHOT_KEYS if document["values"][key] is None
    ]


def test_a_reported_zero_is_a_value_not_an_absence() -> None:
    document = snapshot_document("AAPL", row(pe_ttm=0.0, volume=0.0))

    assert document["values"]["pe"] == 0.0
    assert document["values"]["volume"] == 0.0
    assert "pe" not in document["missing"]
    assert "volume" not in document["missing"]


def test_a_non_finite_number_is_an_absence() -> None:
    document = snapshot_document("AAPL", row(pe_ttm=float("nan"), beta_1y=float("inf")))

    assert document["values"]["pe"] is None
    assert document["values"]["beta"] is None
    assert "pe" in document["missing"]


def test_a_text_cell_is_not_silently_dropped() -> None:
    document = snapshot_document("AAPL", row(ipo_date="1980-12-12"))

    assert document["values"]["ipo_date"] == "1980-12-12"


def test_an_epoch_date_cell_becomes_iso_text() -> None:
    """The parity scan spells a date as TradingView epoch seconds."""

    document = snapshot_document("AAPL", row(earnings_date=1793655000.0))

    assert document["values"]["earnings_date"] == "2026-11-02"
    assert "earnings_date" not in document["missing"]


def test_prev_close_is_the_screener_identity() -> None:
    document = snapshot_document("AAPL", row(price=80.0, change_pct=-20.0))

    assert document["values"]["prev_close"] == pytest.approx(100.0)
    assert document["units"]["prev_close"] == "usd"


# ---------------------------------------------------------------------------
# Units — the bug this route exists to end
# ---------------------------------------------------------------------------


def test_debt_to_equity_is_published_as_a_ratio_with_a_percent_sibling() -> None:
    document = snapshot_document("AAPL", row())
    values = document["values"]

    assert values["debt_to_equity"] == pytest.approx(0.7844)
    assert values["debt_to_equity_pct"] == pytest.approx(78.44)
    assert document["units"]["debt_to_equity"] == "ratio"
    assert document["units"]["debt_to_equity_pct"] == "percent"


def test_a_percent_cell_normalises_to_the_same_ratio() -> None:
    """76.54 is what the page printed; 0.7654 is what a ratio key must hold."""

    ratio, percent = leverage_pair(76.54, unit="percent")

    assert ratio == pytest.approx(0.7654)
    assert percent == pytest.approx(76.54)


def test_the_two_leverage_keys_are_the_only_ones_with_a_pct_sibling() -> None:
    assert set(LEVERAGE_KEYS) == {"debt_to_equity", "lt_debt_to_equity"}
    siblings = {
        key
        for key in SNAPSHOT_KEYS
        if key.endswith("_pct") and key[: -len("_pct")] in LEVERAGE_KEYS
    }
    assert siblings == {"debt_to_equity_pct", "lt_debt_to_equity_pct"}
    # `float_pct` ends in `_pct` but is a catalogue name of its own, not a
    # percent spelling of a `float` cell: it must keep its measured value.
    assert snapshot_document("AAPL", row(float_pct=12.5))["values"]["float_pct"] == 12.5


def test_the_percent_sibling_of_change_is_a_wire_alias_not_a_conversion() -> None:
    document = snapshot_document("AAPL", row(change_pct=2.5))

    assert document["values"]["change"] == 2.5
    assert document["values"]["change_pct"] == 2.5
    assert document["units"]["change"] == "percent"
    assert document["units"]["change_pct"] == "percent"


def test_a_missing_leverage_cell_leaves_both_spellings_null() -> None:
    document = snapshot_document("AAPL", {"symbol": "AAPL"})

    assert document["values"]["debt_to_equity"] is None
    assert document["values"]["debt_to_equity_pct"] is None
    assert "debt_to_equity" in document["missing"]
    assert "debt_to_equity_pct" in document["missing"]


# ---------------------------------------------------------------------------
# Flags — 'Yes'/'No' from the depth store become booleans
# ---------------------------------------------------------------------------


def test_optionable_and_shortable_are_booleans_on_the_wire() -> None:
    document = snapshot_document(
        "AAPL", row(optionable="Yes", shortable="No")
    )

    assert document["values"]["optionable"] is True
    assert document["values"]["shortable"] is False
    assert document["units"]["optionable"] == "bool"
    assert document["units"]["shortable"] == "bool"
    assert "optionable" not in document["missing"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Yes", True),
        ("yes", True),
        (" Y ", True),
        ("true", True),
        ("No", False),
        ("NO", False),
        ("false", False),
        (True, True),
        (False, False),
        (1, True),
        (0, False),
        ("maybe", None),
        (2, None),
        (None, None),
    ],
)
def test_flag_normalisation(raw: Any, expected: bool | None) -> None:
    assert flag_value(raw) is expected


# ---------------------------------------------------------------------------
# Index membership and the depth store's own units
# ---------------------------------------------------------------------------


def test_index_membership_is_passed_through() -> None:
    document = snapshot_document(
        "AAPL", row(), index_members=["S&P 500", "NASDAQ 100"]
    )

    assert document["values"]["index_membership"] == ["S&P 500", "NASDAQ 100"]
    assert "index_membership" not in document["missing"]


def test_unknown_membership_is_null_and_in_none_is_an_empty_list() -> None:
    assert (
        snapshot_document("AAPL", row(), index_members=None)["values"][
            "index_membership"
        ]
        is None
    )
    empty = snapshot_document("AAPL", row(), index_members=[])
    assert empty["values"]["index_membership"] == []
    assert "index_membership" not in empty["missing"]


def test_a_depth_only_moving_average_declares_the_stores_percent_unit() -> None:
    """The scan's SMA is a price level; the depth store's is a percent distance.

    Keeping the number while lying about its unit is the class of bug this
    route exists to end, so a cell only the depth store could fill publishes
    the depth store's unit — and the static table still says ``usd``.
    """

    scan = {"symbol": "AAPL", "price": 100.0}
    merged = dict(scan, sma50=3.2)

    document = snapshot_document("AAPL", merged, scan_row=scan)

    assert document["values"]["sma50"] == 3.2
    assert document["units"]["sma50"] == "percent"
    assert DEPTH_UNIT_OVERRIDES["sma50"] == "percent"
    # A cell the scan did carry keeps the level unit.
    assert snapshot_document("AAPL", row())["units"]["sma50"] == "usd"


# ---------------------------------------------------------------------------
# The route
# ---------------------------------------------------------------------------


@asynccontextmanager
async def route_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[httpx2.AsyncClient]:
    monkeypatch.setenv("MEE_DB_PATH", str(tmp_path / "mee.db"))
    monkeypatch.setenv("MEE_EXPORT_DIR", str(tmp_path / "exports"))

    from app.main import create_app

    application = create_app()
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=application), base_url="http://mee.test"
    ) as client:
        yield client


def install(
    monkeypatch: pytest.MonkeyPatch,
    rows: Sequence[dict[str, Any]] | Exception,
    depth: Any = None,
    members: Sequence[str] | None = None,
) -> None:
    if isinstance(rows, Exception):
        async def failing(_request: Any) -> Sequence[dict[str, Any]]:
            raise rows

        monkeypatch.setattr("app.api.engine.ticker_universe", failing)
    else:
        async def universe(_request: Any) -> Sequence[dict[str, Any]]:
            return rows

        monkeypatch.setattr("app.api.engine.ticker_universe", universe)
    monkeypatch.setattr(
        "app.api.engine.current_depth", lambda: depth if depth is not None else no_depth()
    )
    monkeypatch.setattr("app.api.engine.index_members_for", lambda symbol: members)


async def test_the_route_answers_the_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install(monkeypatch, [row()], members=["S&P 500"])

    async with route_client(tmp_path, monkeypatch) as client:
        response = await client.get("/engine/snapshot/AAPL")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["symbol"] == "AAPL"
    assert body["as_of"] is None  # no depth store, no invented stamp
    assert body["values"]["pe"] == 39.44
    assert body["values"]["index_membership"] == ["S&P 500"]
    assert set(body["values"]) == set(SNAPSHOT_KEYS)
    filled = sum(1 for value in body["values"].values() if value is not None)
    assert filled + len(body["missing"]) == len(SNAPSHOT_KEYS)


async def test_the_route_fills_gaps_from_depth_and_keeps_the_scans_own_cell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    depth = depth_of(
        {
            "AAPL": {
                "ps": 8.51,
                "pc": 30.2,
                "ev_sales": 9.4,
                "lt_debt_to_equity": 0.66,
                "optionable": "Yes",
                "shortable": "No",
                "pe": 999.0,  # the scan's own P/E must win
            }
        }
    )
    install(monkeypatch, [row()], depth=depth)

    async with route_client(tmp_path, monkeypatch) as client:
        response = await client.get("/engine/snapshot/aapl")

    assert response.status_code == 200, response.text
    body = response.json()
    values = body["values"]
    assert body["as_of"] == "2026-11-02T04:30:00+00:00"
    assert values["ps"] == 8.51
    assert values["pc"] == 30.2
    assert values["ev_sales"] == 9.4
    assert values["lt_debt_to_equity"] == 0.66
    assert values["lt_debt_to_equity_pct"] == pytest.approx(66.0)
    assert values["optionable"] is True
    assert values["shortable"] is False
    assert values["pe"] == 39.44


async def test_the_route_unknown_symbol_is_404(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install(monkeypatch, [row(symbol="MSFT")])

    async with route_client(tmp_path, monkeypatch) as client:
        response = await client.get("/engine/snapshot/AAPL")

    assert response.status_code == 404, response.text
    assert response.json()["error"] == "unknown_symbol"


async def test_a_symbol_the_scan_does_not_cover_answers_from_depth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    depth = depth_of(
        {
            "VOD.L": {
                "pe": 12.0,
                "market_cap": 1.0e11,
                "optionable": "No",
            }
        }
    )
    install(monkeypatch, [row()], depth=depth)

    async with route_client(tmp_path, monkeypatch) as client:
        response = await client.get("/engine/snapshot/VOD.L")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["symbol"] == "VOD.L"
    assert body["as_of"] == "2026-11-02T04:30:00+00:00"
    assert body["values"]["pe"] == 12.0
    assert body["values"]["market_cap"] == 1.0e11
    assert body["values"]["optionable"] is False
    # Everything neither store carried is named, not zeroed.
    assert body["values"]["price"] is None
    assert "price" in body["missing"]
    assert "dividend_yield" in body["missing"]


async def test_a_blocked_scan_with_no_depth_is_503(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.screen.scanner import ScanError

    install(monkeypatch, ScanError("scanner blocked"))

    async with route_client(tmp_path, monkeypatch) as client:
        response = await client.get("/engine/snapshot/AAPL")

    assert response.status_code == 503, response.text
    assert response.json()["error"] == "scanner_unavailable"


async def test_a_blocked_scan_still_answers_a_symbol_depth_knows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.screen.scanner import ScanError

    install(
        monkeypatch,
        ScanError("scanner blocked"),
        depth=depth_of({"AAPL": {"pe": 39.4, "optionable": "Yes"}}),
    )

    async with route_client(tmp_path, monkeypatch) as client:
        response = await client.get("/engine/snapshot/AAPL")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["values"]["pe"] == 39.4
    assert body["values"]["optionable"] is True
    assert "price" in body["missing"]
