/**
 * The ticker band's three actions: Compare, Add to, Set alert.
 *
 * All three are the screener's own flows — the same compare dialog, the same
 * add-to dialog, the same alert form — but the page already knows which
 * instrument the reader is looking at, so none of them may ask for that symbol
 * again. These cases prove the seeding *and* the effect: the star writes the
 * real watchlist key, Portfolio opens the quantity/cost form and writes a
 * the bell saves an alert under ``signal-research.alerts.v1``, and Escape hands
 * focus back to whatever opened the dialog.
 */

import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { resetSnapshotCache } from "@/detail/useDetail";
import { ALERTS_KEY, alertsStore } from "@/grid/alerts";
import { PORTFOLIO_KEY, WATCHLIST_KEY, portfolioStore, watchlistStore } from "@/lists/storage";

import { TickerPage } from "./TickerPage";
import { json, rowPayload, stubTickerApi } from "./__fixtures__/ticker";

const MSFT = { symbol: "MSFT", name: "Microsoft Corp", exchange: "Nasdaq" };

function stored(key: string): unknown {
  return JSON.parse(window.localStorage.getItem(key) ?? "null");
}

beforeEach(() => {
  resetSnapshotCache();
  window.localStorage.clear();
  // The stores cache their parsed snapshot; a key cleared behind their back
  // would read as this file's previous test. Reset them through the same path
  // every page writes.
  watchlistStore.write([]);
  portfolioStore.write([]);
  alertsStore.write([]);
  window.history.replaceState(null, "", "/app/stock/AAPL");
});

afterEach(() => {
  vi.unstubAllGlobals();
});

async function button(name: string): Promise<HTMLElement> {
  return screen.findByRole("button", { name });
}

async function openCompare(
  user: ReturnType<typeof userEvent.setup>,
  symbol = "AAPL",
): Promise<{ button: HTMLElement; dialog: HTMLElement }> {
  const trigger = await button(`Compare ${symbol}`);
  await user.click(trigger);
  const dialog = screen.getByRole("dialog", { name: `Compare · ${symbol}` });
  return { button: trigger, dialog };
}

describe("the band's action group", () => {
  it("draws Compare, Add to and Set alert as one group, icons only below 900 px", async () => {
    stubTickerApi();
    render(<TickerPage symbol="AAPL" />);

    const group = await screen.findByTestId("ticker-actions");
    expect(screen.getByTestId("ticker-header").contains(group)).toBe(true);

    const compare = within(group).getByTestId("ticker-compare");
    const add = within(group).getByTestId("ticker-add");
    const alert = within(group).getByTestId("ticker-alert");

    // One glyph and one word each, and the word is what disappears on a phone.
    for (const [node, word] of [
      [compare, "Compare"],
      [add, "Add to"],
      [alert, "Set alert"],
    ] as const) {
      expect(node.querySelector("svg")?.getAttribute("aria-hidden")).toBe("true");
      expect(within(node).getByText(word).className).toContain("max-[900px]:hidden");
      expect(node.getAttribute("aria-label")).toContain(word);
    }

    expect(compare.getAttribute("aria-label")).toBe("Compare AAPL");
    expect(add.getAttribute("aria-label")).toBe("Add to list: AAPL");
    expect(alert.getAttribute("aria-label")).toBe("Set alert for AAPL");
  });
});

