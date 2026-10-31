import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import * as React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "@/app";
import { LIVE_BREADTH } from "@/breadth/__fixtures__/breadth";
import { filterSchemaFixture, filterSchemaWire } from "@/filters/__fixtures__/schema";
import { buildRegistry } from "@/filters/registry";
import { resetFilterSchemaCache } from "@/filters/schema";
import { parseScreenState } from "@/filters/urlState";
import type { ActiveFilter } from "@/filters/types";
import type { ScreenRow, ViewDefinition } from "@/data/screens";

import { ScreenPanel } from "./ScreenPanel";
import { sectorNamesFromReport, withSector } from "./sectors";

/**
 * The sector choice, at the panel and at the shell.
 *
 * The operator's question was "per sector it has its own column / is there a
 * choice?" — so the menu is the market's sector list, the answer is one `sec`
 * equality, and the state travels: chip, URL, request body, one filter list.
 *
 * What is proved here:
 *
 *   * 21 options plus "All sectors", and the source of those 21 is printed;
 *   * picking one sends `sec eq <label>` **unchanged**, resets the page to 1,
 *     keeps the reader's other filters, and shows a removable chip;
 *   * clearing the chip restores the unfiltered panel (minus nothing else);
 *   * when breadth is unavailable the menu falls back to the rows on screen and
 *     says so;
 *   * the choice survives a reload through the address bar.
 */

const registry = buildRegistry(filterSchemaFixture());
const MARKET_SECTORS = sectorNamesFromReport(LIVE_BREADTH);
const CHOSEN = "Electronic Technology";

const VIEW: ViewDefinition = {
  name: "today",
  label: "Today",
  job: "What is moving right now?",
  columns: ["price"],
};

function rows(count: number, prefix = "A", sector?: string): ScreenRow[] {
  return Array.from({ length: count }, (_, index) => ({
    symbol: `${prefix}${index}`,
    price: 100 + index,
    ...(sector ? { sector } : {}),
  }));
}

function answer(list: ScreenRow[], total = 5_600): Response {
  return new Response(
    JSON.stringify({
      as_of: "2026-10-31T01:28:01Z",
      limit: 100,
      total,
      universe_total: 5_600,
      returned: list.length,
      columns: VIEW.columns,
      rows: list,
      budget: { truncated: false, dropped_fields: [] },
    }),
    { status: 200, headers: { "content-type": "application/json" } },
  );
}

function bodyOf(call: unknown): Record<string, unknown> {
  const [, init] = call as [string, RequestInit];
  return JSON.parse(String(init.body)) as Record<string, unknown>;
}

function filtersOf(call: unknown): unknown {
  return (bodyOf(call)["criteria"] as Record<string, unknown>)["filters"];
}

afterEach(() => {
  vi.unstubAllGlobals();
});

/**
 * The shell's shape, in miniature: one filter list, one page, and a sector
 * change that replaces its predicate and turns the page back to the first —
 * exactly what `useScreenState().setFilters` does.
 */
function Harness({
  initialFilters = [],
  initialPage = 0,
  sectorOptions,
  sectorSource,
}: {
  initialFilters?: ActiveFilter[];
  initialPage?: number;
  sectorOptions?: readonly string[];
  sectorSource?: "breadth" | "loading" | "unavailable";
}) {
  const [filters, setFilters] = React.useState<ActiveFilter[]>(initialFilters);
  const [page, setPage] = React.useState(initialPage);

  return (
    <ScreenPanel
      view={VIEW}
      selectedSymbol={null}
      onActivate={vi.fn()}
      onClear={vi.fn()}
      containerWidth={1280}
      page={page}
      onPageChange={setPage}
      filters={filters}
      registry={registry}
      onRemoveFilter={(id) => setFilters((current) => current.filter((filter) => filter.id !== id))}
      onClearFilters={() => setFilters([])}
      sectorOptions={sectorOptions}
      sectorSource={sectorSource}
      onSectorChange={(name) => {
        setFilters((current) => withSector(current, name));
        setPage(0);
      }}
    />
  );
}

