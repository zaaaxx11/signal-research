import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { ScreenRow, ViewDefinition } from "@/data/screens";

import { TODAY_COLUMNS, TODAY_ROWS, TODAY_TOTAL } from "./__fixtures__/today100";
import { VIEW_FIXTURE } from "./__fixtures__/views";
import { columnSpec, headerNeed } from "./columnMeta";
import { DataTable, STICKY_HEADER, STICKY_LANE, type DataTableProps, uniqueRowIds } from "./DataTable";
import { solveView } from "./solver";

/**
 * The grid's goal tests, at the DOM level:
 *
 * * 100 real rows from a captured `POST /screens/select` answer;
 * * widths that sum to the container in all nine views (no horizontal scroll);
 * * the sticky contract, which jsdom can only assert as CSS;
 * * absent vs zero;
 * * a long company name that stays out of the 101 px lane;
 * * rows identified by symbol, not position;
 * * a keyboard path: Tab, Enter, Arrow, Escape.
 */

const TODAY_VIEW: ViewDefinition = {
  name: "today",
  label: "Today",
  job: "What is moving right now?",
  columns: [...TODAY_COLUMNS],
};

function renderGrid(overrides: Partial<DataTableProps> = {}) {
  const props: DataTableProps = {
    view: TODAY_VIEW,
    rows: TODAY_ROWS,
    status: "ready",
    error: null,
    revalidated: false,
    pageIndex: 0,
    pageSize: 100,
    rowCount: TODAY_TOTAL,
    onRetry: vi.fn(),
    containerWidth: 1280,
    ...overrides,
  };
  return { ...render(<DataTable {...props} />), props };
}

function columnWidths(table: HTMLElement): number[] {
  return [...table.querySelectorAll("colgroup col")].map((col) =>
    Number.parseFloat((col as HTMLElement).style.width || "0"),
  );
}

describe("a real 100-row answer", () => {
  it("renders every row and every column", () => {
    renderGrid();
    const table = screen.getByTestId("grid-table");

    const rows = table.querySelectorAll("tbody tr[data-symbol]");
    expect(TODAY_ROWS).toHaveLength(100);
    expect(rows).toHaveLength(100);

    expect(table.querySelectorAll("thead th")).toHaveLength(TODAY_COLUMNS.length + 1);
    expect(table.querySelector("tbody tr[data-symbol]")?.querySelectorAll("td")).toHaveLength(
      TODAY_COLUMNS.length + 1,
    );

    expect(rows[0]?.getAttribute("data-symbol")).toBe("AAPL");
    expect(rows[99]?.getAttribute("data-symbol")).toBe("SHOP");
    expect(rows[0]?.textContent).toContain("$344.63");
  });

  it("uses symbols as row ids, so a reordered page still merges by symbol", () => {
    const shuffled: ScreenRow[] = [
      { symbol: "ZZZ", price: 9.5, as_of: "2026-10-31T00:00:00Z" },
      { symbol: "AAA", price: 1.25, as_of: "2026-10-31T00:00:00Z" },
      { symbol: "MMM", price: 3.75, as_of: "2026-10-31T00:00:00Z" },
    ];
    const view: ViewDefinition = { ...TODAY_VIEW, columns: ["price"] };
    renderGrid({ view, rows: shuffled, rowCount: 3 });

    const rows = screen.getByTestId("grid-table").querySelectorAll("tbody tr[data-symbol]");
    expect([...rows].map((row) => row.getAttribute("data-symbol"))).toEqual(["ZZZ", "AAA", "MMM"]);
    expect([...rows].map((row) => row.getAttribute("data-row-id"))).toEqual(["ZZZ", "AAA", "MMM"]);
    // Each row shows its own price, not the price of the row at that index in
    // some client-side index map.
    expect(rows[0]?.textContent).toContain("$9.50");
    expect(rows[1]?.textContent).toContain("$1.25");
    expect(rows[2]?.textContent).toContain("$3.75");
  });
});

