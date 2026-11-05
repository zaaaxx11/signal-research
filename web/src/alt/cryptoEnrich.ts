/**
 * Types, fetchers, and formatters for the crypto enrichment layer:
 * `/market/crypto/meta` (cap/rank/logo/7d/TVL keyed by Yahoo symbol),
 * `/market/crypto/global` (total cap, BTC dominance, 24h change, active coins),
 * and `/market/chart/{symbol}?range=5d` (mini sparkline bars).
 *
 * Every field is nullable and every fetcher survives a refused read:
 * the table renders from Yahoo alone, and the pulse strip says "unavailable".
 */

import { getJson } from "@/lib/api";
import { formatCompact } from "@/grid/format";

/* -----------------------------------------------------------------------
 * Meta — one symbol's universe from CoinGecko + DeFiLlama
 * --------------------------------------------------------------------- */

export interface CryptoMetaRow {
  readonly symbol: string;
  readonly id: string;
  readonly name: string | null;
  readonly image: string | null;
  readonly market_cap: number | null;
  readonly market_cap_rank: number | null;
  readonly circulating_supply: number | null;
  readonly max_supply: number | null;
  readonly total_supply: number | null;
  readonly fully_diluted_valuation: number | null;
  readonly high_24h: number | null;
  readonly low_24h: number | null;
  readonly ath: number | null;
  readonly ath_change_pct: number | null;
  readonly pct_1h: number | null;
  readonly pct_7d: number | null;
  readonly pct_30d: number | null;
  readonly tvl: number | null;
  /** Which sources answered: "coingecko" alone, or ["coingecko", "defillama"]. */
  readonly sources: readonly string[];
}

export interface CryptoMetaDoc {
  readonly as_of: string;
  readonly method: string;
  readonly universe_total: number;
  readonly measured: number;
  /** Keyed by Yahoo symbol (BTC-USD). */
  readonly rows: Readonly<Record<string, CryptoMetaRow>>;
}

export interface CryptoGlobalDoc {
  readonly as_of: string;
  readonly method: string;
  readonly available: boolean;
  readonly total_market_cap_usd: number | null;
  readonly market_cap_change_pct_24h: number | null;
  readonly btc_dominance: number | null;
  readonly eth_dominance: number | null;
  readonly active_cryptocurrencies: number | null;
}

export interface EnrichData {
  readonly meta: CryptoMetaDoc | null;
  readonly global: CryptoGlobalDoc | null;
}

/* -----------------------------------------------------------------------
 * Sparkline
 * --------------------------------------------------------------------- */

export interface SparklineBar {
  /** Unix seconds. */
  readonly t: number;
  readonly c: number;
}

export interface SparklineSeries {
  readonly symbol: string;
  readonly closes: readonly number[];
  /** The sign of the first-to-last close move. */
  readonly direction: "up" | "down" | "flat";
}

/* -----------------------------------------------------------------------
 * Formatting
 * --------------------------------------------------------------------- */

/** `1_500_000_000_000` → `"$1.5T"`; `48_200_000_000` → `"$48.2B"`. */
export function formatMarketCap(value: number | null): string | null {
  if (value === null || !Number.isFinite(value)) return null;
  return `$${formatCompact(value)}`;
}

/** `58.1` → `"58.1%"` (dominance). */
export function formatDominance(value: number | null): string | null {
  if (value === null || !Number.isFinite(value)) return null;
  return `${value.toFixed(1)}%`;
}

/** A signed percent with one decimal, no leading `+` on zero. */
export function formatSignedPct(value: number | null): string | null {
  if (value === null || !Number.isFinite(value)) return null;
  if (value === 0) return "0.0%";
  return value > 0 ? `+${value.toFixed(1)}%` : `${value.toFixed(1)}%`;
}

/** `17221` → `"17,221"` (active coins). */
export function formatCount(value: number | null): string | null {
  if (value === null || !Number.isFinite(value)) return null;
  return new Intl.NumberFormat("en-US").format(value);
}

/* -----------------------------------------------------------------------
 * Fetchers
 * --------------------------------------------------------------------- */

const META_PATH = "/market/crypto/meta";
const GLOBAL_PATH = "/market/crypto/global";
const CHART_PATH_PREFIX = "/market/chart/";

