/**
 * The dashboard's fixture: one document, shaped exactly like `GET /market/home`
 * once the endpoint exists.
 *
 * It is deliberately full — five indices, three sparks, forty movers, seven map
 * sectors, three headlines, five filings — so a test that renders the page is
 * testing the layout the operator described and not a three-row sketch of it.
 * Every number is a real-looking number and no field is a zero unless the zero
 * is the point (`unchanged`).
 */

import { vi } from "vitest";

export interface WireOverrides {
  readonly [key: string]: unknown;
}

/** A rising or falling series of `count` closes, ending at `last`. */
export function series(last: number, count = 30, step = 0.4): number[] {
  return Array.from({ length: count }, (_, index) =>
    Number((last - (count - 1 - index) * step).toFixed(2)),
  );
}

export function moverRow(
  symbol: string,
  changePct: number,
  price = 100 + changePct,
): Record<string, unknown> {
  return { symbol, name: `${symbol} Holdings`, price, change_pct: changePct };
}

const GAINERS = [
  "NVDA", "SMCI", "PLTR", "COIN", "MSTR", "AFRM", "SOFI", "RIVN",
  "HOOD", "DKNG", "U", "IONQ",
].map((symbol, index) => moverRow(symbol, 12 - index * 0.5));

const NEW_HIGH = [
  "LLY", "VRTX", "REGN", "ANET", "PANW", "CRWD", "NOW", "ADBE", "INTU", "ISRG",
].map((symbol, index) => moverRow(symbol, 3.4 - index * 0.1));

const OVERBOUGHT = [
  "MSFT", "AAPL", "GOOGL", "AMZN", "META", "TSLA", "AVGO", "ORCL", "CRM",
].map((symbol, index) => moverRow(symbol, 2.1 - index * 0.1));

const NEW_LOW = [
  "INTC", "PFE", "VZ", "T", "F", "GM", "BA", "KHC", "WBA",
].map((symbol, index) => moverRow(symbol, -4.2 + index * 0.2));

