import { describe, expect, it } from "vitest";

import {
  INDICATOR_LABELS,
  SMA_BY_ID,
  ema,
  isIndicatorId,
  orderIndicators,
  rsi,
  sma,
  type Slot,
} from "./indicators";

/**
 * The arithmetic, pinned against numbers a reader can check by hand.
 *
 * An indicator is only worth drawing if its warm-up, its smoothing and its
 * degenerate cases are decided rather than discovered, so every convention the
 * module's header states has a test here: alignment (same length, `null` for
 * the warm-up), a hand-computed SMA over `1..20`, the canonical published RSI
 * series, gaps that break a window instead of being skipped, and the flat-run
 * RSI of 50.
 */

const closes = (count: number): number[] => Array.from({ length: count }, (_, index) => index + 1);

/** Wilder's worked example, as printed by StockCharts' RSI article. */
const WILDER_CLOSES = [
  44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.1, 45.42, 45.84, 46.08, 45.89, 46.03, 45.61, 46.28,
  46.28, 46.0, 46.03, 46.41, 46.22, 45.64, 46.21, 46.25, 45.71, 46.45, 45.78, 45.35, 44.03, 44.18,
  44.22, 44.57, 43.42, 42.66, 43.13,
];

/** The same table's RSI column, rounded to two decimals, starting at bar 14. */
const WILDER_RSI = [
  70.53, 66.32, 66.55, 69.41, 66.36, 57.97, 62.93, 63.26, 56.06, 62.38, 54.71, 50.42, 39.99, 41.46,
  41.87, 45.46, 37.3, 33.08, 37.77,
];

describe("sma", () => {
  it("averages the trailing window and is null for the warm-up", () => {
    const values = closes(20);
    const result = sma(values, 3);

    expect(result).toEqual([
      null, null, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19,
    ]);
    // Spot-check by hand: bars 3, 4 and 5 are 3, 4 and 5.
    expect(result[4]).toBe(4);
  });

  it("is aligned with its input and null until the period is satisfied", () => {
    const result = sma(closes(20), 20);
    expect(result).toHaveLength(20);
    expect(result.slice(0, 19)).toEqual(new Array(19).fill(null));
    expect(result[19]).toBe(10.5); // (1 + … + 20) / 20
  });

  it("returns all nulls for a series shorter than the period", () => {
    expect(sma([1, 2, 3], 20)).toEqual([null, null, null]);
    expect(sma([], 20)).toEqual([]);
  });

  it("breaks the window at a gap instead of averaging across it", () => {
    // 4 and 5 follow a hole: no three consecutive closes end before index 5.
    expect(sma([1, 2, null, 4, 5, 6], 3)).toEqual([null, null, null, null, null, 5]);
    // A gap that lands inside a window nulls that window only.
    expect(sma([1, 2, 3, null, 5, 6, 7], 3)).toEqual([null, null, 2, null, null, null, 6]);
  });

  it("treats a non-finite number as a hole, not as zero", () => {
    const values: Slot[] = [1, 2, Number.NaN, 4, 5, 6];
    expect(sma(values, 3)).toEqual([null, null, null, null, null, 5]);
    expect(sma([1, 2, Number.POSITIVE_INFINITY, 4, 5, 6], 3)).toEqual([
      null,
      null,
      null,
      null,
      null,
      5,
    ]);
  });

  it("refuses a period that is not a positive integer", () => {
    expect(() => sma([1, 2, 3], 0)).toThrow(RangeError);
    expect(() => sma([1, 2, 3], 2.5)).toThrow(RangeError);
    expect(() => sma([1, 2, 3], -3)).toThrow(RangeError);
  });
});

describe("ema", () => {
  it("seeds with the first window's simple average, then smooths", () => {
    // period 3 on 1..10: seed = (1+2+3)/3 = 2 at index 2, alpha = 0.5, so every
    // later value of a linear ramp lands on its own index.
    expect(ema(closes(10), 3)).toEqual([null, null, 2, 3, 4, 5, 6, 7, 8, 9]);
  });

  it("weights the newest close more heavily than the oldest", () => {
    // alpha = 2 / (4 + 1) = 0.4: 10 → 20 gives 10 + 0.4 * (20 - 10) = 14.
    const values = [10, 10, 10, 10, 20];
    expect(ema(values, 4)).toEqual([null, null, null, 10, 14]);
  });

  it("restarts after a gap", () => {
    expect(ema([1, 2, 3, null, 4, 5, 6], 3)).toEqual([null, null, 2, null, null, null, 5]);
  });

  it("returns all nulls for an empty or too-short series", () => {
    expect(ema([], 5)).toEqual([]);
    expect(ema([1, 2], 5)).toEqual([null, null]);
  });
});

