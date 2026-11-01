/**
 * One bar chart, drawn by `lightweight-charts`.
 *
 * This is the only component in the product that owns a canvas library. It is
 * kept deliberately small and presentational: it receives bars that
 * `bars.ts` has already ordered, de-duplicated and repaired, and it never
 * fetches, formats a symbol or decides what "no data" means. Its callers own
 * those states, exactly as they did for the hand-rolled canvas painter.
 *
 * Four decisions are load-bearing:
 *
 *   * **Created once, updated thereafter.** The chart and its series are made
 *     in one effect that runs on mount and torn down in its cleanup. A data
 *     change calls `setData`, never `createChart`: recreating a chart per
 *     render leaks a canvas and resets the reader's view. `remove()` on
 *     unmount is the library's only disposal path.
 *   * **Sized by the box, not by a number.** `autoSize` lets the library track
 *     its container with its own `ResizeObserver`; this component also
 *     observes the same box, which is what gives a browser without
 *     `ResizeObserver` (and jsdom) an explicit width/height fallback, and what
 *     lets the root publish the measured box for a test to read.
 *   * **The palette is the shell's.** Every colour is a token read from the
 *     live stylesheet (`theme.ts`): bars use `--up`/`--down`, the plane is
 *     `--surface`, hairlines are `--border`, the last-price line is
 *     `--accent`. There is no hex literal here, so a palette edit repaints the
 *     chart without touching it. `attributionLogo` is off: the only mark on
 *     this surface is the product's own.
 *   * **The canvas has a text twin.** A canvas is opaque to a screen reader,
 *     so the same bars are listed in a visually hidden table (time, O/H/L/C,
 *     volume) with a caption that says how many and in which order. The
 *     image's own name states the series; the table is where the numbers are.
 *     A switched-on study adds its own column, and a bar before the study has
 *     warmed up is an empty cell — not a zero and not a repeated last value.
 *
 * **Studies are series, not a second chart.** An SMA overlay is a `LineSeries`
 * on the price pane; RSI is a `LineSeries` in a pane of its own, created with
 * v5's `addPane()` the first time it is switched on and dropped when the last
 * series in it goes away (the library prunes an empty, unpreserved pane —
 * which is why nothing here calls `removePane`). Toggling a study adds or
 * removes a series on the chart that already exists: it never calls
 * `createChart` again, and it never resets the reader's view. The total height
 * is the container's, so a pane appearing splits an existing box instead of
 * growing the page.
 *
 * The values themselves are `indicators.ts`: pure functions over the closes,
 * memoised on the bars and the set of switched-on ids, so a hover, a resize or
 * a palette change does not redo 200-period arithmetic.
 *
 * Motion: the entrance fade is a class (`zt-chart-in`), and it is not rendered
 * at all when the reader asked for reduced motion — the stylesheet's own
 * `prefers-reduced-motion` block is the second line of defence for a mount
 * that happens before the preference is read.
 */

import * as React from "react";
import {
  BarSeries,
  ColorType,
  CrosshairMode,
  HistogramSeries,
  LineSeries,
  createChart,
} from "lightweight-charts";
import type {
  ChartOptions,
  DeepPartial,
  IChartApi,
  ISeriesApi,
  LineData,
} from "lightweight-charts";

import { useReducedMotion } from "@/grid/pending";
import { formatPrice, formatVolume } from "@/home/signals";
import { cn } from "@/lib/utils";

import { barData, type ChartBar } from "./bars";
import {
  INDICATOR_COLUMNS,
  RSI_PERIOD,
  SMA_BY_ID,
  orderIndicators,
  rsi,
  sma,
  type IndicatorId,
  type Point,
  type SmaId,
} from "./indicators";
import { readChartTheme, type ChartTheme } from "./theme";

