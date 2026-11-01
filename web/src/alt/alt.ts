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
