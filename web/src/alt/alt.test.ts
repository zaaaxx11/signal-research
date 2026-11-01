/**
 * The alt table's arithmetic, asserted without a DOM.
 *
 * Three families of rule, and each one exists because the tempting shortcut is
 * a lie:
 *
 * * **Parsing drops what cannot be printed.** A row with no price, a negative
 *   price, a NaN or a blank symbol is not a row this page can draw, and it must
 *   not become `0.00`. A missing name is *not* a reason to drop a row — the
 *   symbol stands in for it.
 * * **Sorting is a total order.** Every comparison falls back to the symbol, so
 *   two equal changes never swap places between renders, and an absent value
 *   sorts last in both directions rather than pretending to be the smallest or
 *   the largest.
 * * **Filter then sort, never the other way round.** The filter narrows the
 *   input of the sort; a sort that ran first would still be decided by rows the
 *   reader cannot see.
 */

import { describe, expect, it } from "vitest";

import {
  ALT_PER,
  AltMalformed,
  altPath,
  classCopy,
  filterRows,
  formatPrice,
  formatReadAt,
  formatVolume,
  nextSort,
  pricePrecision,
  readAlt,
  sortRows,
  stockHref,
  visibleRows,
  type AltRow,
} from "./alt";

function row(overrides: Partial<AltRow> = {}): AltRow {
  return {
    symbol: "BTC-USD",
    name: "Bitcoin",
    price: 77399.29,
    changePct: 0.26,
    volume: 12424515584,
    currency: "USD",
    ...overrides,
  };
}

function document_(rows: unknown[], overrides: Record<string, unknown> = {}) {
  return {
    as_of: "2026-10-31T23:40:00Z",
    class: "crypto",
    universe_total: rows.length,
    measured: rows.length,
    per: ALT_PER,
    method: "yahoo_chart",
    rows,
    ...overrides,
  };
}

describe("altPath", () => {
  it("names the class and the page size, and nothing else", () => {
    expect(altPath("crypto")).toBe("/market/alt?class=crypto&per=50");
    expect(altPath("futures")).toBe("/market/alt?class=futures&per=50");
    expect(altPath("forex")).toBe("/market/alt?class=forex&per=50");
  });
});

describe("readAlt", () => {
  it("keeps the payload's own totals and stamps", () => {
    const report = readAlt(
      document_([{ symbol: "BTC-USD", name: "Bitcoin", price: 77399.29, change_pct: 0.26, volume: 1, currency: "USD" }], {
        universe_total: 41,
        measured: 40,
      }),
      "crypto",
    );

    expect(report.asOf).toBe("2026-10-31T23:40:00Z");
    expect(report.class).toBe("crypto");
    expect(report.universeTotal).toBe(41);
    expect(report.measured).toBe(40);
    expect(report.per).toBe(ALT_PER);
    expect(report.rows).toHaveLength(1);
    expect(report.rows[0]).toEqual({
      symbol: "BTC-USD",
      name: "Bitcoin",
      price: 77399.29,
      changePct: 0.26,
      volume: 1,
      currency: "USD",
    });
  });

  it("drops a row with no price instead of drawing a zero", () => {
    const report = readAlt(
      document_([
        { symbol: "BTC-USD", name: "Bitcoin", price: null, change_pct: 1, volume: 1, currency: "USD" },
        { symbol: "ETH-USD", name: "Ether", price: 2249.18, change_pct: null, volume: null, currency: "USD" },
      ]),
      "crypto",
    );

    expect(report.rows.map((candidate) => candidate.symbol)).toEqual(["ETH-USD"]);
    expect(report.rows[0]?.changePct).toBeNull();
    expect(report.rows[0]?.volume).toBeNull();
  });

  it("drops a negative, an infinite and a NaN price", () => {
    const report = readAlt(
      document_([
        { symbol: "NEG", name: null, price: -1, change_pct: 1, volume: null, currency: "USD" },
        { symbol: "NAN", name: null, price: Number.NaN, change_pct: 1, volume: null, currency: "USD" },
        { symbol: "INF", name: null, price: Number.POSITIVE_INFINITY, change_pct: 1, volume: null, currency: "USD" },
        { symbol: "OK", name: null, price: 0.42, change_pct: 0, volume: 0, currency: "USD" },
      ]),
      "crypto",
    );

    expect(report.rows.map((candidate) => candidate.symbol)).toEqual(["OK"]);
  });

  it("keeps a row whose name is absent and forbids an empty one", () => {
    const report = readAlt(
      document_([
        { symbol: "ES=F", name: null, price: 7614, change_pct: -1.81, volume: 1200, currency: "USD" },
        { symbol: "NQ=F", name: "   ", price: 28534, change_pct: 0.4, volume: 900, currency: "USD" },
      ]),
      "futures",
    );

    expect(report.rows.map((candidate) => candidate.name)).toEqual([null, null]);
  });

  it("uppercases the symbol, refuses a blank one and keeps the first of a duplicate", () => {
    const report = readAlt(
      document_([
        { symbol: " btc-usd ", name: "Bitcoin", price: 1, change_pct: 1, volume: null, currency: "USD" },
        { symbol: "BTC-USD", name: "Duplicate", price: 2, change_pct: 1, volume: null, currency: "USD" },
        { symbol: "  ", name: "Blank", price: 3, change_pct: 1, volume: null, currency: "USD" },
      ]),
      "crypto",
    );

    expect(report.rows).toHaveLength(1);
    expect(report.rows[0]?.symbol).toBe("BTC-USD");
    expect(report.rows[0]?.name).toBe("Bitcoin");
  });

  it("refuses a payload that is not a table", () => {
    expect(() => readAlt(null, "crypto")).toThrow(AltMalformed);
    expect(() => readAlt({ rows: "nope" }, "crypto")).toThrow(AltMalformed);
    expect(() => readAlt([], "crypto")).toThrow(AltMalformed);
  });

  it("is empty, not malformed, when every row was unusable", () => {
    const report = readAlt(
      document_([{ symbol: "BTC-USD", price: null, change_pct: 1, volume: 1, currency: "USD" }]),
      "crypto",
    );
    expect(report.rows).toEqual([]);
  });
});

