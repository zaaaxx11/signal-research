import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import * as React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "@/app";
import { LIVE_BREADTH } from "@/breadth/__fixtures__/breadth";
import { filterSchemaFixture, filterSchemaWire } from "@/filters/__fixtures__/schema";
import { buildRegistry } from "@/filters/registry";
import { resetFilterSchemaCache } from "@/filters/schema";
import { encodeScreenState, parseScreenState } from "@/filters/urlState";
import type { ActiveFilter } from "@/filters/types";
import type { ScreenRow, ViewDefinition } from "@/data/screens";

import { ScreenPanel } from "./ScreenPanel";
import { withExchange, type VenueBucket } from "./exchanges";

/**
 * The exchange control, at the panel and at the shell.
 *
 * What is proved here:
 *
 *   * the menu lists the market's venues with label, count and code, and names
 *     the population they came from;
 *   * choosing two venues sends **one** `in` predicate, resets the page to 1,
 *     keeps the reader's other filters, and shows one chip in the labels;
 *   * unchecking to one becomes `eq`, unchecking the last removes the predicate
 *     and the chip;
 *   * a link's code arrives checked — even one the market list no longer has —
 *     and is still sent byte-for-byte;
 *   * the keyboard is the ARIA multi-select: one tab stop, ArrowDown opens at
 *     the first checked option, Space toggles and stays open, Enter toggles and
 *     closes, Escape closes and returns focus, Tab leaves, letters type ahead;
 *   * the choice survives a reload through the address bar, and the shell makes
 *     exactly one `GET /market/breadth` and one `GET /market/exchanges`.
 */

const registry = buildRegistry(filterSchemaFixture());

const VIEW: ViewDefinition = {
  name: "today",
  label: "Today",
  job: "What is moving right now?",
  columns: ["price", "exchange"],
};

/** The four venues `GET /market/exchanges` counts for the US universe. */
const MARKET_VENUES: VenueBucket[] = [
  { code: "XNAS", count: 3_308 },
  { code: "XNYS", count: 2_009 },
  { code: "XASE", count: 258 },
  { code: "XCBO", count: 1 },
];

const CAP: ActiveFilter = { id: "f-cap", key: "cap", op: "gte", value: 2_000_000_000 };

function rows(count: number, prefix = "A", exchange?: string): ScreenRow[] {
  return Array.from({ length: count }, (_, index) => ({
    symbol: `${prefix}${index}`,
    price: 100 + index,
    ...(exchange ? { exchange } : {}),
  }));
}