export interface ChartBarsProps {
  /** Ascending, de-duplicated bars from `toBars`. */
  readonly bars: readonly ChartBar[];
  /** CSS height in px. Under 80 px the chart is a sparkline: no axes, no grid. */
  readonly height: number;
  /** The chart's accessible name, e.g. "S&P 500 chart, 1 day, last 78 bars". */
  readonly label: string;
  /** Draw the volume histogram under the price bars. */
  readonly showVolume?: boolean;
  /**
   * Price overlays (SMA) and the RSI study to draw, in any order. Absent or
   * empty is a bare bar chart; a strip under 80 px ignores them, because three
   * lines and a 36 px box are not a chart.
   */
  readonly indicators?: readonly IndicatorId[];
  readonly className?: string;
}

/** A compact chart is a trend, not an instrument: no scales, no crosshair. */
const COMPACT_HEIGHT = 80;

/** Half a day, in seconds: below this the x-axis is an intraday clock. */
const INTRADAY_SPAN = 43_200;

function fontStack(): string {
  if (typeof window === "undefined" || typeof document === "undefined") {
    return "ui-monospace, monospace";
  }
  const declared = window
    .getComputedStyle(document.documentElement)
    .getPropertyValue("--font-mono")
    .trim();
  return declared.length > 0 ? declared : "ui-monospace, SFMono-Regular, Menlo, monospace";
}

function ink(value: string): string | undefined {
  return value.length > 0 ? value : undefined;
}

interface View {
  readonly compact: boolean;
  readonly intraday: boolean;
  readonly showVolume: boolean;
  readonly height: number;
  readonly width: number;
  readonly autoSize: boolean;
}