describe("rsi", () => {
  it("reproduces Wilder's published 14-period example", () => {
    const result = rsi(WILDER_CLOSES, 14);
    const defined = result.slice(14);

    expect(defined).toHaveLength(WILDER_RSI.length);
    defined.forEach((value, index) => {
      expect(value).not.toBeNull();
      // The published table is rounded to two decimals and seeded from a
      // slightly longer history; a tenth is the honest tolerance.
      expect(Math.abs((value as number) - (WILDER_RSI[index] as number))).toBeLessThan(0.1);
    });
  });

  it("starts at index period — not before", () => {
    const result = rsi(WILDER_CLOSES, 14);
    expect(result).toHaveLength(WILDER_CLOSES.length);
    expect(result.slice(0, 14)).toEqual(new Array(14).fill(null));
    expect(result[14]).toBeCloseTo(70.46, 2);
  });

  it("uses Wilder's smoothing, not a plain average of gains", () => {
    // Fourteen rises seed a gain average of 1 and a loss average of 0, so bar
    // 14 is 100. The fall to 4 then smooths both: the gain average keeps
    // 13/14 of its weight, the loss average gains 11/14 → rs = 13/11.
    const values = [...closes(15), 4];
    const result = rsi(values, 14);
    expect(result[14]).toBe(100);
    expect(result[15]).toBeCloseTo(100 - 100 / (1 + 13 / 11), 6);
  });

  it("is 50 on a flat series — the pinned convention", () => {
    const result = rsi(new Array(20).fill(5), 14);
    expect(result.slice(0, 14)).toEqual(new Array(14).fill(null));
    expect(result.slice(14)).toEqual(new Array(6).fill(50));
    for (const value of result) {
      if (value !== null) expect(Number.isFinite(value)).toBe(true);
    }
  });

  it("clamps every value to 0..100 and never emits NaN", () => {
    const noisy = WILDER_CLOSES.map((value, index) =>
      index % 3 === 0 ? value * 3 : index % 5 === 0 ? value / 7 : value,
    );
    for (const value of rsi(noisy, 14)) {
      if (value === null) continue;
      expect(value).toBeGreaterThanOrEqual(0);
      expect(value).toBeLessThanOrEqual(100);
      expect(Number.isNaN(value)).toBe(false);
    }
    // A series that only falls is 0, not negative.
    expect(rsi(closes(20).map((value) => 100 - value), 14).at(-1)).toBe(0);
  });

  it("restarts its averages after a gap", () => {
    const values = [...closes(15), null, ...closes(15)];
    const result = rsi(values, 14);

    // Before the hole: the first value at index 14.
    expect(result[14]).not.toBeNull();
    // The hole and the 14 bars after it have no complete window.
    expect(result.slice(15, 30)).toEqual(new Array(15).fill(null));
    // Index 30 is the 15th close after the hole: 14 changes have accumulated.
    expect(result[30]).not.toBeNull();
  });

  it("returns all nulls for an empty or too-short series", () => {
    expect(rsi([], 14)).toEqual([]);
    expect(rsi([1, 2, 3], 14)).toEqual([null, null, null]);
    expect(rsi(closes(14), 14).every((value) => value === null)).toBe(true);
  });

  it("refuses a period that is not a positive integer", () => {
    expect(() => rsi(closes(20), 0)).toThrow(RangeError);
  });
});

describe("catalogue", () => {
  it("orders a selection canonically and drops duplicates and unknowns", () => {
    expect(orderIndicators(undefined)).toEqual([]);
    expect(orderIndicators([])).toEqual([]);
    expect(orderIndicators(["rsi", "sma200", "sma20", "sma200"])).toEqual([
      "sma20",
      "sma200",
      "rsi",
    ]);
  });

  it("names each id with its period in the label the chip shows", () => {
    expect(INDICATOR_LABELS).toEqual({
      sma20: "SMA 20",
      sma50: "SMA 50",
      sma200: "SMA 200",
      rsi: "RSI 14",
    });
    expect(SMA_BY_ID.sma50).toBe(50);
    expect(isIndicatorId("sma200")).toBe(true);
    expect(isIndicatorId("sma100")).toBe(false);
  });
});
