/**
 * The browser proof harness: the real `ChartsPage`, the real stylesheet, and a
 * deterministic series behind a mocked endpoint.
 *
 * `npm run build` never sees this directory (it is its own entry), so it costs
 * the product nothing. It exists because jsdom can prove the geometry but not
 * the frame: the paint time, the first-paint latency, the raster, DPR-2 text
 * and pointer coordinates all need a browser.
 *
 * `window.__chartProof` is the measurement hook. It runs the *shipped* geometry
 * and painter — not a copy — against an offscreen canvas of the on-screen size,
 * and flushes the raster so the number includes pixels rather than only the
 * command queue.
 */

import { createRoot } from "react-dom/client";

import "@/styles/index.css";
import { ChartsPage, chartGeometry, paintChart, readChartTheme, type ChartRange } from "@/charts";
import { barsFor } from "@/charts/__fixtures__/chart";

const REAL_RANGES: readonly ChartRange[] = ["1d", "5d", "1mo", "6mo", "1y"];

function wireFor(symbol: string, range: ChartRange): unknown {
  const bars = barsFor(symbol, range);
  const first = bars[0];
  return {
    symbol,
    range,
    interval: range === "1d" ? "5m" : "1d",
    timezone: "America/New_York",
    previous_close: first ? first.o : null,
    bars: bars.map((entry) => ({ ...entry })),
    source: "proof_fixture",
    truncated: false,
  };
}

const requests: string[] = [];

window.fetch = (input: RequestInfo | URL): Promise<Response> => {
  const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
  const match = /\/market\/chart\/([^?]+)\?range=([^&]+)/.exec(url);
  if (match === null) return Promise.reject(new Error(`unexpected fetch: ${url}`));
  requests.push(url);
  const symbol = decodeURIComponent(match[1] as string);
  const range = decodeURIComponent(match[2] as string);
  if (!REAL_RANGES.includes(range as ChartRange)) {
    return Promise.reject(new Error(`unexpected range: ${range}`));
  }
  return Promise.resolve(
    new Response(JSON.stringify(wireFor(symbol, range as ChartRange)), {
      status: 200,
      headers: { "content-type": "application/json" },
    }),
  );
};

function percentile(values: readonly number[], fraction: number): number {
  const sorted = [...values].sort((left, right) => left - right);
  return sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * fraction))] as number;
}

declare global {
  interface Window {
    __chartProof?: {
      requests: readonly string[];
      reset: () => void;
      bench: (runs?: number) => Record<string, unknown>;
      canvas: () => HTMLCanvasElement | null;
    };
  }
}

window.__chartProof = {
  get requests() {
    return [...requests];
  },
  reset() {
    requests.length = 0;
  },
  canvas() {
    return document.querySelector('[data-testid="chart-canvas"]') as HTMLCanvasElement | null;
  },
  bench(runs = 24) {
    const onScreen = document.querySelector('[data-testid="chart-canvas"]') as HTMLCanvasElement | null;
    const box = onScreen?.getBoundingClientRect() ?? { width: 1000, height: 320 };
    const width = Math.round(box.width);
    const height = Math.round(box.height);

    const bars = barsFor("AAPL", "1y");
    const geometry = chartGeometry({
      width,
      height,
      bars,
      previousClose: bars[0]?.o ?? null,
      range: "1y",
      timezone: "America/New_York",
    });

    const layoutTimes: number[] = [];
    for (let index = 0; index < 8; index += 1) {
      const started = performance.now();
      chartGeometry({
        width,
        height,
        bars,
        previousClose: bars[0]?.o ?? null,
        range: "1y",
        timezone: "America/New_York",
      });
      layoutTimes.push(performance.now() - started);
    }

    const theme = readChartTheme(onScreen);
    const offscreen = document.createElement("canvas");
    offscreen.width = width;
    offscreen.height = height;
    const ctx = offscreen.getContext("2d", { alpha: false }) as CanvasRenderingContext2D;

    const line: number[] = [];
    const lineFlush: number[] = [];
    const candle: number[] = [];
    for (let index = 0; index < runs; index += 1) {
      const started = performance.now();
      paintChart(ctx, geometry, theme, { dpr: 1, kind: "line", hoverIndex: index % 250 });
      const issued = performance.now();
      ctx.getImageData(0, 0, 1, 1);
      lineFlush.push(performance.now() - started);
      line.push(issued - started);

      const candleStart = performance.now();
      paintChart(ctx, geometry, theme, { dpr: 1, kind: "candle" });
      candle.push(performance.now() - candleStart);
    }

    return {
      size: { width, height },
      bars: geometry.bars.length,
      segments: geometry.segments.length,
      xTicks: geometry.xTicks.length,
      yTicks: geometry.yTicks.length,
      geometry: { median: percentile(layoutTimes, 0.5), min: Math.min(...layoutTimes) },
      paint: {
        median: percentile(line, 0.5),
        min: Math.min(...line),
        p95: percentile(line, 0.95),
      },
      paintWithRaster: {
        median: percentile(lineFlush, 0.5),
        min: Math.min(...lineFlush),
        p95: percentile(lineFlush, 0.95),
      },
      candlePaint: {
        median: percentile(candle, 0.5),
        min: Math.min(...candle),
        p95: percentile(candle, 0.95),
      },
    };
  },
};

const root = document.getElementById("root");
if (root) {
  createRoot(root).render(
    <div style={{ position: "fixed", inset: 0, display: "flex" }}>
      <ChartsPage />
    </div>,
  );
}