describe("container fit", () => {
  it.each(VIEW_FIXTURE.map((view) => [view.name, view] as const))(
    "%s: table width equals the container and the columns sum to it",
    (_name, view) => {
      renderGrid({ view, rows: [], status: "ready", rowCount: 0 });
      const table = screen.getByTestId("grid-table");
      const declared = Number.parseFloat(table.style.width);

      expect(table.style.tableLayout).toBe("fixed");
      expect(declared).toBe(1280);
      expect(declared).toBeLessThanOrEqual(1280);

      const widths = columnWidths(table);
      expect(widths).toHaveLength(view.columns.length + 1);
      expect(widths.reduce((a, b) => a + b, 0)).toBe(1280);
      expect(widths[0]).toBe(101);
    },
  );

  it("stays inside a 1920 px container too", () => {
    renderGrid({ containerWidth: 1920 });
    const table = screen.getByTestId("grid-table");
    expect(Number.parseFloat(table.style.width)).toBe(1920);
    expect(columnWidths(table).reduce((a, b) => a + b, 0)).toBe(1920);
  });
});

describe("the sticky contract", () => {
  it("pins the header to the top and the identity lane to the left", () => {
    renderGrid();
    const table = screen.getByTestId("grid-table");
    const headers = table.querySelectorAll<HTMLElement>("thead th");
    const firstRow = table.querySelector<HTMLElement>("tbody tr[data-symbol]");
    const cells = firstRow?.querySelectorAll<HTMLElement>("td") ?? [];

    expect(STICKY_HEADER).toEqual({ position: "sticky", top: 0 });
    expect(STICKY_LANE).toEqual({ position: "sticky", left: 0 });

    expect(headers[0]?.style.position).toBe("sticky");
    expect(headers[0]?.style.top).toBe("0px");
    expect(headers[0]?.style.left).toBe("0px");

    expect(cells[0]?.style.position).toBe("sticky");
    expect(cells[0]?.style.left).toBe("0px");
    expect(cells[0]?.className).toContain("bg-inherit");

    // Only the lane is pinned horizontally: a second pinned column would eat
    // the panel at 1280 px.
    expect(headers[1]?.style.left).toBe("");
    expect(cells[1]?.style.left).toBe("");
  });

  it("renders the header at 24 px and rows at 24 px (compact density)", () => {
    renderGrid();
    const table = screen.getByTestId("grid-table");
    const header = table.querySelector<HTMLElement>("thead th");
    const row = table.querySelector<HTMLElement>("tbody tr[data-symbol]");

    expect(header?.style.height).toBe("24px");
    expect(row?.style.height).toBe("24px");
    expect(row?.className).not.toContain("h-7");
  });
});

describe("absent vs zero", () => {
  it("renders a null as the placeholder and a real zero as zero", () => {
    const view: ViewDefinition = { ...TODAY_VIEW, columns: ["open", "high"] };
    const rows: ScreenRow[] = [
      { symbol: "NULL", open: null, high: 0, as_of: "2026-10-31T00:00:00Z" },
    ];
    renderGrid({ view, rows, rowCount: 1 });

    const cells = screen.getByTestId("grid-table").querySelectorAll("tbody tr[data-symbol] td");
    expect(cells[1]?.textContent).toBe("—");
    expect(cells[1]?.querySelector("[data-null=true]")).not.toBeNull();
    expect(cells[2]?.textContent).toBe("$0.00");
    expect(cells[2]?.querySelector("[data-null=true]")).toBeNull();
  });

  it("renders an absent field (not merely null) as the placeholder", () => {
    const view: ViewDefinition = { ...TODAY_VIEW, columns: ["low", "price"] };
    // `low` is not in the row at all: the store never filled it.
    const rows: ScreenRow[] = [{ symbol: "MISSING", price: 12.5 }];
    renderGrid({ view, rows, rowCount: 1 });

    const cells = screen.getByTestId("grid-table").querySelectorAll("tbody tr[data-symbol] td");
    expect(cells[1]?.textContent).toBe("—");
    expect(cells[2]?.textContent).toBe("$12.50");
  });
});