describe("the sector menu", () => {
  it("lists All sectors plus the 21 sectors breadth counted, and names its source", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(answer(rows(100))));
    render(<Harness sectorOptions={MARKET_SECTORS} sectorSource="breadth" />);

    const select = (await screen.findByLabelText("Sector")) as HTMLSelectElement;
    const options = [...select.options].map((option) => option.value);

    expect(options).toHaveLength(22);
    expect(options[0]).toBe("");
    expect(select.options[0]?.textContent).toBe("All sectors");
    expect(options.slice(1)).toEqual(MARKET_SECTORS);
    expect(options).toContain(CHOSEN);

    expect(screen.getByTestId("sector-source").textContent).toBe("from breadth");
    // The count is the menu itself: 21 labels, named in the tooltip.
    expect(screen.getByTestId("sector-source").getAttribute("title")).toContain("21 sector names");
  });

  it("(c) does not offer a name the payload could not give", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(answer(rows(100))));
    render(
      <Harness
        sectorOptions={["Finance", "", "   ", CHOSEN]}
        sectorSource="breadth"
      />,
    );

    const select = (await screen.findByLabelText("Sector")) as HTMLSelectElement;
    const options = [...select.options].map((option) => option.value);
    expect(options).toEqual(["", "Finance", CHOSEN]);
    expect(screen.getByTestId("sector-source").textContent).toBe("from breadth");
  });

  it("says when the names are the rows on screen rather than the market", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        answer([
          { symbol: "AAPL", price: 100, sector: "Electronic Technology" },
          { symbol: "JPM", price: 200, sector: "Finance" },
          { symbol: "X", price: 1, sector: null },
        ]),
      ),
    );
    render(<Harness sectorSource="unavailable" />);

    const select = (await screen.findByLabelText("Sector")) as HTMLSelectElement;
    expect([...select.options].map((option) => option.value)).toEqual([
      "",
      "Electronic Technology",
      "Finance",
    ]);
    await waitFor(() =>
      expect(screen.getByTestId("sector-source").textContent).toBe("from rows on screen"),
    );
    expect(screen.getByTestId("sector-source").getAttribute("data-source")).toBe("rows");
  });
});

