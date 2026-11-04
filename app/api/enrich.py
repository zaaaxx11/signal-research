"""``GET /market/crypto/meta``, ``GET /market/crypto/global`` — the crypto row's
universe, priced by a ladder of two keyless public sources.

Why these routes exist
----------------------
``GET /market/alt?class=crypto`` prices every catalogue symbol from Yahoo's v8
chart endpoint, which knows a price and a volume and nothing else: no market
cap, no rank, no supply, no 52-week high, no logo. A crypto table that lists
"BTC-USD 78,249" without a cap or a rank is half-instrumented. The free sources
that *do* know those fields are public and keyless: **CoinGecko**'s
``/coins/markets`` (one call, 40 ids, cap/rank/supply/logo/high/low/ATH and
1h/7d/30d moves) and **DeFiLlama**'s ``/tvl/{slug}`` (one number per protocol).
This module asks them, once per TTL, and publishes the result as a map keyed by
the *Yahoo* symbol (``BTC-USD``), so a client that already read ``/market/alt``
joins by ``symbol`` and renders without a second table.

Futures and FX are not here
---------------------------
CoinGecko, DeFiLlama and CMC's public endpoints do not carry futures or FX
quotes — there is no free keyless source for a contract's market cap because a
contract has none. Their ``/market/alt`` rows stay Yahoo-priced, unchanged.

Graceful degradation
--------------------
Every field is nullable and the document is null-tolerant by construction: when
CoinGecko answers, a symbol's row carries its cap, rank, supply and logo; when
it does not (rate limited, transport down, an id the catalogue names that
CoinGecko does not), the row is absent from the map — ``/market/alt``'s own
rows keep coming from Yahoo, and the page renders a price-only table. **No
field is ever invented**: a missing rank is `null`, not 0; a missing logo is
absent, not a fallback guess.

One transport, injected
-----------------------
:class:`SourceTransport` is the only object that opens a socket; it is the same
seam shape :mod:`app.api.alt` uses. The application holds an
:class:`EnrichState` on ``app.state.enrich`` so a test installs a counting
double and the suite stays socket-free.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from fastapi import APIRouter, Request
from fastapi.responses import Response

from app.api.alt import CRYPTO
from app.api.cache import not_modified
from app.market.sources import BROWSER_USER_AGENT

router = APIRouter(tags=["discovery"])

#: Why the numbers exist, published as ``method`` so a client reads the recipe.
METHOD = "coingecko_markets_v3_plus_defillama_tvl"

#: Fetch window for the CoinGecko markets snapshot.
COINGECKO_URL = "https://api.coingecko.com/api/v3/coins/markets"
COINGECKO_TTL_S = 300.0
#: One headline answer is small; a five-minute window keeps a polling page
#: inside the public API's ~30 req/min budget comfortably.
GLOBAL_TTL_S = 300.0
GLOBAL_URL = "https://api.coingecko.com/api/v3/global"
#: Per-protocol TVL: one number per slug.
LLAMA_BASE_URL = "https://api.llama.fi/tvl"
LLAMA_TTL_S = 600.0

#: Transport budget and concurrency: one CoinGecko call, a handful of llama
#: calls — never more than six at a time, timeout still the alt reader's 8 s.
FETCH_TIMEOUT_S = 8.0
CONCURRENCY = 6

#: The percentage fields CoinGecko ships are picked by their exact key names
#: (``price_change_percentage_{1h,7d,30d}_in_currency``), so a horizon the
#: request does not include is a ``None``, never a misattributed number.

#: DeFiLlama protocol slugs for the catalogue symbols that *are* protocols —
#: curated and slug-verified, not guessed. A symbol absent here simply has no
#: TVL row: a coin's cap is CoinGecko's field and it is already there.
LLAMA_SLUGS: dict[str, str] = {
    "AAVE-USD": "aave",
    "MKR-USD": "makerdao",
    "LDO-USD": "lido",
    "JUP-USD": "jupiter",
    "TIA-USD": "celestia",
    "ARB-USD": "arbitrum",
    "OP-USD": "optimism",
    "NEAR-USD": "near",
}

#: Yahoo symbol → CoinGecko id, verified against a live probe on 2026-11-04
#: (all 40 ids answered with a row). The map is data: an id CoinGecko renames
#: leaves its symbol absent from the meta map, not a row of invented zeros.
COINGECKO_IDS: dict[str, str] = {
    "BTC-USD": "bitcoin",
    "ETH-USD": "ethereum",
    "USDT-USD": "tether",
    "BNB-USD": "binancecoin",
    "SOL-USD": "solana",
    "XRP-USD": "ripple",
    "USDC-USD": "usd-coin",
    "ADA-USD": "cardano",
    "DOGE-USD": "dogecoin",
    "TRX-USD": "tron",
    "AVAX-USD": "avalanche-2",
    "LINK-USD": "chainlink",
    "DOT-USD": "polkadot",
    "TON-USD": "the-open-network",
    "SHIB-USD": "shiba-inu",
    "LTC-USD": "litecoin",
    "BCH-USD": "bitcoin-cash",
    "XLM-USD": "stellar",
    "ATOM-USD": "cosmos",
    "ETC-USD": "ethereum-classic",
    "XMR-USD": "monero",
    "FIL-USD": "filecoin",
    "ARB-USD": "arbitrum",
    "OP-USD": "optimism",
    "NEAR-USD": "near",
    "ICP-USD": "internet-computer",
    "INJ-USD": "injective-protocol",
    "HBAR-USD": "hedera-hashgraph",
    "VET-USD": "vechain",
    "RENDER-USD": "render-token",
    "AAVE-USD": "aave",
    "MKR-USD": "maker",
    "ALGO-USD": "algorand",
    "SAND-USD": "the-sandbox",
    "TIA-USD": "celestia",
    "SEI-USD": "sei-network",
    "LDO-USD": "lido-dao",
    "WLD-USD": "worldcoin-wld",
    "JUP-USD": "jupiter-exchange-solana",
    "ONDO-USD": "ondo-finance",
}


def ids_query() -> str:
    """The ``ids=`` value: every catalogue id, comma-joined, in map order."""

    return ",".join(COINGECKO_IDS[symbol] for symbol in COINGECKO_IDS)


# ---------------------------------------------------------------------------
# One populated row
# ---------------------------------------------------------------------------


def _num(value: Any) -> float | None:
    """A provider number, or ``None`` for anything else that is not finite."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if number == number and -1e308 < number < 1e308 else None