describe("the identity lane survives a long company name", () => {
  it("keeps the lane at 101 px and the name out of the flow", () => {
    const longName = "The ".repeat(40) + "Very Long Company Name Incorporated Holdings Limited";
    const view: ViewDefinition = { ...TODAY_VIEW, columns: ["company"] };
    const rows: ScreenRow[] = [{ symbol: "LONGSYM", company: longName, price: 1 }];
    renderGrid({ view, rows, rowCount: 1 });

    const table = screen.getByTestId("grid-table");
    expect(columnWidths(table)[0]).toBe(101);

    const lane = table.querySelector<HTMLElement>("tbody tr[data-symbol] td");
    const visibleSymbol = lane?.querySelector(".truncate");
    expect(visibleSymbol?.textContent).toBe("LONGSYM");
    expect(lane?.querySelector("span[title]")?.getAttribute("title")).toContain("Very Long Company");
    // The full name is present for screen readers only, in a clipped span that
    // cannot participate in the lane's layout.
    const spoken = lane?.querySelector(".sr-only");
    expect(spoken?.textContent).toContain("Very Long Company");
    expect(visibleSymbol?.textContent).not.toContain("Very Long Company");

    // The company column exists, has a fixed width, and carries the full name
    // in a title rather than stretching the table.
    const company = table.querySelectorAll<HTMLElement>("tbody tr[data-symbol] td")[1];
    expect(company?.textContent).toBe(longName);
    expect(company?.querySelector("span[title]")?.getAttribute("title")).toBe(longName);
    expect(Number.parseFloat(table.style.width)).toBe(1280);
  });
});

describe("states", () => {
  it("shows the API's own message on 422, not an empty table", () => {
    renderGrid({
      rows: [],
      status: "error",
      error: { status: 422, message: "unknown sort 'bogus'; expected one of market_cap, turnover, gainers" },
    });
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toContain("422");
    expect(alert.textContent).toContain("unknown sort 'bogus'");
    expect(alert.textContent).toContain("Retry");
  });

  it("keeps the rows and still shows the alert when a refresh fails", () => {
    renderGrid({
      status: "error",
      error: { status: 503, message: "the scan is unavailable" },
    });
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toContain("503");
    expect(alert.textContent).toContain("the scan is unavailable");
    // Stale rows stay visible; the reader is told they may be old.
    expect(screen.getByTestId("grid-table").querySelectorAll("tbody tr[data-symbol]")).toHaveLength(
      100,
    );
  });

  it("renders a labelled empty state", () => {
    renderGrid({ rows: [], status: "empty", rowCount: 0 });
    // The empty screen names the cause (filters) and offers the way out.
    expect(screen.getByText("No stocks match")).toBeTruthy();
    expect(screen.getByText(/Try removing filters/)).toBeTruthy();
    // Without a clear handler there is no button — words only, no dead control.
    expect(screen.queryByTestId("grid-clear-filters")).toBeNull();
  });

  it("the empty state's CTA clears the filters when the panel can", async () => {
    const user = userEvent.setup();
    const onClearFilters = vi.fn();
    renderGrid({ rows: [], status: "empty", rowCount: 0, onClearFilters });
    await user.click(screen.getByTestId("grid-clear-filters"));
    expect(onClearFilters).toHaveBeenCalledTimes(1);
  });

  it("a page that came back empty of a full screen is a page problem, not a filter problem", () => {
    renderGrid({ rows: [], status: "ready", rowCount: 5_600 });
    expect(screen.getByText("No rows on this page")).toBeTruthy();
    expect(screen.queryByText("No stocks match")).toBeNull();
    expect(screen.queryByTestId("grid-clear-filters")).toBeNull();
  });

  it("renders a skeleton while the first page loads", () => {
    renderGrid({ rows: [], status: "loading" });
    const table = screen.getByTestId("grid-table");
    expect(table.querySelectorAll("tbody tr[data-symbol]")).toHaveLength(0);
    expect(table.querySelectorAll("tbody tr[aria-hidden=true]")).toHaveLength(12);
    expect(screen.getByTestId("grid-scroll").getAttribute("aria-busy")).toBe("true");
  });

  it("announces a 304 without clearing the rows", () => {
    renderGrid({ revalidated: true });
    expect(screen.getByTestId("grid-table").querySelectorAll("tbody tr[data-symbol]")).toHaveLength(
      100,
    );
    expect(screen.getByTestId("revalidated-announcer")).toBeTruthy();
  });
});

