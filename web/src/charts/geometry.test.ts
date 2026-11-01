import { describe, expect, it } from "vitest";

import { bar, intradayBars, tradingBars } from "./__fixtures__/chart";
import { chartGeometry, nearestIndex, niceTicks } from "./geometry";

/**
 * The geometry's invariants, on the shapes that break naive chart code.
 *
 * Every assertion is about a *relationship* the reader can see: the line stays
 * inside its box, a flat series is drawn rather than divided by zero, a gap is a
 * break, and the price window is the bars rather than the origin.
 */

const SIZE = { width: 900, height: 360 };

describe("chartGeometry — price window", () => {
  it("frames the low and high with padding, and never anchors at zero", () => {
    const bars = tradingBars(22, { level: 100 });
    const geometry = chartGeometry({ ...SIZE, bars });

    expect(geometry.price.min).toBeGreaterThan(90);
    expect(geometry.price.max).toBeLessThan(115);
    expect(geometry.price.min).toBeLessThan(Math.min(...bars.map((entry) => entry.l)));
    expect(geometry.price.max).toBeGreaterThan(Math.max(...bars.map((entry) => entry.h)));
    // "y = 0 never implied": the origin is not in the window at all, so a 1 %
    // day cannot be flattened against a $0 baseline.
    expect(geometry.y(0)).toBeGreaterThan(geometry.priceBox.y + geometry.priceBox.h);
  });

  it("draws a flat series instead of dividing by zero", () => {
    const flat = [100, 101, 102, 103, 104].map((t) =>
      bar(1_767_000_000 + t * 300, 50, 50, 50, 50, 1000),
    );
    const geometry = chartGeometry({ ...SIZE, bars: flat });

    expect(geometry.price.max).toBeGreaterThan(geometry.price.min);
    for (const point of geometry.points) {
      expect(Number.isFinite(point.y)).toBe(true);
      expect(point.y).toBeGreaterThanOrEqual(geometry.priceBox.y);
      expect(point.y).toBeLessThanOrEqual(geometry.priceBox.y + geometry.priceBox.h);
    }
    // The flat line sits in the middle of its padded window, not on an edge.
    expect(geometry.y(50)).toBeCloseTo(geometry.priceBox.y + geometry.priceBox.h / 2, 5);
  });

  it("renders a single bar, at the centre, with a real body", () => {
    const geometry = chartGeometry({
      ...SIZE,
      bars: [bar(1_767_000_000, 99, 101, 98, 100, 0)],
    });

    expect(geometry.points).toHaveLength(1);
    expect(geometry.segments).toHaveLength(1);
    expect(geometry.points[0]?.x).toBeCloseTo(geometry.plot.x + geometry.plot.w / 2, 5);
    expect(geometry.candles[0]?.width).toBeGreaterThanOrEqual(2);
    expect(geometry.candles[0]?.bodyBottom).toBeGreaterThan(geometry.candles[0]?.bodyTop ?? 0);
    expect(geometry.x(0)).toBeCloseTo(geometry.plot.x + geometry.plot.w / 2, 5);
  });

  it("does not produce NaN for an empty series or a zero-sized box", () => {
    const empty = chartGeometry({ ...SIZE, bars: [] });
    expect(empty.points).toHaveLength(0);
    expect(empty.segments).toHaveLength(0);
    expect(Number.isFinite(empty.y(123))).toBe(true);
    expect(empty.xTicks).toHaveLength(0);

    const none = chartGeometry({ width: 0, height: 0, bars: tradingBars(5) });
    expect(none.width).toBe(0);
    expect(Number.isFinite(none.y(100))).toBe(true);
    for (const point of none.points) expect(Number.isFinite(point.x)).toBe(true);
  });
});

