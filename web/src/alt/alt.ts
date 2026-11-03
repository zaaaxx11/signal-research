/**
 * The three alternative markets, as one table: crypto, futures and forex.
 *
 * `GET /market/alt?class=…&per=50` answers a single document — `as_of`, the
 * class, the universe's true size, how many symbols it could measure, and up to
 * `per` rows. This module owns everything that can be decided without a DOM:
 * the URL, the parse, the filter, the sort and the strings. The page owns the
 * pixels.
 *
 * Four rules, each one the opposite of the convenient default:
 *
 * * **A row that cannot be priced is not a row.** `price: null` is dropped, and
 *   so is a negative, infinite or NaN price. Printed as `0.00` it would be a
 *   claim about the asset that the endpoint never made. The row is absent from
 *   the table, and the count line says how many of the universe were measured.
 * * **A missing name is not a missing row.** The symbol is the identity; the
 *   name is a convenience. `name: null` renders the symbol in the name cell
 *   rather than an empty column, and the filter still matches the symbol.
 * * **The sort is a total order.** Every comparison falls back to the symbol,
 *   so two equal changes land in the same order on every render, and a null
 *   change sorts last in both directions — it is not "smallest".
 * * **Filter first, then sort.** A sort that ran before the filter could be
 *   decided by a row the reader cannot see.
 */

import { formatCompact, formatMoney } from "@/grid/format";
import type { ColumnSpec } from "@/grid/columnMeta";

/** The three classes the endpoint understands. */
export const MARKET_CLASSES = ["crypto", "futures", "forex"] as const;

export type MarketClass = (typeof MARKET_CLASSES)[number];

/** The page size: one screen of rows, and the endpoint's own cap. */
export const ALT_PER = 50;

/** One priced row. `price` is a number here because unpriced rows never arrive. */
export interface AltRow {
  readonly symbol: string;
  readonly name: string | null;
  readonly price: number;
  /** Percent, already multiplied: `0.26` is +0.26%. Null when the venue sent none. */
  readonly changePct: number | null;
  readonly volume: number | null;
  readonly currency: string | null;
}

export interface AltReport {
  readonly asOf: string;
  readonly class: MarketClass;
  /** How many symbols the class has, measured or not. */
  readonly universeTotal: number | null;
  /** How many of them the endpoint could price. */
  readonly measured: number | null;
  readonly per: number | null;
  readonly rows: readonly AltRow[];
}

/** A 200 whose body is not the document this page can read. */
export class AltMalformed extends Error {
  constructor(message = "The alt payload could not be read") {
    super(message);
    this.name = "AltMalformed";
  }
}

/** The one URL this surface reads. Same-origin, root-absolute, class explicit. */
export function altPath(marketClass: MarketClass): string {
  return `/market/alt?class=${marketClass}&per=${ALT_PER}`;
}

function numberOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/**
 * A price the table may print: finite and strictly positive.
 *
 * Zero is excluded with the negatives: no asset in these classes quotes at
 * zero, so a `0` is a broken read rather than a price, and printing it would
 * say the asset is worthless.
 */
function priceOrNull(value: unknown): number | null {
  if (typeof value !== "number" || !Number.isFinite(value) || value <= 0) return null;
  return value;
}

function textOrNull(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : null;
}

/**
 * Read one document. Rows are validated one at a time and the useless ones are
 * dropped rather than repaired: a page that draws 39 true rows is a better
 * answer than one that draws 40 with a fabricated zero.
 */
