/**
 * The screener's two panes share one frame.
 *
 * The table and the docked detail panel are one row: same border colour,
 * width and radius, both stretched to the same top and bottom edge. The
 * table becomes a scroll box only when the panel is the taller of the two —
 * a height equaliser, never the default state (the table is not scrollable by
 * default; the page owns that scroll).
 */

import { render, screen, waitFor } from "@testing-library/react";
import type * as React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DetailPanel } from "@/detail/DetailPanel";
import { resetFilterSchemaCache } from "@/filters/schema";
import { filterSchemaWire } from "@/filters/__fixtures__/schema";

import { ScreenerScreen } from "./ScreenerScreen";
import { filterDocumentWire } from "./__fixtures__/filters";
import { resetFiltersDocumentCache } from "./filtersDocument";

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

function page(): Record<string, unknown> {
  return {
    as_of: "2026-11-01T10:00:00Z",
    limit: 20,
    total: 1,
    universe_total: 1,
    returned: 1,
    columns: ["price"],
    rows: [{ symbol: "AAPL", price: 100 }],
    budget: { truncated: false, dropped_fields: [] },
  };
}

function stubApi(): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/screens/select/schema") return json(filterSchemaWire());
      if (url === "/screens/filters") return json(filterDocumentWire());
      if (url === "/screens/select" && init?.method === "POST") return json(page());
      return json({ errorMessage: `no route for ${url}` });
    }),
  );
}

/** jsdom has no layout: the equaliser reads these two numbers. */
function mockHeights(heights: Record<string, number>): void {
  Object.defineProperty(Element.prototype, "scrollHeight", {
    configurable: true,
    get(this: Element) {
      return heights[this.getAttribute("data-testid") ?? ""] ?? 0;
    },
  });
}

function detailNode(): React.ReactElement {
  return <DetailPanel symbol="AAPL" onClose={vi.fn()} />;
}

function renderScreen(detail: React.ReactNode) {
  return render(
    <ScreenerScreen assetClass="stock" symbol="AAPL" onSelectSymbol={vi.fn()} detail={detail} />,
  );
}

beforeEach(() => {
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
  window.history.replaceState(null, "", "/app/screener");
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("the screener row", () => {
  it("frames the table and the panel alike and scrolls only to equalise a taller panel", async () => {
    stubApi();
    mockHeights({ "detail-panel": 900, "table-frame": 600 });

    const { rerender } = renderScreen(detailNode());
    const table = await screen.findByTestId("table-frame");
    const panel = screen.getByTestId("detail-panel");

    // One frame language: same border colour, width and radius tokens.
    for (const token of ["border", "border-line", "rounded-md"]) {
      expect(table.className, `table is missing ${token}`).toContain(token);
      expect(panel.className, `panel is missing ${token}`).toContain(token);
    }

    // One row, stretched top to bottom.
    const row = screen.getByTestId("screener-row");
    expect(row.className).toContain("flex");
    expect(row.className).toContain("items-stretch");

    // A taller panel turns the table into the scroll container...
    await waitFor(() => expect(table.getAttribute("data-equalise")).toBe("true"));
    expect(screen.getByTestId("table-frame").className).toContain("overflow-y-auto");

    // ...and a shorter panel gives the table its natural height back.
    mockHeights({ "detail-panel": 300, "table-frame": 600 });
    rerender(
      <ScreenerScreen
        assetClass="stock"
        symbol="AAPL"
        onSelectSymbol={vi.fn()}
        detail={detailNode()}
      />,
    );
    await waitFor(() =>
      expect(screen.getByTestId("table-frame").getAttribute("data-equalise")).toBe("false"),
    );
    expect(screen.getByTestId("table-frame").className).not.toContain("overflow-y-auto");
  });
});
