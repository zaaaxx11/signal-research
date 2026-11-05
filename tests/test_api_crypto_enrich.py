"""Contract for ``GET /market/crypto/meta`` and ``GET /market/crypto/global``.

The enrichment routes: the crypto catalogue's universe from keyless CoinGecko
(cap, rank, supply, logo, 1h/7d/30d moves) plus DeFiLlama's TVL for protocol
slugs, keyed by the Yahoo symbol so a client joins into ``/market/alt``.

The rules pinned here, in the order the routes compute them:

* **the document** — ``{as_of, method, universe_total, measured, rows}``,
  ``rows`` keyed by symbol, one row with the full field set including ``tvl``
  (``null`` when the symbol is not a protocol slug) and ``sources``;
* **the enrichment is additive, never invented** — a field the source refused
  is ``null``; a symbol CoinGecko does not name is absent from ``rows`` while
  still counted in ``universe_total``;
* **the cache is real** — a second meta call inside the TTL makes zero
  transport calls (a counting double answers once), and a refused read is a
  ``measured: 0`` document that is *not* cached (the next caller asks again);
* **global** — the headline fields, or ``available: false`` when refused;
* **the id map is the catalogue** — every ``CRYPTO`` symbol has a CoinGecko id,
  and the ids are unique (one probe, 40 ids, one call).
"""

from __future__ import annotations

import json
import time
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import enrich as enrich_api
from app.api.sectors import encode

_ROW_KEYS = frozenset(
    {
        "symbol",
        "id",
        "name",
        "image",
        "market_cap",
        "market_cap_rank",
        "circulating_supply",
        "max_supply",
        "total_supply",
        "fully_diluted_valuation",
        "high_24h",
        "low_24h",
        "ath",
        "ath_change_pct",
        "pct_1h",
        "pct_7d",
        "pct_30d",
        "tvl",
        "sources",
    }
)


class CountingTransport:
    """A double that answers one markets body, one global body, and counts."""

    def __init__(
        self,
        *,
        markets: list[dict[str, Any]] | None = None,
        refuse_markets: bool = False,
        global_data: dict[str, Any] | None = None,
        tvls: dict[str, float | None] | None = None,
        coins: dict[str, dict[str, Any] | None] | None = None,
    ) -> None:
        self._markets = markets
        self._refuse_markets = refuse_markets
        self._global_data = global_data
        self._tvls = tvls or {}
        self._coins = coins or {}
        self.markets_calls = 0
        self.global_calls = 0
        self.tvl_calls: list[str] = []
        self.coin_detail_calls: list[str] = []

    def _default_markets(self) -> list[dict[str, Any]]:
        return [
            {
                "id": coin_id,
                "name": coin_id.title(),
                "image": f"https://img/{coin_id}.png",
                "market_cap": 1000.0 + i,
                "market_cap_rank": i + 1,
                "circulating_supply": 2_000_000.0,
                "high_24h": 105.0,
                "low_24h": 95.0,
                "ath": 200.0,
                "ath_change_percentage": -12.5,
                "price_change_percentage_1h_in_currency": 0.1,
                "price_change_percentage_7d_in_currency": -2.2,
                "price_change_percentage_30d_in_currency": 5.5,
            }
            for i, coin_id in enumerate(enrich_api.COINGECKO_IDS.values())
        ]

    async def markets(self) -> list[dict[str, Any]] | None:
        self.markets_calls += 1
        if self._refuse_markets:
            return None
        return self._markets if self._markets is not None else self._default_markets()

    async def global_data(self) -> dict[str, Any] | None:
        self.global_calls += 1
        return self._global_data

    async def tvl(self, symbol: str) -> tuple[str, float | None]:
        self.tvl_calls.append(symbol)
        if symbol not in enrich_api.LLAMA_SLUGS:
            return symbol, None
        return symbol, self._tvls.get(symbol)

    async def coin_detail(self, coin_id: str) -> dict[str, Any] | None:
        self.coin_detail_calls.append(coin_id)
        return self._coins.get(coin_id)


def _client(transport: CountingTransport) -> TestClient:
    app = FastAPI()
    app.include_router(enrich_api.router)
    app.state.enrich = enrich_api.EnrichState(clock=time.time, transport=transport)
    return TestClient(app)


def test_the_id_map_is_the_catalogue_and_unique() -> None:
    from app.api.alt import CRYPTO

    assert set(enrich_api.COINGECKO_IDS) == {instrument.symbol for instrument in CRYPTO}
    assert len(set(enrich_api.COINGECKO_IDS.values())) == len(enrich_api.COINGECKO_IDS)


def test_meta_maps_every_answered_symbol_onto_yahoo_keys() -> None:
    transport = CountingTransport(tvls={"AAVE-USD": 15.5e9})
    document = json.loads(encode(enrich_api.build_meta_map(
        transport_markets(transport), {"AAVE-USD": 15.5e9}, as_of="now"
    )))

    assert document["universe_total"] == 40
    assert document["measured"] == 40
    assert set(document["rows"]) == set(enrich_api.COINGECKO_IDS)
    btc = document["rows"]["BTC-USD"]
    assert set(btc) == _ROW_KEYS
    assert btc["id"] == "bitcoin"
    assert btc["market_cap_rank"] == 1 and btc["market_cap"] == 1000.0
    assert btc["pct_7d"] == -2.2 and btc["ath_change_pct"] == -12.5
    assert btc["sources"] == ["coingecko"] and btc["tvl"] is None
    aave = document["rows"]["AAVE-USD"]
    assert aave["tvl"] == 15.5e9
    assert aave["sources"] == ["coingecko", "defillama"]


