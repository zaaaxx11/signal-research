/**
 * The two lists this product keeps, and the one place that decides what a
 * stored byte string means.
 *
 * A watchlist and a portfolio are *this browser's*, not this account's: the
 * product has no accounts at all, so there is no session to attach them to and
 * no server that could answer for them. `localStorage` is the whole store. That
 * is a real limitation and the pages say it in words; this module is the part
 * that makes it true.
 *
 * Four rules, each of which has a test:
 *
 * * **A versioned key.** `ztockz.watchlist.v1` / `ztockz.portfolio.v1`. A future
 *   shape can ship beside this one instead of silently reinterpreting it.
 * * **A tolerant read.** Corrupt JSON, a non-array, a number where a symbol
 *   belongs — all read as `[]`/dropped rows. A hand-edited or half-written key
 *   must not white-screen the page; it must look like an empty list, which the
 *   empty state already explains.
 * * **A refused write is a refused row.** A quantity of 0, −2 or `ten` never
 *   reaches storage: the add path returns the reason and the page prints it next
 *   to the field. A stored row that somehow has one is dropped on read rather
 *   than priced at zero.
 * * **One notify path.** Same document: an in-process listener set, so two
 *   mounted pages agree the moment one writes. Another document: the `storage`
 *   event, which the browser fires in every *other* tab. See the note on tabs.
 *
 * Two tabs: writing in tab A updates tab B when the browser dispatches
 * `storage` — that part is handled and tested. What is deliberately not built is
 * a merge: two tabs writing the same key at the same moment means the last write
 * wins, whole-list, because the value is a list rather than a log. That is out of
 * scope for a single-user, single-browser page and it is not pretended
 * otherwise.
 */

import { MAX_QUOTE_SYMBOLS } from "./quotes";

export interface Holding {
  readonly symbol: string;
  readonly quantity: number;
  /** Average cost per share, in the quote's currency. */
  readonly cost: number;
}

export const WATCHLIST_KEY = "ztockz.watchlist.v1";
export const PORTFOLIO_KEY = "ztockz.portfolio.v1";

/** The quote endpoint's ceiling; also this list's, so a full list still asks once. */
export const MAX_SYMBOLS = MAX_QUOTE_SYMBOLS;

/** The shape every consumer reads. `read()` is stable, as React requires. */
export interface LocalStore<T> {
  readonly key: string;
  read(): T;
  write(next: T): void;
  subscribe(listener: () => void): () => void;
}

/* ------------------------------------------------------------------------- *
 * Storage access
 * ------------------------------------------------------------------------- */

/**
 * A last resort when `localStorage` is denied (private mode, a hardened
 * profile, a quota that is already full). The session keeps working; the page's
 * honesty line is still true for the reload it will not survive.
 */
const memory = new Map<string, string>();
let denied = false;

function parse(text: string | null): unknown {
  if (text === null) return undefined;
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return undefined;
  }
}

function readRaw(key: string): unknown {
  if (!denied && typeof window !== "undefined") {
    try {
      return parse(window.localStorage.getItem(key));
    } catch {
      denied = true;
    }
  }
  return parse(memory.get(key) ?? null);
}

function writeRaw(key: string, value: unknown): void {
  const text = JSON.stringify(value);
  if (!denied && typeof window !== "undefined") {
    try {
      window.localStorage.setItem(key, text);
      return;
    } catch {
      denied = true;
    }
  }
  memory.set(key, text);
}

/**
 * A typed store over one key.
 *
 * The parsed value is cached so `read()` returns the same reference until a
 * write — `useSyncExternalStore` calls it on every render and would loop on a
 * fresh array each time. Both roads into a change (a local write, another tab's
 * `storage` event) reset that cache before notifying.
 */
export function createLocalStore<T>(key: string, decode: (raw: unknown) => T): LocalStore<T> {
  let snapshot: T | undefined;
  let loaded = false;
  const listeners = new Set<() => void>();

  const read = (): T => {
    if (!loaded) {
      snapshot = decode(readRaw(key));
      loaded = true;
    }
    return snapshot as T;
  };

  const notify = (): void => {
    for (const listener of [...listeners]) listener();
  };

  const write = (next: T): void => {
    snapshot = next;
    loaded = true;
    writeRaw(key, next);
    notify();
  };

  const subscribe = (listener: () => void): (() => void) => {
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  };

  if (typeof window !== "undefined") {
    window.addEventListener("storage", (event) => {
      // `key === null` is `localStorage.clear()`: every key changed, this one
      // included. Anything else is only interesting when it is ours.
      if (event.key !== null && event.key !== key) return;
      loaded = false;
      notify();
    });
  }

  return { key, read, write, subscribe };
}

/* ------------------------------------------------------------------------- *
 * Symbols
 * ------------------------------------------------------------------------- */

/**
 * Provider tickers: `AAPL`, `BRK-B`, `BTC-USD`. Deliberately narrow — no
 * spaces, commas, semicolons or slashes — because this string is concatenated
 * into one query parameter and a separator that slips through would split one
 * symbol into two requests.
 */
const SYMBOL_PATTERN = /^[A-Z0-9][A-Z0-9.\-^=]{0,19}$/;

export function normalizeSymbol(input: string): string | null {
  const symbol = input.trim().toUpperCase();
  if (symbol.length === 0) return null;
  return SYMBOL_PATTERN.test(symbol) ? symbol : null;
}