function answer(list: ScreenRow[], total = 5_600): Response {
  return new Response(
    JSON.stringify({
      as_of: "2026-10-31T01:28:01Z",
      limit: 20,
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

function criteriaOf(call: unknown): { offset: number; filters: unknown[] } {
  return bodyOf(call)["criteria"] as { offset: number; filters: unknown[] };
}

function filtersOf(call: unknown): unknown {
  return criteriaOf(call).filters;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

/**
 * The shell's shape in miniature: one filter list, one page, and an exchange
 * change that replaces its own predicate and turns the page back to the first —
 * exactly what `useScreenState().setFilters` does.
 */
function Harness({
  initialFilters = [],
  initialPage = 0,
  venues,
  source,
}: {
  initialFilters?: ActiveFilter[];
  initialPage?: number;
  venues?: readonly VenueBucket[];
  source?: "market" | "loading" | "unavailable";
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
      exchangeOptions={venues}
      exchangeSource={source}
      onExchangeChange={(codes) => {
        setFilters((current) => withExchange(current, codes));
        setPage(0);
      }}
    />
  );
}

async function openMenu(testId = "exchange-trigger") {
  const user = userEvent.setup();
  const trigger = await screen.findByTestId(testId);
  await user.click(trigger);
  return { user, trigger, menu: screen.getByRole("listbox", { name: "Exchanges" }) };
}

describe("the exchange menu", () => {
  it("lists the market's venues with label, count and code, and names its source", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(answer(rows(20))));
    render(<Harness venues={MARKET_VENUES} source="market" />);

    const trigger = await screen.findByTestId("exchange-trigger");
    expect(trigger.getAttribute("aria-haspopup")).toBe("listbox");
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
    expect(trigger.textContent).toContain("All exchanges");

    const { menu } = await openMenu();
    expect(menu.getAttribute("aria-multiselectable")).toBe("true");

    const options = within(menu).getAllByRole("option");
    expect(options).toHaveLength(4);
    // US majors first, and each row carries all three facts it must (adversarial
    // reading of "label, count, code"): the name, the population, the wire.
    expect(options[0]?.textContent).toContain("NASDAQ");
    expect(options[0]?.textContent).toContain("3,308");
    expect(options[0]?.textContent).toContain("XNAS");
    expect(options.map((option) => option.getAttribute("data-code"))).toEqual([
      "XNAS",
      "XNYS",
      "XASE",
      "XCBO",
    ]);
    for (const option of options) {
      expect(option.getAttribute("aria-selected")).toBe("false");
    }

    const source = screen.getByTestId("exchange-trigger");
    expect(source.getAttribute("title")).toContain("from /market/exchanges");
    expect(source.getAttribute("data-source")).toBe("market");
  });

  it("falls back to the page's own exchange cells when the route has no list", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        answer([
          { symbol: "AAPL", price: 100, exchange: "XNAS" },
          { symbol: "MSFT", price: 200, exchange: "XNAS" },
          { symbol: "JPM", price: 300, exchange: "XNYS" },
          { symbol: "X", price: 1, exchange: null },
        ]),
      ),
    );
    render(<Harness venues={[]} source="unavailable" />);

    await waitFor(() =>
      expect(screen.getByTestId("exchange-trigger").getAttribute("data-source")).toBe("rows"),
    );
    expect(screen.getByTestId("exchange-trigger").getAttribute("data-source")).toBe("rows");

    const { menu } = await openMenu();
    const options = within(menu).getAllByRole("option");
    expect(options.map((option) => option.getAttribute("data-code"))).toEqual(["XNAS", "XNYS"]);
    // A page count is not a market count: the tooltip says which one it is.
    expect(within(options[0] as HTMLElement).getByTitle(/rows on this page/).textContent).toBe("2");
  });

  it("collapses a blank or duplicated option instead of drawing it twice", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(answer(rows(10))));
    render(
      <Harness
        venues={[
          { code: "XNAS", count: 10 },
          { code: "", count: 5 },
          { code: "XNAS", count: 10 },
        ]}
        source="market"
      />,
    );

    const { menu } = await openMenu();
    expect(within(menu).getAllByRole("option")).toHaveLength(1);
  });

  it("is fixed and clamped to the viewport, not clipped by the panel", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(answer(rows(10))));
    render(<Harness venues={MARKET_VENUES} source="market" />);
    const trigger = await screen.findByTestId("exchange-trigger");

    // A trigger in the bottom-right corner: below it there is no room, so the
    // menu must open upward and pull itself inside the right edge.
    vi.spyOn(trigger, "getBoundingClientRect").mockReturnValue({
      top: 740,
      bottom: 762,
      left: 1_000,
      right: 1_100,
      width: 100,
      height: 22,
      x: 1_000,
      y: 740,
      toJSON: () => ({}),
    } as DOMRect);

    const user = userEvent.setup();
    await user.click(trigger);
    const menu = screen.getByRole("listbox", { name: "Exchanges" });

    expect(menu.style.position).toBe("fixed");
    const top = Number.parseFloat(menu.style.top);
    const left = Number.parseFloat(menu.style.left);
    expect(top).toBeGreaterThanOrEqual(8);
    expect(top + 4 * 26).toBeLessThanOrEqual(window.innerHeight);
    expect(left).toBe(Math.max(8, window.innerWidth - 264 - 8));
    // The panel clips (`overflow-hidden`); a fixed box is not a descendant of
    // that clip, which is the whole reason it is positioned this way.
    expect(menu.parentElement?.className).not.toContain("overflow-hidden");
  });
});

