/**
 * The Watchlist page's promises, on the rendered page:
 *
 * * an empty list asks the network for nothing at all;
 * * adding a symbol is one request for the whole list, and removing one drops it
 *   out of the next request rather than leaving it in a stale URL;
 * * the order is the reader's, and moving a row is not a market event — it costs
 *   no request;
 * * a symbol the answer does not price is named "Unavailable" in its row, not
 *   dropped and not drawn at $0.00;
 * * the list survives a reload because it is in `localStorage`, and two mounted
 *   pages agree the moment one writes;
 * * the page says, in words, that this is this browser's list and nothing else.
 */

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LOCAL_ONLY_SENTENCE } from "./LocalNotice";
import { WatchlistPage } from "./WatchlistPage";
import { WATCHLIST_KEY, createLocalStore, decodeWatchlist, portfolioStore, watchlistStore } from "./storage";

function quoteRow(
  symbol: string,
  price: number,
  changePct: number,
  name: string | null = null,
): Record<string, unknown> {
  return { symbol, name, price, change_pct: changePct, currency: "USD", class: "stock" };
}

function doc(rows: Record<string, unknown>[], missing: string[] = []) {
  return { as_of: "2026-10-31T23:40:00+00:00", rows, missing };
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function stub(body: unknown = doc([])) {
  const fetchMock = vi.fn(async (_input: RequestInfo | URL) => json(body));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

/** The symbols the request asked for, decoded, in the order they were sent. */
function asked(call: unknown): string[] {
  const url = String(call);
  const query = url.split("?")[1] ?? "";
  return (new URLSearchParams(query).get("symbols") ?? "").split(",").filter(Boolean);
}

function rowOrder(): string[] {
  return screen
    .queryAllByTestId(/^watchlist-row-/)
    .map((element) => element.getAttribute("data-symbol") ?? "");
}

beforeEach(() => {
  window.localStorage.clear();
  watchlistStore.write([]);
  portfolioStore.write([]);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("WatchlistPage", () => {
  it("draws an empty state that teaches the field, and asks for nothing", () => {
    const fetchMock = stub();
    render(<WatchlistPage />);

    const empty = screen.getByTestId("watchlist-empty");
    expect(empty.textContent).toMatch(/empty/i);
    expect(empty.textContent).toMatch(/field above/i);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.queryByTestId("watchlist-table")).toBeNull();
  });

  it("says the list is this browser's, in words", () => {
    stub();
    render(<WatchlistPage />);
    expect(screen.getByText(LOCAL_ONLY_SENTENCE)).toBeTruthy();
    expect(LOCAL_ONLY_SENTENCE).toMatch(/no account/i);
    expect(LOCAL_ONLY_SENTENCE).toMatch(/no sync/i);
  });

  it("adds a symbol, asks once for it, and draws the quote", async () => {
    const fetchMock = stub(doc([quoteRow("AAPL", 344.63, -1.2266, "Apple Inc.")]));
    const user = userEvent.setup();
    render(<WatchlistPage />);

    await user.type(screen.getByLabelText("Symbol"), "aapl");
    await user.click(screen.getByRole("button", { name: "Add" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(asked(fetchMock.mock.calls[0]?.[0])).toEqual(["AAPL"]);

    const row = await screen.findByTestId("watchlist-row-AAPL");
    await waitFor(() => expect(row.textContent).toContain("$344.63"));
    expect(row.textContent).toContain("Apple Inc.");
    expect(row.textContent).toContain("-1.23%");
    expect(screen.queryByTestId("watchlist-empty")).toBeNull();
    expect((screen.getByLabelText("Symbol") as HTMLInputElement).value).toBe("");
  });

  it("asks once for a whole list, in one parameter, whatever the order", async () => {
    watchlistStore.write(["NVDA", "AAPL", "MSFT"]);
    const fetchMock = stub(
      doc([
        quoteRow("NVDA", 180.1, 1),
        quoteRow("AAPL", 344.63, -1.2),
        quoteRow("MSFT", 521.91, -1.5),
      ]),
    );

    render(<WatchlistPage />);

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const url = String(fetchMock.mock.calls[0]?.[0]);
    expect(url.startsWith("/market/quotes?")).toBe(true);
    expect(url.split("symbols=")[0]).toBe("/market/quotes?");
    expect(asked(fetchMock.mock.calls[0]?.[0])).toEqual(["AAPL", "MSFT", "NVDA"]);
    // The table keeps the reader's order even though the request is sorted.
    expect(rowOrder()).toEqual(["NVDA", "AAPL", "MSFT"]);
  });

  it("refetches when the set changes, once per change and not once per render", async () => {
    const fetchMock = stub(
      doc([quoteRow("AAPL", 344.63, -1.2), quoteRow("MSFT", 521.91, -1.5)]),
    );
    const user = userEvent.setup();
    render(<WatchlistPage />);

    await user.type(screen.getByLabelText("Symbol"), "AAPL");
    await user.click(screen.getByRole("button", { name: "Add" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

    await user.type(screen.getByLabelText("Symbol"), "MSFT");
    await user.click(screen.getByRole("button", { name: "Add" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(asked(fetchMock.mock.calls[1]?.[0])).toEqual(["AAPL", "MSFT"]);

    // A render that changes no set is not a request.
    await screen.findByText("$344.63");
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("removes a row and drops the symbol out of the next request", async () => {
    watchlistStore.write(["AAPL", "MSFT"]);
    const fetchMock = stub(
      doc([quoteRow("AAPL", 344.63, -1.2), quoteRow("MSFT", 521.91, -1.5)]),
    );
    const user = userEvent.setup();
    render(<WatchlistPage />);

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    await user.click(screen.getByRole("button", { name: "Remove MSFT" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(asked(fetchMock.mock.calls[1]?.[0])).toEqual(["AAPL"]);
    expect(screen.queryByTestId("watchlist-row-MSFT")).toBeNull();
    expect(JSON.parse(window.localStorage.getItem(WATCHLIST_KEY) ?? "null")).toEqual(["AAPL"]);
  });

  it("reorders locally without asking the market anything", async () => {
    watchlistStore.write(["AAPL", "MSFT", "NVDA"]);
    const fetchMock = stub(
      doc([quoteRow("AAPL", 344.63, -1.2), quoteRow("MSFT", 521.91, -1.5), quoteRow("NVDA", 180, 1)]),
    );
    const user = userEvent.setup();
    render(<WatchlistPage />);

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    await user.click(screen.getByRole("button", { name: "Move MSFT up" }));

    expect(rowOrder()).toEqual(["MSFT", "AAPL", "NVDA"]);
    expect(JSON.parse(window.localStorage.getItem(WATCHLIST_KEY) ?? "null")).toEqual([
      "MSFT",
      "AAPL",
      "NVDA",
    ]);
    expect(fetchMock).toHaveBeenCalledTimes(1);

    // The first row has nowhere to go, and says so by being disabled.
    expect(
      (screen.getByRole("button", { name: "Move MSFT up" }) as HTMLButtonElement).disabled,
    ).toBe(true);
  });

  it("names a symbol the answer does not price, in its own row", async () => {
    watchlistStore.write(["AAPL", "TSLA"]);
    stub(doc([quoteRow("AAPL", 344.63, -1.2)], ["TSLA"]));

    render(<WatchlistPage />);

    const tsla = await screen.findByTestId("watchlist-row-TSLA");
    await waitFor(() => expect(tsla.textContent).toContain("Unavailable"));
    expect(tsla.getAttribute("data-missing")).toBe("true");
    expect(screen.getByTestId("watchlist-row-AAPL").getAttribute("data-missing")).toBe("false");
  });

  it("says quotes are unavailable, not the symbol, when the request fails", async () => {
    watchlistStore.write(["AAPL"]);
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => json({ error: "not_found", errorMessage: "Not Found" }, 404)),
    );

    render(<WatchlistPage />);

    const alert = await screen.findByRole("alert");
    await waitFor(() => expect(alert.textContent).toMatch(/does not serve quotes/i));
    const row = screen.getByTestId("watchlist-row-AAPL");
    expect(row.textContent).not.toContain("Unavailable");
    expect(row.getAttribute("data-missing")).toBe("false");
  });

  it("survives a reload, and every mounted page agrees", async () => {
    const fetchMock = stub(doc([quoteRow("AAPL", 344.63, -1.2)]));
    const user = userEvent.setup();
    const first = render(<WatchlistPage />);

    await user.type(screen.getByLabelText("Symbol"), "AAPL");
    await user.click(screen.getByRole("button", { name: "Add" }));
    await screen.findByTestId("watchlist-row-AAPL");

    // A fresh store instance is what a reload builds: same key, no memory.
    expect(createLocalStore(WATCHLIST_KEY, decodeWatchlist).read()).toEqual(["AAPL"]);

    first.unmount();
    render(<WatchlistPage />);
    expect(await screen.findByTestId("watchlist-row-AAPL")).toBeTruthy();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  });

  it("tells a second mounted page the moment the first writes", async () => {
    stub(doc([quoteRow("AAPL", 344.63, -1.2)]));
    const user = userEvent.setup();
    render(
      <div>
        <WatchlistPage />
        <WatchlistPage />
      </div>,
    );

    expect(screen.getAllByTestId("watchlist-empty")).toHaveLength(2);

    const [input] = screen.getAllByLabelText("Symbol");
    const [button] = screen.getAllByRole("button", { name: "Add" });
    await user.type(input as HTMLElement, "AAPL");
    await user.click(button as HTMLElement);

    await waitFor(() => expect(screen.getAllByTestId("watchlist-row-AAPL")).toHaveLength(2));
  });

  it("refuses a symbol it cannot ask for, without a request or a write", async () => {
    const fetchMock = stub();
    const user = userEvent.setup();
    render(<WatchlistPage />);

    await user.type(screen.getByLabelText("Symbol"), "A,B");
    await user.click(screen.getByRole("button", { name: "Add" }));

    const alert = await screen.findByTestId("watchlist-error");
    expect(alert.textContent).toMatch(/ticker/i);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(JSON.parse(window.localStorage.getItem(WATCHLIST_KEY) ?? "null")).toEqual([]);
  });

  it("keeps the row's mark and name inside the identity cell", async () => {
    watchlistStore.write(["AAPL"]);
    stub(doc([quoteRow("AAPL", 344.63, -1.2, "Apple Inc.")]));
    render(<WatchlistPage />);

    const row = await screen.findByTestId("watchlist-row-AAPL");
    expect(within(row).getByTestId("logo-img-AAPL")).toBeTruthy();
    expect(within(row).getByRole("link", { name: "AAPL" }).getAttribute("href")).toBe(
      "/app/stock/AAPL",
    );
  });
});
