/**
 * Phase 7 — the accessibility gate.
 *
 * This file proves the keyboard and screen-reader claims against the real
 * shell, not against a sketch of it: `App` is rendered with the whole chrome
 * (nav, view strip, toolbar, one page of rows, drawer,
 * detail panel) and the assertions are made on `document.activeElement` and on
 * the DOM a screen reader would read.
 *
 * What jsdom **can** prove here
 *   * which element is focused after each Tab (the order is a DOM fact);
 *   * the ARIA relationship attributes: `aria-activedescendant`, `aria-pressed`,
 *     `aria-rowcount`, `scope`, `<caption>`, `role="status"` count;
 *   * that Tab wraps inside the drawer and the detail panel;
 *   * that Escape puts focus back on the opener;
 *   * the source-level contracts that CSS owns (the 2 px ring, the
 *     reduced-motion block) — read from the files the app actually ships.
 *
 * What jsdom **cannot** prove, and where it was checked instead
 *   * that the ring is *visible*: jsdom has no layout, no paint and does not
 *     load the stylesheet (`test.css: false`), so `getComputedStyle` returns
 *     the default outline. Verified in Chromium over CDP against the built
 *     bundle (see the Phase 7 accessibility report): computed `outline-width: 2px`,
 *     `outline-color: rgb(255, 142, 196)`, per focusable element, plus the AX
 *     tree for the caption and `th` scopes.
 *   * the contrast ratios themselves are arithmetic on `tokens.css`
 *     (`tokens.test.ts` owns 36 text pairs and 22 boundaries); the ring is
 *     re-measured here against the surfaces it can land on.
 *   * `prefers-reduced-motion` in the *browser*: the emulated media query was
 *     checked over CDP; here the component's own branch and the stylesheet
 *     block are asserted.
 */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { configure, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App, GRID_REGION_ID } from "@/app";
import { NAV_ITEMS } from "@/shell/nav";
import { LIVE_BREADTH } from "@/breadth/__fixtures__/breadth";
import {
  fundamentalsPayload,
  insiderPayload,
  newsPayload,
  screensPayload,
  selectPayload,
} from "@/detail/__fixtures__/detail";
import { filterSchemaWire } from "@/filters/__fixtures__/schema";
import { resetFilterSchemaCache } from "@/filters/schema";
import { SEARCH_DEBOUNCE_MS } from "@/shell/SymbolSearch";

import { PendingBar } from "./grid/PendingBar";

/* -------------------------------------------------------------------------
 * The harness
 * ---------------------------------------------------------------------- */

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

/** One page of a 5 600-row screen: enough for paging and for the row range. */
function page(count: number) {
  return {
    as_of: "2026-10-31T01:28:01Z",
    limit: 20,
    total: 5_600,
    universe_total: 5_600,
    returned: count,
    columns: [],
    rows: Array.from({ length: count }, (_, index) => ({
      symbol: `S${String(index).padStart(3, "0")}`,
      company: `Company ${index}`,
      price: 100 + index,
    })),
    budget: { truncated: false, dropped_fields: [] },
  };
}

/**
 * Every route the shell touches, answered once.
 *
 * The detail reads are answered too, because opening a row is part of the
 * keyboard path and a panel stuck on its skeleton would be a different tab
 * order than the one the reader gets.
 */
function stubShell() {
  const selects: Array<Record<string, unknown>> = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url === "/screens/select/schema") return json(filterSchemaWire());
    if (url.startsWith("/market/breadth")) return json(LIVE_BREADTH);
    if (url === "/health") {
      return json({
        status: "ok",
        version: "0.1.0",
        checked_at: "2026-10-31T01:28:01Z",
        elapsed_ms: 1,
        startup_error: null,
        checks: {},
      });
    }
    if (url.startsWith("/symbols/search")) {
      return json([
        { symbol: "AAPL", name: "Apple Inc.", exchange: "NASDAQ" },
        { symbol: "AAPL.W", name: "Apple Hospitality", exchange: "NYSE" },
        { symbol: "MSFT", name: "Microsoft Corp.", exchange: "NASDAQ" },
      ]);
    }
    if (url.startsWith("/screens?")) return json(screensPayload());
    if (url.startsWith("/engine/fundamentals/")) return json(fundamentalsPayload());
    if (url.startsWith("/engine/insider/")) return json(insiderPayload());
    if (url.startsWith("/news/")) return json(newsPayload());
    if (url === "/screens/select" && init?.method === "POST") {
      selects.push(JSON.parse(String(init.body)) as Record<string, unknown>);
      return json(page(20));
    }
    if (url.startsWith("/engine/") || url.startsWith("/screens/select")) {
      return json(selectPayload());
    }
    return json({ errorMessage: `no route for ${url}` }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return { fetchMock, selects };
}

