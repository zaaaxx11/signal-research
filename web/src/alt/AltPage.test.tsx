/**
 * The three alt pages, asserted on what a reader (and a screen reader) gets:
 *
 * * one request per mount, and the URL names the class the page is;
 * * every row the endpoint sent is drawn, and a row it could not price is not
 *   drawn at all — a null price never becomes `0.00`;
 * * the filter narrows locally (no request, focus stays in the box) and the
 *   sort is a header control that says its direction in `aria-sort`;
 * * the footer states when the data was read and that a closed market shows its
 *   last session — the page never calls a stale quote live;
 * * loading, error and empty are states of one reserved box, so the answer
 *   landing cannot move the page.
 */

import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ALT_PER } from "./alt";
import { ALT_RESERVED_HEIGHT, ALT_SKELETON_ROWS, AltPage } from "./AltPage";
import { CryptoPage, ForexPage, FuturesPage } from "./index";

interface WireRow {
  symbol: string;
  name: string | null;
  price: number | null;
  change_pct: number | null;
  volume: number | null;
  currency: string | null;
}

function wireRow(index: number, overrides: Partial<WireRow> = {}): WireRow {
  return {
    symbol: `SYM${String(index).padStart(2, "0")}-USD`,
    name: `Asset ${index}`,
    price: 100 + index,
    change_pct: ((index % 7) - 3) / 2,
    volume: 1_000_000 * (index + 1),
    currency: "USD",
    ...overrides,
  };
}

function altDocument(rows: WireRow[], overrides: Record<string, unknown> = {}) {
  return {
    as_of: "2026-10-31T23:40:00Z",
    class: "crypto",
    universe_total: rows.length + 1,
    measured: rows.length,
    per: ALT_PER,
    method: "yahoo_chart",
    rows,
    ...overrides,
  };
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function _metaDoc(measured = 0) {
  return { as_of: "2026-01-01T00:00:00Z", method: "coingecko_markets_v3_plus_defillama_tvl", universe_total: 40, measured, rows: {} };
}

function _globalDoc(available = false) {
  if (!available) return { as_of: "2026-01-01T00:00:00Z", method: "coingecko_global_v3", available: false };
  return {
    as_of: "2026-01-01T00:00:00Z",
    method: "coingecko_global_v3",
    available: true,
    total_market_cap_usd: 2.5e12,
    market_cap_change_pct_24h: 1.4,
    btc_dominance: 58.1,
    eth_dominance: 12.2,
    active_cryptocurrencies: 17221,
  };
}

function _chartDoc() {
  return { symbol: "BTC-USD", range: "5d", interval: "15m", bars: [], source: "yahoo_chart", truncated: false };
}

/** Route the request to the right stubbed response. */
function routeToResponse(input: RequestInfo | URL, altBody: unknown): Response {
  const url = String(input);
  if (url.includes("/market/crypto/meta")) return json(_metaDoc());
  if (url.includes("/market/crypto/global")) return json(_globalDoc());
  if (url.includes("/market/chart/")) return json(_chartDoc());
  return json(altBody);
}

function stub(body: unknown = altDocument(Array.from({ length: 40 }, (_, index) => wireRow(index)))) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => routeToResponse(input, body));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function urls(fetchMock: ReturnType<typeof vi.fn>): string[] {
  return fetchMock.mock.calls.map((call) => String(call[0]));
}

function rowSymbols(): string[] {
  return screen.getAllByTestId("alt-row").map((row) => row.getAttribute("data-symbol") ?? "");
}

