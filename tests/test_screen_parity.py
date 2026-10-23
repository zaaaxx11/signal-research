"""Screen P5 — the Finviz filters run over a bulk parity universe, not 13 columns.

Contract (PLAN-finviz-parity.md §6 gate 3, "82/82 keys"):

* **32 of the 82 keys filter a real column** — the seven light ones plus the
  twenty-five parity metrics a bulk scan copies from TradingView whose columns
  are really filled — and each of the 32 is proven by running its compiled
  predicate over a row that carries the field;
* **the other 50 are refused by name**, with the metric they need, instead of
  being applied as a no-op (an ignored filter returns the unfiltered screen as
  if it matched);
* a parity cell is copied **verbatim**: a number stays a number, a provider
  ``null`` stays ``None`` (never ``0``), and a cell that is not a finite number
  (``NaN``, ``inf``, ``"N/A"``) is unknown — so ``P/E != 10`` does not quietly
  keep a company whose P/E nobody reported;
* a **criteria** selection scans the parity columns with no market-cap floor and
  no ``subtype=common`` gate, which is what lets ``P/E <= 10`` mean the whole US
  stock universe (~5k live rows, 5,240 here) instead of the 23-issuer display
  subset; ``symbols`` keeps the light scan because a ticker lookup needs no
  metric;
* the default browse scan is **unchanged**: 17 columns, the four documented
  provider filters, the 100M floor and ``subtype=common``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

import pytest

from app.screen.filter_fields import (
    ROW_FIELDS,
    UNAVAILABLE,
    UnavailableFilterKey,
    binding,
    mapped_keys,
    unavailable_keys,
)
from app.screen.filters import FIELDS, apply_filters, compile_filter
from app.screen.scanner import SCAN_COLUMNS, scan_body
from app.screen.selection import SelectionError, parse_selection, select_rows
from app.screen.universe import (
    MIN_MARKET_CAP_USD,
    PARITY_COLUMNS,
    PARITY_FIELDS,
    PARITY_NULLABLE,
    UNIVERSE_COLUMNS,
    build_universe,
)
from tests import _screen as S
from tests._phase2 import MockWeb

#: Fixed clock for the executor, so two runs of one selection are comparable.
AS_OF = "2026-10-23T12:00:00.000000+00:00"

#: Rows the patched scanner answers with: the observed live US order of
#: magnitude (5,197 common / 5,720 without the gates), written as a literal so
#: the assertion does not depend on a probe.
BIG_UNIVERSE = 5_240


# ---------------------------------------------------------------------------
# Rows and executors
# ---------------------------------------------------------------------------


def provider_row(
    symbol: str,
    *,
    subtype: str = "common",
    exchange: str = "NASDAQ",
    **cells: Any,
) -> dict[str, Any]:
    """One scanner row: a fully reported US common stock plus ``cells``."""

    row: dict[str, Any] = {
        "symbol": symbol,
        "name": symbol,
        "description": f"{symbol} Holdings Inc.",
        "exchange": exchange,
        "country": "United States",
        "sector": "Technology",
        "type": "stock",
        "subtype": subtype,
        "currency": "USD",
        "close": 100.0,
        "change": 1.0,
        "volume": 1_000_000.0,
        "Value.Traded": 100_000_000.0,
        "market_cap_basic": 1_000_000_000.0,
    }
    row.update(cells)
    return row


def parity_universe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The universes a criteria selection runs over: whole market, parity columns."""

    return build_universe(
        rows,
        region="us",
        min_market_cap=0,
        columns=PARITY_COLUMNS,
        allow_any_subtype=True,
    )


def criteria(*filters: dict[str, Any]) -> dict[str, Any]:
    return {"mode": "criteria", "region": "us", "criteria": {"filters": list(filters)}}


