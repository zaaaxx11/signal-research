/**
 * Live-shaped fixtures for the ticker page.
 *
 * The fundamentals document is the detail panel's own fixture (the numbers
 * `GET /engine/fundamentals/AAPL` answered on 2026-06-27), so the snapshot test
 * formats real values rather than convenient ones. The selection row and the
 * statement document are shaped exactly as their endpoints answer, including a
 * null statement cell — the case the table must print as an em dash.
 */

import { vi } from "vitest";

import { fundamentalsPayload } from "@/detail/__fixtures__/detail";

import type { MetricValue } from "../snapshot";

export function snapshotMetrics(): Record<string, MetricValue> {
  return fundamentalsPayload()["metrics"] as Record<string, MetricValue>;
}

export function rowPayload(symbol = "AAPL"): Record<string, unknown> {
  return {
    mode: "symbols",
    region: "us",
    source: "tradingview",
    as_of: "2026-10-31T09:22:50Z",
    snapshot_id: "scr_us_market_cap_test",
    total: 1,
    returned: 1,
    rows: [
      {
        symbol,
        price: 343.36,
        change: 1.24,
        market_cap: 5_180_000_000_000,
        volume: 54_300_000,
        prev_close: 339.15,
        index_membership: "NDX, S&P 500",
        shares_outstanding: 14_594_200_000,
        float_shares: 14_576_745_336.8,
        short_ratio: 3.48,
        insider_own: 1.648,
        inst_own: 66.338,
        short_float: 0.9,
        sector: "Technology",
        industry: "Electronic Computers",
        company: "Apple Inc.",
        as_of: "2026-10-31T09:22:50Z",
      },
    ],
  };
}

/**
 * `GET /engine/snapshot/{symbol}`: one document, one unit per numeric key.
 *
 * The values are canonical for their unit — a `ratio` is 0.7654, not 76.54 —
 * because the whole point of the endpoint is that the client no longer has to
 * guess whether a number arrived as a fraction or a percentage. `optionable`
 * is a flag; a date is text; a key the engine cannot answer is `null` and is
 * named in `missing`.
 */
export function snapshotPayload(symbol = "AAPL"): Record<string, unknown> {
  const values: Record<string, unknown> = {
    index_membership: "NDX, S&P 500",
    market_cap: 5_180_000_000_000,
    enterprise_value: 5_242_000_000_000,
    net_income: 128_930_000_000,
    sales: 466_823_000_000,
    book_per_share: 7.36,
    cash_per_share: 2.71,
    dividend: 1.04,
    dividend_ttm: 1.04,
    dividend_ex_date: "2026-11-07",
    dividend_growth_3y: null,
    payout_ratio: 11.93,
    employees: 164_000,
    ipo_date: "1980-12-12",

    pe: 34.12,
    forward_pe: 29.4,
    peg: 2.31,
    ps: 8.19,
    pb: 47.31,
    pc: 22.14,
    pfcf: 31.53,
    ev_ebitda: 24.18,
    ev_sales: 8.42,
    quick_ratio: 0.93,
    current_ratio: 1.0032,
    debt_to_equity: 0.7654,
    lt_debt_to_equity: 0.6632,
    optionable: true,

    eps: 8.72,
    recommendation: 1.71,
    target_price: 372.5,
    earnings_date: "2026-10-29",

    insider_own: 1.648,
    inst_own: 66.338,
    roa: 33.64,
    roe: 119.91,

    shares_outstanding: 14_594_200_000,
    shares_float: 14_576_745_336.8,
    short_float: 0.9,
    short_ratio: 3.48,
    high_52w: 358.42,
    low_52w: 208.14,
    beta: 1.08,
    rsi: 55.35,
    average_volume: 52_100_000,
    volume: 54_300_000,

    perf_1w: 2.41,
    change: 1.24,
    prev_close: 339.15,
    price: 343.36,
  };

  const units: Record<string, string> = {
    market_cap: "usd",
    enterprise_value: "usd",
    net_income: "usd",
    sales: "usd",
    book_per_share: "usd",
    cash_per_share: "usd",
    dividend: "usd",
    dividend_ttm: "usd",
    payout_ratio: "percent",
    employees: "count",

    pe: "ratio",
    forward_pe: "ratio",
    peg: "ratio",
    ps: "ratio",
    pb: "ratio",
    pc: "ratio",
    pfcf: "ratio",
    ev_ebitda: "ratio",
    ev_sales: "ratio",
    quick_ratio: "ratio",
    current_ratio: "ratio",
    debt_to_equity: "ratio",
    lt_debt_to_equity: "ratio",
    optionable: "bool",

    eps: "usd",
    recommendation: "ratio",
    target_price: "usd",

    insider_own: "percent",
    inst_own: "percent",
    roa: "percent",
    roe: "percent",

    shares_outstanding: "count",
    shares_float: "count",
    short_float: "percent",
    short_ratio: "ratio",
    high_52w: "usd",
    low_52w: "usd",
    beta: "ratio",
    rsi: "ratio",
    average_volume: "count",
    volume: "count",

    perf_1w: "percent",
    change: "percent",
    prev_close: "usd",
    price: "usd",
  };

  return {
    symbol,
    as_of: "2026-11-02T14:30:00Z",
    values,
    units,
    missing: Object.keys(values).filter((key) => values[key] === null),
  };
}

