"""Shared harness for the three market-view routes (map, groups, calendar).

Not a test module (leading underscore), so pytest never collects it. The three
suites that use it pin the same population by construction: one responder over
one row list, so ``/market/map``, ``/market/groups`` and ``/screens/select`` are
answering questions about the same rows and their totals can be compared rather
than eyeballed.

Two seams, and both are the product's own:

* **the scan** — :class:`~tests._phase2.MockWeb` answers TradingView with a body
  whose ``d`` arrays are aligned to the columns the request asked for, because
  the parser reads them positionally;
* **the depth store** — a real JSON file in ``tmp_path``, named through
  ``MEE_SCREEN_DEPTH_STORE`` before the app is built. The calendar route reads it
  through the same :func:`app.screen.depth_source.load_depth` the screener uses,
  so the file's own ``(mtime_ns, size)`` fingerprint is exercised, not mocked.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pytest

from tests import _screen as S

#: The live US sector distribution (Finviz's own twenty-one names), used by the
#: payload-size case so the body it measures has the shape and the tile count of
#: the real one rather than a made-up split.
LIVE_SECTORS: tuple[tuple[str, int], ...] = (
    ("Finance", 1559),
    ("Health Technology", 830),
    ("Technology Services", 576),
    ("Electronic Technology", 338),
    ("Producer Manufacturing", 295),
    ("Commercial Services", 226),
    ("Consumer Services", 187),
    ("Non-Energy Minerals", 179),
    ("Process Industries", 161),
    ("Retail Trade", 156),
    ("Transportation", 155),
    ("Industrial Services", 151),
    ("Consumer Non-Durables", 136),
    ("Consumer Durables", 127),
    ("Utilities", 116),
    ("Energy Minerals", 102),
    ("Distribution Services", 98),
    ("Health Services", 80),
    ("Miscellaneous", 67),
    ("Communications", 36),
    ("Government", 1),
)


@dataclass(frozen=True, slots=True)
class Row:
    """One provider row, in the cells the scanner and the universe read."""

    symbol: str
    sector: str | None = "Finance"
    cap: float | None = 1_000_000_000.0
    change: float | None = 1.0
    exchange: str = "NASDAQ"
    country: str = "United States"
    name: str | None = None
    type: str = "stock"
    subtype: str = "common"
    currency: str = "USD"
    #: The provider's industry string ("Semiconductors"). It is a parity text
    #: field, so it arrives only when the scan asked for that column; the
    #: default is ``None`` — a row the provider did not classify.
    industry: str | None = None

    def cells(self) -> dict[str, Any]:
        """The provider's cells for this row, by column name."""

        return {
            "name": self.symbol,
            "description": self.name or f"{self.symbol} Corporation",
            "exchange": self.exchange,
            "country": self.country,
            "sector": self.sector,
            "industry": self.industry,
            "type": self.type,
            "subtype": self.subtype,
            "currency": self.currency,
            "close": 10.0,
            "change": self.change,
            "volume": 1_000.0,
            "Value.Traded": 10_000.0,
            "market_cap_basic": self.cap,
        }


def scan_body(rows: Sequence[Row], columns: Sequence[str]) -> dict[str, Any]:
    """A scanner response whose ``d`` arrays are aligned with ``columns``."""

    data = [
        {
            "s": f"{row.exchange}:{row.symbol}",
            "d": [row.cells().get(column) for column in columns],
        }
        for row in rows
    ]
    return {"totalCount": len(data), "data": data}


def scanned(rows: Sequence[Row]) -> Any:
    """A ``MockWeb`` responder that answers the scan with ``rows``."""

    def respond(request: Any) -> dict[str, Any]:
        body = json.loads(request.content)
        return scan_body(rows, tuple(body["columns"]))

    return respond


class Clock:
    """A monotonic clock a test can advance, shared with the ``ScanCache``."""

    def __init__(self, start: float = 1_000.0) -> None:
        self.now = float(start)

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += float(seconds)


