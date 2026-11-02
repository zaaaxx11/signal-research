/**
 * The toolbar's own promises, at the panel: the star, the two lists, the
 * compare flow, the alert form, the pager arrows, and the page cache.
 *
 * The panel is where the toolbar's buttons become requests and storage writes,
 * so every assertion here is on `localStorage` or on the wire — never on a
 * label that could be true while the action did nothing.
 */

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ALERTS_KEY } from "./alerts";
import { PORTFOLIO_KEY, WATCHLIST_KEY, portfolioStore, watchlistStore } from "@/lists/storage";
import type { ViewDefinition } from "@/data/screens";

import { ScreenPanel } from "./ScreenPanel";

const VIEW: ViewDefinition = {
  name: "today",
  label: "Today",
  job: "What is moving right now?",
  columns: ["price", "change", "volume"],
};

const HITS = [
  { symbol: "AAPL", name: "Apple Inc", exchange: "NASDAQ" },
  { symbol: "APPS", name: "Digital Turbine Inc", exchange: "NYSE" },
];

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

/** Page one is A*, page two is B* — a page's rows name the page. */
function pageBody(offset: number) {
  const prefix = offset === 0 ? "A" : "B";
  return {
    as_of: "2026-11-01T10:00:00Z",
    limit: 20,
    total: 5_600,
    universe_total: 5_600,
    returned: 20,
    columns: VIEW.columns,
    rows: Array.from({ length: 20 }, (_, index) => ({
      symbol: `${prefix}${index}`,
      price: 100 + index,
      change: 1,
      volume: 1_000,
    })),
    budget: { truncated: false, dropped_fields: [] },
  };
}

function stubApi() {
  const selects: Array<{ criteria: { offset: number } }> = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.startsWith("/symbols/search")) return json(HITS);
    if (url === "/screens/select") {
      const body = JSON.parse(String(init?.body)) as { criteria: { offset: number } };
      selects.push(body);
      return json(pageBody(body.criteria.offset));
    }
    return json({ error: "not_found" }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return { fetchMock, selects };
}

function renderPanel() {
  return render(<ScreenPanel view={VIEW} onActivate={vi.fn()} containerWidth={1280} />);
}

/** Tick one row's checkbox: the only way a row is selected now. */
async function selectRow(symbol: string): Promise<void> {
  await userEvent.setup().click(screen.getByTestId(`row-select-${symbol}`));
}

async function ready(): Promise<void> {
  await waitFor(() => expect(screen.getByTestId("range").textContent).toBe("1/5,600 Total"));
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  // The list stores keep an in-memory snapshot; clear it as well as the key,
  // or the next test inherits this one's symbols.
  watchlistStore.write([]);
  portfolioStore.write([]);
  window.localStorage.clear();
});

