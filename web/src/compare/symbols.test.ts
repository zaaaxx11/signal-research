import { describe, expect, it } from "vitest";

import {
  MAX_COMPARE,
  addSymbol,
  compareHref,
  normaliseSymbol,
  parseSymbols,
  removeSymbol,
  symbolsFromSearch,
} from "./symbols";

/**
 * The comparison URL is product surface: two people must be able to share one
 * address and see the same companies. So the parsing rules are stated here,
 * away from the page, and the page cannot quietly disagree with them.
 *
 * The alphabet is the screener's own (`^[A-Za-z][A-Za-z0-9.\-]{0,9}$`), because
 * a ticker the filter accepts and the comparison refuses would be two answers
 * to "is this a symbol?".
 */

describe("normaliseSymbol", () => {
  it("upper-cases a valid ticker", () => {
    expect(normaliseSymbol("aapl")).toBe("AAPL");
    expect(normaliseSymbol(" brk.b ")).toBe("BRK.B");
    expect(normaliseSymbol("RDS-A")).toBe("RDS-A");
  });

  it("refuses a token the screener's ticker field would refuse", () => {
    expect(normaliseSymbol("")).toBeNull();
    expect(normaliseSymbol("1AAPL")).toBeNull();
    expect(normaliseSymbol("TOOLONGSYMBOL")).toBeNull();
    expect(normaliseSymbol("BRK B")).toBeNull();
    expect(normaliseSymbol("AAPL;DROP")).toBeNull();
    expect(normaliseSymbol("<script>")).toBeNull();
  });
});

describe("parseSymbols", () => {
  it("reads a comma list, upper-cases it and keeps the order", () => {
    expect(parseSymbols("msft,AAPL,nvda")).toEqual(["MSFT", "AAPL", "NVDA"]);
  });

  it("dedupes case-insensitively", () => {
    expect(parseSymbols("AAPL,aapl,Aapl,MSFT")).toEqual(["AAPL", "MSFT"]);
  });

  it("drops an invalid token instead of failing the whole list", () => {
    expect(parseSymbols("AAPL,1BAD,MSFT,")).toEqual(["AAPL", "MSFT"]);
  });

  it("caps the list at ten companies", () => {
    const twelve = Array.from({ length: 12 }, (_, index) => `S${index}`);
    expect(MAX_COMPARE).toBe(10);
    expect(parseSymbols(twelve.join(","))).toHaveLength(10);
  });

  it("reads an empty or absent parameter as no companies", () => {
    expect(parseSymbols(null)).toEqual([]);
    expect(parseSymbols("")).toEqual([]);
    expect(parseSymbols(",,")).toEqual([]);
  });
});

describe("symbolsFromSearch", () => {
  it("reads the symbols parameter out of a query string", () => {
    expect(symbolsFromSearch("symbols=AAPL,MSFT")).toEqual(["AAPL", "MSFT"]);
    expect(symbolsFromSearch("?symbols=AAPL,MSFT&view=value")).toEqual(["AAPL", "MSFT"]);
  });

  it("ignores other parameters and a missing one", () => {
    expect(symbolsFromSearch("")).toEqual([]);
    expect(symbolsFromSearch("?view=value")).toEqual([]);
  });
});

describe("compareHref", () => {
  it("writes the shareable path", () => {
    expect(compareHref(["AAPL", "MSFT"])).toBe("/compare?symbols=AAPL,MSFT");
  });

  it("normalises what it is given, so a link cannot carry junk", () => {
    expect(compareHref(["aapl", "bad token", "AAPL", "brk.b"])).toBe(
      "/compare?symbols=AAPL,BRK.B",
    );
  });

  it("drops the parameter when there is nothing to compare", () => {
    expect(compareHref([])).toBe("/compare");
  });
});

describe("addSymbol / removeSymbol", () => {
  it("appends a new symbol and refuses a duplicate", () => {
    expect(addSymbol(["AAPL"], "msft")).toEqual(["AAPL", "MSFT"]);
    expect(addSymbol(["AAPL"], "aapl")).toEqual(["AAPL"]);
  });

  it("stops at the ceiling rather than truncating silently", () => {
    const ten = Array.from({ length: MAX_COMPARE }, (_, index) => `S${index}`);
    expect(addSymbol(ten, "NEW")).toEqual(ten);
  });

  it("refuses a token that is not a ticker", () => {
    expect(addSymbol(["AAPL"], "not a ticker")).toEqual(["AAPL"]);
  });

  it("removes one company and leaves the order of the rest alone", () => {
    expect(removeSymbol(["AAPL", "MSFT", "NVDA"], "MSFT")).toEqual(["AAPL", "NVDA"]);
    expect(removeSymbol(["AAPL"], "MSFT")).toEqual(["AAPL"]);
  });
});