def run(payload: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    request = parse_selection(payload)
    selection = select_rows(rows, request, region="us", as_of=AS_OF)
    return selection.as_document()


def symbols_of(document: dict[str, Any]) -> list[str]:
    return [row["symbol"] for row in document["rows"]]


# ---------------------------------------------------------------------------
# 82 keys: 37 filter a real column, 45 refuse with a reason
# ---------------------------------------------------------------------------


def test_the_disposition_is_thirty_seven_mapped_and_forty_five_refused() -> None:
    assert len(mapped_keys()) == 32
    assert len(unavailable_keys()) == 50
    assert set(mapped_keys()) == set(ROW_FIELDS)
    assert set(unavailable_keys()) == set(UNAVAILABLE)
    assert len(mapped_keys()) + len(unavailable_keys()) == 82
    assert set(ROW_FIELDS.values()) <= set(FIELDS)
    assert set(ROW_FIELDS.values()) <= set(PARITY_FIELDS.values()) | set(UNIVERSE_COLUMNS), (
        "a key must point at a column a parity row can carry"
    )


@pytest.mark.parametrize("key", list(mapped_keys()))
def test_a_mapped_key_really_filters_a_row(key: str) -> None:
    """Not "resolves to a column" — the compiled predicate actually selects."""

    field = binding(key).field
    assert field is not None
    kind = FIELDS[field]
    if kind == "text":
        rows = [{"symbol": "LOW", field: "Alpha"}, {"symbol": "HIGH", field: "Zulu"}]
        compiled = compile_filter({"field": field, "op": "eq", "value": "Alpha"})
        assert [row["symbol"] for row in apply_filters(rows, compiled)] == ["LOW"]
        return
    rows = [{"symbol": "LOW", field: 5.0}, {"symbol": "HIGH", field: 50.0}]
    compiled = compile_filter({"field": field, "op": "gt", "value": 10.0})
    assert [row["symbol"] for row in apply_filters(rows, compiled)] == ["HIGH"], (
        f"{key} -> {field} did not filter a numeric row"
    )
    assert [row["symbol"] for row in apply_filters(rows, compile_filter(
        {"field": field, "op": "between", "value": [0, 10]}
    ))] == ["LOW"]


@pytest.mark.parametrize("key", list(unavailable_keys()))
def test_a_refused_key_is_named_and_reasoned_never_ignored(key: str) -> None:
    with pytest.raises(UnavailableFilterKey) as caught:
        binding(key)
    assert key in str(caught.value)

    with pytest.raises(SelectionError) as selection:
        parse_selection(criteria({"key": key, "op": "gte", "value": 1}))
    assert selection.value.code == "unavailable_filter_key"
    assert key in str(selection.value)


# ---------------------------------------------------------------------------
# build_universe: verbatim copy, no fabrication
# ---------------------------------------------------------------------------


def test_build_universe_copies_parity_metrics_verbatim() -> None:
    row = provider_row(
        "AAA",
        price_earnings_ttm=12.5,
        RSI=63.2,
        beta_1_year=1.1,
        SMA50=101.5,
    )
    # A dotted provider column cannot be a keyword argument.
    row["Recommend.All"] = 2.1
    universe = parity_universe([row])

    assert len(universe) == 1
    copied = universe[0]
    assert copied["pe_ttm"] == 12.5
    assert copied["rsi_14"] == 63.2
    assert copied["beta_1y"] == 1.1
    assert copied["sma_50"] == 101.5
    assert copied["recommendation"] == 2.1


def test_the_light_row_does_not_grow_a_metric_it_did_not_ask_for() -> None:
    rows = [provider_row("AAA", price_earnings_ttm=12.5)]
    light = build_universe(rows, region="us")

    assert [row["symbol"] for row in light] == ["AAA"]
    assert set(light[0]) == set(UNIVERSE_COLUMNS), (
        "columns=None is the light row: no parity metric may leak into it"
    )
    assert "pe_ttm" not in light[0]


def test_every_parity_field_has_a_provider_column_and_a_kind() -> None:
    assert len(PARITY_COLUMNS) == len(set(PARITY_COLUMNS))
    assert set(SCAN_COLUMNS) <= set(PARITY_COLUMNS), (
        "one scan must still serve the browse row"
    )
    missing_fields = set(PARITY_FIELDS.values()) - set(FIELDS)
    assert not missing_fields, f"parity fields with no comparison kind: {missing_fields}"
    assert PARITY_NULLABLE <= set(PARITY_FIELDS.values())


def test_a_null_metric_is_null_never_zero() -> None:
    rows = [
        provider_row(
            "NULLS",
            price_earnings_forward=None,
            price_sales_ttm=None,
            short_float=None,
            ipo_date=None,
        ),
        provider_row(
            "KNOWN",
            price_earnings_forward=9.0,
            price_sales_ttm=2.0,
            short_float=4.0,
            ipo_date=1_600_000_000,
        ),
    ]
    universe = {row["symbol"]: row for row in parity_universe(rows)}

    for field in ("pe_forward", "ps", "short_float", "listing_date"):
        assert universe["NULLS"][field] is None, f"{field} became {universe['NULLS'][field]!r}"
        assert universe["KNOWN"][field] is not None

    for field, known in (("pe_forward", 9.0), ("ps", 2.0), ("short_float", 4.0)):
        for op, value in (("lte", 100.0), ("gte", 0.0), ("ne", 5.0), ("in", [known])):
            hits = [
                row["symbol"]
                for row in apply_filters(
                    list(universe.values()), compile_filter({"field": field, "op": op, "value": value})
                )
            ]
            assert hits == ["KNOWN"], (
                f"{field} {op} {value} matched a null row; unknown is not a value"
            )


def test_a_reported_zero_is_a_zero_and_still_filters() -> None:
    rows = [provider_row("ZERO", price_earnings_ttm=0.0), provider_row("NONE", price_earnings_ttm=None)]
    universe = {row["symbol"]: row for row in parity_universe(rows)}

    assert universe["ZERO"]["pe_ttm"] == 0.0
    assert universe["NONE"]["pe_ttm"] is None
    hits = [
        row["symbol"]
        for row in apply_filters(
            list(universe.values()),
            compile_filter({"field": "pe_ttm", "op": "lte", "value": 0}),
        )
    ]
    assert hits == ["ZERO"], "a real zero is inside '<= 0'; a missing P/E is not"


def test_a_cell_that_is_not_a_finite_number_is_unknown() -> None:
    rows = [
        provider_row("NAN", price_earnings_ttm=float("nan"), RSI=float("inf")),
        provider_row("TEXT", price_earnings_ttm="N/A", RSI="overbought"),
        provider_row("BOOL", price_earnings_ttm=True, RSI=False),
    ]
    universe = {row["symbol"]: row for row in parity_universe(rows)}

    for symbol in ("NAN", "TEXT", "BOOL"):
        assert universe[symbol]["pe_ttm"] is None, symbol
        assert universe[symbol]["rsi_14"] is None, symbol


# ---------------------------------------------------------------------------
# The filters the parity universe was built for
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "op", "value", "expected"),
    [
        ("fa_pe", "lte", 10.0, ["A"]),
        ("ta_rsi", "lt", 30.0, ["B"]),
        ("ta_beta", "gte", 1.5, ["C"]),
        ("ta_sma50", "gt", 160.0, ["B"]),
        ("fa_roe", "gte", 20.0, ["C"]),
    ],
)
def test_pe_rsi_beta_sma_and_roe_filter_the_whole_universe(
    key: str, op: str, value: float, expected: list[str]
) -> None:
    rows = [
        provider_row(
            "A",
            price_earnings_ttm=8.0,
            RSI=60.0,
            beta_1_year=0.4,
            SMA50=90.0,
            return_on_equity=5.0,
        ),
        provider_row(
            "B",
            price_earnings_ttm=15.0,
            RSI=25.0,
            beta_1_year=1.0,
            SMA50=200.0,
            return_on_equity=12.0,
        ),
        provider_row(
            "C",
            price_earnings_ttm=40.0,
            RSI=75.0,
            beta_1_year=2.0,
            SMA50=150.0,
            return_on_equity=31.0,
        ),
    ]

    document = run(criteria({"key": key, "op": op, "value": value}), parity_universe(rows))

    assert symbols_of(document) == expected
    assert document["total"] == len(expected)
    assert document["universe_total"] == 3
    assert document["filters"][0]["field"] == ROW_FIELDS[key]


