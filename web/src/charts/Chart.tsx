/**
 * The chart instrument: one canvas, a range selector, a hover readout, and the
 * same facts as a table for anyone who is not reading the picture.
 *
 * It is deliberately not a chart *library*. A library would ship a layout
 * engine, a gesture system and a theme language for one instrument; this is
 * `geometry.ts` (pure arithmetic), `paint.ts` (one 2D pass) and this component,
 * which owns the only three things that need a browser: measurement, pointer
 * hit-testing and the canvas backing store.
 *
 * **Two renderers, one instrument.** `renderer="bars"` (used by the charts
 * route and the stock page) hands the same parsed series to
 * `lightweight-charts` through `ChartBars`, imported dynamically so the library
 * is a chunk of its own. Everything else here is shared: the read, the range
 * selector, the four states, the label and the live region. The canvas painter
 * stays the default for anything that has not asked for bars, and its
 * arithmetic-driven hover is a canvas-only feature — the library brings its own
 * crosshair.
 *
 * **Hover is arithmetic.** `nearestIndex` searches the points the painter used;
 * no element exists per bar, so a 250-bar series cannot get slower to hover than
 * a 25-bar one. The popup is `pointer-events: none` for the same reason the
 * map's is: a tooltip that can take the pointer freezes the surface under it.
 *
 * **The canvas is an image, and it has a text twin.** `role="img"` plus a label
 * that states the symbol, range and first/last close is what a screen reader
 * hears; the visually hidden table carries first, last, high and low for a
 * reader who wants numbers rather than a shape. The range selector is a
 * `role="group"` of `aria-pressed` buttons, not a menu.
 *
 * **Load, error, empty and ready are four distinct states.** A 404 says "No
 * chart for X"; it never renders axes around nothing, which would claim the
 * symbol exists and merely had no trades.
 */

import * as React from "react";

import { formatChange, formatPrice, formatVolume } from "@/home/signals";
import { cn } from "@/lib/utils";

import { toBars } from "./bars";
import { barTime } from "./format";
import { chartGeometry, nearestIndex } from "./geometry";
import { ChartBarsLazy } from "./lazy";
import { paintChart, type ChartKind } from "./paint";
import { mergeChartTheme, readChartTheme, type ChartTheme } from "./theme";
import {
  CHART_RANGES,
  RANGE_LABELS,
  RANGE_NAMES,
  seriesChange,
  type Bar,
  type ChartRange,
  type ChartSeries,
} from "./types";
import { useChartData } from "./useChartData";

export interface ChartProps {
  readonly symbol: string;
  /** The range the instrument opens on; the buttons own it afterwards. */
  readonly initialRange?: ChartRange;
  /** Total height in CSS px, toolbar included. */
  readonly height?: number;
  /** Canvas renderer only: a line or candles. Bars are always OHLC bars. */
  readonly kind?: ChartKind;
  /**
   * Which engine draws the series.
   *
   * `canvas` is the hand-rolled painter: no dependency, a line or candles, a
   * hover readout driven by arithmetic. `bars` hands the same payload to
   * `lightweight-charts` through a lazily imported `ChartBars` — real OHLC
   * bars, its own crosshair, and the same accessible table. The toolbar, the
   * states and the data read are identical for both, which is why this is a
   * prop and not a second component.
   */
  readonly renderer?: "canvas" | "bars";
  /** Bars only: draw the volume histogram under the price series. */
  readonly showVolume?: boolean;
  /** A gallery card: no toolbar, no range buttons, just the picture. */
  readonly compact?: boolean;
  readonly className?: string;
  /** Base palette; the live stylesheet's tokens win where they resolve. */
  readonly theme?: ChartTheme;
  /** Called with the series when it lands, and null on loading/error. */
  readonly onData?: (series: ChartSeries | null) => void;
}

const POPUP_WIDTH = 184;

function toneClass(percent: number | null): string {
  if (percent === null || percent === 0) return "text-flat";
  return percent > 0 ? "text-up" : "text-down";
}

function extremes(bars: readonly Bar[]): { high: number; low: number } | null {
  if (bars.length === 0) return null;
  let high = Number.NEGATIVE_INFINITY;
  let low = Number.POSITIVE_INFINITY;
  for (const bar of bars) {
    if (bar.h > high) high = bar.h;
    if (bar.l < low) low = bar.l;
  }
  return { high, low };
}

