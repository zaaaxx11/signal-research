/**
 * The shell at the top of the tree: which URL renders which page, under the
 * same nav.
 *
 * These are deep links, not clicks — each test writes the address bar and
 * mounts `<App />`, which is exactly what a browser does when someone opens a
 * shared URL. The server answers every `/app/*` path with `index.html`; this
 * file proves the app then picks the right surface out of that one document.
 */

import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "@/app";
import { LIVE_BREADTH } from "@/breadth/__fixtures__/breadth";
import { filterSchemaWire } from "@/filters/__fixtures__/schema";
import { resetFilterSchemaCache } from "@/filters/schema";
import { homeDocument } from "@/home/__fixtures__/home";
import { AVAILABLE_GROUPS } from "@/home/signals";

// The detail panel is a separate chunk with its own reads; the route test only
// needs to prove the route mounts it, so the chunk is replaced by a marker.
vi.mock("@/detail", () => ({
  default: ({ symbol }: { symbol: string | null }) => (
    <div data-testid="detail-panel" data-symbol={symbol} role="complementary" aria-label={`Details for ${symbol}`}>
      Details for {symbol}
    </div>
  ),
}));

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function page(count = 20) {
  return {
    as_of: "2026-10-31T01:28:01Z",
    limit: 20,
    total: 5_600,
    universe_total: 5_600,
    returned: count,
    columns: [],
    rows: Array.from({ length: count }, (_, index) => ({ symbol: `S${index}`, price: index })),
    budget: { truncated: false, dropped_fields: [] },
  };
}

function signalsDocument() {
  return {
    groups: Object.fromEntries(
      AVAILABLE_GROUPS.map((group) => [
        group.key,
        {
          rows: Array.from({ length: 20 }, (_, index) => ({
            symbol: `${group.key.slice(0, 2).toUpperCase()}${index}`,
            price: 10 + index,
            change_pct: 1,
            volume: 1000,
          })),
        },
      ]),
    ),
  };
}

interface Harness {
  fetchMock: ReturnType<typeof vi.fn>;
  selects: Array<Record<string, unknown>>;
}

