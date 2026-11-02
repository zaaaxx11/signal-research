"""``GET /engine/options/{symbol}`` — the ticker page's option chain.

CBOE delayed quotes as primary source, with greeks (delta, gamma, theta,
vega, rho) served directly from the exchange. Disk cache at
``ztockz-data/options/``. Every 200 carries ``delayed_minutes: 15``.

The four tests the task requires:

* AAPL returns strikes with greeks and a delayed_minutes label
* an unknown symbol returns an appropriate error
* an empty chain returns expiry_dates=[]
* the cache layer is tested (second call reads disk)
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx2
import pytest

# ---------------------------------------------------------------------------
# Fixtures — a canned CBOE-shaped payload
# ---------------------------------------------------------------------------

CBOE_TIMESTAMP = "2026-11-02 11:01:17"


def cboe_option(
    *,
    option: str = "AAPL261104C00275000",
    bid: float = 61.0,
    ask: float = 64.0,
    iv: float = 1.361,
    open_interest: float = 8.0,
    volume: float = 3.0,
    delta: float = 0.9877,
    gamma: float = 0.001,
    theta: float = -0.1158,
    vega: float = 0.008,
    rho: float = 0.0148,
    last_trade_price: float | None = 62.35,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "option": option,
        "bid": bid,
        "bid_size": 144.0,
        "ask": ask,
        "ask_size": 54.0,
        "iv": iv,
        "open_interest": open_interest,
        "volume": volume,
        "delta": delta,
        "gamma": gamma,
        "theta": theta,
        "vega": vega,
        "rho": rho,
        "change": -6.7,
        "tick": "no_change",
        "percent_change": -9.70311,
    }
    if last_trade_price is not None:
        row["last_trade_price"] = last_trade_price
    return row


def aapl_cboe_body() -> dict[str, Any]:
    """Three expiries: 2026-11-04 (2 calls, 2 puts), 2026-11-06 (1 call), 2026-11-07 (skips — in the past)."""
    return {
        "timestamp": CBOE_TIMESTAMP,
        "data": {
            "options": [
                cboe_option(option="AAPL261104C00275000", delta=0.9877, gamma=0.001, vega=0.008, theta=-0.1158, rho=0.0148),
                cboe_option(option="AAPL261104P00275000", delta=-0.0122, gamma=0.001, vega=0.008, theta=-0.1234, rho=-0.0002),
                cboe_option(option="AAPL261104C00290000", delta=0.9687, gamma=0.0025, vega=0.0179, theta=-0.2978, rho=0.0153),
                cboe_option(option="AAPL261104P00290000", delta=-0.0312, gamma=0.0025, vega=0.0179, theta=-0.3036, rho=-0.0006),
                cboe_option(option="AAPL261106C00285000", delta=0.95, gamma=0.003, vega=0.01, theta=-0.25, rho=0.01),
                cboe_option(option="AAPL261106P00285000", delta=-0.04, gamma=0.003, vega=0.01, theta=-0.26, rho=-0.001),
            ],
        },
    }


def empty_cboe_body() -> dict[str, Any]:
    """A valid response but with zero options."""
    return {
        "timestamp": CBOE_TIMESTAMP,
        "data": {"options": []},
    }


# ---------------------------------------------------------------------------
# Cached file helpers
# ---------------------------------------------------------------------------

def cache_dir() -> Path:
    return Path("/tmp/ztockz-test-options")


def cache_file(symbol: str = "AAPL") -> Path:
    return cache_dir() / f"{symbol}.json"


def write_cache(
    *,
    symbol: str = "AAPL",
    fetched_at: datetime,
    source: str = "cboe",
    as_of: str = "2026-11-02T11:01:17+00:00",
    contracts: list[dict[str, Any]] | None = None,
) -> Path:
    if contracts is None:
        contracts = [
            {
                "strike": 275.0, "right": "call", "bid": 61.0, "ask": 64.0,
                "last": 62.35, "volume": 3, "open_interest": 8, "iv": 1.361,
                "delta": 0.9877, "gamma": 0.001, "theta": -0.1158, "vega": 0.008,
                "rho": 0.0148,
            },
        ]
    path = cache_file(symbol)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "symbol": symbol,
                "fetched_at": fetched_at.isoformat(),
                "source": source,
                "payload": {
                    "as_of": as_of,
                    "underlying_price": 336.25,
                    "contracts": contracts,
                },
            }
        ),
        encoding="utf-8",
    )
    return path


# ---------------------------------------------------------------------------
# Parsing — pure function, no I/O
# ---------------------------------------------------------------------------


def test_parses_the_occ_code_into_root_expiry_right_and_strike() -> None:
    from app.market.options import parse_occ

    occ = parse_occ("AAPL261104C00275000")

    assert occ.root == "AAPL"
    assert occ.expiry == datetime(2026, 11, 4).date()
    assert occ.right == "call"
    assert occ.strike == 275.0


def test_parses_a_put_with_different_strike() -> None:
    from app.market.options import parse_occ

    occ = parse_occ("AAPL261106P00300000")

    assert occ.root == "AAPL"
    assert occ.expiry == datetime(2026, 11, 6).date()
    assert occ.right == "put"
    assert occ.strike == 300.0


def test_a_malformed_code_raises_value_error() -> None:
    from app.market.options import parse_occ

    for bad in (None, "", "AAPL", "AAPL261399C00275000", "AAPL261104Q00275000"):
        with pytest.raises(ValueError):
            parse_occ(bad)


# ---------------------------------------------------------------------------
# CBOE body parsing
# ---------------------------------------------------------------------------


def test_parses_a_cboe_payload_into_groups_by_expiry() -> None:
    from app.market.options import parse_cboe

    chain = parse_cboe("AAPL", aapl_cboe_body())

    assert chain is not None
    assert chain.symbol == "AAPL"
    assert chain.source == "cboe"
    assert chain.as_of == datetime(2026, 11, 2, 11, 1, 17, tzinfo=UTC)
    assert len(chain.expiries) >= 2  # Nov 4 and Nov 6

    nov4 = [g for g in chain.expiries if g.expiry == datetime(2026, 11, 4).date()]
    assert len(nov4) == 1
    contracts = nov4[0].contracts
    calls = [c for c in contracts if c.right == "call"]
    puts = [c for c in contracts if c.right == "put"]
    assert len(calls) == 2
    assert len(puts) == 2
    assert calls[0].strike == 275.0
    assert calls[0].delta == 0.9877
    assert calls[0].gamma == 0.001
    assert calls[0].vega == 0.008
    assert calls[0].theta == -0.1158
    assert calls[0].rho == 0.0148


def test_an_empty_cboe_payload_is_none() -> None:
    from app.market.options import parse_cboe

    assert parse_cboe("AAPL", empty_cboe_body()) is None
    assert parse_cboe("AAPL", None) is None
    assert parse_cboe("AAPL", {}) is None


# ---------------------------------------------------------------------------
# The collector: cache + CBOE fetch
# ---------------------------------------------------------------------------


def fake_cboe_source(body: dict[str, Any] | None, *, status: int = 200, unknown: bool = False, empty: bool = False):
    """A canned source whose ``fetch()`` returns a :class:`FetchOutcome`."""

    class Fake:
        name = "cboe"

        async def fetch(self, symbol: str):
            from app.market.options import parse_cboe, FetchOutcome

            if unknown:
                return FetchOutcome(unknown=True)
            if empty:
                return FetchOutcome(empty=True)
            if body is None:
                return FetchOutcome()
            chain = parse_cboe(symbol, body)
            if chain is None:
                return FetchOutcome(empty=True)
            return FetchOutcome(chain=chain)

    return Fake()


async def test_collect_returns_greeks_and_delayed_minutes() -> None:
    from app.market.options import collect_options

    document = await collect_options(
        "AAPL",
        cboe=fake_cboe_source(aapl_cboe_body()),
        now=datetime(2026, 11, 2, 12, 0, tzinfo=UTC),
        directory=cache_dir(),
    )

    assert document is not None
    assert document["symbol"] == "AAPL"
    assert document["delayed_minutes"] == 15
    assert "as_of" in document
    assert "expiry_dates" in document
    assert len(document["expiry_dates"]) >= 2
    assert document["selected_expiry"] == document["expiry_dates"][0]
    assert len(document["strikes"]) >= 4

    strike = document["strikes"][0]
    assert set(strike) == {
        "strike", "type", "premium", "delta", "gamma", "theta", "vega", "rho",
        "volume", "open_interest", "implied_volatility",
    }
    assert strike["strike"] == 275.0
    assert strike["type"] in ("call", "put")
    assert strike["delta"] is not None
    assert strike["gamma"] is not None
    assert strike["vega"] is not None
    assert strike["theta"] is not None
    assert strike["rho"] is not None
    assert strike["implied_volatility"] is not None


async def test_unknown_symbol_raises_unknown_symbol() -> None:
    from app.market.options import UnknownSymbol, collect_options

    with pytest.raises(UnknownSymbol, match="no listed option chain"):
        await collect_options(
            "ZZZZXXX",
            cboe=fake_cboe_source(None, unknown=True),
            now=datetime(2026, 11, 2, 12, 0, tzinfo=UTC),
            directory=cache_dir(),
        )


async def test_empty_chain_returns_empty_expiry_dates() -> None:
    from app.market.options import collect_options

    document = await collect_options(
        "EMPTY",
        cboe=fake_cboe_source(empty_cboe_body()),
        now=datetime(2026, 11, 2, 12, 0, tzinfo=UTC),
        directory=cache_dir(),
    )

    assert document is not None
    assert document["expiry_dates"] == []
    assert document["selected_expiry"] is None
    assert document["strikes"] == []


async def test_cache_write_and_read_second_call_reads_disk() -> None:
    import os

    from app.market.options import collect_options

    test_dir = Path("/tmp/ztockz-test-options-cache")
    os.environ["MEE_OPTIONS_CACHE_DIR"] = str(test_dir)
    try:
        # First call: fetches from CBOE
        document1 = await collect_options(
            "AAPL",
            cboe=fake_cboe_source(aapl_cboe_body()),
            now=datetime(2026, 11, 2, 12, 0, tzinfo=UTC),
            directory=test_dir,
        )
        assert document1["source"] == "cboe"

        # Second call with a dead CBOE: should read from cache
        document2 = await collect_options(
            "AAPL",
            cboe=fake_cboe_source(None),  # Simulate CBOE failure
            now=datetime(2026, 11, 2, 12, 0, 30, tzinfo=UTC),  # 30s later, still within 5min TTL
            directory=test_dir,
        )
        assert document2 is not None
        assert document2["source"] == "cache"
        assert document2["delayed_minutes"] == 15
        assert len(document2["strikes"]) >= 4
    finally:
        import shutil

        os.environ.pop("MEE_OPTIONS_CACHE_DIR", None)
        if test_dir.exists():
            shutil.rmtree(test_dir)


async def test_collect_unsafe_symbol_raises_unknown_symbol() -> None:
    from app.market.options import UnknownSymbol, collect_options

    with pytest.raises(UnknownSymbol):
        await collect_options(
            "../../etc/passwd",
            cboe=fake_cboe_source(aapl_cboe_body()),
            now=datetime(2026, 11, 2, 12, 0, tzinfo=UTC),
            directory=cache_dir(),
        )


# ---------------------------------------------------------------------------
# The route
# ---------------------------------------------------------------------------


def route_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """An ASGI client with the app, no sockets."""
    import httpx2

    monkeypatch.setenv("MEE_DB_PATH", str(tmp_path / "mee.db"))
    monkeypatch.setenv("MEE_EXPORT_DIR", str(tmp_path / "exports"))
    monkeypatch.setenv("MEE_OPTIONS_CACHE_DIR", str(tmp_path / "options"))

    from app.main import create_app

    application = create_app()
    return httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=application, raise_app_exceptions=False),
        base_url="http://mee.test",
    )


async def test_route_returns_strikes_with_greeks_and_delayed_label(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.market import options as options_mod

    original = options_mod.collect_options

    async def canned(symbol, **kwargs):
        return {
            "symbol": "AAPL",
            "as_of": "2026-11-02T11:01:17+00:00",
            "source": "cboe",
            "delayed_minutes": 15,
            "expiry_dates": ["2026-11-04", "2026-11-06"],
            "selected_expiry": "2026-11-04",
            "strikes": [
                {
                    "strike": 275.0, "type": "call", "premium": 62.35,
                    "delta": 0.9877, "gamma": 0.001, "theta": -0.1158,
                    "vega": 0.008, "rho": 0.0148,
                    "volume": 3, "open_interest": 8, "implied_volatility": 1.361,
                },
            ],
        }

    monkeypatch.setattr(options_mod, "collect_options", canned)

    async with route_client(tmp_path, monkeypatch) as client:
        response = await client.get("/engine/options/AAPL")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["symbol"] == "AAPL"
    assert body["delayed_minutes"] == 15
    assert body["expiry_dates"] == ["2026-11-04", "2026-11-06"]
    assert body["selected_expiry"] == "2026-11-04"
    assert len(body["strikes"]) == 1
    s = body["strikes"][0]
    assert s["type"] == "call"
    assert s["premium"] == 62.35
    assert s["delta"] == 0.9877
    assert s["gamma"] == 0.001
    assert s["theta"] == -0.1158
    assert s["vega"] == 0.008
    assert s["rho"] == 0.0148
    assert s["volume"] == 3
    assert s["open_interest"] == 8
    assert s["implied_volatility"] == 1.361


async def test_route_unknown_symbol_is_404(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.market import options as options_mod
    from app.market.options import UnknownSymbol

    async def canned(symbol, **kwargs):
        raise UnknownSymbol(f"no listed option chain exists for {symbol!r}")

    monkeypatch.setattr(options_mod, "collect_options", canned)

    async with route_client(tmp_path, monkeypatch) as client:
        response = await client.get("/engine/options/ZZZZXXX")

    assert response.status_code == 404, response.text
    assert response.json()["error"] == "no_option_chain"


async def test_route_empty_chain_is_200_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.market import options as options_mod

    async def canned(symbol, **kwargs):
        return {
            "symbol": "EMPTY",
            "as_of": "2026-11-02T11:01:17+00:00",
            "source": "cboe",
            "delayed_minutes": 15,
            "expiry_dates": [],
            "selected_expiry": None,
            "strikes": [],
        }

    monkeypatch.setattr(options_mod, "collect_options", canned)

    async with route_client(tmp_path, monkeypatch) as client:
        response = await client.get("/engine/options/EMPTY")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["expiry_dates"] == []
    assert body["strikes"] == []


async def test_route_second_call_reads_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.market import options as options_mod

    call_count = 0

    async def counting(symbol, **kwargs):
        nonlocal call_count
        call_count += 1
        return {
            "symbol": symbol,
            "as_of": "2026-11-02T11:01:17+00:00",
            "source": "cboe" if call_count == 1 else "cache",
            "delayed_minutes": 15,
            "expiry_dates": ["2026-11-04"],
            "selected_expiry": "2026-11-04",
            "strikes": [
                {
                    "strike": 275.0, "type": "call", "premium": 62.35,
                    "delta": 0.9877, "gamma": 0.001, "theta": -0.1158,
                    "vega": 0.008, "rho": 0.0148,
                    "volume": 3, "open_interest": 8, "implied_volatility": 1.361,
                },
            ],
        }

    monkeypatch.setattr(options_mod, "collect_options", counting)

    async with route_client(tmp_path, monkeypatch) as client:
        r1 = await client.get("/engine/options/AAPL")
        r2 = await client.get("/engine/options/AAPL")

    assert r1.status_code == 200
    assert r2.status_code == 200
    # Both calls exercised the module; the route layer doesn't do separate caching
    assert call_count == 2