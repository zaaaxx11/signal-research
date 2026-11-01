import { describe, expect, it } from "vitest";

import { bar, wireFor } from "./__fixtures__/chart";
import { ChartMalformed, parseChart, seriesChange, type ChartRange } from "./types";

/** The reader: never a bar from a number nobody sent, never a zero change. */

const FALLBACK = { symbol: "AAPL", range: "1d" as ChartRange };

describe("parseChart", () => {
  it("reads the wire's snake_case and normalises the symbol", () => {
    const series = parseChart(wireFor("aapl", "1d"), FALLBACK);
    expect(series.symbol).toBe("AAPL");
    expect(series.range).toBe("1d");
    expect(series.timezone).toBe("UTC");
    expect(series.previousClose).toBeTypeOf("number");
    expect(series.bars.length).toBeGreaterThan(0);
    expect(series.truncated).toBe(false);
  });

  it("drops a bar with no timestamp or no price, and keeps a valid one", () => {
    const good = bar(100, 10, 11, 9, 10.5, 100);
    const volumeFree = { t: 110, o: 10, h: 11, l: 9, c: 10.5 };
    const series = parseChart(
      {
        ...wireFor("AAPL", "1d"),
        bars: [
          { t: 90, o: 0, h: 1, l: 1, c: 1, v: 1 },
          { t: 95, o: 1, h: 1, l: 1 },
          { t: 100, o: 10, h: 11, l: 9, c: 10.5, v: 100 },
          volumeFree,
          "nope",
        ],
      },
      FALLBACK,
    );
    expect(series.bars).toEqual([good, { ...volumeFree, v: 0 }]);
  });

  it("sorts by time and keeps the last write for a timestamp", () => {
    const series = parseChart(
      {
        ...wireFor("AAPL", "1d"),
        bars: [
          { t: 300, o: 3, h: 3, l: 3, c: 3, v: 3 },
          { t: 100, o: 1, h: 1, l: 1, c: 1, v: 1 },
          { t: 200, o: 2, h: 2, l: 2, c: 2, v: 2 },
          { t: 200, o: 2.5, h: 2.5, l: 2.5, c: 2.5, v: 4 },
        ],
      },
      FALLBACK,
    );
    expect(series.bars.map((entry) => entry.t)).toEqual([100, 200, 300]);
    expect(series.bars[1]?.c).toBe(2.5);
  });

  it("keeps an absent previous close absent, not zero", () => {
    const series = parseChart({ ...wireFor("AAPL", "1d"), previous_close: null }, FALLBACK);
    expect(series.previousClose).toBeNull();
  });

  it("accepts an empty series but rejects a document with no bars array", () => {
    const empty = parseChart({ ...wireFor("AAPL", "1d"), bars: [] }, FALLBACK);
    expect(empty.bars).toHaveLength(0);

    expect(() => parseChart({ bars: "nope" }, FALLBACK)).toThrow(ChartMalformed);
    expect(() => parseChart(null, FALLBACK)).toThrow(ChartMalformed);
  });

  it("falls back to the requested range when the wire states an unknown one", () => {
    const series = parseChart({ ...wireFor("AAPL", "1d"), range: "3y" }, FALLBACK);
    expect(series.range).toBe("1d");
  });
});

describe("seriesChange", () => {
  it("uses the stated previous close", () => {
    const wire = wireFor("AAPL", "1d");
    const series = parseChart(wire, FALLBACK);
    const change = seriesChange(series);
    const bars = wire.bars as Array<{ c: number }>;
    const last = bars[bars.length - 1] as { c: number };
    const expected = ((last.c - (wire.previous_close as number)) / (wire.previous_close as number)) * 100;

    expect(change.last).toBe(last.c);
    expect(change.previous).toBe(wire.previous_close);
    expect(change.percent).toBeCloseTo(expected, 10);
  });

  it("falls back to the first close only for display", () => {
    const series = { ...parseChart(wireFor("AAPL", "1d"), FALLBACK), previousClose: null };
    const change = seriesChange(series);
    expect(change.previous).toBe(series.bars[0]?.c);
  });

  it("reports no change for an empty series", () => {
    const series = { ...parseChart(wireFor("AAPL", "1d"), FALLBACK), bars: [] };
    expect(seriesChange(series)).toEqual({ last: null, previous: null, change: null, percent: null });
  });

  it("refuses a percentage when the prior close is zero", () => {
    const series = { ...parseChart(wireFor("AAPL", "1d"), FALLBACK), previousClose: 0 };
    const change = seriesChange(series);
    expect(change.percent).toBeNull();
  });
});
