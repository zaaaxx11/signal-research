/**
 * The local store's promises:
 *
 * * a write survives a *new* store instance reading the same key — that is what
 *   "saved in this browser" means, and it is the only persistence claim these
 *   pages make;
 * * a corrupt byte string reads as empty, not as a crash and not as a half-list;
 * * a quantity that is not a positive number never enters the store, and a row
 *   that has one is dropped on read rather than drawn at zero;
 * * one mounted list tells every other mounted list — same document, through the
 *   in-process listeners; another document, through the `storage` event. The
 *   second is a convenience, not a claim: see the note on two tabs below.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  MAX_SYMBOLS,
  PORTFOLIO_KEY,
  WATCHLIST_KEY,
  addHolding,
  addSymbol,
  createLocalStore,
  decodePortfolio,
  decodeWatchlist,
  moveSymbol,
  normalizeSymbol,
  portfolioStore,
  removeHolding,
  removeSymbol,
  watchlistStore,
} from "./storage";

describe("localStorage store", () => {
  beforeEach(() => {
    window.localStorage.clear();
    // The stores cache their parsed snapshot, so a test that clears storage has
    // to tell them the truth: write the empty value through the same path a
    // page uses.
    watchlistStore.write([]);
    portfolioStore.write([]);
  });

  it("round-trips a watchlist so a fresh instance sees it", () => {
    watchlistStore.write(["AAPL", "MSFT", "BTC-USD"]);

    // A different instance is what a page reload builds: same key, no memory.
    const afterReload = createLocalStore(WATCHLIST_KEY, decodeWatchlist);
    expect(afterReload.read()).toEqual(["AAPL", "MSFT", "BTC-USD"]);
    expect(JSON.parse(window.localStorage.getItem(WATCHLIST_KEY) ?? "null")).toEqual([
      "AAPL",
      "MSFT",
      "BTC-USD",
    ]);
  });

  it("round-trips holdings, including a fractional quantity", () => {
    portfolioStore.write([
      { symbol: "AAPL", quantity: 10, cost: 100 },
      { symbol: "MSFT", quantity: 2.5, cost: 400.1 },
    ]);

    const afterReload = createLocalStore(PORTFOLIO_KEY, decodePortfolio);
    expect(afterReload.read()).toEqual([
      { symbol: "AAPL", quantity: 10, cost: 100 },
      { symbol: "MSFT", quantity: 2.5, cost: 400.1 },
    ]);
  });

  it("reads corrupt JSON as empty instead of throwing", () => {
    for (const junk of ["{not json", "", "null", "42", '"AAPL"', '{"symbols":["AAPL"]}']) {
      window.localStorage.setItem(WATCHLIST_KEY, junk);
      const fresh = createLocalStore(WATCHLIST_KEY, decodeWatchlist);
      expect(fresh.read()).toEqual([]);
    }

    window.localStorage.setItem(PORTFOLIO_KEY, "[{");
    expect(createLocalStore(PORTFOLIO_KEY, decodePortfolio).read()).toEqual([]);
  });

  it("keeps only symbols it can actually ask the API for", () => {
    expect(decodeWatchlist(["aapl", "AAPL", " msft ", "", 7, null, "a,b", "BTC-USD"])).toEqual([
      "AAPL",
      "MSFT",
      "BTC-USD",
    ]);
  });

  it("drops a holding whose quantity is not a positive number", () => {
    const raw = [
      { symbol: "AAPL", quantity: 0, cost: 10 },
      { symbol: "MSFT", quantity: -3, cost: 10 },
      { symbol: "TSLA", quantity: "2", cost: 10 },
      { symbol: "NVDA", quantity: 3, cost: -1 },
      { symbol: "AMD", quantity: 3, cost: 50 },
      "not a holding",
      null,
    ];
    expect(decodePortfolio(raw)).toEqual([{ symbol: "AMD", quantity: 3, cost: 50 }]);
  });

  it("tells every listener, and stops when they unsubscribe", () => {
    const listener = vi.fn();
    const off = watchlistStore.subscribe(listener);

    watchlistStore.write(["AAPL"]);
    expect(listener).toHaveBeenCalledTimes(1);

    off();
    watchlistStore.write(["MSFT"]);
    expect(listener).toHaveBeenCalledTimes(1);
  });

  it("re-reads on another document's storage event, and ignores other keys", () => {
    const listener = vi.fn();
    watchlistStore.subscribe(listener);

    window.localStorage.setItem(WATCHLIST_KEY, JSON.stringify(["NVDA"]));
    window.dispatchEvent(new StorageEvent("storage", { key: WATCHLIST_KEY }));

    expect(listener).toHaveBeenCalledTimes(1);
    expect(watchlistStore.read()).toEqual(["NVDA"]);

    window.dispatchEvent(new StorageEvent("storage", { key: PORTFOLIO_KEY }));
    expect(listener).toHaveBeenCalledTimes(1);
    expect(watchlistStore.read()).toEqual(["NVDA"]);
  });

  it("accepts a symbol however a reader types it, and refuses the rest", () => {
    expect(normalizeSymbol("  aapl ")).toBe("AAPL");
    expect(normalizeSymbol("brk-b")).toBe("BRK-B");
    expect(normalizeSymbol("btc-usd")).toBe("BTC-USD");
    expect(normalizeSymbol("")).toBeNull();
    expect(normalizeSymbol("   ")).toBeNull();
    expect(normalizeSymbol("A,B")).toBeNull();
    expect(normalizeSymbol("A B")).toBeNull();
    expect(normalizeSymbol("AAPL;DROP")).toBeNull();
  });

  it("adds once, refuses a duplicate, a blank and the 51st symbol", () => {
    const first = addSymbol([], " aapl ");
    expect(first).toEqual({ ok: true, symbols: ["AAPL"] });

    const duplicate = addSymbol(["AAPL"], "aapl");
    expect(duplicate.ok).toBe(false);
    if (!duplicate.ok) expect(duplicate.error).toMatch(/already/i);

    const blank = addSymbol(["AAPL"], "   ");
    expect(blank.ok).toBe(false);

    const full = Array.from({ length: MAX_SYMBOLS }, (_, index) => `S${index}`);
    const over = addSymbol(full, "EXTRA");
    expect(over.ok).toBe(false);
    if (!over.ok) expect(over.error).toMatch(new RegExp(String(MAX_SYMBOLS)));
  });

  it("removes and reorders, and refuses a move off either end", () => {
    expect(removeSymbol(["AAPL", "MSFT", "NVDA"], "MSFT")).toEqual(["AAPL", "NVDA"]);
    expect(removeSymbol(["AAPL"], "MSFT")).toEqual(["AAPL"]);

    const list = ["AAPL", "MSFT", "NVDA"];
    expect(moveSymbol(list, "MSFT", -1)).toEqual(["MSFT", "AAPL", "NVDA"]);
    expect(moveSymbol(list, "MSFT", 1)).toEqual(["AAPL", "NVDA", "MSFT"]);
    expect(moveSymbol(list, "AAPL", -1)).toBeNull();
    expect(moveSymbol(list, "NVDA", 1)).toBeNull();
    // The original is untouched: the caller decides whether to write.
    expect(list).toEqual(["AAPL", "MSFT", "NVDA"]);
  });

  it("refuses a holding with no symbol, a zero or negative quantity, a bad cost", () => {
    const zero = addHolding([], { symbol: "AAPL", quantity: "0", cost: "100" });
    expect(zero.ok).toBe(false);
    if (!zero.ok) expect(zero.field).toBe("quantity");

    const negative = addHolding([], { symbol: "AAPL", quantity: "-2", cost: "100" });
    expect(negative.ok).toBe(false);
    if (!negative.ok) expect(negative.field).toBe("quantity");

    const word = addHolding([], { symbol: "AAPL", quantity: "ten", cost: "100" });
    expect(word.ok).toBe(false);

    const noCost = addHolding([], { symbol: "AAPL", quantity: "1", cost: "" });
    expect(noCost.ok).toBe(false);
    if (!noCost.ok) expect(noCost.field).toBe("cost");

    const negativeCost = addHolding([], { symbol: "AAPL", quantity: "1", cost: "-5" });
    expect(negativeCost.ok).toBe(false);

    const noSymbol = addHolding([], { symbol: " ", quantity: "1", cost: "5" });
    expect(noSymbol.ok).toBe(false);
    if (!noSymbol.ok) expect(noSymbol.field).toBe("symbol");
  });

  it("adds a holding, refuses a second row for the same symbol, and removes it", () => {
    const added = addHolding([], { symbol: "msft", quantity: "2.5", cost: "400.10" });
    expect(added).toEqual({
      ok: true,
      holdings: [{ symbol: "MSFT", quantity: 2.5, cost: 400.1 }],
    });

    if (!added.ok) throw new Error("fixture did not add");
    const duplicate = addHolding(added.holdings, { symbol: "MSFT", quantity: "1", cost: "1" });
    expect(duplicate.ok).toBe(false);
    if (!duplicate.ok) expect(duplicate.field).toBe("duplicate");

    expect(removeHolding(added.holdings, "MSFT")).toEqual([]);
  });
});