function chartOptions(theme: ChartTheme, view: View): DeepPartial<ChartOptions> {
  const border = ink(theme.border);
  const label = ink(theme.subtle);
  const surface = ink(theme.surface);
  return {
    autoSize: view.autoSize,
    // The fallback the library documents for a browser whose ResizeObserver
    // never reports: ignored while `autoSize` is active.
    width: Math.max(1, Math.round(view.width)),
    height: Math.max(1, Math.round(view.height)),
    layout: {
      background: { type: ColorType.Solid, color: surface ?? "transparent" },
      textColor: ink(theme.muted),
      fontSize: 10,
      fontFamily: fontStack(),
      // No vendor mark on the product's surface.
      attributionLogo: false,
    },
    grid: {
      vertLines: { visible: false },
      horzLines: { visible: !view.compact, color: border },
    },
    rightPriceScale: {
      visible: !view.compact,
      borderColor: border,
      entireTextOnly: true,
      scaleMargins: view.showVolume ? { top: 0.1, bottom: 0.3 } : { top: 0.1, bottom: 0.1 },
    },
    timeScale: {
      visible: !view.compact,
      borderColor: border,
      timeVisible: view.intraday,
      secondsVisible: false,
      rightOffset: 0,
      // The series is the frame: a reader cannot drag the bars off screen and
      // then wonder where the chart went.
      fixLeftEdge: true,
      fixRightEdge: true,
      lockVisibleTimeRangeOnResize: true,
    },
    crosshair: view.compact
      ? {
          vertLine: { visible: false },
          horzLine: { visible: false },
        }
      : {
          mode: CrosshairMode.Normal,
          vertLine: { color: label, labelBackgroundColor: ink(theme.raised) },
          horzLine: { color: label, labelBackgroundColor: ink(theme.raised) },
        },
    // A page scrolls; a chart does not steal the wheel. Drag and pinch stay
    // available on the full-size instrument.
    handleScroll: view.compact
      ? false
      : { mouseWheel: false, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
    handleScale: view.compact
      ? false
      : { mouseWheel: false, axisPressedMouseMove: true, pinch: true },
  };
}

function barOptions(theme: ChartTheme, compact: boolean) {
  return {
    upColor: ink(theme.up),
    downColor: ink(theme.down),
    // A 36 px strip has no room for the open tick; a 180 px card does.
    openVisible: !compact,
    thinBars: compact,
    priceLineColor: ink(theme.accent),
    priceLineVisible: !compact,
    lastValueVisible: !compact,
  };
}

function volumeOptions(theme: ChartTheme, showVolume: boolean) {
  return {
    priceScaleId: "",
    priceFormat: { type: "volume" as const },
    color: ink(theme.subtle),
    priceLineVisible: false,
    lastValueVisible: false,
    visible: showVolume,
  };
}

function isOrdinal(bars: readonly ChartBar[]): boolean {
  const first = bars[0];
  return first !== undefined && first.timeLabel.startsWith("Bar ");
}

/**
 * The price pane keeps three quarters of the plot; a study is a footnote, not
 * a second instrument. Stretch factors (not pixels) are what make the split
 * survive a resize — the container's height is the only fixed number.
 */
const PRICE_STRETCH = 3;
const RSI_STRETCH = 1;

/**
 * Which token each overlay draws with. Theme slots, not hex: `--accent` for
 * the fast average, `--fg-muted` for the one that is on by default,
 * `--fg-subtle` for the slow one, so the three lines read as three weights of
 * the same ink rather than three colours competing with the bars.
 */
const OVERLAY_INK: Readonly<Record<SmaId, keyof ChartTheme>> = {
  sma20: "accent",
  sma50: "muted",
  sma200: "subtle",
};

interface LineOptions {
  readonly color?: string;
  readonly lineWidth: 1;
  readonly title: string;
  readonly crosshairMarkerVisible: boolean;
  readonly priceLineVisible: boolean;
  readonly lastValueVisible: boolean;
}

/**
 * A study's series options.
 *
 * No price line, no last-value badge and no crosshair marker: a moving average
 * is context for the bars, and three floating labels on the price scale would
 * be three numbers competing with the one the reader came for. `title` is the
 * period, which is what a future legend would read.
 */
function lineOptions(theme: ChartTheme, slot: keyof ChartTheme, title: string): LineOptions {
  const color = ink(theme[slot]);
  return {
    ...(color === undefined ? {} : { color }),
    lineWidth: 1,
    title,
    crosshairMarkerVisible: false,
    priceLineVisible: false,
    lastValueVisible: false,
  };
}

/**
 * The study's values, zipped onto the bars' own clock.
 *
 * The warm-up is dropped rather than sent as whitespace: the bar series owns
 * the time scale, so a line that starts at bar 50 simply starts there. Times
 * are already ascending and unique (see `bars.ts`), which is the only promise
 * `setData` makes.
 */
function indicatorLine(bars: readonly ChartBar[], values: readonly Point[]): LineData[] {
  const line: LineData[] = [];
  const count = Math.min(bars.length, values.length);
  for (let index = 0; index < count; index += 1) {
    const value = values[index];
    const bar = bars[index];
    if (value === null || value === undefined || bar === undefined) continue;
    line.push({ time: bar.time, value });
  }
  return line;
}

/** An RSI is not a price: two decimals, no currency mark, no grouping. */
function formatStudy(id: IndicatorId, value: Point): string {
  if (value === null) return "";
  return id === "rsi" ? value.toFixed(2) : formatPrice(value);
}

export function ChartBars({
  bars,
  height,
  label,
  showVolume = false,
  indicators,
  className,
}: ChartBarsProps) {
  const hostRef = React.useRef<HTMLDivElement>(null);
  const chartRef = React.useRef<IChartApi | null>(null);
  const barsRef = React.useRef<ISeriesApi<"Bar"> | null>(null);
  const volumeRef = React.useRef<ISeriesApi<"Histogram"> | null>(null);
  /** Overlay lines on the price pane, by id: created and removed on toggle. */
  const overlayRefs = React.useRef(new Map<IndicatorId, ISeriesApi<"Line">>());
  const rsiRef = React.useRef<ISeriesApi<"Line"> | null>(null);
  const tableId = React.useId();
  const captionId = `${tableId}-caption`;
  const reduced = useReducedMotion();
  const [renderable, setRenderable] = React.useState(true);
  const [size, setSize] = React.useState({ w: 0, h: 0 });
  const [theme, setTheme] = React.useState<ChartTheme>(() => readChartTheme());

  const compact = height < COMPACT_HEIGHT;
  const autoSize = typeof ResizeObserver !== "undefined";

  /**
   * The selection, as a stable list.
   *
   * The key is the identity that matters: a caller that passes a fresh array
   * with the same contents must not re-run 200-period arithmetic, and a caller
   * that reorders it must not move the table's columns. `active` is rebuilt
   * only when the key changes, so it is safe as an effect dependency.
   */
  const indicatorKey = React.useMemo(
    () => orderIndicators(indicators).join(","),
    [indicators],
  );
  const active = React.useMemo<IndicatorId[]>(
    () => (indicatorKey.length === 0 ? [] : (indicatorKey.split(",") as IndicatorId[])),
    [indicatorKey],
  );
  const rsiOn = indicatorKey.includes("rsi");

  /**
   * Every switched-on study, computed once per (bars, selection) pair.
   *
   * `bars` is already the adapter's output, so its identity changes only when
   * a new payload lands. RSI reads the same closes the SMA does: one array,
   * one pass per study, no second parse of the series.
   */
  const studies = React.useMemo(() => {
    const closes = bars.map((bar) => bar.close);
    const computed = new Map<IndicatorId, Point[]>();
    for (const id of active) {
      computed.set(id, id === "rsi" ? rsi(closes, RSI_PERIOD) : sma(closes, SMA_BY_ID[id]));
    }
    return computed;
  }, [bars, active]);

  const intraday = React.useMemo(() => {
    const first = bars[0];
    const last = bars[bars.length - 1];
    if (first === undefined || last === undefined || bars.length < 3) return false;
    return (last.time - first.time) / (bars.length - 1) < INTRADAY_SPAN;
  }, [bars]);

  const view = React.useMemo<View>(
    () => ({ compact, intraday, showVolume, height, width: size.w, autoSize }),
    [compact, intraday, showVolume, height, size.w, autoSize],
  );
  // The creation effect must not re-run when the palette resolves a moment
  // later, so the latest theme is read through a ref.
  const themeRef = React.useRef(theme);
  themeRef.current = theme;
  const viewRef = React.useRef(view);
  viewRef.current = view;

  // Read the live palette once the node exists, so a `[data-theme]` scope on
  // an ancestor wins. `readChartTheme` caches the root read and returns the
  // same object for an unscoped chart, so this is one render at most.
  React.useEffect(() => {
    const next = readChartTheme(hostRef.current);
    setTheme((current) => (next === current ? current : next));
  }, []);

  // Measure the box. The library's own observer keeps the canvas in step; this
  // one is the fallback for the environments that have no ResizeObserver, and
  // the number the component publishes for a test.
  React.useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const apply = (width: number, boxHeight: number) => {
      const w = Math.max(0, Math.round(width));
      const h = Math.max(0, Math.round(boxHeight));
      setSize((previous) => (previous.w === w && previous.h === h ? previous : { w, h }));
    };
    const measure = () => {
      const rect = host.getBoundingClientRect();
      apply(rect.width, rect.height);
    };
    measure();

    if (typeof ResizeObserver === "undefined") {
      window.addEventListener("resize", measure);
      return () => window.removeEventListener("resize", measure);
    }
    const observer = new ResizeObserver((entries) => {
      // The entry is already at most one per frame; applying it is the
      // debounce. An observer that reports a box the rect disagrees with is
      // still the browser's answer, so the entry wins.
      const entry = entries[entries.length - 1];
      if (entry) apply(entry.contentRect.width, entry.contentRect.height);
      else measure();
    });
    observer.observe(host);
    return () => observer.disconnect();
  }, []);

  // Create once. The chart's options are configured again by the palette
  // effect below; nothing here depends on data or on a later render.
  React.useEffect(() => {
    const host = hostRef.current;
    if (!host) return;

    let chart: IChartApi | null = null;
    let series: ISeriesApi<"Bar"> | null = null;
    let volume: ISeriesApi<"Histogram"> | null = null;
    try {
      chart = createChart(host, chartOptions(themeRef.current, viewRef.current));
      series = chart.addSeries(BarSeries, barOptions(themeRef.current, viewRef.current.compact));
      volume = chart.addSeries(
        HistogramSeries,
        volumeOptions(themeRef.current, viewRef.current.showVolume),
      );
      volume.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    } catch {
      // A browser that refuses the canvas still gets the table: the data is
      // the page's promise, the picture is the enhancement.
      chart?.remove();
      setRenderable(false);
      return;
    }
    if (!chart || !series || !volume) return;

    chartRef.current = chart;
    barsRef.current = series;
    volumeRef.current = volume;

    return () => {
      chartRef.current = null;
      barsRef.current = null;
      volumeRef.current = null;
      // The chart owns every series; `remove()` disposes them, so the maps are
      // only cleared, never removed one by one.
      overlayRefs.current.clear();
      rsiRef.current = null;
      chart.remove();
    };
  }, []);

  // Overlays: one line series per switched-on SMA, on the price pane. A toggle
  // adds or removes a series on the chart that exists — never a new chart, and
  // never a re-fit of the time scale, so the reader's view survives the click.
  React.useEffect(() => {
    const chart = chartRef.current;
    if (!chart || compact) return;

    const wanted = new Set<SmaId>(active.filter((id): id is SmaId => id !== "rsi"));
    for (const [id, series] of overlayRefs.current) {
      // The map only ever holds SMA ids; the cast is the map's invariant.
      if (wanted.has(id as SmaId)) continue;
      chart.removeSeries(series);
      overlayRefs.current.delete(id);
    }
    for (const id of wanted) {
      if (overlayRefs.current.has(id)) continue;
      overlayRefs.current.set(
        id,
        chart.addSeries(
          LineSeries,
          lineOptions(themeRef.current, OVERLAY_INK[id], INDICATOR_COLUMNS[id].header),
        ),
      );
    }
  }, [active, compact]);

  // RSI: a pane of its own, below the price pane. v5's `addPane` appends, and
  // removing the pane's only series is what drops the pane again (the library
  // prunes an empty, unpreserved pane), so there is no `removePane(1)` to get
  // wrong when the reader switches the study off.
  React.useEffect(() => {
    const chart = chartRef.current;
    if (!chart || compact) return;

    if (!rsiOn) {
      const series = rsiRef.current;
      if (series !== null) {
        chart.removeSeries(series);
        rsiRef.current = null;
      }
      return;
    }
    if (rsiRef.current !== null) return;

    chart.panes()[0]?.setStretchFactor(PRICE_STRETCH);
    const pane = chart.addPane();
    pane.setStretchFactor(RSI_STRETCH);
    rsiRef.current = pane.addSeries(
      LineSeries,
      lineOptions(themeRef.current, "accent", INDICATOR_COLUMNS.rsi.header),
    );
  }, [rsiOn, compact]);

  // The values, once the series above exist. Effects run in declaration order,
  // so a series created on this commit is in the map by the time this runs.
  React.useEffect(() => {
    for (const id of active) {
      const values = studies.get(id);
      if (values === undefined) continue;
      const series = id === "rsi" ? rsiRef.current : (overlayRefs.current.get(id) ?? null);
      series?.setData(indicatorLine(bars, values));
    }
  }, [active, studies, bars]);

  // Data, not structure: a new series is a `setData` on the chart that exists.
  React.useEffect(() => {
    const series = barsRef.current;
    const volume = volumeRef.current;
    const chart = chartRef.current;
    if (!series || !volume || !chart) return;

    series.setData(barData(bars));
    volume.setData(
      showVolume
        ? bars.map((bar) => ({
            time: bar.time,
            value: bar.volume,
            color: ink(theme.subtle),
          }))
        : [],
    );
    chart.timeScale().fitContent();
  }, [bars, showVolume, theme]);

  // The palette and the view flags can change under a mounted chart (a theme
  // switch, a card that grows); applying options is cheaper than a new canvas.
  React.useEffect(() => {
    const chart = chartRef.current;
    const series = barsRef.current;
    const volume = volumeRef.current;
    if (!chart || !series || !volume) return;
    chart.applyOptions(chartOptions(theme, view));
    series.applyOptions(barOptions(theme, compact));
    volume.applyOptions(volumeOptions(theme, showVolume));
    for (const [id, line] of overlayRefs.current) {
      line.applyOptions(lineOptions(theme, OVERLAY_INK[id as SmaId], INDICATOR_COLUMNS[id].header));
    }
    rsiRef.current?.applyOptions(lineOptions(theme, "accent", INDICATOR_COLUMNS.rsi.header));
  }, [theme, view, compact, showVolume]);

  const ordinal = isOrdinal(bars);
  const sentence = /[.!?]$/.test(label) ? label : `${label}.`;
  // The table states what it now carries: a reader who cannot see the lines
  // needs the period in words, and needs to know that a blank cell means "not
  // enough history", not "zero".
  const studyNote =
    active.length === 0
      ? ""
      : `Indicator columns: ${active
          .map((id) => INDICATOR_COLUMNS[id].description)
          .join("; ")}. A blank cell is a bar before the study had enough history to warm up.`;
  const caption = [
    sentence,
    `${bars.length} ${bars.length === 1 ? "bar" : "bars"}, oldest first.`,
    ordinal ? "Positions are bar numbers; this series carries no clock." : "Times are UTC.",
    studyNote,
  ]
    .join(" ")
    .trim();

  return (
    <div
      data-testid="chart-bars"
      data-bars={bars.length}
      data-height={height}
      data-width={size.w}
      data-bars-source={ordinal ? "ordinal" : "dated"}
      data-render={renderable ? "chart" : "table-only"}
      data-motion={reduced ? "reduced" : "full"}
      data-indicators={indicatorKey}
      data-rsi={rsiOn && !compact ? "pane" : "off"}
      style={{ height }}
      className={cn("relative w-full min-w-0 overflow-hidden", className)}
    >
      <div
        ref={hostRef}
        data-testid="chart-bars-canvas"
        role="img"
        aria-label={label}
        aria-describedby={captionId}
        className={cn(
          "h-full w-full",
          reduced ? undefined : "zt-chart-in",
          // A strip chart has no hover, no drag and no crosshair: it must not
          // eat the swipe that scrolls the row, or the tile becomes a wall.
          compact ? "pointer-events-none" : undefined,
        )}
      />

      <div className="sr-only">
        <table data-testid="chart-bars-table">
          <caption id={captionId}>{caption}</caption>
          <thead>
            <tr>
              <th scope="col">Time</th>
              <th scope="col">Open</th>
              <th scope="col">High</th>
              <th scope="col">Low</th>
              <th scope="col">Close</th>
              {showVolume ? <th scope="col">Volume</th> : null}
              {active.map((id) => (
                <th key={id} scope="col">
                  {INDICATOR_COLUMNS[id].header}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {bars.map((bar, index) => (
              <tr key={bar.time}>
                <th scope="row">{bar.timeLabel}</th>
                <td>{formatPrice(bar.open)}</td>
                <td>{formatPrice(bar.high)}</td>
                <td>{formatPrice(bar.low)}</td>
                <td>{formatPrice(bar.close)}</td>
                {showVolume ? <td>{formatVolume(bar.volume)}</td> : null}
                {active.map((id) => (
                  <td key={id} data-study={id}>
                    {formatStudy(id, studies.get(id)?.[index] ?? null)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
