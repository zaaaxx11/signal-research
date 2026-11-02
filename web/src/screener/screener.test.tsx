/**
 * The screen, end to end: the header writes the one filter state, the panel
 * sends it, and a column preset moves the columns without touching the rows.
 *
 * The network is stubbed at `fetch`, so every assertion about a request is an
 * assertion about the wire body — the same body the live API receives.
 */

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { resetFilterSchemaCache } from "@/filters/schema";
import { filterSchemaWire } from "@/filters/__fixtures__/schema";
import { VIEW_FIXTURE } from "@/grid/__fixtures__/views";
import { isClientColumn } from "@/grid/columnMeta";

import { ScreenerScreen } from "./ScreenerScreen";
import { defaultColumns } from "./defaultColumns";
import { resetFiltersDocumentCache } from "./filtersDocument";
import { USER_PRESETS_KEY, serializeUserPresets } from "./userPresets";
import { CAP_MEGA } from "./__fixtures__/filters";
import { filterDocumentWire } from "./__fixtures__/filters";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function page(count: number) {
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

interface WireBody {
  view: string;
  columns: string[];
  criteria: { filters: unknown[]; offset: number; sort?: string };
  signal?: string;
}

function stubApi(
  selectAnswer?: (body: WireBody) => Response,
  schema: Record<string, unknown> = filterSchemaWire(),
) {
  const selects: WireBody[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url === "/screens/select/schema") return json(schema);
    if (url === "/screens/filters") return json(filterDocumentWire());
    if (url === "/screens/select" && init?.method === "POST") {
      const body = JSON.parse(String(init.body)) as WireBody;
      selects.push(body);
      return selectAnswer ? selectAnswer(body) : json(page(20));
    }
    if (url.startsWith("/market/")) return json({ errorMessage: "not in this test" }, 404);
    if (url.startsWith("/symbols/search")) {
      return json([{ symbol: "AAPL", name: "Apple Inc.", exchange: "NASDAQ" }]);
    }
    return json({ errorMessage: `no route for ${url}` }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return { selects, fetchMock };
}

function renderScreen(detail: React.ReactNode = null) {
  return render(
    <ScreenerScreen assetClass="stock" symbol={null} onSelectSymbol={vi.fn()} detail={detail} />,
  );
}

/** A schema whose views carry the real catalogue keys, not `column_N` filler. */
function realColumnSchema(): Record<string, unknown> {
  return { ...filterSchemaWire(), views: VIEW_FIXTURE };
}

/** The union `splitCatalogue` reads when the schema serves no wide view. */
const REAL_CATALOGUE = [...new Set(VIEW_FIXTURE.flatMap((view) => view.columns))];

/** What the Summary tab asks for at the wire: the product default set. */
const SUMMARY_WIRE_COLUMNS = defaultColumns(VIEW_FIXTURE[0]!.columns, REAL_CATALOGUE).filter(
  (key) => !isClientColumn(key),
);

beforeEach(() => {
  // jsdom has no layout: without a measured container the table renders its
  // "not measured yet" branch and the grid's own states (error included) never
  // mount. 1 280 px is the width the solver is written against.
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
  window.localStorage.clear();
  window.history.replaceState(null, "", "/app/screener");
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("the screener screen", () => {
  it("adds a chip and issues exactly one more request when an option is chosen", async () => {
    const user = userEvent.setup();
    const api = stubApi();
    renderScreen();

    await waitFor(() => expect(api.selects).toHaveLength(1));
    expect(api.selects[0]?.criteria.filters).toEqual([]);

    await user.click(screen.getByTestId("filter-button"));
    await user.selectOptions(screen.getByTestId("control-select-cap"), CAP_MEGA);

    // One option, one predicate, one request — not one per option rendered.
    await waitFor(() => expect(api.selects).toHaveLength(2));
    expect(api.selects[1]?.criteria.filters).toEqual([
      { key: "cap", op: "eq", value: "mega" },
    ]);
    expect(api.selects[1]?.criteria.offset).toBe(0);

    const chips = screen.getByTestId("chip-bar");
    expect(within(chips).getByText("Market Cap.")).toBeTruthy();
    // The chip names the option the select named, not the API's wire token.
    expect(chips.textContent).toContain("Mega ($200bln and more)");
    expect(chips.textContent).not.toContain("cap:mega");

    // Removing the chip restores the previous screen: one request with no
    // predicates, and the bar that described the filter is gone with it.
    await user.click(within(chips).getByRole("button", { name: /Remove filter/ }));
    await waitFor(() => expect(api.selects).toHaveLength(3));
    expect(api.selects[2]?.criteria.filters).toEqual([]);
    expect(screen.queryByTestId("chip-bar")).toBeNull();
  });

  it("changes the view from a preset without clearing the filter", async () => {
    const user = userEvent.setup();
    const api = stubApi();
    renderScreen();

    await waitFor(() => expect(api.selects).toHaveLength(1));
    await user.click(screen.getByTestId("filter-button"));
    await user.selectOptions(screen.getByTestId("control-select-cap"), CAP_MEGA);
    await waitFor(() => expect(api.selects).toHaveLength(2));

    await user.click(screen.getByTestId("preset-valuation"));

    await waitFor(() => expect(api.selects).toHaveLength(3));
    const body = api.selects[2];
    // The value view in the served schema is 13 columns; `view` itself travels
    // in the query key, not the body.
    expect(body?.columns).toHaveLength(13);
    expect(body?.columns[0]).toBe("column_0");
    // Columns changed; rows did not. This is the whole promise of the bar.
    expect(body?.criteria.filters).toEqual([{ key: "cap", op: "eq", value: "mega" }]);
    expect(within(screen.getByTestId("chip-bar")).getByText("Market Cap.")).toBeTruthy();
    expect(new URLSearchParams(window.location.search).get("view")).toBe("value");
  });

  it("keeps Summary on its own view and columns instead of falling through to Custom", async () => {
    const user = userEvent.setup();
    const api = stubApi(undefined, realColumnSchema());
    renderScreen();

    await waitFor(() => expect(api.selects).toHaveLength(1));
    // The screen opens on the product's default set, and the bar says Summary —
    // not Custom — because Summary resolves to that same set.
    expect(api.selects[0]?.columns).toEqual(SUMMARY_WIRE_COLUMNS);
    expect(screen.getByTestId("preset-summary").getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByTestId("preset-custom").getAttribute("aria-pressed")).toBe("false");

    await user.click(screen.getByTestId("preset-valuation"));
    await waitFor(() => expect(api.selects).toHaveLength(2));
    expect(screen.getByTestId("preset-summary").getAttribute("aria-pressed")).toBe("false");

    await user.click(screen.getByTestId("preset-summary"));
    await waitFor(() => expect(api.selects).toHaveLength(3));

    const url = new URLSearchParams(window.location.search);
    expect(url.get("view")).toBe("today");
    expect(url.get("view")).not.toBe("custom");
    expect(url.get("cols") ?? "").not.toContain("custom");
    expect(screen.getByTestId("preset-summary").getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByTestId("preset-custom").getAttribute("aria-pressed")).toBe("false");
    // The Summary columns, not a silent Custom fallback.
    expect(api.selects[2]?.columns).toEqual(SUMMARY_WIRE_COLUMNS);
  });

  it("treats Custom as a column set and renders no Columns card", async () => {
    const user = userEvent.setup();
    const api = stubApi(undefined, realColumnSchema());
    renderScreen();

    await waitFor(() => expect(api.selects).toHaveLength(1));
    await user.click(screen.getByTestId("preset-custom"));

    // No dialog, no "Columns 12 / 40" card: the tab is a column set, and the
    // selection control lives in the header.
    expect(screen.queryByTestId("column-picker")).toBeNull();
    expect(document.body.textContent ?? "").not.toMatch(/Columns\s+\d+\s*\/\s*40/);

    await waitFor(() => expect(api.selects).toHaveLength(2));
    // Without a saved set, Custom is the served view's own columns.
    expect(api.selects[1]?.columns).toEqual(VIEW_FIXTURE[0]!.columns);
    expect(screen.getByTestId("preset-custom").getAttribute("aria-pressed")).toBe("true");
    expect(new URLSearchParams(window.location.search).get("view")).toBe("today");
  });

  it("uses the selected saved set as the Custom tab's columns", async () => {
    window.localStorage.setItem(
      USER_PRESETS_KEY,
      serializeUserPresets({
        presets: [
          { id: "my-stock-presets", name: "My stock presets", columns: [] },
          { id: "cheap-1", name: "Cheap", columns: ["price", "pe", "market_cap"] },
        ],
        selected: "cheap-1",
      }),
    );
    const user = userEvent.setup();
    const api = stubApi(undefined, realColumnSchema());
    renderScreen();

    await waitFor(() => expect(api.selects).toHaveLength(1));
    await user.click(screen.getByTestId("preset-custom"));
    await waitFor(() => expect(api.selects).toHaveLength(2));

    expect(api.selects[1]?.columns).toEqual(["price", "pe", "market_cap"]);
    expect(screen.getByTestId("preset-custom").getAttribute("aria-pressed")).toBe("true");
    // Still the view, never a `view=custom` fallback.
    expect(new URLSearchParams(window.location.search).get("view")).toBe("today");
  });

  it("opens the column picker from the header control, with the 40-column cap intact", async () => {
    const user = userEvent.setup();
    const api = stubApi(undefined, realColumnSchema());
    renderScreen();

    await waitFor(() => expect(api.selects).toHaveLength(1));
    await user.click(screen.getByTestId("open-column-picker"));

    const dialog = await screen.findByTestId("column-picker");
    expect(within(dialog).getByTestId("column-picker-count").textContent).toMatch(/\/ 40$/);
    // The picker is the header's, not the table view's: closing it leaves the
    // table exactly as it was.
    await user.click(within(dialog).getByRole("button", { name: "Close columns" }));
    expect(screen.queryByTestId("column-picker")).toBeNull();
  });

  it("reproduces the screen from the URL after a reload", async () => {
    const user = userEvent.setup();
    const api = stubApi();
    const first = renderScreen();

    await waitFor(() => expect(api.selects).toHaveLength(1));
    await user.click(screen.getByTestId("filter-button"));
    await user.selectOptions(screen.getByTestId("control-select-cap"), CAP_MEGA);
    await waitFor(() => expect(api.selects).toHaveLength(2));
    await user.click(screen.getByTestId("preset-valuation"));
    await waitFor(() => expect(api.selects).toHaveLength(3));

    const search = window.location.search;
    expect(search).toContain("view=value");
    expect(new URLSearchParams(search).getAll("f")).toEqual(["cap:eq:mega"]);

    // A reload: unmount, drop the module caches, mount again on the same URL.
    first.unmount();
    resetFilterSchemaCache();
    resetFiltersDocumentCache();
    api.selects.length = 0;
    render(<ScreenerScreen assetClass="stock" symbol={null} onSelectSymbol={vi.fn()} detail={null} />);

    await waitFor(() => expect(api.selects.length).toBeGreaterThan(0));
    const body = api.selects.at(-1);
    expect(body?.columns).toHaveLength(13);
    expect(body?.criteria.filters).toEqual([{ key: "cap", op: "eq", value: "mega" }]);
    expect(screen.queryByText(/Ignored 1 filter/)).toBeNull();
  });

  it("surfaces a 403 premium_required as a message, never as an empty table", async () => {
    stubApi(() =>
      json(
        {
          error: "premium_required",
          errorMessage:
            "premium_required: Market cap is part of the Pro plan. Upgrade to screen on it.",
        },
        403,
      ),
    );
    renderScreen();

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("The screen request failed (403)");
    expect(alert.textContent).toContain("premium_required");
    expect(alert.textContent).toContain("Pro plan");
    // No rows, no "0 results" table: the refusal is the answer.
    expect(document.querySelectorAll("tr[data-symbol]")).toHaveLength(0);
    expect(screen.queryByTestId("chip-bar")).toBeNull();
  });

  it("sends the signal the header chose, and only when one is chosen", async () => {
    const user = userEvent.setup();
    const api = stubApi();
    renderScreen();

    await waitFor(() => expect(api.selects).toHaveLength(1));
    expect(api.selects[0]).not.toHaveProperty("signal");

    await user.selectOptions(screen.getByTestId("signal-select"), "top-gainers");
    await waitFor(() => expect(api.selects).toHaveLength(2));
    expect(api.selects[1]?.signal).toBe("top-gainers");
    expect(new URLSearchParams(window.location.search).get("signal")).toBe("top-gainers");

    await user.selectOptions(screen.getByTestId("signal-select"), "");
    await waitFor(() => expect(api.selects).toHaveLength(3));
    expect(api.selects[2]).not.toHaveProperty("signal");
    expect(new URLSearchParams(window.location.search).get("signal")).toBeNull();
  });

  it("saves the reader's own column set and puts it back after a tab changed the columns", async () => {
    const user = userEvent.setup();
    const api = stubApi();
    renderScreen();

    await waitFor(() => expect(api.selects).toHaveLength(1));
    const savedColumns = api.selects[0]!.columns;

    // Save the columns on screen under a name.
    await user.click(screen.getByTestId("manage-column-presets"));
    const dialog = screen.getByRole("dialog", { name: "Column presets" });
    const field = within(dialog).getByRole("textbox", { name: "Preset name" });
    await user.clear(field);
    await user.type(field, "My screen");
    await user.click(within(dialog).getByRole("button", { name: "Save preset" }));
    await user.click(within(dialog).getByRole("button", { name: "Close" }));

    const select = screen.getByTestId("columns-select") as HTMLSelectElement;
    expect(select.value).not.toBe("my-stock-presets");

    // A built-in tab replaces the columns; the select stops claiming the set.
    await user.click(screen.getByTestId("preset-valuation"));
    await waitFor(() => expect(api.selects).toHaveLength(2));
    expect(select.value).toBe("my-stock-presets");

    // Picking the saved set writes its columns back through ?cols=.
    const savedId = [...select.options].find((option) => option.textContent?.includes("My screen"))!
      .value;
    await user.selectOptions(select, savedId);
    await waitFor(() => expect(api.selects).toHaveLength(3));
    expect(api.selects[2]?.columns).toEqual(savedColumns);
    // Applied to the view on screen (the tab switched it to `value`), and
    // spelled in the URL so a reload reproduces it.
    expect(new URLSearchParams(window.location.search).get("cols")).toContain("value:");
  });

  it("keeps the screen when the filter document fails, and says so in the panel", async () => {
    const user = userEvent.setup();
    const selects: WireBody[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url === "/screens/select/schema") return json(filterSchemaWire());
        if (url === "/screens/filters") return json({ errorMessage: "filter store offline" }, 503);
        if (url === "/screens/select" && init?.method === "POST") {
          selects.push(JSON.parse(String(init.body)) as WireBody);
          return json(page(20));
        }
        return json({ errorMessage: `no route for ${url}` }, 404);
      }),
    );

    renderScreen();

    // The grid is not held hostage by the second document: rows arrive, and the
    // panel is the only thing that reports the failure.
    await waitFor(() => expect(selects).toHaveLength(1));
    expect(await screen.findByTestId("range")).toBeTruthy();

    await user.click(screen.getByTestId("filter-button"));
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toContain("The filter list did not load");
    expect(alert.textContent).toContain("filter store offline");
    expect(within(alert).getByRole("button", { name: "Retry" })).toBeTruthy();
    expect(document.querySelectorAll('[data-testid^="control-"]')).toHaveLength(0);
  });
});
