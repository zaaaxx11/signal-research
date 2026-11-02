import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "@/app";
import { clearPageCache } from "@/data/screens";
import { filterSchemaWire } from "@/filters/__fixtures__/schema";
import { resetFilterSchemaCache } from "@/filters/schema";

import { filterDocumentWire } from "./__fixtures__/filters";
import { resetFiltersDocumentCache } from "./filtersDocument";

/**
 * The table's headers are the sort control.
 *
 * One state — the request's `criteria.sort` — has two views: the header the
 * reader pressed (which carries `aria-sort` and an arrow) and the masthead's
 * Order by select. Neither touches a filter. The ordering is the server's: the
 * assertion is always on the wire body, never on the order of the rows this
 * page happens to hold.
 *
 * The view below is the real default column set, served as a catalogue, so a
 * press on "Country" is a press on a column that actually exists.
 */

const COLUMNS = [
  "company",
  "sector",
  "industry",
  "country",
  "market_cap",
  "pe",
  "price",
  "change",
  "volume",
  "exchange",
  "dividend_yield",
  "rsi",
];

/** The served schema, with one view that draws the default set. */
function schemaWire(): Record<string, unknown> {
  const wire = filterSchemaWire();
  wire["views"] = [{ name: "today", label: "Today", job: "What is moving right now?", columns: COLUMNS }];
  return wire;
}