function decodeSymbol(value: unknown): string | null {
  return typeof value === "string" ? normalizeSymbol(value) : null;
}

/** Every rule the store applies, applied again to whatever the key held. */
export function decodeWatchlist(raw: unknown): string[] {
  if (!Array.isArray(raw)) return [];
  const symbols: string[] = [];
  for (const item of raw) {
    const symbol = decodeSymbol(item);
    if (symbol === null || symbols.includes(symbol)) continue;
    if (symbols.length >= MAX_SYMBOLS) break;
    symbols.push(symbol);
  }
  return symbols;
}

export type ListEdit = { ok: true; symbols: string[] } | { ok: false; error: string };

export function addSymbol(
  list: readonly string[],
  raw: string,
  limit: number = MAX_SYMBOLS,
): ListEdit {
  const symbol = normalizeSymbol(raw);
  if (symbol === null) {
    return { ok: false, error: "Enter a ticker, such as AAPL or BTC-USD." };
  }
  if (list.includes(symbol)) {
    return { ok: false, error: `${symbol} is already on this list.` };
  }
  if (list.length >= limit) {
    return {
      ok: false,
      error: `This list holds ${limit} symbols — the most one quote request can ask for.`,
    };
  }
  return { ok: true, symbols: [...list, symbol] };
}

export function removeSymbol(list: readonly string[], symbol: string): string[] {
  return list.filter((item) => item !== symbol);
}

/**
 * `delta` is −1 for up, +1 for down. `null` means "nothing to move" — the first
 * row cannot go up — so the caller can skip a pointless write. The input array
 * is never mutated.
 */
export function moveSymbol(
  list: readonly string[],
  symbol: string,
  delta: -1 | 1,
): string[] | null {
  const index = list.indexOf(symbol);
  if (index === -1) return null;
  const target = index + delta;
  if (target < 0 || target >= list.length) return null;
  const next = [...list];
  next[index] = list[target] as string;
  next[target] = symbol;
  return next;
}

/* ------------------------------------------------------------------------- *
 * Holdings
 * ------------------------------------------------------------------------- */

/** A decimal quantity or cost: no exponent, no hex, no thousands separator. */
const DECIMAL_PATTERN = /^(?:\d+(?:\.\d*)?|\.\d+)$/;

function parseDecimal(text: string): number | null {
  const trimmed = text.trim();
  if (!DECIMAL_PATTERN.test(trimmed)) return null;
  const value = Number(trimmed);
  return Number.isFinite(value) ? value : null;
}

export function parseQuantity(text: string): number | null {
  const value = parseDecimal(text);
  return value !== null && value > 0 ? value : null;
}

export function parseCost(text: string): number | null {
  const value = parseDecimal(text);
  return value !== null && value >= 0 ? value : null;
}

export function decodePortfolio(raw: unknown): Holding[] {
  if (!Array.isArray(raw)) return [];
  const holdings: Holding[] = [];
  for (const item of raw) {
    if (typeof item !== "object" || item === null) continue;
    const record = item as Record<string, unknown>;
    const symbol = decodeSymbol(record["symbol"]);
    if (symbol === null) continue;
    const quantity = record["quantity"];
    const cost = record["cost"];
    // Both are required numbers. A string "2" is not a quantity this product
    // wrote, and reading it as 2 would be accepting a shape we never store.
    if (typeof quantity !== "number" || !Number.isFinite(quantity) || quantity <= 0) continue;
    if (typeof cost !== "number" || !Number.isFinite(cost) || cost < 0) continue;
    if (holdings.some((holding) => holding.symbol === symbol)) continue;
    holdings.push({ symbol, quantity, cost });
  }
  return holdings;
}

export interface HoldingInput {
  readonly symbol: string;
  readonly quantity: string;
  readonly cost: string;
}

export type HoldingEdit =
  | { ok: true; holdings: Holding[] }
  | { ok: false; field: "symbol" | "quantity" | "cost" | "duplicate"; error: string };

/**
 * One row per symbol: the page has one average cost per holding, so a second
 * row for the same ticker would make "average" ambiguous. Refusing it is
 * clearer than silently merging two costs into a number the reader never typed.
 */
export function addHolding(list: readonly Holding[], input: HoldingInput): HoldingEdit {
  const symbol = normalizeSymbol(input.symbol);
  if (symbol === null) {
    return { ok: false, field: "symbol", error: "Enter a ticker, such as AAPL." };
  }
  if (list.some((holding) => holding.symbol === symbol)) {
    return {
      ok: false,
      field: "duplicate",
      error: `${symbol} is already a holding. Remove that row to change it.`,
    };
  }
  const quantity = parseQuantity(input.quantity);
  if (quantity === null) {
    return {
      ok: false,
      field: "quantity",
      error: "Quantity must be a number greater than zero.",
    };
  }
  const cost = parseCost(input.cost);
  if (cost === null) {
    return { ok: false, field: "cost", error: "Average cost must be zero or more." };
  }
  return { ok: true, holdings: [...list, { symbol, quantity, cost }] };
}

export function removeHolding(list: readonly Holding[], symbol: string): Holding[] {
  return list.filter((holding) => holding.symbol !== symbol);
}

/* ------------------------------------------------------------------------- *
 * The two stores the pages read
 * ------------------------------------------------------------------------- */

export const watchlistStore = createLocalStore<string[]>(WATCHLIST_KEY, decodeWatchlist);
export const portfolioStore = createLocalStore<Holding[]>(PORTFOLIO_KEY, decodePortfolio);