describe("choosing a sector", () => {
  it("sends sec eq the label unchanged, resets the page, keeps other filters, shows the chip", async () => {
    const user = userEvent.setup();
    const cap: ActiveFilter = { id: "f1", key: "cap", op: "gte", value: 2_000_000_000 };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(answer(rows(100)))
      .mockResolvedValueOnce(answer(rows(100)));
    vi.stubGlobal("fetch", fetchMock);

    render(
      <Harness
        initialFilters={[cap]}
        initialPage={3}
        sectorOptions={MARKET_SECTORS}
        sectorSource="breadth"
      />,
    );

    await waitFor(() => expect(screen.getByTestId("range").textContent).toBe("301–400 of 5,600"));
    expect(filtersOf(fetchMock.mock.calls[0])).toEqual([
      { key: "cap", op: "gte", value: 2_000_000_000 },
    ]);

    await user.selectOptions(screen.getByLabelText("Sector"), CHOSEN);

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));

    const criteria = bodyOf(fetchMock.mock.calls[1])["criteria"] as {
      offset: number;
      filters: unknown[];
    };
    // Page 1 again, the sector exactly as the API spells it, and the reader's
    // market-cap filter is still there.
    expect(criteria.offset).toBe(0);
    expect(criteria.filters).toEqual([
      { key: "cap", op: "gte", value: 2_000_000_000 },
      { key: "sec", op: "eq", value: CHOSEN },
    ]);

    // The chip: one predicate, one chip, in the operator's words.
    const chip = screen.getByTestId("sector-chip");
    expect(chip.textContent?.replace("×", "").trim()).toBe(`Sector: ${CHOSEN}`);
    expect(screen.getByTestId("sector-chip-name").textContent).toBe(CHOSEN);

    // The chip bar above the table draws the *other* predicates — the sector is
    // not printed twice.
    const bar = screen.getByTestId("chip-bar");
    expect(bar.textContent).toContain("Market Cap");
    expect(bar.textContent).not.toContain(CHOSEN);

    // And the menu agrees with the chip.
    expect((screen.getByLabelText("Sector") as HTMLSelectElement).value).toBe(CHOSEN);
  });

  it("survives a reload: the URL's spelling is the predicate the panel sends", async () => {
    // The address bar a reload comes back to: page 1, one market-cap filter,
    // and the sector spelled with an encoded separator and a form-encoded space.
    const route =
      "?view=today&sort=market_cap&page=1&f=cap%3Agte%3A2000000000&f=sec%3Aeq%3AElectronic+Technology";
    const parsed = parseScreenState(route, registry, [VIEW.name]);
    expect(parsed.dropped).toEqual([]);

    const fetchMock = vi.fn().mockResolvedValue(answer(rows(100)));
    vi.stubGlobal("fetch", fetchMock);

    render(
      <Harness
        initialFilters={parsed.state.filters}
        sectorOptions={MARKET_SECTORS}
        sectorSource="breadth"
      />,
    );

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(filtersOf(fetchMock.mock.calls[0])).toEqual([
      { key: "cap", op: "gte", value: 2_000_000_000 },
      { key: "sec", op: "eq", value: CHOSEN },
    ]);
    expect(screen.getByTestId("sector-chip-name").textContent).toBe(CHOSEN);
  });
  it("still offers — and still sends — a sector the scan no longer lists", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(answer(rows(10, "D", "Delisted Sector"), 10));
    vi.stubGlobal("fetch", fetchMock);

    render(
      <Harness
        initialFilters={[{ id: "f1", key: "sec", op: "eq", value: "Delisted Sector" }]}
        sectorOptions={MARKET_SECTORS}
        sectorSource="breadth"
      />,
    );

    const select = (await screen.findByLabelText("Sector")) as HTMLSelectElement;
    // The chosen name is added to the menu, never dropped for want of a slot:
    // a link to a sector the last scan lost must still show what it is doing.
    expect(select.value).toBe("Delisted Sector");
    expect(select.options).toHaveLength(23);
    expect(screen.getByTestId("sector-chip-name").textContent).toBe("Delisted Sector");
    expect(filtersOf(fetchMock.mock.calls[0])).toEqual([
      { key: "sec", op: "eq", value: "Delisted Sector" },
    ]);
  });
});

