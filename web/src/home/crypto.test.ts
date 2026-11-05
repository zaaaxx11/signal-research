import { describe, expect, it } from "vitest";

import { ALT_PER } from "@/alt/alt";
import {
  cryptoAltPath,
  cryptoGlobalPath,
  cryptoMetaPath,
  joinCrypto,
  metaFor,
  metaMatches,
  readCryptoGlobal,
  readCryptoMeta,
} from "./crypto";
import { readAlt } from "@/alt/alt";

/**
 * The crypto document's own contract, before any DOM:
 *
 * * the three paths this section asks never drift from the endpoints;
 * * a meta map's rows keep what exists and drop what does not — a cap the
 *   source did not send is `null`, not zero, and a logo that is not an
 *   `http(s)` URL is no logo;
 * * the join shades the price rows without mutating them: a re-read cannot
 *   leave a stale cap behind a fresh price.
 */
describe("crypto paths", () => {
  it("names the three endpoints one class explicit", () => {
    expect(cryptoAltPath()).toBe(`/market/alt?class=crypto&per=${ALT_PER}`);
    expect(cryptoMetaPath()).toBe("/market/crypto/meta");
    expect(cryptoGlobalPath()).toBe("/market/crypto/global");
  });
});

describe("readCryptoMeta", () => {
  it("reads the object form, keeping absent fields null", () => {
    const meta = readCryptoMeta({
      "BTC-USD": {
        market_cap: 1_573_000_000_000,
        market_cap_rank: 1,
        image: "https://example.com/btc.png",
        pct_1h: 0.12,
        pct_7d: -2.4,
        pct_30d: 8.9,
        tvl: null,
      },
    });
    const row = meta.get("BTC-USD");
    expect(row).not.toBeNull();
    expect(row?.marketCap).toBe(1_573_000_000_000);
    expect(row?.marketCapRank).toBe(1);
    expect(row?.image).toBe("https://example.com/btc.png");
    expect(row?.pct7d).toBe(-2.4);
    expect(row?.tvl).toBeNull();
  });

  it("drops a logo that is not an http(s) URL", () => {
    const meta = readCryptoMeta({
      ETH: { image: "javascript:alert(1)" },
      "USDT-USD": { image: 42 },
    });
    expect(meta.get("ETH")?.image).toBeNull();
    expect(meta.get("USDT-USD")?.image).toBeNull();
  });

  it("accepts an array of rows keyed by their own symbol", () => {
    const meta = readCryptoMeta([
      { symbol: "SOL", market_cap: 80.1e9, market_cap_rank: 5 },
    ]);
    expect(meta.get("SOL")?.marketCap).toBe(80.1e9);
    expect(meta.get("SOL")?.marketCapRank).toBe(5);
  });

  it("keeps the first entry when a symbol repeats, and skips unreadable rows", () => {
    // Array form, because only it can carry the same key twice.
    const meta = readCryptoMeta([
      { symbol: "BTC", market_cap_rank: 1 },
      { symbol: "", market_cap_rank: 99 },
      42,
      null,
      { symbol: "BTC", market_cap_rank: 2 },
      undefined,
    ] as unknown as unknown[]);
    expect(meta.get("BTC")?.marketCapRank).toBe(1);
    expect(meta.size).toBe(1);
  });

  it("reads a body that is nothing as an empty map", () => {
    expect(readCryptoMeta(null).size).toBe(0);
    expect(readCryptoMeta(42).size).toBe(0);
    expect(readCryptoMeta([null, 7, undefined]).size).toBe(0);
  });
});

describe("readCryptoGlobal", () => {
  it("reads the three pulse figures, numeric strings included", () => {
    const global = readCryptoGlobal({
      total_market_cap_usd: "3.42e12",
      btc_dominance: 54.3,
      market_cap_change_pct_24h: -1.42,
    });
    expect(global.totalMarketCapUsd).toBeCloseTo(3.42e12);
    expect(global.btcDominance).toBe(54.3);
    expect(global.marketCapChangePct24h).toBe(-1.42);
  });

  it("answers a non-object null, null and a plain string", () => {
    const empty = { totalMarketCapUsd: null, btcDominance: null, marketCapChangePct24h: null };
    expect(readCryptoGlobal(null)).toEqual(empty);
    expect(readCryptoGlobal("nothing")).toEqual(empty);
  });
});

describe("metaFor and the join", () => {
  const meta = readCryptoMeta({
    "BTC-USD": { market_cap: 1234.5, market_cap_rank: 1, image: "https://x/y.png", pct_7d: 4.2 },
    BTC: { market_cap: 999 }, // same asset, the bare coin id — shadowed in order
    ETH: { market_cap: 555 },
  });

  it("looks a row's meta up by symbol, then by the bare base", () => {
    expect(metaFor(meta, "ETH-USD")?.marketCap).toBe(555);
    expect(metaFor(meta, "SOL-USD")).toBeNull();
    expect(metaFor(meta, "BTC-USD")?.marketCapRank).toBe(1);
  });

  it("shades alt rows without mutating them", () => {
    const alt = readAlt(
      {
        rows: [
          { symbol: "BTC-USD", name: "Bitcoin", price: 80024.02, change_pct: 3.47, volume: 40.9e9, currency: "USD" },
          { symbol: "SOL-USD", name: "Solana", price: 148.6, change_pct: null, volume: null, currency: "USD" },
        ],
      },
      "crypto",
    );
    const rows = joinCrypto(alt.rows, meta);
    expect(rows).toHaveLength(2);

    const btc = rows[0];
    expect(btc.price).toBe(80024.02);
    expect(btc.marketCapRank).toBe(1);
    expect(btc.pct7d).toBe(4.2);

    const sol = rows[1];
    // A symbol meta did not answer keeps its price and prints em dashes for
    // the shading — never zeroes.
    expect(sol.marketCap).toBeNull();
    expect(sol.marketCapRank).toBeNull();
    expect(sol.changePct).toBeNull();
  });

  it("counts how many rows the meta map echoed", () => {
    const alt = readAlt(
      {
        rows: [
          { symbol: "BTC-USD", name: null, price: 1, change_pct: null, volume: null, currency: "USD" },
          { symbol: "ETH-USD", name: null, price: 2, change_pct: null, volume: null, currency: "USD" },
          { symbol: "SOL-USD", name: null, price: 3, change_pct: null, volume: null, currency: "USD" },
        ],
      },
      "crypto",
    );
    expect(metaMatches(alt.rows, meta)).toBe(2);
  });
});
