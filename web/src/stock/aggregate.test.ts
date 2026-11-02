/**
 * D/W/M are bucketings of one daily series, and a bucket must not invent a
 * price: open from the first session, close from the last, extremes from the
 * whole week, volume summed.
 */

import { describe, expect, it } from "vitest";

import type { ChartBar } from "@/charts/bars";
import { aggregateBars } from "./aggregate";

function bar(time: number, open: number, high: number, low: number, close: number, volume: number): ChartBar {
  return {
    time: time as ChartBar["time"],
    open,
    high,
    low,
    close,
    volume,
    timeLabel: new Date(time * 1000).toISOString(),
  };
}

// 2026-10-26 is a Monday; 2026-11-02 the next one.
const WEEK_ONE = [
  bar(1793001600, 100, 104, 99, 103, 10),
  bar(1793088000, 103, 108, 102, 107, 20),
];
const WEEK_TWO = [
  bar(1793606400, 107, 109, 105, 106, 30),
  bar(1793692800, 106, 110, 101, 102, 40),
];

describe("aggregateBars", () => {
  it("returns the daily series unchanged for D", () => {
    const bars = [...WEEK_ONE, ...WEEK_TWO];
    expect(aggregateBars(bars, "D")).toEqual(bars);
  });

  it("folds a week into one bar with first open, last close and summed volume", () => {
    const weekly = aggregateBars([...WEEK_ONE, ...WEEK_TWO], "W");
    expect(weekly).toHaveLength(2);
    const first = weekly[0];
    expect(first?.open).toBe(100);
    expect(first?.close).toBe(107);
    expect(first?.high).toBe(108);
    expect(first?.low).toBe(99);
    expect(first?.volume).toBe(30);
    expect(first?.timeLabel).toBe("Week of 2026-10-26");
  });

  it("folds a month into one bar and keeps the calendar label", () => {
    const monthly = aggregateBars([...WEEK_ONE, ...WEEK_TWO], "M");
    expect(monthly).toHaveLength(2);
    expect(monthly[0]?.timeLabel).toBe("2026-10");
    expect(monthly[1]?.timeLabel).toBe("2026-11");
    expect(monthly[1]?.open).toBe(107);
    expect(monthly[1]?.close).toBe(102);
  });

  it("leaves an ordinal series alone: it has no calendar to bucket by", () => {
    const ordinal = WEEK_ONE.map((entry, index) => ({ ...entry, timeLabel: `Bar ${index + 1}` }));
    expect(aggregateBars(ordinal, "W")).toEqual(ordinal);
  });
});
