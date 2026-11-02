/**
 * A row is a door, not a selection.
 *
 * The screener no longer docks a detail panel: every ticker, company and
 * symbol in a row is a real link to `/app/stock/{symbol}`, and the only
 * thing that selects a row is its checkbox — because three toolbar actions
 * (Add to, Compare, Set alert) still need one named row.
 *
 * These are the promises:
 *
 * * a plain click is a client-side navigation, not a round trip;
 * * a ctrl/meta click keeps the browser's own new-tab behaviour, which means
 *   the anchor has a real href and the handler does not swallow it;
 * * a checkbox selects without navigating, and the header checkbox selects
 *   every row on the page;
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

describe("the selection column", () => {
  it("selects a row without navigating", async () => {
    stubApi();
    const user = userEvent.setup();
    renderScreen();
    await firstRow();

    const checkbox = screen.getByTestId("row-select-S000") as HTMLInputElement;
    expect(checkbox.checked).toBe(false);

    await user.click(checkbox);

    expect(checkbox.checked).toBe(true);
    expect(window.location.pathname).toBe("/app/screener");
    // The selection is what the toolbar's Add to names.
    expect(screen.getByTestId("add-to-button").getAttribute("title")).toContain("S000");
  });

  it("selects every visible row from the header checkbox", async () => {
    stubApi();
    const user = userEvent.setup();
    renderScreen();
    await firstRow();

    const all = screen.getByTestId("select-all") as HTMLInputElement;
    await user.click(all);

    const rowBoxes = [
      ...screen.getByTestId("grid-table").querySelectorAll<HTMLInputElement>(
        'tbody input[data-testid^="row-select-"]',
      ),
    ];
    expect(rowBoxes).toHaveLength(20);
    expect(rowBoxes.every((box) => box.checked)).toBe(true);
    expect(window.location.pathname).toBe("/app/screener");

    await user.click(all);
    expect(rowBoxes.every((box) => box.checked)).toBe(false);
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