describe("keyboard", () => {
  it("reaches a row with Tab, activates with Enter, moves with arrows, clears with Escape", async () => {
    const onActivate = vi.fn();
    const onClear = vi.fn();
    const user = userEvent.setup();
    renderGrid({ onActivate, onClear });

    const rows = screen.getByTestId("grid-table").querySelectorAll<HTMLElement>("tbody tr[data-symbol]");
    expect(rows[0]?.getAttribute("tabindex")).toBe("0");
    expect(rows[1]?.getAttribute("tabindex")).toBe("-1");

    await user.tab();
    expect(document.activeElement).toBe(rows[0]);

    await user.keyboard("{Enter}");
    expect(onActivate).toHaveBeenCalledWith("AAPL");

    await user.keyboard("{ArrowDown}");
    expect(document.activeElement).toBe(rows[1]);

    await user.keyboard("{Enter}");
    expect(onActivate).toHaveBeenLastCalledWith(rows[1]?.getAttribute("data-symbol"));

    await user.keyboard("{Escape}");
    expect(onClear).toHaveBeenCalledTimes(1);
    expect(rows[1]?.getAttribute("tabindex")).toBe("-1");
  });

  it("keeps a selected row visible to assistive tech", () => {
    const view: ViewDefinition = { ...TODAY_VIEW, columns: ["price"] };
    renderGrid({ view, selectedSymbol: "NVDA", rows: TODAY_ROWS.slice(0, 3), rowCount: 3 });
    const selected = screen.getByTestId("grid-table").querySelector("tbody tr[data-selected=true]");
    expect(selected?.getAttribute("data-symbol")).toBe("NVDA");
    // F3: `aria-selected` on a `<tr>` inside `role=table` is dropped from the
    // accessibility tree (Chrome exposes no `selected` property on the row);
    // `aria-current` is valid there and survives.
    expect(selected?.getAttribute("aria-current")).toBe("true");
    expect(selected?.hasAttribute("aria-selected")).toBe(false);
  });
});

/**
 * F3 — the open row must be announced, and it must be the only thing claiming
 * `aria-current`. A Chromium AX dump showed the selected `<tr>` carrying
 * `aria-selected="true"` and *no* `selected` property: the table's role is
 * `table`, where the attribute is not mapped. `aria-current` is valid on any
 * element inside a table and lands on the row node.
 */
describe("the selected row is announced (F3)", () => {
  it("puts aria-current on the role=table row, and nowhere else", () => {
    const view: ViewDefinition = { ...TODAY_VIEW, columns: ["price"] };
    renderGrid({ view, selectedSymbol: "NVDA", rows: TODAY_ROWS.slice(0, 3), rowCount: 3 });

    const table = screen.getByTestId("grid-table");
    expect(table.getAttribute("role")).toBeNull(); // implicit role=table
    const rows = screen.getAllByRole("row");
    const selected = rows.filter((row) => row.getAttribute("aria-current") === "true");
    expect(selected).toHaveLength(1);
    expect(selected[0]?.getAttribute("data-symbol")).toBe("NVDA");
    // The table is named by its `<caption>`, not by `aria-label`: one name, in
    // the element HTML reserves for it.
    expect(table.querySelector("caption")?.textContent).toContain("Today");
    expect(table.hasAttribute("aria-label")).toBe(false);

    // One current item per document: the tab strip marks itself with
    // `aria-selected` (role=tab), so a row cursor cannot be mistaken for it.
    expect(document.querySelectorAll("[aria-current]")).toHaveLength(1);
    expect(document.querySelector("[aria-current]")?.getAttribute("data-symbol")).toBe("NVDA");
  });

  it("keeps the attribute off every other row", () => {
    const view: ViewDefinition = { ...TODAY_VIEW, columns: ["price"] };
    renderGrid({ view, selectedSymbol: "AAPL", rows: TODAY_ROWS.slice(0, 3), rowCount: 3 });

    const rows = screen.getAllByRole("row").filter((row) => row.hasAttribute("data-symbol"));
    expect(rows).toHaveLength(3);
    expect(rows.map((row) => row.getAttribute("aria-current"))).toEqual(["true", null, null]);
  });
});

/**
 * F7 — `aria-rowcount` must count the whole screen (5,600 rows), not the page
 * (100), and must include the header row. 101 on a 5,600-row screen told a
 * screen-reader user "row 5 of 101".
 */
describe("the announced row count (F7)", () => {
  it("reports rowCount + 1, not the page length + 1", () => {
    renderGrid({ rowCount: 5600 });
    expect(TODAY_ROWS).toHaveLength(100);
    expect(screen.getByTestId("grid-table").getAttribute("aria-rowcount")).toBe("5601");
  });

  it("never under-reports the rows it is actually showing", () => {
    // A body that claims fewer rows than it renders is worse than a stale total.
    renderGrid({ rowCount: 3 });
    expect(screen.getByTestId("grid-table").getAttribute("aria-rowcount")).toBe("101");
  });
});