/**
 * `GET /engine/technicals/{symbol}`: one daily-close document.
 *
 * The values are percent **points** for momentum and the two calendar returns,
 * a 0–100 index for RSI, and quote-currency prices for the averages — the
 * endpoint's own units map is the contract. Every requested window is present;
 * a window the series cannot fill would be `null`.
 */
export function technicalsPayload(symbol = "AAPL"): Record<string, unknown> {
  const values: Record<string, unknown> = {
    sma_13: 341.22,
    sma_21: 337.84,
    sma_50: 330.11,
    sma_100: 318.47,
    sma_200: 295.6,
    ema_13: 342.05,
    ema_21: 338.9,
    ema_50: 331.72,
    ema_100: 320.35,
    ema_200: 298.12,
    rsi_14: 58.42,
    momentum_10: 4.61,
    perf_wtd: 2.17,
    perf_mtd: 5.83,
  };

  const units: Record<string, string> = {};
  for (const key of Object.keys(values)) {
    units[key] = key.startsWith("sma_") || key.startsWith("ema_")
      ? "price"
      : key.startsWith("rsi_")
        ? "index"
        : "percent";
  }

  return {
    symbol,
    as_of: "2026-10-31",
    series_length: 252,
    values,
    units,
    missing: [],
  };
}

/**
 * `GET /engine/risk/{symbol}`: the benchmark-relative group.
 *
 * Percent **points** for alpha, volatility and max drawdown; plain ratios for
 * beta, Sharpe and R². The endpoint may omit `units`; the client carries the
 * fallback map (see `technicals.ts`).
 */
export function riskPayload(symbol = "AAPL"): Record<string, unknown> {
  return {
    symbol,
    as_of: "2026-10-31",
    window: "1y",
    values: {
      alpha: 4.82,
      beta: 1.08,
      correlation: 0.86,
      r_squared: 0.74,
      volatility: 21.43,
      sharpe: 1.42,
      max_drawdown: -18.35,
    },
    units: {
      alpha: "percent",
      beta: "ratio",
      correlation: "ratio",
      r_squared: "ratio",
      volatility: "percent",
      sharpe: "ratio",
      max_drawdown: "percent",
    },
  };
}

export function securityPayload(symbol = "AAPL"): Record<string, unknown> {
  return {
    symbol,
    name: "Apple Inc.",
    cik: "0000320193",
    exchange: "Nasdaq",
    mic: "XNAS",
    asset_class: "stock",
    currency: "USD",
    country: "United States",
    sector: "Technology",
    industry: "Electronic Computers",
    active: true,
  };
}

/** The profile endpoint, for the deployments that serve it. */
export function profilePayload(symbol = "AAPL"): Record<string, unknown> {
  return {
    symbol,
    company: "Apple Inc.",
    sector: "Technology",
    industry: "Electronic Computers",
    index_membership: "NDX, S&P 500",
    price: 343.36,
    change: 1.24,
    volume: 54_300_000,
    market_cap: 5_180_000_000_000,
    prev_close: 339.15,
    as_of: "2026-10-31T09:22:50Z",
  };
}

export function peersPayload(symbol = "AAPL", count = 12): Record<string, unknown> {
  return {
    symbol,
    industry: "Electronic Computers",
    as_of: "2026-10-31",
    count,
    peers: Array.from({ length: count }, (_, index) => ({
      symbol: `PEER${index}`,
      name: `Peer Company ${index}`,
      exchange: index % 2 === 0 ? "Nasdaq" : "NYSE",
      mic: index % 2 === 0 ? "XNAS" : "XNYS",
      industry: "Electronic Computers",
      country: "United States",
    })),
  };
}

