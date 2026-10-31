/**
 * Live-shaped fixtures for the detail tests.
 *
 * Values are the ones `GET /engine/fundamentals/AAPL` actually answered on
 * 2026-06-27 (and the insider/news/select payloads captured 2026-10-31), so a
 * formatting test is asserting against a real number, not a convenient one.
 * A fixture may be sparse on purpose: ASML's real answer carries three text
 * fields and no statements, which is the case the catalogue must survive.
 */

import { vi } from "vitest";

import { METRIC_DEFINITIONS } from "../metrics";

const VALUES: Record<string, unknown> = {
  ticker: "AAPL",
  company: "Apple Inc.",
  industry: "Electronic Computers",
  country: "United States",
  exchange: "Nasdaq",

  assets: 383_266_000_000,
  liabilities: 275_746_000_000,
  equity: 107_520_000_000,
  current_assets: 149_818_000_000,
  current_liabilities: 149_326_000_000,
  cash_and_equivalents: 39_544_000_000,
  inventory: 11_092_000_000,
  receivables: 31_398_000_000,
  ppe: 51_431_000_000,
  long_term_debt: 71_340_000_000,
  total_debt: 82_300_000_000,
  retained_earnings: 11_326_000_000,
  shares_outstanding: 14_608_963_000,

  revenue: 466_823_000_000,
  sales: 466_823_000_000,
  cost_of_revenue: 239_700_000_000,
  gross_profit: 227_123_000_000,
  operating_income: 154_859_000_000,
  net_income: 128_930_000_000,
  interest_expense: 3_933_000_000,
  depreciation_amortization: 13_100_000_000,
  operating_cash_flow: 146_724_000_000,
  capex: 10_041_000_000,
  fcf: 136_683_000_000,

  eps: 8.72,
  eps_basic: 8.76,
  eps_diluted: 8.72,
  book_per_share: 7.36,
  cash_per_share: 2.71,

  gross_margin: 48.65291555900202,
  operating_margin: 33.17295848747812,
  net_margin: 27.618604910212223,
  roa: 33.639821951334056,
  roe: 119.91257440476191,
  current_ratio: 1.0032948046555858,
  debt_to_equity: 76.54389880952381,

  dividends_paid: 15_640_000_000,
  buybacks: 82_226_000_000,
};

export function fundamentalsPayload(
  overrides: Record<string, unknown> = {},
): Record<string, unknown> {
  const merged: Record<string, unknown> = { ...VALUES, ...overrides };
  const metrics: Record<string, unknown> = {};

  for (const definition of METRIC_DEFINITIONS) {
    metrics[definition.key] = {
      value: merged[definition.key] ?? null,
      unit: definition.unit,
      provenance: { source: "sec", status: "ok" },
    };
  }
  for (const [key, value] of Object.entries(merged)) {
    if (metrics[key]) continue;
    metrics[key] = { value, unit: "unknown", provenance: { source: "sec", status: "ok" } };
  }

  return {
    symbol: String(merged["ticker"] ?? "AAPL"),
    as_of: "2026-06-27",
    metrics,
  };
}

/** ASML's real shape: identity only, no statements. */
export function sparseFundamentalsPayload(): Record<string, unknown> {
  return {
    symbol: "ASML",
    as_of: "2026-06-27",
    metrics: {
      ticker: { value: "ASML", unit: "text" },
      company: { value: "ASML HOLDING NV", unit: "text" },
      exchange: { value: "Nasdaq", unit: "text" },
    },
  };
}

