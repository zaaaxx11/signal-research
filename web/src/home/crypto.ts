/**
 * The dashboard's crypto read: three endpoints, one table.
 *
 * `GET /market/alt?class=crypto` carries the price facts (up to 50 rows of
 * symbol, name, price, change, volume, currency); `GET /market/crypto/meta`
 * carries what would make the table more than a price list — market cap, rank,
 * logo and the 1-hour / 7-day / 30-day moves — as a map keyed by symbol;
 * `GET /market/crypto/global` carries the market's three pulse figures. The
 * home page already owns the equities document; this section asks for its own
 * three, because a crypto read must never delay (or be delayed by) the equity
 * aggregate it sits under.
 *
 * Three rules carried over from :mod:`home`, restated where they bite here:
 *
 * * **Absent is not zero.** A missing cap, rank or move is `null`, printed as
 *   an em dash — never as `$0` or `#0`.
 * * **A row that cannot be priced is not a row.** `readAlt` already drops
 *   them; nothing here re-weaker its contract.
 * * **A field the source did not send is not a guess.** Meta records are
 *   shaded but not repaired: a symbol absent from the meta map keeps its
 *   price columns and prints em dashes where meta would have spoken.
 *
 * The join is not a merge in place: :func:`joinCrypto` builds new rows, so a
 * re-read cannot leave an old meta value behind a new alt row.
 */

import { altPath, readAlt, type AltRow } from "@/alt/alt";

/** One meta record: what the coin's identity document says, all nullable. */
export interface CryptoMetaRow {
  readonly symbol: string;
  readonly marketCap: number | null;
  readonly marketCapRank: number | null;
  /** An `http(s)` URL, or `null` when the source sent nothing linkable. */
  readonly image: string | null;
  readonly pct1h: number | null;
  readonly pct7d: number | null;
  readonly pct30d: number | null;
  readonly tvl: number | null;
}

/** The three market-wide figures the pulse strip prints. */
export interface CryptoGlobal {
  readonly totalMarketCapUsd: number | null;
  readonly btcDominance: number | null;
  readonly marketCapChangePct24h: number | null;
}

export type CryptoMetaMap = ReadonlyMap<string, CryptoMetaRow>;

/** One table row: the alt facts plus whatever meta answered for the symbol. */
export interface CryptoRow {
  readonly symbol: string;
  readonly name: string | null;
  readonly price: number;
  readonly changePct: number | null;
  readonly volume: number | null;
  readonly currency: string | null;
  readonly marketCap: number | null;
  readonly marketCapRank: number | null;
  readonly image: string | null;
  readonly pct1h: number | null;
  readonly pct7d: number | null;
  readonly pct30d: number | null;
  readonly tvl: number | null;
}

/* ---------------------------------------------------------------------------
 * Paths. Root-absolute, same-origin, like every other read in the shell.
 * ------------------------------------------------------------------------ */

/** The priced rows. Reuses the alt page's own URL and budget (50). */
export function cryptoAltPath(): string {
  return altPath("crypto");
}

/** The identity map. A map, not an array: symbols look rows up by name. */
export function cryptoMetaPath(): string {
  return "/market/crypto/meta";
}

/** The market's pulse: total cap, BTC dominance, the day's move. */
export function cryptoGlobalPath(): string {
  return "/market/crypto/global";
}

/* ---------------------------------------------------------------------------
 * Parsing.
 * ------------------------------------------------------------------------ */

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** A finite number, or `null` — numeric strings accepted, `""`/NaN are not. */
function num(value: unknown): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value === "string" && value.trim().length > 0) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function text(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : null;
}

/** `https://…` or `http://…` only: whatever else the field carried is not a logo. */
function safeImageUrl(value: unknown): string | null {
  const url = text(value);
  if (url === null) return null;
  try {
    const parsed = new URL(url);
    if (parsed.protocol !== "https:" && parsed.protocol !== "http:") return null;
    return url;
  } catch {
    return null;
  }
}

/**
 * The key a meta row claims. The wire keys the map however its source does:
 * `BTC-USD` (the alt symbol) and `BTC` (the coin id) are both accepted, but
 * whatever it claims must be a plain string — a non-string key is dropped.
 */
function metaKey(value: unknown): string | null {
  return text(value);
}

function firstNumber(raw: Record<string, unknown>, keys: readonly string[]): number | null {
  for (const key of keys) {
    const value = num(raw[key]);
    if (value !== null) return value;
  }
  return null;
}