/** The business description is long on purpose: the clamp and the expander. */
export function companyPayload(symbol = "AAPL"): Record<string, unknown> {
  return {
    symbol,
    name: "Apple Inc.",
    description:
      "Apple Inc. designs, manufactures and markets smartphones, personal computers, " +
      "tablets, wearables and accessories, and sells a variety of related services. " +
      "The company's product line includes the iPhone, Mac, iPad, Apple Watch, AirPods " +
      "and Vision Pro, while its services segment spans the App Store, iCloud, Apple Music, " +
      "Apple Pay and advertising. Apple sells through its retail stores, its online store, " +
      "direct sales and third-party resellers in more than 150 countries. The company also " +
      "designs its own silicon and operating systems, which it positions as a differentiator " +
      "in performance, privacy and integration across devices.",
    officers: [
      { name: "Mr. Timothy D. Cook", title: "Chief Executive Officer" },
      { name: "Mr. Kevan Parekh", title: "Chief Financial Officer" },
      { name: "Ms. Katherine L. Adams", title: "General Counsel" },
      { name: "Mr. John Ternus", title: "Senior Vice President, Hardware Engineering" },
      { name: "Ms. Deirdre O'Brien", title: "Senior Vice President, Retail" },
    ],
  };
}

export function insiderItems(count = 12): unknown[] {
  return Array.from({ length: count }, (_, index) => ({
    accession: `0001140361-26-${String(41_000 + index)}`,
    filed: "2026-10-29",
    period_of_report: "2026-10-27",
    owner_name: index === 0 ? "Newstead Jennifer" : `Insider ${index}`,
    role: "Officer: SVP",
    security: "Common Stock",
    transaction_date: "2026-10-27",
    code: index % 2 === 0 ? "S" : "M",
    shares: 1000 + index,
    price: index % 3 === 0 ? null : 343.36,
    acquired_disposed: "D",
    shares_after: 32396,
    url: index === 0 ? "https://www.sec.gov/Archives/edgar/data/320193/form4.xml" : null,
  }));
}

export function newsItems(count = 12): unknown[] {
  return Array.from({ length: count }, (_, index) => ({
    headline: `Headline number ${index} about Apple`,
    url: `https://example.com/news/${index}`,
    source: "Wire",
    published_at: new Date(Date.now() - index * 3_600_000).toISOString(),
  }));
}

export function statementsPayload(symbol = "AAPL"): Record<string, unknown> {
  return {
    symbol,
    as_of: "2025-09-27",
    periods: [
      { period_end: "2025-09-27", fiscal_year: 2025, form: "10-K" },
      { period_end: "2024-09-28", fiscal_year: 2024, form: "10-K" },
    ],
    statements: {
      income: {
        revenue: [
          { value: 416_161_000_000, unit: "USD", provenance: { status: "ok", as_of: "2025-09-27" } },
          { value: 391_035_000_000, unit: "USD", provenance: { status: "ok", as_of: "2024-09-28" } },
        ],
        gross_profit: [
          { value: 195_201_000_000, unit: "USD", provenance: { status: "ok", as_of: "2025-09-27" } },
          { value: null, unit: "USD", provenance: { status: "not_reported", as_of: "2024-09-28" } },
        ],
        eps_diluted: [
          { value: 7.49, unit: "unknown", provenance: { status: "ok", as_of: "2025-09-27" } },
          { value: 6.75, unit: "unknown", provenance: { status: "ok", as_of: "2024-09-28" } },
        ],
      },
      balance: {
        assets: [
          { value: 359_241_000_000, unit: "USD", provenance: { status: "ok", as_of: "2025-09-27" } },
          { value: 364_980_000_000, unit: "USD", provenance: { status: "ok", as_of: "2024-09-28" } },
        ],
        equity: [
          { value: 56_950_000_000, unit: "USD", provenance: { status: "ok", as_of: "2025-09-27" } },
          { value: 56_950_000_000, unit: "USD", provenance: { status: "ok", as_of: "2024-09-28" } },
        ],
      },
      cash_flow: {
        operating_cash_flow: [
          { value: 118_254_000_000, unit: "USD", provenance: { status: "ok", as_of: "2025-09-27" } },
          { value: 118_254_000_000, unit: "USD", provenance: { status: "ok", as_of: "2024-09-28" } },
        ],
        capex: [
          { value: -12_715_000_000, unit: "USD", provenance: { status: "ok", as_of: "2025-09-27" } },
          { value: -9_447_000_000, unit: "USD", provenance: { status: "ok", as_of: "2024-09-28" } },
        ],
        fcf: [
          { value: 105_539_000_000, unit: "USD", provenance: { status: "derived", as_of: "2025-09-27" } },
          { value: 108_807_000_000, unit: "USD", provenance: { status: "derived", as_of: "2024-09-28" } },
        ],
      },
    },
    provider_errors: [],
  };
}

