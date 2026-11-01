/**
 * The product-shaped measurement entry: the page, with the network answered by a
 * small hand-written payload instead of the 5,576-row generator.
 *
 * The generator is proof scaffolding, not product code; counting it would
 * overstate what the shell pays to ship Maps. `bundle delta = gzip(this) −
 * gzip(baseline)` is the page's marginal gzip weight.
 */

import { createRoot } from "react-dom/client";

import "@/styles/index.css";
import { MapPage } from "@/map";

const payload = {
  as_of: "2026-10-31T20:58:12+00:00",
  region: "us",
  metric: "change",
  universe_total: 9,
  mapped: 7,
  sectors: [
    {
      id: "technology",
      label: "Technology",
      count: 3,
      value: 0.83,
      adv: 2,
      dec: 1,
      symbols: [
        { s: "AAPL", c: 5034.7, v: 2.1 },
        { s: "MSFT", c: 3100.2, v: 1.2 },
        { s: "NVDA", c: 1204.9, v: -0.8 },
      ],
    },
    {
      id: "financials",
      label: "Financials",
      count: 2,
      value: -0.55,
      adv: 1,
      dec: 1,
      symbols: [
        { s: "JPM", c: 612.4, v: -1.4 },
        { s: "BAC", c: 301.7, v: 0.3 },
      ],
    },
    {
      id: "energy",
      label: "Energy",
      count: 2,
      value: -1.3,
      adv: 0,
      dec: 2,
      symbols: [
        { s: "XOM", c: 452.8, v: -2.6 },
        { s: "CVX", c: 208.3, v: 0 },
      ],
    },
  ],
};

window.fetch = (input: RequestInfo | URL): Promise<Response> => {
  const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
  if (url.startsWith("/market/map")) {
    return Promise.resolve(
      new Response(JSON.stringify(payload), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
  }
  return Promise.reject(new Error(`unexpected fetch: ${url}`));
};

const root = document.getElementById("root");
if (root) {
  createRoot(root).render(
    <div style={{ position: "fixed", inset: 0, display: "flex" }}>
      <MapPage />
    </div>,
  );
}
