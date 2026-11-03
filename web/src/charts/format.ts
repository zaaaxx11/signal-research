/**
 * The chart's words: an axis label, a readout's clock, a price at a tick.
 *
 * Nothing here invents a price format. The readout and the summary table use
 * the shell's existing `formatPrice` / `formatVolume` (`@/home/signals`), so a
 * chart's `$123.46` is the same string the grid prints for the same value. This
 * module only adds the two shapes an axis needs and neither helper provides:
 *
 *   * a price at a *step*, where the number of decimals is a function of the
 *     grid the label sits on — a 50-point S&P step prints `6,800`, a 0.05 VIX
 *     step prints `18.25`, and neither is `6,800.00` crowding a 58 px gutter;
 *   * a timestamp in the payload's own timezone, formatted by `Intl`, cached
 *     per zone and range because `Intl.DateTimeFormat` construction is the
 *     expensive part of a tick that repaints on hover.
 *
 * An unknown timezone falls back to UTC rather than throwing: a provider that
 * names a zone this runtime does not know must cost the reader a label's zone,
 * not the chart.
 */

import type { ChartRange } from "./types";

type Stamp = Intl.DateTimeFormatOptions;

/**
 * Formatter shapes, per range.
 *
 * 1D is a clock. 5D and 1M are dates, because five sessions contain five
 * midnights and an hour label would repeat itself. 6M keeps the day (its ticks
 * are ~3 weeks apart), and 1Y drops to the month — six ticks over a year are
 * two months apart and the day is noise. 5Y adds the year back, because a
 * month repeats every twelve and five years of ticks would name the same month
 * five times.
 */
const AXIS_STAMPS: Readonly<Record<ChartRange, Stamp>> = {
  "1d": { hour: "2-digit", minute: "2-digit", hour12: false },
  "5d": { month: "short", day: "numeric" },
  "1mo": { month: "short", day: "numeric" },
  "6mo": { month: "short", day: "numeric" },
  "1y": { month: "short" },
  "5y": { month: "short", year: "numeric" },
};

/** The readout is more specific than the axis: a bar has a day *and* a time. */
const READOUT_STAMPS: Readonly<Record<ChartRange, Stamp>> = {
  "1d": { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false },
  "5d": { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false },
  "1mo": { month: "short", day: "numeric" },
  "6mo": { month: "short", day: "numeric" },
  "1y": { month: "short", day: "numeric", year: "numeric" },
  "5y": { month: "short", day: "numeric", year: "numeric" },
};

const formatters = new Map<string, Intl.DateTimeFormat>();

function formatter(stamp: Stamp, timezone: string): Intl.DateTimeFormat {
  const zone = timezone.trim().length > 0 ? timezone.trim() : "UTC";
  const key = `${zone}\u0000${JSON.stringify(stamp)}`;
  const cached = formatters.get(key);
  if (cached !== undefined) return cached;
  let created: Intl.DateTimeFormat;
  try {
    created = new Intl.DateTimeFormat("en-US", { ...stamp, timeZone: zone });
  } catch {
    // An IANA name this runtime does not carry (or a bogus string): UTC is a
    // real time zone and an honest fallback, unlike dropping the tick.
    created = new Intl.DateTimeFormat("en-US", { ...stamp, timeZone: "UTC" });
  }
  formatters.set(key, created);
  return created;
}

/** `1767225600` → `14:35` (1D) or `Jan 2` (5D/1M/6M) or `Jan` (1Y) or `Jan '21` (5Y). */
export function axisTime(epochSeconds: number, range: ChartRange, timezone = "UTC"): string {
  if (!Number.isFinite(epochSeconds)) return "";
  return formatter(AXIS_STAMPS[range], timezone).format(new Date(epochSeconds * 1000));
}

/** The hover readout's stamp: a day and a clock for intraday ranges. */
export function barTime(epochSeconds: number, range: ChartRange, timezone = "UTC"): string {
  if (!Number.isFinite(epochSeconds)) return "—";
  return formatter(READOUT_STAMPS[range], timezone).format(new Date(epochSeconds * 1000));
}

function priceFormatter(precision: number): Intl.NumberFormat {
  const key = `price\u0000${precision}`;
  const cached = priceFormatters.get(key);
  if (cached !== undefined) return cached;
  const created = new Intl.NumberFormat("en-US", {
    minimumFractionDigits: precision,
    maximumFractionDigits: precision,
  });
  priceFormatters.set(key, created);
  return created;
}

const priceFormatters = new Map<string, Intl.NumberFormat>();

/** Decimals a price axis needs to print a given grid step exactly. */
export function pricePrecision(step: number): number {
  if (!Number.isFinite(step) || step <= 0) return 2;
  for (let precision = 0; precision <= 4; precision += 1) {
    const scaled = step * 10 ** precision;
    if (Math.abs(scaled - Math.round(scaled)) < 1e-9) return precision;
  }
  return 4;
}

/** `6800` at a 50-point step → `6,800`; `18.25` at 0.05 → `18.25`. */
export function axisPrice(value: number, step: number): string {
  if (!Number.isFinite(value)) return "";
  return priceFormatter(pricePrecision(step)).format(value);
}

/**
 * A conservative width for a 10 px label, in CSS px.
 *
 * `measureText` is exact but needs a context; the axis filter runs on every
 * geometry build and must be usable without one (a test, a server render).
 * 5.6 px per character overestimates the mono face's average advance, so the
 * only error is dropping a label that would have fit by a hair — never
 * overlapping two.
 */
export function estimateLabelWidth(label: string, fontPx = 10): number {
  return label.length * fontPx * 0.56 + 2;
}
