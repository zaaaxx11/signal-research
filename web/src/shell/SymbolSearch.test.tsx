import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SEARCH_DEBOUNCE_MS, SEARCH_LIMIT, SymbolSearch } from "./SymbolSearch";

/**
 * The search box's two contracts that are easy to get subtly wrong:
 *
 *  1. one request per typing burst (debounce resets on every keystroke), and
 *  2. the arrow keys move a selection that Enter commits — the listbox is
 *     reachable without a mouse.
 *
 * `fetch` is mocked at the global boundary, so the test never needs a server
 * and never asserts on anything but the request the browser would have made.
 */

const HITS = [
  { symbol: "APP", name: "AppLovin Corp", exchange: "NASDAQ" },
  { symbol: "AAPL", name: "Apple Inc", exchange: "NASDAQ" },
  { symbol: "APPS", name: "Digital Turbine Inc", exchange: "NYSE" },
];

/**
 * The list the box draws for "app": the exact symbol, then the symbols that
 * start with it, then the one that merely contains it. The API answers with a
 * ranking of its own; this is the suggestion order the component promises.
 */
const RANKED = [HITS[0], HITS[2], HITS[1]];

function mockFetch(payload: unknown = HITS, status = 200) {
  const fetchMock = vi.fn(
    async (_input: RequestInfo | URL, _init?: RequestInit) =>
      new Response(JSON.stringify(payload), {
        status,
        headers: { "content-type": "application/json" },
      }),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

/** Let pending promise chains settle inside act. */
async function flush(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

async function advance(ms: number): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
  await flush();
}

function type(input: HTMLElement, value: string): void {
  fireEvent.change(input, { target: { value } });
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("SymbolSearch debounce", () => {
  it("does not call the API until the debounce window elapses", async () => {
    const fetchMock = mockFetch();
    render(<SymbolSearch />);
    const input = screen.getByRole("combobox");

    type(input, "app");
    expect(fetchMock).not.toHaveBeenCalled();

    await advance(SEARCH_DEBOUNCE_MS - 1);
    expect(fetchMock).not.toHaveBeenCalled();

    await advance(1);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0]?.[0]).toBe(`/symbols/search?q=app&limit=${SEARCH_LIMIT}`);
    expect(screen.getAllByRole("option")).toHaveLength(HITS.length);
  });

  it("coalesces a keystroke burst into one request for the final query", async () => {
    const fetchMock = mockFetch();
    render(<SymbolSearch />);
    const input = screen.getByRole("combobox");

    type(input, "a");
    await advance(100);
    type(input, "ap");
    await advance(100);
    type(input, "app");
    expect(fetchMock).not.toHaveBeenCalled();

    await advance(SEARCH_DEBOUNCE_MS);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0]?.[0]).toBe(`/symbols/search?q=app&limit=${SEARCH_LIMIT}`);
  });

  it("never asks the API a blank question", async () => {
    const fetchMock = mockFetch();
    render(<SymbolSearch />);
    const input = screen.getByRole("combobox");

    type(input, "   ");
    await advance(SEARCH_DEBOUNCE_MS * 3);
    expect(fetchMock).not.toHaveBeenCalled();

    type(input, "");
    await advance(SEARCH_DEBOUNCE_MS * 3);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("trims the query before it becomes a request", async () => {
    const fetchMock = mockFetch();
    render(<SymbolSearch />);
    type(screen.getByRole("combobox"), "  aapl  ");

    await advance(SEARCH_DEBOUNCE_MS);
    expect(fetchMock.mock.calls[0]?.[0]).toBe(`/symbols/search?q=aapl&limit=${SEARCH_LIMIT}`);
  });
});

describe("SymbolSearch keyboard navigation", () => {
  async function openWithResults() {
    const onSelect = vi.fn();
    const fetchMock = mockFetch();
    render(<SymbolSearch onSelect={onSelect} />);
    const input = screen.getByRole("combobox");
    type(input, "app");
    await advance(SEARCH_DEBOUNCE_MS);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    return { input, onSelect };
  }

  it("moves the active option with the arrow keys and commits with Enter", async () => {
    const { input, onSelect } = await openWithResults();

    expect(input.getAttribute("aria-activedescendant")).toBe("symbol-search-option-0");
    expect(screen.getAllByRole("option")[0]?.getAttribute("aria-selected")).toBe("true");

    fireEvent.keyDown(input, { key: "ArrowDown" });
    expect(input.getAttribute("aria-activedescendant")).toBe("symbol-search-option-1");

    fireEvent.keyDown(input, { key: "ArrowDown" });
    expect(input.getAttribute("aria-activedescendant")).toBe("symbol-search-option-2");

    fireEvent.keyDown(input, { key: "ArrowUp" });
    expect(input.getAttribute("aria-activedescendant")).toBe("symbol-search-option-1");

    fireEvent.keyDown(input, { key: "Enter" });
    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(onSelect).toHaveBeenCalledWith(RANKED[1]);
    // The chosen symbol replaces the query and the list closes.
    expect((input as HTMLInputElement).value).toBe("APPS");
    expect(input.getAttribute("aria-expanded")).toBe("false");
  });

  it("clamps at both ends instead of wrapping into nothing", async () => {
    const { input, onSelect } = await openWithResults();

    for (let i = 0; i < 10; i += 1) fireEvent.keyDown(input, { key: "ArrowDown" });
    expect(input.getAttribute("aria-activedescendant")).toBe("symbol-search-option-2");

    for (let i = 0; i < 10; i += 1) fireEvent.keyDown(input, { key: "ArrowUp" });
    expect(input.getAttribute("aria-activedescendant")).toBe("symbol-search-option-0");

    fireEvent.keyDown(input, { key: "Home" });
    expect(input.getAttribute("aria-activedescendant")).toBe("symbol-search-option-0");

    fireEvent.keyDown(input, { key: "End" });
    expect(input.getAttribute("aria-activedescendant")).toBe("symbol-search-option-2");

    fireEvent.keyDown(input, { key: "Enter" });
    expect(onSelect).toHaveBeenCalledWith(RANKED[2]);
  });

  it("closes on Escape without clearing the query", async () => {
    const { input, onSelect } = await openWithResults();

    fireEvent.keyDown(input, { key: "Escape" });
    expect(input.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByRole("listbox")).toBeNull();
    expect((input as HTMLInputElement).value).toBe("app");
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("closes when the pointer lands outside the box", async () => {
    const { input } = await openWithResults();
    expect(input.getAttribute("aria-expanded")).toBe("true");

    fireEvent.pointerDown(document.body);
    expect(screen.queryByRole("listbox")).toBeNull();
    expect((input as HTMLInputElement).value).toBe("app");
  });

  it("does not commit an Enter with no list open", async () => {
    const onSelect = vi.fn();
    mockFetch();
    render(<SymbolSearch onSelect={onSelect} />);
    const input = screen.getByRole("combobox");

    type(input, "app");
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onSelect).not.toHaveBeenCalled();
  });
});

/**
 * Staleness: the list on screen is the last *completed* query, not the text in
 * the box. Between a keystroke and the debounce firing, those differ, and Enter
 * must not commit a row that belongs to the text the user just replaced.
 */
describe("SymbolSearch staleness", () => {
  it("does not commit the previous query's hit after the text is edited", async () => {
    const onSelect = vi.fn();
    const fetchMock = mockFetch();
    render(<SymbolSearch onSelect={onSelect} />);
    const input = screen.getByRole("combobox");

    type(input, "app");
    await advance(SEARCH_DEBOUNCE_MS);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(screen.getAllByRole("option")).toHaveLength(HITS.length);

    // The dropdown still shows APP while "zzzz" is debouncing.
    type(input, "zzzz");
    expect(screen.getAllByRole("option")).toHaveLength(HITS.length);

    fireEvent.keyDown(input, { key: "Enter" });
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("commits the highlighted row once the list matches the current query", async () => {
    const onSelect = vi.fn();
    mockFetch();
    render(<SymbolSearch onSelect={onSelect} />);
    const input = screen.getByRole("combobox");

    type(input, "app");
    await advance(SEARCH_DEBOUNCE_MS);
    fireEvent.keyDown(input, { key: "ArrowDown" });

    expect(input.getAttribute("aria-activedescendant")).toBe("symbol-search-option-1");
    fireEvent.keyDown(input, { key: "Enter" });

    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(onSelect).toHaveBeenCalledWith(RANKED[1]);
    expect((input as HTMLInputElement).value).toBe("APPS");
    expect(screen.queryByRole("listbox")).toBeNull();
  });

  it("does nothing on Enter when the current query has zero results", async () => {
    const onSelect = vi.fn();
    mockFetch([]);
    render(<SymbolSearch onSelect={onSelect} />);
    const input = screen.getByRole("combobox");

    type(input, "zzzz");
    await advance(SEARCH_DEBOUNCE_MS);
    expect(screen.queryAllByRole("option")).toHaveLength(0);

    fireEvent.keyDown(input, { key: "Enter" });
    expect(onSelect).not.toHaveBeenCalled();
    expect((input as HTMLInputElement).value).toBe("zzzz");
  });

  it("drops a response that arrives after the query changed", async () => {
    const onSelect = vi.fn();
    let settle: ((body: unknown) => void) | undefined;
    const fetchMock = vi.fn(
      () =>
        new Promise<Response>((resolve) => {
          settle = (body: unknown) =>
            resolve(
              new Response(JSON.stringify(body), {
                status: 200,
                headers: { "content-type": "application/json" },
              }),
            );
        }),
    );
    vi.stubGlobal("fetch", fetchMock);
    render(<SymbolSearch onSelect={onSelect} />);
    const input = screen.getByRole("combobox");

    type(input, "app");
    await advance(SEARCH_DEBOUNCE_MS);
    expect(fetchMock).toHaveBeenCalledTimes(1);

    type(input, "zzzz");
    settle?.(HITS);
    await flush();

    expect(screen.queryAllByRole("option")).toHaveLength(0);
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("Escape dismisses without selecting, and the next Enter needs a fresh list", async () => {
    const onSelect = vi.fn();
    mockFetch();
    render(<SymbolSearch onSelect={onSelect} />);
    const input = screen.getByRole("combobox");

    type(input, "app");
    await advance(SEARCH_DEBOUNCE_MS);
    fireEvent.keyDown(input, { key: "ArrowDown" });
    fireEvent.keyDown(input, { key: "Escape" });

    expect(input.getAttribute("aria-expanded")).toBe("false");
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onSelect).not.toHaveBeenCalled();
    expect((input as HTMLInputElement).value).toBe("app");
  });
});

describe("SymbolSearch suggestion panel", () => {
  /**
   * Six hits, so "does the list show a scroll box rather than pushing the
   * header taller" is a real question. The API caps a page at eight; six is
   * enough to overflow a ten-row panel only when the rows are taller than a
   * line, which is the regression this block guards.
   */
  const MANY = [
    { symbol: "A", name: "Agilent Technologies Inc", exchange: "NYSE" },
    { symbol: "AA", name: "Alcoa Corp", exchange: "NYSE" },
    { symbol: "AAL", name: "American Airlines Group Inc", exchange: "NASDAQ" },
    { symbol: "AAPL", name: "Apple Inc", exchange: "NASDAQ" },
    { symbol: "ABBV", name: "AbbVie Inc", exchange: "NYSE" },
    { symbol: "ABNB", name: "Airbnb Inc", exchange: "NASDAQ" },
  ];

  async function openWithMany() {
    const onSelect = vi.fn();
    mockFetch(MANY);
    render(<SymbolSearch onSelect={onSelect} label="Ticker search" idPrefix="ticker-search" />);
    const input = screen.getByRole("combobox", { name: "Ticker search" });
    type(input, "a");
    await advance(SEARCH_DEBOUNCE_MS);
    return { input, onSelect };
  }

  it("draws the hits as one vertical listbox of at least five rows", async () => {
    await openWithMany();

    const list = screen.getByRole("listbox");
    expect(list.className).toContain("flex-col");
    expect(list.className).toContain("w-full");

    const options = screen.getAllByRole("option");
    expect(options.length).toBeGreaterThanOrEqual(5);
    for (const option of options) {
      expect(option.className).toContain("flex");
      expect(option.className).toContain("h-7");
      expect(option.className).toContain("items-center");
    }
  });

  it("scrolls inside a 320 px box instead of growing the header", async () => {
    await openWithMany();

    const list = screen.getByRole("listbox");
    expect(list.className).toContain("max-h-[320px]");
    expect(list.className).toContain("overflow-y-auto");
  });

  it("anchors the panel to the page, clear of any clipping header", async () => {
    await openWithMany();

    const panel = screen.getByTestId("symbol-search-panel");
    // A portal under <body> is what keeps an `overflow-hidden` ancestor from
    // cutting the list off; the classes say the geometry is its own.
    expect(panel.parentElement).toBe(document.body);
    expect(panel.className).toContain("fixed");
    expect(panel.className).toContain("z-50");
    expect(panel.getAttribute("data-placement")).toBe("below");
  });

  it("flips above the input when the viewport has no room below it", async () => {
    const originalHeight = window.innerHeight;
    Object.defineProperty(window, "innerHeight", { value: 240, configurable: true });
    try {
      mockFetch(MANY);
      render(<SymbolSearch />);
      const input = screen.getByRole("combobox");
      // The anchor is the field's outer root: input → shell → root.
      const anchor = input.parentElement?.parentElement as HTMLElement;
      vi.spyOn(anchor, "getBoundingClientRect").mockReturnValue({
        top: 180,
        bottom: 212,
        left: 12,
        right: 236,
        width: 224,
        height: 32,
        x: 12,
        y: 180,
        toJSON: () => ({}),
      } as DOMRect);

      type(input, "a");
      await advance(SEARCH_DEBOUNCE_MS);

      expect(screen.getByTestId("symbol-search-panel").getAttribute("data-placement")).toBe("above");
    } finally {
      Object.defineProperty(window, "innerHeight", { value: originalHeight, configurable: true });
    }
  });

  it("fills the input from a click and closes the list", async () => {
    const { input, onSelect } = await openWithMany();

    const option = screen.getAllByRole("option")[3];
    expect(option).toBeTruthy();
    fireEvent.mouseDown(option as HTMLElement);

    expect((input as HTMLInputElement).value).toBe("AAPL");
    expect(screen.queryByRole("listbox")).toBeNull();
    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(onSelect).toHaveBeenCalledWith(MANY[3]);
  });

  it("closes on Escape and keeps the typed text", async () => {
    const { input } = await openWithMany();

    fireEvent.keyDown(input, { key: "Escape" });
    expect(screen.queryByRole("listbox")).toBeNull();
    expect((input as HTMLInputElement).value).toBe("a");
  });

  /**
   * The owner's second requirement: the panel is a small table of stocks, not a
   * menu of the first three hits. Every row the API hands back is rendered into
   * one scrollable box — twenty here, the endpoint's own ceiling — and the rows
   * beyond the tenth are reached with a wheel, a trackpad or PageDown.
   */
  const PAGE = Array.from({ length: 20 }, (_, index) => ({
    symbol: `S${String(index).padStart(2, "0")}`,
    name: `Sample Company ${index}`,
    exchange: index % 2 === 0 ? "NASDAQ" : "NYSE",
  }));

  async function openWithPage() {
    const onSelect = vi.fn();
    const fetchMock = mockFetch(PAGE);
    render(<SymbolSearch onSelect={onSelect} />);
    const input = screen.getByRole("combobox");
    type(input, "s");
    await advance(SEARCH_DEBOUNCE_MS);
    return { input, onSelect, fetchMock };
  }

  it("renders every hit the API returns, not only the first handful", async () => {
    const { fetchMock } = await openWithPage();

    expect(fetchMock.mock.calls[0]?.[0]).toBe(`/symbols/search?q=s&limit=${SEARCH_LIMIT}`);
    expect(screen.getAllByRole("option")).toHaveLength(PAGE.length);
  });

  it("keeps a visible scrollbar on the list so it reads as a table", async () => {
    await openWithPage();

    const list = screen.getByRole("listbox");
    expect(list.className).toContain("overflow-y-auto");
    expect(list.className).toContain("[scrollbar-width:thin]");
    expect(list.className).toContain("[&::-webkit-scrollbar-thumb]:bg-line-strong");
  });

  it("survives a wheel gesture over the list, so the browser can scroll it", async () => {
    await openWithPage();

    const list = screen.getByRole("listbox");
    fireEvent.wheel(list, { deltaY: 240 });
    expect(screen.getByRole("listbox")).toBe(list);
    expect(screen.getAllByRole("option")).toHaveLength(PAGE.length);
  });

  it("moves by a screenful on PageDown/PageUp and to the ends on End/Home", async () => {
    const { input } = await openWithPage();

    fireEvent.keyDown(input, { key: "PageDown" });
    expect(input.getAttribute("aria-activedescendant")).toBe("symbol-search-option-8");

    fireEvent.keyDown(input, { key: "PageDown" });
    expect(input.getAttribute("aria-activedescendant")).toBe("symbol-search-option-16");

    fireEvent.keyDown(input, { key: "End" });
    expect(input.getAttribute("aria-activedescendant")).toBe("symbol-search-option-19");

    fireEvent.keyDown(input, { key: "PageUp" });
    expect(input.getAttribute("aria-activedescendant")).toBe("symbol-search-option-11");

    fireEvent.keyDown(input, { key: "Home" });
    expect(input.getAttribute("aria-activedescendant")).toBe("symbol-search-option-0");
  });

  it("scrolls the highlighted row into view as the arrows move", async () => {
    const scrolled = vi.fn();
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
      value: scrolled,
      configurable: true,
      writable: true,
    });
    try {
      const { input } = await openWithPage();

      fireEvent.keyDown(input, { key: "ArrowDown" });
      fireEvent.keyDown(input, { key: "End" });

      expect(scrolled).toHaveBeenCalled();
      const instances = scrolled.mock.instances;
      const calls = scrolled.mock.calls;
      expect(instances[instances.length - 1]).toBe(screen.getAllByRole("option")[19]);
      expect(calls[calls.length - 1]?.[0]).toEqual({ block: "nearest" });
    } finally {
      Reflect.deleteProperty(HTMLElement.prototype, "scrollIntoView");
    }
  });

  it("closes with Escape without closing the dialog that owns it", async () => {
    const onDialogKeyDown = vi.fn();
    mockFetch(HITS);
    render(
      // eslint-disable-next-line jsx-a11y/no-noninteractive-element-interactions
      <div role="dialog" aria-label="Add to watchlist" onKeyDown={onDialogKeyDown}>
        <SymbolSearch label="Search a ticker to add" idPrefix="add-to" />
      </div>,
    );
    const dialog = screen.getByRole("dialog", { name: "Add to watchlist" });
    const input = within(dialog).getByRole("combobox", { name: "Search a ticker to add" });
    type(input, "app");
    await advance(SEARCH_DEBOUNCE_MS);
    expect(within(dialog).getByRole("listbox")).toBeTruthy();

    fireEvent.keyDown(input, { key: "Escape" });

    expect(screen.queryByRole("listbox")).toBeNull();
    expect(onDialogKeyDown).not.toHaveBeenCalled();
  });

  it("portals into the dialog that owns the field, not out of it", async () => {
    mockFetch(HITS);
    render(
      <div role="dialog" aria-label="Add to watchlist">
        <SymbolSearch label="Search a ticker to add" idPrefix="add-to" />
      </div>,
    );
    const dialog = screen.getByRole("dialog", { name: "Add to watchlist" });
    const input = within(dialog).getByRole("combobox", { name: "Search a ticker to add" });

    type(input, "app");
    await advance(SEARCH_DEBOUNCE_MS);

    // A modal is a focus scope: the list has to be inside it, or the dialog's
    // own Tab trap and `within(dialog)` queries cannot see the rows.
    const list = within(dialog).getByRole("listbox", { name: "Symbol results" });
    expect(within(list).getAllByRole("option")).toHaveLength(HITS.length);
  });

  it("says how to narrow a full page instead of pretending it is all of them", async () => {
    await openWithPage();

    const more = screen.getByTestId("symbol-search-more");
    expect(more.textContent).toMatch(/20/);
    expect(more.textContent).toMatch(/typing/i);
    expect(more.className).toContain("text-fg-subtle");
  });
});

describe("SymbolSearch presentation", () => {
  it("shows the exchange for every hit", async () => {
    mockFetch();
    render(<SymbolSearch />);
    type(screen.getByRole("combobox"), "app");
    await advance(SEARCH_DEBOUNCE_MS);

    expect(screen.getAllByText("NASDAQ")).toHaveLength(2);
    expect(screen.getByText("NYSE")).toBeTruthy();
    expect(screen.getByText("Apple Inc")).toBeTruthy();
  });

  it("surfaces the API's own message instead of an empty list", async () => {
    mockFetch({ error: "unavailable", errorMessage: "universe is not ready" }, 503);
    render(<SymbolSearch />);
    type(screen.getByRole("combobox"), "app");
    await advance(SEARCH_DEBOUNCE_MS);

    expect(screen.getByText("universe is not ready")).toBeTruthy();
    expect(screen.queryAllByRole("option")).toHaveLength(0);
  });

  it("says so when nothing matched", async () => {
    mockFetch([]);
    render(<SymbolSearch />);
    type(screen.getByRole("combobox"), "zzzz");
    await advance(SEARCH_DEBOUNCE_MS);

    expect(screen.getByText(/No symbol or company matches/)).toBeTruthy();
  });
});