def test_filters_are_anded_across_parity_metrics() -> None:
    rows = [
        provider_row("A", price_earnings_ttm=8.0, RSI=25.0, beta_1_year=2.0),
        provider_row("B", price_earnings_ttm=8.0, RSI=70.0, beta_1_year=2.0),
        provider_row("C", price_earnings_ttm=40.0, RSI=25.0, beta_1_year=2.0),
    ]
    document = run(
        criteria(
            {"key": "fa_pe", "op": "lte", "value": 10},
            {"key": "ta_rsi", "op": "lt", "value": 30},
            {"key": "ta_beta", "op": "gte", "value": 1},
        ),
        parity_universe(rows),
    )
    assert symbols_of(document) == ["A"]
    assert document["total"] == 1


# ---------------------------------------------------------------------------
# The scan a criteria selection runs, and the one it must not change
# ---------------------------------------------------------------------------


def test_the_default_scan_and_universe_are_unchanged() -> None:
    body = scan_body("us")

    assert body["filter"] == [
        {"left": "type", "operation": "equal", "right": "stock"},
        {"left": "subtype", "operation": "equal", "right": "common"},
        {"left": "market_cap_basic", "operation": "greater", "right": MIN_MARKET_CAP_USD},
        {"left": "exchange", "operation": "not_in_range", "right": ["OTC", "PNK"]},
    ], "the browse scan keeps its two gates; a selection drops them explicitly"
    assert tuple(body["columns"]) == SCAN_COLUMNS

    default = build_universe(S.scan_rows("us"), region="us")
    assert len(default) == S.US_TOTAL
    assert set(default[0]) == set(UNIVERSE_COLUMNS)