@dataclass(frozen=True, slots=True)
class CoinMeta:
    """One catalogue symbol's universe, as the enrichment sources state it."""

    symbol: str
    coingecko_id: str
    name: str | None
    image: str | None
    market_cap: float | None
    market_cap_rank: int | None
    circulating_supply: float | None
    max_supply: float | None
    total_supply: float | None
    fully_diluted_valuation: float | None
    high_24h: float | None
    low_24h: float | None
    ath: float | None
    ath_change_pct: float | None
    pct_1h: float | None
    pct_7d: float | None
    pct_30d: float | None
    tvl: float | None = None
    sources: tuple[str, ...] = ("coingecko",)

    def document(self) -> dict[str, Any]:
        """The row the meta map publishes, in the order a page reads it."""

        return {
            "symbol": self.symbol,
            "id": self.coingecko_id,
            "name": self.name,
            "image": self.image,
            "market_cap": self.market_cap,
            "market_cap_rank": self.market_cap_rank,
            "circulating_supply": self.circulating_supply,
            "max_supply": self.max_supply,
            "total_supply": self.total_supply,
            "fully_diluted_valuation": self.fully_diluted_valuation,
            "high_24h": self.high_24h,
            "low_24h": self.low_24h,
            "ath": self.ath,
            "ath_change_pct": self.ath_change_pct,
            "pct_1h": self.pct_1h,
            "pct_7d": self.pct_7d,
            "pct_30d": self.pct_30d,
            "tvl": self.tvl,
            "sources": list(self.sources),
        }


# ``tvl`` defaults to ``None``: CoinGecko knows nothing of it and a
# CoinGecko-only row is constructible without an argument — but the document
# always publishes the key, as ``null`` or a number, so a client joins blindly.