interface WireBody {
  columns: string[];
  criteria: { offset: number; limit: number; sort?: string; filters: unknown[] };
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function page() {
  return {
    as_of: "2026-11-01T10:00:00Z",
    limit: 20,
    total: 5_600,
    universe_total: 5_600,
    returned: 20,
    columns: COLUMNS,
    rows: Array.from({ length: 20 }, (_, index) => ({
      symbol: `S${String(index).padStart(3, "0")}`,
      company: `Company ${index}`,
      sector: "Technology",
      industry: "Software",
      country: "USA",
      market_cap: 1_000_000 * (index + 1),
      pe: 10 + index,
      price: 100 + index,
      change: 1.5,
      volume: 1_000 * (index + 1),
      exchange: "XNAS",
      dividend_yield: 0.5,
      rsi: 55,
    })),
    budget: { truncated: false, dropped_fields: [] },
  };
}

function stubApi() {
  const selects: WireBody[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url === "/screens/select/schema") return json(schemaWire());
    if (url === "/screens/filters") return json(filterDocumentWire());
    if (url === "/screens/select" && init?.method === "POST") {
      selects.push(JSON.parse(String(init.body)) as WireBody);
      return json(page());
    }
    return json({ errorMessage: `no route for ${url}` }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return { selects, fetchMock };
}

const header = (key: string) =>
  screen.getByTestId("grid-table").querySelector<HTMLElement>(`thead th[data-key="${key}"]`);

beforeEach(() => {
  // jsdom has no layout: without a measured container the table draws its
  // "not measured yet" branch and no header exists to press.
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

async function renderScreen() {
  const api = stubApi();
  render(<App />);
  await waitFor(() => expect(api.selects.length).toBe(1));
  await screen.findByTestId("grid-table");
  return api;
}

describe("a header press is one server-side sort", () => {
  it("sends sort=country, highest first, in exactly one request", async () => {
    const api = await renderScreen();
    const user = userEvent.setup();
    const before = api.selects.length;

    await user.click(screen.getByTestId("sort-country"));

    await waitFor(() => expect(api.selects.length).toBe(before + 1));
    const body = api.selects.at(-1)!;
    expect(body.criteria.sort).toBe("country");
    expect(body.criteria.offset).toBe(0);
    // The filter list is untouched by an ordering change.
    expect(body.criteria.filters).toEqual([]);
    expect(header("country")?.getAttribute("aria-sort")).toBe("descending");
    expect(screen.getByTestId("sort-arrow-country").textContent).toBe("▾");
  });

  it("sends -country, lowest first, when the same header is pressed again", async () => {
    const api = await renderScreen();
    const user = userEvent.setup();

    await user.click(screen.getByTestId("sort-country"));
    await waitFor(() => expect(api.selects.at(-1)?.criteria.sort).toBe("country"));

    await user.click(screen.getByTestId("sort-country"));
    await waitFor(() => expect(api.selects.at(-1)?.criteria.sort).toBe("-country"));
    expect(header("country")?.getAttribute("aria-sort")).toBe("ascending");
    expect(screen.getByTestId("sort-arrow-country").textContent).toBe("▴");

    // … and back again: the two directions toggle, they do not cycle through a
    // third state. The first ordering is a page this tab has already seen, so
    // the cache is emptied to keep the assertion about the request.
    clearPageCache();
    await user.click(screen.getByTestId("sort-country"));
    await waitFor(() => expect(api.selects.at(-1)?.criteria.sort).toBe("country"));
    expect(header("country")?.getAttribute("aria-sort")).toBe("descending");
  });
});

describe("the header and the Order by select are one state", () => {
  it("moves the select when a header is pressed", async () => {
    await renderScreen();
    const user = userEvent.setup();

    const order = screen.getByTestId("order-select") as HTMLSelectElement;
    expect(order.value).toBe("market_cap");

    await user.click(screen.getByTestId("sort-country"));

    await waitFor(() => expect(order.value).toBe("country"));
    expect([...order.options].map((option) => option.value)).toContain("country");
    expect([...order.options].find((option) => option.value === "country")?.textContent).toBe(
      "Country",
    );
  });

  it("moves aria-sort when the select changes", async () => {
    const api = await renderScreen();
    const user = userEvent.setup();

    const order = screen.getByTestId("order-select") as HTMLSelectElement;
    await user.selectOptions(order, "sector");

    await waitFor(() => expect(api.selects.at(-1)?.criteria.sort).toBe("sector"));
    expect(header("sector")?.getAttribute("aria-sort")).toBe("descending");
    expect(screen.getByTestId("sort-arrow-sector")).toBeTruthy();
    // One marked header, never two.
    expect(header("country")?.getAttribute("aria-sort")).toBe("none");
    const marked = [
      ...screen.getByTestId("grid-table").querySelectorAll("thead th[aria-sort]"),
    ].filter((node) => node.getAttribute("aria-sort") !== "none");
    expect(marked).toHaveLength(1);
    expect(marked[0]?.getAttribute("data-key")).toBe("sector");
  });
});

describe("sort survives a page change", () => {
  it("carries the ordering and the arrow to the next page", async () => {
    const api = await renderScreen();
    const user = userEvent.setup();

    await user.click(screen.getByTestId("sort-country"));
    await waitFor(() => expect(api.selects.at(-1)?.criteria.sort).toBe("country"));

    await user.click(screen.getByRole("button", { name: "Next page" }));

    await waitFor(() => expect(api.selects.at(-1)?.criteria.offset).toBe(20));
    const body = api.selects.at(-1)!;
    expect(body.criteria.sort).toBe("country");
    // The header keeps saying which order this page is in.
    expect(header("country")?.getAttribute("aria-sort")).toBe("descending");
    expect((screen.getByTestId("order-select") as HTMLSelectElement).value).toBe("country");
    // The page position moved; the ordering did not, and no filter came along.
    expect(screen.getByTestId("range").textContent).toBe("2/5,600 Total");
    expect(body.criteria.filters).toEqual([]);
  });
});

describe("the default column set", () => {
  it("draws the eleven mandatory columns, in order", async () => {
    await renderScreen();

    const headers = [...screen.getByTestId("grid-table").querySelectorAll("thead th")];
    // The keys, in render order: the row number, the pinned ticker lane, then
    // the mandatory columns. No checkbox lane stands in front of them.
    expect(headers.map((node) => node.getAttribute("data-key"))).toEqual([
      "no",
      "identity",
      "company",
      "sector",
      "industry",
      "country",
      "market_cap",
      "pe",
      "price",
      "change",
      "volume",
      // The optional extras the catalogue backs follow the mandatory eleven.
      "exchange",
      "dividend_yield",
      "rsi",
    ]);
    // The full name each header carries (a narrow column may shorten the
    // painted label, never the column it stands for).
    expect(headers.slice(0, 11).map((node) => (node.getAttribute("title") ?? "").toLowerCase())).toEqual([
      "no",
      "ticker",
      "company",
      "sector",
      "industry",
      "country",
      "market cap",
      "p/e",
      "price",
      "change %",
      "volume",
    ]);
  });

  it("never still asks for the client-drawn columns", async () => {
    const api = await renderScreen();
    const columns = api.selects[0]?.columns ?? [];
    expect(columns).not.toContain("no");
    // `ticker` is the pinned identity lane; a second request column would draw
    // the symbol twice.
    expect(columns).not.toContain("ticker");
    expect(columns.slice(0, 6)).toEqual([
      "company",
      "sector",
      "industry",
      "country",
      "market_cap",
      "pe",
    ]);
  });

  it("numbers the rows by page, not from one", async () => {
    const user = userEvent.setup();
    await renderScreen();

    const numbers = () =>
      [
        ...screen
          .getByTestId("grid-table")
          .querySelectorAll("tbody tr[data-symbol] td[data-row-number]"),
      ].map((cell) => cell.textContent);
    expect(numbers()[0]).toBe("1");
    expect(numbers()[19]).toBe("20");

    await user.click(screen.getByRole("button", { name: "Next page" }));
    await waitFor(() => expect(screen.getByTestId("range").textContent).toBe("2/5,600 Total"));
    expect(numbers()[0]).toBe("21");
    expect(numbers()[19]).toBe("40");
  });
});

describe("the table is on the page, not in a scroll box", () => {
  it("has no inner vertical scroll and renders every row of the page at once", async () => {
    await renderScreen();

    const wrapper = screen.getByTestId("grid-scroll");
    expect(wrapper.className).not.toContain("overflow-auto");
    expect(wrapper.className).not.toContain("overflow-y-auto");
    expect(wrapper.style.maxHeight).toBe("");
    expect(wrapper.getAttribute("data-scroll")).toBe("page");

    // Every row of the page is in the DOM: the pager is what shortens a table,
    // never a clipped viewport.
    expect(wrapper.querySelectorAll("tbody tr[data-symbol]")).toHaveLength(20);
    expect(screen.getByTestId("pager")).toBeTruthy();
    expect(within(wrapper).getByTestId("grid-table")).toBeTruthy();
  });
});
