/**
 * Sectors: the names the operator may choose, and the one predicate a choice
 * becomes.
 *
 * Two facts drive this file.
 *
 * 1. **The sector list is the market's, not the page's.** `GET /market/breadth`
 *    counts the whole scanned universe and returns one bucket per provider
 *    sector label — 21 of them for US stocks. That is the list the selector
 *    offers. When breadth cannot be read, the fallback is the `sector` cells of
 *    the rows on screen, and the control says so: a list of seven sectors drawn
 *    from a hundred rows is a different claim from a list of twenty-one drawn
 *    from 5 678.
 * 2. **The label is the wire value.** `sec eq "Electronic Technology"` is what
 *    the API matches against, and it is case-sensitive about the Finviz labels.
 *    Nothing here title-cases, trims a chosen name beyond its own whitespace, or
 *    substitutes a "friendlier" spelling: the chip, the URL and the request body
 *    all carry the byte-for-byte name the breadth route reported.
 *
 * A `null` or blank sector bucket is *not* offered. The scan puts unlabelled
 * rows in a null bucket so the counts still add up; that bucket is not a sector
 * the operator can screen for, and offering it would send `sec eq null`.
 */

import type { ScreenRow } from "@/data/screens";
import type { BreadthReport } from "@/breadth/report";
import type { ActiveFilter } from "@/filters/types";
import { makeFilter } from "@/filters/state";

/** The Finviz filter key for a provider sector label. */
export const SECTOR_KEY = "sec";

/** One name, trimmed, or null when the payload did not really name a sector. */
function clean(name: unknown): string | null {
  if (typeof name !== "string") return null;
  const trimmed = name.trim();
  return trimmed.length > 0 ? trimmed : null;
}

function uniqueSorted(names: Iterable<string>): string[] {
  const seen = new Set<string>();
  for (const name of names) seen.add(name);
  return [...seen].sort((left, right) => left.localeCompare(right));
}

/**
 * The sector names in a breadth document, alphabetically.
 *
 * Alphabetical rather than the endpoint's strength order: this list is a menu,
 * and a menu whose items move with the tape is a menu the reader has to re-read
 * every minute. The strip above the grid keeps the server's ordering.
 */
export function sectorNamesFromReport(report: BreadthReport | null): string[] {
  if (!report) return [];
  const names: string[] = [];
  for (const bucket of report.sectors) {
    const name = clean(bucket.name);
    if (name !== null) names.push(name);
  }
  return uniqueSorted(names);
}

/**
 * The fallback: the distinct `sector` cells of the rows this panel is showing.
 *
 * It is deliberately the *visible* rows — when breadth is unavailable there is
 * no market-wide answer to give, and pretending the page is the universe is the
 * exact error the strip above exists to prevent. The control labels the source.
 */
export function sectorNamesFromRows(rows: readonly ScreenRow[]): string[] {
  const names: string[] = [];
  for (const row of rows) {
    const name = clean(row["sector"]);
    if (name !== null) names.push(name);
  }
  return uniqueSorted(names);
}

/**
 * The predicate the selector owns: `sec` is a text key, and the selector only
 * ever writes an equality. `sec contains "Tech"` typed in the drawer is a
 * different claim and stays a chip of its own.
 */
export function isSectorChoice(filter: ActiveFilter): boolean {
  return (
    filter.key === SECTOR_KEY &&
    filter.op === "eq" &&
    typeof filter.value === "string" &&
    filter.value.trim().length > 0
  );
}

/** The sector currently shown by the control, or null for "All sectors". */
export function activeSector(filters: readonly ActiveFilter[]): string | null {
  for (let index = filters.length - 1; index >= 0; index -= 1) {
    const filter = filters[index];
    if (filter && isSectorChoice(filter)) return String(filter.value);
  }
  return null;
}

/**
 * The filter list with the sector choice replaced (or removed).
 *
 * **Other keys survive untouched** — this is a replacement of the one predicate
 * the control owns, not a reset. A link carrying `cap ≥ $2B` and a sector keeps
 * its market-cap chip when the sector changes, and the request carries both.
 *
 * `sec` equality predicates are removed rather than appended to: two sector
 * chips would be two different claims about one control, and the reader would
 * have no way to tell which one the menu is showing.
 */
export function withSector(
  filters: readonly ActiveFilter[],
  name: string | null,
): ActiveFilter[] {
  const kept = filters.filter((filter) => !isSectorChoice(filter));
  const chosen = clean(name);
  if (chosen === null) return kept;
  return [...kept, makeFilter(SECTOR_KEY, "eq", chosen)];
}
