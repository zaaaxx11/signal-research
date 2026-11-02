/**
 * A row is a door, not a selection.
 *
 * The screener no longer docks a detail panel: every ticker, company and
 * symbol in a row is a real link to `/app/stock/{symbol}`. A row is never a
 * selection: its own micro-actions name the one symbol they act on.
 *
 * These are the promises:
 *
 * * a plain click is a client-side navigation, not a round trip;
 * * a ctrl/meta click keeps the browser's own new-tab behaviour, which means
 *   the anchor has a real href and the handler does not swallow it;
 * * there is no checkbox lane, and an action on a row does not navigate it;
 * * there is no docked panel left to find.
 */

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { clearPageCache } from "@/data/screens";
import { VIEW_FIXTURE } from "@/grid/__fixtures__/views";
import { filterSchemaWire } from "@/filters/__fixtures__/schema";
import { resetFilterSchemaCache } from "@/filters/schema";

import { ScreenerScreen } from "./ScreenerScreen";
import { filterDocumentWire } from "./__fixtures__/filters";
import { resetFiltersDocumentCache } from "./filtersDocument";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function page(count = 20) {
  return {
    as_of: "2026-11-01T10:00:00Z",
    limit: 20,
    total: 5_600,
    universe_total: 5_600,
    returned: count,
    columns: [],
    rows: Array.from({ length: count }, (_, index) => ({
      symbol: `S${String(index).padStart(3, "0")}`,
      price: 100 + index,
    })),
    budget: { truncated: false, dropped_fields: [] },
  };
}

function stubApi(): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/screens/select/schema") {
        return json({ ...filterSchemaWire(), views: VIEW_FIXTURE });
      }
      if (url === "/screens/filters") return json(filterDocumentWire());
      if (url === "/screens/select" && init?.method === "POST") return json(page());
      return json({ errorMessage: `no route for ${url}` }, 404);
    }),
  );
}

beforeEach(() => {
  // jsdom has no layout: without a measured container the table never solves
  // its widths and no row mounts.
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
  resetFiltersDocumentCache();
  clearPageCache();
  window.localStorage.clear();
  window.history.replaceState(null, "", "/app/screener");
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function renderScreen() {
  return render(<ScreenerScreen assetClass="stock" />);
}

async function firstRow(): Promise<HTMLElement> {
  await waitFor(() =>
    expect(screen.getByTestId("grid-table").querySelector("tbody tr[data-symbol]")).not.toBeNull(),
  );
  return screen.getByTestId("grid-table").querySelector<HTMLElement>('tbody tr[data-symbol="S000"]')!;
}

describe("a screener row opens the ticker page", () => {
  it("navigates on a plain click on the ticker", async () => {
    stubApi();
    const user = userEvent.setup();
    renderScreen();
    await firstRow();

    const link = screen.getByTestId("symbol-link-S000");
    expect(link.getAttribute("href")).toBe("/app/stock/S000");

    await user.click(link);

    expect(window.location.pathname).toBe("/app/stock/S000");
  });

  it("keeps a real href for a ctrl-click, and lets the browser open the tab", async () => {
    stubApi();
    renderScreen();
    const row = await firstRow();
    const link = within(row).getByTestId("symbol-link-S000");

    // The href is the server-answerable URL, so middle-click and "open in a
    // new tab" work without JavaScript.
    expect(link.getAttribute("href")).toBe("/app/stock/S000");

    fireEvent.click(link, { ctrlKey: true });
    expect(window.location.pathname).toBe("/app/screener");

    fireEvent.click(link, { metaKey: true });
    expect(window.location.pathname).toBe("/app/screener");
  });

  it("navigates from the row itself, and from Enter on a focused row", async () => {
    stubApi();
    const user = userEvent.setup();
    renderScreen();
    const row = await firstRow();

    // A press anywhere in the row is the same door as the ticker.
    const price = row.querySelectorAll("td")[row.querySelectorAll("td").length - 1] as HTMLElement;
    await user.click(price);
    expect(window.location.pathname).toBe("/app/stock/S000");

    window.history.replaceState(null, "", "/app/screener");
    row.focus();
    await user.keyboard("{Enter}");
    expect(window.location.pathname).toBe("/app/stock/S000");
  });
});

describe("the action lane", () => {
  it("draws no checkbox in any row or in the header", async () => {
    stubApi();
    renderScreen();
    const row = await firstRow();

    expect(screen.getByTestId("grid-table").querySelectorAll('input[type="checkbox"]')).toHaveLength(0);
    expect(within(row).queryByRole("checkbox")).toBeNull();
    expect(screen.queryByTestId("select-all")).toBeNull();
    expect(screen.queryByTestId("row-select-S000")).toBeNull();
  });

  it("opens the row's Add-to menu without navigating", async () => {
    stubApi();
    const user = userEvent.setup();
    renderScreen();
    const row = await firstRow();

    await user.click(within(row).getByRole("button", { name: "Add S000 to a watchlist or portfolio" }));

    const menu = within(row).getByRole("menu", { name: "Add to" });
    expect(within(menu).getAllByRole("menuitem").map((item) => item.textContent)).toEqual([
      "Watchlist",
      "Portfolio",
    ]);
    expect(window.location.pathname).toBe("/app/screener");
  });

  it("alerts the row's symbol without navigating", async () => {
    stubApi();
    const user = userEvent.setup();
    renderScreen();
    const row = await firstRow();

    await user.click(within(row).getByRole("button", { name: "Set an alert for S000" }));
    const alert = screen.getByRole("dialog", { name: "Set alert" });
    expect(within(alert).getByTestId("alert-target").textContent).toContain("S000");
    expect(window.location.pathname).toBe("/app/screener");
  });

  it("compares through the toolbar mode and navigates with the ticked symbols", async () => {
    stubApi();
    const user = userEvent.setup();
    renderScreen();
    await firstRow();

    await user.click(screen.getByRole("button", { name: "Compare rows" }));
    await user.click(screen.getByRole("checkbox", { name: "Compare S000" }));
    await user.click(screen.getByRole("checkbox", { name: "Compare S002" }));
    await user.click(screen.getByRole("button", { name: "Confirm comparison of 2 symbols" }));

    expect(window.location.pathname).toBe("/app/compare");
    expect(window.location.search).toBe("?symbols=S000,S002");
  });
});

describe("the docked panel", () => {
  it("is gone: no detail panel and no two-pane row", async () => {
    stubApi();
    renderScreen();
    await firstRow();

    expect(screen.queryByTestId("detail-panel")).toBeNull();
    expect(screen.queryByTestId("screener-row")).toBeNull();
    // The table is the content band, at the full width of its region.
    expect(screen.getByTestId("table-frame")).toBeTruthy();
  });
});
