import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "@/app";
import { filterSchemaWire } from "@/filters/__fixtures__/schema";
import { resetFilterSchemaCache } from "@/filters/schema";

import { filterDocumentWire } from "@/screener/__fixtures__/filters";
import { resetFiltersDocumentCache } from "@/screener/filtersDocument";

/**
 * The exchange predicate, after the toolbar's own menu was removed.
 *
 * The old panel grew a bespoke multi-select over `GET /market/exchanges`; that
 * control is gone. The venue is now one more server-driven control in the
 * header's filter panel, and the predicate it writes is the same `exch` filter
 * the API has always compiled. What is proved here:
 *
 *   * the removed control stays removed (`exchange-trigger`, `control-chip-bar`);
 *   * choosing a venue in the header writes one `exch in` predicate, sends it,
 *     and shows it as a chip;
 *   * the URL carries it, and a reload sends the same predicate again;
 *   * a venue the current document does not list is still sent, unchanged.
 */

interface WireBody {
  criteria: { offset: number; filters: unknown[]; sort?: string };
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function page(count: number, total = 5_600) {
  return {
    as_of: "2026-11-01T10:00:00Z",
    limit: 20,
    total,
    universe_total: 5_600,
    returned: count,
    columns: ["price"],
    rows: Array.from({ length: count }, (_, index) => ({
      symbol: `S${String(index).padStart(3, "0")}`,
      price: 100 + index,
    })),
    budget: { truncated: false, dropped_fields: [] },
  };
}

function stubApi() {
  const selects: WireBody[] = [];
  const urls: string[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    urls.push(url);
    if (url === "/screens/select/schema") return json(filterSchemaWire());
    if (url === "/screens/filters") return json(filterDocumentWire());
    if (url === "/screens/select" && init?.method === "POST") {
      const body = JSON.parse(String(init.body)) as WireBody;
      selects.push(body);
      return json(page(20));
    }
    return json({ errorMessage: `no route for ${url}` }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return { selects, urls, fetchMock };
}

beforeEach(() => {
  resetFilterSchemaCache();
  resetFiltersDocumentCache();
  window.history.replaceState(null, "", "/app/screener");
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

/** Open the header's Filter disclosure. */
async function openFilterPanel(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByTestId("filter-button"));
  return screen.getByTestId("filter-panel");
}

describe("the exchange control that was removed", () => {
  it("does not mount a toolbar menu, a source badge or a second chip bar", async () => {
    stubApi();
    render(<App />);
    await waitFor(() => expect(screen.getByTestId("range")).toBeTruthy());

    expect(screen.queryByTestId("exchange-trigger")).toBeNull();
    expect(screen.queryByTestId("exchange-chip")).toBeNull();
    expect(screen.queryByTestId("control-chip-bar")).toBeNull();
  });
});

describe("choosing a venue in the header", () => {
  it("sends one exch in predicate, chips it, and keeps it in the URL", async () => {
    const api = stubApi();
    const user = userEvent.setup();
    const first = render(<App />);

    await waitFor(() => expect(api.selects.length).toBe(1));
    expect(api.selects[0]?.criteria.filters).toEqual([]);

    const panel = await openFilterPanel(user);
    const select = await within(panel).findByTestId("control-select-exch");
    await user.selectOptions(select, "exch\u0000nyse");

    await waitFor(() => expect(api.selects.length).toBe(2));
    expect(api.selects[1]?.criteria.offset).toBe(0);
    expect(api.selects[1]?.criteria.filters).toEqual([
      { key: "exch", op: "in", value: ["nyse"] },
    ]);

    // The chip names the control and the value the reader chose.
    const chips = screen.getByTestId("chip-bar");
    expect(chips.textContent).toContain("Exchange");
    expect(chips.textContent).toContain("NYSE");

    const params = new URLSearchParams(window.location.search);
    expect(params.getAll("f")).toEqual(['exch:in:["nyse"]']);
    expect(params.get("page")).toBe("1");

    // A reload is a fresh mount against the same address bar.
    first.unmount();
    resetFilterSchemaCache();
    resetFiltersDocumentCache();
    api.selects.length = 0;
    render(<App />);

    await waitFor(() => expect(api.selects.length).toBe(1));
    expect(api.selects[0]?.criteria.filters).toEqual([
      { key: "exch", op: "in", value: ["nyse"] },
    ]);
  });

  it("still sends a venue the served document does not list", async () => {
    const api = stubApi();
    window.history.replaceState(null, "", "/app/screener?f=exch%3Aeq%3AXWAR");

    render(<App />);

    await waitFor(() => expect(api.selects.length).toBe(1));
    expect(api.selects[0]?.criteria.filters).toEqual([
      { key: "exch", op: "eq", value: "XWAR" },
    ]);
    const chips = screen.getByTestId("chip-bar");
    expect(chips.textContent).toContain("XWAR");
    expect(screen.queryByText(/Ignored 1 filter/)).toBeNull();
  });

  it("clears the predicate when the chip is removed", async () => {
    const api = stubApi();
    const user = userEvent.setup();
    window.history.replaceState(null, "", "/app/screener?f=exch%3Aeq%3AXNAS");

    render(<App />);
    await waitFor(() => expect(api.selects.length).toBe(1));

    await user.click(
      within(screen.getByTestId("chip-bar")).getByRole("button", {
        name: /Remove filter: Exchange/,
      }),
    );

    await waitFor(() => expect(api.selects.length).toBe(2));
    expect(api.selects[1]?.criteria.filters).toEqual([]);
    expect(screen.queryByTestId("chip-bar")).toBeNull();
    // The address bar is canonical again, too.
    expect(new URLSearchParams(window.location.search).getAll("f")).toEqual([]);
  });
});

describe("the exchange key is still one filter", () => {
  it("keeps another predicate when the exchange chip is removed", async () => {
    const api = stubApi();
    const user = userEvent.setup();
    window.history.replaceState(
      null,
      "",
      "/app/screener?f=cap%3Agte%3A2000000000&f=exch%3Aeq%3AXNAS",
    );

    render(<App />);
    await waitFor(() => expect(api.selects.length).toBe(1));
    expect(api.selects[0]?.criteria.filters).toEqual([
      { key: "cap", op: "gte", value: 2_000_000_000 },
      { key: "exch", op: "eq", value: "XNAS" },
    ]);

    await user.click(
      within(screen.getByTestId("chip-bar")).getByRole("button", {
        name: /Remove filter: Exchange/,
      }),
    );

    await waitFor(() => expect(api.selects.length).toBe(2));
    expect(api.selects[1]?.criteria.filters).toEqual([
      { key: "cap", op: "gte", value: 2_000_000_000 },
    ]);
    expect(within(screen.getByTestId("chip-bar")).getByText(/Market Cap/)).toBeTruthy();
  });

  it("does not read the removed market route", async () => {
    const api = stubApi();
    render(<App />);
    await waitFor(() => expect(screen.getByTestId("range")).toBeTruthy());
    fireEvent.keyDown(window, { key: "k", metaKey: true });
    expect(api.urls.filter((url) => url.startsWith("/market/exchanges"))).toHaveLength(0);
  });
});