def test_none_drops_one_provider_filter_and_nothing_else() -> None:
    no_cap = scan_body("us", min_market_cap=None)
    assert [item["left"] for item in no_cap["filter"]] == ["type", "subtype", "exchange"]
    assert no_cap["markets"] == ["america"]

    no_subtype = scan_body("us", subtype=None)
    assert [item["left"] for item in no_subtype["filter"]] == ["type", "market_cap_basic", "exchange"]

    neither = scan_body("us", min_market_cap=None, subtype=None)
    assert [item["left"] for item in neither["filter"]] == ["type", "exchange"], (
        "type=stock and the OTC exclusion are not negotiable"
    )

    preferred = scan_body("us", subtype="preferred")
    assert preferred["filter"][1] == {"left": "subtype", "operation": "equal", "right": "preferred"}


def test_zero_floor_and_any_subtype_widen_the_universe_without_widening_the_instruments() -> None:
    rows = [
        provider_row("BIG"),
        provider_row("NANO", market_cap_basic=30_000_000.0),
        provider_row("PREF", subtype="preferred"),
        provider_row("FUND", subtype="etf", type="fund"),
        provider_row("NO_CAP", market_cap_basic=None),
    ]

    assert [row["symbol"] for row in build_universe(rows, region="us")] == ["BIG"]
    wide = parity_universe(rows)
    assert [row["symbol"] for row in wide] == ["BIG", "NANO", "PREF"], (
        "a criteria selection sees nano caps and every stock subtype, but not a fund "
        "and not a row with no market cap"
    )