/**
 * F6 — at 1024 px the 13-column Value panel used to compress columns below
 * their declared minimums and clip 10 of 1,300 numbers ($823,210.89 rendered
 * as `$823,21…`, a 1000× misread).
 *
 * jsdom has no layout: `scrollWidth` and ellipsis cannot be measured here, so
 * the promise is asserted as the contract that produces it — the overflow flag,
 * the scrolling class, the table's width, and the fact that every column is at
 * (never below) its declared floor.
 */
describe("a narrow panel keeps its numbers whole (F6)", () => {
  const VALUE_VIEW = VIEW_FIXTURE[1] as ViewDefinition;

  it("keeps every column at its floor and scrolls at 1024", () => {
    renderGrid({
      view: VALUE_VIEW,
      rows: [],
      status: "ready",
      rowCount: 0,
      containerWidth: 1024,
    });

    const scroll = screen.getByTestId("grid-scroll");
    const table = screen.getByTestId("grid-table");
    const solved = solveView(VALUE_VIEW.columns, 1024);
    const widths = columnWidths(table);

    expect(solved.overflow).toBe(true);
    expect(scroll.getAttribute("data-overflow")).toBe("true");
    expect(scroll.className).toContain("overflow-x-auto");

    expect(Number.parseFloat(table.style.width)).toBe(solved.total);
    expect(Number.parseFloat(table.style.width)).toBeGreaterThan(1024);
    expect(table.style.minWidth).toBe(`${solved.total}px`);
    expect(widths).toEqual(solved.widths);

    solved.columns.forEach((column, index) => {
      expect(widths[index], `${column.key} is below its floor`).toBeGreaterThanOrEqual(
        column.fixed ?? column.min,
      );
    });

    // The header's own label still fits the column it was given.
    const headers = table.querySelectorAll<HTMLElement>("thead th");
    solved.columns.forEach((column, index) => {
      const spec = columnSpec(column.key);
      const text = (headers[index]?.textContent ?? "").trim();
      expect(headerNeed({ ...spec, label: text })).toBeLessThanOrEqual((widths[index] ?? 0) + 2);
    });

    expect(screen.getByTestId("grid-overflow-note").textContent).toContain("scroll");
    expect(table.getAttribute("aria-describedby")).toBe("grid-overflow-note");
  });

  it("does not ask any view to scroll at 1280", () => {
    for (const view of VIEW_FIXTURE) {
      const rendered = renderGrid({
        view: view as ViewDefinition,
        rows: [],
        status: "ready",
        rowCount: 0,
        containerWidth: 1280,
      });
      const scroll = screen.getByTestId("grid-scroll");
      expect(scroll.getAttribute("data-overflow"), `${view.name} scrolls at 1280`).toBe("false");
      expect(screen.queryByTestId("grid-overflow-note")).toBeNull();
      expect(columnWidths(screen.getByTestId("grid-table")).reduce((a, b) => a + b, 0)).toBe(1280);
      rendered.unmount();
    }
  });
});

/** F12 — two rows with the same symbol must not share a React key. */
describe("duplicate symbols (F12)", () => {
  it("gives duplicate rows unique ids without dropping either", () => {
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    const view: ViewDefinition = { ...TODAY_VIEW, columns: ["price"] };
    const rows: ScreenRow[] = [
      { symbol: "AAPL", price: 1 },
      { symbol: "AAPL", price: 2 },
      { symbol: "MSFT", price: 3 },
    ];

    renderGrid({ view, rows, rowCount: 3 });

    const rendered = [
      ...screen.getByTestId("grid-table").querySelectorAll("tbody tr[data-symbol]"),
    ];
    expect(rendered).toHaveLength(3);
    expect(rendered.map((row) => row.getAttribute("data-row-id"))).toEqual([
      "AAPL",
      "AAPL#2",
      "MSFT",
    ]);
    // Each duplicate still shows its own price.
    expect(rendered[0]?.querySelector("td:last-child")?.textContent).toBe("$1.00");
    expect(rendered[1]?.querySelector("td:last-child")?.textContent).toBe("$2.00");

    const duplicateKeyWarning = errorSpy.mock.calls.some((args) =>
      args.some((argument) => typeof argument === "string" && /same key/i.test(argument)),
    );
    expect(duplicateKeyWarning).toBe(false);
    errorSpy.mockRestore();
  });

  it("suffixes only the repeated occurrence", () => {
    expect(uniqueRowIds([{ symbol: "A" }, { symbol: "A" }, { symbol: "A" }, { symbol: "B" }])).toEqual([
      "A",
      "A#2",
      "A#3",
      "B",
    ]);
    expect(uniqueRowIds([])).toEqual([]);
  });
});