export function readAlt(body: unknown, marketClass: MarketClass): AltReport {
  if (typeof body !== "object" || body === null || Array.isArray(body)) {
    throw new AltMalformed();
  }
  const record = body as Record<string, unknown>;
  const rows = record["rows"];
  if (!Array.isArray(rows)) throw new AltMalformed();

  const seen = new Set<string>();
  const parsed: AltRow[] = [];

  for (const candidate of rows) {
    if (typeof candidate !== "object" || candidate === null) continue;
    const row = candidate as Record<string, unknown>;
    const symbol = textOrNull(row["symbol"]);
    const price = priceOrNull(row["price"]);
    if (symbol === null || price === null) continue;

    const key = symbol.toUpperCase();
    if (seen.has(key)) continue;
    seen.add(key);
    parsed.push({
      symbol: key,
      name: textOrNull(row["name"]),
      price,
      changePct: numberOrNull(row["change_pct"]),
      // A volume of 0 is a real answer (nothing traded); a negative one is not.
      volume: (() => {
        const volume = numberOrNull(row["volume"]);
        return volume !== null && volume >= 0 ? volume : null;
      })(),
      currency: textOrNull(row["currency"]),
    });
  }

  const asOf = typeof record["as_of"] === "string" ? record["as_of"] : "";

  return {
    asOf,
    class: marketClass,
    universeTotal: numberOrNull(record["universe_total"]),
    measured: numberOrNull(record["measured"]),
    per: numberOrNull(record["per"]),
    rows: parsed,
  };
}

/* ---------------------------------------------------------------------------
 * Filter
 * ------------------------------------------------------------------------ */

/**
 * Narrow by symbol or name, case-insensitively. The name falls back to the
 * symbol exactly as the cell does, so what a reader sees is what the box
 * matches.
 */
export function filterRows(rows: readonly AltRow[], query: string): AltRow[] {
  const needle = query.trim().toLowerCase();
  if (needle.length === 0) return [...rows];
  return rows.filter((row) => {
    const name = row.name ?? row.symbol;
    return (
      row.symbol.toLowerCase().includes(needle) || name.toLowerCase().includes(needle)
    );
  });
}

/* ---------------------------------------------------------------------------
 * Sort
 * ------------------------------------------------------------------------ */

export type SortKey = "symbol" | "price" | "change";
export type SortDir = "asc" | "desc";

export interface SortState {
  readonly key: SortKey;
  readonly dir: SortDir;
}

/**
 * The direction a column opens in: largest first for a figure a reader scans
 * for its extremes, A→Z for text.
 */
const OPENING_DIR: Readonly<Record<SortKey, SortDir>> = {
  symbol: "asc",
  price: "desc",
  change: "desc",
};

/** Clicking the active column flips it; a new column opens in its own direction. */
export function nextSort(current: SortState | null, key: SortKey): SortState {
  if (current !== null && current.key === key) {
    return { key, dir: current.dir === "asc" ? "desc" : "asc" };
  }
  return { key, dir: OPENING_DIR[key] };
}

/** Code-point order: stable across machines and ICU versions, unlike `localeCompare`. */
function compareSymbol(a: string, b: string): number {
  if (a < b) return -1;
  if (a > b) return 1;
  return 0;
}

function valueOf(row: AltRow, key: SortKey): number | string | null {
  if (key === "price") return row.price;
  if (key === "change") return row.changePct;
  return row.symbol;
}

/**
 * Sort a copy, with the symbol as the final tie-break so the order is total.
 * An absent value is last in both directions: "no reading" is not a magnitude,
 * and putting it first under `asc` would make a missing change look like the
 * strongest fall.
 */
export function sortRows(rows: readonly AltRow[], sort: SortState | null): AltRow[] {
  const copy = [...rows];
  if (sort === null) return copy;

  const { key, dir } = sort;
  const sign = dir === "asc" ? 1 : -1;

  copy.sort((a, b) => {
    const left = valueOf(a, key);
    const right = valueOf(b, key);

    if (left === null && right === null) return compareSymbol(a.symbol, b.symbol);
    if (left === null) return 1;
    if (right === null) return -1;

    if (typeof left === "number" && typeof right === "number") {
      if (left !== right) return left < right ? -sign : sign;
    } else if (typeof left === "string" && typeof right === "string") {
      const order = compareSymbol(left, right);
      if (order !== 0) return order * sign;
    }

    return compareSymbol(a.symbol, b.symbol);
  });

  return copy;
}

/** The table's rows: filtered, then sorted. One function so the two cannot drift. */
export function visibleRows(
  rows: readonly AltRow[],
  query: string,
  sort: SortState | null,
): AltRow[] {
  return sortRows(filterRows(rows, query), sort);
}