describe("chartGeometry — x is time, with a bounded closed-market gutter", () => {
  it("maps unequal intervals proportionally, clamped at three typical steps", () => {
    const bars = [
      bar(0, 100, 100, 100, 100),
      bar(300, 100, 100, 100, 100),
      bar(600, 100, 100, 100, 100),
      bar(3900, 100, 100, 100, 100), // a 10× hole
      bar(4200, 100, 100, 100, 100),
    ];
    const geometry = chartGeometry({ ...SIZE, bars });

    const normal = (geometry.points[1]?.x ?? 0) - (geometry.points[0]?.x ?? 0);
    const hole = (geometry.points[3]?.x ?? 0) - (geometry.points[2]?.x ?? 0);
    expect(normal).toBeGreaterThan(0);
    expect(hole / normal).toBeGreaterThan(2.9);
    expect(hole / normal).toBeLessThan(3.1);
    expect(geometry.time.medianStep).toBe(300);
  });

  it("does not let a long halt compress the trading bars to nothing", () => {
    const before = tradingBars(10, { level: 100 });
    const last = before[before.length - 1] as { t: number };
    const after = tradingBars(10, { start: last.t + 90 * 86_400, level: 100 });
    const bars = [...before, ...after];
    const geometry = chartGeometry({ ...SIZE, bars });

    const normal = (geometry.points[1]?.x ?? 0) - (geometry.points[0]?.x ?? 0);
    const halt = (geometry.points[10]?.x ?? 0) - (geometry.points[9]?.x ?? 0);
    expect(halt).toBeLessThanOrEqual(normal * 3.05);
    // And the bars after the halt still occupy most of the width, which is the
    // reader-visible half of the claim.
    const tail = (geometry.points[19]?.x ?? 0) - (geometry.points[10]?.x ?? 0);
    expect(tail / geometry.plot.w).toBeGreaterThan(0.4);
  });

  it("keeps a weekend in the scale without turning it into a hole", () => {
    // Friday → Monday is three days against a one-day median: three steps, not
    // three sevenths of the chart.
    const bars = [
      bar(0, 100, 101, 99, 100),
      bar(86_400, 100, 101, 99, 100),
      bar(4 * 86_400, 100, 101, 99, 100),
      bar(5 * 86_400, 100, 101, 99, 100),
      bar(6 * 86_400, 100, 101, 99, 100),
    ];
    const geometry = chartGeometry({ ...SIZE, bars });
    const weekday = (geometry.points[1]?.x ?? 0) - (geometry.points[0]?.x ?? 0);
    const weekend = (geometry.points[2]?.x ?? 0) - (geometry.points[1]?.x ?? 0);
    expect(weekend).toBeCloseTo(weekday * 3, 5);
    expect(geometry.points[4]?.x).toBeCloseTo(geometry.plot.x + geometry.plot.w, 5);
  });
});

describe("chartGeometry — a gap is a break, not a slope", () => {
  it("starts a new polyline run when a bar is missing", () => {
    const head = intradayBars(10, 300);
    const tailStart = (head[head.length - 1] as { t: number }).t + 6 * 3600;
    const tail = intradayBars(10, 300).map((entry, index) => ({
      ...entry,
      t: tailStart + index * 300,
    }));
    const geometry = chartGeometry({ ...SIZE, bars: [...head, ...tail] });

    expect(geometry.segments).toHaveLength(2);
    expect(geometry.time.gapBreaks).toBe(1);
    expect(geometry.segments[0]).toHaveLength(10);
    expect(geometry.segments[1]).toHaveLength(10);
    expect(geometry.segments[0]?.[9]?.index).toBe(9);
    expect(geometry.segments[1]?.[0]?.index).toBe(10);
  });

  it("does not break the line across an ordinary weekend", () => {
    const bars = [
      bar(0, 100, 101, 99, 100),
      bar(86_400, 100, 101, 99, 100),
      bar(3 * 86_400, 100, 101, 99, 100),
      bar(4 * 86_400, 100, 101, 99, 100),
    ];
    const geometry = chartGeometry({ ...SIZE, bars });
    expect(geometry.segments).toHaveLength(1);
    expect(geometry.time.gapBreaks).toBe(0);
  });
});

