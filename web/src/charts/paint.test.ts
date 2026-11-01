import { describe, expect, it } from "vitest";

import { fakeContext } from "./__fixtures__/canvas";
import { intradayBars, tradingBars } from "./__fixtures__/chart";
import { chartGeometry } from "./geometry";
import { paintChart } from "./paint";
import type { ChartTheme } from "./theme";

/**
 * The painter, on a recording context.
 *
 * jsdom cannot raster, so what is asserted here is the command stream: one
 * frame per `setTransform`, a path per contiguous run, a dash for the previous
 * close, labels for the grid, and a dot on the hovered bar. The browser proof
 * covers the pixels and the clock; these assertions cover the decisions.
 */

const THEME: ChartTheme = {
  surface: "#181215",
  raised: "#1f181c",
  border: "#332a2f",
  fg: "#efe9ec",
  muted: "#b9afb4",
  ink: "#1f0a14",
  up: "#3ddc97",
  down: "#ff5f4d",
  flat: "#a6a2a4",
  focus: "#ff8ec4",
  accent: "#ff8ec4",
  subtle: "#9d9398",
};

const SIZE = { width: 900, height: 360 };

function geometryFor(bars = tradingBars(250, { level: 5900 })) {
  return chartGeometry({ ...SIZE, bars, previousClose: bars[0]?.o ?? null });
}

describe("paintChart — line", () => {
  it("scales once, clears, and draws one run per segment", () => {
    const { ctx, passes } = fakeContext();
    const geometry = geometryFor();
    const stats = paintChart(ctx, geometry, THEME, { dpr: 2, kind: "line" });

    const [frame] = passes();
    expect(frame?.transform).toEqual([2, 0, 0, 2, 0, 0]);
    expect(stats.points).toBe(250);
    expect(stats.segments).toBe(geometry.segments.length);
    expect(frame?.moveTos.length).toBeGreaterThanOrEqual(geometry.segments.length);
    expect(frame?.strokesIssued).toBeGreaterThan(0);
  });

  it("does not bridge a data gap", () => {
    const head = intradayBars(10, 300);
    const tailStart = (head[head.length - 1] as { t: number }).t + 6 * 3600;
    const tail = intradayBars(10, 300).map((entry, index) => ({
      ...entry,
      t: tailStart + index * 300,
    }));
    const geometry = chartGeometry({ ...SIZE, bars: [...head, ...tail] });
    const { ctx, passes } = fakeContext();
    const stats = paintChart(ctx, geometry, THEME, { dpr: 1, kind: "line" });

    expect(stats.segments).toBe(2);
    // Each run opens with one moveTo; the grid contributes more, so the
    // assertion is on the stats rather than on the raw command count.
    const [frame] = passes();
    expect(frame?.lineTos.length).toBeGreaterThan(0);
  });

  it("marks the previous close with a dashed line and a word", () => {
    const { ctx, passes } = fakeContext();
    paintChart(ctx, geometryFor(), THEME, { dpr: 1, kind: "line" });
    const [frame] = passes();

    expect(frame?.dashes.some((pattern) => pattern.length === 2 && pattern[0] === 4)).toBe(true);
    expect(frame?.texts.some((mark) => mark.text === "prev")).toBe(true);
  });

  it("writes axis labels in CSS pixels, so DPR 2 is sharp rather than rescaled", () => {
    const { ctx, passes } = fakeContext();
    const geometry = geometryFor();
    paintChart(ctx, geometry, THEME, { dpr: 2, kind: "line" });
    const [frame] = passes();
    const axisTexts = frame?.texts.filter((mark) => mark.text !== "prev") ?? [];

    expect(axisTexts.length).toBeGreaterThanOrEqual(geometry.xTicks.length + geometry.yTicks.length);
    for (const mark of axisTexts) expect(mark.font).toContain("10px");
  });

  it("draws a single point as a dot, not a blank frame", () => {
    const geometry = chartGeometry({
      ...SIZE,
      bars: [{ t: 1_767_000_000, o: 99, h: 101, l: 98, c: 100, v: 10 }],
    });
    const { ctx, passes } = fakeContext();
    const stats = paintChart(ctx, geometry, THEME, { dpr: 1, kind: "line" });
    const [frame] = passes();

    expect(stats.points).toBe(1);
    expect(frame?.arcs).toBeGreaterThanOrEqual(1);
    expect(frame?.fills).toBeGreaterThanOrEqual(1);
  });
});

describe("paintChart — candles", () => {
  it("draws a body per bar, plus a wick pass for each direction", () => {
    const geometry = geometryFor();
    const { ctx, passes } = fakeContext();
    const stats = paintChart(ctx, geometry, THEME, { dpr: 1, kind: "candle" });
    const [frame] = passes();

    expect(stats.candles).toBe(250);
    // 250 bodies + 250 volume bars + the background.
    expect(frame?.fillRects.length).toBeGreaterThanOrEqual(501);
    expect(frame?.strokesIssued).toBeGreaterThanOrEqual(2);
  });

  it("draws a doji with a one-pixel body", () => {
    const geometry = chartGeometry({
      ...SIZE,
      bars: [{ t: 1_767_000_000, o: 100, h: 101, l: 99, c: 100, v: 10 }],
    });
    const { ctx, passes } = fakeContext();
    paintChart(ctx, geometry, THEME, { dpr: 1, kind: "candle" });
    const [frame] = passes();
    const bodies = frame?.fillRects.slice(1) ?? [];
    // The body is the widest-height fill at the bar's x; every fill has height.
    expect(bodies.length).toBeGreaterThan(0);
    for (const rect of bodies) expect(rect[3]).toBeGreaterThanOrEqual(1);
  });
});

describe("paintChart — crosshair", () => {
  it("adds a vertical and horizontal line plus a dot for the hovered bar", () => {
    const geometry = geometryFor();
    const plain = fakeContext();
    paintChart(plain.ctx, geometry, THEME, { dpr: 1, kind: "line" });
    const base = plain.passes()[0] as { moveTos: number[][]; arcs: number };

    const hovered = fakeContext();
    paintChart(hovered.ctx, geometry, THEME, { dpr: 1, kind: "line", hoverIndex: 120 });
    const withHover = hovered.passes()[0] as { moveTos: number[][]; arcs: number };

    expect(withHover.moveTos.length).toBeGreaterThan(base.moveTos.length);
    expect(withHover.arcs).toBeGreaterThan(base.arcs);
  });

  it("ignores an index outside the series", () => {
    const geometry = geometryFor();
    const { ctx, passes } = fakeContext();
    paintChart(ctx, geometry, THEME, { dpr: 1, kind: "line", hoverIndex: 9999 });
    const [frame] = passes();
    expect(frame?.arcs).toBe(0);
  });
});

describe("paintChart — unresolved palette", () => {
  it("draws the text layer even when jsdom resolves no tokens", () => {
    const geometry = geometryFor();
    const { ctx, passes } = fakeContext();
    const empty: ChartTheme = {
      surface: "",
      raised: "",
      border: "",
      fg: "",
      muted: "",
      ink: "",
      up: "",
      down: "",
      flat: "",
      focus: "",
      accent: "",
      subtle: "",
    };
    const stats = paintChart(ctx, geometry, empty, { dpr: 1, kind: "line" });

    expect(stats.points).toBe(250);
    expect(passes()[0]?.texts.length).toBeGreaterThan(0);
  });
});