describe("clearing the sector", () => {
  it("removes the sector predicate and restores the unfiltered panel", async () => {
    const user = userEvent.setup();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(answer(rows(40, "B", "Finance"), 40))
      .mockResolvedValueOnce(answer(rows(100)));
    vi.stubGlobal("fetch", fetchMock);

    render(
      <Harness
        initialFilters={[{ id: "f1", key: "sec", op: "eq", value: "Finance" }]}
        sectorOptions={MARKET_SECTORS}
        sectorSource="breadth"
      />,
    );

    await waitFor(() => expect(screen.getByTestId("sector-chip-name").textContent).toBe("Finance"));

    await user.click(screen.getByRole("button", { name: "Clear sector filter: Finance" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(filtersOf(fetchMock.mock.calls[1])).toEqual([]);
    expect(screen.queryByTestId("sector-chip")).toBeNull();
    expect((screen.getByLabelText("Sector") as HTMLSelectElement).value).toBe("");
    await waitFor(() => expect(screen.getByTestId("range").textContent).toBe("1–100 of 5,600"));
  });

  it("(d) leaves the reader's other filters in place", async () => {
    const user = userEvent.setup();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(answer(rows(40, "B", "Finance"), 40))
      .mockResolvedValueOnce(answer(rows(60, "C"), 60));
    vi.stubGlobal("fetch", fetchMock);

    render(
      <Harness
        initialFilters={[
          { id: "f1", key: "cap", op: "gte", value: 2_000_000_000 },
          { id: "f2", key: "sec", op: "eq", value: "Finance" },
        ]}
        sectorOptions={MARKET_SECTORS}
        sectorSource="breadth"
      />,
    );

    await waitFor(() => expect(screen.getByTestId("sector-chip")).toBeTruthy());
    await user.click(screen.getByRole("button", { name: "Clear sector filter: Finance" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(filtersOf(fetchMock.mock.calls[1])).toEqual([
      { key: "cap", op: "gte", value: 2_000_000_000 },
    ]);
    expect(within(screen.getByTestId("chip-bar")).getByText(/Market Cap/)).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// The shell: one state, one URL, one request
// ---------------------------------------------------------------------------

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

function stubApp() {
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
    if (url === "/screens/select" && init?.method === "POST") {
      const body = JSON.parse(String(init.body)) as {
        columns: string[];
        criteria: { limit: number };
      };
      selects.push(body as unknown as Record<string, unknown>);
      const list = Array.from({ length: body.criteria.limit }, (_, index) => ({
        symbol: `S${index}`,
        column_0: index,
      }));
      return json({
        as_of: "2026-10-31T01:28:01Z",
        limit: body.criteria.limit,
        total: 5_600,
        universe_total: 5_600,
        returned: list.length,
        columns: body.columns,
        rows: list,
        budget: { truncated: false, dropped_fields: [] },
      });
    }
    throw new Error(`unexpected fetch ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return { selects, fetchMock };
}

describe("the sector choice in the shell", () => {
  beforeEach(() => {
    resetFilterSchemaCache();
    window.history.replaceState(null, "", "/app/");
  });

  it("writes the sector to the URL at page 1 and reads it back on a reload", async () => {
    const api = stubApp();
    const user = userEvent.setup();

    const first = render(<App />);
    await waitFor(
      () => expect(screen.getByTestId("range").textContent).toBe("1–100 of 5,600"),
      { timeout: 3_000 },
    );

    // The menu is the served breadth document's 21 labels.
    const select = (await screen.findByLabelText("Sector")) as HTMLSelectElement;
    expect(select.options).toHaveLength(22);
    expect(screen.getByTestId("sector-source").textContent).toBe("from breadth");

    api.selects.length = 0;
    await user.selectOptions(select, CHOSEN);

    await waitFor(() => expect(api.selects.length).toBe(1), { timeout: 3_000 });
    const body = api.selects[0] as { criteria: { offset: number; filters: unknown[] } };
    expect(body.criteria.offset).toBe(0);
    expect(body.criteria.filters).toEqual([{ key: "sec", op: "eq", value: CHOSEN }]);

    const params = new URLSearchParams(window.location.search);
    expect(params.get("page")).toBe("1");
    expect(params.getAll("f")).toEqual([`sec:eq:${CHOSEN}`]);
    expect(screen.getByTestId("sector-chip-name").textContent).toBe(CHOSEN);

    // A reload is a fresh mount against the same address bar.
    first.unmount();
    resetFilterSchemaCache();
    api.selects.length = 0;
    render(<App />);

    await waitFor(() => expect(api.selects.length).toBe(1));
    expect((api.selects[0] as { criteria: { filters: unknown[] } }).criteria.filters).toEqual([
      { key: "sec", op: "eq", value: CHOSEN },
    ]);
    expect(screen.getByTestId("sector-chip-name").textContent).toBe(CHOSEN);
    expect((screen.getByLabelText("Sector") as HTMLSelectElement).value).toBe(CHOSEN);
  });
});