describe("choosing exchanges", () => {
  it("sends one exch in for two venues, at page 1, keeping the other filters", async () => {
    const fetchMock = vi.fn().mockResolvedValue(answer(rows(20)));
    vi.stubGlobal("fetch", fetchMock);

    render(
      <Harness initialFilters={[CAP]} initialPage={3} venues={MARKET_VENUES} source="market" />,
    );

    await waitFor(() => expect(screen.getByTestId("range").textContent).toBe("61–80 of 5,600"));
    expect(filtersOf(fetchMock.mock.calls[0])).toEqual([{ key: "cap", op: "gte", value: 2_000_000_000 }]);

    const { user, menu } = await openMenu();
    await user.click(within(menu).getByRole("option", { name: /NASDAQ/ }));
    await user.click(within(menu).getByRole("option", { name: /NYSE/ }));

    await waitFor(() => expect(fetchMock.mock.calls.length).toBeGreaterThanOrEqual(3));
    const criteria = criteriaOf(fetchMock.mock.calls.at(-1));
    // The wire assertion: one predicate, both codes, offset zero.
    expect(criteria.offset).toBe(0);
    expect(criteria.filters).toEqual([
      { key: "cap", op: "gte", value: 2_000_000_000 },
      { key: "exch", op: "in", value: ["XNAS", "XNYS"] },
    ]);

    // One chip for the set, in the labels.
    const chip = screen.getByTestId("exchange-chip");
    expect(chip.textContent?.replace("×", "").trim()).toBe("Exchange: NASDAQ, NYSE");
    expect(screen.getByTestId("exchange-chip-name").getAttribute("title")).toContain("XNAS");
    expect(screen.getByRole("button", { name: "Clear exchange filter: NASDAQ, NYSE" })).toBeTruthy();

    // The trigger says how many, not which two.
    expect(screen.getByTestId("exchange-trigger-label").textContent).toBe("2 exchanges");

    // The chip bar above the table draws the *other* predicates: the exchange
    // is not printed twice.
    const bar = screen.getByTestId("chip-bar");
    expect(bar.textContent).toContain("Market Cap");
    expect(bar.textContent).not.toContain("NASDAQ");
    // And it lives in the toolbar's shared chip bar, not in ChipBar.
    expect(within(screen.getByTestId("control-chip-bar")).getByTestId("exchange-chip")).toBeTruthy();
  });

  it("switches to an equality when one venue is left", async () => {
    const fetchMock = vi.fn().mockResolvedValue(answer(rows(20)));
    vi.stubGlobal("fetch", fetchMock);

    render(
      <Harness
        initialFilters={[{ id: "f1", key: "exch", op: "in", value: ["XNAS", "XNYS"] }]}
        venues={MARKET_VENUES}
        source="market"
      />,
    );

    const { user, menu } = await openMenu();
    const nyse = within(menu).getByRole("option", { name: /NYSE/ });
    expect(nyse.getAttribute("aria-selected")).toBe("true");
    await user.click(nyse);

    await waitFor(() => expect(fetchMock.mock.calls.length).toBeGreaterThanOrEqual(2));
    expect(filtersOf(fetchMock.mock.calls.at(-1))).toEqual([
      { key: "exch", op: "eq", value: "XNAS" },
    ]);
    expect(screen.getByTestId("exchange-trigger-label").textContent).toBe("NASDAQ");
  });

  it("removes the predicate and the chip when the last venue is unchecked", async () => {
    const fetchMock = vi.fn().mockResolvedValue(answer(rows(20)));
    vi.stubGlobal("fetch", fetchMock);

    render(
      <Harness
        initialFilters={[CAP, { id: "f1", key: "exch", op: "eq", value: "XNAS" }]}
        venues={MARKET_VENUES}
        source="market"
      />,
    );

    expect(await screen.findByTestId("exchange-chip")).toBeTruthy();
    const { user, menu } = await openMenu();
    await user.click(within(menu).getByRole("option", { name: /NASDAQ/ }));

    await waitFor(() => expect(screen.queryByTestId("exchange-chip")).toBeNull());
    expect(filtersOf(fetchMock.mock.calls.at(-1))).toEqual([
      { key: "cap", op: "gte", value: 2_000_000_000 },
    ]);
  });

  it("keeps the chip when the answer is zero rows", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(answer(rows(20)))
      .mockResolvedValue(answer([], 0));
    vi.stubGlobal("fetch", fetchMock);

    render(<Harness venues={MARKET_VENUES} source="market" />);

    const { user, menu } = await openMenu();
    await user.click(within(menu).getByRole("option", { name: /NASDAQ/ }));

    // The grid says what happened; the filter is one click from being cleared,
    // so the chip must not disappear with the rows.
    await waitFor(() => expect(screen.getByText("No rows in this view")).toBeTruthy());
    expect(screen.getByTestId("exchange-chip-name").textContent).toBe("NASDAQ");
    expect(screen.getByTestId("range").textContent).toBe("—");
  });
});