/** Narrow by category, then filter by text, then sort. One pipeline for both views. */
export function categoryRows(
  rows: readonly AltRow[],
  marketClass: MarketClass,
  category: string,
  query: string,
  sort: SortState | null,
): AltRow[] {
  const narrowed =
    category === "all" ? [...rows] : rows.filter((row) => categoryOf(row, marketClass) === category);
  return sortRows(filterRows(narrowed, query), sort);
}

/* ---------------------------------------------------------------------------
 * Categories
 * ------------------------------------------------------------------------ */

export interface CategoryTab {
  readonly id: string;
  readonly label: string;
}

const FUTURES_TABS: readonly CategoryTab[] = [
  { id: "all", label: "All" },
  { id: "indices", label: "Indices" },
  { id: "rates", label: "Rates" },
  { id: "energy", label: "Energy" },
  { id: "metals", label: "Metals" },
  { id: "grains", label: "Grains" },
  { id: "softs", label: "Softs" },
  { id: "currencies", label: "Currencies" },
];

const FOREX_TABS: readonly CategoryTab[] = [
  { id: "all", label: "All" },
  { id: "majors", label: "Majors" },
  { id: "crosses", label: "Crosses" },
];

const CRYPTO_TABS: readonly CategoryTab[] = [
  { id: "all", label: "All" },
  { id: "usd", label: "USD" },
  { id: "usdt", label: "USDT" },
  { id: "eur", label: "EUR" },
  { id: "btc", label: "BTC" },
];

/** The tab bar's contents for each class. */
export const CATEGORY_TABS: Readonly<Record<MarketClass, readonly CategoryTab[]>> = {
  futures: FUTURES_TABS,
  forex: FOREX_TABS,
  crypto: CRYPTO_TABS,
};

/**
 * Futures root → category, which is what the tab bar groups by.
 *
 * The groups are the CME/CBOT/Bot complex the Yahoo roots belong to: equity
 * indices, interest rates, energy, metals, grains, softs and currency futures.
 * A root not in the table is *not* filed under a guess — it simply does not
 * appear under a named tab, and `categoryCounts` reports how many rows are
 * uncategorised so the bar can never claim to be complete.
 *
 * The lookup is by longest matching prefix, so a dated contract (`ESZ26=F`)
 * files under the same category as the continuous front (`ES=F`).
 */
const FUTURES_ROOTS: Readonly<Record<string, string>> = {
  // Equity index
  ES: "indices",
  NQ: "indices",
  YM: "indices",
  RTY: "indices",
  EMD: "indices",
  NKD: "indices",
  FDAX: "indices",
  FTSE: "indices",
  FESX: "indices",
  HSI: "indices",
  ER: "indices",
  // Interest rates
  ZB: "rates",
  ZN: "rates",
  ZF: "rates",
  ZT: "rates",
  ZQ: "rates",
  ZT_F: "rates",
  UB: "rates",
  TN: "rates",
  FF: "rates",
  GE: "rates",
  TWE: "rates",
  KB: "rates",
  DX: "rates",
  // Energy
  CL: "energy",
  BZ: "energy",
  NG: "energy",
  RB: "energy",
  HO: "energy",
  EH: "energy",
  QL: "energy",
  // Metals
  GC: "metals",
  SI: "metals",
  HG: "metals",
  PL: "metals",
  PA: "metals",
  MGC: "metals",
  // Grains and oilseeds
  ZC: "grains",
  ZS: "grains",
  ZW: "grains",
  ZL: "grains",
  ZM: "grains",
  ZR: "grains",
  ZO: "grains",
  // Softs
  SB: "softs",
  KC: "softs",
  CT: "softs",
  CC: "softs",
  LE: "softs",
  OJ: "softs",
  LBS: "softs",
  // Currency futures
  "6E": "currencies",
  "6J": "currencies",
  "6B": "currencies",
  "6A": "currencies",
  "6C": "currencies",
  "6S": "currencies",
  "6N": "currencies",
  "6M": "currencies",
  "6L": "currencies",
  "6R": "currencies",
  "6Z": "currencies",
  "6J_F": "currencies",
};

