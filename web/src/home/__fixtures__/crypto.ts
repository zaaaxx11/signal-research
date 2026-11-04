/**
 * The crypto wire's fixture, over four documents: the priced rows are the alt
 * endpoint's own shape (kept small — three coins), the meta map and the global
 * pulse are the two shading documents. Tests import `altDocument` for rows and
 * may override or null the shadings; a shading that answers `null` is a source
 * that failed, never an empty market.
 */

export function altDocument(): Record<string, unknown> {
  return {
    as_of: "2026-10-31T20:00:00Z",
    class: "crypto",
    universe_total: 3,
    measured: 3,
    per: 50,
    method: "last_5m_close_vs_chart_previous_close",
    rows: [
      { symbol: "BTC-USD", name: "Bitcoin", price: 80024.02, change_pct: 3.47, volume: 40979443712.0, currency: "USD" },
      { symbol: "ETH-USD", name: "Ethereum", price: 2414.1399, change_pct: 6.26, volume: 22117713920.0, currency: "USD" },
      { symbol: "USDT-USD", name: "Tether", price: 0.999029994, change_pct: 0.04, volume: 46791970816.0, currency: "USD" },
    ],
  };
}

/** The meta map, keyed by the venue symbol the alt rows carry. */
export function metaDocument(): Record<string, Record<string, unknown>> {
  return {
    "BTC-USD": {
      market_cap: 1_573_000_000_000,
      market_cap_rank: 1,
      image: "https://img.local/btc.png",
      pct_1h: 0.12,
      pct_7d: -2.4,
      pct_30d: 8.9,
      tvl: 55e9,
    },
    "ETH-USD": {
      market_cap: 2.1e8,
      market_cap_rank: 42,
      image: "https://img.local/eth.png",
      pct_1h: 0.04,
      pct_7d: 3.1,
      pct_30d: -5.5,
      tvl: null,
    },
  };
}

export function globalDocument(): Record<string, unknown> {
  return {
    total_market_cap_usd: 3_421_000_000_000,
    btc_dominance: 54.3,
    market_cap_change_pct_24h: -1.42,
  };
}