# ---------------------------------------------------------------------------
# The state: one transport, two caches
# ---------------------------------------------------------------------------


class SourceTransport:
    """The keyless public reads: CoinGecko once, llama per slug, concurrent."""

    __slots__ = ("_concurrency", "_semaphore", "_timeout_s")

    def __init__(
        self, *, timeout_s: float = FETCH_TIMEOUT_S, concurrency: int = CONCURRENCY
    ) -> None:
        self._timeout_s = float(timeout_s)
        self._concurrency = max(1, int(concurrency))
        self._semaphore = asyncio.Semaphore(self._concurrency)

    async def _get(self, url: str) -> Any:
        """One JSON body, or ``None`` for any failure at all."""

        from app.providers import http as provider_http

        headers = {"user-agent": BROWSER_USER_AGENT, "accept": "application/json"}
        try:
            async with provider_http.build_client(
                timeout_s=self._timeout_s, headers=headers
            ) as client:
                response = await client.get(url)
        except Exception:  # noqa: BLE001 - a refused source is a null field, not a 500
            return None
        if int(getattr(response, "status_code", 0)) != 200:
            return None
        try:
            return response.json()
        except Exception:  # noqa: BLE001 - an unreadable body is a refused read
            return None

    async def markets(self) -> list[Mapping[str, Any]] | None:
        """The CoinGecko markets snapshot for every catalogue id, or ``None``."""

        url = f"{COINGECKO_URL}?vs_currency=usd&ids={ids_query()}"
        url += "&price_change_percentage=1h%2C7d%2C30d&per_page=50&page=1"
        body = await self._get(url)
        return body if isinstance(body, list) else None

    async def global_data(self) -> Mapping[str, Any] | None:
        """CoinGecko's ``/global`` body (the ``data`` object), or ``None``."""

        body = await self._get(GLOBAL_URL)
        data = body.get("data") if isinstance(body, Mapping) else None
        return data if isinstance(data, Mapping) else None

    async def tvl(self, symbol: str) -> tuple[str, float | None]:
        """``(symbol, tvl)``: DeFiLlama's number for the symbol's slug, or None."""

        slug = LLAMA_SLUGS.get(symbol)
        if slug is None:
            return symbol, None
        body = await self._get(f"{LLAMA_BASE_URL}/{slug}")
        value = _num(body) if not isinstance(body, Mapping) else None
        if value is None:
            return symbol, None
        return symbol, (0.0 if value == 0 else value)


@dataclass(frozen=True, slots=True)
class Timed:
    """A payload and the moment it landed."""

    payload: Any
    stored_at: float


class EnrichState:
    """The cached snapshots and the transport, owned by the application."""

    __slots__ = ("clock", "global_cache", "llama_cache", "markets_cache", "transport")

    def __init__(self, *, clock: Any = time.time, transport: Any = None) -> None:
        self.clock = clock
        self.markets_cache: dict[str, Timed] = {}
        self.global_cache: dict[str, Timed] = {}
        self.llama_cache: dict[str, Timed] = {}
        self.transport = transport if transport is not None else SourceTransport()

    def get(self, cache: dict[str, Timed], key: str, ttl: float) -> Timed | None:
        """A live entry, or ``None`` (expired entries are dropped)."""

        entry = cache.get(key)
        if entry is None:
            return None
        if self.clock() - entry.stored_at >= ttl:
            del cache[key]
            return None
        return entry

    def put(self, cache: dict[str, Timed], key: str, payload: Any) -> Timed:
        entry = Timed(payload=payload, stored_at=self.clock())
        cache[key] = entry
        return entry


def enrich_state(request: Request) -> EnrichState:
    """The process's state, created on first use (one per app, not global)."""

    state = getattr(request.app.state, "enrich", None)
    if isinstance(state, EnrichState):
        return state
    state = EnrichState()
    request.app.state.enrich = state
    return state


# ---------------------------------------------------------------------------
# The documents
# ---------------------------------------------------------------------------


