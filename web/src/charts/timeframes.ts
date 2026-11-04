/**
 * The `/charts` timeframes: what the tabs ask for, and what the endpoint gets.
 *
 * `GET /market/chart/{symbol}` speaks `range` (`1d|5d|1mo|6mo|1y|5y|max`) and
 * `interval` (`1m|5m|15m|1h|1d`), with a legal table per range in
 * `app/api/chart.py` (`LEGAL_INTERVALS`). The tabs a reader of a charting
 * product expects — 15M, 4H, D, W, M, plus a calendar view — are named above
 * that vocabulary, so each tab is one (range, interval) pair the endpoint
 * actually accepts, and the coarser-than-daily tabs are that daily answer
 * **aggregated here**, not a request the provider never promised:
 *
 *   * `15M` — 15-minute bars across five sessions        (`5d` + `15m`);
 *   * `4H`  — four 1-hour bars merged into one 4h bar    (`1mo` + `1h`);
 *   * `D`   — day bars over a year                       (`1y`  + `1d`);
 *   * `W`   — daily bars merged, Monday to Monday        (`5y`  + `1d`);
 *   * `M`   — daily bars merged by calendar month        (`max` + `1d`);
 *   * `KALENDER` — the same daily series rendered one row per calendar day
 *     with date axis labels, so the reader reads the tape against the clock
 *     rather than against session boundaries.
 *
 * The aggregates live in `aggregate.ts`; this file is the catalogue only, and
 * `isChartTimeframe` is the validator the page and the tests share.
 */

import type { ChartRange } from "./types";

export type ChartTimeframeId = "15m" | "4h" | "1d" | "1w" | "1M" | "cal";

export type AggregateUnit = "4h" | "week" | "month";

export interface TimeframeSpec {
  readonly id: ChartTimeframeId;
  /** The tab's visible label: short, because six tabs share one row. */
  readonly label: string;
  /** The long name the accessible name of the tab and the chart state. */
  readonly name: string;
  /** The (range, interval) pair this tab asks the endpoint for. */
  readonly request: { readonly range: ChartRange; readonly interval: string };
  /** Non-null when the endpoint's answer is merged client-side before drawing. */
  readonly aggregate: AggregateUnit | null;
  /** True when the body is the calendar-day table, not a price instrument. */
  readonly calendar: boolean;
}

export const CHART_TIMEFRAMES: readonly TimeframeSpec[] = [
  {
    id: "15m",
    label: "15M",
    name: "15-minute bars, five sessions",
    request: { range: "5d", interval: "15m" },
    aggregate: null,
    calendar: false,
  },
  {
    id: "4h",
    label: "4H",
    name: "4-hour bars, one month (each bar from four 1-hour bars)",
    request: { range: "1mo", interval: "1h" },
    aggregate: "4h",
    calendar: false,
  },
  {
    id: "1d",
    label: "D",
    name: "day bars, one year",
    request: { range: "1y", interval: "1d" },
    aggregate: null,
    calendar: false,
  },
  {
    id: "1w",
    label: "W",
    name: "week bars, five years (daily bars merged Monday to Monday)",
    request: { range: "5y", interval: "1d" },
    aggregate: "week",
    calendar: false,
  },
  {
    id: "1M",
    label: "M",
    name: "month bars, all time (daily bars merged by calendar month)",
    request: { range: "max", interval: "1d" },
    aggregate: "month",
    calendar: false,
  },
  {
    id: "cal",
    label: "KALENDER",
    name: "calendar days, one row per day, one month",
    request: { range: "1mo", interval: "1d" },
    aggregate: null,
    calendar: true,
  },
];

export const DEFAULT_TIMEFRAME: ChartTimeframeId = "1d";

export function isChartTimeframe(value: unknown): value is ChartTimeframeId {
  return typeof value === "string" && CHART_TIMEFRAMES.some((spec) => spec.id === value);
}

/** One row, so a caller with an id does not scan the catalogue itself. */
export function timeframeById(id: ChartTimeframeId): TimeframeSpec {
  const found = CHART_TIMEFRAMES.find((spec) => spec.id === id);
  // The catalogue owns every id `isChartTimeframe` accepts; this guard is for
  // a caller that skipped it.
  if (found === undefined) {
    throw new Error(`unknown chart timeframe: ${String(id)}`);
  }
  return found;
}
