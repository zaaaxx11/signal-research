import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { parseRoute } from "@/shell/routes";

import { ComparePage } from "./ComparePage";

/**
 * The page's promises, on the rendered page:
 *
 * * fewer than two companies is an empty state with a way back to the screener,
 *   and it asks the API nothing;
 * * two symbols are two columns, in URL order, from **one** request;
 * * an absent value is an em dash — never a zero, never a blank cell;
 * * removing a company rewrites the URL, and undo puts it back;
 * * the tenth company is the ceiling: no eleventh column, ever;
 * * the table scrolls sideways under 900 px with the metric column pinned.
 *
 * The address bar is written before the render, exactly as a shared link
 * arrives, so the tests exercise the real reading path and not a prop.
 */

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

/** One row as `POST /screens/select` serves it, with the cells this test names. */
function row(symbol: string, cells: Record<string, unknown> = {}) {
  return {
    symbol,
    company: symbol === "AAPL" ? "Apple Inc." : `${symbol} Corp.`,
    price: symbol === "AAPL" ? 344.63 : 410.12,
    change: symbol === "AAPL" ? -1.2266 : 0.84,
    ...cells,
  };
}

function page(rows: Array<Record<string, unknown>>) {
  return {
    as_of: "2026-11-02T10:45:09Z",
    limit: 10,
    total: rows.length,
    universe_total: 5576,
    returned: rows.length,
    columns: [],
    rows,
    budget: { truncated: false, dropped_fields: [] },
  };
}

