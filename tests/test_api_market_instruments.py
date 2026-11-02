"""Contract for ``GET /market/instruments`` and ``/market/instruments/meta``.

What is pinned here, in the order the plan argues it:

* **one row per manifest entry, in manifest order** — a manifest entry is a
  promise to *show* an instrument, so an entry the scan cannot price is still a
  row, with ``null`` cells and a reason;
* **every computable signal is read off the scan row** — the same
  :func:`app.screen.universe.build_universe` the selector calls, the same
  :func:`app.screen.depth_overlay.overlay`, and **one** scan whose column list
  asks for ``beta_1_year``. There is no second computation path: the test spies
  on both readers and compares the served numbers to the provider's cells;
* **what cannot be computed is named, never invented** — greeks, option expiry
  and days-to-maturity are ``null`` with ``no option-chain source yet``, and
  IBKR is ``null``/``broker integration not configured``;
* **an unknown id is a 422** ``unknown_instrument``, decided before any scan;
* **meta reports the manifest's own state** — path, version, stamp, count, the
  labels the master does not carry and the ones it cannot price;
* **a missing manifest is an empty table** (and costs no scan), while a broken
  one is a 422 ``invalid_manifest``: an empty answer for a broken file would
  look exactly like an owner who removed every instrument.

The suite is hermetic: the scanner is a ``MockWeb`` over a handwritten body and
the manifest is a real JSON file in ``tmp_path``.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from tests import _screen as S

#: The computable signals the plan names. It says "the 16 signals above" and
#: then lists fifteen; these are the fifteen, spelled here as the oracle (not
#: imported from the route) in the plan's order.
_SIGNALS: tuple[str, ...] = (
    "price",
    "change_pct",
    "volume",
    "beta_1y",
    "market_cap_usd",
    "dividend_yield",
    "recommendation",
    "pe_ttm",
    "pe_forward",
    "rsi_14",
    "sma_50",
    "sma_200",
    "ret_ytd",
    "ret_1w",
    "ret_1m",
)

#: The three option cells no source can fill yet, plus the broker capability.
_OPTION_SIGNALS: tuple[str, ...] = ("greeks", "expiry", "days_to_maturity")

_DOCUMENT_KEYS = frozenset(
    {"as_of", "manifest", "region", "scan", "universe_total", "signals", "unavailable", "rows"}
)
_ROW_KEYS = frozenset(
    {"id", "symbol", "asset", "contract", "priced", "reason", *_SIGNALS, *_OPTION_SIGNALS}
)
_META_KEYS = frozenset(
    {
        "path",
        "present",
        "version",
        "updated_at",
        "count",
        "legacy",
        "master",
        "not_in_master",
        "no_price",
    }
)
_UNAVAILABLE_REASONS = {
    "greeks": "no option-chain source yet",
    "expiry": "no option-chain source yet",
    "days_to_maturity": "no option-chain source yet",
    "ibkr": "broker integration not configured",
}


# ---------------------------------------------------------------------------
# The provider's rows, and the manifest that names them
# ---------------------------------------------------------------------------


def _prow(symbol: str, **cells: Any) -> dict[str, Any]:
    """One provider row in the provider's own column names.

    The parity cells are deliberately distinctive so a test can prove each
    served number came from *this* row rather than from a formula or a default.
    """

    row: dict[str, Any] = {
        "name": symbol,
        "description": f"{symbol} Incorporated",
        "exchange": "NASDAQ",
        "country": "United States",
        "sector": "Technology",
        "type": "stock",
        "subtype": "common",
        "currency": "USD",
        "close": 190.5,
        "change": 1.25,
        "volume": 55_000_000.0,
        "Value.Traded": 10_000_000_000.0,
        "market_cap_basic": 2_900_000_000_000.0,
        "beta_1_year": 1.28,
        "price_earnings_ttm": 31.2,
        "price_earnings_forward": 29.1,
        "dividends_yield": 0.44,
        "Recommend.All": 0.12,
        "RSI": 58.4,
        "SMA50": 188.1,
        "SMA200": 180.2,
        "Perf.YTD": 12.3,
        "Perf.W": 1.1,
        "Perf.1M": 2.2,
    }
    row.update(cells)
    return row


#: Three provider rows: two priceable, one whose ``close`` the provider did not
#: send. ``ZZZZ`` is in the manifest and in no row — the unpriceable case.
_ROWS: tuple[dict[str, Any], ...] = (
    _prow("AAPL"),
    _prow(
        "MSFT",
        description="Microsoft Corporation",
        close=410.0,
        change=-0.75,
        volume=22_000_000.0,
        market_cap_basic=3_050_000_000_000.0,
        beta_1_year=0.91,
        price_earnings_ttm=35.4,
        price_earnings_forward=31.7,
        dividends_yield=0.72,
        **{"Recommend.All": 0.31, "RSI": 47.2, "SMA50": 415.0, "SMA200": 400.0},
        **{"Perf.YTD": 9.4, "Perf.W": -0.6, "Perf.1M": 1.8},
    ),
    _prow(
        "NOPRICE",
        description="No Price Incorporated",
        close=None,
        change=None,
        volume=None,
        market_cap_basic=5_000_000_000.0,
        beta_1_year=None,
        price_earnings_ttm=None,
    ),
)

_MANIFEST: dict[str, Any] = {
    "version": 1,
    "updated_at": "2026-11-02T00:00:00Z",
    "instruments": [
        {"id": "id1", "symbol": "AAPL", "asset": "stock"},
        {"id": "id2", "symbol": "MSFT", "asset": "stock"},
        {"id": "id3", "symbol": "NOPRICE", "asset": "stock"},
        {"id": "id4", "symbol": "ZZZZ", "asset": "stock"},
        {
            "id": "id5",
            "symbol": "AAPL",
            "asset": "option",
            "expiry": "2026-01-16",
            "strike": 200,
            "right": "C",
        },
    ],
}


# ---------------------------------------------------------------------------
# Harness: the real app, a mocked scanner, a manifest in tmp_path
# ---------------------------------------------------------------------------


@dataclass
class Harness:
    client: Any
    app: Any
    web: Any
    manifest_path: Path
    columns_seen: list[tuple[str, ...]]

    async def get(self, path: str, **params: Any) -> Any:
        return await self.client.get(path, params=params or None)


@asynccontextmanager
async def _client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    manifest: Mapping[str, Any] | None = None,
    missing_manifest: bool = False,
    scan_status: int | None = None,
) -> AsyncIterator[Harness]:
    """An ASGI client over the real app, the mocked scanner and a manifest file."""

    monkeypatch.setenv("MEE_DB_PATH", str(tmp_path / "mee.db"))
    monkeypatch.setenv("MEE_EXPORT_DIR", str(tmp_path / "exports"))
    monkeypatch.setenv("MEE_PROVIDER_MODE", "live")
    monkeypatch.delenv("MEE_DEPTH", raising=False)

    manifest_path = tmp_path / "coinid.json"
    monkeypatch.setenv("MEE_COINIDS_PATH", str(manifest_path))
    if not missing_manifest:
        manifest_path.write_text(
            json.dumps(_MANIFEST if manifest is None else manifest), encoding="utf-8"
        )

    columns_seen: list[tuple[str, ...]] = []

    def respond(request: Any) -> dict[str, Any]:
        body = json.loads(request.content)
        columns = tuple(body["columns"])
        columns_seen.append(columns)
        return {
            "totalCount": len(_ROWS),
            "data": [
                {"s": f"NASDAQ:{row['name']}", "d": [row.get(column) for column in columns]}
                for row in _ROWS
            ],
        }

    web = S.MockWeb()
    if scan_status is None:
        web.json("tradingview", respond)
    else:
        web.status("tradingview", scan_status)
    S.install_web(monkeypatch, web)

    from httpx2 import ASGITransport, AsyncClient

    from app.api.screens import ScanCache
    from app.main import create_app

    application = create_app()
    application.state.scan_cache = ScanCache(ttl_s=60.0)

    transport = ASGITransport(app=application, raise_app_exceptions=False)
    async with AsyncClient(
        transport=transport,
        base_url="http://mee.test",
        headers={"x-api-key": "instruments-key"},
    ) as client:
        yield Harness(
            client=client,
            app=application,
            web=web,
            manifest_path=manifest_path,
            columns_seen=columns_seen,
        )


def _rows_by_id(body: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {str(row["id"]): row for row in body["rows"]}


# ---------------------------------------------------------------------------
# The table
# ---------------------------------------------------------------------------


async def test_instruments_table_answers_one_row_per_manifest_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _client(tmp_path, monkeypatch) as harness:
        response = await harness.get("/market/instruments")

    assert response.status_code == 200, response.text
    body = response.json()

    assert set(body) == _DOCUMENT_KEYS
    assert body["region"] == "us"
    assert [row["symbol"] for row in body["rows"]] == [
        "AAPL",
        "MSFT",
        "NOPRICE",
        "ZZZZ",
        "AAPL",
    ]
    assert [row["id"] for row in body["rows"]] == ["id1", "id2", "id3", "id4", "id5"]
    assert body["manifest"]["count"] == 5
    assert body["manifest"]["present"] is True
    assert body["manifest"]["version"] == 1
    assert body["manifest"]["updated_at"] == "2026-11-02T00:00:00Z"

    for row in body["rows"]:
        assert set(row) == _ROW_KEYS, row

    # The computable cells are the scan row's own numbers, one for one.
    aapl = _rows_by_id(body)["id1"]
    assert aapl["asset"] == "stock"
    assert aapl["priced"] is True
    assert aapl["reason"] is None
    assert aapl["contract"] is None
    assert aapl["price"] == 190.5
    assert aapl["change_pct"] == 1.25
    assert aapl["volume"] == 55_000_000.0
    assert aapl["beta_1y"] == 1.28
    assert aapl["market_cap_usd"] == 2_900_000_000_000.0
    assert aapl["dividend_yield"] == 0.44
    assert aapl["recommendation"] == 0.12
    assert aapl["pe_ttm"] == 31.2
    assert aapl["pe_forward"] == 29.1
    assert aapl["rsi_14"] == 58.4
    assert aapl["sma_50"] == 188.1
    assert aapl["sma_200"] == 180.2
    assert aapl["ret_ytd"] == 12.3
    assert aapl["ret_1w"] == 1.1
    assert aapl["ret_1m"] == 2.2

    # Nothing that needs an option chain or a broker is invented.
    assert body["signals"] == list(_SIGNALS)
    reasons = {item["signal"]: item["reason"] for item in body["unavailable"]}
    assert reasons == _UNAVAILABLE_REASONS
    for row in body["rows"]:
        for key in _OPTION_SIGNALS:
            assert row[key] is None, (row["id"], key)


async def test_option_row_is_a_declared_contract_without_a_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The manifest owns the contract terms; the *market* cells stay null."""

    async with _client(tmp_path, monkeypatch) as harness:
        body = (await harness.get("/market/instruments", ids="id5")).json()

    assert len(body["rows"]) == 1
    option = body["rows"][0]
    assert option["asset"] == "option"
    assert option["contract"] == {"expiry": "2026-01-16", "strike": 200.0, "right": "C"}
    assert option["priced"] is False
    assert option["reason"] == "no option-chain source yet"
    assert option["price"] is None
    assert option["expiry"] is None


