/**
 * The `/market/quotes` client's promises:
 *
 * * the path is one request for N symbols, in a stable order, so a reorder of
 *   the watchlist is not a new address;
 * * a body that is not a quote list is refused loudly, not drawn as an empty
 *   market;
 * * a symbol the answer does not price is reported as missing — including one
 *   the caller asked for that appears in neither `rows` nor `missing`, which is
 *   exactly the case a naive reader would silently drop.
 */

import { describe, expect, it } from "vitest";

import {
  QuotesMalformed,
  canonicalSymbols,
  quoteFor,
  quotesPath,
  readQuotes,
} from "./quotes";

const DOC = {
  as_of: "2026-10-31T23:40:00+00:00",
  rows: [
    {
      symbol: "AAPL",
      name: "Apple Inc.",
      price: 344.63,
      change_pct: -1.2266,
      currency: "USD",
      class: "stock",
    },
    {
      symbol: "BTC-USD",
      name: "Bitcoin",
      price: 77393.49,
      change_pct: 0.2475,
      currency: "USD",
      class: "crypto",
    },
  ],
  missing: ["TSLA"],
};

describe("quotesPath", () => {
  it("asks for every symbol once, in one parameter", () => {
    expect(quotesPath(["AAPL", "MSFT", "BTC-USD"])).toBe(
      "/market/quotes?symbols=AAPL,MSFT,BTC-USD",
    );
  });

  it("has no address when there is nothing to ask", () => {
    expect(quotesPath([])).toBe("");
  });

  it("canonicalises to a sorted, deduped request so order is not an address", () => {
    expect(canonicalSymbols(["MSFT", "AAPL", "MSFT"])).toEqual(["AAPL", "MSFT"]);
    expect(quotesPath(canonicalSymbols(["MSFT", "AAPL"]))).toBe(
      quotesPath(canonicalSymbols(["AAPL", "MSFT"])),
    );
  });
});

describe("readQuotes", () => {
  it("maps the wire document onto rows and a lookup", () => {
    const report = readQuotes(DOC);
    expect(report.asOf).toBe("2026-10-31T23:40:00+00:00");
    expect(report.rows).toHaveLength(2);
    expect(report.rows[0]).toEqual({
      symbol: "AAPL",
      name: "Apple Inc.",
      price: 344.63,
      changePct: -1.2266,
      currency: "USD",
      assetClass: "stock",
    });
    expect(report.missing).toEqual(["TSLA"]);
    expect(quoteFor(report, "BTC-USD")?.price).toBe(77393.49);
    expect(quoteFor(report, "TSLA")).toBeNull();
  });

  it("names a requested symbol that the answer neither priced nor listed", () => {
    const report = readQuotes(DOC, ["AAPL", "TSLA", "ZZZZ"]);
    expect(report.missing).toEqual(["TSLA", "ZZZZ"]);
  });

  it("treats a row without a usable price as missing, never as zero", () => {
    const report = readQuotes({
      as_of: null,
      rows: [
        { symbol: "AAPL", price: 344.63, change_pct: -1.2 },
        { symbol: "MSFT", price: null },
        { symbol: "NVDA", price: "not a number" },
        { price: 12 },
        "junk",
      ],
      missing: [],
    });

    expect(report.rows.map((row) => row.symbol)).toEqual(["AAPL"]);
    expect(report.missing).toEqual(["MSFT", "NVDA"]);
    expect(report.rows[0]?.name).toBeNull();
    expect(report.rows[0]?.currency).toBeNull();
  });

  it("refuses a body that is not a quote document", () => {
    for (const body of [null, 42, [], { rows: 5 }, { rows: {} }]) {
      expect(() => readQuotes(body)).toThrow(QuotesMalformed);
    }
  });
});
