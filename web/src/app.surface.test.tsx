/**
 * The shell at the top of the tree: which URL renders which page, under the
 * same nav.
 *
 * These are deep links, not clicks — each test writes the address bar and
 * mounts `<App />`, which is exactly what a browser does when someone opens a
 * shared URL. The server answers every `/app/*` path with `index.html`; this
 * file proves the app then picks the right surface out of that one document.
 */

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "@/app";
import { LIVE_BREADTH } from "@/breadth/__fixtures__/breadth";
import { filterSchemaWire } from "@/filters/__fixtures__/schema";
import { resetFilterSchemaCache } from "@/filters/schema";
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
    if (url.startsWith("/market/signals")) return json(signalsDocument());
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
  it("renders Home's signal cards at / and asks for nothing else", async () => {
    const api = stubApi();
    render(<App />);

    await waitFor(() =>
      expect(screen.getByTestId("signal-card-gainers").getAttribute("aria-busy")).toBeNull(),
    );
    expect(document.querySelectorAll('[data-testid^="signal-card-"]')).toHaveLength(13);
    expect(urls(api).filter((url) => url === "/market/signals?region=us&per=20")).toHaveLength(1);
    expect(urls(api).some((url) => url === "/screens/select")).toBe(false);
    expect(urls(api)).not.toContain("/health");
  });
});

describe("the screener routes", () => {
  it("renders the grid at /screener and asks for 20 rows of stocks", async () => {
    const api = stubApi();
    window.history.replaceState(null, "", "/app/screener");
    render(<App />);

    await waitFor(() => expect(screen.getByTestId("range").textContent).toBe("1–20 of 5,600"));
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

    await waitFor(() => expect(screen.getByTestId("range").textContent).toBe("1–20 of 5,600"));
    expect(api.selects).toHaveLength(1);
    expect((api.selects[0] as { asset_class: string }).asset_class).toBe("etf");
    expect(
      screen.getAllByRole("link").find((link) => link.getAttribute("aria-current") === "page")
        ?.textContent,
    ).toBe("ETF Screener");
  });
});

describe("a ticker page", () => {
  it("renders the detail panel full-page at /stock/{symbol}", async () => {
    const api = stubApi();
    window.history.replaceState(null, "", "/app/stock/AAPL");
    render(<App />);

    const panel = await screen.findByTestId("detail-panel", undefined, { timeout: 5_000 });
    expect(panel.getAttribute("data-symbol")).toBe("AAPL");
    expect(screen.getByTestId("stock-page").getAttribute("data-symbol")).toBe("AAPL");
    // No grid request: the page is the panel.
    expect(api.selects).toHaveLength(0);
  });
});

describe("an unbuilt section", () => {
  it("renders /soon/{slug} inside the same shell, naming the section", async () => {
    stubApi();
    window.history.replaceState(null, "", "/app/soon/news");
    render(<App />);

    const page = await screen.findByTestId("soon-page");
    expect(page.getAttribute("data-slug")).toBe("news");
    expect(within(page).getByRole("heading", { level: 1 }).textContent).toBe("News");
    expect(page.textContent).toContain("not built yet");
    // The nav is still there: this is a page of the product, not a dead end.
    expect(screen.getByRole("navigation", { name: "Primary" })).toBeTruthy();
  });

  it("routes a nav click to the soon page without a reload", async () => {
    stubApi();
    const user = userEvent.setup();
    render(<App />);

    await screen.findByTestId("signal-card-gainers");
    const nav = screen.getByRole("navigation", { name: "Primary" });
    await user.click(within(nav).getByRole("link", { name: "News" }));

    expect(window.location.pathname).toBe("/app/soon/news");
    const page = await screen.findByTestId("soon-page");
    expect(page.getAttribute("data-slug")).toBe("news");
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
    await waitFor(() => expect(screen.getByTestId("range").textContent).toBe("1–20 of 5,600"));

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