export function insiderPayload(items?: unknown[]): Record<string, unknown> {
  return {
    symbol: "AAPL",
    as_of: "2026-10-29",
    count: items ? items.length : 2,
    items: items ?? [
      {
        accession: "0001140361-26-041201",
        filed: "2026-10-29",
        period_of_report: "2026-10-27",
        owner_name: "Newstead Jennifer",
        owner_cik: "0001780525",
        role: "Officer: SVP, GC and Government Affairs",
        security: "Common Stock",
        transaction_date: "2026-10-27",
        code: "S",
        shares: 2399,
        price: 343.36,
        acquired_disposed: "D",
        shares_after: 32396,
        url: "https://www.sec.gov/Archives/edgar/data/320193/000114036126041201/form4.xml",
      },
      {
        accession: "0001140361-26-040127",
        filed: "2026-10-19",
        period_of_report: "2026-10-15",
        owner_name: "Borders Ben",
        owner_cik: "0002100523",
        role: "Officer: Principal Accounting Officer",
        security: "Common Stock",
        transaction_date: "2026-10-16",
        code: "M",
        shares: 151,
        price: null,
        acquired_disposed: "A",
        shares_after: 39417,
        url: null,
      },
    ],
    provider_errors: [],
  };
}

export function emptyInsiderPayload(): Record<string, unknown> {
  return { symbol: "ASML", as_of: "2026-10-29", count: 0, items: [], provider_errors: [] };
}

export function newsPayload(items?: unknown[]): Record<string, unknown> {
  const hourAgo = new Date(Date.now() - 3_600_000).toISOString();
  return {
    symbol: "AAPL",
    as_of: new Date().toISOString(),
    count: items ? items.length : 2,
    latest_published_at: hourAgo,
    items: items ?? [
      {
        headline: "Apple Inc. $AAPL Stock Position Lifted by Opal Wealth Advisors LLC",
        url: "https://news.google.com/rss/articles/abc?oc=5",
        source: "MarketBeat",
        published_at: hourAgo,
        source_url: "https://www.marketbeat.com",
      },
      {
        headline: "What Apple Doubters Got Wrong",
        url: "javascript:alert(document.cookie)",
        source: "Yahoo Finance",
        published_at: hourAgo,
      },
    ],
    provider_errors: [],
  };
}

export function emptyNewsPayload(): Record<string, unknown> {
  return { symbol: "AAPL", as_of: new Date().toISOString(), count: 0, items: [], provider_errors: [] };
}

export function screensPayload(screenId = "scr_us_market_cap_test"): Record<string, unknown> {
  return { screen_id: screenId, region: "us", source: "tradingview", as_of: "2026-10-31T09:00:00Z" };
}

export function selectPayload(symbol = "AAPL"): Record<string, unknown> {
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
        shares_outstanding: 14_594_200_000,
        float_shares: 14_576_745_336.8,
        short_ratio: 3.48,
        insider_own: 1.6480000000000001,
        inst_own: 66.337997,
        short_float: 0.9,
        as_of: "2026-10-31T09:22:50Z",
      },
    ],
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

export function failure(message = "the scanner could not answer", status = 500): Response {
  return json({ error: "selection_failed", errorMessage: message }, status);
}

export type RouteHandler = (url: string, init?: RequestInit) => Response | Promise<Response>;

export interface ApiRoutes {
  fundamentals?: RouteHandler;
  insider?: RouteHandler;
  news?: RouteHandler;
  screens?: RouteHandler;
  select?: RouteHandler;
}

/**
 * A fetch stub routed by URL. A resource with no route answers 404, which the
 * panel treats as empty — the honest default for "this test only cares about
 * the fundamentals tab".
 */
export function stubApi(routes: ApiRoutes): ReturnType<typeof vi.fn> {
  const mock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const handler = url.includes("/engine/fundamentals/")
      ? routes.fundamentals
      : url.includes("/engine/insider/")
        ? routes.insider
        : url.includes("/news/")
          ? routes.news
          : url.includes("/screens/select")
            ? routes.select
            : url.includes("/screens")
              ? routes.screens
              : undefined;
    if (!handler) return Promise.resolve(notFound());
    return Promise.resolve(handler(url, init));
  });
  vi.stubGlobal("fetch", mock);
  return mock;
}

export interface Deferred<T> {
  promise: Promise<T>;
  resolve: (value: T) => void;
}

export function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((settle) => {
    resolve = settle;
  });
  return { promise, resolve };
}

/** A tab state without a network: the four states, constructed by hand. */
export function resource<T>(
  status: "loading" | "ready" | "empty" | "error",
  data: T | null = null,
  error: string | null = null,
): import("../useDetail").Resource<T> {
  return { status, data, error, retry: vi.fn() };
}
