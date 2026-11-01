/**
 * The picture the screener opens with.
 *
 * A reader who has not chosen a preset yet is asking the most ordinary
 * question a stock table answers — *what is this company, how big is it, what
 * does it cost, and is it moving?* — and the served `today` view answers a
 * trader's version of it (price, gap, three volumes) with no company identity
 * at all. So the default column set is a product decision, stated once here:
 * eleven columns, in the order a reader scans them, with the row number and the
 * ticker lanes first.
 *
 * It is still narrowed to what the schema actually serves: a key this build
 * cannot request is dropped rather than sent, and a catalogue that shares none
 * of these keys keeps the view's own columns (the test fixtures are one such
 * catalogue). Choosing any preset replaces this set wholesale — the default is
 * where you start, not a floor on what you may look at.
 */

import { CLIENT_COLUMNS, ROW_NUMBER_KEY, isClientColumn } from "@/grid/columnMeta";

/**
 * The mandatory columns, in order.
 *
 * `ticker` is listed for the reader's benefit and for the documentation: the
 * pinned identity lane draws it, so it is never a request column. `no` is drawn
 * from the page offset and is never a request column either — but both are
 * still *columns on screen*, which is what this list describes.
 */
export const DEFAULT_COLUMN_ORDER = [
  ROW_NUMBER_KEY,
  "ticker",
  "company",
  "sector",
  "industry",
  "country",
  "market_cap",
  "pe",
  "price",
  "change",
  "volume",
] as const;

/**
 * A few more the catalogue backs, after the mandatory eleven: where the name
 * trades, what it pays, and how stretched it is. Optional by construction —
 * `defaultColumns` keeps only the keys the schema serves.
 */
export const DEFAULT_EXTRA_COLUMNS = ["exchange", "dividend_yield", "rsi"] as const;

/** The identity lane draws `ticker`; it is never a column of its own. */
const IDENTITY_KEYS = new Set<string>(["ticker"]);

/**
 * The default set for a served view, filtered to the served catalogue.
 *
 * Falls back to the view's own columns when the catalogue shares none of the
 * default keys: a table must still be a table.
 */
export function defaultColumns(
  viewColumns: readonly string[],
  catalogue: readonly string[],
): string[] {
  const known = new Set<string>([...catalogue, ...CLIENT_COLUMNS]);
  const columns: string[] = [];
  for (const key of [...DEFAULT_COLUMN_ORDER, ...DEFAULT_EXTRA_COLUMNS]) {
    if (IDENTITY_KEYS.has(key)) continue;
    if (!known.has(key)) continue;
    if (columns.includes(key)) continue;
    columns.push(key);
  }
  // A set of nothing but the row number would be an empty table with a
  // numbering lane: if the catalogue backs none of these columns, the view's
  // own columns are the honest answer.
  const requestable = columns.filter((key) => !isClientColumn(key));
  return requestable.length > 0 ? columns : [...viewColumns];
}