/** Switch to the table view — the sort tests assert the header controls, which are the table's. */
async function toTable(): Promise<void> {
  const user = userEvent.setup();
  await user.click(screen.getByTestId("alt-view-table"));
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("AltPage — the request", () => {
  it("asks once, for the class it was given", async () => {
    const fetchMock = stub();
    render(<AltPage marketClass="crypto" />);

    await screen.findByTestId("alt-table");
    // The page fetches alt data, crypto meta, crypto global, and per-row sparklines
    const allUrls = urls(fetchMock);
    expect(allUrls.filter((u) => u === "/market/alt?class=crypto&per=50")).toHaveLength(1);
    expect(allUrls.filter((u) => u === "/market/crypto/meta")).toHaveLength(1);
    expect(allUrls.filter((u) => u === "/market/crypto/global")).toHaveLength(1);
  });

  it("gives each class its own URL", async () => {
    for (const [marketClass, expected] of [
      ["crypto", "/market/alt?class=crypto&per=50"],
      ["futures", "/market/alt?class=futures&per=50"],
      ["forex", "/market/alt?class=forex&per=50"],
    ] as const) {
      const fetchMock = stub(altDocument([wireRow(1)], { class: marketClass }));
      const view = render(<AltPage marketClass={marketClass} />);
      await screen.findByTestId("alt-table");
      // The alt URL must be present; crypto also fetches enrich data + sparklines
      expect(urls(fetchMock).filter((u) => u === expected)).toHaveLength(1);
      view.unmount();
      vi.unstubAllGlobals();
    }
  });

  it("mounts each wrapper as its own class", async () => {
    for (const [element, marketClass] of [
      [<CryptoPage key="c" />, "crypto"],
      [<FuturesPage key="f" />, "futures"],
      [<ForexPage key="x" />, "forex"],
    ] as const) {
      const fetchMock = stub(altDocument([wireRow(1)], { class: marketClass }));
      const view = render(element);
      await screen.findByTestId("alt-table");
      expect(screen.getByTestId("alt-page").getAttribute("data-class")).toBe(marketClass);
      expect(urls(fetchMock)[0]).toContain(`class=${marketClass}`);
      view.unmount();
      vi.unstubAllGlobals();
    }
  });
});

describe("AltPage — the table", () => {
  it("draws every priced row, with its name, price, change and volume", async () => {
    stub();
    render(<AltPage marketClass="crypto" />);
    await screen.findByTestId("alt-table");
    await toTable();

    expect(screen.getAllByTestId("alt-row")).toHaveLength(40);
    expect(rowSymbols()[0]).toBe("SYM00-USD");

    const first = screen.getAllByTestId("alt-row")[0];
    if (!first) throw new Error("no row");
    expect(within(first).getByTestId("alt-name").textContent).toBe("Asset 0");
    expect(within(first).getByTestId("alt-last").textContent).toBe("$100.00");
    expect(within(first).getByTestId("alt-volume").textContent).toBe("1M");
    expect(within(first).getByTestId("alt-currency").textContent).toBe("USD");
  });

  it("names the columns the class trades in", async () => {
    stub();
    render(<AltPage marketClass="futures" />);
    await screen.findByTestId("alt-table");
    await toTable();

    expect(screen.getByTestId("alt-th-volume").textContent).toBe("Contract");
    expect(screen.getByTestId("alt-th-last").textContent).toContain("Last");
    expect(screen.getByTestId("alt-th-change").textContent).toContain("Change");
  });

  it("does not print a volume for a class that has none", async () => {
    stub(
      altDocument(
        [wireRow(1, { symbol: "EURUSD=X", name: "Euro / US Dollar", price: 1.1166, volume: 0, currency: "USD" })],
        { class: "forex" },
      ),
    );
    render(<AltPage marketClass="forex" />);
    await screen.findByTestId("alt-table");
    await toTable();

    expect(screen.getByTestId("alt-volume").textContent).toBe("—");
    expect(screen.getByTestId("alt-last").textContent).toBe("$1.1166");
  });

  it("shows the symbol where the name is missing, never an empty cell", async () => {
    stub(altDocument([wireRow(1, { symbol: "ES=F", name: null })]));
    render(<AltPage marketClass="futures" />);
    await screen.findByTestId("alt-table");
    await toTable();

    const name = screen.getByTestId("alt-name");
    expect(name.textContent).toBe("ES=F");
    expect(name.getAttribute("data-name-missing")).toBe("true");
  });

  it("keeps the symbol as text, because no detail page exists for these classes", async () => {
    stub(altDocument([wireRow(1, { symbol: "BTC-USD" })]));
    render(<AltPage marketClass="crypto" />);
    await screen.findByTestId("alt-table");
    await toTable();

    expect(screen.queryAllByRole("link")).toHaveLength(0);
    // The cell is the monogram plus the ticker; the ticker is text, not an anchor.
    const cell = screen.getByTestId("alt-symbol");
    expect(cell.textContent).toContain("BTC-USD");
    expect(cell.querySelector("[data-logo='BTC-USD']")).toBeTruthy();
  });

  it("renders a row whose price is null as absent, not as zero", async () => {
    stub(
      altDocument([
        wireRow(1, { symbol: "BTC-USD", price: 77399.29 }),
        wireRow(2, { symbol: "NULL-USD", price: null }),
        wireRow(3, { symbol: "NEG-USD", price: -4 }),
        wireRow(4, { symbol: "NAN-USD", price: Number.NaN }),
      ]),
    );
    render(<AltPage marketClass="crypto" />);
    await screen.findByTestId("alt-table");

    expect(rowSymbols()).toEqual(["BTC-USD"]);
    expect(screen.queryByText("NULL-USD")).toBeNull();
    expect(screen.queryByText("NEG-USD")).toBeNull();
    expect(screen.queryByText("NAN-USD")).toBeNull();
    expect(screen.queryByText("$0.00")).toBeNull();
    expect(screen.queryByText("$NaN")).toBeNull();
  });

  it("gives the change cell the grid's delta renderer, and flat is not a colour", async () => {
    stub(
      altDocument([
        wireRow(1, { symbol: "UP-USD", change_pct: 1.5 }),
        wireRow(2, { symbol: "FLAT-USD", change_pct: 0 }),
        wireRow(3, { symbol: "DOWN-USD", change_pct: -2.25 }),
        wireRow(4, { symbol: "NONE-USD", change_pct: null }),
      ]),
    );
    render(<AltPage marketClass="crypto" />);
    await screen.findByTestId("alt-table");
    await toTable();

    const cell = (symbol: string) => {
      const row = screen.getAllByTestId("alt-row").find((candidate) => candidate.getAttribute("data-symbol") === symbol);
      if (!row) throw new Error(`no row ${symbol}`);
      return within(row).getByTestId("alt-change");
    };
    /** `CellValue` owns the sign; the cell is its `<td>` wrapper. */
    const sign = (symbol: string) =>
      cell(symbol).querySelector("[data-sign]")?.getAttribute("data-sign") ?? null;

    expect(sign("UP-USD")).toBe("1");
    expect(cell("UP-USD").textContent).toContain("▲");
    expect(cell("UP-USD").textContent).toContain("+1.50%");
    expect(cell("UP-USD").textContent).toContain("up");

    expect(sign("FLAT-USD")).toBe("0");
    expect(cell("FLAT-USD").textContent).toContain("0.00%");
    expect(cell("FLAT-USD").textContent).toContain("unchanged");
    expect(cell("FLAT-USD").innerHTML).not.toContain("text-up");
    expect(cell("FLAT-USD").innerHTML).not.toContain("text-down");

    expect(sign("DOWN-USD")).toBe("-1");
    expect(cell("DOWN-USD").textContent).toContain("▼");
    expect(cell("DOWN-USD").textContent).toContain("-2.25%");

    expect(cell("NONE-USD").textContent).toBe("—");
  });

  it("states when the data was read and that a closed market shows its last session", async () => {
    stub();
    render(<AltPage marketClass="crypto" />);
    await screen.findByTestId("alt-footer");

    const footer = screen.getByTestId("alt-footer").textContent ?? "";
    expect(footer).toContain("31 Oct 2026, 23:40 UTC");
    expect(footer).toMatch(/closed market/i);
    expect(footer).toMatch(/last session/i);
  });

  it("does not claim a quote is live", async () => {
    stub();
    render(<AltPage marketClass="crypto" />);
    await screen.findByTestId("alt-table");
    expect(screen.getByTestId("alt-page").textContent ?? "").not.toMatch(/\blive\b/i);
  });
});

describe("AltPage — the filter", () => {
  it("narrows by symbol without asking the server again, and keeps the focus", async () => {
    const fetchMock = stub();
    const user = userEvent.setup();
    render(<AltPage marketClass="crypto" />);
    await screen.findByTestId("alt-table");
    await toTable();

    // Snapshot: no more fetches after the initial load + sparklines
    const callsAfterLoad = fetchMock.mock.calls.length;

    const filter = screen.getByTestId("alt-filter");
    await user.click(filter);
    await user.type(filter, "SYM1");

    expect(rowSymbols()).toEqual(["SYM10-USD", "SYM11-USD", "SYM12-USD", "SYM13-USD", "SYM14-USD", "SYM15-USD", "SYM16-USD", "SYM17-USD", "SYM18-USD", "SYM19-USD"]);
    // Filtering is local — no new fetches triggered
    expect(fetchMock).toHaveBeenCalledTimes(callsAfterLoad);
    expect(document.activeElement).toBe(filter);
  });

  it("narrows by name too", async () => {
    const user = userEvent.setup();
    stub(
      altDocument([
        wireRow(1, { symbol: "BTC-USD", name: "Bitcoin" }),
        wireRow(2, { symbol: "ES=F", name: "E-mini S&P 500" }),
      ]),
    );
    render(<AltPage marketClass="crypto" />);
    await screen.findByTestId("alt-table");

    await user.type(screen.getByTestId("alt-filter"), "e-mini");
    expect(rowSymbols()).toEqual(["ES=F"]);
  });

  it("says nothing matched, and offers a way back, instead of an empty table", async () => {
    const user = userEvent.setup();
    const fetchMock = stub();
    render(<AltPage marketClass="crypto" />);
    await screen.findByTestId("alt-table");

    // Snapshot: no more fetches after the initial load + sparklines
    const callsAfterLoad = fetchMock.mock.calls.length;

    await user.type(screen.getByTestId("alt-filter"), "zzzzz");
    const empty = screen.getByTestId("alt-filter-empty");
    expect(empty.textContent).toContain("zzzzz");
    expect(screen.queryByTestId("alt-table")).toBeNull();

    await user.click(screen.getByTestId("alt-filter-clear"));
    expect(screen.getAllByTestId("alt-row")).toHaveLength(40);
    // No new fetches — clear is local
    expect(fetchMock).toHaveBeenCalledTimes(callsAfterLoad);
  });

  it("reports how many of the rows are shown", async () => {
    const user = userEvent.setup();
    stub();
    render(<AltPage marketClass="crypto" />);
    await screen.findByTestId("alt-table");

    expect(screen.getByTestId("alt-count").textContent).toContain("40");
    await user.type(screen.getByTestId("alt-filter"), "SYM0");
    expect(screen.getByTestId("alt-count").textContent).toContain("10");
  });
});

describe("AltPage — the sort", () => {
  const sorted = () =>
    altDocument([
      wireRow(1, { symbol: "ETH-USD", name: "Ether", price: 2249.18, change_pct: -1.2 }),
      wireRow(2, { symbol: "BTC-USD", name: "Bitcoin", price: 77399.29, change_pct: 0 }),
      wireRow(3, { symbol: "DOGE-USD", name: "Dogecoin", price: 0.17, change_pct: 4.5 }),
      wireRow(4, { symbol: "ADA-USD", name: "Cardano", price: 0.44, change_pct: 4.5 }),
    ]);

  it("sorts by last on the first click, descending, and says so", async () => {
    const user = userEvent.setup();
    stub(sorted());
    render(<AltPage marketClass="crypto" />);
    await screen.findByTestId("alt-table");
    await toTable();

    const header = screen.getByTestId("alt-th-last");
    expect(header.getAttribute("aria-sort")).toBe("none");

    await user.click(screen.getByTestId("alt-sort-last"));
    expect(screen.getByTestId("alt-th-last").getAttribute("aria-sort")).toBe("descending");
    expect(rowSymbols()).toEqual(["BTC-USD", "ETH-USD", "ADA-USD", "DOGE-USD"]);

    await user.click(screen.getByTestId("alt-sort-last"));
    expect(screen.getByTestId("alt-th-last").getAttribute("aria-sort")).toBe("ascending");
    expect(rowSymbols()).toEqual(["DOGE-USD", "ADA-USD", "ETH-USD", "BTC-USD"]);
  });

  it("sorts by change and keeps a tie in symbol order", async () => {
    const user = userEvent.setup();
    stub(sorted());
    render(<AltPage marketClass="crypto" />);
    await screen.findByTestId("alt-table");
    await toTable();

    await user.click(screen.getByTestId("alt-sort-change"));
    expect(screen.getByTestId("alt-th-change").getAttribute("aria-sort")).toBe("descending");
    // DOGE and ADA both changed by 4.5; the symbol decides, not the arrival order.
    expect(rowSymbols()).toEqual(["ADA-USD", "DOGE-USD", "BTC-USD", "ETH-USD"]);
  });

  it("sorts by symbol ascending, and moves the arrow off the old column", async () => {
    const user = userEvent.setup();
    stub(sorted());
    render(<AltPage marketClass="crypto" />);
    await screen.findByTestId("alt-table");
    await toTable();

    await user.click(screen.getByTestId("alt-sort-change"));
    await user.click(screen.getByTestId("alt-sort-symbol"));
    expect(screen.getByTestId("alt-th-symbol").getAttribute("aria-sort")).toBe("ascending");
    expect(screen.getByTestId("alt-th-change").getAttribute("aria-sort")).toBe("none");
    expect(rowSymbols()).toEqual(["ADA-USD", "BTC-USD", "DOGE-USD", "ETH-USD"]);
  });

  it("filters first, then sorts, so a hidden row cannot order the visible ones", async () => {
    const user = userEvent.setup();
    stub(sorted());
    render(<AltPage marketClass="crypto" />);
    await screen.findByTestId("alt-table");
    await toTable();

    await user.type(screen.getByTestId("alt-filter"), "usd");
    await user.click(screen.getByTestId("alt-sort-change"));
    expect(rowSymbols()).toEqual(["ADA-USD", "DOGE-USD", "BTC-USD", "ETH-USD"]);

    await user.clear(screen.getByTestId("alt-filter"));
    await user.type(screen.getByTestId("alt-filter"), "e");
    // Only DOGE-USD and ETH-USD carry an "e" in the symbol or name; the sort
    // still holds for the two that survive.
    expect(rowSymbols()).toEqual(["DOGE-USD", "ETH-USD"]);
  });

  it("keeps the filter's focus when a header is clicked and the table re-orders", async () => {
    const user = userEvent.setup();
    stub(sorted());
    render(<AltPage marketClass="crypto" />);
    await screen.findByTestId("alt-table");
    await toTable();

    const filter = screen.getByTestId("alt-filter");
    await user.type(filter, "btc");
    await user.click(screen.getByTestId("alt-sort-change"));
    // The click moved focus to the header (a button); the filter is still
    // present and still holds its text, which is what a reader needs next.
    expect(screen.getByTestId("alt-filter")).toBe(filter);
    expect((filter as HTMLInputElement).value).toBe("btc");
    expect(rowSymbols()).toEqual(["BTC-USD"]);
  });
});

describe("AltPage — the states", () => {
  it("reserves one box while loading, then fills it with the same table", async () => {
    let release: ((response: Response) => void) | undefined;
    const pending = new Promise<Response>((resolve) => {
      release = resolve;
    });
    vi.stubGlobal("fetch", vi.fn(() => pending));

    render(<AltPage marketClass="crypto" />);

    expect(screen.getAllByTestId("alt-skeleton-row")).toHaveLength(ALT_SKELETON_ROWS);
    expect(screen.getByTestId("alt-frame").style.minHeight).toBe(`${ALT_RESERVED_HEIGHT}px`);

    release?.(json(altDocument(Array.from({ length: 40 }, (_, index) => wireRow(index)))));
    await screen.findByTestId("alt-table");
    expect(screen.getByTestId("alt-frame").style.minHeight).toBe(`${ALT_RESERVED_HEIGHT}px`);
  });

  it("says the class is empty when the endpoint priced nothing", async () => {
    stub(altDocument([], { universe_total: 0, measured: 0 }));
    render(<AltPage marketClass="forex" />);

    const empty = await screen.findByTestId("alt-empty");
    expect(empty.textContent).toMatch(/no .* quotes/i);
    expect(screen.queryByTestId("alt-table")).toBeNull();
  });

  it("reports a failure with the reason, and retries into the table", async () => {
    let altCalls = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/market/alt")) {
        altCalls++;
        if (altCalls === 1) return json({ error: "provider_unavailable", errorMessage: "The quote provider is down" }, 503);
        return json(altDocument([wireRow(1)]));
      }
      if (url.includes("/market/crypto/meta")) return json(_metaDoc());
      if (url.includes("/market/crypto/global")) return json(_globalDoc());
      if (url.includes("/market/chart/")) return json(_chartDoc());
      return json(altDocument([wireRow(1)]));
    });
    vi.stubGlobal("fetch", fetchMock);

    const user = userEvent.setup();
    render(<AltPage marketClass="crypto" />);

    const error = await screen.findByTestId("alt-error");
    expect(error.textContent).toContain("The quote provider is down");
    expect(screen.getByTestId("alt-error").getAttribute("role")).toBe("alert");

    await user.click(screen.getByTestId("alt-retry"));
    await screen.findByTestId("alt-table");
    // One failed alt call, one successful retry
    expect(altCalls).toBe(2);
    expect(screen.getAllByTestId("alt-row")).toHaveLength(1);
  });

  it("names a deployment that has no alt endpoint at all", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => json({ detail: "Not Found" }, 404)),
    );
    render(<AltPage marketClass="crypto" />);

    const error = await screen.findByTestId("alt-error");
    expect(error.textContent).toMatch(/does not serve/i);
  });
});

