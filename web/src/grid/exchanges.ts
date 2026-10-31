/**
 * Exchanges as a choice: the codes the control may offer, and the one predicate
 * a choice becomes.
 *
 * The sector control's four pieces are copied here deliberately (read, menu,
 * predicate, chip), because the two facets share a grammar but not a meaning:
 *
 * * **The value is a set of codes, not a label.** `XNAS` is what the API
 *   matches; `NASDAQ` is what the reader sees (`lib/exchanges.ts`). The chip,
 *   the URL and the request body all carry the code, and a link that says
 *   `exch:eq:XNAS` arrives checked.
 * * **One predicate for the set.** Two `exch eq` rows are ANDed by the API into
 *   zero rows, so "NASDAQ or NYSE" — the canonical US screen — must be one
 *   `in`. `eq` for a single code, because an equality is what the drawer writes
 *   and a link that says `eq` should not be rewritten into `in` just because
 *   the control was used.
 * * **It owns `eq` and `in` only.** `exch ne XNAS` and `exch contains nas` are
 *   different claims, typed elsewhere; they survive a choice untouched and stay
 *   in the chip bar — exactly the rule `isSectorChoice` follows for `sec`.
 * * **`[]` means "all exchanges"**, and removing the choice removes the
 *   predicate rather than writing an empty one.
 */

import type { ScreenRow } from "@/data/screens";
import type { ActiveFilter } from "@/filters/types";
import { makeFilter } from "@/filters/state";
import { sortExchanges } from "@/lib/exchanges";

/** The Finviz filter key for an exchange code. */
export const EXCHANGE_KEY = "exch";

/** One venue and how many rows carried it. */
export interface VenueBucket {
  readonly code: string;
  readonly count: number;
}

/** A code as this module speaks it: trimmed, upper-cased, or null when blank. */
export function cleanExchangeCode(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const trimmed = value.trim().toUpperCase();
  return trimmed.length > 0 ? trimmed : null;
}

/**
 * Does this predicate belong to the control?
 *
 * `eq` and `in` are its claim, whatever the value: even a half-typed
 * `exch eq ""` from a hand-written link is a row this control owns and will
 * replace. `ne` and `contains` are somebody else's — the control cannot express
 * "not NASDAQ", so it must not swallow the chip that does.
 */
export function isExchangeChoice(filter: ActiveFilter): boolean {
  return filter.key === EXCHANGE_KEY && (filter.op === "eq" || filter.op === "in");
}

/**
 * The codes the control is showing, in menu order; `[]` is "All exchanges".
 *
 * Read from the last `eq`/`in` predicate on the key, because that is the one a
 * later choice replaced. A code the list is missing still comes back here — the
 * menu prepends it (`ExchangeSelect`), so a link to a venue the scan no longer
 * counts is visible and uncheckable rather than silently dropped.
 */
export function activeExchanges(filters: readonly ActiveFilter[]): string[] {
  for (let index = filters.length - 1; index >= 0; index -= 1) {
    const filter = filters[index];
    if (!filter || filter.key !== EXCHANGE_KEY) continue;
    if (filter.op === "eq" && typeof filter.value === "string") {
      const code = cleanExchangeCode(filter.value);
      return code === null ? [] : [code];
    }
    if (filter.op === "in" && Array.isArray(filter.value)) {
      const codes = filter.value
        .map((item) => cleanExchangeCode(item))
        .filter((code): code is string => code !== null);
      return sortExchanges(codes);
    }
  }
  return [];
}

/**
 * The filter list with the exchange predicate replaced.
 *
 * **Other keys survive untouched.** A link carrying `cap ≥ $2B` and two venues
 * keeps its market-cap chip when a venue is toggled; the request carries both.
 *
 * `[]` removes the predicate entirely (the control's "All exchanges"), one code
 * becomes `eq`, several become `in` in canonical order. The write is
 * normalised: `exch` is compared exactly on the server, so `xnas` would match
 * nothing.
 */
export function withExchange(
  filters: readonly ActiveFilter[],
  codes: readonly string[],
): ActiveFilter[] {
  // `eq`/`in` are dropped whether or not they are well-formed: a half-typed
  // `exch eq ""` from a link is still this control's row to own.
  const kept = filters.filter((filter) => !isExchangeChoice(filter));
  const chosen = sortExchanges(codes);
  if (chosen.length === 0) return kept;
  if (chosen.length === 1) return [...kept, makeFilter(EXCHANGE_KEY, "eq", chosen[0] as string)];
  return [...kept, makeFilter(EXCHANGE_KEY, "in", chosen)];
}

/**
 * The fallback population: the distinct `exchange` cells of the rows on screen.
 *
 * It is deliberately the *visible* rows. When the market list cannot be read
 * there is no market-wide answer to give, and presenting a hundred rows as the
 * universe is the error the source sentence exists to prevent. The counts are
 * page counts, and the menu's tooltip says so.
 */
export function exchangeVenuesFromRows(rows: readonly ScreenRow[]): VenueBucket[] {
  const counts = new Map<string, number>();
  for (const row of rows) {
    const code = cleanExchangeCode(row["exchange"]);
    if (code === null) continue;
    counts.set(code, (counts.get(code) ?? 0) + 1);
  }
  return orderBuckets(counts);
}

/**
 * The document `GET /market/exchanges` answers with, read leniently.
 *
 * This facet is a *menu*, not a headline figure: a payload without `exchanges`,
 * a bucket with no code, or a count that is not a number must degrade to the
 * rows on screen, never take the grid down. So an unreadable answer is `[]`
 * rather than a throw — the opposite of `breadth/report.ts`, whose counts are
 * the product of the strip. A missing count becomes `0` and is printed as
 * `0` only because the server did not claim one; the menu never sums counts to
 * make a claim of its own.
 */
export function venueBucketsFromReport(body: unknown): VenueBucket[] {
  if (typeof body !== "object" || body === null) return [];
  const list = (body as Record<string, unknown>)["exchanges"];
  if (!Array.isArray(list)) return [];

  const counts = new Map<string, number>();
  for (const entry of list) {
    if (typeof entry !== "object" || entry === null) continue;
    const record = entry as Record<string, unknown>;
    const code = cleanExchangeCode(record["code"]);
    if (code === null) continue;
    const raw = record["count"];
    const count = typeof raw === "number" && Number.isFinite(raw) && raw >= 0 ? Math.floor(raw) : 0;
    // A duplicate code is one venue: keep the larger count rather than summing,
    // so a payload that repeats a bucket cannot inflate the menu.
    counts.set(code, Math.max(counts.get(code) ?? 0, count));
  }
  return orderBuckets(counts);
}

function orderBuckets(counts: ReadonlyMap<string, number>): VenueBucket[] {
  return sortExchanges([...counts.keys()]).map((code) => ({
    code,
    count: counts.get(code) ?? 0,
  }));
}
