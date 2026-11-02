/**
 * The toolbar's own promises, at the panel: the star, the two lists, the
 * compare flow, the alert form, the pager arrows, and the page cache.
 *
 * The panel is where the toolbar's buttons become requests and storage writes,
 * so every assertion here is on `localStorage` or on the wire — never on a
 * label that could be true while the action did nothing.
 */

import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ALERTS_KEY, alertsStore } from "./alerts";
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

function rowFor(symbol: string): HTMLElement {
  const row = screen
    .getByTestId("grid-table")
    .querySelector<HTMLElement>(`tbody tr[data-symbol="${symbol}"]`);
  if (!row) throw new Error(`row ${symbol} is not on screen`);
  return row;
}

/** The row's own star, then one of the two lists: no checkbox, no selection. */
async function addRowTo(symbol: string, list: "Watchlist" | "Portfolio"): Promise<void> {
  const user = userEvent.setup();
  await user.click(
    within(rowFor(symbol)).getByRole("button", {
      name: `Add ${symbol} to a watchlist or portfolio`,
    }),
  );
  await user.click(within(rowFor(symbol)).getByRole("menuitem", { name: list }));
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
  alertsStore.write([]);
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
  it("writes a row's ticker to the watchlist from the star, with no dialog", async () => {
    stubApi();
    renderPanel();
    await ready();

    const star = within(rowFor("A3")).getByRole("button", {
      name: "Add A3 to a watchlist or portfolio",
    });
    expect(star.getAttribute("data-starred")).toBe("false");

    await addRowTo("A3", "Watchlist");

    expect(JSON.parse(window.localStorage.getItem(WATCHLIST_KEY) ?? "[]")).toEqual(["A3"]);
    expect(screen.getByTestId("toolbar-notice").textContent).toContain("A3 is on the watchlist");
    expect(screen.queryByRole("dialog")).toBeNull();
    // The store subscription is what fills the star — the write above, not a
    // second local flag.
    expect(star.getAttribute("data-starred")).toBe("true");
    expect(star.querySelector("svg")?.getAttribute("class")).toContain("fill-current");
  });

  it("writes a portfolio holding from the star with no quantity/cost detour", async () => {
    stubApi();
    renderPanel();
    await ready();
    await addRowTo("A4", "Portfolio");

    expect(screen.queryByRole("dialog")).toBeNull();
    expect(JSON.parse(window.localStorage.getItem(PORTFOLIO_KEY) ?? "[]")).toEqual([
      { symbol: "A4", quantity: 1, cost: 104 },
    ]);
    expect(screen.getByTestId("toolbar-notice").textContent).toContain("A4 is in the portfolio");
    expect(
      within(rowFor("A4")).getByRole("button", {
        name: /^Add A4 to a watchlist or portfolio/,
      }).getAttribute("data-starred"),
    ).toBe("true");
  });

  it("turns the star back to outline when the symbol leaves the list", async () => {
    stubApi();
    renderPanel();
    await ready();
    await addRowTo("A3", "Watchlist");

    const star = within(rowFor("A3")).getByRole("button", {
      name: /^Add A3 to a watchlist or portfolio/,
    });
    expect(star.getAttribute("data-starred")).toBe("true");

    act(() => watchlistStore.write([]));
    expect(star.getAttribute("data-starred")).toBe("false");
    expect(star.querySelector("svg")?.getAttribute("class")).not.toContain("fill-current");
  });

  it("asks through the ticker search when the toolbar's Add to has no row", async () => {
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
  it("enters selection mode with one checkbox per row and no dialog", async () => {
    stubApi();
    const user = userEvent.setup();
    renderPanel();
    await ready();

    expect(screen.getByTestId("grid-table").querySelectorAll('input[type="checkbox"]')).toHaveLength(0);
    await user.click(screen.getByRole("button", { name: "Compare rows" }));

    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByTestId("grid-table").querySelectorAll('input[type="checkbox"]')).toHaveLength(20);
  });

  it("takes up to ten symbols and Done navigates with them in the query", async () => {
    stubApi();
    const user = userEvent.setup();
    renderPanel();
    await ready();

    await user.click(screen.getByRole("button", { name: "Compare rows" }));
    for (let index = 0; index < 10; index += 1) {
      await user.click(screen.getByRole("checkbox", { name: `Compare A${index}` }));
    }
    const boxes = screen.getAllByRole("checkbox") as HTMLInputElement[];
    expect(boxes.filter((box) => box.checked)).toHaveLength(10);
    expect(boxes[10]?.disabled).toBe(true);

    const done = screen.getByRole("button", { name: "Confirm comparison of 10 symbols" });
    expect(done.textContent).toContain("10");
    await user.click(done);

    expect(window.location.pathname).toBe("/app/compare");
    expect(window.location.search).toBe(
      "?symbols=A0,A1,A2,A3,A4,A5,A6,A7,A8,A9",
    );
  });

  it("Escape leaves compare mode and hides the boxes", async () => {
    stubApi();
    const user = userEvent.setup();
    renderPanel();
    await ready();

    await user.click(screen.getByRole("button", { name: "Compare rows" }));
    await user.click(screen.getByRole("checkbox", { name: "Compare A0" }));
    expect(screen.getByRole("button", { name: "Confirm comparison of 1 symbol" })).toBeTruthy();

    await user.keyboard("{Escape}");
    expect(screen.queryByTestId("compare-done")).toBeNull();
    expect(screen.getByTestId("grid-table").querySelectorAll('input[type="checkbox"]')).toHaveLength(0);
  });
});

