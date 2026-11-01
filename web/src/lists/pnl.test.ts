/**
 * The P&L arithmetic, on a fixture small enough to check by hand.
 *
 * | holding | qty | avg cost | last  | market value | cost basis | P&L     | %      |
 * |---------|-----|----------|-------|--------------|------------|---------|--------|
 * | AAPL    |  10 |   100.00 | 110.00|      1100.00 |    1000.00 | +100.00 | +10.00 |
 * | MSFT    | 2.5 |   400.10 | 390.04|       975.10 |    1000.25 |  -25.15 |  -2.51 |
 * | total   |     |          |       |      2075.10 |    2000.25 |  +74.85 |  +3.74 |
 *
 * The fourth row is the one that matters: a holding with no quote contributes
 * neither a market value nor a cost basis, so the total is asked over two of
 * three rows and says so. Treating a missing price as zero would turn a +74.85
 * portfolio into a -125.15 one — a number a reader would act on.
 */

import { describe, expect, it } from "vitest";

import { priceHolding, priceHoldings, toCents, totalOf } from "./pnl";
import type { QuoteRow } from "./quotes";

function quote(symbol: string, price: number, changePct = 0): QuoteRow {
  return {
    symbol,
    name: null,
    price,
    changePct,
    currency: "USD",
    assetClass: "stock",
  };
}

const REPORT = new Map<string, QuoteRow>([
  ["AAPL", quote("AAPL", 110)],
  ["MSFT", quote("MSFT", 390.04)],
]);

const HOLDINGS = [
  { symbol: "AAPL", quantity: 10, cost: 100 },
  { symbol: "MSFT", quantity: 2.5, cost: 400.1 },
];

describe("priceHolding", () => {
  it("computes the gain on the fixture to the cent", () => {
    const row = priceHolding(HOLDINGS[0]!, quote("AAPL", 110));
    expect(row.marketValue).toBe(1100);
    expect(row.costBasis).toBe(1000);
    expect(row.pnl).toBe(100);
    expect(row.pnlPct).toBeCloseTo(10, 9);
  });

  it("computes the loss on the fixture to the cent", () => {
    const row = priceHolding(HOLDINGS[1]!, quote("MSFT", 390.04));
    expect(row.marketValue).toBe(975.1);
    expect(row.costBasis).toBe(1000.25);
    expect(row.pnl).toBe(-25.15);
    expect(row.pnlPct).toBeCloseTo(-2.5144, 3);
  });

  it("rounds each side to the cent before subtracting", () => {
    // 3 × 33.33 = 99.99000000000001 in binary floating point; the cents are 99.99.
    const row = priceHolding({ symbol: "AMD", quantity: 3, cost: 33 }, quote("AMD", 33.33));
    expect(row.marketValue).toBe(99.99);
    expect(row.costBasis).toBe(99);
    expect(row.pnl).toBe(0.99);
  });

  it("has no percentage when there is no cost to divide by", () => {
    const row = priceHolding({ symbol: "GIFT", quantity: 2, cost: 0 }, quote("GIFT", 10));
    expect(row.marketValue).toBe(20);
    expect(row.costBasis).toBe(0);
    expect(row.pnl).toBe(20);
    expect(row.pnlPct).toBeNull();
  });

  it("is unpriced, not zero, when there is no quote", () => {
    const row = priceHolding({ symbol: "TSLA", quantity: 1, cost: 200 }, null);
    expect(row.marketValue).toBeNull();
    expect(row.pnl).toBeNull();
    expect(row.pnlPct).toBeNull();
    expect(row.costBasis).toBe(200);
  });
});

describe("priceHoldings", () => {
  it("joins holdings to quotes by symbol and keeps a missing one visible", () => {
    const rows = priceHoldings(
      [...HOLDINGS, { symbol: "TSLA", quantity: 1, cost: 200 }],
      REPORT,
    );
    expect(rows.map((row) => row.holding.symbol)).toEqual(["AAPL", "MSFT", "TSLA"]);
    expect(rows.map((row) => row.quote !== null)).toEqual([true, true, false]);
  });
});

describe("totalOf", () => {
  it("sums the priced rows and states how many it covers", () => {
    const rows = priceHoldings(HOLDINGS, REPORT);
    const total = totalOf(rows);

    expect(total.marketValue).toBe(2075.1);
    expect(total.costBasis).toBe(2000.25);
    expect(total.pnl).toBe(74.85);
    expect(total.pnlPct).toBeCloseTo(3.742, 3);
    expect(total.covered).toBe(2);
    expect(total.total).toBe(2);
  });

  it("excludes an unpriced holding from both sides, and counts it", () => {
    const rows = priceHoldings([...HOLDINGS, { symbol: "TSLA", quantity: 1, cost: 200 }], REPORT);
    const total = totalOf(rows);

    // The unpriced 200 is in neither the market value nor the cost basis: had
    // it been counted as a zero market value the P&L would read -125.15.
    expect(total.marketValue).toBe(2075.1);
    expect(total.costBasis).toBe(2000.25);
    expect(total.pnl).toBe(74.85);
    expect(total.covered).toBe(2);
    expect(total.total).toBe(3);
  });

  it("has no total at all when nothing is priced", () => {
    const total = totalOf(priceHoldings([{ symbol: "TSLA", quantity: 1, cost: 200 }], REPORT));
    expect(total.covered).toBe(0);
    expect(total.total).toBe(1);
    expect(total.marketValue).toBe(0);
    expect(total.pnl).toBe(0);
    expect(total.pnlPct).toBeNull();
  });

  it("has no percentage when the covered cost basis is zero", () => {
    const total = totalOf(priceHoldings([{ symbol: "GIFT", quantity: 2, cost: 0 }], REPORT));
    expect(total.pnl).toBe(0);
    expect(total.pnlPct).toBeNull();
  });
});

describe("toCents", () => {
  it("is idempotent and keeps a true zero a zero", () => {
    expect(toCents(1.005)).toBe(1.01);
    expect(toCents(1.004)).toBe(1);
    expect(toCents(toCents(74.848)).valueOf()).toBe(74.85);
    expect(Object.is(toCents(0), 0)).toBe(true);
  });
});