describe("filterRows", () => {
  const rows = [
    row({ symbol: "BTC-USD", name: "Bitcoin" }),
    row({ symbol: "ETH-USD", name: "Ether" }),
    row({ symbol: "DOGE-USD", name: null }),
  ];

  it("matches a symbol or a name, case-insensitively", () => {
    expect(filterRows(rows, "btc").map((candidate) => candidate.symbol)).toEqual(["BTC-USD"]);
    expect(filterRows(rows, "ETHER").map((candidate) => candidate.symbol)).toEqual(["ETH-USD"]);
  });

  it("falls back to the symbol when the name is absent", () => {
    expect(filterRows(rows, "doge").map((candidate) => candidate.symbol)).toEqual(["DOGE-USD"]);
  });

  it("trims the query and returns everything for a blank one", () => {
    expect(filterRows(rows, "   ")).toHaveLength(3);
    expect(filterRows(rows, "  eth ")).toHaveLength(1);
  });

  it("returns nothing rather than everything when nothing matches", () => {
    expect(filterRows(rows, "zzz")).toEqual([]);
  });
});

describe("sortRows", () => {
  const rows = [
    row({ symbol: "ETH-USD", name: "Ether", price: 2249.18, changePct: -1.2 }),
    row({ symbol: "BTC-USD", name: "Bitcoin", price: 77399.29, changePct: 0 }),
    row({ symbol: "DOGE-USD", name: null, price: 0.17, changePct: 4.5 }),
    // Equal change on purpose: the tie must be broken by symbol, not by order.
    row({ symbol: "ADA-USD", name: "Cardano", price: 0.44, changePct: 4.5 }),
  ];

  it("sorts by price, both ways", () => {
    expect(sortRows(rows, { key: "price", dir: "desc" }).map((candidate) => candidate.symbol)).toEqual([
      "BTC-USD",
      "ETH-USD",
      "ADA-USD",
      "DOGE-USD",
    ]);
    expect(sortRows(rows, { key: "price", dir: "asc" }).map((candidate) => candidate.symbol)).toEqual([
      "DOGE-USD",
      "ADA-USD",
      "ETH-USD",
      "BTC-USD",
    ]);
  });

  it("sorts by change and breaks a tie by symbol, deterministically", () => {
    const descending = sortRows(rows, { key: "change", dir: "desc" }).map((candidate) => candidate.symbol);
    expect(descending).toEqual(["ADA-USD", "DOGE-USD", "BTC-USD", "ETH-USD"]);
    // Same input, same answer — twice, so a comparator with a hidden
    // dependency on array order cannot pass.
    expect(sortRows([...rows].reverse(), { key: "change", dir: "desc" }).map((candidate) => candidate.symbol)).toEqual(
      descending,
    );
  });

  it("sorts by symbol as text, both ways", () => {
    expect(sortRows(rows, { key: "symbol", dir: "asc" }).map((candidate) => candidate.symbol)).toEqual([
      "ADA-USD",
      "BTC-USD",
      "DOGE-USD",
      "ETH-USD",
    ]);
    expect(sortRows(rows, { key: "symbol", dir: "desc" }).map((candidate) => candidate.symbol)).toEqual([
      "ETH-USD",
      "DOGE-USD",
      "BTC-USD",
      "ADA-USD",
    ]);
  });

  it("puts an absent value last in both directions", () => {
    const withAbsent = [...rows, row({ symbol: "SOL-USD", name: "Solana", price: 150, changePct: null })];
    expect(sortRows(withAbsent, { key: "change", dir: "desc" }).at(-1)?.symbol).toBe("SOL-USD");
    expect(sortRows(withAbsent, { key: "change", dir: "asc" }).at(-1)?.symbol).toBe("SOL-USD");
  });

  it("leaves the given order alone when no column is chosen", () => {
    expect(sortRows(rows, null).map((candidate) => candidate.symbol)).toEqual([
      "ETH-USD",
      "BTC-USD",
      "DOGE-USD",
      "ADA-USD",
    ]);
  });

  it("does not mutate its input", () => {
    const before = rows.map((candidate) => candidate.symbol);
    sortRows(rows, { key: "price", dir: "asc" });
    expect(rows.map((candidate) => candidate.symbol)).toEqual(before);
  });
});

