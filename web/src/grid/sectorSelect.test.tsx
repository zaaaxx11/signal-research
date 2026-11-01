import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "@/app";
import { filterSchemaWire } from "@/filters/__fixtures__/schema";
import { resetFilterSchemaCache } from "@/filters/schema";

import { filterDocumentWire } from "@/screener/__fixtures__/filters";
import { resetFiltersDocumentCache } from "@/screener/filtersDocument";

/**
 * The sector predicate, after the toolbar's own menu was removed.
 *
 * The panel used to carry a 21-name `<select>` fed by `GET /market/breadth`,
 * with a source badge beside it. Both are gone: the header's filter document is
 * the only vocabulary, and it publishes the Sector control with no values yet
 * (`options_source: /market/sectors`). So the honest contract is:
 *
 *   * the removed select and its badge stay removed;
 *   * the header still draws the Sector control, and says why it is empty
 *     instead of offering an empty menu that looks broken;
 *   * a `sec` predicate that arrives in a link — the one way to name a sector
 *     this build cannot list — is sent unchanged, chipped, and survives a
 *     reload;
 *   * removing its chip leaves the reader's other filters alone.
 */

interface WireBody {
  criteria: { offset: number; filters: unknown[] };
}

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
    columns: ["price"],
    rows: Array.from({ length: count }, (_, index) => ({
      symbol: `S${String(index).padStart(3, "0")}`,
      price: 100 + index,
    })),
    budget: { truncated: false, dropped_fields: [] },
  };
}

function stubApi() {
  const selects: WireBody[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url === "/screens/select/schema") return json(filterSchemaWire());
    if (url === "/screens/filters") return json(filterDocumentWire());
    if (url === "/screens/select" && init?.method === "POST") {
      selects.push(JSON.parse(String(init.body)) as WireBody);
      return json(page(20));
    }
    return json({ errorMessage: `no route for ${url}` }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return { selects, fetchMock };
}

const CHOSEN = "Electronic Technology";

beforeEach(() => {
  resetFilterSchemaCache();
  resetFiltersDocumentCache();
  window.history.replaceState(null, "", "/app/screener");
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("the sector control that was removed", () => {
  it("does not mount a toolbar select or a source badge", async () => {
    stubApi();
    render(<App />);
    await waitFor(() => expect(screen.getByTestId("range")).toBeTruthy());

    expect(screen.queryByLabelText("Sector")).toBeNull();
    expect(screen.queryByTestId("sector-source")).toBeNull();
    expect(screen.queryByTestId("sector-chip")).toBeNull();
  });
});

describe("the header's sector control", () => {
  it("is drawn from the document, and says why it has no values", async () => {
    stubApi();
    const user = userEvent.setup();
    render(<App />);
    await waitFor(() => expect(screen.getByTestId("range")).toBeTruthy());

    await user.click(screen.getByTestId("filter-button"));
    const control = await screen.findByTestId("control-sec");

    const select = within(control).getByRole("combobox") as HTMLSelectElement;
    expect(select.disabled).toBe(true);
    expect(select.textContent).toContain("No values published");
    // Not an empty menu: the closed control carries the reason it is closed.
    expect(select.getAttribute("title")).toContain("/market/sectors");
    // One field per control, named — never a second, bespoke sector menu.
    expect(within(control).getByText("Sector")).toBeTruthy();
  });
});

describe("a sector that arrives in a link", () => {
  it("is sent unchanged, chipped and restored on reload", async () => {
    const api = stubApi();
    window.history.replaceState(
      null,
      "",
      "/app/screener?f=cap%3Agte%3A2000000000&f=sec%3Aeq%3AElectronic+Technology",
    );

    const first = render(<App />);

    await waitFor(() => expect(api.selects.length).toBe(1));
    expect(api.selects[0]?.criteria.filters).toEqual([
      { key: "cap", op: "gte", value: 2_000_000_000 },
      { key: "sec", op: "eq", value: CHOSEN },
    ]);

    const chips = screen.getByTestId("chip-bar");
    expect(chips.textContent).toContain("Sector");
    expect(chips.textContent).toContain(CHOSEN);

    // A reload is a fresh mount against the same address bar.
    first.unmount();
    resetFilterSchemaCache();
    resetFiltersDocumentCache();
    api.selects.length = 0;
    render(<App />);

    await waitFor(() => expect(api.selects.length).toBe(1));
    expect(api.selects[0]?.criteria.filters).toEqual([
      { key: "cap", op: "gte", value: 2_000_000_000 },
      { key: "sec", op: "eq", value: CHOSEN },
    ]);
    expect(screen.getByTestId("chip-bar").textContent).toContain(CHOSEN);
  });

  it("still sends a name the current scan no longer lists", async () => {
    const api = stubApi();
    window.history.replaceState(null, "", "/app/screener?f=sec%3Aeq%3ADelisted+Sector");

    render(<App />);

    await waitFor(() => expect(api.selects.length).toBe(1));
    expect(api.selects[0]?.criteria.filters).toEqual([
      { key: "sec", op: "eq", value: "Delisted Sector" },
    ]);
    // The chip prints the name it cannot offer, never a blank.
    expect(screen.getByTestId("chip-bar").textContent).toContain("Delisted Sector");
    expect(screen.queryByText(/Ignored 1 filter/)).toBeNull();
  });
});

describe("clearing the sector", () => {
  it("removes the predicate and leaves the reader's other filters in place", async () => {
    const api = stubApi();
    const user = userEvent.setup();
    window.history.replaceState(
      null,
      "",
      "/app/screener?f=cap%3Agte%3A2000000000&f=sec%3Aeq%3AFinance",
    );

    render(<App />);
    await waitFor(() => expect(api.selects.length).toBe(1));
    expect(screen.getByTestId("chip-bar").textContent).toContain("Finance");

    await user.click(
      within(screen.getByTestId("chip-bar")).getByRole("button", { name: /Remove filter: Sector/ }),
    );

    await waitFor(() => expect(api.selects.length).toBe(2));
    expect(api.selects[1]?.criteria.filters).toEqual([
      { key: "cap", op: "gte", value: 2_000_000_000 },
    ]);
    expect(within(screen.getByTestId("chip-bar")).getByText(/Market Cap/)).toBeTruthy();
    expect(screen.getByTestId("chip-bar").textContent).not.toContain("Finance");
  });
});