describe("a link that already names an exchange", () => {
  it("arrives checked, and is sent unchanged", async () => {
    const fetchMock = vi.fn().mockResolvedValue(answer(rows(20)));
    vi.stubGlobal("fetch", fetchMock);

    render(
      <Harness
        initialFilters={[{ id: "f1", key: "exch", op: "eq", value: "XNAS" }]}
        venues={MARKET_VENUES}
        source="market"
      />,
    );

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(filtersOf(fetchMock.mock.calls[0])).toEqual([{ key: "exch", op: "eq", value: "XNAS" }]);
    expect(screen.getByTestId("exchange-trigger-label").textContent).toBe("NASDAQ");
    expect(screen.getByTestId("exchange-chip-name").textContent).toBe("NASDAQ");

    const { menu } = await openMenu();
    const nasdaq = within(menu).getByRole("option", { name: /NASDAQ/ });
    expect(nasdaq.getAttribute("aria-selected")).toBe("true");
  });

  it("offers an unknown code as an extra, checked, and still sends it", async () => {
    const fetchMock = vi.fn().mockResolvedValue(answer(rows(10)));
    vi.stubGlobal("fetch", fetchMock);

    render(
      <Harness
        initialFilters={[{ id: "f1", key: "exch", op: "eq", value: "XWAR" }]}
        venues={MARKET_VENUES}
        source="market"
      />,
    );

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(filtersOf(fetchMock.mock.calls[0])).toEqual([{ key: "exch", op: "eq", value: "XWAR" }]);

    const { menu } = await openMenu();
    const options = within(menu).getAllByRole("option");
    expect(options).toHaveLength(5);
    const extra = options[0] as HTMLElement;
    expect(extra.getAttribute("data-extra")).toBe("true");
    expect(extra.getAttribute("data-code")).toBe("XWAR");
    expect(extra.getAttribute("aria-selected")).toBe("true");
    expect(extra.getAttribute("title")).toContain("still sends XWAR");
    // The chip prints the code it cannot name — never a blank.
    expect(screen.getByTestId("exchange-chip-name").textContent).toBe("XWAR");
    expect(screen.getByTestId("exchange-trigger-label").textContent).toBe("XWAR");
  });

  it("round-trips the set through the address bar byte-for-byte", () => {
    const filters = withExchange([CAP], ["XNYS", "XNAS"]);
    const state = { view: "today", sort: "market_cap" as const, page: 0, filters };
    const query = encodeScreenState(state);

    expect(new URLSearchParams(query).getAll("f")).toEqual([
      "cap:gte:2000000000",
      'exch:in:["XNAS","XNYS"]',
    ]);

    const parsed = parseScreenState(query, registry, ["today"]);
    expect(parsed.dropped).toEqual([]);
    expect(parsed.state.page).toBe(0);
    expect(parsed.state.filters.map((filter) => ({ key: filter.key, op: filter.op, value: filter.value }))).toEqual([
      { key: "cap", op: "gte", value: 2_000_000_000 },
      { key: "exch", op: "in", value: ["XNAS", "XNYS"] },
    ]);
  });
});