describe("Set alert", () => {
  it("opens from the row's bell with only condition, value and note", async () => {
    stubApi();
    const user = userEvent.setup();
    renderPanel();
    await ready();

    await user.click(within(rowFor("A5")).getByRole("button", { name: "Set an alert for A5" }));
    const dialog = screen.getByRole("dialog", { name: "Set alert" });

    expect(within(dialog).getByTestId("alert-target").textContent).toBe("A5");
    // The symbol is known: no ticker search inside the dialog.
    expect(within(dialog).queryByRole("combobox", { name: /search/i })).toBeNull();
    expect(within(dialog).queryByRole("textbox", { name: /ticker/i })).toBeNull();
    expect(within(dialog).getByRole("combobox", { name: "Condition" })).toBeTruthy();
    expect(within(dialog).getByRole("spinbutton", { name: "Value" })).toBeTruthy();
    expect(within(dialog).getByRole("textbox", { name: "Note" })).toBeTruthy();

    await user.type(within(dialog).getByRole("spinbutton", { name: "Value" }), "250");
    await user.click(within(dialog).getByRole("button", { name: "Save alert" }));

    const stored = JSON.parse(window.localStorage.getItem(ALERTS_KEY) ?? "{}") as {
      alerts: Array<{ symbol: string; value: number }>;
    };
    expect(stored.alerts[0]).toMatchObject({ symbol: "A5", value: 250 });
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
  it("has no compare icon on any row", async () => {
    stubApi();
    renderPanel();
    await ready();

    expect(screen.queryByRole("button", { name: /^Compare A\d/ })).toBeNull();
    const lane = within(rowFor("A2")).getByTestId("row-actions-A2");
    expect(lane.querySelector('[data-icon="split"]')).toBeNull();
  });

  it("draws no checkbox in the grid", async () => {
    stubApi();
    renderPanel();
    await ready();

    expect(screen.getByTestId("grid-table").querySelectorAll('input[type="checkbox"]')).toHaveLength(0);
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

describe("state behind a row's star and bell", () => {
  const starName = (suffix = "") => `Add A5 to a watchlist or portfolio${suffix}`;

  it("fills the star for a watchlist symbol alone, and says so", async () => {
    watchlistStore.write(["A5"]);
    stubApi();
    renderPanel();
    await ready();

    const star = within(rowFor("A5")).getByRole("button", { name: starName(" (on a list)") });
    expect(star.getAttribute("data-starred")).toBe("true");
    expect(star.querySelector("svg")?.getAttribute("class")).toContain("fill-current");
    expect(star.getAttribute("title")).toContain("A5 is on a list");
  });

  it("fills the star for a portfolio holding alone, and says so", async () => {
    portfolioStore.write([{ symbol: "A5", quantity: 1, cost: 100 }]);
    stubApi();
    renderPanel();
    await ready();

    const star = within(rowFor("A5")).getByRole("button", { name: starName(" (on a list)") });
    expect(star.getAttribute("data-starred")).toBe("true");
    expect(star.querySelector("svg")?.getAttribute("class")).toContain("fill-current");
  });

  it("leaves other rows' stars empty", async () => {
    watchlistStore.write(["A5"]);
    stubApi();
    renderPanel();
    await ready();

    const other = within(rowFor("A6")).getByRole("button", { name: starName().replace("A5", "A6") });
    expect(other.getAttribute("data-starred")).toBe("false");
    expect(other.querySelector("svg")?.getAttribute("class") ?? "").not.toContain("fill-current");
  });

  it("fills A5's bell on save and empties it when the last alert is deleted", async () => {
    stubApi();
    const user = userEvent.setup();
    renderPanel();
    await ready();

    const rowBell = (name: string) => within(rowFor("A5")).getByRole("button", { name });

    // Empty first: the bell is an outline with no state behind it.
    const empty = rowBell("Set an alert for A5");
    expect(empty.getAttribute("data-alerted")).toBe("false");
    expect(empty.querySelector("svg")?.getAttribute("class") ?? "").not.toContain("fill-current");

    await user.click(empty);
    const dialog = screen.getByRole("dialog", { name: "Set alert" });
    await user.type(within(dialog).getByRole("spinbutton", { name: "Value" }), "250");
    await user.click(within(dialog).getByRole("button", { name: "Save alert" }));

    // No reload, no reopen: the row behind the dialog is already filled.
    const filled = rowBell("Set an alert for A5 (1 active)");
    expect(filled.getAttribute("data-alerted")).toBe("true");
    expect(filled.querySelector("svg")?.getAttribute("class")).toContain("fill-current");
    expect(filled.querySelector("svg")?.getAttribute("class")).toContain("text-accent-text");
    expect(filled.getAttribute("title")).toBe("1 alert for A5 — review or add another");

    await user.click(within(dialog).getByRole("button", { name: /^Delete alert: A5/ }));

    const back = rowBell("Set an alert for A5");
    expect(back.getAttribute("data-alerted")).toBe("false");
    expect(back.querySelector("svg")?.getAttribute("class") ?? "").not.toContain("fill-current");
    expect(JSON.parse(window.localStorage.getItem(ALERTS_KEY) ?? "null")).toEqual({
      version: 1,
      alerts: [],
    });
  });
});
