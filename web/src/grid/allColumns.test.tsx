import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ViewDefinition } from "@/data/screens";

import { AllColumnsPanel } from "./AllColumnsPanel";
import { visibleRange } from "./window";

/**
 * The 116 × 20 view, measured and proved.
 *
 * The destination's promises are physical: 116 keys on 20 symbols, a table
 * wider than its panel with a real scrollbar, an identity lane pinned at every
 * scroll offset, and every catalogue key reachable — because the renderer keeps
 * only the visible window of columns in the DOM (2 340 cells laid out at once
 * is a measured second of frozen main thread in Chrome). Scrolling to the end
 * must show the last key, and scrolling back must show the first.
 *
 * The chunk failure cases are here too: a missing chunk leaves visible gaps
 * with the chunk named, never a plausible-looking row.
 */

const KEYS = Array.from({ length: 116 }, (_, index) => `k${String(index).padStart(3, "0")}`);
const SYMBOLS = Array.from({ length: 20 }, (_, index) => `S${index}`);

const DESTINATION: ViewDefinition = {
  name: "all",
  label: "All columns",
  job: "What does the whole catalogue look like at once?",
  columns: KEYS,
};

function answer(columns: readonly string[], symbols: readonly string[] = SYMBOLS) {
  return new Response(
    JSON.stringify({
      as_of: "2026-10-31T01:28:01Z",
      limit: 20,
      total: 5_576,
      universe_total: 5_576,
      returned: symbols.length,
      columns,
      rows: symbols.map((symbol) => {
        const row: Record<string, unknown> = { symbol };
        for (const key of columns) row[key] = 1.5;
        return row;
      }),
      budget: { truncated: false, dropped_fields: [] },
    }),
    { status: 200, headers: { "content-type": "application/json" } },
  );
}

function stub(handler: (columns: string[], index: number) => Response | Promise<Response>) {
  const calls: string[][] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) !== "/screens/select") throw new Error(`unexpected ${String(input)}`);
      const body = JSON.parse(String(init?.body ?? "{}")) as { columns: string[] };
      calls.push(body.columns);
      return handler(body.columns, calls.length - 1);
    }),
  );
  return calls;
}

/** Header cells that carry a column, i.e. not the window spacers. */
function realHeaders(table: HTMLElement): HTMLElement[] {
  return [...table.querySelectorAll("thead th")].filter(
    (cell) => !cell.hasAttribute("aria-hidden"),
  ) as HTMLElement[];
}

function keysOnScreen(table: HTMLElement): string[] {
  return realHeaders(table)
    .map((cell) => cell.getAttribute("data-key") ?? "")
    .filter((key) => key.length > 0 && key !== "identity");
}

/** jsdom has no scroll engine: set the offset the handler will read, then fire. */
async function scrollTo(scrollLeft: number) {
  const scroll = screen.getByTestId("grid-scroll");
  Object.defineProperty(scroll, "scrollLeft", { value: scrollLeft, configurable: true });
  fireEvent.scroll(scroll);
  await waitFor(() => expect(scroll.getAttribute("data-window")).not.toBe("every"));
}

const props = {
  destination: DESTINATION,
  keys: KEYS,
  selectedSymbol: null,
  onActivate: vi.fn(),
  onClear: vi.fn(),
  onBack: vi.fn(),
  containerWidth: 1280,
};

afterEach(() => vi.unstubAllGlobals());

describe("the column window", () => {
  it("starts at the left edge and covers what the viewport can hold", () => {
    const widths = Array.from({ length: 116 }, () => 80);
    const range = visibleRange(widths, 0, 800, 0);
    expect(range.start).toBe(0);
    expect(range.end).toBe(9); // the tenth column starts at 800 — outside
  });

  it("follows the scroll offset and keeps the last column reachable", () => {
    const widths = Array.from({ length: 116 }, () => 80);
    const near = visibleRange(widths, 4000, 800, 0);
    expect(near.start).toBe(50);
    expect(near.end).toBe(59);

    const end = visibleRange(widths, 116 * 80 - 800, 800, 0);
    expect(end.end).toBe(115);
    expect(end.start).toBe(106);
  });

  it("renders every column when the viewport is unknown", () => {
    const widths = Array.from({ length: 116 }, () => 80);
    expect(visibleRange(widths, 0, 0)).toEqual({ start: 0, end: 115 });
    expect(visibleRange([], 0, 800)).toEqual({ start: 0, end: -1 });
  });
});

