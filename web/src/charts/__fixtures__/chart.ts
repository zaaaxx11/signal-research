/**
 * Chart fixtures: a deterministic series, in both the shapes a test needs —
 * `Bar[]` for the geometry and the wire's snake_case document for `fetch`.
 *
 * The times are real market times (14:30 UTC, weekdays only) so the weekend and
 * session-gap arithmetic is exercised against the same clock the browser uses,
 * not against 0, 1, 2.
 */

import type { Bar, ChartRange, ChartSeries } from "../types";

/** Friday 2026-01-02 14:30:00 UTC, epoch seconds. */
export const BASE_TIME = 1767364200;

const DAY = 86_400;

/** The level each card's series starts from; keeps the gallery realistic. */
export const INDEX_LEVEL: Readonly<Record<string, number>> = {
  "^GSPC": 5900,
  "^NDX": 21000,
  "^DJI": 44000,
  "^RUT": 2300,
  "^VIX": 18,
  AAPL: 230,
  MSFT: 470,
};

const RANGE_BARS: Readonly<Record<ChartRange, number>> = {
  "1d": 78,
  "5d": 130,
  "1mo": 22,
  "6mo": 126,
  "1y": 250,
  // Five trading years, and over the route's default thousand-bar cap on
  // purpose: a fixture that stopped at 1,000 could not notice the cap.
  "5y": 1_260,
  "max": 3_500,
};

export function bar(t: number, o: number, h: number, l: number, c: number, v = 1_000_000): Bar {
  return { t, o, h, l, c, v };
}

/** Whether a UTC day is a weekday — the market's own filter, not a holiday table. */
function weekday(time: number): boolean {
  const day = new Date(time * 1000).getUTCDay();
  return day !== 0 && day !== 6;
}

export interface BarOptions {
  readonly start?: number;
  readonly step?: number;
  readonly level?: number;
  readonly volume?: number;
  readonly grow?: number;
}

/**
 * `count` weekday bars, 14:30 UTC, gently deterministic.
 *
 * The wave keeps opens and closes apart (so a candle has a body) without a
 * random seed: the same series on every run is what makes a pixel or a timing
 * assertion reproducible.
 */
export function tradingBars(count: number, options: BarOptions = {}): Bar[] {
  const start = options.start ?? BASE_TIME;
  const step = options.step ?? DAY;
  const level = options.level ?? 100;
  const volume = options.volume ?? 1_500_000;
  const grow = options.grow ?? 0.08;

  const bars: Bar[] = [];
  let time = start;
  for (let index = 0; index < count; index += 1) {
    if (index > 0) {
      time += step;
      while (step >= DAY && !weekday(time)) time += DAY;
    }
    const wave = Math.sin(index / 3) * level * 0.004 + Math.cos(index / 7) * level * 0.002;
    const open = level + index * level * grow * 0.01;
    const close = open + wave;
    const high = Math.max(open, close) + level * 0.003;
    const low = Math.min(open, close) - level * 0.003;
    bars.push({ t: time, o: open, h: high, l: low, c: close, v: volume + index * 1000 });
  }
  return bars;
}

/** Intraday bars at `step` seconds, for the 1D/5D shapes. */
export function intradayBars(count: number, step = 300, level = 5900): Bar[] {
  const bars: Bar[] = [];
  for (let index = 0; index < count; index += 1) {
    const time = BASE_TIME + index * step;
    const open = level + Math.sin(index / 5) * 2;
    const close = open + Math.cos(index / 4) * 1.5;
    const high = Math.max(open, close) + 1;
    const low = Math.min(open, close) - 1;
    bars.push({ t: time, o: open, h: high, l: low, c: close, v: 900_000 + index * 2000 });
  }
  return bars;
}

export function barsFor(symbol: string, range: ChartRange): Bar[] {
  const level = INDEX_LEVEL[symbol] ?? 100;
  const count = RANGE_BARS[range];
  if (range === "1d") return intradayBars(count, 300, level);
  if (range === "5d") return intradayBars(count, 900, level);
  return tradingBars(count, { level });
}

/** The JSON the endpoint would answer for one symbol/range. */
export function wireFor(
  symbol: string,
  range: ChartRange,
  overrides: Record<string, unknown> = {},
): Record<string, unknown> {
  const bars = barsFor(symbol, range);
  const first = bars[0] as Bar;
  return {
    symbol,
    range,
    interval: range === "1d" ? "5m" : "1d",
    timezone: "UTC",
    previous_close: first.o,
    bars: bars.map((entry) => ({
      t: entry.t,
      o: entry.o,
      h: entry.h,
      l: entry.l,
      c: entry.c,
      v: entry.v,
    })),
    source: "fixture",
    truncated: false,
    ...overrides,
  };
}

/** A parsed series, for tests that start after the fetch. */
export function seriesFor(
  symbol: string,
  range: ChartRange,
  bars?: readonly Bar[],
): ChartSeries {
  const rows = bars ?? barsFor(symbol, range);
  const first = rows[0];
  return {
    symbol,
    range,
    interval: "1d",
    timezone: "UTC",
    previousClose: first ? first.o : null,
    bars: rows,
    source: "fixture",
    truncated: false,
    receivedAt: 0,
  };
}

/** A `Response` with a JSON body. A body reads once, so always build fresh. */
export function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

interface Route {
  readonly status?: number;
  readonly body: unknown;
}

/**
 * A `fetch` that answers `/market/chart/{symbol}?range={range}` from a map.
 *
 * Keys are `SYMBOL|range`; a missing key answers 404, which is what the
 * endpoint does for a symbol it does not know. Every call is recorded by the
 * returned `vi.fn`, so a test can count requests per range.
 */
export function chartResponder(
  routes: Readonly<Record<string, Route | unknown>>,
  fallback?: (symbol: string, range: ChartRange) => Route | unknown,
): (input: RequestInfo | URL) => Promise<Response> {
  return (input: RequestInfo | URL): Promise<Response> => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
    const match = /\/market\/chart\/([^?]+)\?range=([^&]+)/.exec(url);
    if (match === null) return Promise.reject(new Error(`unexpected fetch: ${url}`));
    const symbol = decodeURIComponent(match[1] as string);
    const range = decodeURIComponent(match[2] as string) as ChartRange;
    const key = `${symbol}|${range}`;
    const route = routes[key] ?? (fallback ? fallback(symbol, range) : undefined);
    if (route === undefined) return Promise.resolve(json({ errorMessage: "unknown symbol" }, 404));
    if (typeof route === "object" && route !== null && "body" in route) {
      const typed = route as Route;
      return Promise.resolve(json(typed.body, typed.status ?? 200));
    }
    return Promise.resolve(json(route));
  };
}