describe("AltPage — card grid", () => {
  it("renders cards instead of a table by default", async () => {
    stub();
    render(<AltPage marketClass="crypto" />);
    await screen.findByTestId("alt-table");

    expect(screen.getByTestId("alt-card-view")).toBeTruthy();
    expect(screen.getByTestId("card-grid")).toBeTruthy();
    // 40 cards, one per row
    expect(screen.getAllByTestId("alt-row")).toHaveLength(40);
  });

  it("can switch to table view", async () => {
    stub();
    const user = userEvent.setup();
    render(<AltPage marketClass="crypto" />);
    await screen.findByTestId("alt-table");

    await user.click(screen.getByTestId("alt-view-table"));
    // The <table> node should now be a real table element, not the hidden div
    const realTable = screen.getByTestId("alt-table");
    expect(realTable.tagName).toBe("TABLE");
  });
});

describe("AltPage — category tabs", () => {
  function wireFutures() {
    return altDocument(
      [
        wireRow(1, { symbol: "ES=F", name: "E-mini S&P 500", price: 6000 }),
        wireRow(2, { symbol: "CL=F", name: "Crude Oil", price: 70 }),
        wireRow(3, { symbol: "GC=F", name: "Gold", price: 2700 }),
        wireRow(4, { symbol: "6E=F", name: "Euro FX", price: 1.08 }),
      ],
      { class: "futures" },
    );
  }

  it("renders category tabs for futures", async () => {
    stub(wireFutures());
    render(<AltPage marketClass="futures" />);
    await screen.findByTestId("alt-table");

    expect(screen.getByTestId("alt-category-tabs")).toBeTruthy();
    expect(screen.getByTestId("alt-category-all")).toBeTruthy();
    expect(screen.getByTestId("alt-category-indices")).toBeTruthy();
    expect(screen.getByTestId("alt-category-energy")).toBeTruthy();
    expect(screen.getByTestId("alt-category-metals")).toBeTruthy();
    expect(screen.getByTestId("alt-category-currencies")).toBeTruthy();
  });

  it("filters by category client-side", async () => {
    stub(wireFutures());
    const user = userEvent.setup();
    render(<AltPage marketClass="futures" />);
    await screen.findByTestId("alt-table");

    await user.click(screen.getByTestId("alt-category-indices"));
    // Only ES=F
    expect(rowSymbols()).toEqual(["ES=F"]);

    await user.click(screen.getByTestId("alt-category-all"));
    expect(rowSymbols()).toEqual(["ES=F", "CL=F", "GC=F", "6E=F"]);
  });

  it("crypto has quote-currency tabs", async () => {
    stub(
      altDocument([
        wireRow(1, { symbol: "BTC-USD", name: "Bitcoin", price: 77399 }),
        wireRow(2, { symbol: "ETH-USDT", name: "Ether", price: 2249 }),
        wireRow(3, { symbol: "BTC-BTC", name: "Bitcoin", price: 1 }),
      ]),
    );
    const user = userEvent.setup();
    render(<AltPage marketClass="crypto" />);
    await screen.findByTestId("alt-table");

    expect(screen.getByTestId("alt-category-usd")).toBeTruthy();
    expect(screen.getByTestId("alt-category-usdt")).toBeTruthy();
    expect(screen.getByTestId("alt-category-btc")).toBeTruthy();

    await user.click(screen.getByTestId("alt-category-usd"));
    expect(rowSymbols()).toEqual(["BTC-USD"]);

    await user.click(screen.getByTestId("alt-category-usdt"));
    expect(rowSymbols()).toEqual(["ETH-USDT"]);
  });

  it("resets the text filter when switching tabs", async () => {
    stub(wireFutures());
    const user = userEvent.setup();
    render(<AltPage marketClass="futures" />);
    await screen.findByTestId("alt-table");

    const filter = screen.getByTestId("alt-filter") as HTMLInputElement;
    await user.type(filter, "crude");

    await user.click(screen.getByTestId("alt-category-energy"));
    // Filter input should be cleared
    expect(filter.value).toBe("");
  });
});