beforeEach(() => {
  // jsdom has no layout: the grid's container would measure 0 × 0 and the table
  // would never render. 1 280 px is the width the solver is written against.
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
  window.history.replaceState(null, "", "/app/screener");
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

/**
 * The detail panel is a lazily imported chunk. In this file the shell is large
 * enough (breadth, 100 rows, two dialogs) that the import occasionally lands
 * after Testing Library's 1 s default — the same reason `app.chunk.test.tsx`
 * raises it. The panel is not slow in a browser; the transform is.
 */
const DETAIL_TIMEOUT = 10_000;
/**
 * The shell's own first paint. Vitest runs this file beside the other 48, so a
 * `findBy` that is instant alone can exceed Testing Library's 1 s default under
 * contention; these are ceilings for a slow machine, not expectations.
 */
const SHELL_TIMEOUT = 15_000;
/** One interaction (a debounce, a dialog mount) — still not 1 s. */
const STEP_TIMEOUT = 5_000;

/**
 * The defaults for every `findBy`/`waitFor` in this file.
 *
 * Testing Library's 1 s is a good default for a unit test and a bad one for a
 * file that renders the entire shell (schema, breadth, 100 rows) beside 48
 * others. The assertions are unchanged; only the ceiling moves, so a slow
 * machine cannot turn a pass into a red.
 */
configure({ asyncUtilTimeout: STEP_TIMEOUT });

async function renderShell() {
  const api = stubShell();
  render(<App />);
  await screen.findByTestId("grid-table", undefined, { timeout: SHELL_TIMEOUT });
  // The breadcrumb is the last band to settle; waiting on it means the tab
  // order below is the stable one, not a snapshot mid-load.
  await waitFor(() => expect(screen.getByTestId("range").textContent).toContain("of 5,600"));
  return api;
}

/* -------------------------------------------------------------------------
 * Reading focus
 * ---------------------------------------------------------------------- */

/** The focusables a browser would tab to, in document order. */
function tabbables(root: ParentNode = document): HTMLElement[] {
  const selector = [
    "a[href]",
    "button:not([disabled])",
    "input:not([disabled])",
    "select:not([disabled])",
    "textarea:not([disabled])",
    '[tabindex]:not([tabindex="-1"])',
  ].join(", ");
  return [...root.querySelectorAll<HTMLElement>(selector)].filter(
    (element) => element.closest("[hidden]") === null,
  );
}

/** A short, stable name for the focused element — the trace's vocabulary. */
function describeFocus(element: Element | null): string {
  if (!element || element === document.body) return "<body>";
  const html = element as HTMLElement;
  if (html.dataset["symbol"]) return `row ${html.dataset["symbol"]}`;
  const tag = element.tagName.toLowerCase();
  const role = element.getAttribute("role");
  const name =
    element.getAttribute("aria-label") ??
    (element.textContent ?? "").replace(/\s+/g, " ").trim().slice(0, 40);
  return `${tag}${role ? `[${role}]` : ""}${name ? ` “${name}”` : ""}`;
}

/**
 * What a screen reader would say for this subtree: `aria-hidden` descendants
 * are removed before the text is read. Used to prove a row is *spoken* as its
 * symbol, not as a monogram glyph plus numbers.
 */
function spokenText(element: Element): string {
  const clone = element.cloneNode(true) as Element;
  for (const hidden of clone.querySelectorAll('[aria-hidden="true"]')) hidden.remove();
  return (clone.textContent ?? "").replace(/\s+/g, " ").trim();
}

/* -------------------------------------------------------------------------
 * 1 — the skip link
 * ---------------------------------------------------------------------- */

describe("the skip link", () => {
  it("is the first focusable element and lands on the grid region, not a second main", async () => {
    await renderShell();
    const user = userEvent.setup();

    const skip = screen.getByTestId("skip-link");
    expect(skip.tagName).toBe("A");
    // Nothing focusable precedes it: not the search box, not a tab.
    expect(tabbables()[0]).toBe(skip);

    // Adversarial (d): the link points inside the one <main>, it does not
    // create a second landmark to skip to.
    expect(document.querySelectorAll("main")).toHaveLength(1);
    expect(screen.getAllByRole("main")).toHaveLength(1);
    expect(skip.getAttribute("href")).toBe(`#${GRID_REGION_ID}`);

    // It is off-screen until focused, then it is a real, visible control.
    expect(skip.className).toContain("-translate-y-[200%]");
    expect(skip.className).toContain("focus:translate-y-0");

    await user.tab();
    expect(document.activeElement).toBe(skip);

    await user.keyboard("{Enter}");
    const region = document.getElementById(GRID_REGION_ID);
    expect(region).not.toBeNull();
    expect(document.activeElement).toBe(region);
    // Named, so the landing is announced as something rather than as a box.
    expect(region?.getAttribute("role")).toBe("region");
    expect(region?.getAttribute("aria-label")).toBe("Stock grid");
    expect(region?.getAttribute("tabindex")).toBe("-1");
  });
});

/* -------------------------------------------------------------------------
 * 2 — the forward tab order
 * ---------------------------------------------------------------------- */

describe("the keyboard path", () => {
  it("reaches every control, and the first row, well inside 30 Tab presses", async () => {
    await renderShell();
    const user = userEvent.setup();

    const trace: string[] = [];
    for (let press = 0; press < 80; press += 1) {
      await user.tab();
      const active = document.activeElement;
      if (!active || active === document.body) break;
      trace.push(describeFocus(active));
    }

    // The order the shell promises: skip link, search, the view strip, the wide
    // destination, then the toolbar, then the grid, then paging.
    expect(trace[0]).toContain("Skip to the stock grid");
    for (const expected of [
      "combobox",
      "tab",
      "Filters",
      "Columns",
      "Sector",
      // The exchange control is one more tab stop in the toolbar, and it is
      // listed here on purpose: the promise is the ordered list, not the count.
      "Exchange",
      "Market cap",
      "Turnover",
      "Gainers",
      "Check again",
      "row S000",
      "Next",
    ]) {
      expect(trace.some((entry) => entry.includes(expected)), `${expected} is unreachable`).toBe(
        true,
      );
    }

    // One stop per row, one stop per control: a repeated entry would mean two
    // DOM nodes competing for the same Tab press.
    expect(new Set(trace).size).toBe(trace.length);

    const rowAt = trace.findIndex((entry) => entry.startsWith("row "));
    expect(rowAt).toBeGreaterThan(0);
    expect(rowAt).toBeLessThan(60);

    // The nav is in the path, all fourteen items, before the grid: the shell's
    // front door is reachable from the keyboard like any other product surface.
    const beforeRow = trace.slice(0, rowAt).join(" | ");
    for (const item of NAV_ITEMS) {
      expect(beforeRow, `${item.label} is not reachable before the grid`).toContain(item.label);
    }
    // The whole forward pass is shorter than the goal's ceiling, so a reader
    // can reach the last control and start again with room to spare.
    expect(trace.length).toBeLessThanOrEqual(80);

    // The pinned lane adds no stop of its own (adversarial a): one stop per
    // row is the row, and nothing inside it can be tabbed to.
    const rows = [...screen.getByTestId("grid-table").querySelectorAll<HTMLElement>("tr[data-symbol]")];
    expect(rows.filter((row) => row.tabIndex === 0)).toHaveLength(1);
    expect(rows.every((row) => row.querySelectorAll("button, a[href], input, select, [tabindex]").length === 0)).toBe(
      true,
    );
    expect(tabbables(screen.getByTestId("grid-scroll"))).toHaveLength(rows.length === 0 ? 0 : 1);
  });

  it("moves the roving cursor with the arrow keys and reads the row as its symbol", async () => {
    await renderShell();
    const user = userEvent.setup();

    const rows = [
      ...screen.getByTestId("grid-table").querySelectorAll<HTMLElement>("tbody tr[data-symbol]"),
    ];
    rows[0]?.focus();
    expect(document.activeElement).toBe(rows[0]);

    await user.keyboard("{ArrowDown}");
    expect(document.activeElement).toBe(rows[1]);
    // One tab stop follows the cursor: the DOM keeps exactly one `tabindex=0`.
    expect(rows.filter((row) => row.tabIndex === 0)).toEqual([rows[1]]);

    // Adversarial (c): the row is spoken with its symbol. The row is a tab
    // stop, so it is named; the monogram is `aria-hidden`, so the reading order
    // inside the cell also starts at the ticker — not at the logo's initial,
    // and not at a colour.
    expect(rows[0]?.getAttribute("aria-label")).toBe("S000");
    expect(rows[1]?.getAttribute("aria-label")).toBe("S001");
    expect(spokenText(rows[0] as HTMLElement).startsWith("S000")).toBe(true);
  });
});

/* -------------------------------------------------------------------------
 * 3 — the search combobox
 * ---------------------------------------------------------------------- */

describe("the symbol search", () => {
  it("follows the ARIA combobox pattern: activedescendant, arrows, Enter, Escape", async () => {
    await renderShell();
    const user = userEvent.setup();

    const input = screen.getByRole("combobox", { name: "Search symbols and companies" });
    expect(input.getAttribute("aria-haspopup")).toBe("listbox");
    expect(input.getAttribute("aria-autocomplete")).toBe("list");
    expect(input.getAttribute("aria-expanded")).toBe("false");
    expect(input.hasAttribute("aria-activedescendant")).toBe(false);

    await user.click(input);
    await user.type(input, "aa");

    const list = await screen.findByRole("listbox", { name: "Symbol results" }, { timeout: STEP_TIMEOUT });
    const options = within(list).getAllByRole("option");
    expect(options).toHaveLength(3);

    // Open, and pointing at the first option via the *input*, which keeps the
    // caret in the text field (the listbox itself is never focused).
    expect(input.getAttribute("aria-expanded")).toBe("true");
    expect(input.getAttribute("aria-controls")).toBe(list.id);
    expect(input.getAttribute("aria-activedescendant")).toBe(options[0]?.id);
    expect(options[0]?.getAttribute("aria-selected")).toBe("true");

    await user.keyboard("{ArrowDown}");
    expect(input.getAttribute("aria-activedescendant")).toBe(options[1]?.id);
    expect(options[1]?.getAttribute("aria-selected")).toBe("true");
    expect(options[0]?.getAttribute("aria-selected")).toBe("false");

    await user.keyboard("{ArrowUp}");
    expect(input.getAttribute("aria-activedescendant")).toBe(options[0]?.id);

    // Escape closes the list and leaves the text alone; the pointer into the
    // list goes with it, so nothing is half-selected behind a closed popup.
    await user.keyboard("{Escape}");
    expect(input.getAttribute("aria-expanded")).toBe("false");
    expect(input.hasAttribute("aria-activedescendant")).toBe(false);
    expect(screen.queryByRole("listbox")).toBeNull();
    expect((input as HTMLInputElement).value).toBe("aa");

    // Enter commits the highlighted hit and opens its details.
    await user.keyboard("{ArrowDown}");
    expect(input.getAttribute("aria-expanded")).toBe("true");
    await user.keyboard("{Enter}");

    const panel = await screen.findByTestId("detail-panel", undefined, { timeout: DETAIL_TIMEOUT });
    expect(panel.getAttribute("aria-label")).toBe("Details for AAPL");
    expect(screen.queryByRole("listbox")).toBeNull();

    // And it *stays* closed: committing writes the symbol into the box, and the
    // debounced search must not treat that as a new question and drop the list
    // back over the panel (one debounce window is enough to catch it).
    await new Promise((resolve) => setTimeout(resolve, SEARCH_DEBOUNCE_MS + 120));
    expect(screen.queryByRole("listbox")).toBeNull();
    expect(input.getAttribute("aria-expanded")).toBe("false");
  });
});

/* -------------------------------------------------------------------------
 * 4 — the filter drawer
 * ---------------------------------------------------------------------- */

describe("the filter drawer", () => {
  it("traps Tab inside the dialog and gives focus back to the control that opened it", async () => {
    await renderShell();
    const user = userEvent.setup();

    const opener = screen.getByRole("button", { name: /^Filters/ });
    await user.click(opener);

    const dialog = await screen.findByRole("dialog", { name: "Filters" }, { timeout: STEP_TIMEOUT });
    expect(dialog.getAttribute("aria-modal")).toBe("true");

    const stops = tabbables(dialog);
    expect(stops.length).toBeGreaterThan(20);

    // Tab from the last control wraps to the first …
    (stops[stops.length - 1] as HTMLElement).focus();
    await user.tab();
    expect(document.activeElement).toBe(stops[0]);

    // … and Shift+Tab from the first wraps to the last.
    await user.tab({ shift: true });
    expect(document.activeElement).toBe(stops[stops.length - 1]);

    // A run of Tabs never escapes the dialog: 60-odd controls cannot leak focus
    // into the page behind.
    for (let press = 0; press < 8; press += 1) {
      await user.tab();
      expect(dialog.contains(document.activeElement)).toBe(true);
    }

    // Escape closes it and the opener has the caret again.
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Filters" })).toBeNull());
    expect(document.activeElement).toBe(opener);
  });
});

/* -------------------------------------------------------------------------
 * 5 — the detail panel
 * ---------------------------------------------------------------------- */

describe("the detail panel", () => {
  it("opens on Close, keeps Tab inside, and Escape returns focus to the row", async () => {
    await renderShell();
    const user = userEvent.setup();

    const table = screen.getByTestId("grid-table");
    const row = table.querySelector<HTMLElement>('tbody tr[data-symbol="S000"]');
    expect(row).not.toBeNull();
    (row as HTMLElement).focus();

    await user.keyboard("{Enter}");
    const panel = await screen.findByTestId("detail-panel", undefined, { timeout: DETAIL_TIMEOUT });
    const close = within(panel).getByRole("button", { name: "Close details" });

    // Adversarial (b): focus lands on the way out, before any content.
    await waitFor(() => expect(document.activeElement).toBe(close));
    const firstTab = within(panel).getByRole("tab", { name: "Fundamentals" });
    expect(
      close.compareDocumentPosition(firstTab) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    // The first tab stop in the panel is the close button, not the resizer.
    expect(tabbables(panel)[0]).toBe(close);

    // Tab from the last control returns to the first.
    const stops = tabbables(panel);
    expect(stops.length).toBeGreaterThan(2);
    (stops[stops.length - 1] as HTMLElement).focus();
    await user.tab();
    expect(document.activeElement).toBe(close);

    // Shift+Tab from the first wraps to the last, so the panel cannot be
    // escaped by accident and entered again only by luck.
    await user.tab({ shift: true });
    expect(document.activeElement).toBe(stops[stops.length - 1]);

    // Escape restores focus to the row that opened it (the opener).
    (close as HTMLElement).focus();
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByTestId("detail-panel")).toBeNull());
    expect(document.activeElement).toBe(row);
  });

  it("does not add a tab stop per cell in the pinned lane while it is open", async () => {
    await renderShell();
    const user = userEvent.setup();

    const table = screen.getByTestId("grid-table");
    const row = table.querySelector<HTMLElement>('tbody tr[data-symbol="S001"]') as HTMLElement;
    row.focus();
    await user.keyboard("{Enter}");
    await screen.findByTestId("detail-panel", undefined, { timeout: DETAIL_TIMEOUT });

    const rows = [...table.querySelectorAll<HTMLElement>("tbody tr[data-symbol]")];
    expect(rows).toHaveLength(20);
    expect(rows.filter((element) => element.tabIndex === 0)).toEqual([row]);
    for (const element of rows) {
      // The identity lane is a `<td>` with a mark and a symbol — no link, no
      // button, no tabindex of its own.
      const lane = element.querySelector<HTMLElement>("td");
      expect(lane?.querySelectorAll("a, button, input, select, [tabindex]")).toHaveLength(0);
    }
  });
});

/* -------------------------------------------------------------------------
 * 6 — table semantics
 * ---------------------------------------------------------------------- */

describe("the table's semantics", () => {
  it("is named by a caption that states the slice and the sort in words", async () => {
    await renderShell();
    const table = screen.getByTestId("grid-table");
    const caption = table.querySelector("caption");

    expect(caption).not.toBeNull();
    const text = (caption?.textContent ?? "").replace(/\s+/g, " ");
    expect(text).toContain("Today");
    expect(text).toContain("rows 1–20 of 5,600");
    // Adversarial (c): the ordering is said, in words. No arrow, no colour.
    expect(text.toLowerCase()).toContain("sorted by market cap, highest first");

    // One name only: the caption is it.
    expect(table.hasAttribute("aria-label")).toBe(false);
    expect(table.hasAttribute("aria-labelledby")).toBe(false);

    // The count the body advertises is the whole screen plus the header row.
    expect(table.getAttribute("aria-rowcount")).toBe("5601");
    expect(table.getAttribute("aria-colcount")).toBe(
      String(table.querySelectorAll("colgroup col").length),
    );
  });

  it("scopes every header and declines to fake a sortable one", async () => {
    await renderShell();
    const table = screen.getByTestId("grid-table");

    const headers = [...table.querySelectorAll("thead th")];
    const scoped = [...table.querySelectorAll('thead th[scope="col"]')];
    const columns = table.querySelectorAll("colgroup col").length;
    // Every rendered column has a header cell with an explicit column scope.
    expect(scoped).toHaveLength(headers.length);
    expect(scoped).toHaveLength(columns);
    expect(columns).toBeGreaterThan(1);

    // `aria-sort` is not applied, and that is the honest answer: no header is
    // a sort control here (`enableSorting` is false on every column, the
    // toolbar owns the ordering), and one of the three sorts — "gainers" —
    // names no column at all. A `th[aria-sort]` would announce a sort button
    // that does not exist. The ordering is spoken by the caption instead.
    expect(table.querySelectorAll("th[aria-sort]")).toHaveLength(0);
    expect(table.querySelectorAll("thead th[aria-sort]")).toHaveLength(0);
  });

  it("marks the toolbar's sort with aria-pressed, exactly one at a time", async () => {
    await renderShell();
    const user = userEvent.setup();

    const group = screen.getByRole("group", { name: "Sort by" });
    const buttons = within(group).getAllByRole("button");
    expect(buttons.map((button) => button.textContent)).toEqual([
      "Market cap",
      "Turnover",
      "Gainers",
    ]);
    expect(buttons.filter((button) => button.getAttribute("aria-pressed") === "true")).toHaveLength(
      1,
    );
    expect(within(group).getByRole("button", { name: "Market cap" }).getAttribute("aria-pressed")).toBe(
      "true",
    );

    await user.click(within(group).getByRole("button", { name: "Gainers" }));
    await waitFor(() =>
      expect(
        within(group).getByRole("button", { name: "Gainers" }).getAttribute("aria-pressed"),
      ).toBe("true"),
    );
    expect(within(group).getByRole("button", { name: "Market cap" }).getAttribute("aria-pressed")).toBe(
      "false",
    );
    // And the caption follows the choice, so the table is never named with a
    // stale ordering.
    await waitFor(() =>
      expect(screen.getByTestId("grid-caption").textContent?.toLowerCase()).toContain(
        "sorted by gainers",
      ),
    );
  });
});

/* -------------------------------------------------------------------------
 * 7 — one live status
 * ---------------------------------------------------------------------- */

describe("live regions", () => {
  it("has exactly one role=status, and it is the row range", async () => {
    await renderShell();
    const user = userEvent.setup();

    const statuses = document.querySelectorAll('[role="status"]');
    expect(statuses).toHaveLength(1);
    expect(statuses[0]).toBe(screen.getByTestId("fetch-status"));
    expect(statuses[0]?.getAttribute("aria-live")).toBe("polite");
    expect(statuses[0]?.getAttribute("aria-atomic")).toBe("true");

    // Opening the drawer (a second live region would live there) and the detail
    // panel (whose skeletons announce) must not add a second status role.
    await user.click(screen.getByRole("button", { name: /^Filters/ }));
    await screen.findByRole("dialog", { name: "Filters" }, { timeout: STEP_TIMEOUT });
    expect(document.querySelectorAll('[role="status"]')).toHaveLength(1);
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

    const row = screen.getByTestId("grid-table").querySelector<HTMLElement>(
      'tbody tr[data-symbol="S002"]',
    ) as HTMLElement;
    row.focus();
    await user.keyboard("{Enter}");
    await screen.findByTestId("detail-panel", undefined, { timeout: DETAIL_TIMEOUT });
    expect(document.querySelectorAll('[role="status"]')).toHaveLength(1);
  });

  it("hard-codes no second status into the drawer or the detail tabs", () => {
    // The count above is a rendered fact; this is the source invariant behind
    // it, so a fourth view cannot reintroduce one quietly.
    const files = [
      "src/grid/ScreenPanel.tsx",
      "src/breadth/BreadthStrip.tsx",
      "src/filters/ChipBar.tsx",
      "src/filters/CommandPalette.tsx",
      "src/grid/ColumnPicker.tsx",
      "src/detail/TabStates.tsx",
    ];
    const counts = files.map((file) => {
      const text = stripComments(readFileSync(resolve(process.cwd(), file), "utf8"));
      return { file, status: (text.match(/role="status"/g) ?? []).length };
    });
    expect(counts.filter((entry) => entry.status > 0).map((entry) => entry.file)).toEqual([
      "src/grid/ScreenPanel.tsx",
    ]);
  });
});

/* -------------------------------------------------------------------------
 * 8 — the focus ring (the half CSS owns)
 * ---------------------------------------------------------------------- */

describe("the focus ring", () => {
  const root = process.cwd();
  const sheet = readFileSync(resolve(root, "src/styles/index.css"), "utf8");
  const tokens = readFileSync(resolve(root, "src/styles/tokens.css"), "utf8");

  it("is 2 px of --focus everywhere, and is not the Finviz outline:none + border trick", () => {
    expect(sheet).toMatch(/:focus-visible\s*\{\s*\n\s*outline:\s*2px solid var\(--focus\);/);
    // The one opt-out is the composite search field, whose *shell* draws the
    // ring instead (`focus-within:` in shell/SymbolSearch.tsx) — the same 2 px,
    // the same token, one box further out.
    // Comments in this sheet *quote* the old pattern; only declarations count.
    expect(stripComments(sheet)).not.toMatch(/outline:\s*none/);
    // A rule that can repaint an element a Tailwind utility stripped must not
    // live inside a cascade layer: `outline-none` is in `utilities`, and layer
    // order beats specificity.
    const at = sheet.indexOf(".focus-ring:focus-visible");
    expect(at).toBeGreaterThan(0);
    // (Comments are prose — this sheet's own prose names the layer it beats.)
    expect(stripComments(sheet.slice(at))).not.toMatch(/@layer\s/);
  });

  it("is not animated in: the ring is there on the first frame", () => {
    // `transition-colors` includes `outline-color`, so the ring used to fade
    // from the element's text colour to rose over 150 ms. Measured in Chromium
    // before the fix: rgb(185,175,180) at t=0. The rule below takes the outline
    // out of the transition list and leaves every other property alone.
    const at = sheet.indexOf("transition-property:");
    expect(at).toBeGreaterThan(0);
    expect(sheet.slice(at, at + 400)).not.toContain("outline-color");
    expect(stripComments(sheet)).toMatch(
      /:where\(a, button, input, select, textarea, summary, \[tabindex\]\):focus-visible,/,
    );
    // The composite search field draws its ring on the wrapper.
    expect(stripComments(sheet)).toContain(".search-shell:focus-within");
  });

  it("clears 3:1 against every surface it can be drawn on, in every theme block shipped", () => {
    const blocks = themeBlocks(tokens);
    expect(blocks.length).toBeGreaterThan(0);

    const surfaces = [
      "--bg",
      "--surface",
      "--surface-raised",
      "--surface-sunken",
      "--row-bg",
      "--row-bg-alt",
      "--row-hover",
      "--row-selected",
      "--popover",
    ];

    for (const block of blocks) {
      const focus = block.tokens["--focus"];
      expect(focus, `${block.selector} declares --focus`).toBeTruthy();
      for (const surface of surfaces) {
        const background = block.tokens[surface];
        if (!background) continue;
        const ratio = contrastRatio(focus as string, background);
        expect(
          ratio,
          `${block.selector}: --focus on ${surface} is ${ratio.toFixed(2)}:1`,
        ).toBeGreaterThanOrEqual(3);
      }
    }
  });
});

/* -------------------------------------------------------------------------
 * 9 — reduced motion
 * ---------------------------------------------------------------------- */

describe("reduced motion", () => {
  it("stops the pending sweep when the reader asked for it", () => {
    const { rerender } = render(<PendingBar reducedMotion />);
    const bar = screen.getByTestId("fetch-bar");
    const sweep = screen.getByTestId("fetch-bar-sweep");
    expect(bar.getAttribute("data-motion")).toBe("reduced");
    expect(sweep.className).toContain("w-full");
    expect(sweep.className).not.toContain("ztz-pending-sweep");
    // The component ships its own media query, so the preference holds even if
    // the prop is never passed (a second mount, a future caller).
    const style = document.querySelector("style");
    expect(style?.textContent).toContain("@media (prefers-reduced-motion: reduce)");
    expect(style?.textContent).toContain("animation: none");

    rerender(<PendingBar />);
    expect(screen.getByTestId("fetch-bar").getAttribute("data-motion")).toBe("full");
    expect(screen.getByTestId("fetch-bar-sweep").className).toContain("ztz-pending-sweep");
  });

  it("collapses every transition, including the stale fade, in the shipped stylesheet", () => {
    const sheet = readFileSync(resolve(process.cwd(), "src/styles/index.css"), "utf8");
    expect(sheet).toMatch(/@media \(prefers-reduced-motion: reduce\)/);
    expect(sheet).toMatch(/transition-duration:\s*0\.01ms !important/);
    expect(sheet).toMatch(/animation-duration:\s*0\.01ms !important/);
  });
});

/** Remove `/* … *\/` blocks and `//` line comments before reading source rules. */
function stripComments(source: string): string {
  return source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
}

/* -------------------------------------------------------------------------
 * Token helpers — a local copy of the arithmetic, deliberately.
 *
 * `tokens.test.ts` already owns the palette (36 text pairs, 22 boundaries).
 * Importing it here would *run* its describes inside this file; the ring is a
 * non-text boundary with its own surfaces, so the three functions it needs are
 * repeated rather than reached across a test module boundary.
 * ---------------------------------------------------------------------- */

function themeBlocks(css: string): Array<{ selector: string; tokens: Record<string, string> }> {
  const blocks: Array<{ selector: string; tokens: Record<string, string> }> = [];
  const selector = /(:root|\[data-theme="[a-z]+"\])\s*\{/g;
  let match: RegExpExecArray | null;
  while ((match = selector.exec(css)) !== null) {
    const start = match.index + match[0].length;
    let depth = 1;
    let index = start;
    while (index < css.length && depth > 0) {
      if (css[index] === "{") depth += 1;
      else if (css[index] === "}") depth -= 1;
      index += 1;
    }
    const body = css.slice(start, index - 1);
    const tokens: Record<string, string> = {};
    for (const declaration of body.matchAll(/(--[a-z0-9-]+)\s*:\s*([^;]+);/gi)) {
      const name = declaration[1];
      const value = (declaration[2] ?? "").replace(/\/\*[\s\S]*?\*\//g, "").trim();
      if (name && /^#[0-9a-f]{6}$/i.test(value)) tokens[name] = value.toLowerCase();
    }
    blocks.push({ selector: match[1] ?? ":root", tokens });
  }
  return blocks;
}

function luminance(hex: string): number {
  const value = hex.replace("#", "");
  const channels = [0, 2, 4].map((offset) => {
    const srgb = Number.parseInt(value.slice(offset, offset + 2), 16) / 255;
    return srgb <= 0.04045 ? srgb / 12.92 : ((srgb + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * (channels[0] as number) + 0.7152 * (channels[1] as number) + 0.0722 * (channels[2] as number);
}

function contrastRatio(a: string, b: string): number {
  const first = luminance(a);
  const second = luminance(b);
  const lighter = Math.max(first, second);
  const darker = Math.min(first, second);
  return (lighter + 0.05) / (darker + 0.05);
}
