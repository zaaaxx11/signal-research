/**
 * Client-side aggregation: coarser bars from finer ones.
 *
 * The provider's legal (range, interval) table does not include `4h`, `1w` or
 * `1M` bars — those are the tab labels a charting reader expects, so each is
 * served as a finer answer merged **here**, before any pixel. The merged bar
 * is honest arithmetic the reader can verify against the finer bars: the open
 * is the first bar's open, the high the maximum, the low the minimum, the
 * close the last bar's close, and the volume a sum.
 *
 * Grouping keys are UTC-anchored (the series' own timezone is the terminal's)
 * and buckets are stable across every visitor: the same series merges to the
 * same bars, so two tabs cannot disagree.
 */
import type { AggregateUnit } from "./timeframes";
import type { Bar } from "./types";

const DAY_S = 86_400;
const FOUR_HOURS_S = 14_400;
const MS_TO_S = 1_000;

/** UTC midnight of a bar, as the `(day, slot)` arithmetic's anchor. */
export function dayStartUTC(t: number): number {
  const date = new Date(t * MS_TO_S);
  return Math.floor(Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate()) / MS_TO_S);
}

/** The Monday 00:00 UTC of the week a bar belongs to. */
export function weekStartUTC(t: number): number {
  const date = new Date(t * MS_TO_S);
  const weekday = (date.getUTCDay() + 6) % 7; // Mon = 0
  return dayStartUTC(t) - weekday * DAY_S;
}

/** The 1st-of-month 00:00 UTC of the month a bar belongs to. */
export function monthStartUTC(t: number): number {
  const date = new Date(t * MS_TO_S);
  return Math.floor(Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), 1) / MS_TO_S);
}

/** The 4-hour slot a bar belongs to, anchored at its session's midnight. */
export function fourHourSlotUTC(t: number): number {
  return dayStartUTC(t) + Math.floor((t - dayStartUTC(t)) / FOUR_HOURS_S) * FOUR_HOURS_S;
}

function bucketStart(unit: AggregateUnit, t: number): number {
  if (unit === "4h") return fourHourSlotUTC(t);
  if (unit === "week") return weekStartUTC(t);
  return monthStartUTC(t);
}

/**
 * Merge `bars` into one bar per bucket. Bars arrive ordered by `t` (the
 * endpoint's contract), so "first" and "last" are positional.
 */
export function aggregateBars(unit: AggregateUnit, bars: readonly Bar[]): Bar[] {
  const merged: Bar[] = [];
  let start: number | null = null;
  let top: { o: number; h: number; l: number; c: number; v: number } | null = null;
  for (const bar of bars) {
    const first = bucketStart(unit, bar.t);
    if (top === null || first !== start) {
      if (top !== null) merged.push({ t: start!, ...top });
      start = first;
      top = { o: bar.o, h: bar.h, l: bar.l, c: bar.c, v: bar.v };
      continue;
    }
    top = {
      o: top.o,
      h: Math.max(top.h, bar.h),
      l: Math.min(top.l, bar.l),
      c: bar.c,
      v: top.v + bar.v,
    };
  }
  if (top !== null && start !== null) merged.push({ t: start, ...top });
  return merged;
}
