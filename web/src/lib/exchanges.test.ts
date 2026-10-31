import { describe, expect, it } from "vitest";

import { exchangeLabel, exchangeTitle, sortExchanges } from "./exchanges";

/**
 * The label leaf: one table, three functions, and the two properties both
 * surfaces depend on.
 *
 * The grid passes codes (`XNAS`), the detail panel passes whatever the security
 * master said (`Nasdaq`), and the chip passes codes. A label function that
 * rewrote either vocabulary — or that returned `""` for something it did not
 * recognise — would silently delete a value from a row it was asked to
 * describe. And because it runs once per exchange cell per render, its cost is
 * part of the 100-row format pass: hence the guard at the bottom.
 */

/** The 17 codes the product has issued, from the universe and depth exports. */
const OBSERVED = [
  "XNAS",
  "XNYS",
  "XASE",
  "XCBO",
  "SSE",
  "SIX",
  "LSE",
  "KRX",
  "HKEX",
  "TWSE",
  "TSE",
  "XETR",
  "TSX",
  "TADAWUL",
  "SZSE",
  "EURONEXT",
] as const;

describe("exchangeLabel", () => {
  it("labels every code the product issues, and the four US venues exactly once", () => {
    expect(exchangeLabel("XNAS")).toBe("NASDAQ");
    expect(exchangeLabel("XNYS")).toBe("NYSE");
    expect(exchangeLabel("XASE")).toBe("AMEX");
    expect(exchangeLabel("XCBO")).toBe("CBOE");
    expect(exchangeLabel("SSE")).toBe("Shanghai");
    expect(exchangeLabel("EURONEXT")).toBe("Euronext");
  });

  it("has a label for every observed code that is not its own code", () => {
    for (const code of OBSERVED) {
      const label = exchangeLabel(code);
      expect(label.length, `${code} has no label`).toBeGreaterThan(0);
      expect(label, `${code} would print a raw code in the chip`).not.toBe(code);
    }
  });

  it("is case-insensitive on a code", () => {
    expect(exchangeLabel("xnas")).toBe("NASDAQ");
    expect(exchangeLabel("Xnys")).toBe("NYSE");
    expect(exchangeLabel("  xase  ")).toBe("AMEX");
  });

  it("leaves a name, an unknown code and a blank alone", () => {
    // The detail payload already reads like a name: it must not be re-cased or
    // looked up again, or `Nasdaq` would become `NASDAQ` for no reason.
    expect(exchangeLabel("Nasdaq")).toBe("Nasdaq");
    expect(exchangeLabel("NASDAQ")).toBe("NASDAQ");
    expect(exchangeLabel("NYSE American")).toBe("NYSE American");
    expect(exchangeLabel("XWAR")).toBe("XWAR");
    expect(exchangeLabel("BCS")).toBe("BCS");
    expect(exchangeLabel("")).toBe("");
  });

  it("never returns empty for a non-empty input", () => {
    // The detail panel counts *non-empty* cells to decide what a symbol
    // reported; an unknown code that labelled to "" would vanish from the list.
    for (const value of ["XWAR", "  ", "zzz", "纳斯达克"]) {
      expect(exchangeLabel(value).length).toBeGreaterThan(0);
    }
  });

  it("costs one map lookup: 100,000 calls stay far under 50 ms", () => {
    // ~3 ms today. The regression this catches is a formatter built on
    // `Intl.DisplayNames` or a `new Map` per call, which turns a 100-row format
    // pass into a visible keypress delay.
    const started = performance.now();
    let sink = 0;
    for (let index = 0; index < 100_000; index += 1) {
      sink += exchangeLabel(index % 2 === 0 ? "XNAS" : "XWAR").length;
    }
    const elapsed = performance.now() - started;
    expect(sink).toBeGreaterThan(0);
    expect(elapsed, `100k labels took ${elapsed.toFixed(1)} ms`).toBeLessThan(50);
  });
});

describe("exchangeTitle", () => {
  it("pairs the name with the code it came from", () => {
    expect(exchangeTitle("XNAS")).toBe("NASDAQ (XNAS)");
    expect(exchangeTitle("XASE")).toBe("AMEX (XASE)");
  });

  it("does not repeat a value that is already its own label", () => {
    expect(exchangeTitle("XWAR")).toBe("XWAR");
    expect(exchangeTitle("Nasdaq")).toBe("Nasdaq");
  });
});

describe("sortExchanges", () => {
  it("puts the US majors first, then names, then unknown codes", () => {
    expect(sortExchanges(["BCS", "XLON", "XNYS", "XNAS"])).toEqual(["XNAS", "XNYS", "BCS", "XLON"]);
    expect(sortExchanges(["LSE", "XCBO", "XNAS", "XASE"])).toEqual(["XNAS", "XASE", "XCBO", "LSE"]);
  });

  it("orders known codes by name and unknown codes by code", () => {
    // Shanghai (SSE) before Shenzhen (SZSE) by name; two unknown codes have
    // only their code to sort by.
    expect(sortExchanges(["SZSE", "SSE"])).toEqual(["SSE", "SZSE"]);
    expect(sortExchanges(["ZZZZ", "AAAA"])).toEqual(["AAAA", "ZZZZ"]);
  });

  it("collapses duplicates, normalises the write form, and never mutates its input", () => {
    const input = ["xnas", "XNAS", "XNYS", "", "  "];
    const frozen = [...input];
    expect(sortExchanges(input)).toEqual(["XNAS", "XNYS"]);
    expect(input).toEqual(frozen);
  });
});