def build_meta_map(
    markets: Sequence[Mapping[str, Any]] | None,
    tvls: Mapping[str, float | None],
    *,
    as_of: str,
) -> dict[str, Any]:
    """``{as_of, method, measured, universe_total, rows: {symbol: fields}}``.

    ``rows`` is keyed — not listed — because the client joins into ``/market/alt``
    by symbol. Symbols CoinGecko did not answer are absent from ``rows`` but
    counted in ``universe_total``, so a shrink is visible and never a zero row.
    """

    rows: dict[str, Any] = {}
    if markets:
        by_id = {entry.get("id"): entry for entry in markets if isinstance(entry, Mapping)}
        for symbol, coin_id in COINGECKO_IDS.items():
            entry = by_id.get(coin_id)
            if not isinstance(entry, Mapping):
                continue
            meta = CoinMeta(
                symbol=symbol,
                coingecko_id=coin_id,
                name=(entry.get("name") if isinstance(entry.get("name"), str) else None),
                image=(entry.get("image") if isinstance(entry.get("image"), str) else None),
                market_cap=_num(entry.get("market_cap")),
                market_cap_rank=(
                    int(rank) if (rank := _num(entry.get("market_cap_rank"))) is not None else None
                ),
                circulating_supply=_num(entry.get("circulating_supply")),
                max_supply=_num(entry.get("max_supply")),
                total_supply=_num(entry.get("total_supply")),
                fully_diluted_valuation=_num(entry.get("fully_diluted_valuation")),
                high_24h=_num(entry.get("high_24h")),
                low_24h=_num(entry.get("low_24h")),
                ath=_num(entry.get("ath")),
                ath_change_pct=_num(entry.get("ath_change_percentage")),
                pct_1h=_num(entry.get("price_change_percentage_1h_in_currency")),
                pct_7d=_num(entry.get("price_change_percentage_7d_in_currency")),
                pct_30d=_num(entry.get("price_change_percentage_30d_in_currency")),
                tvl=tvls.get(symbol),
                sources=(
                    ("coingecko", "defillama") if tvls.get(symbol) is not None else ("coingecko",)
                ),
            )
            rows[symbol] = meta.document()
    return {
        "as_of": as_of,
        "method": METHOD,
        "universe_total": len(COINGECKO_IDS),
        "measured": len(rows),
        "rows": rows,
    }


def build_global_document(data: Mapping[str, Any], *, as_of: str) -> dict[str, Any]:
    """CoinGecko's global block, as the fields a dashboard strip prints."""

    def cap_usd() -> float | None:
        caps = data.get("total_market_cap")
        if isinstance(caps, Mapping):
            return _num(caps.get("usd"))
        return None

    def volume_usd() -> float | None:
        vols = data.get("total_volume")
        if isinstance(vols, Mapping):
            return _num(vols.get("usd"))
        return None

    def cap_change() -> float | None:
        changes = data.get("market_cap_change_percentage_24h_usd")
        return _num(changes)

    dominance = data.get("market_cap_percentage")
    shares = dominance if isinstance(dominance, Mapping) else {}
    return {
        "as_of": as_of,
        "method": "coingecko_global_v3",
        "total_market_cap_usd": cap_usd(),
        "total_volume_usd": volume_usd(),
        "market_cap_change_pct_24h": cap_change(),
        "btc_dominance": _num(shares.get("btc")),
        "eth_dominance": _num(shares.get("eth")),
        "active_cryptocurrencies": (
            int(n) if (n := _num(data.get("active_cryptocurrencies"))) is not None else None
        ),
    }


# ---------------------------------------------------------------------------
# The answers
# ---------------------------------------------------------------------------


def _iso(seconds: float) -> str:
    from datetime import UTC, datetime

    return datetime.fromtimestamp(float(seconds), UTC).isoformat(timespec="seconds")