/** Read the meta map and validate it into the shapes the page uses. */
export async function fetchCryptoMeta(signal?: AbortSignal): Promise<CryptoMetaDoc | null> {
  try {
    const body = await getJson(META_PATH, signal);
    if (typeof body !== "object" || body === null) return null;
    const doc = body as Record<string, unknown>;
    const rawRows = doc["rows"];
    if (typeof rawRows !== "object" || rawRows === null) return null;

    const rows: Record<string, CryptoMetaRow> = {};
    for (const [symbol, entry] of Object.entries(rawRows as Record<string, unknown>)) {
      if (typeof entry !== "object" || entry === null) continue;
      const row = entry as Record<string, unknown>;
      rows[symbol] = {
        symbol: typeof row["symbol"] === "string" ? row["symbol"] : symbol,
        id: typeof row["id"] === "string" ? row["id"] : "",
        name: typeof row["name"] === "string" ? row["name"] : null,
        image: typeof row["image"] === "string" ? row["image"] : null,
        market_cap: safeNumber(row["market_cap"]),
        market_cap_rank: safeInt(row["market_cap_rank"]),
        circulating_supply: safeNumber(row["circulating_supply"]),
        max_supply: safeNumber(row["max_supply"]),
        total_supply: safeNumber(row["total_supply"]),
        fully_diluted_valuation: safeNumber(row["fully_diluted_valuation"]),
        high_24h: safeNumber(row["high_24h"]),
        low_24h: safeNumber(row["low_24h"]),
        ath: safeNumber(row["ath"]),
        ath_change_pct: safeNumber(row["ath_change_pct"]),
        pct_1h: safeNumber(row["pct_1h"]),
        pct_7d: safeNumber(row["pct_7d"]),
        pct_30d: safeNumber(row["pct_30d"]),
        tvl: safeNumber(row["tvl"]),
        sources: Array.isArray(row["sources"]) ? row["sources"] as string[] : [],
      };
    }

    const measured = safeInt(doc["measured"]);
    return {
      as_of: typeof doc["as_of"] === "string" ? doc["as_of"] : "",
      method: typeof doc["method"] === "string" ? doc["method"] : "",
      universe_total: safeInt(doc["universe_total"]) ?? Object.keys(rows).length,
      measured: measured ?? Object.keys(rows).length,
      rows,
    };
  } catch {
    return null;
  }
}

export async function fetchCryptoGlobal(signal?: AbortSignal): Promise<CryptoGlobalDoc | null> {
  try {
    const body = await getJson(GLOBAL_PATH, signal);
    if (typeof body !== "object" || body === null) return null;
    const doc = body as Record<string, unknown>;

    if (doc["available"] === false) {
      return {
        as_of: typeof doc["as_of"] === "string" ? doc["as_of"] : "",
        method: typeof doc["method"] === "string" ? doc["method"] : "",
        available: false,
        total_market_cap_usd: null,
        market_cap_change_pct_24h: null,
        btc_dominance: null,
        eth_dominance: null,
        active_cryptocurrencies: null,
      };
    }

    return {
      as_of: typeof doc["as_of"] === "string" ? doc["as_of"] : "",
      method: typeof doc["method"] === "string" ? doc["method"] : "",
      available: true,
      total_market_cap_usd: safeNumber(doc["total_market_cap_usd"]),
      market_cap_change_pct_24h: safeNumber(doc["market_cap_change_pct_24h"]),
      btc_dominance: safeNumber(doc["btc_dominance"]),
      eth_dominance: safeNumber(doc["eth_dominance"]),
      active_cryptocurrencies: safeInt(doc["active_cryptocurrencies"]),
    };
  } catch {
    return null;
  }
}

/** Fetch the chart bars for one symbol and reduce to a sparkline series. */
export async function fetchSparkline(
  symbol: string,
  signal?: AbortSignal,
): Promise<SparklineSeries | null> {
  try {
    const url = `${CHART_PATH_PREFIX}${encodeURIComponent(symbol)}?range=5d`;
    const body = await getJson(url, signal);
    if (typeof body !== "object" || body === null) return null;
    const doc = body as Record<string, unknown>;
    const bars = doc["bars"];
    if (!Array.isArray(bars) || bars.length === 0) return null;

    const closes: number[] = [];
    for (const bar of bars) {
      if (typeof bar !== "object" || bar === null) continue;
      const c = (bar as Record<string, unknown>)["c"];
      if (typeof c === "number" && Number.isFinite(c) && c > 0) {
        closes.push(c);
      }
    }
    if (closes.length < 2) return null;

    const direction: SparklineSeries["direction"] =
      closes[closes.length - 1]! > closes[0]! ? "up"
      : closes[closes.length - 1]! < closes[0]! ? "down"
      : "flat";

    return { symbol, closes, direction };
  } catch {
    return null;
  }
}

/* -----------------------------------------------------------------------
 * Helpers
 * --------------------------------------------------------------------- */

function safeNumber(value: unknown): number | null {
  if (typeof value !== "number" || !Number.isFinite(value)) return null;
  return value;
}

function safeInt(value: unknown): number | null {
  if (typeof value !== "number" || !Number.isFinite(value)) return null;
  return Math.trunc(value);
}