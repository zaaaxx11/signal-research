"""Shared harness for the two alternative-market routes (``/market/alt``, ``/market/quotes``).

Not a test module (leading underscore), so pytest never collects it.

Two seams, and both are the product's own:

* **the scan** — :class:`~tests._phase2.MockWeb` answers TradingView with rows whose
  ``d`` arrays are aligned to the columns the request asked for, so a quote for
  ``AAPL`` is read from the same population ``/market/map`` publishes;
* **the quote transport** — :class:`FakeTransport` stands in for
  :class:`app.api.alt.HttpTransport`. It answers one Yahoo v8 chart body per
  symbol, records **one batch per call**, and can fail a symbol, empty a
  symbol's timestamps, or refuse to answer at all. No socket is opened, which is
  the whole point: the conftest egress guard would fail the test otherwise.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Mapping, Sequence

import pytest

from tests import _market as M

#: 2024-11-01 14:30 UTC, five minutes apart — a past session, so a chart looks
#: like a closed market's last session rather than a live one.
BASE_T = 1_730_471_400
STEP_S = 300


# ---------------------------------------------------------------------------
# Payload builders
# ---------------------------------------------------------------------------


def chart_body(
    symbol: str = "BTC-USD",
    *,
    closes: Sequence[Any] = (100.0,),
    previous_close: Any = 100.0,
    previous_key: str = "chartPreviousClose",
    currency: str | None = "USD",
    timezone: str = "America/New_York",
    volumes: Sequence[Any] | None = None,
    start: int = BASE_T,
    step: int = STEP_S,
    meta: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """One Yahoo v8 chart body, shaped exactly as the provider sends it.

    ``closes`` may contain ``None`` (a halt), ``0.0`` (a market-closed print) and
    any number of trailing nulls (the forming bar); the route has to pick the
    last *price* out of it.
    """

    stamps = [start + index * step for index in range(len(closes))]
    volume = (
        list(volumes)
        if volumes is not None
        else [1_000.0 + index for index in range(len(closes))]
    )
    quote = {
        "open": list(closes),
        "high": list(closes),
        "low": list(closes),
        "close": list(closes),
        "volume": volume,
    }
    block: dict[str, Any] = {
        "symbol": symbol,
        "exchangeTimezoneName": timezone,
        "dataGranularity": "5m",
    }
    if currency is not None:
        block["currency"] = currency
    if previous_close is not None:
        block[previous_key] = previous_close
    if meta:
        block.update(dict(meta))
    return {
        "chart": {
            "result": [
                {"meta": block, "timestamp": stamps, "indicators": {"quote": [quote]}}
            ],
            "error": None,
        }
    }


def empty_body(symbol: str = "BTC-USD", *, previous_close: Any = 100.0) -> dict[str, Any]:
    """A body with an empty ``timestamp`` array: no bar at all, but a meta."""

    return chart_body(symbol, closes=(), previous_close=previous_close)


def priced(
    instruments: Sequence[Any], *, base: float = 100.0, step: float = 1.0
) -> dict[str, Any]:
    """One body per instrument, drifting upward, against a common previous close.

    ``instruments`` is a catalogue (anything with a ``symbol``); the resulting
    change for the n-th symbol is exactly ``n * step / base * 100`` percent.
    """

    return {
        instrument.symbol: chart_body(
            instrument.symbol, closes=(base + index * step,), previous_close=base
        )
        for index, instrument in enumerate(instruments)
    }


# ---------------------------------------------------------------------------
# The transport double
# ---------------------------------------------------------------------------


@dataclass
class FakeTransport:
    """A counting stand-in for :class:`app.api.alt.HttpTransport`.

    ``bodies`` maps a symbol to the chart body the provider would send, or
    ``None`` for "this fetch failed". ``batches`` records every call with the
    symbols it was asked for, so a test can prove one read per class and that a
    warm request makes none.
    """

    bodies: dict[str, Any] = field(default_factory=dict)
    delay_s: float = 0.0
    raise_on_fetch: bool = False
    batches: list[tuple[str, ...]] = field(default_factory=list)

    async def payloads(self, symbols: Sequence[str]) -> dict[str, Any]:
        self.batches.append(tuple(symbols))
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        if self.raise_on_fetch:
            raise RuntimeError("the quote transport is down")
        return {symbol: self.bodies.get(symbol) for symbol in symbols}

    @property
    def asked(self) -> list[str]:
        """Every symbol asked for, in the order the batches asked."""

        return [symbol for batch in self.batches for symbol in batch]

    def count(self) -> int:
        """How many symbol reads were made in total."""

        return sum(len(batch) for batch in self.batches)


@dataclass(frozen=True)
class BareRow:
    """A provider row with an explicit price/change, for the awkward cases.

    :class:`tests._market.Row` hardcodes ``close=10.0``; a quote for a stock the
    scanner could not price needs a row whose ``close`` is null.
    """

    symbol: str
    price: float | None = 10.0
    change: float | None = 1.0
    currency: str = "USD"
    exchange: str = "NASDAQ"

    def cells(self) -> dict[str, Any]:
        return {
            "name": self.symbol,
            "description": f"{self.symbol} Corporation",
            "exchange": "NASDAQ",
            "country": "United States",
            "sector": "Finance",
            "type": "stock",
            "subtype": "common",
            "currency": self.currency,
            "close": self.price,
            "change": self.change,
            "volume": 1_000.0,
            "Value.Traded": 10_000.0,
            "market_cap_basic": 1_000_000_000.0,
        }


# ---------------------------------------------------------------------------
# The app
# ---------------------------------------------------------------------------


@asynccontextmanager
async def alt_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    rows: Sequence[Any] = (),
    transport: FakeTransport | None = None,
    clock: M.Clock | None = None,
    scan_ttl: float = 5.0,
) -> AsyncIterator[M.Harness]:
    """An ASGI client over the real app, a mocked scanner and a fake transport."""

    async with M.market_client(
        tmp_path, monkeypatch, rows=list(rows), scan_ttl=scan_ttl, clock=clock, depth={}
    ) as harness:
        from app.api.alt import AltState

        harness.app.state.alt = AltState(clock=clock or M.Clock(), transport=transport)
        yield harness
