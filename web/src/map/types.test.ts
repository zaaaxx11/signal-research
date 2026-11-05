import { describe, expect, it } from "vitest";

import { MapMalformed, parseMap, sectorCap } from "./types";

/**
 * The payload reader. Every case here is a wire shape the endpoint can
 * legitimately produce — an absent count, a null change, a duplicate symbol —
 * and the rule is always the same: keep what is readable, never invent the
 * rest, and refuse a document that is not a map at all.
 */

function wire(sectors: unknown[]): unknown {
  return {
    as_of: "2026-10-31T20:58:12+00:00",
    region: "us",
    metric: "change",
    universe_total: 100,
    mapped: 5,
    sectors,
  };
}

describe("parseMap", () => {
  it("expands the wire's abbreviations once", () => {
    const payload = parseMap(
      wire([
        {
          id: "technology",
          label: "Technology",
          count: 2,
          value: 0.5,
          adv: 1,
          dec: 1,
          symbols: [
            { s: "aapl", c: 5034.7, v: 2.1 },
            { s: "MSFT", c: 3100.2, v: -0.8 },
          ],
        },
      ]),
    );

    expect(payload.region).toBe("us");
    expect(payload.metric).toBe("change");
    expect(payload.sectors).toHaveLength(1);
    const sector = payload.sectors[0];
    expect(sector?.stocks[0]).toEqual({ symbol: "AAPL", cap: 5034.7, change: 2.1, name: null, price: null });
    expect(sectorCap(sector!)).toBeCloseTo(8134.9, 5);
  });

  it("recomputes what the wire omitted instead of printing a lie", () => {
    const payload = parseMap(
      wire([
        {
          id: "energy",
          symbols: [
            { s: "XOM", c: 452.8, v: -2.6 },
            { s: "CVX", c: 208.3, v: 0 },
            { s: "COP", c: 120, v: 1.6 },
          ],
        },
      ]),
    );
    const sector = payload.sectors[0]!;
    expect(sector.label).toBe("Energy");
    expect(sector.count).toBe(3);
    expect(sector.adv).toBe(1);
    expect(sector.dec).toBe(1);
    expect(sector.value).toBeCloseTo((-2.6 + 0 + 1.6) / 3, 6);
  });

  it("drops rows with no area or no symbol, and keeps the rest", () => {
    const payload = parseMap(
      wire([
        {
          id: "mixed",
          label: "Mixed",
          count: 5,
          value: 0,
          adv: 0,
          dec: 0,
          symbols: [
            { s: "OK", c: 10, v: 1 },
            { s: "", c: 10, v: 1 },
            { s: "NOCAP", v: 1 },
            { s: "NEG", c: -4, v: 1 },
            { s: "NULLV", c: 4, v: null },
            "not-a-row",
          ],
        },
      ]),
    );
    const stocks = payload.sectors[0]!.stocks;
    expect(stocks.map((stock) => stock.symbol)).toEqual(["OK", "NULLV"]);
    // An absent change is flat, never a fabricated move.
    expect(stocks[1]?.change).toBe(0);
    // The provider said 5; only 2 were readable, and count is not inflated.
    expect(payload.sectors[0]?.count).toBe(5);
  });

  it("keeps one tile per symbol inside a sector", () => {
    const payload = parseMap(
      wire([
        {
          id: "dupes",
          label: "Dupes",
          count: 2,
          value: 0,
          adv: 1,
          dec: 0,
          symbols: [
            { s: "AAPL", c: 10, v: 1 },
            { s: "AAPL", c: 99, v: -3 },
          ],
        },
      ]),
    );
    const stocks = payload.sectors[0]!.stocks;
    expect(stocks).toHaveLength(1);
    expect(stocks[0]?.cap).toBe(99);
    // The first row's change wins, so the colour is the one that arrived first.
    expect(stocks[0]?.change).toBe(1);
  });

  it("drops a sector with no readable rows and a sector with no id", () => {
    const payload = parseMap(
      wire([
        { id: "empty", label: "Empty", count: 0, value: null, adv: 0, dec: 0, symbols: [] },
        { label: "No id", count: 1, value: 0, adv: 0, dec: 0, symbols: [{ s: "A", c: 1, v: 0 }] },
        { id: "ok", label: "Ok", count: 1, value: 0, adv: 0, dec: 0, symbols: [{ s: "B", c: 1, v: 0 }] },
      ]),
    );
    expect(payload.sectors.map((sector) => sector.id)).toEqual(["ok"]);
  });

  it("refuses a document that is not a map", () => {
    expect(() => parseMap(null)).toThrow(MapMalformed);
    expect(() => parseMap({})).toThrow(MapMalformed);
    expect(() => parseMap({ sectors: "nope" })).toThrow(MapMalformed);
  });

  it("falls back to the rows when the totals are absent or impossible", () => {
    const payload = parseMap({
      sectors: [{ id: "a", label: "A", symbols: [{ s: "A", c: 1, v: 0 }] }],
    });
    expect(payload.universeTotal).toBe(1);
    expect(payload.mapped).toBe(1);
    expect(payload.asOf).toBe("");
  });
});