describe("nextSort", () => {
  it("opens a numeric column descending and a text column ascending", () => {
    expect(nextSort(null, "price")).toEqual({ key: "price", dir: "desc" });
    expect(nextSort(null, "change")).toEqual({ key: "change", dir: "desc" });
    expect(nextSort(null, "symbol")).toEqual({ key: "symbol", dir: "asc" });
  });

  it("toggles the same column and starts over on a new one", () => {
    expect(nextSort({ key: "price", dir: "desc" }, "price")).toEqual({ key: "price", dir: "asc" });
    expect(nextSort({ key: "price", dir: "asc" }, "price")).toEqual({ key: "price", dir: "desc" });
    expect(nextSort({ key: "price", dir: "asc" }, "change")).toEqual({ key: "change", dir: "desc" });
    expect(nextSort({ key: "change", dir: "desc" }, "symbol")).toEqual({ key: "symbol", dir: "asc" });
  });
});

describe("visibleRows", () => {
  const rows = [row({ symbol: "BTC-USD", name: "Bitcoin", price: 77399.29 }), row({ symbol: "ETH-USD", name: "Ether", price: 2249.18 })];

  it("filters first, then sorts what is left", () => {
    const visible = visibleRows(rows, "usd", { key: "price", dir: "asc" });
    expect(visible.map((candidate) => candidate.symbol)).toEqual(["ETH-USD", "BTC-USD"]);
  });

  it("cannot be re-ordered by a row the filter removed", () => {
    const visible = visibleRows(rows, "eth", { key: "price", dir: "desc" });
    expect(visible.map((candidate) => candidate.symbol)).toEqual(["ETH-USD"]);
  });
});

describe("formatting", () => {
  it("prints a forex pair at four places and a large price at two", () => {
    expect(pricePrecision(1.1166)).toBe(4);
    expect(pricePrecision(77399.29)).toBe(2);
    expect(formatPrice(1.1166)).toBe("$1.1166");
    expect(formatPrice(77399.29)).toBe("$77,399.29");
    expect(formatPrice(7614)).toBe("$7,614.00");
  });

  it("gives a sub-cent token enough places to be its own number", () => {
    expect(pricePrecision(0.0000099)).toBe(8);
    expect(formatPrice(0.0000099)).toBe("$0.00000990");
    expect(pricePrecision(0.0006)).toBe(6);
    expect(formatPrice(0.0006)).toBe("$0.000600");
  });

  it("never prints a sub-precision price as zero", () => {
    expect(formatPrice(1e-10)).toBe("<$0.00000001");
  });

  it("compacts a volume and leaves an absent one absent", () => {
    expect(formatVolume(12424515584)).toBe("12.42B");
    expect(formatVolume(0)).toBe("0");
    expect(formatVolume(null)).toBeNull();
  });

  it("stamps the read time in UTC, and refuses an unreadable one", () => {
    expect(formatReadAt("2026-10-31T23:40:00Z")).toBe("31 Oct 2026, 23:40 UTC");
    expect(formatReadAt("2026-11-01T07:05:00+08:00")).toBe("31 Oct 2026, 23:05 UTC");
    expect(formatReadAt("not a date")).toBeNull();
    expect(formatReadAt("")).toBeNull();
  });
});

describe("stockHref", () => {
  it("does not invent a detail page the API cannot answer", () => {
    // `/engine/fundamentals/BTC-USD` answers 404 security_not_found on the
    // running deployment, and the shell's route pattern refuses `=` outright,
    // so these rows are text. When that changes, this expectation changes with
    // it — `ALT_SYMBOLS_LINKABLE` is the one switch.
    expect(stockHref("BTC-USD")).toBeNull();
    expect(stockHref("ES=F")).toBeNull();
    expect(stockHref("EURUSD=X")).toBeNull();
  });
});

describe("classCopy", () => {
  it("names each class, says what it is and how it is quoted", () => {
    const crypto = classCopy("crypto");
    expect(crypto.title).toBe("Crypto");
    expect(crypto.blurb).toMatch(/digital asset/i);
    expect(crypto.blurb).toMatch(/US dollar/i);
    expect(crypto.amountLabel).toBe("Volume");
    expect(crypto.hasAmount).toBe(true);

    const futures = classCopy("futures");
    expect(futures.title).toBe("Futures");
    expect(futures.blurb).toMatch(/contract/i);
    expect(futures.amountLabel).toBe("Contract");
    expect(futures.hasAmount).toBe(true);

    const forex = classCopy("forex");
    expect(forex.title).toBe("Forex");
    expect(forex.blurb).toMatch(/currency pair/i);
    expect(forex.amountLabel).toBe("Volume");
    // Spot FX has no consolidated volume: the column must not print a count it
    // does not have.
    expect(forex.hasAmount).toBe(false);
  });
});
