/**
 * The Insider page's two small formatters.
 *
 * `formatNetShares` is the column's figure. The underlying field is Yahoo's
 * `netPercentInsiderShares` — a net change expressed as a PERCENTAGE of shares
 * outstanding, not a count of shares. Printing 0.6712 as "0.67 shares" would
 * be a unit error of six orders of magnitude, so the figure always carries its
 * percent sign and the sentence under the title names the unit.
 *
 * It carries no `$`: the measure is a change in ownership, not a trade value.
 *
 * `safeHref` is the one link a filing may carry. The payload's `url` is
 * EDGAR's archive; anything that is not absolute http(s) is refused rather
 * than handed to an anchor.
 */

import { formatNumber } from "@/grid/format";

/**
 * `1234567` -> `+1,234,567`; `-89000` -> `-89,000`; `0` -> `0`.
 *
 * Whole shares print at zero decimals, with grouping. A payload that carries a
 * fraction is printed at the two decimals it needs rather than rounded into a
 * different integer: `0.67` is not `1`, and the page may not say it is. A
 * magnitude that rounds to nothing at the precision it is printed with claims
 * no direction at all — `0.00`, not `+0.00` — for the same reason
 * `grid/format.ts` prints `0.00%` for `-0.001`.
 */
export function formatNetShares(value: number): string {
  // Two decimals always: this is a percentage of shares outstanding, where a
  // whole point is already a large move. A magnitude that prints as 0.00 makes
  // no directional claim, for the same reason the grid prints 0.00% for -0.001.
  const magnitude = formatNumber(Math.abs(value), 2);
  const printed = Number(magnitude.replace(/,/g, ""));
  if (!(printed > 0)) return `${magnitude}%`;
  return `${value > 0 ? "+" : "-"}${magnitude}%`;
}

/** The sentence behind the number, for a title and for a screen reader. */
export function netSharesLabel(value: number): string {
  if (!Number.isFinite(value) || value === 0) return "no net change in insider ownership";
  const magnitude = formatNumber(Math.abs(value), 2);
  return `insider ownership ${value > 0 ? "up" : "down"} ${magnitude} percentage points net`;
}

/**
 * An href a browser may follow: absolute http(s) only. `null` for
 * `javascript:`, `data:`, a relative path, or a non-string. The parsed href is
 * returned, so the anchor carries the canonical URL rather than the raw field.
 */
export function safeHref(raw: unknown): string | null {
  if (typeof raw !== "string") return null;
  const trimmed = raw.trim();
  if (trimmed.length === 0) return null;
  // Browsers strip control characters before parsing a scheme; reject instead
  // of letting `java\tscript:` become executable.
  if (/[\u0000-\u001f\u007f]/.test(trimmed)) return null;
  let parsed: URL;
  try {
    parsed = new URL(trimmed);
  } catch {
    return null;
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return null;
  if (parsed.username || parsed.password) return null;
  return parsed.href;
}

/**
 * A filing date as `YYYY-MM-DD`. A bare date passes through; an ISO instant is
 * cut to its UTC day; anything unparseable is printed verbatim rather than
 * replaced with a date the payload did not state.
 */
export function dayText(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  if (trimmed.length === 0) return null;
  if (/^\d{4}-\d{2}-\d{2}$/.test(trimmed)) return trimmed;
  const date = new Date(trimmed);
  if (Number.isNaN(date.getTime())) return trimmed;
  return date.toISOString().slice(0, 10);
}