/** The roots sorted longest-first, so `FDAX` beats a shorter prefix. */
const ROOT_PREFIXES: readonly string[] = Object.keys(FUTURES_ROOTS).sort(
  (a, b) => b.length - a.length,
);

/**
 * `ES=F` → `indices`; `ESZ26=F` → `indices`; `GC=F` → `metals`.
 *
 * The `=F` suffix is Yahoo's continuous-future marker. A symbol without it is
 * not a futures root and belongs to no category.
 */
function futuresCategory(symbol: string): string | null {
  if (!symbol.endsWith("=F")) return null;
  const root = symbol.slice(0, -2).toUpperCase();
  for (const prefix of ROOT_PREFIXES) {
    if (root === prefix || root.startsWith(prefix)) return FUTURES_ROOTS[prefix] ?? null;
  }
  return null;
}

/** `EURUSD=X` → `USD`; `BTC-USD` → `USD`; `BTCUSDT` → `USDT`. */
const KNOWN_QUOTES: readonly string[] = [
  "USDT",
  "USDC",
  "USD",
  "EUR",
  "BTC",
  "ETH",
  "GBP",
  "JPY",
  "AUD",
  "CAD",
  "CHF",
  "NZD",
  "CNY",
];

function quoteCurrency(symbol: string): string | null {
  const upper = symbol.toUpperCase();
  const tail = upper.includes("-")
    ? upper.slice(upper.lastIndexOf("-") + 1)
    : upper.includes("=X")
      ? upper.slice(0, -2)
      : upper;

  for (const quote of KNOWN_QUOTES) {
    if (tail.endsWith(quote)) return quote;
  }
  return null;
}

/** Fiat majors quoted against the dollar. Everything else is a cross. */
// Reserved for a future "majors" filter; not referenced today.

/**
 * Which tab a row belongs under.
 *
 * Futures: the ticker's root on the left of the `=F` suffix, e.g. `ES=F` →
 * `indices`. A root that is in no group belongs to none of the named tabs; it
 * stays visible under `all` and is counted as uncategorised rather than being
 * filed under a guess.
 *
 * Crypto: the symbol's quote leg.
 *
 * Forex: a four-or-six-letter pair of known fiat codes is a cross, anything
 * else with `=X` is a major.
 */
export function categoryOf(row: AltRow, marketClass: MarketClass): string | null {
  const symbol = row.symbol.toUpperCase();

  if (marketClass === "futures") {
    return futuresCategory(symbol);
  }

  if (marketClass === "crypto") {
    return quoteCurrency(symbol)?.toLowerCase() ?? null;
  }

  return null;
}

/**
 * How many rows land under each tab, and how many match no tab at all.
 *
 * The tab bar prints its own count per tab so an empty category reads as empty
 * rather than as a broken page, and `uncategorised` keeps the arithmetic honest:
 * the tab counts plus it equal `rows.length`.
 */
export interface CategoryCounts {
  readonly counts: Readonly<Record<string, number>>;
  readonly uncategorised: number;
  readonly total: number;
}

export function categoryCounts(
  rows: readonly AltRow[],
  marketClass: MarketClass,
): CategoryCounts {
  const counts: Record<string, number> = {};
  let uncategorised = 0;

  for (const row of rows) {
    const category = categoryOf(row, marketClass);
    if (category === null) uncategorised += 1;
    else counts[category] = (counts[category] ?? 0) + 1;
  }

  return { counts, uncategorised, total: rows.length };
}

/* ---------------------------------------------------------------------------
 * Forex performance — currency strength vs the dollar
 * ------------------------------------------------------------------------ */

/** One bar of the forex strength chart. */
export interface PerfEntry {
  /** The base currency, e.g. `EUR` from `EURUSD=X`. */
  readonly code: string;
  /** Percent the pair moved; positive means the base beat the USD leg. */
  readonly changePct: number;
}