/** The one selection this page is allowed to make, repeated per test. */
function stub(rows: Array<Record<string, unknown>> = [row("AAPL"), row("MSFT")]) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url === "/screens/select" && init?.method === "POST") return json(page(rows));
    return json({ error: "not_found", errorMessage: `unexpected ${url}` }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function at(symbols: string) {
  window.history.replaceState(null, "", `/app/compare${symbols.length > 0 ? `?symbols=${symbols}` : ""}`);
}

function search(): string {
  return window.location.search;
}

afterEach(() => {
  window.history.replaceState(null, "", "/");
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("ComparePage", () => {
  it("is a route this build answers", () => {
    expect(parseRoute("/app/compare")).toEqual({ kind: "compare" });
    expect(parseRoute("/compare")).toEqual({ kind: "compare" });
  });

  it("asks for companies when there are fewer than two, and reads nothing", () => {
    const fetchMock = stub();
    at("");
    render(<ComparePage />);

    const empty = screen.getByTestId("compare-empty");
    expect(empty.textContent).toContain("screener");
    const link = within(empty).getByRole("link", { name: /screener/i });
    expect(link.getAttribute("href")).toBe("/app/screener");
    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.queryByTestId("compare-table")).toBeNull();
  });

  it("treats one symbol as an unfinished comparison, not a column", () => {
    const fetchMock = stub();
    at("AAPL");
    render(<ComparePage />);

    expect(screen.getByTestId("compare-empty")).toBeTruthy();
    expect(screen.queryByTestId("compare-table")).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("renders two symbols as two columns from one request, in URL order", async () => {
    const fetchMock = stub();
    at("MSFT,AAPL");
    render(<ComparePage />);

    const table = await screen.findByTestId("compare-table");
    expect(fetchMock).toHaveBeenCalledTimes(1);

    const [url, init] = fetchMock.mock.calls[0] ?? [];
    expect(String(url)).toBe("/screens/select");
    expect(init?.method).toBe("POST");
    const body = JSON.parse(String(init?.body)) as {
      mode: string;
      columns: string[];
      criteria: { limit: number; filters: Array<Record<string, unknown>> };
    };
    expect(body.mode).toBe("criteria");
    // The symbol list travels as one membership predicate, not one request each.
    expect(body.criteria.filters).toEqual([
      { field: "symbol", op: "in", value: ["MSFT", "AAPL"] },
    ]);
    expect(body.columns).toContain("company");
    expect(body.columns).toContain("pe");
    expect(body.criteria.limit).toBe(2);

    const columns = within(table).getAllByTestId("compare-col");
    expect(columns).toHaveLength(2);
    expect(columns.map((column) => column.getAttribute("data-symbol"))).toEqual([
      "MSFT",
      "AAPL",
    ]);
    // The header carries identity and the quote; the API's own order is ignored.
    expect(columns[0]?.textContent).toContain("MSFT");
    expect(columns[0]?.textContent).toContain("MSFT Corp.");
    expect(columns[0]?.textContent).toContain("$410.12");
    expect(columns[1]?.textContent).toContain("Apple Inc.");
    expect(columns[1]?.textContent).toContain("$344.63");

    // Metrics are rows, and the sections are named.
    expect(screen.getByTestId("compare-row-pe")).toBeTruthy();
    expect(screen.getByTestId("compare-group-valuation").textContent).toContain("Valuation");
    expect(screen.getByTestId("compare-group-financial-health").textContent).toContain(
      "Financial health",
    );
  });

  it("prints an em dash for a null cell and a number for a real zero", async () => {
    stub([
      row("AAPL", { pe: null, forward_pe: 0, roe: 152.4 }),
      row("MSFT", { pe: 39.5, forward_pe: 36.07, roe: null }),
    ]);
    at("AAPL,MSFT");
    render(<ComparePage />);

    await screen.findByTestId("compare-table");
    expect(screen.getByTestId("compare-cell-pe-AAPL").textContent).toBe("—");
    expect(screen.getByTestId("compare-cell-pe-MSFT").textContent).toBe("39.50");
    // A zero is a measurement, not an absence.
    expect(screen.getByTestId("compare-cell-forward_pe-AAPL").textContent).toBe("0.00");
    expect(screen.getByTestId("compare-cell-roe-MSFT").textContent).toBe("—");
    // One reported value is not a comparison: MSFT's P/E wins nothing against
    // an em dash, and the row carries no marker at all.
    expect(screen.getByTestId("compare-cell-pe-MSFT").getAttribute("data-best")).toBeNull();
    // Two reported values, one of them a zero multiple: the real multiple is
    // the better answer, and it gets the row's one accent marker.
    expect(screen.getByTestId("compare-cell-forward_pe-MSFT").getAttribute("data-best")).toBe(
      "true",
    );
    expect(
      screen.getByTestId("compare-cell-forward_pe-AAPL").getAttribute("data-best"),
    ).toBeNull();
  });

  it("removes a company from the table and the URL, and undoes it", async () => {
    const user = userEvent.setup();
    stub();
    at("AAPL,MSFT,NVDA");
    render(<ComparePage />);

    await screen.findByTestId("compare-table");
    expect(within(screen.getByTestId("compare-table")).getAllByTestId("compare-col")).toHaveLength(3);

    await user.click(screen.getByRole("button", { name: "Remove MSFT from the comparison" }));

    expect(decodeURIComponent(search())).toBe("?symbols=AAPL,NVDA");
    await waitFor(() =>
      expect(
        within(screen.getByTestId("compare-table")).getAllByTestId("compare-col"),
      ).toHaveLength(2),
    );
    expect(screen.queryByRole("button", { name: "Remove MSFT from the comparison" })).toBeNull();

    await user.click(screen.getByRole("button", { name: /undo/i }));

    expect(search()).toContain("MSFT");
    await waitFor(() =>
      expect(
        within(screen.getByTestId("compare-table")).getAllByTestId("compare-col"),
      ).toHaveLength(3),
    );
    // The restored comparison is the original one, in the original order.
    expect(
      within(screen.getByTestId("compare-table"))
        .getAllByTestId("compare-col")
        .map((column) => column.getAttribute("data-symbol")),
    ).toEqual(["AAPL", "MSFT", "NVDA"]);
  });

  it("holds the line at ten companies and offers no eleventh", async () => {
    const ten = Array.from({ length: 10 }, (_, index) => `S${index}`);
    stub(ten.map((symbol) => row(symbol)));
    at(ten.join(","));
    render(<ComparePage />);

    const table = await screen.findByTestId("compare-table");
    expect(within(table).getAllByTestId("compare-col")).toHaveLength(10);

    const add = screen.getByTestId("compare-add");
    expect((add as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByTestId("compare-full").textContent).toContain("10");
    // Nothing in the URL changed by opening the page at the ceiling.
    expect(search()).toContain("S0");
    expect(search().match(/S\d/g)).toHaveLength(10);
  });

  it("scrolls sideways under 900 px with the metric column pinned", async () => {
    stub();
    at("AAPL,MSFT");
    render(<ComparePage />);

    await screen.findByTestId("compare-table");
    const scroller = screen.getByTestId("compare-scroll");
    expect(scroller.className).toContain("overflow-x-auto");
    const metricHeader = screen.getByTestId("compare-metric-head");
    expect(metricHeader.className).toContain("sticky");
    expect(metricHeader.className).toContain("left-0");
    expect(screen.getByTestId("compare-row-pe").firstElementChild?.className).toContain(
      "sticky",
    );
    // The sr-only "best" marker is absolutely positioned; every cell owns its
    // containing block so the marker cannot widen the document instead.
    expect(screen.getByTestId("compare-cell-pe-AAPL").className).toContain("relative");
  });
});