describe("the page cache", () => {
  it("draws a page already seen without a skeleton and without a request", async () => {
    const { fetchMock } = stubApi();
    const user = userEvent.setup();
    renderPanel();
    await ready();

    await user.click(screen.getByRole("button", { name: "Next page" }));
    await waitFor(() => expect(screen.getByTestId("range").textContent).toBe("2/5,600 Total"));
    expect(fetchMock).toHaveBeenCalledTimes(2);

    // Back to page one: the rows are already in the tab.
    await user.click(screen.getByRole("button", { name: "Previous page" }));
    await waitFor(() => expect(screen.getByTestId("range").textContent).toBe("1/5,600 Total"));

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(screen.getByTestId("cache-hint")).toBeTruthy();
    const table = screen.getByTestId("grid-table");
    // A skeleton is a `tr` with no data; a cached page must not paint one.
    expect(table.querySelectorAll('tbody tr[aria-hidden="true"]')).toHaveLength(0);
    expect(table.querySelector("tbody tr[data-symbol]")?.getAttribute("data-symbol")).toBe("A0");
  });

  it("still re-asks the server on Refresh", async () => {
    const { fetchMock } = stubApi();
    const user = userEvent.setup();
    renderPanel();
    await ready();

    await user.click(screen.getByRole("button", { name: "Next page" }));
    await waitFor(() => expect(screen.getByTestId("range").textContent).toBe("2/5,600 Total"));
    await user.click(screen.getByRole("button", { name: "Previous page" }));
    await waitFor(() => expect(screen.getByTestId("range").textContent).toBe("1/5,600 Total"));
    expect(fetchMock).toHaveBeenCalledTimes(2);

    await user.click(screen.getByRole("button", { name: "Refresh the screen" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
  });
});

describe("Add to", () => {
  it("writes the selected ticker to the watchlist and says so", async () => {
    stubApi();
    const user = userEvent.setup();
    renderPanel();
    await ready();
    await selectRow("A3");

    await user.click(screen.getByTestId("add-to-button"));
    const menu = screen.getByRole("menu", { name: "Add to" });
    expect(within(menu).getAllByRole("menuitem").map((item) => item.textContent)).toEqual([
      "Watchlist",
      "Portfolio",
    ]);

    await user.click(within(menu).getByRole("menuitem", { name: "Watchlist" }));
    expect(JSON.parse(window.localStorage.getItem(WATCHLIST_KEY) ?? "[]")).toEqual(["A3"]);
    expect(screen.getByTestId("toolbar-notice").textContent).toContain("A3 is on the watchlist");
    // A single ticker is added directly: no search box, because the row named it.
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("persists a portfolio holding with the quantity and cost it was given", async () => {
    stubApi();
    const user = userEvent.setup();
    renderPanel();
    await ready();
    await selectRow("A4");

    await user.click(screen.getByTestId("add-to-button"));
    await user.click(screen.getByRole("menuitem", { name: "Portfolio" }));

    const dialog = screen.getByRole("dialog", { name: "Add to portfolio" });
    await user.clear(within(dialog).getByRole("spinbutton", { name: "Quantity" }));
    await user.type(within(dialog).getByRole("spinbutton", { name: "Quantity" }), "4");
    await user.type(within(dialog).getByRole("spinbutton", { name: "Average cost" }), "12.5");
    await user.click(within(dialog).getByRole("button", { name: "Add holding" }));

    expect(JSON.parse(window.localStorage.getItem(PORTFOLIO_KEY) ?? "[]")).toEqual([
      { symbol: "A4", quantity: 4, cost: 12.5 },
    ]);
    expect(within(dialog).getByRole("status").textContent).toContain("A4 is in the portfolio");
  });

  it("asks through the ticker search when no row is selected", async () => {
    stubApi();
    const user = userEvent.setup();
    renderPanel();
    await ready();

    await user.click(screen.getByTestId("add-to-button"));
    await user.click(screen.getByRole("menuitem", { name: "Watchlist" }));

    const dialog = screen.getByRole("dialog", { name: "Add to watchlist" });
    const search = within(dialog).getByRole("combobox", { name: "Search a ticker to add" });
    await user.type(search, "app");
    const list = await within(dialog).findByRole("listbox", { name: "Symbol results" });
    // "app" starts APPS and only contains AAPL, so the prefix comes first.
    const options = within(list).getAllByRole("option");
    expect(options[0]?.textContent).toMatch(/^APPS/);
    expect(options[1]?.textContent).toMatch(/^AAPL/);
    await user.click(options[0]!);

    expect(JSON.parse(window.localStorage.getItem(WATCHLIST_KEY) ?? "[]")).toEqual(["APPS"]);
    expect(within(dialog).getByRole("status").textContent).toContain("APPS is on the watchlist");
  });
});

describe("Compare", () => {
  it("starts with a ticker search, takes up to five, and shows the comparison", async () => {
    stubApi();
    const user = userEvent.setup();
    renderPanel();
    await ready();

    await user.click(screen.getByRole("button", { name: "Compare rows" }));
    const dialog = screen.getByRole("dialog", { name: "Compare · Today" });
    const search = within(dialog).getByRole("combobox", { name: "Search a ticker to compare" });

    await user.type(search, "app");
    const options = await within(dialog).findAllByRole("option");
    await user.click(options[0]!);
    // Choosing writes the symbol into the box; clear it to ask a second time.
    await user.clear(search);
    await user.type(search, "app");
    const again = await within(dialog).findAllByRole("option");
    await user.click(again[1]!);

    const table = within(dialog).getByTestId("compare-table");
    expect(within(table).getByText("AAPL")).toBeTruthy();
    expect(within(table).getByText("APPS")).toBeTruthy();
    expect(dialog.textContent).toContain("2 of 5 picked.");
  });
});

describe("Set alert", () => {
  it("searches a ticker, saves the alert and can delete it again", async () => {
    stubApi();
    const user = userEvent.setup();
    renderPanel();
    await ready();

    await user.click(screen.getByRole("button", { name: "Set alert" }));
    const dialog = screen.getByRole("dialog", { name: "Set alert" });
    const search = within(dialog).getByRole("combobox", { name: "Search a ticker for the alert" });
    await user.type(search, "aapl");
    // The condition select also renders `<option>`s; the hits live in the
    // combobox's own listbox.
    const list = await within(dialog).findByRole("listbox", { name: "Symbol results" });
    await user.click(within(list).getAllByRole("option")[0]!);

    await user.type(within(dialog).getByRole("spinbutton", { name: "Value" }), "250");
    await user.click(within(dialog).getByRole("button", { name: "Save alert" }));

    const stored = JSON.parse(window.localStorage.getItem(ALERTS_KEY) ?? "{}") as {
      alerts: Array<{ symbol: string; value: number }>;
    };
    expect(stored.alerts).toHaveLength(1);
    expect(stored.alerts[0]).toMatchObject({ symbol: "AAPL", value: 250 });

    const alerts = within(dialog).getByRole("list", { name: "Saved alerts" });
    expect(alerts.textContent).toContain("AAPL · Price above 250");
    await user.click(within(alerts).getByRole("button", { name: /Delete alert/ }));

    expect(
      (JSON.parse(window.localStorage.getItem(ALERTS_KEY) ?? "{}") as { alerts: unknown[] }).alerts,
    ).toHaveLength(0);
    expect(within(dialog).getByText("No alerts yet.")).toBeTruthy();
  });
});

describe("the pager arrows", () => {
  it("page from the bottom bar and disable at the ends", async () => {
    stubApi();
    const user = userEvent.setup();
    renderPanel();
    await ready();

    const prev = screen.getByTestId("pager-prev") as HTMLButtonElement;
    const next = screen.getByTestId("pager-next") as HTMLButtonElement;
    expect(prev.disabled).toBe(true);
    expect(next.disabled).toBe(false);

    await user.click(next);
    await waitFor(() => expect(screen.getByTestId("range").textContent).toBe("2/5,600 Total"));
    expect((screen.getByTestId("pager-prev") as HTMLButtonElement).disabled).toBe(false);

    await user.click(screen.getByTestId("pager-prev"));
    await waitFor(() => expect(screen.getByTestId("range").textContent).toBe("1/5,600 Total"));
  });
});

describe("the toolbar's row actions", () => {
  it("Compares from the checked row", async () => {
    stubApi();
    const user = userEvent.setup();
    renderPanel();
    await ready();
    await selectRow("A2");

    await user.click(screen.getByRole("button", { name: "Compare rows" }));
    const dialog = screen.getByRole("dialog", { name: "Compare · Today" });

    // The box is not empty: the row the reader checked is already in it.
    expect(within(dialog).getByTestId("compare-table").textContent).toContain("A2");
    expect(dialog.textContent).toContain("1 of 5 picked.");
  });

  it("Sets an alert on the checked row", async () => {
    stubApi();
    const user = userEvent.setup();
    renderPanel();
    await ready();
    await selectRow("A5");

    await user.click(screen.getByRole("button", { name: "Set alert" }));
    const dialog = screen.getByRole("dialog", { name: "Set alert" });

    // The form names the ticker instead of asking for one.
    expect(within(dialog).getByText("A5")).toBeTruthy();
  });
});

describe("the toolbar's names", () => {
  it("gives every button visible text or an accessible name", async () => {
    stubApi();
    renderPanel();
    await ready();

    const toolbar = screen.getByTestId("table-toolbar");
    const buttons = [...toolbar.querySelectorAll<HTMLButtonElement>("button")];
    expect(buttons.length).toBeGreaterThan(6);
    for (const button of buttons) {
      const text = (button.textContent ?? "").trim();
      const named = (button.getAttribute("aria-label") ?? "").trim();
      expect(text.length > 0 || named.length > 0, `unnamed button: ${button.outerHTML}`).toBe(true);
      // Icon-only buttons carry both a name and a title.
      if (text.length === 0) expect((button.getAttribute("title") ?? "").length).toBeGreaterThan(0);
    }
  });
});