@dataclass
class Harness:
    """The app, its client, the mocked provider and the depth file it reads."""

    client: Any
    app: Any
    web: Any
    clock: Clock
    depth_path: Path

    def write_depth(
        self, symbols: Mapping[str, Mapping[str, Any]], *, as_of: str = "2026-10-31T20:00:00+00:00"
    ) -> Path:
        """Rewrite the depth store, moving its ``(mtime_ns, size)`` fingerprint.

        The content is padded with a throwaway key so two writes in the same
        nanosecond still differ in size: ``load_depth`` caches by fingerprint,
        and a test that changed a cell without changing the file would be
        testing its own cache.
        """

        document = {"as_of": as_of, "symbols": dict(symbols)}
        encoded = json.dumps(document, sort_keys=True)
        # A rewrite always changes the size, so the fingerprint moves even when
        # the filesystem's mtime resolution is coarser than the test's clock.
        encoded = encoded[:-1] + f',"pad":{len(encoded)}' + "}"
        self.depth_path.write_text(encoded, encoding="utf-8")
        return self.depth_path

    async def get(self, path: str, **params: Any) -> Any:
        return await self.client.get(path, params=params or None)


@asynccontextmanager
async def market_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    rows: Sequence[Row],
    scan_ttl: float = 5.0,
    clock: Clock | None = None,
    depth: Mapping[str, Mapping[str, Any]] | None = None,
) -> AsyncIterator[Harness]:
    """An ASGI client over the real app, the mocked scanner and a depth file."""

    monkeypatch.setenv("MEE_DB_PATH", str(tmp_path / "mee.db"))
    monkeypatch.setenv("MEE_EXPORT_DIR", str(tmp_path / "exports"))
    monkeypatch.setenv("MEE_PROVIDER_MODE", "live")
    # A stray collector flag would start a background task and reach for the
    # network; these routes must not need one.
    monkeypatch.delenv("MEE_DEPTH", raising=False)
    depth_path = tmp_path / "depth.json"
    if depth is not None:
        depth_path.write_text(
            json.dumps({"as_of": "2026-10-31T20:00:00+00:00", "symbols": dict(depth)}),
            encoding="utf-8",
        )
    monkeypatch.setenv("MEE_SCREEN_DEPTH_STORE", str(depth_path))

    web = S.MockWeb()
    web.json("tradingview", scanned(rows))
    S.install_web(monkeypatch, web)

    from httpx2 import ASGITransport, AsyncClient

    from app.api.screens import ScanCache
    from app.db import ensure_schema_async
    from app.main import create_app

    application = create_app()
    await ensure_schema_async(Path(tmp_path / "mee.db"))
    test_clock = clock or Clock()
    application.state.scan_cache = ScanCache(ttl_s=scan_ttl, clock=test_clock)

    transport = ASGITransport(app=application, raise_app_exceptions=False)
    async with AsyncClient(
        transport=transport, base_url="http://mee.test", headers={"x-api-key": "market-key"}
    ) as client:
        yield Harness(
            client=client,
            app=application,
            web=web,
            clock=test_clock,
            depth_path=depth_path,
        )


def selection(*, sector: str | None = None, region: str = "us") -> dict[str, Any]:
    """A ``/screens/select`` body over the same population these routes publish."""

    criteria: dict[str, Any] = {"sort": "market_cap", "limit": 1}
    if sector is not None:
        criteria["filters"] = [{"key": "sector", "op": "eq", "value": sector}]
    return {"mode": "criteria", "region": region, "criteria": criteria}


def universe(count: int = 5_576) -> list[Row]:
    """``count`` synthetic rows across the live sector distribution.

    Caps are strictly increasing and distinct, so the universe builder's issuer
    dedupe — keyed on description and cap — never merges two of them, and the
    treemap sees exactly ``count`` tiles.
    """

    rows: list[Row] = []
    index = 0
    for sector, size in LIVE_SECTORS:
        for _ in range(size):
            if index >= count:
                break
            rows.append(
                Row(
                    symbol=f"S{index:04d}",
                    sector=sector,
                    cap=1_000_000_000.0 + index * 1_500_000.0,
                    change=round(((index * 37) % 900 - 450) / 100.0, 2),
                )
            )
            index += 1
        if index >= count:
            break
    return [replace(row) for row in rows]