/** `EURUSD=X` → `EUR`; `JPY=X` → `JPY`; anything else → null. */
function forexBase(symbol: string): string | null {
  if (!symbol.endsWith("=X")) return null;
  const pair = symbol.slice(0, -2).toUpperCase();
  if (pair.length !== 6 && pair.length !== 3) return null;
  // `EURUSD` → the base is the first three letters. `JPY` alone is USD/JPY's
  // inverse form and is skipped: its change is already the dollar's own move.
  if (pair.length === 6) {
    const base = pair.slice(0, 3);
    const quote = pair.slice(3);
    return quote === "USD" ? base : null;
  }
  return null;
}

/**
 * Bars for the forex chart: every pair quoted *against* the dollar, so one
 * common denominator and no normalisation fudge.
 *
 * Pairs not quoted in USD (a cross such as `EURGBP=X`) do not appear: their
 * move mixes two currencies and would read as a dollar reading it is not. A row
 * with no `change_pct` is skipped too — a bar cannot be drawn for an absent
 * magnitude, and treating it as flat would put it on the axis as a claim.
 *
 * Sorted strongest first so the chart reads top-down.
 */
export function forexStrength(rows: readonly AltRow[]): PerfEntry[] {
  const entries: PerfEntry[] = [];
  const seen = new Set<string>();

  for (const row of rows) {
    if (row.changePct === null) continue;
    const base = forexBase(row.symbol);
    if (base === null || seen.has(base)) continue;
    seen.add(base);
    entries.push({ code: base, changePct: row.changePct });
  }

  entries.sort((a, b) => {
    if (a.changePct !== b.changePct) return b.changePct - a.changePct;
    return compareSymbol(a.code, b.code);
  });

  return entries;
}

/**
 * The chart's symmetric scale: half-width = the largest absolute move.
 *
 * Returning the magnitude rather than a percentage per bar keeps the axis in
 * one place, so a +0.4% bar and a −0.4% bar are the same length on either side.
 * A chart with no bars has scale 0 and draws no bar widths at all.
 */
export function strengthScale(entries: readonly PerfEntry[]): number {
  let peak = 0;
  for (const entry of entries) {
    const magnitude = Math.abs(entry.changePct);
    if (magnitude > peak) peak = magnitude;
  }
  return peak;
}

/* ---------------------------------------------------------------------------
 * Formatting
 * ------------------------------------------------------------------------ */

/**
 * Decimal places for a price.
 *
 * Prices are quoted at the precision where their significant digits are: a
 * dollar pair needs four, a large index two, and a token at $0.0000099 needs
 * eight — `<$0.0001` is true and useless when most of a class trades below a
 * cent, and `$0.00` would be false.
 */
export function pricePrecision(value: number): number {
  const magnitude = Math.abs(value);
  if (magnitude >= 10) return 2;
  if (magnitude >= 0.01) return 4;
  if (magnitude >= 0.0001) return 6;
  return 8;
}

/** `$1.1166`, `$77,399.29`; a sub-precision magnitude prints `<$0.0001`. */
export function formatPrice(value: number): string {
  return formatMoney(value, false, pricePrecision(value));
}

/**
 * `12.42B`; an absent volume stays absent so the cell can say so.
 *
 * Uses the grid's cached formatter rather than building one: a 40-row table
 * would otherwise construct 40 `Intl.NumberFormat` objects per render.
 */
export function formatVolume(value: number | null): string | null {
  if (value === null || !Number.isFinite(value)) return null;
  return formatCompact(value);
}

const READ_AT = new Intl.DateTimeFormat("en-GB", {
  day: "numeric",
  month: "short",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
  timeZone: "UTC",
});

/**
 * `2026-10-31T23:40:00Z` → `31 Oct 2026, 23:40 UTC`.
 *
 * UTC, spelled out: the markets close on exchange clocks, and a reader
 * comparing a US close with a Tokyo open needs one zone for both. An
 * unparseable stamp returns null rather than "Invalid Date" — the footer then
 * says it does not know when the data was read.
 */
