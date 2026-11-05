/**
 * The crypto detail page: header, chart, stats grid, description, back link,
 * and the four load/error states.
 *
 * The reads are stubbed at the network seam (`globalThis.fetch`), so what this
 * file proves is the page's own contract — the symbol from the URL drives the
 * header, the stats grid, and the chart.
 */

import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CryptoDetailPage } from "./CryptoDetailPage";
import { parseQuoteAttr } from "@/lib/quoteAttrs";

/* ---------------------------------------------------------------------------
 * Fixtures
 * ------------------------------------------------------------------------ */

function altResponse(symbol = "BTC-USD") {
  return {
    as_of: "2026-11-05T14:00:00Z",
    class: "crypto",
    universe_total: 1,
    measured: 1,
    per: 200,
    method: "last_5m_close_vs_chart_previous_close",
    rows: [
      {
        symbol,
        name: "Bitcoin",
        price: 80024.02,
        change_pct: 3.47,
        volume: 40979443712.0,
        currency: "USD",
      },
    ],
  };
}

function metaResponse(symbol = "BTC-USD") {
  return {
    as_of: "2026-11-05T14:00:00Z",
    method: "coingecko",
    universe_total: 1,
    measured: 1,
    rows: {
      [symbol]: {
        symbol,
        id: "bitcoin",
        name: "Bitcoin",
        image: "https://img.local/btc.png",
        market_cap: 1_573_000_000_000,
        market_cap_rank: 1,
        circulating_supply: 19_500_000,
        max_supply: 21_000_000,
        total_supply: 19_500_000,
        fully_diluted_valuation: 1_573_000_000_000,
        high_24h: 81004.0,
        low_24h: 78231.0,
        ath: 126080.0,
        ath_change_pct: -36.53,
        pct_1h: 0.12,
        pct_7d: -2.4,
        pct_30d: 8.9,
        tvl: null,
        sources: ["coingecko"],
      },
    },
  };
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function stubFetch(routes: {
  alt?: (url: string) => Response | Promise<Response>;
  meta?: (url: string) => Response | Promise<Response>;
  chart?: (url: string) => Response | Promise<Response>;
  fallback?: (url: string) => Response | Promise<Response>;
}) {
  const mock = vi.fn((input: RequestInfo | URL, _init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/market/alt") && routes.alt) return Promise.resolve(routes.alt(url));
    if (url.includes("/market/crypto/meta") && routes.meta) return Promise.resolve(routes.meta(url));
    if (url.includes("/market/chart/") && routes.chart) return Promise.resolve(routes.chart(url));
    if (routes.fallback) return Promise.resolve(routes.fallback(url));
    return Promise.resolve(new Response("null", { status: 404 }));
  });
  vi.stubGlobal("fetch", mock);
  return mock;
}

beforeEach(() => {
  window.history.replaceState(null, "", "/app/crypto/BTC-USD");
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

/* ---------------------------------------------------------------------------
 * Tests
 * ------------------------------------------------------------------------ */

describe("CryptoDetailPage", () => {
  it("renders a skeleton while reads are loading", () => {
    stubFetch({
      alt: () => new Promise<Response>(() => {}),
      meta: () => new Promise<Response>(() => {}),
      chart: () => new Promise<Response>(() => {}),
    });

    render(<CryptoDetailPage symbol="BTC-USD" />);

    expect(screen.getByTestId("crypto-detail-skeleton")).toBeInTheDocument();
  });

  it("renders the header with logo, name, symbol, price, change and rank", async () => {
    stubFetch({
      alt: () => json(altResponse()),
      meta: () => json(metaResponse()),
      chart: () => json({ bars: [], symbol: "BTC-USD", range: "1d" }),
    });

    render(<CryptoDetailPage symbol="BTC-USD" />);

    const header = await screen.findByTestId("crypto-detail-header", undefined, {
      timeout: 4_000,
    });

    expect(header.getAttribute("data-quote")).toBeTruthy();
    const quote = parseQuoteAttr(header.getAttribute("data-quote"));
    expect(quote?.symbol).toBe("BTC-USD");
    expect(quote?.name).toBe("Bitcoin");
    expect(quote?.price).toBe(80024.02);
    expect(quote?.changePct).toBe(3.47);
    expect(quote?.kind).toBe("crypto");

    expect(screen.getByTestId("crypto-detail-name").textContent).toBe("Bitcoin");
    expect(screen.getByTestId("crypto-detail-symbol").textContent).toBe("BTC-USD");
    expect(screen.getByTestId("crypto-detail-price").textContent).toBe("$80,024.02");
    expect(screen.getByTestId("crypto-detail-change").textContent).toContain("+3.47%");
    expect(screen.getByTestId("crypto-detail-rank").textContent).toBe("#1");
  });

  it("renders the back link to the crypto list", async () => {
    stubFetch({
      alt: () => json(altResponse()),
      meta: () => json(metaResponse()),
      chart: () => json({ bars: [], symbol: "BTC-USD", range: "1d" }),
    });

    render(<CryptoDetailPage symbol="BTC-USD" />);

    const back = await screen.findByTestId("crypto-detail-back", undefined, { timeout: 4_000 });
    expect(back).toHaveTextContent("Back to Crypto List");
    expect(back).toHaveAttribute("href", "/app/crypto");
  });

  it("renders the stats grid with all available fields", async () => {
    stubFetch({
      alt: () => json(altResponse()),
      meta: () => json(metaResponse()),
      chart: () => json({ bars: [], symbol: "BTC-USD", range: "1d" }),
    });

    render(<CryptoDetailPage symbol="BTC-USD" />);

    await screen.findByTestId("crypto-detail-stats", undefined, {
      timeout: 4_000,
    });

    expect(screen.getByTestId("crypto-stat-market-cap").textContent).toContain("Market Cap");
    expect(screen.getByTestId("crypto-stat-high-24h").textContent).toContain("24h High");
    expect(screen.getByTestId("crypto-stat-low-24h").textContent).toContain("24h Low");
    expect(screen.getByTestId("crypto-stat-ath").textContent).toContain("All-Time High");
    expect(screen.getByTestId("crypto-stat-supply").textContent).toContain("Circulating Supply");
    expect(screen.getByTestId("crypto-stat-pct-1h").textContent).toContain("1h Change");
    expect(screen.getByTestId("crypto-stat-pct-7d").textContent).toContain("7d Change");
    expect(screen.getByTestId("crypto-stat-pct-30d").textContent).toContain("30d Change");
    expect(screen.getByTestId("crypto-stat-volume").textContent).toContain("24h Volume");

    // Market cap value
    const mcap = screen.getByTestId("crypto-stat-market-cap");
    expect(mcap.textContent).toContain("$1.57T");
  });

  it("renders the description section with the coin name", async () => {
    stubFetch({
      alt: () => json(altResponse()),
      meta: () => json(metaResponse()),
      chart: () => json({ bars: [], symbol: "BTC-USD", range: "1d" }),
    });

    render(<CryptoDetailPage symbol="BTC-USD" />);

    const desc = await screen.findByTestId("crypto-detail-description", undefined, {
      timeout: 4_000,
    });
    expect(desc.textContent).toContain("Bitcoin");
  });

  it("renders the chart section", async () => {
    stubFetch({
      alt: () => json(altResponse()),
      meta: () => json(metaResponse()),
      chart: () => json({ bars: [], symbol: "BTC-USD", range: "1d" }),
    });

    render(<CryptoDetailPage symbol="BTC-USD" />);

    const chartSection = await screen.findByTestId("crypto-detail-chart-section", undefined, {
      timeout: 4_000,
    });
    expect(chartSection).toBeInTheDocument();
  });

  it("shows the error state when both reads fail", async () => {
    stubFetch({
      alt: () => json({ error: "alt failed" }, 500),
      meta: () => json({ error: "meta failed" }, 500),
      chart: () => json({ error: "chart failed" }, 500),
    });

    render(<CryptoDetailPage symbol="BTC-USD" />);

    const error = await screen.findByTestId("crypto-detail-error", undefined, {
      timeout: 4_000,
    });
    expect(error).toBeInTheDocument();
    expect(error.textContent).toContain("Could not load crypto data");
  });

  it("shows the main content even when meta fails but price succeeds", async () => {
    stubFetch({
      alt: () => json(altResponse()),
      meta: () => json({ error: "meta failed" }, 500),
      chart: () => json({ bars: [], symbol: "BTC-USD", range: "1d" }),
    });

    render(<CryptoDetailPage symbol="BTC-USD" />);

    const header = await screen.findByTestId("crypto-detail-header", undefined, {
      timeout: 4_000,
    });
    expect(header).toBeInTheDocument();
    // Meta is null, so the rank badge is absent
    expect(screen.queryByTestId("crypto-detail-rank")).toBeNull();
    // Price still shows
    expect(screen.getByTestId("crypto-detail-price").textContent).toBe("$80,024.02");
  });

  it("shows the main content even when price fails but meta succeeds", async () => {
    stubFetch({
      alt: () => json({ error: "alt failed" }, 500),
      meta: () => json(metaResponse()),
      chart: () => json({ bars: [], symbol: "BTC-USD", range: "1d" }),
    });

    render(<CryptoDetailPage symbol="BTC-USD" />);

    const header = await screen.findByTestId("crypto-detail-header", undefined, {
      timeout: 4_000,
    });
    expect(header).toBeInTheDocument();
    // Meta still shows rank
    expect(screen.getByTestId("crypto-detail-rank").textContent).toBe("#1");
    // Price is absent
    expect(screen.getByTestId("crypto-detail-price").textContent).toBe("—");
  });

  it("shows the TVL stat card only when TVL is non-null", async () => {
    // With TVL = null
    stubFetch({
      alt: () => json(altResponse()),
      meta: () => json(metaResponse()),
      chart: () => json({ bars: [], symbol: "BTC-USD", range: "1d" }),
    });

    render(<CryptoDetailPage symbol="BTC-USD" />);

    await screen.findByTestId("crypto-detail-header", undefined, { timeout: 4_000 });
    expect(screen.queryByTestId("crypto-stat-tvl")).toBeNull();
  });

  it("shows the TVL stat card when TVL is present", async () => {
    const metaWithTvl = {
      ...metaResponse(),
      rows: {
        "BTC-USD": { ...metaResponse().rows["BTC-USD"], tvl: 12e9 },
      },
    };
    stubFetch({
      alt: () => json(altResponse()),
      meta: () => json(metaWithTvl),
      chart: () => json({ bars: [], symbol: "BTC-USD", range: "1d" }),
    });

    render(<CryptoDetailPage symbol="BTC-USD" />);

    const tvl = await screen.findByTestId("crypto-stat-tvl", undefined, { timeout: 4_000 });
    expect(tvl).toBeInTheDocument();
    expect(tvl.textContent).toContain("TVL");
  });

  it("shows the main page test id with the symbol", async () => {
    stubFetch({
      alt: () => json(altResponse()),
      meta: () => json(metaResponse()),
      chart: () => json({ bars: [], symbol: "BTC-USD", range: "1d" }),
    });

    render(<CryptoDetailPage symbol="BTC-USD" />);

    const page = await screen.findByTestId("crypto-detail-page", undefined, {
      timeout: 4_000,
    });
    expect(page.getAttribute("data-symbol")).toBe("BTC-USD");
  });

  it("renders the ATH stat with the change from ATH", async () => {
    stubFetch({
      alt: () => json(altResponse()),
      meta: () => json(metaResponse()),
      chart: () => json({ bars: [], symbol: "BTC-USD", range: "1d" }),
    });

    render(<CryptoDetailPage symbol="BTC-USD" />);

    const ath = await screen.findByTestId("crypto-stat-ath", undefined, { timeout: 4_000 });
    expect(ath.textContent).toContain("$126,080.00");
    expect(ath.textContent).toContain("from ATH");
  });

  it("renders the supply stat formatted correctly", async () => {
    stubFetch({
      alt: () => json(altResponse()),
      meta: () => json(metaResponse()),
      chart: () => json({ bars: [], symbol: "BTC-USD", range: "1d" }),
    });

    render(<CryptoDetailPage symbol="BTC-USD" />);

    const supply = await screen.findByTestId("crypto-stat-supply", undefined, {
      timeout: 4_000,
    });
    expect(supply.textContent).toContain("19.50M");
  });

  it("renders the 1h/7d/30d change stats", async () => {
    stubFetch({
      alt: () => json(altResponse()),
      meta: () => json(metaResponse()),
      chart: () => json({ bars: [], symbol: "BTC-USD", range: "1d" }),
    });

    render(<CryptoDetailPage symbol="BTC-USD" />);

    await screen.findByTestId("crypto-detail-header", undefined, { timeout: 4_000 });

    expect(screen.getByTestId("crypto-stat-pct-1h").textContent).toContain("+0.1%");
    expect(screen.getByTestId("crypto-stat-pct-7d").textContent).toContain("-2.4%");
    expect(screen.getByTestId("crypto-stat-pct-30d").textContent).toContain("+8.9%");
  });
});