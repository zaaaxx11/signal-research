/**
 * Display strings for the breadth strip.
 *
 * Every function here reads a value the endpoint already computed and returns
 * text. Three rules carry over from the grid's formatter, because they are the
 * difference between a screener and a dashboard:
 *
 * * **Absent is not zero.** A `null` average prints `n/a`, never `0.00%`.
 * * **Sign is never carried by colour alone.** `+0.48%` / `-1.10%` carry the
 *   direction in the string; the tone class only repeats what the digits say.
 * * **A number is never printed as a different number.** The sign is read off
 *   the magnitude that was actually printed, so `-0.001` is `0.00%` and flat,
 *   not `-0.00%` in red.
 */

/** `5678` → `5,678`. Counts are never compacted: `5.7K` hides a rounding. */
const COUNTS = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });

export function formatCount(value: number): string {
  return COUNTS.format(value);
}

/** One formatter per precision, built once (a strip can print 21 sectors). */
const DECIMALS = new Map<number, Intl.NumberFormat>();

function decimal(precision: number): Intl.NumberFormat {
  const cached = DECIMALS.get(precision);
  if (cached) return cached;
  const created = new Intl.NumberFormat("en-US", {
    minimumFractionDigits: precision,
    maximumFractionDigits: precision,
  });
  DECIMALS.set(precision, created);
  return created;
}

/** -1 = printed negative, 0 = printed flat, 1 = printed positive, null = absent. */
export type Sign = -1 | 0 | 1 | null;

export interface SignedPercent {
  readonly text: string;
  readonly sign: Sign;
  readonly absent: boolean;
}

/**
 * `-1.0998…` → `{ text: "-1.10%", sign: -1 }`; `null` → `{ text: "n/a" }`.
 *
 * The return type pairs the string with the sign *it printed*, so the colour
 * and the digits cannot disagree — `SectorBlock` paints from `sign`, not from
 * the raw number.
 */
export function signedPercent(value: number | null | undefined, precision = 2): SignedPercent {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return { text: "n/a", sign: null, absent: true };
  }

  const magnitude = decimal(precision).format(Math.abs(value));
  const printed = Number(magnitude.replace(/,/g, ""));
  if (!(printed > 0)) return { text: `${magnitude}%`, sign: 0, absent: false };

  return value > 0
    ? { text: `+${magnitude}%`, sign: 1, absent: false }
    : { text: `-${magnitude}%`, sign: -1, absent: false };
}

/**
 * The token pair for a printed sign. `null` (absent) is deliberately *not* a
 * direction colour: an unmeasured sector is neutral ink, not a flat market.
 */
export function toneClass(sign: Sign): string {
  switch (sign) {
    case 1:
      return "text-up";
    case -1:
      return "text-down";
    case 0:
      return "text-flat";
    default:
      return "text-fg-subtle";
  }
}

/**
 * A bucket label. The scan emits a null-named bucket for rows its provider did
 * not attribute; calling it "Unspecified" is honest, calling it "Other" is not.
 */
export function sectorLabel(name: string | null): string {
  return name === null || name.trim().length === 0 ? "Unspecified" : name;
}

/**
 * `45 / 5,675 reported`.
 *
 * The denominator is part of the sentence on purpose: "46 new highs" out of
 * 5,675 rows that carried both a price and a 52-week high is a different claim
 * from 46 out of 5,678, and the endpoint reports both numbers so the strip
 * must not collapse them. When the server measured nothing, say so.
 */
export function extremeLabel(count: number, denominator: number | null): string {
  if (denominator === null || !Number.isFinite(denominator) || denominator <= 0) {
    return `${formatCount(count)} / no basis`;
  }
  return `${formatCount(count)} / ${formatCount(denominator)} reported`;
}

/**
 * The clock part of an ISO timestamp, in UTC: `2026-10-31T09:21:58+00:00` →
 * `09:21:58Z`.
 *
 * A string with no offset is *not* relabelled Z — we do not know which zone it
 * is in, and printing a UTC mark on a local clock would be a fabricated fact.
 * It is shown as the server wrote it instead.
 */
export function formatAsOf(iso: string): string {
  if (iso.trim().length === 0) return "—";

  const hasOffset = /(?:[Zz]|[+-]\d{2}:?\d{2})$/.test(iso);
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) {
    // Not a date at all: show what arrived rather than inventing a time.
    return iso.length > 24 ? `${iso.slice(0, 24)}…` : iso;
  }
  if (!hasOffset) {
    const clock = /T(\d{2}:\d{2}(?::\d{2})?)/.exec(iso)?.[1];
    return clock ?? parsed.toISOString().slice(0, 19).replace("T", " ") + " (unzoned)";
  }
  return `${parsed.toISOString().slice(11, 19)}Z`;
}

/**
 * One bar segment's width, in percent, clamped to `[0, 100]`.
 *
 * `total === 0` is the case that used to be `NaN%` (two empty buckets dividing
 * each other); it returns `0`, and the caller renders the flat track instead.
 */
export function barShare(part: number, total: number): number {
  if (!Number.isFinite(part) || !Number.isFinite(total) || total <= 0) return 0;
  const share = (part / total) * 100;
  if (!Number.isFinite(share)) return 0;
  return Math.min(100, Math.max(0, share));
}

/** The spoken sentence behind the two-segment bar. */
export function barLabel(report: {
  advancing: number;
  declining: number;
  unchanged: number;
  universe_total: number;
}): string {
  const movers = report.advancing + report.declining;
  const head =
    movers === 0
      ? `No advancing or declining names in ${formatCount(report.universe_total)} scanned`
      : `Advancing ${formatCount(report.advancing)} versus declining ${formatCount(report.declining)} over ${formatCount(report.universe_total)} scanned`;
  return `${head}; ${formatCount(report.unchanged)} unchanged.`;
}