/**
 * The row action lane: star, bell and a "…" menu. No compare icon — compare
 * is a mode on the toolbar — and no checkbox column unless that mode asks.
 */
describe("row actions without a checkbox lane", () => {
  function actionProps() {
    const onAddTo = vi.fn();
    const onAlert = vi.fn();
    return { onAddTo, onAlert, rowActions: { onAddTo, onAlert } };
  }

  function firstRow() {
    return screen
      .getByTestId("grid-table")
      .querySelector<HTMLElement>("tbody tr[data-symbol]") as HTMLElement;
  }

  it("draws no checkbox anywhere and gives the freed 30 px back to the columns", () => {
    renderGrid();
    const table = screen.getByTestId("grid-table");

    expect(table.querySelectorAll('input[type="checkbox"]')).toHaveLength(0);
    expect(screen.queryByTestId("select-all")).toBeNull();
    expect(screen.queryByTestId("select-all-cell")).toBeNull();
    expect(columnWidths(table)).toHaveLength(TODAY_COLUMNS.length + 1);
    expect(columnWidths(table).reduce((a, b) => a + b, 0)).toBe(1280);
    expect(Number.parseFloat(table.style.width)).toBe(1280);
  });

  it("shows star, bell and the '…' door on hover or focus, over a soft fade", () => {
    const { rowActions } = actionProps();
    renderGrid({ rowActions });
    const row = firstRow();
    const lane = within(row).getByTestId("row-actions-AAPL");

    // 20 px glyphs inside one absolutely positioned lane: it never takes a
    // column, and the row's cells do not move when it appears.
    expect(lane.className).toContain("absolute");
    expect(lane.className).toContain("right-0");
    expect(lane.className).toContain("opacity-0");
    expect(lane.className).toContain("group-hover:opacity-100");
    expect(lane.className).toContain("group-focus-within:opacity-100");
    expect(lane.className).toContain("max-[900px]:opacity-100");
    expect(lane.className).toMatch(/from-surface|bg-surface/);

    const glyphs = [...lane.querySelectorAll("svg")];
    expect(glyphs.map((glyph) => glyph.getAttribute("data-icon"))).toEqual([
      "star",
      "bell",
      "more-horizontal",
    ]);
    for (const glyph of glyphs) {
      expect(glyph.getAttribute("width")).toBe("20");
      expect(glyph.getAttribute("height")).toBe("20");
    }
    expect(within(row).getByRole("button", { name: "Add AAPL to a watchlist or portfolio" })).toBeTruthy();
    expect(within(row).queryByRole("button", { name: "Compare AAPL" })).toBeNull();
    expect(within(row).getByRole("button", { name: "Set an alert for AAPL" })).toBeTruthy();
    expect(within(row).getByRole("button", { name: "Row actions for AAPL" })).toBeTruthy();
  });

  it("fills the star when the symbol is on either list", () => {
    const { rowActions } = actionProps();
    renderGrid({ rowActions, starredSymbols: ["AAPL"] });
    const star = within(firstRow()).getByRole("button", {
      name: "Add AAPL to a watchlist or portfolio (on a list)",
    });
    expect(star.getAttribute("data-starred")).toBe("true");
    expect(star.querySelector("svg")?.getAttribute("class")).toContain("fill-current");
    // The state is spoken and tooltipped, not left to the fill alone.
    expect(star.getAttribute("title")).toContain("AAPL is on a list");
  });

  it("draws the bell as an outline until the symbol has an alert", () => {
    const { rowActions } = actionProps();
    renderGrid({ rowActions });
    const bell = within(firstRow()).getByRole("button", { name: "Set an alert for AAPL" });

    expect(bell.getAttribute("data-alerted")).toBe("false");
    expect(bell.querySelector("svg")?.getAttribute("class") ?? "").not.toContain("fill-current");
    expect(bell.getAttribute("title")).toBe("Set a price alert for AAPL");
  });

  it("fills the bell when the symbol has alerts and says how many", () => {
    const { rowActions } = actionProps();
    renderGrid({ rowActions, alertCounts: new Map([["AAPL", 2]]) });
    const bell = within(firstRow()).getByRole("button", {
      name: "Set an alert for AAPL (2 active)",
    });

    expect(bell.getAttribute("data-alerted")).toBe("true");
    expect(bell.querySelector("svg")?.getAttribute("class")).toContain("fill-current");
    expect(bell.querySelector("svg")?.getAttribute("class")).toContain("text-accent-text");
    expect(bell.getAttribute("title")).toBe("2 alerts for AAPL — review or add another");
  });

  it("opens the Add-to menu from the star, without navigating or a dialog", async () => {
    const onActivate = vi.fn();
    const { onAddTo, rowActions } = actionProps();
    const user = userEvent.setup();
    renderGrid({ onActivate, rowActions });
    const row = firstRow();

    await user.click(within(row).getByRole("button", { name: "Add AAPL to a watchlist or portfolio" }));

    const menu = within(row).getByRole("menu", { name: "Add to" });
    expect(within(menu).getAllByRole("menuitem").map((item) => item.textContent)).toEqual([
      "Watchlist",
      "Portfolio",
    ]);
    expect(onActivate).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).toBeNull();

    await user.click(within(menu).getByRole("menuitem", { name: "Portfolio" }));
    expect(onAddTo).toHaveBeenCalledWith("AAPL", "portfolio");
    expect(onActivate).not.toHaveBeenCalled();
  });

  it("alerts the row's own symbol without navigating", async () => {
    const onActivate = vi.fn();
    const { onAlert, rowActions } = actionProps();
    const user = userEvent.setup();
    renderGrid({ onActivate, rowActions });
    const row = firstRow();

    await user.click(within(row).getByRole("button", { name: "Set an alert for AAPL" }));
    expect(onAlert).toHaveBeenCalledWith("AAPL");
    expect(onActivate).not.toHaveBeenCalled();
  });

  it("tells the panel which row is hovered, and clears it on leave", async () => {
    const onRowPoint = vi.fn();
    const user = userEvent.setup();
    renderGrid({ onRowPoint });
    const row = firstRow();

    await user.hover(row);
    expect(onRowPoint).toHaveBeenCalledWith("AAPL");
    await user.unhover(row);
    expect(onRowPoint).toHaveBeenLastCalledWith(null);
  });

  it("reaches the '…' menu with Tab, and Escape returns focus to the row", async () => {
    const onActivate = vi.fn();
    const { onAlert, rowActions } = actionProps();
    const user = userEvent.setup();
    renderGrid({ onActivate, rowActions });
    const row = firstRow();
    row.focus();

    await user.tab();
    const trigger = within(row).getByRole("button", { name: "Row actions for AAPL" });
    expect(document.activeElement).toBe(trigger);
    expect(trigger.getAttribute("aria-haspopup")).toBe("menu");
    expect(trigger.getAttribute("aria-expanded")).toBe("false");

    await user.keyboard("{Enter}");
    const menu = within(row).getByRole("menu", { name: "Row actions" });
    expect(within(menu).getAllByRole("menuitem").map((item) => item.textContent)).toEqual([
      "Add to",
      "Set alert",
    ]);
    expect(trigger.getAttribute("aria-expanded")).toBe("true");

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("menu")).toBeNull();
    expect(document.activeElement).toBe(row);

    // The row is still a door: Enter on the row itself navigates, even while
    // the action lane lives inside it.
    await user.keyboard("{Enter}");
    expect(onActivate).toHaveBeenCalledWith("AAPL");

    await user.click(trigger);
    await user.click(within(row).getByRole("menuitem", { name: "Set alert" }));
    expect(onAlert).toHaveBeenCalledWith("AAPL");
  });
});