/**
 * The ticker table's own shape: ten annual + eight quarterly columns, values
 * keyed by the period label, with the collector's `yoy_pct` and `ttm`.
 */
export function statementTablePayload(symbol = "AAPL"): Record<string, unknown> {
  return {
    symbol,
    source: "sec-xbrl",
    as_of: "2025-09-27",
    periods: [
      { period: "2025", kind: "annual", end: "2025-09-27", period_end: "2025-09-27", fiscal_year: 2025, form: "10-K" },
      { period: "2024", kind: "annual", end: "2024-09-28", period_end: "2024-09-28", fiscal_year: 2024, form: "10-K" },
      { period: "2025Q3", kind: "quarterly", end: "2025-06-28", period_end: "2025-06-28", fiscal_year: 2025, form: "10-Q" },
      { period: "2024Q3", kind: "quarterly", end: "2024-06-29", period_end: "2024-06-29", fiscal_year: 2024, form: "10-Q" },
    ],
    statements: {
      income: [
        {
          key: "revenue",
          label: "Revenue",
          unit: "USD",
          values: {
            "2025": 416_161_000_000,
            "2024": 391_035_000_000,
            "2025Q3": 102_466_000_000,
            "2024Q3": 85_777_000_000,
          },
          yoy_pct: 6.43,
          ttm: 420_000_000_000,
        },
        {
          key: "eps_diluted",
          label: "EPS (diluted)",
          unit: "USD/shares",
          values: { "2025": 7.49, "2024": 6.75, "2025Q3": 1.57, "2024Q3": 1.4 },
          yoy_pct: 10.96,
          ttm: 8.1,
        },
      ],
      balance: [
        {
          key: "assets",
          label: "Total assets",
          unit: "USD",
          values: {
            "2025": 359_241_000_000,
            "2024": 364_980_000_000,
            "2025Q3": 331_495_000_000,
            "2024Q3": 336_000_000_000,
          },
          yoy_pct: -1.57,
          ttm: null,
        },
      ],
      cash_flow: [
        {
          key: "free_cash_flow",
          label: "Free cash flow",
          unit: "USD",
          values: {
            "2025": 105_539_000_000,
            "2024": null,
            "2025Q3": 27_000_000_000,
            "2024Q3": null,
          },
          yoy_pct: null,
          ttm: 108_000_000_000,
        },
      ],
    },
    provider_errors: [],
  };
}

export function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

export function notFound(message = "security is not in the loaded identity master"): Response {
  return json({ error: "security_not_found", errorMessage: message }, 404);
}

export type RouteHandler = (url: string, init?: RequestInit) => Response | Promise<Response>;

export interface TickerRoutes {
  profile?: RouteHandler;
  security?: RouteHandler;
  fundamentals?: RouteHandler;
  snapshot?: RouteHandler;
  technicals?: RouteHandler;
  risk?: RouteHandler;
  peers?: RouteHandler;
  insider?: RouteHandler;
  news?: RouteHandler;
  statements?: RouteHandler;
  chart?: RouteHandler;
  screens?: RouteHandler;
  select?: RouteHandler;
  company?: RouteHandler;
  /** `/symbols/search` — the compare dialog's own ticker lookup. */
  search?: RouteHandler;
}

/**
 * A fetch stub routed by URL, with the two defaults that make a page test
 * honest: the profile endpoint is absent (404 — the state the backend is in
 * today) and the chart answers 404 (jsdom cannot draw it).
 */