describe("Add to", () => {
  it("writes the symbol to the watchlist from the star and confirms it", async () => {
    stubTickerApi();
    const user = userEvent.setup();
    render(<TickerPage symbol="AAPL" />);

    await user.click(await button("Add to list: AAPL"));
    await user.click(screen.getByRole("menuitem", { name: "Watchlist" }));

    expect(stored(WATCHLIST_KEY)).toEqual(["AAPL"]);
    expect(screen.getByTestId("ticker-action-notice").textContent).toContain(
      "AAPL is on the watchlist.",
    );
    expect(screen.getByTestId("ticker-action-notice").className).toContain("text-fg-muted");
    // A confirmation is a sentence, not a dialog the reader has to dismiss.
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("opens the quantity and cost form for Portfolio and stores the holding", async () => {
    stubTickerApi();
    const user = userEvent.setup();
    render(<TickerPage symbol="AAPL" />);

    await user.click(await button("Add to list: AAPL"));
    await user.click(screen.getByRole("menuitem", { name: "Portfolio" }));

    const dialog = screen.getByRole("dialog", { name: "Add to portfolio" });
    // Seeded: the page's symbol is already the row, only the numbers are open.
    expect(within(dialog).getByText("AAPL")).toBeTruthy();

    const quantity = within(dialog).getByRole("spinbutton", { name: "Quantity" });
    await user.clear(quantity);
    await user.type(quantity, "10");
    const cost = within(dialog).getByRole("spinbutton", { name: "Average cost" });
    await user.type(cost, "200");
    await user.click(within(dialog).getByRole("button", { name: "Add holding" }));

    expect(stored(PORTFOLIO_KEY)).toEqual([{ symbol: "AAPL", quantity: 10, cost: 200 }]);
    expect(within(dialog).getByRole("status").textContent).toContain(
      "AAPL is in the portfolio.",
    );

    // The menu item that opened this dialog no longer exists; focus goes back
    // to the control that owns the menu.
    await user.click(within(dialog).getByRole("button", { name: "Done" }));
    await waitFor(() =>
      expect(document.activeElement).toBe(screen.getByTestId("ticker-add")),
    );
  });

  it("reports a duplicate instead of storing it twice", async () => {
    watchlistStore.write(["AAPL"]);
    stubTickerApi();
    const user = userEvent.setup();
    render(<TickerPage symbol="AAPL" />);

    await user.click(await button("Add to list: AAPL (in watchlist)"));
    await user.click(screen.getByRole("menuitem", { name: "Watchlist" }));

    expect(stored(WATCHLIST_KEY)).toEqual(["AAPL"]);
    expect(screen.getByTestId("ticker-action-notice").textContent).toContain(
      "AAPL is already on this list.",
    );
    // A refusal is not dressed as a confirmation.
    expect(screen.getByTestId("ticker-action-notice").className).toContain("text-alert");
  });

  it("closes the menu with Escape and gives focus back to the trigger", async () => {
    stubTickerApi();
    const user = userEvent.setup();
    render(<TickerPage symbol="AAPL" />);

    const trigger = await button("Add to list: AAPL");
    await user.click(trigger);
    expect(screen.getByRole("menu", { name: "Add to a list: AAPL" })).toBeTruthy();

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("menu")).toBeNull();
    await waitFor(() => expect(document.activeElement).toBe(trigger));
  });
});

describe("Set alert", () => {
  it("opens with the symbol prefilled and saves it to the alert list", async () => {
    stubTickerApi();
    const user = userEvent.setup();
    render(<TickerPage symbol="AAPL" />);

    await user.click(await button("Set alert for AAPL"));
    const dialog = screen.getByRole("dialog", { name: "Set alert" });
    expect(within(dialog).getByTestId("alert-target").textContent).toBe("AAPL");

    await user.type(within(dialog).getByRole("spinbutton", { name: "Value" }), "250");
    await user.click(within(dialog).getByRole("button", { name: "Save alert" }));

    const saved = stored(ALERTS_KEY) as {
      version: number;
      alerts: Array<Record<string, unknown>>;
    };
    expect(ALERTS_KEY).toBe("signal-research.alerts.v1");
    expect(saved.version).toBe(1);
    expect(saved.alerts).toHaveLength(1);
    expect(saved.alerts[0]).toMatchObject({
      symbol: "AAPL",
      condition: "price_above",
      value: 250,
    });

    // The same dialog lists what it just wrote.
    const list = within(dialog).getByRole("list", { name: "Saved alerts" });
    expect(list.querySelectorAll("li")).toHaveLength(1);
    expect(within(dialog).getByText(/Saved: AAPL · Price above 250/)).toBeTruthy();
  });
});

describe("the star and the bell carry state", () => {
  /** Seeded through the stores, which is the path every page writes. */
  function resetLists(): void {
    watchlistStore.write([]);
    portfolioStore.write([]);
    alertsStore.write([]);
  }

  it("draws both as outlines when the symbol has no list and no alert", async () => {
    resetLists();
    stubTickerApi();
    render(<TickerPage symbol="AAPL" />);

    const add = await button("Add to list: AAPL");
    const alert = screen.getByTestId("ticker-alert");

    expect(add.getAttribute("data-starred")).toBe("false");
    expect(add.querySelector("svg")?.getAttribute("class") ?? "").not.toContain("fill-current");
    expect(alert.getAttribute("data-alerted")).toBe("false");
    expect(alert.querySelector("svg")?.getAttribute("class") ?? "").not.toContain("fill-current");
    // Empty is stated in words and in a tooltip, not only in the absence of ink.
    expect(add.getAttribute("title")).toContain("Add AAPL");
    expect(alert.getAttribute("title")).toBe("Write a price alert for AAPL");
  });

  it("fills the star from the watchlist and names that list", async () => {
    resetLists();
    watchlistStore.write(["AAPL"]);
    stubTickerApi();
    render(<TickerPage symbol="AAPL" />);

    const add = await button("Add to list: AAPL (in watchlist)");
    expect(add.getAttribute("data-starred")).toBe("true");
    expect(add.querySelector("svg")?.getAttribute("class")).toContain("fill-current");
    expect(add.getAttribute("title")).toBe("AAPL is in the watchlist");
  });

  it("fills the star from the portfolio alone and names that list", async () => {
    resetLists();
    portfolioStore.write([{ symbol: "AAPL", quantity: 10, cost: 200 }]);
    stubTickerApi();
    render(<TickerPage symbol="AAPL" />);

    const add = await button("Add to list: AAPL (in portfolio)");
    expect(add.getAttribute("data-starred")).toBe("true");
    expect(add.querySelector("svg")?.getAttribute("class")).toContain("fill-current");
    expect(add.getAttribute("title")).toBe("AAPL is in the portfolio");
  });

  it("flips the star on the write and back on the remove, without a reload", async () => {
    resetLists();
    stubTickerApi();
    const user = userEvent.setup();
    render(<TickerPage symbol="AAPL" />);

    await user.click(await button("Add to list: AAPL"));
    await user.click(screen.getByRole("menuitem", { name: "Watchlist" }));

    expect(screen.getByTestId("ticker-add").getAttribute("aria-label")).toBe(
      "Add to list: AAPL (in watchlist)",
    );
    expect(screen.getByTestId("ticker-add").getAttribute("data-starred")).toBe("true");

    // The Watchlist page's remove is the same store; a subscription is what
    // makes this an immediate flip rather than a stale fill until reload.
    act(() => watchlistStore.write([]));

    expect(screen.getByTestId("ticker-add").getAttribute("aria-label")).toBe("Add to list: AAPL");
    expect(screen.getByTestId("ticker-add").getAttribute("data-starred")).toBe("false");
  });

  it("fills the bell when an alert is saved and empties it when the last is deleted", async () => {
    resetLists();
    stubTickerApi();
    const user = userEvent.setup();
    render(<TickerPage symbol="AAPL" />);

    await user.click(await button("Set alert for AAPL"));
    const dialog = screen.getByRole("dialog", { name: "Set alert" });
    await user.type(within(dialog).getByRole("spinbutton", { name: "Value" }), "250");
    await user.click(within(dialog).getByRole("button", { name: "Save alert" }));

    // Still inside the dialog: the band behind it is already filled.
    const bell = screen.getByTestId("ticker-alert");
    expect(bell.getAttribute("data-alerted")).toBe("true");
    expect(bell.getAttribute("aria-label")).toBe("Set alert for AAPL (1 active)");
    expect(bell.getAttribute("title")).toBe("1 alert for AAPL — review or add another");
    expect(bell.querySelector("svg")?.getAttribute("class")).toContain("fill-current");
    expect(bell.querySelector("svg")?.getAttribute("class")).toContain("text-accent-text");

    await user.click(within(dialog).getByRole("button", { name: /^Delete alert: AAPL/ }));

    expect(bell.getAttribute("data-alerted")).toBe("false");
    expect(bell.getAttribute("aria-label")).toBe("Set alert for AAPL");
    expect(bell.querySelector("svg")?.getAttribute("class") ?? "").not.toContain("fill-current");
    expect(window.localStorage.getItem(ALERTS_KEY)).toBe('{"version":1,"alerts":[]}');
  });
});

describe("Compare from the ticker page", () => {
  it("opens the comparison with the current symbol already picked", async () => {
    stubTickerApi();
    const user = userEvent.setup();
    render(<TickerPage symbol="AAPL" />);

    const { dialog } = await openCompare(user);

    expect(within(dialog).getByTestId("compare-pick-AAPL")).toBeTruthy();
    expect(dialog.textContent).toContain("1 of 5 picked.");
    // A seeded symbol is a comparison, not a search prompt.
    expect(within(dialog).getByTestId("compare-table")).toBeTruthy();
  });

  it("adds a second ticker to the side-by-side row and drops the seeded one", async () => {
    stubTickerApi({ search: () => json([MSFT]) });
    const user = userEvent.setup();
    render(<TickerPage symbol="AAPL" />);

    const { dialog } = await openCompare(user);
    const search = within(dialog).getByRole("combobox", { name: "Search a ticker to compare" });
    await user.type(search, "msft");
    const list = await within(dialog).findByRole(
      "listbox",
      { name: "Symbol results" },
      { timeout: 3_000 },
    );
    await user.click(within(list).getByRole("option", { name: /MSFT/ }));

    const table = within(dialog).getByTestId("compare-table");
    const headers = within(table)
      .getAllByRole("columnheader")
      .map((cell) => cell.textContent ?? "");
    expect(headers.some((text) => text.startsWith("AAPL"))).toBe(true);
    expect(headers.some((text) => text.startsWith("MSFT"))).toBe(true);

    // One row, one cell per symbol: the comparison is column by column.
    const symbolRow = within(table).getByRole("rowheader", { name: "Symbol" }).closest("tr");
    expect(symbolRow).not.toBeNull();
    expect(
      within(symbolRow as HTMLElement)
        .getAllByRole("cell")
        .map((cell) => cell.textContent),
    ).toEqual(["AAPL", "MSFT"]);
    expect(dialog.textContent).toContain("2 of 5 picked.");

    await user.click(
      within(dialog).getByRole("button", { name: "Remove AAPL from the comparison" }),
    );

    expect(within(dialog).queryByTestId("compare-pick-AAPL")).toBeNull();
    expect(within(dialog).getByTestId("compare-pick-MSFT")).toBeTruthy();
    expect(dialog.textContent).toContain("1 of 5 picked.");
    const remaining = within(dialog).getByTestId("compare-table");
    expect(within(remaining).queryByText("AAPL")).toBeNull();
    expect(within(remaining).getAllByText("MSFT").length).toBeGreaterThan(0);
  });

  it("keeps a symbol added while the page's scan row is still loading", async () => {
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    stubTickerApi({
      search: () => json([MSFT]),
      select: async () => {
        await gate;
        return json(rowPayload());
      },
    });
    const user = userEvent.setup();
    render(<TickerPage symbol="AAPL" />);

    const { button: trigger, dialog } = await openCompare(user);
    const search = within(dialog).getByRole("combobox", { name: "Search a ticker to compare" });
    await user.type(search, "msft");
    const list = await within(dialog).findByRole(
      "listbox",
      { name: "Symbol results" },
      { timeout: 3_000 },
    );
    await user.click(within(list).getByRole("option", { name: /MSFT/ }));

    // The selection route answers mid-comparison. A re-seed here would erase
    // the symbol the reader just added.
    await act(async () => {
      release();
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(within(dialog).getByTestId("compare-pick-AAPL")).toBeTruthy();
    expect(within(dialog).getByTestId("compare-pick-MSFT")).toBeTruthy();

    // The late row is not lost: the next comparison opens with its values.
    await user.keyboard("{Escape}");
    await user.click(trigger);
    const reopened = screen.getByRole("dialog", { name: "Compare · AAPL" });
    const priceRow = within(reopened)
      .getByRole("rowheader", { name: "Price" })
      .closest("tr");
    // One column — a new comparison starts from the page's symbol alone — and
    // a real price rather than the em dash an unloaded row would print.
    expect(
      within(priceRow as HTMLElement)
        .getAllByRole("cell")
        .map((cell) => cell.textContent),
    ).toEqual(["$343.36"]);
  });

  it("closes on Escape and gives focus back to the Compare button", async () => {
    stubTickerApi();
    const user = userEvent.setup();
    render(<TickerPage symbol="AAPL" />);

    const { button: trigger } = await openCompare(user);
    await user.keyboard("{Escape}");

    expect(screen.queryByRole("dialog")).toBeNull();
    await waitFor(() => expect(document.activeElement).toBe(trigger));
  });
});