export function homeDocument(overrides: WireOverrides = {}): Record<string, unknown> {
  return {
    as_of: "2026-10-31T20:00:00Z",
    indices: [
      { symbol: "^GSPC", label: "S&P 500", price: 7580.07, change_pct: -1.82, closes: series(7580.07) },
      { symbol: "^IXIC", label: "Nasdaq", price: 23104.4, change_pct: -2.04, closes: series(23104.4) },
      { symbol: "^DJI", label: "Dow", price: 47112.9, change_pct: -0.94, closes: series(47112.9) },
      { symbol: "^RUT", label: "Russell 2000", price: 2388.31, change_pct: -1.41, closes: series(2388.31) },
      { symbol: "^VIX", label: "VIX", price: 18.42, change_pct: 6.28, closes: series(18.42) },
    ],
    breadth: {
      universe: 5576,
      advancing: 1557,
      declining: 3932,
      unchanged: 189,
      new_highs: 45,
      new_lows: 54,
      above_50: 2104,
      below_50: 3472,
    },
    sparks: [
      { symbol: "AAPL", name: "Apple Inc.", price: 344.63, change_pct: -1.23, volume: 42_421_212, closes: series(344.63, 30, 0.8) },
      { symbol: "MSFT", name: "Microsoft Corp.", price: 512.08, change_pct: 0.64, volume: 18_002_311, closes: series(512.08, 30, 1.1) },
      { symbol: "NVDA", name: "NVIDIA Corp.", price: 188.44, change_pct: 3.12, volume: 310_552_004, closes: series(188.44, 30, 1.4) },
    ],
    movers: {
      gainers: GAINERS,
      new_high: NEW_HIGH,
      overbought: OVERBOUGHT,
      new_low: NEW_LOW,
    },
    map: {
      sectors: [
        { id: "Technology", value: -1.2, symbols: [{ s: "AAPL", c: 5034.7, v: -1.23 }, { s: "MSFT", c: 3802.1, v: 0.64 }] },
        { id: "Finance", value: -0.48, symbols: [{ s: "JPM", c: 780.4, v: -0.48 }, { s: "BAC", c: 340.2, v: -0.61 }] },
        { id: "Healthcare", value: 0.31, symbols: [{ s: "LLY", c: 902.5, v: 0.31 }, { s: "UNH", c: 512.3, v: 0.12 }] },
        { id: "Energy", value: 1.42, symbols: [{ s: "XOM", c: 480.1, v: 1.42 }, { s: "CVX", c: 290.7, v: 1.1 }] },
        { id: "Consumer", value: -0.22, symbols: [{ s: "AMZN", c: 2101.4, v: -0.22 }] },
        { id: "Industrials", value: 0.07, symbols: [{ s: "CAT", c: 190.2, v: 0.07 }] },
        { id: "Utilities", value: null, symbols: [{ s: "NEE", c: 150.9, v: 0 }] },
      ],
      tiles: 600,
      tiles_total: 5573,
    },
    news: [
      {
        headline: "Futures slide as chipmakers guide lower into the weekend",
        url: "https://example.com/news/chips",
        source: "Reuters",
        published_at: "2026-10-31T19:42:00Z",
      },
      {
        headline: "Fed officials split on the pace of cuts, minutes show",
        url: "https://example.com/news/fed",
        source: "Bloomberg",
        published_at: "2026-10-31T18:10:00Z",
      },
      {
        headline: "Oil climbs on supply concerns in the Gulf",
        url: "https://example.com/news/oil",
        source: "AP",
        published_at: "2026-10-31T16:05:00Z",
      },
    ],
    filings: [
      { symbol: "AAPL", owner: "Newstead Jennifer", role: "General Counsel", filed: "2026-10-29" },
      { symbol: "MSFT", owner: "Hood Amy", role: "Chief Financial Officer", filed: "2026-10-29" },
      { symbol: "NVDA", owner: "Colette Kress", role: "Chief Financial Officer", filed: "2026-10-28" },
      { symbol: "TSLA", owner: "Zhu Xiaotong", role: "Director", filed: "2026-10-28" },
      { symbol: "META", owner: "Susan Li", role: "Chief Financial Officer", filed: "2026-10-27" },
    ],
    unavailable: [],
    method: "one pass over the scan; indices and sparks from the chart reader",
    ...overrides,
  };
}

/**
 * jsdom has no 2D context and no ResizeObserver. Both are stubbed so a component
 * test proves the *component's* behaviour (labels, guards, shapes) rather than
 * jsdom's gaps, without a "Not implemented" line per canvas.
 */
export function stubCanvas(): void {
  const ctx = {
    clearRect: () => undefined,
    fillRect: () => undefined,
    strokeRect: () => undefined,
    beginPath: () => undefined,
    closePath: () => undefined,
    moveTo: () => undefined,
    lineTo: () => undefined,
    stroke: () => undefined,
    fill: () => undefined,
    arc: () => undefined,
    rect: () => undefined,
    setTransform: () => undefined,
    setLineDash: () => undefined,
    fillText: () => undefined,
    measureText: () => ({ width: 8 }),
    font: "",
    textAlign: "left",
    textBaseline: "alphabetic",
    lineWidth: 1,
    strokeStyle: "",
    fillStyle: "",
    globalAlpha: 1,
    save: () => undefined,
    restore: () => undefined,
  };
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation(
    () => ctx as unknown as CanvasRenderingContext2D,
  );
}

export function stubResizeObserver(width = 320, height = 40): void {
  class TestResizeObserver {
    private readonly callback: ResizeObserverCallback;

    constructor(callback: ResizeObserverCallback) {
      this.callback = callback;
    }

    observe(): void {
      queueMicrotask(() => {
        const entry = { contentRect: { width, height } } as unknown as ResizeObserverEntry;
        this.callback([entry], this as unknown as ResizeObserver);
      });
    }

    unobserve(): void {}

    disconnect(): void {}
  }
  vi.stubGlobal("ResizeObserver", TestResizeObserver);
}
