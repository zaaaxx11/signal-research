import { describe, expect, it } from "vitest";

import { barData, toBars, utcLabel } from "./bars";

/**
 * The adapter's promises, one test each: ascending order, one bar per
 * timestamp, gaps left alone, impossible rows dropped, bodies contained by
 * their wicks, and an ordinal series told apart from a dated one.
 */

const DAY = 86_400;
const T0 = 1_767_364_200; // 2026-01-02T14:30:00Z

function wire(t: number, o: number, h: number, l: number, c: number, v = 1_000) {
  return { t, o, h, l, c, v };
}

describe("toBars — the chart endpoint's document", () => {
  it("reads bars as they arrive", () => {
    const bars = toBars({
      bars: [wire(T0, 10, 12, 9, 11), wire(T0 + 300, 11, 13, 10.5, 12.5)],
    });

    expect(bars).toHaveLength(2);
    expect(bars.map((bar) => bar.time)).toEqual([T0, T0 + 300]);
    expect(bars[1]).toMatchObject({ open: 11, high: 13, low: 10.5, close: 12.5, volume: 1_000 });
    expect(bars[0]?.timeLabel).toBe(utcLabel(T0));
    expect(bars[0]?.timeLabel).toMatch(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/);
  });

  it("sorts an out-of-order payload ascending", () => {
    const bars = toBars({
      bars: [wire(T0 + 600, 12, 13, 11, 12.5), wire(T0, 10, 11, 9, 10.5), wire(T0 + 300, 11, 12, 10, 11.5)],
    });

    expect(bars.map((bar) => bar.time)).toEqual([T0, T0 + 300, T0 + 600]);
    expect(bars.map((bar) => bar.close)).toEqual([10.5, 11.5, 12.5]);
  });

  it("keeps the last write for a repeated timestamp", () => {
    const bars = toBars({
      bars: [wire(T0, 10, 11, 9, 10.5), wire(T0 + 300, 11, 12, 10, 11.5), wire(T0, 10, 11.5, 9.5, 11)],
    });

    expect(bars).toHaveLength(2);
    expect(bars[0]).toMatchObject({ time: T0, close: 11, high: 11.5 });
  });

  it("leaves a gap where a session is missing", () => {
    const bars = toBars({ bars: [wire(T0, 10, 11, 9, 10.5), wire(T0 + 5 * DAY, 20, 21, 19, 20.5)] });

    // Two bars, not three: the hole is not filled and the times are not
    // renumbered to hide it.
    expect(bars).toHaveLength(2);
    expect(bars.map((bar) => bar.time)).toEqual([T0, T0 + 5 * DAY]);
  });

  it("drops a row with no time or no body", () => {
    const bars = toBars({
      bars: [
        wire(T0, 10, 11, 9, 10.5),
        wire(Number.NaN, 10, 11, 9, 10.5),
        wire(T0 + 300, 0, 11, 9, 10.5), // a zero open is a missing field
        wire(T0 + 600, 10, 11, 9, Number.POSITIVE_INFINITY),
      ],
    });

    expect(bars).toHaveLength(1);
    expect(bars[0]?.time).toBe(T0);
  });

  it("widens a wick that does not contain its body", () => {
    const bars = toBars({ bars: [wire(T0, 10, 10.2, 10.1, 12)] });

    expect(bars[0]).toMatchObject({ open: 10, close: 12, high: 12, low: 10 });
  });

  it("reads a negative volume as zero, never as a downward bar", () => {
    expect(toBars({ bars: [wire(T0, 10, 11, 9, 10.5, -5)] })[0]?.volume).toBe(0);
  });

  it("keeps a single point drawable", () => {
    const bars = toBars({ bars: [wire(T0, 10, 11, 9, 10.5)] });

    expect(bars).toHaveLength(1);
    expect(bars[0]?.close).toBe(10.5);
  });

  it("is an empty series for an empty document, not a throw", () => {
    expect(toBars({ bars: [] })).toEqual([]);
    expect(toBars({})).toEqual([]);
  });
});

describe("toBars — the columnar payload", () => {
  it("zips timestamp and close, with the optional columns when present", () => {
    const bars = toBars({
      timestamp: [T0, T0 + 300],
      close: [10.5, 11.5],
      open: [10, 11],
      high: [11, 12],
      low: [9.5, 10.5],
      volume: [100, 200],
    });

    expect(bars.map((bar) => bar.close)).toEqual([10.5, 11.5]);
    expect(bars[1]).toMatchObject({ open: 11, high: 12, low: 10.5, volume: 200 });
  });

  it("uses the close for a column the payload did not send", () => {
    const bars = toBars({ timestamp: [T0], close: [10.5] });

    expect(bars[0]).toMatchObject({ open: 10.5, high: 10.5, low: 10.5, close: 10.5, volume: 0 });
  });

  it("stops where the shorter column stops", () => {
    const bars = toBars({ timestamp: [T0, T0 + 300, T0 + 600], close: [10.5, 11.5] });

    expect(bars).toHaveLength(2);
  });

  it("drops a timestamp whose close is not a price", () => {
    const bars = toBars({ timestamp: [T0, T0 + 300, T0 + 600], close: [10.5, 0, 12] });

    expect(bars.map((bar) => bar.time)).toEqual([T0, T0 + 600]);
  });

  it("sorts and de-duplicates a columnar payload too", () => {
    const bars = toBars({ timestamp: [T0 + 300, T0, T0 + 300], close: [11, 10, 11.5] });

    expect(bars.map((bar) => bar.time)).toEqual([T0, T0 + 300]);
    expect(bars[1]?.close).toBe(11.5);
  });
});

describe("toBars — Home's closes", () => {
  it("turns prices into ordinal bars, oldest first", () => {
    const bars = toBars({ closes: [10, 10.5, 11] });

    expect(bars.map((bar) => bar.time)).toEqual([0, 1, 2]);
    expect(bars.map((bar) => bar.timeLabel)).toEqual(["Bar 1", "Bar 2", "Bar 3"]);
    expect(bars[2]).toMatchObject({ open: 11, high: 11, low: 11, close: 11, volume: 0 });
  });

  it("drops an unreadable close without sliding the rest left", () => {
    const bars = toBars({ closes: [10, Number.NaN, 11] });

    // The hole keeps its position: bar 3 is still bar 3.
    expect(bars.map((bar) => bar.timeLabel)).toEqual(["Bar 1", "Bar 3"]);
    expect(bars.map((bar) => bar.time)).toEqual([0, 2]);
  });

  it("is empty when every close is missing", () => {
    expect(toBars({ closes: [] })).toEqual([]);
    expect(toBars({ closes: [0, Number.NaN] })).toEqual([]);
  });
});

describe("barData", () => {
  it("crosses into the library without the table's label", () => {
    const bars = toBars({ bars: [wire(T0, 10, 11, 9, 10.5, 42)] });
    const data = barData(bars);

    expect(data).toEqual([{ time: T0, open: 10, high: 11, low: 9, close: 10.5 }]);
    expect("timeLabel" in (data[0] as object)).toBe(false);
    expect("volume" in (data[0] as object)).toBe(false);
  });
});
