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
 * **Panes are recorded too.** v5 splits the chart into panes, and an RSI study
 * lives in one of its own. The fake models the two facts the component relies
 * on: `addPane()` appends a pane that `addSeries` can be called on, and
 * removing the last series of a non-preserved pane drops the pane — the
 * library's own rule, which is why the component never calls `removePane()`
 * itself. A test that toggles RSI twice can therefore see the pane count go
 * 1 → 2 → 1 without guessing at internals.
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
  /** The definition's `type`: `Bar`, `Histogram`, `Line`, … */
  readonly kind: string;
  /** Everything passed to `addSeries` and every later `applyOptions`. */
  readonly options: Record<string, unknown>;
  /** The last `setData` payload. */
  data: Array<Record<string, unknown>>;
  /** The pane the series was created in: 0 is the price pane. */
  pane: number;
  readonly setData: ReturnType<typeof vi.fn>;
  readonly applyOptions: ReturnType<typeof vi.fn>;
  priceScale(): { applyOptions: ReturnType<typeof vi.fn> };
}

export interface FakePane {
  /** `addPane(true)` asks the library to keep the pane when it empties. */
  readonly preserve: boolean;
  /** The handles created through this pane, in creation order. */
  readonly series: FakeSeriesHandle[];
  readonly setStretchFactor: ReturnType<typeof vi.fn>;
  readonly setHeight: ReturnType<typeof vi.fn>;
  readonly addSeries: ReturnType<typeof vi.fn>;
  paneIndex(): number;
  getHTMLElement(): HTMLElement | null;
  priceScale(): { applyOptions: ReturnType<typeof vi.fn> };
}

export interface FakeTimeScale {
  fitContent: ReturnType<typeof vi.fn>;
  applyOptions: ReturnType<typeof vi.fn>;
}

export interface FakeChart {
  readonly host: HTMLElement;
  readonly options: Record<string, unknown>;
  /** Every series on the chart, across panes, in creation order. */
  readonly series: FakeSeriesHandle[];
  readonly panesList: FakePane[];
  readonly addSeries: ReturnType<typeof vi.fn>;
  readonly addPane: ReturnType<typeof vi.fn>;
  readonly removeSeries: ReturnType<typeof vi.fn>;
  readonly removePane: ReturnType<typeof vi.fn>;
  readonly panes: () => FakePane[];
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

/** A handle with the same spies and shape every series gets. */
function makeSeries(
  definition: { type?: string },
  seriesOptions: Record<string, unknown>,
  pane: number,
): FakeSeriesHandle {
  const handle: FakeSeriesHandle = {
    kind: definition.type ?? "unknown",
    options: { ...seriesOptions },
    data: [],
    pane,
    setData: vi.fn((data: Array<Record<string, unknown>>) => {
      handle.data = [...data];
    }),
    applyOptions: vi.fn((next: Record<string, unknown>) => {
      Object.assign(handle.options, next);
    }),
    priceScale: () => ({ applyOptions: vi.fn() }),
  };
  return handle;
}

/** The runtime surface the component imports. */
export function fakeLightweightCharts() {
  const createChart = vi.fn(
    (host: HTMLElement, options: Record<string, unknown> = {}): FakeChart => {
      const series: FakeSeriesHandle[] = [];
      const panesList: FakePane[] = [];
      const timeScaleHandle: FakeTimeScale = { fitContent: vi.fn(), applyOptions: vi.fn() };
      const timeScale = Object.assign(() => timeScaleHandle, timeScaleHandle);

      const makePane = (preserve: boolean): FakePane => {
        const pane: FakePane = {
          preserve,
          series: [],
          setStretchFactor: vi.fn(),
          setHeight: vi.fn(),
          paneIndex: () => panesList.indexOf(pane),
          getHTMLElement: () => document.createElement("div"),
          priceScale: () => ({ applyOptions: vi.fn() }),
          addSeries: vi.fn(
            (definition: { type?: string }, seriesOptions: Record<string, unknown> = {}) =>
              addToPane(pane, definition, seriesOptions),
          ),
        };
        panesList.push(pane);
        return pane;
      };

      const addToPane = (
        pane: FakePane,
        definition: { type?: string },
        seriesOptions: Record<string, unknown>,
      ): FakeSeriesHandle => {
        const handle = makeSeries(definition, seriesOptions, panesList.indexOf(pane));
        series.push(handle);
        pane.series.push(handle);
        return handle;
      };

      const chart: FakeChart = {
        host,
        options: { ...options },
        series,
        panesList,
        timeScale,
        addSeries: vi.fn(
          (definition: { type?: string }, seriesOptions: Record<string, unknown> = {}) =>
            addToPane(panesList[0] as FakePane, definition, seriesOptions),
        ),
        addPane: vi.fn((preserve = false) => makePane(preserve)),
        removeSeries: vi.fn((handle: FakeSeriesHandle) => {
          const at = series.indexOf(handle);
          if (at !== -1) series.splice(at, 1);
          const pane = panesList[handle.pane];
          if (pane === undefined) return;
          const inPane = pane.series.indexOf(handle);
          if (inPane !== -1) pane.series.splice(inPane, 1);
          // The library's rule: an empty pane is dropped unless it asked to be
          // preserved, and the price pane is never dropped.
          if (!pane.preserve && pane.series.length === 0 && panesList.length > 1) {
            const paneAt = panesList.indexOf(pane);
            if (paneAt > 0) panesList.splice(paneAt, 1);
          }
        }),
        removePane: vi.fn((index: number) => {
          if (index > 0 && index < panesList.length) panesList.splice(index, 1);
        }),
        panes: () => panesList,
        applyOptions: vi.fn((next: Record<string, unknown>) => {
          Object.assign(chart.options, next);
        }),
        remove: vi.fn(),
      };
      // The price pane exists before the first series is added.
      makePane(false);
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