/** Compare is a mode: boxes exist only while the toolbar has asked for them. */
describe("Batch 1 Quick Wins", () => {
  describe("zebra striping", () => {
    it("gives even-indexed rows a different background class than odd rows", () => {
      renderGrid();
      const table = screen.getByTestId("grid-table");
      const rows = table.querySelectorAll<HTMLElement>("tbody tr[data-symbol]");

      // Row 0 (first) — odd child → bg-row
      expect(rows[0]?.className).toMatch(/odd:bg-row/);
      // Row 1 (second) — even child → bg-row-alt
      expect(rows[1]?.className).toMatch(/even:bg-row-alt/);
      // Row 2 (third) — odd again
      expect(rows[2]?.className).toMatch(/odd:bg-row/);
    });

    it("every row carries both zebra classes (Tailwind picks the right one at render time)", () => {
      renderGrid();
      const table = screen.getByTestId("grid-table");
      const rows = table.querySelectorAll<HTMLElement>("tbody tr[data-symbol]");
      expect(rows).toHaveLength(100);

      for (const row of rows) {
        expect(row.className).toMatch(/odd:bg-row/);
        expect(row.className).toMatch(/even:bg-row-alt/);
      }
    });
  });

  describe("sticky column headers", () => {
    it("the thead element carries the sticky class", () => {
      renderGrid();
      const table = screen.getByTestId("grid-table");
      const thead = table.querySelector("thead");
      expect(thead).not.toBeNull();
      expect(thead?.className).toMatch(/\bsticky\b/);
      expect(thead?.className).toMatch(/\btop-0\b/);
      expect(thead?.className).toMatch(/\bz-10\b/);
    });

    it("every th still has sticky inline style", () => {
      renderGrid();
      const table = screen.getByTestId("grid-table");
      const headers = table.querySelectorAll<HTMLElement>("thead th");
      for (const th of headers) {
        expect(th.style.position).toBe("sticky");
        expect(th.style.top).toBe("0px");
      }
    });
  });

  describe("compact density", () => {
    it("renders rows at 24 px instead of 28 px", () => {
      renderGrid();
      const table = screen.getByTestId("grid-table");
      const row = table.querySelector<HTMLElement>("tbody tr[data-symbol]");
      expect(row?.style.height).toBe("24px");
      // No h-7 class (h-7 = 1.75rem = 28px); should have h-6 or no fixed h-* class
      expect(row?.className).not.toMatch(/\bh-7\b/);
    });

    it("renders the header at 24 px", () => {
      renderGrid();
      const table = screen.getByTestId("grid-table");
      const header = table.querySelector<HTMLElement>("thead th");
      expect(header?.style.height).toBe("24px");
    });
  });
});