# ---------------------------------------------------------------------------
# Narrowing, and the refusals that cost no scan
# ---------------------------------------------------------------------------


async def test_unknown_id_is_422(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async with _client(tmp_path, monkeypatch) as harness:
        response = await harness.get("/market/instruments", ids="id1,id9")
        scans = harness.web.count("tradingview")

    assert response.status_code == 422
    body = response.json()
    assert body["error"] == "unknown_instrument"
    assert "id9" in body["errorMessage"]
    # Refused before the expensive half: a bad id must not buy a provider scan.
    assert scans == 0


async def test_ids_filter_narrows_in_manifest_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _client(tmp_path, monkeypatch) as harness:
        response = await harness.get("/market/instruments", ids="id2,id1,id2")

    assert response.status_code == 200, response.text
    assert [row["id"] for row in response.json()["rows"]] == ["id1", "id2"]


async def test_invalid_manifest_is_422(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    broken = {
        "instruments": [
            {"id": "id1", "symbol": "AAPL"},
            {"id": "id2", "symbol": "aapl"},
        ]
    }
    async with _client(tmp_path, monkeypatch, manifest=broken) as harness:
        response = await harness.get("/market/instruments")
        scans = harness.web.count("tradingview")

    assert response.status_code == 422
    assert response.json()["error"] == "invalid_manifest"
    assert "duplicate" in response.json()["errorMessage"].lower()
    assert scans == 0


async def test_legacy_flat_manifest_is_served_as_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The owner's own ``{"id1": "AAPL"}`` document is the route's input."""

    async with _client(
        tmp_path, monkeypatch, manifest={"id1": "AAPL", "id2": "MSFT"}
    ) as harness:
        body = (await harness.get("/market/instruments")).json()
        meta = (await harness.get("/market/instruments/meta")).json()

    assert body["manifest"]["legacy"] is True
    assert [row["id"] for row in body["rows"]] == ["id1", "id2"]
    assert [row["symbol"] for row in body["rows"]] == ["AAPL", "MSFT"]
    assert [row["priced"] for row in body["rows"]] == [True, True]
    assert meta["not_in_master"] == []
    assert meta["no_price"] == []


async def test_an_entry_without_id_is_labelled_by_its_symbol(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``id`` is optional; meta still names the row, and ``ids=`` cannot address it."""

    manifest = {"instruments": [{"symbol": "ZZZZ"}]}
    async with _client(tmp_path, monkeypatch, manifest=manifest) as harness:
        body = (await harness.get("/market/instruments")).json()
        meta = (await harness.get("/market/instruments/meta")).json()
        unknown = await harness.get("/market/instruments", ids="ZZZZ")

    assert body["rows"][0]["id"] is None
    assert body["rows"][0]["symbol"] == "ZZZZ"
    assert meta["not_in_master"] == ["ZZZZ"]
    assert unknown.status_code == 422
    assert unknown.json()["error"] == "unknown_instrument"


# ---------------------------------------------------------------------------
# Honest nulls
# ---------------------------------------------------------------------------


async def test_unpriceable_symbol_is_null_with_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _client(tmp_path, monkeypatch) as harness:
        body = (await harness.get("/market/instruments")).json()

    rows = _rows_by_id(body)

    absent = rows["id4"]
    assert absent["symbol"] == "ZZZZ"
    assert absent["priced"] is False
    assert absent["reason"] == "not in the scanned universe"
    for key in _SIGNALS:
        assert absent[key] is None, key

    unpriced = rows["id3"]
    assert unpriced["symbol"] == "NOPRICE"
    assert unpriced["priced"] is False
    assert "no price" in unpriced["reason"]
    for key in _SIGNALS:
        assert unpriced[key] is None, key

    # The priced rows are unaffected by their unpriceable neighbours.
    assert rows["id2"]["priced"] is True
    assert rows["id2"]["price"] == 410.0


async def test_missing_manifest_is_empty_and_costs_no_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _client(tmp_path, monkeypatch, missing_manifest=True) as harness:
        table = (await harness.get("/market/instruments")).json()
        meta = (await harness.get("/market/instruments/meta")).json()
        scans = harness.web.count("tradingview")

    assert table["rows"] == []
    assert table["manifest"]["present"] is False
    assert table["manifest"]["count"] == 0
    assert table["scan"] == "skipped"
    assert meta["count"] == 0
    assert meta["present"] is False
    assert scans == 0


async def test_scanner_failure_is_503(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async with _client(tmp_path, monkeypatch, scan_status=500) as harness:
        response = await harness.get("/market/instruments")

    assert response.status_code == 503
    assert response.json()["error"] == "scanner_unavailable"


# ---------------------------------------------------------------------------
# Reuse: one scan, the selector's readers, no second computation path
# ---------------------------------------------------------------------------


async def test_instruments_reuses_the_scan_row_for_price_and_beta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Price and beta are the scan's cells, read through the selector's readers."""

    async with _client(tmp_path, monkeypatch) as harness:
        from app.api import instruments as instruments_api

        depth = {"AAPL": {"price": 1.0, "beta_1y": 9.9}}
        monkeypatch.setattr(instruments_api, "current_depth", lambda: _snapshot(depth))

        calls = {"universe": [], "overlay": []}
        real_universe = instruments_api.build_universe
        real_overlay = instruments_api.overlay

        def spy_universe(rows: Any, **kwargs: Any) -> Any:
            built = real_universe(rows, **kwargs)
            calls["universe"].append((list(rows), kwargs, built))
            return built

        def spy_overlay(rows: Any, symbols: Any, **kwargs: Any) -> Any:
            calls["overlay"].append((list(rows), dict(symbols), kwargs))
            return real_overlay(rows, symbols, **kwargs)

        monkeypatch.setattr(instruments_api, "build_universe", spy_universe)
        monkeypatch.setattr(instruments_api, "overlay", spy_overlay)

        response = await harness.get("/market/instruments", ids="id1")

    assert response.status_code == 200, response.text
    row = response.json()["rows"][0]

    # The served numbers are the provider's cells, not a default and not depth's.
    assert row["price"] == 190.5
    assert row["beta_1y"] == 1.28

    # One universe build over the one scan page, one depth overlay over it.
    assert len(calls["universe"]) == 1
    from app.screen.universe import PARITY_COLUMNS

    kwargs = calls["universe"][0][1]
    assert kwargs["region"] == "us"
    assert kwargs["min_market_cap"] == 0
    assert kwargs["allow_any_subtype"] is True
    assert kwargs["asset_class"] == "stock"
    assert tuple(kwargs["columns"]) == PARITY_COLUMNS
    assert [built["symbol"] for built in calls["universe"][0][2]] == ["AAPL", "MSFT", "NOPRICE"]

    assert len(calls["overlay"]) == 1
    assert set(calls["overlay"][0][1]) == {"AAPL"}
    assert calls["overlay"][0][2]["default_ticker_key"] == "symbol"
    assert calls["overlay"][0][2]["aliases"] is not None

    # One provider request, asked for the parity columns — beta included.
    assert harness.web.count("tradingview") == 1
    assert len(harness.columns_seen) == 1
    assert "beta_1_year" in harness.columns_seen[0]
    assert "price_earnings_forward" in harness.columns_seen[0]


def _snapshot(symbols: Mapping[str, Mapping[str, Any]]) -> Any:
    from app.screen.depth_source import DepthSnapshot

    return DepthSnapshot(symbols=dict(symbols))


# ---------------------------------------------------------------------------
# The published contract
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parents[1]


def test_the_schema_artifact_is_the_published_document() -> None:
    """One document, two homes: the module and ``schemas/`` may not drift."""

    from app.api.instruments import INSTRUMENTS_RESPONSE_SCHEMA

    artifact = ROOT / "schemas" / "instruments_response_v1.json"
    assert artifact.exists(), "the table's contract must ship at schemas/instruments_response_v1.json"
    assert json.loads(artifact.read_text(encoding="utf-8")) == INSTRUMENTS_RESPONSE_SCHEMA


async def test_the_table_shape_is_the_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every key the schema promises is present, and no key it forbids is."""

    from app.api.instruments import INSTRUMENTS_RESPONSE_SCHEMA

    async with _client(tmp_path, monkeypatch) as harness:
        body = (await harness.get("/market/instruments")).json()

    schema = INSTRUMENTS_RESPONSE_SCHEMA
    assert set(body) == set(schema["properties"])
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])

    row_schema = schema["$defs"]["row"]
    for row in body["rows"]:
        assert set(row) == set(row_schema["properties"]), row["id"]
        assert set(row_schema["required"]) == set(row_schema["properties"])


# ---------------------------------------------------------------------------
# Meta
# ---------------------------------------------------------------------------


async def test_instruments_meta_reports_manifest_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _client(tmp_path, monkeypatch) as harness:
        response = await harness.get("/market/instruments/meta")

    assert response.status_code == 200, response.text
    body = response.json()

    assert set(body) == _META_KEYS
    assert body["path"] == str(tmp_path / "coinid.json")
    assert body["present"] is True
    assert body["version"] == 1
    assert body["updated_at"] == "2026-11-02T00:00:00Z"
    assert body["count"] == 5
    assert body["legacy"] is False
    assert body["master"] == {
        "source": "scan",
        "universe_total": 3,
        "scan": "miss",
        "reason": None,
    }
    assert body["not_in_master"] == ["id4"]
    assert body["no_price"] == ["id3"]


async def test_meta_and_table_share_one_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _client(tmp_path, monkeypatch) as harness:
        table = await harness.get("/market/instruments")
        meta = await harness.get("/market/instruments/meta")
        scans = harness.web.count("tradingview")

    assert table.status_code == 200
    assert meta.status_code == 200
    assert scans == 1, "the manifest routes must share the selector's scan cache"


async def test_meta_degrades_when_the_scanner_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The manifest's own state is still an answer when the scanner is down."""

    async with _client(tmp_path, monkeypatch, scan_status=500) as harness:
        response = await harness.get("/market/instruments/meta")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["count"] == 5
    assert body["present"] is True
    assert body["master"]["scan"] == "unavailable"
    assert body["master"]["universe_total"] is None
    assert body["master"]["reason"]
    assert body["not_in_master"] is None
    assert body["no_price"] is None
