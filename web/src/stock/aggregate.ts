/**
 * Daily / weekly / monthly bars from one daily series.
 *
 * The chart endpoint serves daily bars only. The D/W/M chips are an
 * **aggregation of that same series**, not a different request: a week's open
 * is its first session's open, its high is the highest high, its low the
 * lowest low, its close the last close, and its volume the sum. The bucket's
 * timestamp is the last session in it, so the x-axis stays on the same clock
 * and two adjacent buckets cannot share a time.
 *
 * Weeks start on Monday (UTC); months are the calendar month. A partial first
 * or last bucket is kept as-is — it is a real period with fewer sessions, and
 * dropping it would hide the most recent week.
 *
 * Ordinal series (`Bar 1`, `Bar 2`, …) are returned unchanged: they have no
 * calendar, and bucketing by a date they do not carry would invent one.
 */

import type { ChartBar } from "@/charts/bars";

export type BarInterval = "D" | "W" | "M";

export const INTERVALS: readonly { id: BarInterval; label: string; name: string }[] = [
  { id: "D", label: "D", name: "Daily" },
  { id: "W", label: "W", name: "Weekly" },
  { id: "M", label: "M", name: "Monthly" },
];

function pad(value: number): string {
  return String(value).padStart(2, "0");
}

/** `2026-10-26` — the Monday of the week containing `seconds`. */
function weekKey(seconds: number): string {
  const date = new Date(seconds * 1000);
  const day = date.getUTCDay();
  const offset = day === 0 ? 6 : day - 1;
  const monday = new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate() - offset));
  return `${monday.getUTCFullYear()}-${pad(monday.getUTCMonth() + 1)}-${pad(monday.getUTCDate())}`;
}

function monthKey(seconds: number): string {
  const date = new Date(seconds * 1000);
  return `${date.getUTCFullYear()}-${pad(date.getUTCMonth() + 1)}`;
}

interface Bucket {
  key: string;
  label: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  last: number;
}

/**
 * One bucket per key, in the input's order. `bars` is already ascending and
 * de-duplicated (the `toBars` contract), so the first write is the open and
 * every later write updates the close.
 */
export function aggregateBars(
  bars: readonly ChartBar[],
  interval: BarInterval,
): readonly ChartBar[] {
  if (interval === "D" || bars.length === 0) return bars;
  const first = bars[0];
  if (first === undefined || first.timeLabel.startsWith("Bar ")) return bars;

  const buckets = new Map<string, Bucket>();
  for (const bar of bars) {
    const key = interval === "W" ? weekKey(bar.time) : monthKey(bar.time);
    const existing = buckets.get(key);
    if (existing === undefined) {
      buckets.set(key, {
        key,
        label: interval === "W" ? `Week of ${key}` : key,
        open: bar.open,
        high: bar.high,
        low: bar.low,
        close: bar.close,
        volume: bar.volume,
        last: bar.time,
      });
      continue;
    }
    existing.high = Math.max(existing.high, bar.high);
    existing.low = Math.min(existing.low, bar.low);
    existing.close = bar.close;
    existing.volume += bar.volume;
    existing.last = bar.time;
  }

  return [...buckets.values()].map((bucket) => ({
    time: bucket.last as ChartBar["time"],
    open: bucket.open,
    high: bucket.high,
    low: bucket.low,
    close: bucket.close,
    volume: bucket.volume,
    timeLabel: bucket.label,
  }));
}