def transport_markets(transport: CountingTransport) -> Any:
    """The transport's markets body, synchronously, for the pure-function tests."""

    import asyncio

    return asyncio.run(transport.markets())


def test_meta_satisfies_the_transport_once_per_ttl() -> None:
    transport = CountingTransport()
    client = _client(transport)

    first = client.get("/market/crypto/meta")
    assert first.status_code == 200
    document = first.json()
    assert document["measured"] == 40
    assert first.headers["X-Enrich-Cache"] == "miss"
    assert transport.markets_calls == 1
    # One TVL read per curated slug, and none for the symbols without one.
    assert len(transport.tvl_calls) == len(enrich_api.LLAMA_SLUGS)

    second = client.get("/market/crypto/meta")
    assert second.headers["X-Enrich-Cache"] == "hit"
    assert transport.markets_calls == 1  # the cache answered; no second read


def test_a_refused_markets_read_is_measured_zero_and_not_cached() -> None:
    transport = CountingTransport(refuse_markets=True)
    client = _client(transport)

    first = client.get("/market/crypto/meta")
    assert first.status_code == 200
    document = first.json()
    assert document["rows"] == {} and document["measured"] == 0
    assert document["universe_total"] == 40

    # The refused answer is not cached: the second call asks again.
    client.get("/market/crypto/meta")
    assert transport.markets_calls == 2


def test_global_prints_the_headline_fields() -> None:
    transport = CountingTransport(
        global_data={
            "total_market_cap": {"usd": 3.1e12},
            "total_volume": {"usd": 9.0e10},
            "market_cap_change_percentage_24h_usd": 1.4,
            "market_cap_percentage": {"btc": 58.1, "eth": 12.2},
            "active_cryptocurrencies": 17221,
        }
    )
    client = _client(transport)

    response = client.get("/market/crypto/global")
    assert response.status_code == 200
    document = response.json()
    assert document["total_market_cap_usd"] == 3.1e12
    assert document["btc_dominance"] == 58.1
    assert document["eth_dominance"] == 12.2
    assert document["active_cryptocurrencies"] == 17221
    assert transport.global_calls == 1

    again = client.get("/market/crypto/global")
    assert again.headers["X-Enrich-Cache"] == "hit"
    assert transport.global_calls == 1  # the cache answered; no second read


def test_global_refused_is_available_false() -> None:
    transport = CountingTransport(global_data=None)
    client = _client(transport)
    response = client.get("/market/crypto/global")
    assert response.status_code == 200
    assert response.json()["available"] is False


def test_coin_detail_publishes_the_gecko_record() -> None:
    record = {
        "id": "ethereum",
        "name": "Ethereum",
        "symbol": "eth",
        "image": {"large": "https://img/eth.png", "thumb": "https://img/eth-t.png"},
        "description": {"en": "Ethereum is a smart contract platform."},
        "links": {"homepage": ["https://ethereum.org/", "junk"], "twitter_screen_name": "ethereum"},
        "community_data": {"twitter_followers": 3_200_000},
        "categories": ["Layer 1 (L1)", "Smart Contract Platform"],
        "sentiment_votes_up_percentage": 85.9,
        "market_data": {"atl": {"usd": 0.43}},
    }
    transport = CountingTransport(coins={"ethereum": record})
    client = _client(transport)
    doc = client.get("/market/crypto/coin/ETH-USD").json()
    assert doc["available"] is True
    assert doc["name"] == "Ethereum"
    assert doc["website"] == "https://ethereum.org/"
    assert doc["twitter"] == "ethereum"
    assert doc["twitter_followers"] == 3_200_000.0
    assert doc["categories"] == ["Layer 1 (L1)", "Smart Contract Platform"]
    assert doc["atl"] == 0.43
    assert transport.coin_detail_calls == ["ethereum"]


def test_coin_detail_unknown_symbol_is_refused() -> None:
    transport = CountingTransport()
    client = _client(transport)
    doc = client.get("/market/crypto/coin/NOPE-USD").json()
    assert doc["available"] is False
    assert transport.coin_detail_calls == []  # no id, no read attempted


def test_coin_detail_caches_once_per_ttl() -> None:
    transport = CountingTransport(coins={"bitcoin": {"name": "Bitcoin", "id": "bitcoin"}})
    client = _client(transport)
    first = client.get("/market/crypto/coin/BTC-USD")
    assert first.headers["X-Enrich-Cache"] == "miss"
    again = client.get("/market/crypto/coin/BTC-USD")
    assert again.headers["X-Enrich-Cache"] == "hit"
    assert transport.coin_detail_calls == ["bitcoin"]  # the cache answered


def test_coin_detail_refused_read_is_available_false() -> None:
    transport = CountingTransport(coins={"bitcoin": None})
    client = _client(transport)
    doc = client.get("/market/crypto/coin/BTC-USD").json()
    assert doc["available"] is False
    assert doc["id"] == "bitcoin"
