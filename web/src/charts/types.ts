/**
 * The chart wire contract, and the one reader allowed to trust it.
 *
 * `GET /market/chart/{symbol}?range=1d|5d|1mo|6mo|1y` answers with
 * `{symbol, range, interval, timezone, previous_close, bars, source, truncated}`;
 * a bar is `{t, o, h, l, c, v}` with `t` in epoch **seconds**.
 *
 * A bar chart cannot skip a malformed row the way a table can — one bad price
 * in the middle of a line is a spike drawn from a number nobody sent. So the
 * payload is parsed once, here, into a series the geometry can consume without
 * asking another question:
 *
 *   * a bar missing any of t/o/h/l/c is dropped (it has no position and no
 *     body), and a negative volume is read as zero rather than as a bar that
 *     points downward;
 *   * repeated timestamps keep the last write — the same convention as a
 *     real-time feed, where a correction replaces the print;
 *   * bars are sorted by time, because the provider's order is not a promise
 *     and every x-coordinate downstream assumes ascending time;
 *   * an absent `previous_close` stays null; it is never read as the first
 *     close, which would print a 0.00 % change that the data did not state.
 *
 * `receivedAt` is stamped by the hook that fetched the series. It is not on the
 * wire; it is how the canvas can report "data arrived → first paint" without a
 * second clock.
 */

export const CHART_RANGES = ["1d", "5d", "1mo", "6mo", "1y"] as const;

export type ChartRange = (typeof CHART_RANGES)[number];

/** The selector's labels: the short form a 10 px button can hold. */
export const RANGE_LABELS: Readonly<Record<ChartRange, string>> = {
  "1d": "1D",
  "5d": "5D",
  "1mo": "1M",
  "6mo": "6M",
  "1y": "1Y",
};

/** The long form, for a label a screen reader hears rather than sees. */
export const RANGE_NAMES: Readonly<Record<ChartRange, string>> = {
  "1d": "1 day",
  "5d": "5 days",
  "1mo": "1 month",
  "6mo": "6 months",
  "1y": "1 year",
};

export function isChartRange(value: unknown): value is ChartRange {
  return typeof value === "string" && (CHART_RANGES as readonly string[]).includes(value);
}

/** One OHLCV bar. `t` is epoch seconds, not milliseconds. */
export interface Bar {
  readonly t: number;
  readonly o: number;
  readonly h: number;
  readonly l: number;
  readonly c: number;
  readonly v: number;
}

/** A parsed chart document: the wire's facts, in the shape the geometry wants. */
export interface ChartSeries {
  readonly symbol: string;
  readonly range: ChartRange;
  readonly interval: string;
  readonly timezone: string;
  /** The prior session's close, when the provider states it. */
  readonly previousClose: number | null;
  readonly bars: readonly Bar[];
  readonly source: string;
  readonly truncated: boolean;
  /** `performance.now()` when the payload resolved; 0 when never fetched. */
  readonly receivedAt: number;
}

export interface SeriesChange {
  readonly last: number | null;
  readonly previous: number | null;
  readonly change: number | null;
  readonly percent: number | null;
}

/** A payload that cannot describe a chart at all. Never a half-drawn line. */
export class ChartMalformed extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ChartMalformed";
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function finite(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function text(value: unknown, fallback: string): string {
  return typeof value === "string" && value.trim().length > 0 ? value : fallback;
}

/** `number | null` for a price that must exist for a bar to be drawable. */
function price(value: unknown): number | null {
  const number = finite(value);
  return number !== null && number > 0 ? number : null;
}

function parseBar(raw: unknown): Bar | null {
  if (!isRecord(raw)) return null;
  const t = finite(raw["t"]);
  const o = price(raw["o"]);
  const h = price(raw["h"]);
  const l = price(raw["l"]);
  const c = price(raw["c"]);
  // A timestamp and four prices are the bar; volume is optional and may be
  // legitimately absent for an index (^GSPC reports no consolidated volume).
  if (t === null || o === null || h === null || l === null || c === null) return null;
  const volume = finite(raw["v"]);
  return { t, o, h, l, c, v: volume !== null && volume > 0 ? volume : 0 };
}

function parseBars(raw: unknown): Bar[] {
  if (!Array.isArray(raw)) throw new ChartMalformed("payload has no bars array");
  const byTime = new Map<number, Bar>();
  for (const entry of raw) {
    const bar = parseBar(entry);
    if (bar === null) continue;
    // The feed's correction convention: the later row for a timestamp wins.
    byTime.set(bar.t, bar);
  }
  return [...byTime.values()].sort((left, right) => left.t - right.t);
}

/**
 * Read one chart document.
 *
 * Throws `ChartMalformed` when the body is not an object or has no `bars`
 * array. An empty array is a valid, empty series: the endpoint answered, there
 * were no trades, and the component must say so rather than draw nothing.
 */
export function parseChart(
  body: unknown,
  fallback: { readonly symbol: string; readonly range: ChartRange },
): ChartSeries {
  if (!isRecord(body)) throw new ChartMalformed("payload is not an object");

  const bars = parseBars(body["bars"]);
  const previous = finite(body["previous_close"]);
  const requestedRange = body["range"];

  return {
    symbol: text(body["symbol"], fallback.symbol).toUpperCase(),
    range: isChartRange(requestedRange) ? requestedRange : fallback.range,
    interval: text(body["interval"], ""),
    timezone: text(body["timezone"], "UTC"),
    previousClose: previous !== null && previous > 0 ? previous : null,
    bars,
    source: text(body["source"], ""),
    truncated: body["truncated"] === true,
    receivedAt: 0,
  };
}

/**
 * Last close against the prior close — what the card's header and the chart's
 * toolbar print.
 *
 * The prior close is the wire's `previous_close` when it is stated. Falling
 * back to the first bar is a *rendering* convenience (a 1-year chart's "change"
 * is its span, which is a true statement about the series) and is labelled as
 * such by the caller; it is never written back into `previousClose`.
 */
export function seriesChange(series: ChartSeries): SeriesChange {
  const first = series.bars[0];
  const last = series.bars[series.bars.length - 1];
  if (first === undefined || last === undefined) {
    return { last: null, previous: null, change: null, percent: null };
  }
  const previous = series.previousClose ?? first.c;
  const change = last.c - previous;
  return {
    last: last.c,
    previous,
    change,
    percent: previous !== 0 ? (change / previous) * 100 : null,
  };
}
