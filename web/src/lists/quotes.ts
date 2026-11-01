/**
 * `GET /market/quotes?symbols=AAPL,MSFT,BTC-USD`
 *
 *   { as_of, rows: [{symbol, name, price, change_pct, currency, class}],
 *     missing: [symbol] }
 *
 * One call answers a whole watchlist. Max 50 symbols, deduped, and `missing`
 * keeps the order it was given, which is why the request is canonicalised here
 * (sorted) rather than at the call site: a reorder is a UI gesture, not a new
 * market fact, and should not cost a round trip.
 *
 * The reader is deliberately suspicious. A body that is not a quote document
 * throws `QuotesMalformed` — an empty table would claim the market is empty. A
 * row whose price is not a finite number is moved to `missing` instead of being
 * drawn as `$0.00`, and so is a symbol that was asked for and came back in
 * neither list, because that is precisely the case a naive join drops without
 * telling anyone.
 */

export const QUOTES_PATH = "/market/quotes";

/** The endpoint's own ceiling. A longer list is a 422, not a bigger answer. */
export const MAX_QUOTE_SYMBOLS = 50;

export interface QuoteRow {
  readonly symbol: string;
  readonly name: string | null;
  readonly price: number;
  readonly changePct: number | null;
  readonly currency: string | null;
  readonly assetClass: string | null;
}

export interface QuotesReport {
  readonly asOf: string | null;
  readonly rows: readonly QuoteRow[];
  readonly missing: readonly string[];
  readonly bySymbol: ReadonlyMap<string, QuoteRow>;
}

export class QuotesMalformed extends Error {
  constructor(message: string) {
    super(message);
    this.name = "QuotesMalformed";
  }
}

/** Deduped and sorted: the address of a *set* of symbols, not of an order. */
export function canonicalSymbols(symbols: readonly string[]): string[] {
  return [...new Set(symbols)].sort();
}

/** `""` when there is nothing to ask — the caller must not request it. */
export function quotesPath(symbols: readonly string[]): string {
  if (symbols.length === 0) return "";
  return `${QUOTES_PATH}?symbols=${symbols.map((symbol) => encodeURIComponent(symbol)).join(",")}`;
}

function text(value: unknown): string | null {
  return typeof value === "string" && value.trim().length > 0 ? value.trim() : null;
}

function number(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export function readQuotes(body: unknown, requested: readonly string[] = []): QuotesReport {
  if (typeof body !== "object" || body === null) {
    throw new QuotesMalformed("The quote answer is not an object.");
  }
  const document = body as Record<string, unknown>;
  const rawRows = document["rows"];
  if (!Array.isArray(rawRows)) {
    throw new QuotesMalformed("The quote answer carries no rows array.");
  }

  const rows: QuoteRow[] = [];
  const bySymbol = new Map<string, QuoteRow>();
  const missing: string[] = [];
  const note = (symbol: string): void => {
    if (!missing.includes(symbol)) missing.push(symbol);
  };

  for (const item of rawRows) {
    if (typeof item !== "object" || item === null) continue;
    const record = item as Record<string, unknown>;
    const symbol = text(record["symbol"])?.toUpperCase() ?? "";
    if (symbol.length === 0) continue;

    const price = number(record["price"]);
    if (price === null) {
      note(symbol);
      continue;
    }
    if (bySymbol.has(symbol)) continue;
    const row: QuoteRow = {
      symbol,
      name: text(record["name"]),
      price,
      changePct: number(record["change_pct"]),
      currency: text(record["currency"]),
      assetClass: text(record["class"]),
    };
    rows.push(row);
    bySymbol.set(symbol, row);
  }

  if (Array.isArray(document["missing"])) {
    for (const item of document["missing"]) {
      const symbol = typeof item === "string" ? item.trim().toUpperCase() : "";
      // A symbol with a usable row is priced, whatever `missing` claims.
      if (symbol.length > 0 && !bySymbol.has(symbol)) note(symbol);
    }
  }

  // Asked for and answered nowhere: absent, not free.
  for (const item of requested) {
    const symbol = item.trim().toUpperCase();
    if (symbol.length > 0 && !bySymbol.has(symbol)) note(symbol);
  }

  return {
    asOf: typeof document["as_of"] === "string" ? document["as_of"] : null,
    rows,
    missing,
    bySymbol,
  };
}

/** The row for a symbol, or `null` — never a zero-priced stand-in. */
export function quoteFor(report: QuotesReport | null, symbol: string): QuoteRow | null {
  return report?.bySymbol.get(symbol) ?? null;
}