async def _meta_answer(request: Request, *, head: bool) -> Response:
    state = enrich_state(request)
    cached = state.get(state.markets_cache, "markets", COINGECKO_TTL_S)

    if cached is not None:
        markets, tvls = cached.payload
        body = encode_crypto_map(
            build_meta_map(markets, tvls, as_of=_iso(cached.stored_at))
        )
        headers = {"X-Enrich-Cache": "hit", "Cache-Control": "no-cache"}
        if head:
            return Response(headers={**headers, "Content-Length": str(len(body))})
        return Response(content=body, media_type="application/json", headers=headers)

    markets = await state.transport.markets()
    if markets is None:
        # Refused read: publish the shape with zero rows (a client renders
        # Yahoo-only) but cache nothing — the next caller asks again.
        body = encode_crypto_map(
            {
                "as_of": _iso(state.clock()),
                "method": METHOD,
                "universe_total": len(COINGECKO_IDS),
                "measured": 0,
                "rows": {},
            }
        )
        headers = {"X-Enrich-Cache": "miss", "Cache-Control": "no-cache"}
        if head:
            return Response(headers={**headers, "Content-Length": str(len(body))})
        return Response(content=body, media_type="application/json", headers=headers)
    # TVLs travel with the markets read: one refresh collects both.
    tvls_list = await asyncio.gather(
        *(state.transport.tvl(symbol) for symbol in LLAMA_SLUGS)
    )
    tvls: dict[str, float | None] = dict(tvls_list)
    cached = state.put(state.markets_cache, "markets", (markets, tvls))
    body = encode_crypto_map(
        build_meta_map(markets, tvls, as_of=_iso(cached.stored_at))
    )
    headers = {"X-Enrich-Cache": "miss", "Cache-Control": "no-cache"}
    if head:
        return Response(headers={**headers, "Content-Length": str(len(body))})
    return Response(content=body, media_type="application/json", headers=headers)


async def _global_answer(request: Request, *, head: bool) -> Response:
    state = enrich_state(request)
    snapshot = state.get(state.global_cache, "global", GLOBAL_TTL_S)
    if snapshot is not None:
        body = encode_crypto_map(
            build_global_document(
                snapshot.payload if isinstance(snapshot, Timed) else snapshot,
                as_of=_iso(timestamp_of(snapshot, state)),
            )
        )
        headers = {"X-Enrich-Cache": "hit", "Cache-Control": "no-cache"}
        if head:
            return Response(headers={**headers, "Content-Length": str(len(body))})
        return Response(content=body, media_type="application/json", headers=headers)
    data = await state.transport.global_data()
    if data is None:
        body = encode_crypto_map(
            {"as_of": _iso(state.clock()), "method": "coingecko_global_v3", "available": False}
        )
        headers = {"X-Enrich-Cache": "miss", "Cache-Control": "no-cache"}
        if head:
            return Response(headers={**headers, "Content-Length": str(len(body))})
        return Response(content=body, media_type="application/json", headers=headers)
    snapshot = state.put(state.global_cache, "global", data)
    body = encode_crypto_map(
        build_global_document(data, as_of=_iso(snapshot.stored_at))
    )
    headers = {"X-Enrich-Cache": "miss", "Cache-Control": "no-cache"}
    if head:
        return Response(headers={**headers, "Content-Length": str(len(body))})
    return Response(content=body, media_type="application/json", headers=headers)


def timestamp_of(entry: Any, state: EnrichState) -> float:
    """The entry's landing time, as ``_iso`` wants (a Timed's own value)."""

    return entry.stored_at if isinstance(entry, Timed) else state.clock()


def encode_crypto_map(document: Mapping[str, Any]) -> bytes:
    """The documents' bytes: deterministic JSON, the same rows → same bytes."""

    from app.api.sectors import encode

    return encode(document)


@router.get("/market/crypto/meta")
async def crypto_meta(request: Request) -> Response:
    """The crypto catalogue's cap/rank/supply/logo/TVL map, keyed by symbol (GET)."""

    return await _meta_answer(request, head=False)


@router.get("/market/crypto/global")
async def crypto_global(request: Request) -> Response:
    """The crypto market's headline numbers: total cap, dominance, change (GET)."""

    return await _global_answer(request, head=False)


__all__ = [
    "COINGECKO_IDS",
    "COINGECKO_TTL_S",
    "CoinMeta",
    "EnrichState",
    "GLOBAL_TTL_S",
    "LLAMA_SLUGS",
    "LLAMA_TTL_S",
    "METHOD",
    "SourceTransport",
    "build_global_document",
    "build_meta_map",
    "crypto_global",
    "crypto_meta",
    "enrich_state",
    "router",
]