export function formatReadAt(iso: string): string | null {
  if (iso.trim().length === 0) return null;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return null;

  const parts = new Map(
    READ_AT.formatToParts(date).map((part) => [part.type, part.value] as const),
  );
  const day = parts.get("day");
  const month = parts.get("month");
  const year = parts.get("year");
  const hour = parts.get("hour");
  const minute = parts.get("minute");
  if (!day || !month || !year || !hour || !minute) return null;

  return `${day} ${month} ${year}, ${hour}:${minute} UTC`;
}

/* ---------------------------------------------------------------------------
 * Copy
 * ------------------------------------------------------------------------ */

export interface ClassCopy {
  /** The page's title. */
  readonly title: string;
  /** One line: what the class is, and how its price is quoted. */
  readonly blurb: string;
  /** The amount column's header — futures quote contracts, not shares. */
  readonly amountLabel: string;
  /**
   * Whether the class reports an amount at all.
   *
   * Spot forex does not: there is no consolidated tape, so the quote stack's
   * `volume` is a sentinel rather than a count and printing it would invent a
   * measurement. The column stays (the table has the same shape everywhere)
   * and every cell reads "—".
   */
  readonly hasAmount: boolean;
}

/**
 * The three descriptions. Each one answers the same two questions in a
 * sentence — what is being priced, and in what unit — and none of them says
 * "live": the endpoint carries the last session's price for a market that is
 * closed, and the footer says so.
 */
const COPY: Readonly<Record<MarketClass, ClassCopy>> = {
  crypto: {
    title: "Crypto",
    blurb:
      "Digital assets, quoted in US dollars around the clock; each price is the latest trade the quote stack could read.",
    amountLabel: "Volume",
    hasAmount: true,
  },
  futures: {
    title: "Futures",
    blurb:
      "Exchange-traded contracts for a future delivery date, quoted per contract in US dollars; volume counts contracts traded.",
    amountLabel: "Contract",
    hasAmount: true,
  },
  forex: {
    title: "Forex",
    blurb:
      "Currency pairs, quoted as units of the quote currency per one unit of the base, from Sunday evening to Friday evening UTC.",
    amountLabel: "Volume",
    hasAmount: false,
  },
};

export function classCopy(marketClass: MarketClass): ClassCopy {
  return COPY[marketClass];
}

/* ---------------------------------------------------------------------------
 * Links
 * ------------------------------------------------------------------------ */

/**
 * Whether a row's symbol has a page to open.
 *
 * `/stock/{symbol}` is the *equity* detail page: it mounts the panel that reads
 * `/engine/fundamentals/{symbol}`, and that route resolves only symbols in the
 * loaded identity master. Measured against the running deployment:
 *
 *     GET /engine/fundamentals/BTC-USD   → 404 security_not_found
 *     GET /engine/fundamentals/ES=F      → 404 security_not_found
 *     GET /engine/fundamentals/EURUSD=X  → 404 security_not_found
 *
 * The shell's route pattern also refuses `=` in a path segment, so `ES=F` and
 * `EURUSD=X` cannot even be addressed. A row here is therefore plain text: a
 * link that lands on "not found" is worse than no link. When the detail path
 * learns these classes, flip this to `true` and every row becomes an anchor —
 * the URL is already built by :func:`stockHref`.
 */
export const ALT_SYMBOLS_LINKABLE = false;

/** The detail page's URL for a row, or null while there is no page to open. */
export function stockHref(symbol: string): string | null {
  if (!ALT_SYMBOLS_LINKABLE) return null;
  return `/stock/${encodeURIComponent(symbol)}`;
}

/* ---------------------------------------------------------------------------
 * Cell specs, borrowed from the grid
 * ------------------------------------------------------------------------ */

/** The change column: a `delta` spec buys the arrow, the sign and the spoken word. */
export const CHANGE_SPEC: ColumnSpec = {
  key: "change_pct",
  label: "Change",
  kind: "delta",
  weight: 1,
  min: 64,
  precision: 2,
};