function stubApi(): Harness {
  const selects: Array<Record<string, unknown>> = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url === "/screens/select/schema") return json(filterSchemaWire());
    if (url.startsWith("/market/breadth")) return json(LIVE_BREADTH);
    if (url.startsWith("/market/exchanges")) {
      return json({ venues: [{ code: "XNAS", count: 10 }], total: 10 });
    }
    if (url.startsWith("/market/home")) return json(homeDocument());
    if (url.startsWith("/market/signals")) return json(signalsDocument());
    if (url.startsWith("/engine/profile/")) {
      return json({ error: "security_not_found", errorMessage: "no profile yet" }, 404);
    }
    if (url.startsWith("/engine/company/")) {
      return json({ error: "company_not_found", errorMessage: "no company profile yet" }, 404);
    }
    if (url.startsWith("/engine/security/")) {
      return json({
        symbol: "AAPL",
        name: "Apple Inc.",
        sector: "Technology",
        industry: "Electronic Computers",
      });
    }
    if (url.startsWith("/engine/fundamentals/")) {
      return json({
        symbol: "AAPL",
        as_of: "2026-10-31",
        metrics: {
          company: { value: "Apple Inc.", unit: "text" },
          industry: { value: "Electronic Computers", unit: "text" },
        },
      });
    }
    if (url.startsWith("/engine/peers/")) {
      return json({ symbol: "AAPL", industry: "Electronic Computers", count: 0, peers: [] });
    }
    if (url.startsWith("/engine/insider/")) {
      return json({ symbol: "AAPL", as_of: "2026-10-31", count: 0, items: [] });
    }
    if (url.startsWith("/news/")) {
      return json({ symbol: "AAPL", as_of: "2026-10-31", count: 0, items: [] });
    }
    if (url.startsWith("/market/chart/")) {
      return json({ error: "chart_not_found", errorMessage: "no bars" }, 404);
    }
    if (url.startsWith("/screens?source=")) return json({ screen_id: "scr_app_surface" });
    if (url === "/screens/select" && init?.method === "POST") {
      selects.push(JSON.parse(String(init.body)) as Record<string, unknown>);
      return json(page());
    }
    if (url === "/health") return json({ status: "ok", version: "0.1.0", checks: {} });
    throw new Error(`unexpected fetch ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return { fetchMock, selects };
}

const urls = (api: Harness) => api.fetchMock.mock.calls.map((call) => String(call[0]));

beforeEach(() => {
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
    width: 1280,
    height: 800,
    top: 0,
    left: 0,
    right: 1280,
    bottom: 800,
    x: 0,
    y: 0,
    toJSON: () => ({}),
  } as DOMRect);
  resetFilterSchemaCache();
  window.history.replaceState(null, "", "/app/");
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("the front door", () => {
  it("renders the dashboard at / from one request and asks for nothing else", async () => {
    const api = stubApi();
    render(<App />);

    await waitFor(() =>
      expect(screen.getByTestId("home-grid").getAttribute("data-status")).toBe("ready"),
    );
    expect(screen.getByTestId("home-movers")).toBeTruthy();
    expect(screen.getByTestId("home-map")).toBeTruthy();
    expect(screen.getByTestId("home-footer").textContent).toBe("ztockz");
    expect(urls(api).filter((url) => url === "/market/home?region=us")).toHaveLength(1);
    expect(urls(api).some((url) => url === "/screens/select")).toBe(false);
    expect(urls(api)).not.toContain("/health");
  });
});

describe("the screener routes", () => {
  it("renders the grid at /screener and asks for 20 rows of stocks", async () => {
    const api = stubApi();
    window.history.replaceState(null, "", "/app/screener");
    render(<App />);

    await waitFor(() => expect(screen.getByTestId("range").textContent).toBe("1/5,600 Total"));
    expect(api.selects).toHaveLength(1);
    const criteria = (api.selects[0] as { criteria: Record<string, unknown> }).criteria;
    expect(criteria["limit"]).toBe(20);
    expect(criteria["offset"]).toBe(0);
    expect((api.selects[0] as { asset_class: string }).asset_class).toBe("stock");
  });

  it("carries asset_class=etf on /screener/etf while keeping the same panel", async () => {
    const api = stubApi();
    window.history.replaceState(null, "", "/app/screener/etf");
    render(<App />);

    await waitFor(() => expect(screen.getByTestId("range").textContent).toBe("1/5,600 Total"));
    expect(api.selects).toHaveLength(1);
    expect((api.selects[0] as { asset_class: string }).asset_class).toBe("etf");
    expect(
      screen.getAllByRole("link").find((link) => link.getAttribute("aria-current") === "page")
        ?.textContent,
    ).toBe("ETF Screener");
  });
});

describe("a ticker page", () => {
  it("mounts the ticker page at /stock/{symbol} and asks for one symbol row", async () => {
    const api = stubApi();
    window.history.replaceState(null, "", "/app/stock/AAPL");
    render(<App />);

    const page = await screen.findByTestId("ticker-page", undefined, { timeout: 5_000 });
    expect(page.getAttribute("data-symbol")).toBe("AAPL");
    expect(screen.getByTestId("stock-page").getAttribute("data-symbol")).toBe("AAPL");
    // One symbols-mode selection for the page's own row — not a 20-row grid scan.
    await waitFor(() => expect(api.selects).toHaveLength(1));
    expect((api.selects[0] as { mode: string }).mode).toBe("symbols");
  });
});

describe("an unknown path", () => {
  it("renders the not-found page, inside the nav", async () => {
    stubApi();
    window.history.replaceState(null, "", "/app/no-such-page");
    render(<App />);

    const page = await screen.findByTestId("not-found");
    expect(page.textContent).toContain("/no-such-page");
    expect(screen.getByRole("navigation", { name: "Primary" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Back to signals" })).toBeTruthy();
  });
});

describe("no internal status chrome", () => {
  it("never prints a version, a health check or an engine state", async () => {
    const api = stubApi();
    window.history.replaceState(null, "", "/app/screener");
    render(<App />);
    await waitFor(() => expect(screen.getByTestId("range").textContent).toBe("1/5,600 Total"));

    const text = document.body.textContent ?? "";
    expect(text).not.toMatch(/v0\.1\.0/);
    expect(text).not.toMatch(/depth store|snapshot ok|screen db ok/);
    expect(text).not.toMatch(/as of \d/i);
    expect(text).not.toMatch(/waiting for the engine/);
    expect(text).not.toMatch(/same-origin api only/);
    expect(text).not.toMatch(/19:\d\d:\d\dZ/);
    // And the poll is gone, not merely hidden.
    expect(urls(api)).not.toContain("/health");
  });
});