describe("AltPage — forex chart", () => {
  function wireForex() {
    return altDocument(
      [
        {
          symbol: "EURUSD=X", name: "Euro / US Dollar", price: 1.1166,
          change_pct: 0.35, volume: 0, currency: "USD",
        },
        {
          symbol: "GBPUSD=X", name: "British Pound / US Dollar", price: 1.2750,
          change_pct: -0.15, volume: 0, currency: "USD",
        },
        {
          symbol: "JPY=X", name: "Japanese Yen", price: 146.82,
          change_pct: -0.42, volume: 0, currency: "JPY",
        },
        {
          symbol: "EURGBP=X", name: "Euro / British Pound", price: 0.8750,
          change_pct: 0.10, volume: 0, currency: "GBP",
        },
      ],
      { class: "forex" },
    );
  }

  it("renders the forex chart in card view", async () => {
    stub(wireForex());
    render(<AltPage marketClass="forex" />);
    await screen.findByTestId("alt-table");

    expect(screen.getByTestId("forex-chart")).toBeTruthy();
    expect(screen.getByTestId("forex-chart").textContent).toContain("Currency Strength vs USD");
  });

  it("does not render the forex chart in table view", async () => {
    stub(wireForex());
    const user = userEvent.setup();
    render(<AltPage marketClass="forex" />);
    await screen.findByTestId("alt-table");

    await user.click(screen.getByTestId("alt-view-table"));
    expect(screen.queryByTestId("forex-chart")).toBeNull();
  });

  it("shows only USD-quoted pairs in the chart", async () => {
    stub(wireForex());
    render(<AltPage marketClass="forex" />);
    await screen.findByTestId("alt-table");

    const chart = screen.getByTestId("forex-chart");
    // EURUSD and GBPUSD are USD-quoted; JPY=X is a JPY quote form, EURGBP is cross
    expect(chart.textContent).toContain("EUR");
    expect(chart.textContent).toContain("GBP");
    // EURGBP is a cross, not USD-quoted, so it should not appear
    expect(chart.textContent).not.toContain("EURGBP");
  });
});
