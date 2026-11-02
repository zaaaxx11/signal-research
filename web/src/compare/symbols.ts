/**
 * The comparison URL: `/compare?symbols=AAPL,MSFT`.
 *
 * A comparison is a set of tickers carried in the query string, not a browser
 * local list: the address is shareable, reloadable and answerable by the
 * server's SPA fallback. The page parses the same spelling this module writes,
 * so a pasted link and a confirmed selection agree.
 *
 * Every rule that decides "which companies" lives here rather than in the page:
 *
 * * **The alphabet is the screener's own** — `^[A-Za-z][A-Za-z0-9.\-]{0,9}$`,
 *   the pattern the ticker filter field uses. A symbol the filter accepts and
 *   the comparison refuses would be two answers to one question.
 * * **Two to ten.** Ten is the API's own tickers ceiling and the width at which
 *   a row of columns is still a comparison rather than a spreadsheet. Two is
 *   the point below which there is nothing to compare.
 * * **Canonical on the way in.** Upper-cased, deduplicated, invalid tokens
 *   dropped — a pasted `aapl, MSFT` and a shared `AAPL,MSFT` name one list.
 * * **Nothing invalid on the way out.** Every href is rebuilt from a parsed
 *   list, so no link this module writes can carry a token the reader did not
 *   mean.
 */

/** The route path, mount-free; `appHref`/`navigate` add the mount. */
export const COMPARE_PATH = "/compare";

/** A comparison needs at least two companies; below that there is no comparison. */
export const MIN_COMPARE = 2;

/** The most companies one comparison holds — the API's own tickers ceiling. */
export const MAX_COMPARE = 10;

/** The historical spelling of the ceiling, kept for callers that read it. */
export const COMPARE_MAX = MAX_COMPARE;

/** One ticker: a letter, then up to nine letters, digits, dots or dashes. */
export const SYMBOL_PATTERN = /^[A-Za-z][A-Za-z0-9.\-]{0,9}$/;

/** The query parameter the address bar and the table agree on. */
export const SYMBOLS_PARAM = "symbols";

/**
 * One token as the comparison spells it, or `null` if it is not a ticker.
 *
 * Trimming is deliberate: a paste out of a spreadsheet arrives with the cell's
 * spaces, and refusing ` AAPL` would look like refusing the company.
 */
export function normaliseSymbol(raw: string): string | null {
  const trimmed = raw.trim();
  if (!SYMBOL_PATTERN.test(trimmed)) return null;
  return trimmed.toUpperCase();
}

/**
 * A `symbols` parameter as an ordered, canonical list.
 *
 * Invalid tokens are dropped rather than failing the page: half a shared link
 * still compares, and the reader can see which companies are missing. The cap
 * drops the tail, which is the only honest truncation — the alternative is
 * silently comparing a company the URL named.
 */
export function parseSymbols(raw: string | null | undefined): string[] {
  if (!raw) return [];
  const symbols: string[] = [];
  for (const token of raw.split(",")) {
    if (symbols.length >= MAX_COMPARE) break;
    const symbol = normaliseSymbol(token);
    if (symbol === null || symbols.includes(symbol)) continue;
    symbols.push(symbol);
  }
  return symbols;
}

/** Read the list out of a query string, with or without its leading `?`. */
export function symbolsFromSearch(search: string): string[] {
  const query = search.startsWith("?") ? search.slice(1) : search;
  return parseSymbols(new URLSearchParams(query).get(SYMBOLS_PARAM));
}

/** The earlier spelling of {@link symbolsFromSearch}, same behaviour. */
export function parseCompareSymbols(search: string): string[] {
  return symbolsFromSearch(search);
}

/**
 * The shareable path for a list.
 *
 * No escaping is needed for the tokens themselves: the alphabet excludes every
 * character a query string would have to escape, and `,` is the separator the
 * API and the reader both expect to see. An empty list drops the parameter
 * rather than leaving a bare `?`.
 */
export function compareHref(symbols: readonly string[]): string {
  const clean = parseSymbols(symbols.join(","));
  return clean.length === 0 ? COMPARE_PATH : `${COMPARE_PATH}?${SYMBOLS_PARAM}=${clean.join(",")}`;
}

/**
 * Append one symbol, if it is a ticker, is not already compared, and the list
 * has room. Returning an equal list is the "nothing happened" answer — the
 * caller must not write a URL for it.
 */
export function addSymbol(symbols: readonly string[], raw: string): string[] {
  const symbol = normaliseSymbol(raw);
  if (symbol === null) return [...symbols];
  if (symbols.includes(symbol)) return [...symbols];
  if (symbols.length >= MAX_COMPARE) return [...symbols];
  return [...symbols, symbol];
}

/** Drop one company, keeping every other column in its place. */
export function removeSymbol(symbols: readonly string[], symbol: string): string[] {
  return symbols.filter((entry) => entry !== symbol);
}

/** Does this list have enough companies to be a comparison? */
export function isComparable(symbols: readonly string[]): boolean {
  return symbols.length >= MIN_COMPARE;
}