describe("the keyboard", () => {
  it("opens on ArrowDown at the first checked option and keeps one tab stop", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(answer(rows(20))));
    render(
      <Harness
        initialFilters={[{ id: "f1", key: "exch", op: "eq", value: "XNYS" }]}
        venues={MARKET_VENUES}
        source="market"
      />,
    );

    const trigger = await screen.findByTestId("exchange-trigger");
    trigger.focus();
    const user = userEvent.setup();
    await user.keyboard("{ArrowDown}");

    const menu = screen.getByRole("listbox", { name: "Exchanges" });
    const options = within(menu).getAllByRole("option");
    expect(document.activeElement).toBe(menu);
    expect(menu.getAttribute("aria-activedescendant")).toBe(options[1]?.id);
    expect(trigger.getAttribute("aria-expanded")).toBe("true");
    // One tab stop: the menu and its options are not tabbable.
    expect(menu.getAttribute("tabindex")).toBe("-1");
    expect(options.every((option) => option.getAttribute("tabindex") === null)).toBe(true);
  });

  it("toggles with Space and stays open, toggles with Enter and closes", async () => {
    const fetchMock = vi.fn().mockResolvedValue(answer(rows(20)));
    vi.stubGlobal("fetch", fetchMock);
    render(<Harness venues={MARKET_VENUES} source="market" />);

    const { user, trigger, menu } = await openMenu();
    expect(menu.getAttribute("aria-activedescendant")).toBe(
      within(menu).getAllByRole("option")[0]?.id,
    );

    await user.keyboard(" ");
    // Still open: you are building a set, so Space must not dismiss the list.
    expect(trigger.getAttribute("aria-expanded")).toBe("true");
    expect(within(menu).getAllByRole("option")[0]?.getAttribute("aria-selected")).toBe("true");
    await waitFor(() =>
      expect(filtersOf(fetchMock.mock.calls.at(-1))).toEqual([
        { key: "exch", op: "eq", value: "XNAS" },
      ]),
    );

    await user.keyboard("{ArrowDown}{Enter}");
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
    expect(document.activeElement).toBe(trigger);
    await waitFor(() =>
      expect(filtersOf(fetchMock.mock.calls.at(-1))).toEqual([
        { key: "exch", op: "in", value: ["XNAS", "XNYS"] },
      ]),
    );
  });

  it("closes on Escape and returns focus to the trigger", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(answer(rows(20))));
    render(<Harness venues={MARKET_VENUES} source="market" />);

    const { user, trigger } = await openMenu();
    await user.keyboard("{Escape}");

    expect(trigger.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByRole("listbox", { name: "Exchanges" })).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });

  it("closes on Tab and lets the browser move on", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(answer(rows(20))));
    render(<Harness venues={MARKET_VENUES} source="market" />);

    const { user, trigger } = await openMenu();
    await user.tab();

    expect(trigger.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByRole("listbox", { name: "Exchanges" })).toBeNull();
    // Focus left the control rather than falling to <body> on unmount.
    expect(document.activeElement).not.toBe(document.body);
  });

  it("type-ahead jumps over the labels", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(answer(rows(20))));
    render(<Harness venues={MARKET_VENUES} source="market" />);

    const { user, menu } = await openMenu();
    await user.keyboard("n");

    const options = within(menu).getAllByRole("option");
    expect(menu.getAttribute("aria-activedescendant")).toBe(options[1]?.id);
    expect((options[1] as HTMLElement).textContent).toContain("NYSE");
  });
});