describe("compare selection mode", () => {
  it("has no checkbox lane until compareSelection is supplied", () => {
    renderGrid();
    expect(screen.getByTestId("grid-table").querySelectorAll('input[type="checkbox"]')).toHaveLength(0);
  });

  it("draws one box per row, toggles it, and never navigates", async () => {
    const onToggle = vi.fn();
    const onActivate = vi.fn();
    const user = userEvent.setup();
    renderGrid({
      onActivate,
      compareSelection: { selected: ["AAPL"], onToggle, max: 10 },
    });
    const table = screen.getByTestId("grid-table");
    const boxes = [...table.querySelectorAll<HTMLInputElement>('tbody input[type="checkbox"]')];
    expect(boxes).toHaveLength(TODAY_ROWS.length);
    expect(boxes[0]?.checked).toBe(true);
    expect(boxes[0]?.getAttribute("aria-label")).toBe("Compare AAPL");
    expect(columnWidths(table)).toHaveLength(TODAY_COLUMNS.length + 2);
    expect(columnWidths(table).reduce((a, b) => a + b, 0)).toBe(1280);

    await user.click(boxes[1] as HTMLInputElement);
    expect(onToggle).toHaveBeenCalledWith("NVDA");
    expect(onActivate).not.toHaveBeenCalled();
  });

  it("disables an unselected box once the cap is reached", () => {
    renderGrid({
      compareSelection: { selected: ["AAPL", "NVDA", "MSFT", "AMZN", "GOOG"], onToggle: vi.fn(), max: 5 },
    });
    const table = screen.getByTestId("grid-table");
    const boxes = [...table.querySelectorAll<HTMLInputElement>('tbody input[type="checkbox"]')];
    expect(boxes[0]?.disabled).toBe(false);
    expect(boxes[5]?.disabled).toBe(true);
  });
});