def _scan_responder(
    rows: list[dict[str, Any]], seen: list[dict[str, Any]]
) -> Callable[[Any], dict[str, Any]]:
    """Answer the scanner with ``rows``, echoing the columns the client asked for."""

    def respond(request: Any) -> dict[str, Any]:
        body = json.loads((request.content or b"{}").decode("utf-8"))
        seen.append(body)
        columns = body["columns"]
        data = [
            {
                "s": f"{row['exchange']}:{row['symbol']}",
                "d": [row.get(column) for column in columns],
            }
            for row in rows
        ]
        return {"totalCount": len(data), "data": data}

    return respond


def big_rows(count: int = BIG_UNIVERSE) -> list[dict[str, Any]]:
    """A universe the size of the live one; every 10th row is a preferred share."""

    rows: list[dict[str, Any]] = []
    for index in range(count):
        rows.append(
            provider_row(
                f"S{index:05d}",
                subtype="common" if index % 10 else "preferred",
                market_cap_basic=1_000_000_000.0 + index * 1_000_000.0,
                price_earnings_ttm=5.0 + (index % 40),
                RSI=float(index % 100),
                beta_1_year=0.5 + (index % 20) / 10.0,
                SMA50=100.0 + (index % 50),
            )
        )
    return rows


async def test_a_criteria_selection_runs_over_five_thousand_parity_rows(
    tmp_path: Path, monkeypatch: Any
) -> None:
    rows = big_rows()
    seen: list[dict[str, Any]] = []
    web = MockWeb()
    web.json("tradingview", _scan_responder(rows, seen))

    async with S.screen_client(tmp_path, monkeypatch, web=web) as harness:
        everything = await harness.client.post(
            "/screens/select", json={"mode": "criteria", "region": "us", "criteria": {}}
        )
        assert everything.status_code == 200, everything.text
        whole = everything.json()

        cheap = await harness.client.post(
            "/screens/select",
            json=criteria({"key": "fa_pe", "op": "lte", "value": 10}),
        )
        assert cheap.status_code == 200, cheap.text
        filtered = cheap.json()

    assert whole["universe_total"] == BIG_UNIVERSE > 5_000, (
        "a criteria selection must answer over the whole market, not the display subset"
    )
    assert whole["total"] == BIG_UNIVERSE, (
        "no cap floor and no subtype gate: nano caps and preferred shares survive"
    )
    assert filtered["universe_total"] == BIG_UNIVERSE
    # 5.0 + (i % 40) <= 10 holds for six of every forty rows.
    assert filtered["total"] == BIG_UNIVERSE // 40 * 6 == 786
    assert filtered["returned"] == S.DISPLAY_ROWS
    assert filtered["filters"][0]["field"] == "pe_ttm"

    body = seen[0]
    assert list(body["columns"]) == list(PARITY_COLUMNS), (
        "the criteria scan must ask for the metric columns the filters address"
    )
    assert [item["left"] for item in body["filter"]] == ["type", "exchange"], (
        "the selection scan must not carry the browse floor or the subtype gate"
    )


async def test_symbols_mode_still_scans_the_light_columns(
    tmp_path: Path, monkeypatch: Any
) -> None:
    seen: list[dict[str, Any]] = []
    web = MockWeb()
    web.json("tradingview", _scan_responder(S.scan_rows("us"), seen))

    async with S.screen_client(tmp_path, monkeypatch, web=web) as harness:
        page = await harness.create_screen()
        answer = await harness.client.post(
            "/screens/select",
            json={
                "mode": "symbols",
                "symbols": ["AAPL", "NVDA"],
                "source": "tradingview",
                "snapshot_id": page["screen_id"],
            },
        )

    assert answer.status_code == 200, answer.text
    document = answer.json()
    assert symbols_of(document) == ["AAPL", "NVDA"]
    assert len(seen) == 2, "one scan for the browse snapshot, one for the lookup"
    assert list(seen[-1]["columns"]) == list(SCAN_COLUMNS), (
        "a ticker lookup is a light read: 17 columns, not the parity block"
    )
    assert [item["left"] for item in seen[-1]["filter"]][1:2] == ["subtype"]