describe("chartGeometry — axes, candles, volume, hover", () => {
  const bars = tradingBars(250, { level: 5900 });
  const geometry = chartGeometry({ ...SIZE, bars });

  it("places x labels only where they fit", () => {
    expect(geometry.xTicks.length).toBeGreaterThanOrEqual(3);
    for (const tick of geometry.xTicks) {
      expect(tick.label.length).toBeGreaterThan(0);
      expect(tick.x).toBeGreaterThanOrEqual(0);
      expect(tick.x).toBeLessThanOrEqual(SIZE.width);
    }
    // No two labels overlap: the filter's whole job.
    for (let index = 1; index < geometry.xTicks.length; index += 1) {
      const left = geometry.xTicks[index - 1] as (typeof geometry.xTicks)[number];
      const right = geometry.xTicks[index] as (typeof geometry.xTicks)[number];
      expect(right.x - left.x).toBeGreaterThan(40);
    }

    const narrow = chartGeometry({ width: 160, height: 120, bars });
    expect(narrow.xTicks.length).toBeLessThanOrEqual(2);
    for (const tick of narrow.xTicks) {
      expect(tick.x).toBeGreaterThanOrEqual(0);
      expect(tick.x).toBeLessThanOrEqual(160);
    }
  });

  it("labels the price axis inside its gutter", () => {
    expect(geometry.yTicks.length).toBeGreaterThanOrEqual(2);
    expect(geometry.yTicks.length).toBeLessThanOrEqual(5);
    for (const tick of geometry.yTicks) {
      expect(tick.label.length).toBeGreaterThan(0);
      expect(tick.y).toBeGreaterThanOrEqual(geometry.priceBox.y - 0.5);
      expect(tick.y).toBeLessThanOrEqual(geometry.priceBox.y + geometry.priceBox.h + 0.5);
    }
  });

  it("gives every candle a body and every volume bar a height", () => {
    expect(geometry.candles).toHaveLength(250);
    for (const candle of geometry.candles) {
      expect(candle.width).toBeGreaterThanOrEqual(1);
      expect(candle.bodyBottom - candle.bodyTop).toBeGreaterThanOrEqual(1);
      expect(candle.wickBottom).toBeGreaterThanOrEqual(candle.bodyBottom);
    }
    const tallest = Math.max(...geometry.volume.bars.map((entry) => entry.height));
    expect(tallest).toBeCloseTo(geometry.volumeBox.h, 5);
    for (const entry of geometry.volume.bars) {
      expect(entry.y + entry.height).toBeCloseTo(geometry.volumeBox.y + geometry.volumeBox.h, 5);
    }
  });

  it("marks the previous close on y", () => {
    const previous = 5905;
    const marked = chartGeometry({ ...SIZE, bars, previousClose: previous });
    expect(marked.previousCloseY).not.toBeNull();
    expect(marked.previousCloseY).toBeCloseTo(marked.y(previous), 5);
    expect(chartGeometry({ ...SIZE, bars }).previousCloseY).toBeNull();
  });

  it("keeps a gap-down previous close inside the frame", () => {
    // Every bar of the day traded below yesterday's close. The dashed line is
    // the most important mark on the chart; it must not fall outside the box.
    const gapDown = tradingBars(12, { level: 90 });
    const previous = 100;
    const geometry = chartGeometry({ ...SIZE, bars: gapDown, previousClose: previous });

    expect(geometry.price.min).toBeLessThanOrEqual(previous);
    const line = geometry.previousCloseY as number;
    expect(line).toBeGreaterThanOrEqual(geometry.priceBox.y);
    expect(line).toBeLessThanOrEqual(geometry.priceBox.y + geometry.priceBox.h);
    expect(geometry.y(0)).toBeGreaterThan(geometry.priceBox.y + geometry.priceBox.h);
  });

  it("finds the hovered bar by x, clamped at both ends", () => {
    expect(nearestIndex(geometry.points, -50)).toBe(0);
    expect(nearestIndex(geometry.points, 10_000)).toBe(249);
    expect(nearestIndex(geometry.points, geometry.points[100]?.x ?? 0)).toBe(100);
    expect(nearestIndex([], 10)).toBe(-1);

    const left = geometry.points[50] as { x: number };
    const right = geometry.points[51] as { x: number };
    expect(nearestIndex(geometry.points, (left.x + right.x) / 2)).toBe(50);
    expect(nearestIndex(geometry.points, (left.x + right.x) / 2 + 0.01)).toBe(51);
  });
});

describe("niceTicks", () => {
  it("produces 1/2/2.5/5 steps within the window", () => {
    const { values, step } = niceTicks(99.2, 110.8, 4);
    expect(step).toBeGreaterThan(0);
    expect(values.length).toBeGreaterThan(0);
    for (const value of values) {
      expect(value).toBeGreaterThanOrEqual(99.2);
      expect(value).toBeLessThanOrEqual(110.8);
    }
    const between = values.map((value) => Math.round((value - 99.2) / step));
    expect(new Set(between).size).toBe(values.length);
  });

  it("returns nothing rather than NaN for an empty window", () => {
    expect(niceTicks(5, 5, 4).values).toHaveLength(0);
    expect(niceTicks(Number.NaN, 1, 4).values).toHaveLength(0);
  });
});