function firstText(raw: Record<string, unknown>, keys: readonly string[]): string | null {
  for (const key of keys) {
    const value = text(raw[key]);
    if (value !== null) return value;
  }
  return null;
}

function readMetaRow(key: string, raw: unknown): CryptoMetaRow | null {
  if (!isRecord(raw)) return null;
  return {
    symbol: key,
    marketCap: firstNumber(raw, ["market_cap", "marketCap", "cap"]),
    marketCapRank: firstNumber(raw, ["market_cap_rank", "marketCapRank", "rank"]),
    image: safeImageUrl(firstText(raw, ["image", "logo", "logo_url"])),
    pct1h: firstNumber(raw, ["pct_1h", "pct1h", "change_1h"]),
    pct7d: firstNumber(raw, ["pct_7d", "pct7d", "change_7d"]),
    pct30d: firstNumber(raw, ["pct_30d", "pct30d", "change_30d"]),
    tvl: firstNumber(raw, ["tvl", "total_value_locked"]),
  };
}

/**
 * The meta map, whatever the wire sent.
 *
 * An object keyed by symbol is the contract; an array of rows carries its own
 * `symbol` and is accepted the same way, because both forms describe the same
 * fact. Entries with no readable key, or a symbol that prices under both
 * spellings, keep the first: the map's own order decides, and one symbol gets
 * exactly one row's worth of meta.
 */
export function readCryptoMeta(body: unknown): CryptoMetaMap {
  const map = new Map<string, CryptoMetaRow>();

  if (isRecord(body)) {
    for (const [key, value] of Object.entries(body)) {
      const clean = metaKey(key);
      if (clean === null || map.has(clean)) continue;
      const row = readMetaRow(clean, value);
      if (row !== null) map.set(clean, row);
    }
    return map;
  }

  if (Array.isArray(body)) {
    for (const entry of body) {
      if (!isRecord(entry)) continue;
      const clean = firstText(entry, ["symbol", "id", "coin"]);
      if (clean === null || map.has(clean)) continue;
      const row = readMetaRow(clean, entry);
      if (row !== null) map.set(clean, row);
    }
  }

  return map;
}

export function readCryptoGlobal(body: unknown): CryptoGlobal {
  if (!isRecord(body)) {
    return { totalMarketCapUsd: null, btcDominance: null, marketCapChangePct24h: null };
  }
  return {
    totalMarketCapUsd: firstNumber(body, ["total_market_cap_usd", "total_market_cap_usdt", "totalMarketCapUsd"]),
    btcDominance: firstNumber(body, ["btc_dominance", "btcDominance"]),
    marketCapChangePct24h: firstNumber(body, [
      "market_cap_change_pct_24h",
      "marketCapChangePct24h",
      "market_cap_change_pct_24hr",
    ]),
  };
}

/**
 * The lookup a row uses: its own symbol first (`BTC-USD`), then the bare
 * base (`BTC`), because a meta map keyed by coin id and a price stream keyed
 * by venue pair describe the same asset in two spellings.
 */
export function metaFor(meta: CryptoMetaMap, symbol: string): CryptoMetaRow | null {
  return meta.get(symbol) ?? meta.get(symbol.split("-")[0] ?? "") ?? null;
}

/** The table's rows: alt's price facts, shaded with whatever meta answered. */
export function joinCrypto(
  rows: readonly AltRow[],
  meta: CryptoMetaMap,
): readonly CryptoRow[] {
  return rows.map((alt) => {
    const shading = metaFor(meta, alt.symbol);
    return {
      symbol: alt.symbol,
      name: alt.name,
      price: alt.price,
      changePct: alt.changePct,
      volume: alt.volume,
      currency: alt.currency,
      marketCap: shading?.marketCap ?? null,
      marketCapRank: shading?.marketCapRank ?? null,
      image: shading?.image ?? null,
      pct1h: shading?.pct1h ?? null,
      pct7d: shading?.pct7d ?? null,
      pct30d: shading?.pct30d ?? null,
      tvl: shading?.tvl ?? null,
    };
  });
}

/** How many symbols the meta map echoed for one alt document. */
export function metaMatches(rows: readonly AltRow[], meta: CryptoMetaMap): number {
  let count = 0;
  for (const row of rows) {
    if (metaFor(meta, row.symbol) !== null) count += 1;
  }
  return count;
}

/**
 * `readAlt` with this module's symbol: the same parser, its errors carrying a
 * name the page can explain ("the crypto rows"), kept as one call so the two
 * modules cannot drift into two alt contracts.
 */
export { readAlt };