describe("mounting", () => {
  it("does not mount the control when no callback is given", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(answer(rows(10))));
    render(
      <ScreenPanel
        view={VIEW}
        selectedSymbol={null}
        onActivate={vi.fn()}
        onClear={vi.fn()}
        containerWidth={1280}
        filters={[]}
        registry={registry}
      />,
    );

    await waitFor(() => expect(screen.getByTestId("range")).toBeTruthy());
    expect(screen.queryByTestId("exchange-trigger")).toBeNull();
    expect(screen.queryByTestId("control-chip-bar")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// The shell: one state, one URL, one request each
// ---------------------------------------------------------------------------

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

const EXCHANGES_DOC = {
  as_of: "2026-10-31T01:28:01Z",
  region: "us",
  asset_class: "stock",
  universe_total: 5_576,
  code_total: 4,
  exchanges: [
    { code: "XNAS", label: "NASDAQ", count: 3_308, share: 0.5934 },
    { code: "XNYS", label: "NYSE", count: 2_009, share: 0.3603 },
    { code: "XASE", label: "AMEX", count: 258, share: 0.0463 },
    { code: "XCBO", label: "CBOE", count: 1, share: 0.0002 },
  ],
};

function stubApp() {
  const selects: Array<Record<string, unknown>> = [];
  const urls: string[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    urls.push(url);
    if (url === "/screens/select/schema") return json(filterSchemaWire());
    if (url.startsWith("/market/breadth")) return json(LIVE_BREADTH);
    if (url.startsWith("/market/exchanges")) return json(EXCHANGES_DOC);
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
  return { selects, urls, fetchMock };
}

describe("the exchange choice in the shell", () => {
  beforeEach(() => {
    resetFilterSchemaCache();
    window.history.replaceState(null, "", "/app/screener");
  });

  it("sends one in predicate, writes the set to the URL, and reads it back on reload", async () => {
    const api = stubApp();
    const user = userEvent.setup();

    const first = render(<App />);
    await waitFor(
      () => expect(screen.getByTestId("range").textContent).toBe("1–20 of 5,600"),
      { timeout: 3_000 },
    );

    // The menu is the served route's four venues, in menu order.
    const trigger = await screen.findByTestId("exchange-trigger");
    expect(screen.getByTestId("exchange-trigger").getAttribute("title")).toContain("from /market/exchanges");
    // Two readers already share the breadth document (the strip and the sector
    // menu). The exchange control must not add a third — it has its own route.
    const breadthBefore = api.urls.filter((url) => url.startsWith("/market/breadth")).length;
    expect(breadthBefore).toBeGreaterThan(0);
    await user.click(trigger);
    const menu = screen.getByRole("listbox", { name: "Exchanges" });
    expect(within(menu).getAllByRole("option")).toHaveLength(4);

    api.selects.length = 0;
    await user.click(within(menu).getByRole("option", { name: /NASDAQ/ }));
    await user.click(within(menu).getByRole("option", { name: /NYSE/ }));

    await waitFor(() => expect(api.selects.length).toBeGreaterThan(0), { timeout: 3_000 });
    const body = api.selects.at(-1) as { criteria: { offset: number; filters: unknown[] } };
    expect(body.criteria.offset).toBe(0);
    expect(body.criteria.filters).toEqual([{ key: "exch", op: "in", value: ["XNAS", "XNYS"] }]);

    const params = new URLSearchParams(window.location.search);
    expect(params.get("page")).toBe("1");
    expect(params.getAll("f")).toEqual(['exch:in:["XNAS","XNYS"]']);
    expect(screen.getByTestId("exchange-chip-name").textContent).toBe("NASDAQ, NYSE");

    // One read each: the sector menu's breadth document is not asked for a
    // third time by this control, and the exchange list is its own route, read
    // once per mount — for the selector's population (`common_only=0`), so the
    // counts and the grid describe one universe.
    expect(api.urls.filter((url) => url.startsWith("/market/breadth"))).toHaveLength(breadthBefore);
    const exchangeReads = api.urls.filter((url) => url.startsWith("/market/exchanges"));
    expect(exchangeReads).toHaveLength(1);
    expect(exchangeReads[0]).toBe("/market/exchanges?region=us&asset_class=stock&common_only=0");

    // A reload is a fresh mount against the same address bar.
    first.unmount();
    resetFilterSchemaCache();
    api.selects.length = 0;
    render(<App />);

    await waitFor(() => expect(api.selects.length).toBe(1), { timeout: 3_000 });
    expect(
      (api.selects[0] as { criteria: { filters: unknown[] } }).criteria.filters,
    ).toEqual([{ key: "exch", op: "in", value: ["XNAS", "XNYS"] }]);
    expect(screen.getByTestId("exchange-chip-name").textContent).toBe("NASDAQ, NYSE");

    const reopened = await screen.findByTestId("exchange-trigger");
    expect(reopened.getAttribute("aria-label")).toBe("Exchange: 2 exchanges");
    await userEvent.setup().click(reopened);
    const again = screen.getByRole("listbox", { name: "Exchanges" });
    const checked = within(again)
      .getAllByRole("option")
      .filter((option) => option.getAttribute("aria-selected") === "true")
      .map((option) => option.getAttribute("data-code"));
    expect(checked).toEqual(["XNAS", "XNYS"]);
  });

  it("degrades to the page's exchange cells when the route answers 404", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/screens/select/schema") return json(filterSchemaWire());
      if (url.startsWith("/market/breadth")) return json(LIVE_BREADTH);
      if (url.startsWith("/market/exchanges")) {
        return new Response(JSON.stringify({ errorMessage: "not found" }), { status: 404 });
      }
      if (url === "/health") return json({ status: "ok", version: "0.1.0", checked_at: "", elapsed_ms: 1, startup_error: null, checks: {} });
      return json({ errorMessage: "no route" }, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    const source = await screen.findByTestId("exchange-trigger");
    await waitFor(() => expect(source.getAttribute("data-source")).not.toBe("loading"));
    expect(["rows", "none"]).toContain(source.getAttribute("data-source"));
    // The control is still there and still usable: the panel is not the route.
    expect(screen.getByTestId("exchange-trigger")).toBeTruthy();
  });
});
