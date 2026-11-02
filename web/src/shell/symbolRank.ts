/**
 * One ticker suggestion, ranked the way a reader types it.
 *
 * `GET /symbols/search` already ranks exact symbol → symbol prefix → company
 * prefix → company contains and caps the page at the requested limit. What the
 * server cannot know is how a *suggestion list* reads: a symbol that merely
 * contains the letters ("GOOGL" while typing "OOG") belongs below every symbol
 * that starts with them, and a company match belongs below both. This module
 * is that second pass — a stable re-rank of the rows already on their way, not
 * a second request.
 */

import type { SymbolHit } from "@/lib/api";

/** The order a suggestion list promises, tightest first. */
export const SYMBOL_TIERS = [
  "symbol-exact",
  "symbol-prefix",
  "symbol-contains",
  "name-prefix",
  "name-contains",
] as const;

export type SymbolTier = (typeof SYMBOL_TIERS)[number];

function fold(value: string): string {
  return value.trim().toLowerCase();
}

/**
 * The tier a hit belongs to for `query`, or null when it does not match.
 *
 * Matching is on the folded symbol and the folded, space-collapsed company
 * name, so "aapl", "AAPL" and "aap l" cannot rank one row three ways.
 */
export function tierOf(hit: SymbolHit, query: string): SymbolTier | null {
  const needle = fold(query);
  if (needle.length === 0) return null;
  const symbol = hit.symbol.trim().toLowerCase();
  const name = hit.name.split(/\s+/).join(" ").trim().toLowerCase();
  if (symbol === needle) return "symbol-exact";
  if (symbol.startsWith(needle)) return "symbol-prefix";
  if (symbol.includes(needle)) return "symbol-contains";
  if (name.startsWith(needle)) return "name-prefix";
  if (name.includes(needle)) return "name-contains";
  return null;
}

/**
 * Rows sorted by tier, and — inside one tier — by symbol length then
 * alphabetically, so the same query answers the same list twice. Rows the
 * query no longer matches are dropped: a stale hit can never be committed from
 * a list that claims to answer the text in the box.
 */
export function rankSuggestions(hits: readonly SymbolHit[], query: string): SymbolHit[] {
  const ranked: Array<{ hit: SymbolHit; tier: number }> = [];
  for (const hit of hits) {
    const tier = tierOf(hit, query);
    if (tier === null) continue;
    ranked.push({ hit, tier: SYMBOL_TIERS.indexOf(tier) });
  }
  ranked.sort(
    (a, b) =>
      a.tier - b.tier ||
      a.hit.symbol.length - b.hit.symbol.length ||
      a.hit.symbol.localeCompare(b.hit.symbol, "en-US"),
  );
  return ranked.map((entry) => entry.hit);
}
