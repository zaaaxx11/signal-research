import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SEARCH_DEBOUNCE_MS, SymbolSearch } from "./SymbolSearch";

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
    expect(fetchMock.mock.calls[0]?.[0]).toBe("/symbols/search?q=app&limit=8");
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
    expect(fetchMock.mock.calls[0]?.[0]).toBe("/symbols/search?q=app&limit=8");
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
    expect(fetchMock.mock.calls[0]?.[0]).toBe("/symbols/search?q=aapl&limit=8");
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
    expect(onSelect).toHaveBeenCalledWith(HITS[1]);
    // The chosen symbol replaces the query and the list closes.
    expect((input as HTMLInputElement).value).toBe("AAPL");
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
    expect(onSelect).toHaveBeenCalledWith(HITS[2]);
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
    expect(onSelect).toHaveBeenCalledWith(HITS[1]);
    expect((input as HTMLInputElement).value).toBe("AAPL");
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
