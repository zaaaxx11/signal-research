/**
 * Small, pure formatters the detail panel shares between tabs.
 *
 * Everything here is deliberately testable without a DOM: a relative time, a
 * link that is safe to put in `href`, an insider code translated into a word,
 * and a trade value that is `—` rather than `$0.00` when the price is missing.
 */

import { formatCompact, formatMoney, formatNumber, isAbsent } from "@/grid/format";

/**
 * An href a browser may follow: absolute http(s) only.
 *
 * Returns `null` for anything else — `javascript:`, `data:`, `vbscript:`, a
 * relative path, a non-string. `new URL` lowercases and canonicalises the
 * scheme first, so `JaVaScRiPt:` and `java\nscript:` are both rejected, and
 * returning the parsed `href` means the anchor carries the canonical URL rather
 * than the string the feed sent.
 */
export function safeHref(raw: unknown): string | null {
  if (typeof raw !== "string") return null;
  const trimmed = raw.trim();
  if (trimmed.length === 0) return null;
  // A control character inside a scheme is stripped by browsers before parsing;
  // reject instead of letting `java\tscript:` become executable.
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

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;
const WEEK = 7 * DAY;
const MONTH = 30 * DAY;
const YEAR = 365 * DAY;

/**
 * A feed timestamp as a relative string: `just now`, `12m ago`, `3h ago`,
 * `2d ago`, `3w ago`, `4mo ago`, `1y ago`.
 *
 * A timestamp in the future (clock skew between the feed and the browser) is
 * `just now`, never `-3m ago`. An unparseable one is the placeholder.
 */
export function formatRelativeTime(iso: string, now: number = Date.now()): string {
  if (typeof iso !== "string" || iso.trim().length === 0) return "—";
  const then = new Date(iso).getTime();
  if (!Number.isFinite(then)) return "—";

  const delta = now - then;
  if (delta < MINUTE) return "just now";
  if (delta < HOUR) return `${Math.floor(delta / MINUTE)}m ago`;
  if (delta < DAY) return `${Math.floor(delta / HOUR)}h ago`;
  if (delta < WEEK) return `${Math.floor(delta / DAY)}d ago`;
  if (delta < MONTH) return `${Math.floor(delta / WEEK)}w ago`;
  if (delta < YEAR) return `${Math.floor(delta / MONTH)}mo ago`;
  return `${Math.floor(delta / YEAR)}y ago`;
}

/** The full UTC instant, for a `title` beside the relative string. */
export function formatInstant(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toISOString().replace("T", " ").replace(/\.\d{3}Z$/, " UTC");
}

/** `2026-10-27T00:00:00Z` -> `2026-10-27`; a bare date passes through. */
export function formatDate(value: unknown): string {
  if (typeof value !== "string" || value.trim().length === 0) return "—";
  const trimmed = value.trim();
  if (/^\d{4}-\d{2}-\d{2}$/.test(trimmed)) return trimmed;
  const date = new Date(trimmed);
  if (Number.isNaN(date.getTime())) return trimmed;
  return date.toISOString().slice(0, 10);
}

/** Form 4 transaction codes, shortened for a 360 px table. */
const INSIDER_TYPES: Record<string, { short: string; long: string }> = {
  P: { short: "Buy", long: "Open-market purchase" },
  S: { short: "Sale", long: "Open-market sale" },
  M: { short: "Option", long: "Option exercise" },
  A: { short: "Award", long: "Grant or award" },
  F: { short: "Tax", long: "Tax withholding" },
  G: { short: "Gift", long: "Bona fide gift" },
  C: { short: "Conv", long: "Conversion" },
  X: { short: "Option", long: "Option exercise" },
  D: { short: "Disp", long: "Disposition to the issuer" },
  I: { short: "Disp", long: "Discretionary transaction" },
  J: { short: "Other", long: "Other acquisition or disposition" },
  K: { short: "Swap", long: "Equity swap" },
  L: { short: "Small", long: "Small acquisition" },
  U: { short: "Tender", long: "Tender offer" },
  W: { short: "Will", long: "Acquisition by will" },
  Z: { short: "Trust", long: "Voting trust" },
};

export function insiderType(code: unknown): { short: string; long: string } {
  if (typeof code !== "string" || code.length === 0) {
    return { short: "—", long: "Unknown transaction code" };
  }
  const key = code.trim().toUpperCase();
  return INSIDER_TYPES[key] ?? { short: key, long: `Transaction code ${key}` };
}

/**
 * Shares × price, or `null` when either leg is absent.
 *
 * A missing price is not a zero-dollar trade: the table prints the placeholder
 * so a Form 4 with no reported price cannot look like a gift of nothing.
 */
export function tradeValue(shares: unknown, price: unknown): number | null {
  if (typeof shares !== "number" || !Number.isFinite(shares)) return null;
  if (typeof price !== "number" || !Number.isFinite(price)) return null;
  return shares * price;
}

/** A share count: compact above 10 000, exact below, placeholder when absent. */
export function formatShares(value: unknown): string {
  if (isAbsent(value) || typeof value !== "number" || !Number.isFinite(value)) return "—";
  return Math.abs(value) >= 10_000 ? formatCompact(value) : formatNumber(value, 0);
}

/** A trade value in dollars: `$823.65K`; `—` when absent. */
export function formatTradeMoney(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return "—";
  return formatMoney(value, Math.abs(value) >= 10_000);
}