function readoutText(bar: Bar, range: ChartRange, timezone: string): string {
  return [
    barTime(bar.t, range, timezone),
    `open ${formatPrice(bar.o)}`,
    `high ${formatPrice(bar.h)}`,
    `low ${formatPrice(bar.l)}`,
    `close ${formatPrice(bar.c)}`,
    `volume ${formatVolume(bar.v)}`,
  ].join(", ");
}

export function Chart({
  symbol,
  initialRange = "1d",
  height = 320,
  kind = "line",
  renderer = "canvas",
  showVolume = false,
  compact = false,
  className,
  theme: baseTheme,
  onData,
}: ChartProps) {
  const [range, setRange] = React.useState<ChartRange>(initialRange);
  const view = useChartData(symbol, range);
  const wrapRef = React.useRef<HTMLDivElement>(null);
  const canvasRef = React.useRef<HTMLCanvasElement>(null);
  const [size, setSize] = React.useState({ w: 0, h: 0 });
  const [hover, setHover] = React.useState<{ index: number; x: number; y: number } | null>(null);
  // Bumped on a window resize so a display change (DPR 1 → 2) repaints the
  // backing store; a ResizeObserver sees the CSS box and would stay silent when
  // only the device pixel ratio changed.
  const [ratioTick, setRatioTick] = React.useState(0);

  const base = React.useMemo(() => baseTheme ?? readChartTheme(), [baseTheme]);
  const [resolvedTheme, setResolvedTheme] = React.useState<ChartTheme>(base);

  // A symbol change starts a new instrument: the old range is not the new
  // symbol's range, and the old hover is a bar that does not exist.
  React.useEffect(() => {
    setRange(initialRange);
  }, [initialRange, symbol]);

  // Measure once and on every resize. A ResizeObserver entry is already
  // delivered at most once per frame, so applying it is the debounce; a run
  // that lands on the same integer box is dropped rather than repainting.
  React.useEffect(() => {
    const node = wrapRef.current;
    if (!node) return;

    const apply = (width: number, boxHeight: number) => {
      const w = Math.max(0, Math.round(width));
      const h = Math.max(0, Math.round(boxHeight));
      setSize((previous) => (previous.w === w && previous.h === h ? previous : { w, h }));
    };
    const measure = () => {
      const rect = node.getBoundingClientRect();
      apply(rect.width, rect.height);
    };

    measure();

    if (typeof ResizeObserver === "undefined") {
      window.addEventListener("resize", measure);
      return () => window.removeEventListener("resize", measure);
    }
    const observer = new ResizeObserver((entries) => {
      const entry = entries[entries.length - 1];
      if (entry) apply(entry.contentRect.width, entry.contentRect.height);
      else measure();
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  React.useEffect(() => {
    const bump = () => setRatioTick((value) => value + 1);
    window.addEventListener("resize", bump);
    return () => window.removeEventListener("resize", bump);
  }, []);

  // The palette lives in the stylesheet, so the live values are read after the
  // node exists. A slot jsdom (or a failed stylesheet) cannot resolve keeps the
  // base value rather than collapsing to transparent.
  React.useEffect(() => {
    setResolvedTheme((current) => mergeChartTheme(current, readChartTheme(wrapRef.current)));
  }, [base]);

  const series = view.series;
  // The bar renderer does not need a geometry: the library lays out its own
  // scales, and the hover readout below is a property of the canvas painter.
  const geometry = React.useMemo(() => {
    if (series === null || renderer === "bars") return null;
    return chartGeometry({
      width: size.w,
      height: size.h,
      bars: series.bars,
      previousClose: series.previousClose,
      range: series.range,
      timezone: series.timezone,
      showVolume: true,
    });
  }, [series, size.w, size.h, renderer]);

  // The same payload the canvas painter would have read, in the shape
  // `lightweight-charts` accepts. `toBars` is pure and memoised on the series,
  // so a hover or a resize does not rebuild 78 bars.
  const barSeries = React.useMemo(
    () => (renderer === "bars" && series !== null ? toBars(series) : []),
    [renderer, series],
  );
  const plotHeight = Math.max(1, height - (compact ? 0 : 28));

  // Paint once per geometry/theme/hover. `receivedAt` is stamped by the hook,
  // so the canvas can report "data landed → first paint" for the proof harness.
  React.useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || geometry === null || size.w <= 0 || size.h <= 0) return;

    const dpr = Math.min(3, Math.max(1, window.devicePixelRatio || 1));
    const backingW = Math.round(size.w * dpr);
    const backingH = Math.round(size.h * dpr);
    // Assigning width reallocates and clears; only do it when the box or ratio
    // changed, so a hover repaint does not pay for a new surface.
    if (canvas.width !== backingW) canvas.width = backingW;
    if (canvas.height !== backingH) canvas.height = backingH;
    const ctx = canvas.getContext("2d");
    if (!ctx) {
      canvas.dataset.paintMs = "0";
      return;
    }

    const started = performance.now();
    const stats = paintChart(ctx, geometry, resolvedTheme, {
      dpr,
      kind,
      hoverIndex: hover?.index ?? null,
    });
    const elapsed = performance.now() - started;

    canvas.dataset.paintMs = elapsed.toFixed(2);
    canvas.dataset.dpr = String(dpr);
    canvas.dataset.bars = String(geometry.bars.length);
    canvas.dataset.points = String(stats.points);
    canvas.dataset.segments = String(geometry.segments.length);
    canvas.dataset.candles = String(stats.candles);
    canvas.dataset.labels = String(stats.labels);
    canvas.dataset.volumeBars = String(stats.volumeBars);
    canvas.dataset.painted = String(kind === "candle" ? stats.candles : stats.points);
    canvas.dataset.prev = geometry.previousCloseY === null ? "none" : "marked";
    canvas.dataset.width = String(size.w);
    canvas.dataset.height = String(size.h);
    if (series !== null && series.receivedAt > 0) {
      canvas.dataset.latencyMs = (started - series.receivedAt).toFixed(1);
    }
  }, [geometry, resolvedTheme, kind, hover, size.w, size.h, series, ratioTick]);

  // The card header reads the same payload the canvas does; a stable ref keeps
  // the callback out of the effect's dependencies (a new function per render
  // would otherwise re-report and could loop through the parent's state).
  const onDataRef = React.useRef(onData);
  onDataRef.current = onData;
  React.useEffect(() => {
    onDataRef.current?.(view.status === "ready" ? view.series : null);
  }, [view.status, view.series]);

  React.useEffect(() => {
    setHover(null);
  }, [view.series]);

  const bars = series?.bars ?? [];
  const first = bars[0];
  const last = bars[bars.length - 1];
  const bounds = extremes(bars);
  const change = series ? seriesChange(series) : null;
  const activeRange = series?.range ?? range;

  const locate = (clientX: number, clientY: number): { index: number; x: number; y: number } | null => {
    const canvas = canvasRef.current;
    if (!canvas || geometry === null) return null;
    const rect = canvas.getBoundingClientRect();
    const x = clientX - rect.left;
    const index = nearestIndex(geometry.points, x);
    if (index < 0) return null;
    return { index, x, y: clientY - rect.top };
  };

  const step = (direction: -1 | 1 | "home" | "end") => {
    if (geometry === null || geometry.points.length === 0) return;
    const lastIndex = geometry.points.length - 1;
    const current = hover?.index ?? lastIndex;
    const index =
      direction === "home"
        ? 0
        : direction === "end"
          ? lastIndex
          : Math.max(0, Math.min(lastIndex, current + direction));
    const point = geometry.points[index];
    if (point === undefined) return;
    setHover({ index, x: point.x, y: point.y });
  };

  const label =
    first !== undefined && last !== undefined
      ? `${symbol} chart, ${RANGE_NAMES[activeRange]}, first close ${formatPrice(first.c)}, last close ${formatPrice(last.c)}`
      : `${symbol} chart, ${RANGE_NAMES[activeRange]}, no bars`;

  const helpId = React.useId();
  const hoveredBar = hover !== null ? bars[hover.index] : undefined;
  const ready = view.status === "ready" && series !== null;
  const empty = ready && bars.length === 0;
  const showCanvas = ready && !empty;

  return (
    <section
      data-testid="chart"
      data-symbol={symbol}
      data-range={range}
      data-state={view.status}
      data-renderer={renderer}
      aria-busy={view.status === "loading"}
      style={{ height }}
      className={cn("flex min-w-0 flex-col overflow-hidden bg-surface", className)}
    >
      {!compact ? (
        <div
          data-testid="chart-toolbar"
          className="flex h-7 shrink-0 items-center gap-2 border-b border-line px-1"
        >
          <div
            data-testid="chart-ranges"
            role="group"
            aria-label={`Chart range for ${symbol}`}
            className="flex shrink-0 items-center gap-px"
          >
            {CHART_RANGES.map((value) => {
              const active = value === range;
              return (
                <button
                  key={value}
                  type="button"
                  data-testid={`chart-range-${value}`}
                  aria-pressed={active}
                  onClick={() => setRange(value)}
                  className={cn(
                    "rounded-sm px-1.5 py-0.5 font-mono text-label uppercase tracking-[0.08em] transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-focus",
                    active
                      ? "bg-row-selected text-accent-text"
                      : "text-fg-subtle hover:bg-row-hover hover:text-fg",
                  )}
                >
                  {RANGE_LABELS[value]}
                </button>
              );
            })}
          </div>

          {series !== null && change !== null && change.last !== null ? (
            <p className="ml-auto flex min-w-0 items-baseline gap-2 pr-1 font-mono text-data">
              <span data-testid="chart-last" className="nums shrink-0 text-fg">
                {formatPrice(change.last)}
              </span>
              <span
                data-testid="chart-change"
                className={cn("nums shrink-0 text-label", toneClass(change.percent))}
              >
                {formatChange(change.percent)}
              </span>
            </p>
          ) : null}
        </div>
      ) : null}

      <div ref={wrapRef} data-testid="chart-plot" className="relative min-h-0 w-full flex-1">
        {showCanvas && renderer === "canvas" ? (
          <canvas
            ref={canvasRef}
            data-testid="chart-canvas"
            role="img"
            aria-label={label}
            aria-describedby={helpId}
            tabIndex={0}
            data-hover-index={hover?.index ?? ""}
            className="absolute inset-0 h-full w-full touch-none focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-focus"
            onPointerMove={(event) => {
              const next = locate(event.clientX, event.clientY);
              if (next === null) {
                setHover((current) => (current === null ? current : null));
                return;
              }
              setHover(next);
            }}
            onPointerLeave={() => setHover(null)}
            onPointerDown={(event) => {
              const next = locate(event.clientX, event.clientY);
              if (next !== null) setHover(next);
            }}
            onKeyDown={(event) => {
              switch (event.key) {
                case "ArrowLeft":
                  event.preventDefault();
                  step(-1);
                  return;
                case "ArrowRight":
                  event.preventDefault();
                  step(1);
                  return;
                case "Home":
                  event.preventDefault();
                  step("home");
                  return;
                case "End":
                  event.preventDefault();
                  step("end");
                  return;
                case "Escape":
                  setHover(null);
                  return;
                default:
                  return;
              }
            }}
          />
        ) : null}

        {showCanvas && renderer === "bars" ? (
          <ChartBarsLazy
            bars={barSeries}
            height={plotHeight}
            label={label}
            showVolume={showVolume}
          />
        ) : null}

        {hover !== null && hoveredBar !== undefined ? (
          <div
            data-testid="chart-readout"
            role="tooltip"
            aria-hidden="true"
            style={{
              position: "absolute",
              left: Math.min(Math.max(4, hover.x + 14), Math.max(4, size.w - POPUP_WIDTH - 6)),
              top: Math.min(Math.max(4, hover.y + 14), Math.max(4, size.h - 112)),
              width: POPUP_WIDTH,
              pointerEvents: "none",
            }}
            className="z-20 rounded-md border border-line-strong bg-surface-raised p-1.5 shadow-lg"
          >
            <p data-testid="chart-readout-time" className="font-mono text-label text-fg-muted">
              {barTime(hoveredBar.t, activeRange, series?.timezone ?? "UTC")}
            </p>
            <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-2 gap-y-px font-mono text-label">
              <dt className="text-fg-subtle">O</dt>
              <dd data-testid="chart-readout-o" className="nums text-right text-fg">
                {formatPrice(hoveredBar.o)}
              </dd>
              <dt className="text-fg-subtle">H</dt>
              <dd data-testid="chart-readout-h" className="nums text-right text-fg">
                {formatPrice(hoveredBar.h)}
              </dd>
              <dt className="text-fg-subtle">L</dt>
              <dd data-testid="chart-readout-l" className="nums text-right text-fg">
                {formatPrice(hoveredBar.l)}
              </dd>
              <dt className="text-fg-subtle">C</dt>
              <dd data-testid="chart-readout-c" className="nums text-right text-fg">
                {formatPrice(hoveredBar.c)}
              </dd>
              <dt className="text-fg-subtle">V</dt>
              <dd data-testid="chart-readout-v" className="nums text-right text-fg-muted">
                {formatVolume(hoveredBar.v)}
              </dd>
            </dl>
          </div>
        ) : null}

        {view.status === "loading" ? (
          <div
            data-testid="chart-loading"
            aria-busy="true"
            aria-label={`Loading chart for ${symbol}`}
            className="absolute inset-0 z-10 flex items-center justify-center bg-surface p-3"
          >
            <div className="w-full max-w-[280px]">
              <div className="h-2 w-2/3 animate-pulse rounded-sm bg-accent-soft" />
              <div className="mt-2 h-2 w-1/2 animate-pulse rounded-sm bg-accent-soft" />
              <p className="mt-2 text-label text-fg-subtle">Reading {symbol}…</p>
            </div>
          </div>
        ) : null}

        {view.status === "error" ? (
          <div
            role="alert"
            data-testid="chart-error"
            data-not-found={view.statusCode === 404 ? "true" : "false"}
            className="absolute inset-0 z-10 flex flex-col items-center justify-center gap-1 bg-surface p-3 text-center"
          >
            <p className="text-data text-fg">
              {view.statusCode === 404 ? `No chart for ${symbol.toUpperCase()}` : `${symbol} chart did not load`}
            </p>
            <p data-testid="chart-error-message" className="max-w-[360px] text-label text-fg-muted">
              {view.statusCode === 404
                ? "That symbol is not in this chart source."
                : (view.error ?? "Unknown error")}
            </p>
            {view.statusCode === 404 ? null : (
              <button
                type="button"
                onClick={view.reload}
                className="mt-1 rounded-sm border border-line-control px-2 py-0.5 text-label text-fg transition-colors hover:bg-row-hover focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-focus"
              >
                Retry
              </button>
            )}
          </div>
        ) : null}

        {empty ? (
          <div
            data-testid="chart-empty"
            className="absolute inset-0 z-10 flex flex-col items-center justify-center gap-1 bg-surface p-3 text-center"
          >
            <p className="text-data text-fg">No bars for {symbol.toUpperCase()}</p>
            <p className="max-w-[360px] text-label text-fg-muted">
              The {RANGE_NAMES[activeRange]} range answered with no trades.
            </p>
          </div>
        ) : null}
      </div>

      <p id={helpId} className="sr-only">
        {symbol} price chart.{" "}
        {renderer === "bars"
          ? "This chart is drawn on a canvas; the same bars are listed as a table below, with open, high, low, close and volume for each one."
          : "Use the arrow keys to move the readout across the series, Home and End to jump to the first or last bar, and Escape to clear it. The same first, last, high and low values are listed in the table below."}
      </p>

      <p data-testid="chart-announcer" role="status" aria-live="polite" className="sr-only">
        {hoveredBar !== undefined && series !== null
          ? readoutText(hoveredBar, activeRange, series.timezone)
          : ""}
      </p>

      {renderer === "canvas" &&
      ready &&
      bars.length > 0 &&
      first !== undefined &&
      last !== undefined &&
      bounds !== null ? (
        <div data-testid="chart-summary-wrap" className="sr-only">
          <table data-testid="chart-summary">
            <caption>
              {symbol} chart, {RANGE_LABELS[activeRange]}. First close {formatPrice(first.c)}, last
              close {formatPrice(last.c)}, high {formatPrice(bounds.high)}, low{" "}
              {formatPrice(bounds.low)}.
            </caption>
            <thead>
              <tr>
                <th scope="col">Measure</th>
                <th scope="col">Value</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <th scope="row">First</th>
                <td data-testid="chart-summary-first">{formatPrice(first.c)}</td>
              </tr>
              <tr>
                <th scope="row">Last</th>
                <td data-testid="chart-summary-last">{formatPrice(last.c)}</td>
              </tr>
              <tr>
                <th scope="row">High</th>
                <td data-testid="chart-summary-high">{formatPrice(bounds.high)}</td>
              </tr>
              <tr>
                <th scope="row">Low</th>
                <td data-testid="chart-summary-low">{formatPrice(bounds.low)}</td>
              </tr>
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  );
}