describe("the all-columns panel", () => {
  it("draws 116 keys on 20 symbols, wider than the panel, with the lane pinned", async () => {
    const calls = stub((columns) => answer(columns));

    const started = performance.now();
    render(<AllColumnsPanel {...props} />);
    const table = await screen.findByTestId("grid-table", undefined, { timeout: 8000 });
    const painted = performance.now() - started;

    expect(screen.getByTestId("all-columns-title").textContent).toBe(
      "All columns — 116, wide view, 20 rows",
    );
    expect(calls).toHaveLength(3);
    expect(table.getAttribute("aria-colcount")).toBe("117");

    // The colgroup carries every key's width — that is the scroll range.
    const cols = [...table.querySelectorAll("colgroup col")];
    expect(cols).toHaveLength(117);
    const total = Number.parseInt(table.style.width, 10);
    const sum = cols.reduce((acc, col) => acc + Number.parseFloat((col as HTMLElement).style.width), 0);
    expect(Math.round(sum)).toBe(total);
    expect(total).toBeGreaterThan(1280);

    // Only a window of columns is in the DOM; the identity lane always is.
    const headers = realHeaders(table);
    expect(headers[0]?.textContent).toBe("Symbol");
    expect((headers[0] as HTMLElement).style.position).toBe("sticky");
    expect((headers[0] as HTMLElement).style.left).toBe("0px");
    const onScreen = keysOnScreen(table);
    expect(onScreen.length).toBeGreaterThan(5);
    expect(onScreen.length).toBeLessThan(40);
    expect(onScreen[0]).toBe("k000");

    const rows = [...table.querySelectorAll("tbody tr[data-symbol]")];
    expect(rows).toHaveLength(20);
    expect(new Set(rows.map((row) => row.getAttribute("data-symbol"))).size).toBe(20);

    expect(screen.getByTestId("grid-scroll").getAttribute("data-overflow")).toBe("true");

    // Every key is reachable: scroll to the end, the last key appears.
    await scrollTo(total - 1280);
    await waitFor(() => expect(keysOnScreen(table)).toContain("k115"));
    expect(keysOnScreen(table)).not.toContain("k000");
    // ...and the lane is still the first cell, still pinned.
    expect(realHeaders(table)[0]?.getAttribute("data-key")).toBe("identity");

    // Back to the start, the first key is there again.
    await scrollTo(0);
    await waitFor(() => expect(keysOnScreen(table)).toContain("k000"));

    console.log(
      `[116x20] first paint ${painted.toFixed(1)} ms (jsdom, mocked fetch); window ${onScreen.length} of 116 columns; panel reports ${screen
        .getByTestId("all-columns-panel")
        .getAttribute("data-render-ms")} ms`,
    );
    expect(painted).toBeLessThan(8000);
  });

  it("shows a failed chunk as absent cells and names the chunk", async () => {
    stub((columns, index) =>
      index === 1 ? Promise.reject(new Error("HTTP 500: boom")) : answer(columns),
    );

    render(<AllColumnsPanel {...props} />);
    const table = await screen.findByTestId("grid-table", undefined, { timeout: 8000 });
    const notices = await screen.findByTestId("all-columns-notices");

    expect(notices.getAttribute("role")).toBe("alert");
    expect(notices.textContent).toContain("Chunk 2 of 3 failed");
    expect(notices.textContent).toContain("HTTP 500: boom");
    // 20 rows × the 40 columns chunk 2 owned, counted from the model.
    await waitFor(() =>
      expect(screen.getByTestId("all-columns-as-of").textContent).toContain("800 cells absent"),
    );

    // Scrolled to chunk 2's band, those 40 columns show as absent on every row.
    await scrollTo(3_400);
    await waitFor(() => {
      const absent = table.querySelectorAll("tbody span[data-absent='true']");
      expect(absent.length).toBeGreaterThan(0);
    });
    const absent = [...table.querySelectorAll("tbody span[data-absent='true']")];
    expect(absent.every((cell) => cell.getAttribute("title") === "chunk 2 of 3 failed")).toBe(true);
    expect(absent[0]?.textContent).toBe("—");
    // The rest of the row is intact: no cell is silently filled.
    expect(table.querySelector("tbody td[data-null='true']")).toBeNull();
  });

  it("reports a symbol one chunk omitted without filling it from another", async () => {
    stub((columns, index) =>
      index === 2 ? answer(columns, SYMBOLS.filter((symbol) => symbol !== "S3")) : answer(columns),
    );

    render(<AllColumnsPanel {...props} />);
    const table = await screen.findByTestId("grid-table", undefined, { timeout: 8000 });
    const notices = await screen.findByTestId("all-columns-notices");

    expect(notices.textContent).toContain("Chunk 3 of 3 answered without 1 symbol (S3)");

    // Chunk 3's columns live at the far right; scroll there to see the gap.
    const total = Number.parseInt(table.style.width, 10);
    await scrollTo(total - 1280);
    await waitFor(() => {
      const row = table.querySelector("tbody tr[data-symbol='S3']");
      expect(row?.querySelectorAll("span[data-absent='true']").length ?? 0).toBeGreaterThan(0);
    });
    const s3 = table.querySelector("tbody tr[data-symbol='S3']");
    const s4 = table.querySelector("tbody tr[data-symbol='S4']");
    expect(s3?.querySelectorAll("td:not([aria-hidden])")).toHaveLength(realHeaders(table).length);
    expect(s4?.querySelectorAll("span[data-absent='true']")).toHaveLength(0);
  });
});
