/**
 * A `lightweight-charts` stand-in for jsdom.
 *
 * The real library draws into a canvas, and jsdom has no 2D context worth the
 * name: mounting it proves that a `<canvas>` exists and nothing about what the
 * component asked it to draw. This fake keeps the *contract* the component
 * depends on — one `createChart` per mount, `addSeries` returning a handle with
 * `setData`/`applyOptions`/`priceScale`, and `remove` on disposal — and records
 * every call, so a test can assert that an update is a `setData` on the chart
 * that already exists rather than a second renderer.
 *
 * It is installed per test file:
 *
 *     vi.mock("lightweight-charts", async () => {
 *       const { fakeLightweightCharts } = await import("./__fixtures__/lightweight");
 *       return fakeLightweightCharts();
 *     });
 *
 * The module instance is shared with the test file's static import, which is
 * how `fakeCharts` below sees what the component built.
 */

import { vi } from "vitest";

export interface FakeSeriesHandle {
  /** The definition's `type`: `Bar`, `Histogram`, … */
  readonly kind: string;
  /** Everything passed to `addSeries` and every later `applyOptions`. */
  readonly options: Record<string, unknown>;
  /** The last `setData` payload. */
  data: Array<Record<string, unknown>>;
  readonly setData: ReturnType<typeof vi.fn>;
  readonly applyOptions: ReturnType<typeof vi.fn>;
  priceScale(): { applyOptions: ReturnType<typeof vi.fn> };
}

export interface FakeTimeScale {
  fitContent: ReturnType<typeof vi.fn>;
  applyOptions: ReturnType<typeof vi.fn>;
}

export interface FakeChart {
  readonly host: HTMLElement;
  readonly options: Record<string, unknown>;
  readonly series: FakeSeriesHandle[];
  readonly addSeries: ReturnType<typeof vi.fn>;
  readonly applyOptions: ReturnType<typeof vi.fn>;
  readonly remove: ReturnType<typeof vi.fn>;
  /** A method, as the library's API is — with the same spies on the handle. */
  readonly timeScale: (() => FakeTimeScale) & FakeTimeScale;
}

/** Every chart created since the test began, oldest first. */
export const fakeCharts: FakeChart[] = [];

/** Forget the charts of the previous test. */
export function resetFakeCharts(): void {
  fakeCharts.length = 0;
}

/** The most recently created chart, or undefined. */
export function lastChart(): FakeChart | undefined {
  return fakeCharts[fakeCharts.length - 1];
}

/** The runtime surface the component imports. */
export function fakeLightweightCharts() {
  const createChart = vi.fn(
    (host: HTMLElement, options: Record<string, unknown> = {}): FakeChart => {
      const series: FakeSeriesHandle[] = [];
      const timeScaleHandle: FakeTimeScale = { fitContent: vi.fn(), applyOptions: vi.fn() };
      const timeScale = Object.assign(() => timeScaleHandle, timeScaleHandle);
      const chart: FakeChart = {
        host,
        options: { ...options },
        series,
        timeScale,
        addSeries: vi.fn(
          (definition: { type?: string }, seriesOptions: Record<string, unknown> = {}) => {
            const handle: FakeSeriesHandle = {
              kind: definition.type ?? "unknown",
              options: { ...seriesOptions },
              data: [],
              setData: vi.fn((data: Array<Record<string, unknown>>) => {
                handle.data = [...data];
              }),
              applyOptions: vi.fn((next: Record<string, unknown>) => {
                Object.assign(handle.options, next);
              }),
              priceScale: () => ({ applyOptions: vi.fn() }),
            };
            series.push(handle);
            return handle;
          },
        ),
        applyOptions: vi.fn((next: Record<string, unknown>) => {
          Object.assign(chart.options, next);
        }),
        remove: vi.fn(),
      };
      fakeCharts.push(chart);
      return chart;
    },
  );

  return {
    createChart,
    BarSeries: { type: "Bar" },
    HistogramSeries: { type: "Histogram" },
    LineSeries: { type: "Line" },
    ColorType: { Solid: "solid", VerticalGradient: "gradient" },
    CrosshairMode: { Normal: 0, Magnet: 1, Hidden: 2 },
    LineStyle: { Solid: 0, Dotted: 1, Dashed: 2, LargeDashed: 3, SparseDotted: 4 },
  };
}
