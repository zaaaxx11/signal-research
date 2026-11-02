import { describe, expect, it } from "vitest";

import type { SymbolHit } from "@/lib/api";

import { rankSuggestions, tierOf } from "./symbolRank";

/**
 * The suggestion order the ticker fields promise: a symbol that starts with
 * the typed letters, then one that only contains them, then a company name.
 * The list is short, so its order is the whole feature.
 */

function hit(symbol: string, name: string, exchange = "NASDAQ"): SymbolHit {
  return { symbol, name, exchange };
}

const HITS = [
  hit("GOOGL", "Alphabet Inc"),
  hit("APP", "AppLovin Corp"),
  hit("APPS", "Digital Turbine Inc", "NYSE"),
  hit("AAPL", "Apple Inc"),
  hit("MSFT", "Microsoft Corp"),
];

describe("tierOf", () => {
  it("puts the exact symbol first, then the prefix, then contains, then the name", () => {
    expect(tierOf(hit("APP", "AppLovin Corp"), "app")).toBe("symbol-exact");
    expect(tierOf(hit("APPW", "Something Else"), "app")).toBe("symbol-prefix");
    expect(tierOf(hit("XAPP", "Something Else"), "app")).toBe("symbol-contains");
    expect(tierOf(hit("XYZ", "Appian Corp"), "app")).toBe("name-prefix");
    expect(tierOf(hit("XYZ", "Happy Appian"), "app")).toBe("name-contains");
    expect(tierOf(hit("XYZ", "Unrelated"), "app")).toBeNull();
  });

  it("folds case and collapses whitespace in the company name", () => {
    expect(tierOf(hit("XYZ", "  Appian   Corp "), "APP")).toBe("name-prefix");
  });
});

describe("rankSuggestions", () => {
  it("orders a query by tier and drops rows it does not match", () => {
    expect(rankSuggestions(HITS, "app").map((entry) => entry.symbol)).toEqual([
      "APP",
      "APPS",
      "AAPL",
    ]);
  });

  it("breaks ties inside a tier by symbol length, then alphabetically", () => {
    expect(
      rankSuggestions([hit("APPPP", "A"), hit("APPX", "B"), hit("APPA", "C")], "app").map(
        (entry) => entry.symbol,
      ),
    ).toEqual(["APPA", "APPX", "APPPP"]);
  });

  it("is stable for the same query", () => {
    expect(rankSuggestions(HITS, "app")).toEqual(rankSuggestions(HITS, "app"));
  });
});
