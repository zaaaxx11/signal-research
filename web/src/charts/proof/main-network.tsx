/**
 * The product-shaped entry for the bundle delta: the real `ChartsPage`, with a
 * small hand-written payload per symbol instead of the fixture generator.
 *
 * The generator is proof scaffolding; counting it would overstate what the
 * shell pays to ship Charts. `bundle delta = gzip(this) − gzip(baseline)` is
 * the page's marginal gzip weight.
 */

import { createRoot } from "react-dom/client";

import "@/styles/index.css";
import { ChartsPage, type ChartRange } from "@/charts";

const LEVELS: Readonly<Record<string, number>> = {
  "^GSPC": 5900,
  "^NDX": 21000,
  "^DJI": 44000,
  "^RUT": 2300,
  "^VIX": 18,
  AAPL: 230,
};

function payload(symbol: string, range: ChartRange): unknown {
  const level = LEVELS[symbol] ?? 100;
  const start = 1_767_364_200;
  const bars = Array.from({ length: 12 }, (_unused, index) => {
    const open = level + index * 0.4;
    const close = open + (index % 3) * 0.3 - 0.2;
    return {
      t: start + index * 300,
      o: open,
      h: Math.max(open, close) + 0.5,
      l: Math.min(open, close) - 0.5,
      c: close,
      v: 1_500_000 + index * 1000,
    };
  });
  return {
    symbol,
    range,
    interval: "5m",
    timezone: "America/New_York",
    previous_close: level,
    bars,
    source: "proof_fixture",
    truncated: false,
  };
}

window.fetch = (input: RequestInfo | URL): Promise<Response> => {
  const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
  const match = /\/market\/chart\/([^?]+)\?range=([^&]+)/.exec(url);
  if (match === null) return Promise.reject(new Error(`unexpected fetch: ${url}`));
  const symbol = decodeURIComponent(match[1] as string);
  const range = decodeURIComponent(match[2] as string) as ChartRange;
  return Promise.resolve(
    new Response(JSON.stringify(payload(symbol, range)), {
      status: 200,
      headers: { "content-type": "application/json" },
    }),
  );
};

const root = document.getElementById("root");
if (root) {
  createRoot(root).render(
    <div style={{ position: "fixed", inset: 0, display: "flex" }}>
      <ChartsPage />
    </div>,
  );
}