export function stubTickerApi(routes: TickerRoutes = {}): ReturnType<typeof vi.fn> {
  const mock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const handler = url.startsWith("/symbols/search")
      ? routes.search
      : url.includes("/engine/profile/")
      ? routes.profile
      : url.includes("/engine/company/")
        ? routes.company
        : url.includes("/engine/security/")
        ? routes.security
        : url.includes("/engine/fundamentals/")
          ? routes.fundamentals
          : url.includes("/engine/snapshot/")
            ? routes.snapshot
            : url.includes("/engine/technicals/")
              ? routes.technicals
              : url.includes("/engine/risk/")
                ? routes.risk
                : url.includes("/engine/peers/")
                  ? routes.peers
                  : url.includes("/engine/insider/")
                    ? routes.insider
                    : url.includes("/engine/statements/")
                      ? routes.statements
                      : url.startsWith("/news/")
                        ? routes.news
                        : url.includes("/market/chart/")
                          ? routes.chart
                          : url.includes("/screens/select")
                            ? routes.select
                            : url.includes("/screens")
                              ? routes.screens
                              : undefined;
    if (handler !== undefined) return Promise.resolve(handler(url, init));
    if (url.startsWith("/symbols/search")) {
      return Promise.resolve(json([]));
    }
    if (url.includes("/engine/profile/") || url.includes("/market/chart/")) {
      return Promise.resolve(notFound());
    }
    if (url.includes("/engine/company/")) {
      return Promise.resolve(notFound("no company profile"));
    }
    if (url.includes("/engine/fundamentals/")) {
      return Promise.resolve(json(fundamentalsPayload()));
    }
    if (url.includes("/engine/snapshot/")) {
      return Promise.resolve(notFound("snapshot_not_found: the snapshot endpoint is not deployed"));
    }
    if (url.includes("/engine/technicals/")) {
      return Promise.resolve(json(technicalsPayload()));
    }
    if (url.includes("/engine/risk/")) {
      return Promise.resolve(json(riskPayload()));
    }
    if (url.includes("/engine/security/")) {
      return Promise.resolve(json(securityPayload()));
    }
    if (url.includes("/engine/peers/")) {
      return Promise.resolve(json(peersPayload()));
    }
    if (url.includes("/engine/insider/")) {
      return Promise.resolve(
        json({ symbol: "AAPL", as_of: "2026-10-29", count: 12, items: insiderItems(), provider_errors: [] }),
      );
    }
    if (url.includes("/engine/statements/")) {
      return Promise.resolve(json(statementsPayload()));
    }
    if (url.startsWith("/news/")) {
      return Promise.resolve(
        json({ symbol: "AAPL", as_of: "2026-10-31", count: 12, items: newsItems() }),
      );
    }
    if (url.includes("/screens/select")) {
      const body = JSON.parse(String(init?.body ?? "{}")) as { symbols?: string[] };
      return Promise.resolve(json(rowPayload(body.symbols?.[0] ?? "AAPL")));
    }
    if (url.startsWith("/screens")) {
      return Promise.resolve(json({ screen_id: "scr_us_market_cap_test" }));
    }
    return Promise.resolve(notFound(`no route for ${url}`));
  });
  vi.stubGlobal("fetch", mock);
  return mock;
}

export function optionsPayload(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  const baseStrikes: Record<string, unknown>[] = [
    { strike: 275, type: "call", premium: 62.35, delta: 0.9877, gamma: 0.0010, theta: -0.1158, vega: 0.0080, rho: 0.05, volume: 1000, open_interest: 5000, implied_volatility: 1.361, expiry: "2026-11-04" },
    { strike: 275, type: "put", premium: 2.15, delta: -0.0122, gamma: 0.0008, theta: -0.0021, vega: 0.0065, rho: -0.03, volume: 800, open_interest: 4000, implied_volatility: 0.85, expiry: "2026-11-04" },
    { strike: 290, type: "call", premium: 15.30, delta: 0.65, gamma: 0.0025, theta: -0.08, vega: 0.012, rho: 0.04, volume: 500, open_interest: 3000, implied_volatility: 1.2, expiry: "2026-11-04" },
    { strike: 290, type: "put", premium: 12.40, delta: -0.35, gamma: 0.0030, theta: -0.05, vega: 0.015, rho: -0.02, volume: 600, open_interest: 3500, implied_volatility: 1.1, expiry: "2026-11-04" },
  ];

  let strikes = baseStrikes;
  let expiryDates: string[] = ["2026-11-04", "2026-11-06"];

  if (overrides.shuffled) {
    strikes = [...baseStrikes].reverse();
  }
  if (overrides.nullGamma) {
    strikes = baseStrikes.map((s, i) => i === 2 ? { ...s, gamma: null } : s);
  }
  if (overrides.thirdExpiry) {
    expiryDates = ["2026-11-04", "2026-11-06", "2026-11-10"];
    strikes = [
      ...baseStrikes,
      { strike: 285, type: "call", premium: 25.50, delta: 0.80, gamma: 0.0020, theta: -0.10, vega: 0.015, rho: 0.04, volume: 700, open_interest: 4500, implied_volatility: 1.25, expiry: "2026-11-06" },
      { strike: 285, type: "put", premium: 5.20, delta: -0.20, gamma: 0.0022, theta: -0.03, vega: 0.018, rho: -0.02, volume: 900, open_interest: 4200, implied_volatility: 1.15, expiry: "2026-11-06" },
    ];
  }

  return {
    symbol: "AAPL",
    delayed_minutes: 15,
    as_of: "2026-11-02T15:01:17Z",
    expiry_dates: expiryDates,
    selected_expiry: "2026-11-04",
    strikes,
  };
}
