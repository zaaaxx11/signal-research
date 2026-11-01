/**
 * The chart's geometry: where every pixel of a series goes, with no canvas and
 * no DOM in sight.
 *
 * Four decisions, each one a trap the obvious implementation falls into:
 *
 * **The price domain is the bars, not zero.** A 1-day chart of a $500 stock
 * that moved 1.2 % has a useful range of $494–$506; anchoring it at $0 would
 * squash the entire day into two pixels. `price.min` is the lowest low minus
 * 8 % of the span (1 % of the level when every bar is flat), and it is *never*
 * widened to include zero. "Down" is a direction, not a distance from the
 * origin.
 *
 * **X is time, but a closed market is not a proportional hole.** A five-day
 * chart is five sessions separated by four nights and one weekend; a literal
 * epoch scale would spend 70 % of its width on hours in which nothing traded.
 * So each interval's width is `min(Δtime, 3 × medianStep)` — time-proportional
 * inside a session, with a bounded gutter at its edges. The weekend is visible
 * (three steps wide, not zero) and cannot compress the bars that carry the
 * story, which is the adversarial case stated as "a stop in trading must not
 * compress the x-axis to nothing".
 *
 * **A data gap is a break, not a slope.** The same weights are used for x, but
 * a raw Δ greater than 4 × median starts a new polyline segment. A daily
 * Friday→Monday (3 × 1 day) is a normal market closure and the line crosses it;
 * a halt or a missing session (10 ×, 100 ×) is not a price move and no line is
 * drawn across it.
 *
 * **A flat series has no span, and no division.** `max === min` would make
 * `y()` produce NaN for every point; the domain pads a flat level by 1 % of the
 * level (or 0.5 for a zero series), so the line is drawn across the middle of
 * the box instead of disappearing.
 *
 * A single bar is the degenerate case of all of the above: one weight, one
 * point at the horizontal centre, a body with a real width. It renders.
 */

import { axisPrice, axisTime, estimateLabelWidth } from "./format";
import type { Bar, ChartRange } from "./types";

export interface ChartBox {
  readonly x: number;
  readonly y: number;
  readonly w: number;
  readonly h: number;
}

export interface ChartPoint {
  readonly index: number;
  readonly bar: Bar;
  readonly x: number;
  readonly y: number;
}

export interface ChartCandle {
  readonly index: number;
  readonly x: number;
  /** Body width in CSS px; at least 1, so a dense series still paints. */
  readonly width: number;
  readonly up: boolean;
  readonly bodyTop: number;
  readonly bodyBottom: number;
  readonly wickTop: number;
  readonly wickBottom: number;
}

export interface VolumeBar {
  readonly index: number;
  readonly x: number;
  readonly width: number;
  readonly y: number;
  readonly height: number;
  readonly up: boolean;
}

export interface AxisTick {
  readonly value: number;
  readonly x: number;
  readonly y: number;
  readonly label: string;
}

export interface PriceDomain {
  readonly min: number;
  readonly max: number;
  /** The y-grid's step, for label precision. */
  readonly step: number;
}

export interface TimeDomain {
  readonly from: number;
  readonly to: number;
  /** The typical spacing between bars, in seconds. */
  readonly medianStep: number;
  /** How many times the series breaks its line. */
  readonly gapBreaks: number;
}

export interface ChartGeometry {
  readonly width: number;
  readonly height: number;
  /** The canvas minus its axis gutters; the frame every datum sits inside. */
  readonly plot: ChartBox;
  readonly priceBox: ChartBox;
  readonly volumeBox: ChartBox;
  readonly bars: readonly Bar[];
  readonly points: readonly ChartPoint[];
  /** Polyline runs; a new run starts where the data gaps. */
  readonly segments: readonly (readonly ChartPoint[])[];
  readonly candles: readonly ChartCandle[];
  readonly volume: { readonly max: number; readonly bars: readonly VolumeBar[] };
  readonly price: PriceDomain;
  readonly time: TimeDomain;
  readonly xTicks: readonly AxisTick[];
  readonly yTicks: readonly AxisTick[];
  /** `null` when the payload had no previous close; may sit outside the box. */
  readonly previousCloseY: number | null;
  readonly barWidth: number;
  /** X for an epoch second, interpolated between bars and clamped at the ends. */
  readonly x: (epochSeconds: number) => number;
  readonly y: (price: number) => number;
}

export interface GeometryInput {
  readonly width: number;
  readonly height: number;
  readonly bars: readonly Bar[];
  readonly previousClose?: number | null;
  readonly range?: ChartRange;
  readonly timezone?: string;
  readonly showVolume?: boolean;
}

export const AXIS_RIGHT = 58;
export const AXIS_BOTTOM = 18;
export const PAD_TOP = 10;
export const PAD_LEFT = 8;
export const VOLUME_SHARE = 0.22;
export const VOLUME_MIN = 22;
export const VOLUME_MAX = 56;
export const VOLUME_GAP = 6;
export const PRICE_PAD = 0.08;
/** A closed market's gutter is at most this many typical steps wide. */
export const GAP_CLAMP = 3;
/** A raw interval longer than this many typical steps is a break in the series. */
export const GAP_BREAK = 4;

function clamp(value: number, low: number, high: number): number {
  return Math.max(low, Math.min(high, value));
}

function median(values: readonly number[]): number {
  if (values.length === 0) return 0;
  const sorted = [...values].sort((left, right) => left - right);
  const middle = Math.floor(sorted.length / 2);
  if (sorted.length % 2 === 1) return sorted[middle] as number;
  return ((sorted[middle - 1] as number) + (sorted[middle] as number)) / 2;
}

/**
 * Clean ascending bars, sorted by time, one row per timestamp.
 *
 * `parseChart` already does this; the geometry repeats the last two steps
 * because it is exported as a pure function and a caller may hand it rows from
 * a fixture, a test, or a future streaming append. Repeating a cheap sort is
 * cheaper than a painter that has to defend against unsorted input.
 */
function cleanBars(bars: readonly Bar[]): Bar[] {
  const byTime = new Map<number, Bar>();
  for (const bar of bars) {
    if (
      !Number.isFinite(bar.t) ||
      !Number.isFinite(bar.o) ||
      !Number.isFinite(bar.h) ||
      !Number.isFinite(bar.l) ||
      !Number.isFinite(bar.c)
    ) {
      continue;
    }
    byTime.set(bar.t, {
      t: bar.t,
      o: bar.o,
      h: bar.h,
      l: bar.l,
      c: bar.c,
      v: Number.isFinite(bar.v) && bar.v > 0 ? bar.v : 0,
    });
  }
  return [...byTime.values()].sort((left, right) => left.t - right.t);
}

/**
 * The padded price window. Never includes 0 unless a bar (or the prior close)
 * sits there.
 *
 * The previous close is part of the frame, not an annotation outside it: a 1D
 * chart that gaps down 10 % has every bar below yesterday's close, and a
 * reference line that vanishes exactly when the gap matters is not a downward
 * chart — it is a chart lying by omission. It is included only when finite and
 * positive, so a missing value can never drag the axis to the origin.
 */
function priceWindow(
  bars: readonly Bar[],
  previousClose: number | null,
): { min: number; max: number } {
  let low = Number.POSITIVE_INFINITY;
  let high = Number.NEGATIVE_INFINITY;
  for (const bar of bars) {
    if (bar.l < low) low = bar.l;
    if (bar.h > high) high = bar.h;
  }
  if (previousClose !== null && Number.isFinite(previousClose) && previousClose > 0) {
    if (previousClose < low) low = previousClose;
    if (previousClose > high) high = previousClose;
  }
  if (!Number.isFinite(low) || !Number.isFinite(high)) return { min: 0, max: 1 };

  const span = high - low;
  // A flat series has no span to take 8 % of. Pad by 1 % of the level, with a
  // floor for a zero-valued series, so `max > min` always holds.
  const pad = span > 0 ? span * PRICE_PAD : Math.max(Math.abs(high), 1) * 0.01;
  const min = low - pad;
  const max = high + pad;
  if (!(max > min)) return { min: low - 0.5, max: low + 0.5 };
  return { min, max };
}

/** 1/2/2.5/5/10 × 10^n steps, ascending, within `[min, max]`. */
export function niceTicks(
  min: number,
  max: number,
  target: number,
): { values: number[]; step: number } {
  if (
    !Number.isFinite(min) ||
    !Number.isFinite(max) ||
    !Number.isFinite(target) ||
    target < 1 ||
    max <= min
  ) {
    return { values: [], step: max > min ? max - min : 1 };
  }
  const raw = (max - min) / target;
  const magnitude = 10 ** Math.floor(Math.log10(raw));
  const normalised = raw / magnitude;
  const nice =
    normalised <= 1 ? 1 : normalised <= 2 ? 2 : normalised <= 2.5 ? 2.5 : normalised <= 5 ? 5 : 10;
  const step = nice * magnitude;
  const values: number[] = [];
  const first = Math.ceil(min / step - 1e-9) * step;
  for (let value = first; value <= max + step * 1e-9 && values.length < 12; value += step) {
    values.push(Number(value.toFixed(10)));
  }
  return { values, step };
}

/**
 * The index of the point nearest to `x`, by binary search.
 *
 * Hover is arithmetic over the same points the painter used — not a DOM element
 * per datum. Empty points answer -1, which every caller treats as "no hover".
 */
export function nearestIndex(points: readonly { readonly x: number }[], x: number): number {
  if (points.length === 0) return -1;
  if (x <= (points[0] as { x: number }).x) return 0;
  const last = points.length - 1;
  if (x >= (points[last] as { x: number }).x) return last;

  let low = 0;
  let high = last;
  while (high - low > 1) {
    const middle = (low + high) >> 1;
    if ((points[middle] as { x: number }).x <= x) low = middle;
    else high = middle;
  }
  const left = points[low] as { x: number };
  const right = points[high] as { x: number };
  return x - left.x <= right.x - x ? low : high;
}

/**
 * Build one frame's geometry.
 *
 * Every output is a function of the inputs and nothing else: no clock, no
 * canvas, no viewport read. That is what makes the frame testable at a size the
 * tests choose, and what lets a 250-bar repaint be cheaper than React's commit.
 */
export function chartGeometry(input: GeometryInput): ChartGeometry {
  const width = Number.isFinite(input.width) ? Math.max(0, input.width) : 0;
  const height = Number.isFinite(input.height) ? Math.max(0, input.height) : 0;
  const bars = cleanBars(input.bars);
  const range = input.range ?? "1d";
  const timezone = input.timezone ?? "UTC";
  const showVolume = input.showVolume !== false;

  const plot: ChartBox = {
    x: PAD_LEFT,
    y: PAD_TOP,
    w: Math.max(0, width - PAD_LEFT - AXIS_RIGHT),
    h: Math.max(0, height - PAD_TOP - AXIS_BOTTOM),
  };

  const wantedVolume = showVolume
    ? clamp(Math.round(plot.h * VOLUME_SHARE), VOLUME_MIN, VOLUME_MAX)
    : 0;
  // A 40 px card still gets a price line: the volume strip yields first.
  const volumeHeight = Math.min(wantedVolume, Math.max(0, plot.h - 24));
  const volumeGap = volumeHeight > 0 ? VOLUME_GAP : 0;
  const priceHeight = Math.max(0, plot.h - volumeHeight - volumeGap);

  const priceBox: ChartBox = { x: plot.x, y: plot.y, w: plot.w, h: priceHeight };
  const volumeBox: ChartBox = {
    x: plot.x,
    y: plot.y + priceHeight + volumeGap,
    w: plot.w,
    h: volumeHeight,
  };

  const { min, max } = priceWindow(bars, input.previousClose ?? null);
  const priceSpan = max - min;

  // The y-grid's target count is a function of the room a label needs (44 px),
  // not a constant: a 132 px card gets two lines, a 360 px chart gets five.
  const yTarget = clamp(Math.floor(priceHeight / 44), 2, 5);
  const yTickValues = niceTicks(min, max, yTarget);
  const step = yTickValues.step;

  const y = (value: number): number => {
    if (!Number.isFinite(value) || !(priceSpan > 0)) {
      return priceBox.y + priceBox.h / 2;
    }
    return priceBox.y + ((max - value) / priceSpan) * priceBox.h;
  };

  // --- time weights -------------------------------------------------------
  const deltas: number[] = [];
  for (let index = 1; index < bars.length; index += 1) {
    const delta = (bars[index] as Bar).t - (bars[index - 1] as Bar).t;
    if (delta > 0) deltas.push(delta);
  }
  const medianStep = median(deltas) || 1;

  const weights: number[] = new Array<number>(bars.length).fill(0);
  let total = 0;
  for (let index = 1; index < bars.length; index += 1) {
    const delta = (bars[index] as Bar).t - (bars[index - 1] as Bar).t;
    // A backwards timestamp is already impossible after the sort; a zero one is
    // a duplicate the clean-up collapsed. Both contribute nothing.
    total += clamp(delta, 0, medianStep * GAP_CLAMP);
    weights[index] = total;
  }

  const centre = plot.x + plot.w / 2;
  const positions: number[] = bars.map((_bar, index) => {
    if (bars.length <= 1 || !(total > 0)) return centre;
    const ratio = (weights[index] as number) / total;
    return plot.x + ratio * plot.w;
  });
  // Floating accumulation can leave the last point a hair inside or outside the
  // box; pin it so a line's endpoint is exactly the plot's right edge.
  if (positions.length > 1 && total > 0) positions[positions.length - 1] = plot.x + plot.w;

  const slot = bars.length > 1 ? plot.w / (bars.length - 1) : plot.w;
  const barWidth =
    bars.length <= 1
      ? Math.min(12, Math.max(2, plot.w * 0.02))
      : clamp(slot * 0.6, 1, 18);

  const points: ChartPoint[] = bars.map((bar, index) => ({
    index,
    bar,
    x: positions[index] as number,
    y: y(bar.c),
  }));

  // --- segments -----------------------------------------------------------
  const segments: ChartPoint[][] = [];
  let current: ChartPoint[] = [];
  let gapBreaks = 0;
  for (let index = 0; index < points.length; index += 1) {
    const point = points[index] as ChartPoint;
    if (index > 0) {
      const delta = point.bar.t - (points[index - 1] as ChartPoint).bar.t;
      if (delta > medianStep * GAP_BREAK) {
        if (current.length > 0) segments.push(current);
        current = [];
        gapBreaks += 1;
      }
    }
    current.push(point);
  }
  if (current.length > 0) segments.push(current);

  // --- candles ------------------------------------------------------------
  const candles: ChartCandle[] = bars.map((bar, index) => {
    const up = bar.c >= bar.o;
    const top = y(Math.max(bar.o, bar.c));
    const bottom = y(Math.min(bar.o, bar.c));
    return {
      index,
      x: positions[index] as number,
      width: barWidth,
      up,
      bodyTop: top,
      // A doji is a line, not a vanished bar: one pixel is the minimum body.
      bodyBottom: Math.max(bottom, top + 1),
      wickTop: y(bar.h),
      wickBottom: y(bar.l),
    };
  });

  // --- volume -------------------------------------------------------------
  let volumeMax = 0;
  for (const bar of bars) if (bar.v > volumeMax) volumeMax = bar.v;
  const volumeBars: VolumeBar[] = bars.map((bar, index) => {
    const fraction = volumeMax > 0 ? bar.v / volumeMax : 0;
    const barHeight = fraction > 0 ? Math.max(1, fraction * volumeBox.h) : 0;
    return {
      index,
      x: positions[index] as number,
      width: barWidth,
      y: volumeBox.y + volumeBox.h - barHeight,
      height: barHeight,
      up: bar.c >= bar.o,
    };
  });

  // --- axis ticks ---------------------------------------------------------
  const xTicks: AxisTick[] = [];
  if (bars.length > 0 && plot.w > 0) {
    const wanted = clamp(Math.floor(plot.w / 90), 1, 6);
    const candidateIndices: number[] = [];
    if (wanted === 1) {
      candidateIndices.push(Math.floor((bars.length - 1) / 2));
    } else {
      for (let index = 0; index < wanted; index += 1) {
        candidateIndices.push(Math.round((index * (bars.length - 1)) / (wanted - 1)));
      }
    }
    const candidates = candidateIndices.map((index) => {
      const bar = bars[index] as Bar;
      const label = axisTime(bar.t, range, timezone);
      return {
        value: bar.t,
        x: positions[index] as number,
        y: plot.y + plot.h + 4,
        label,
      };
    });
    for (const tick of candidates) {
      const half = estimateLabelWidth(tick.label) / 2;
      // "Only where they fit": inside the canvas, and not on top of the label
      // already placed to its left.
      if (tick.x - half < 0 || tick.x + half > width) continue;
      const previous = xTicks[xTicks.length - 1];
      if (previous !== undefined) {
        const previousRight = previous.x + estimateLabelWidth(previous.label) / 2;
        if (tick.x - half < previousRight + 6) continue;
      }
      xTicks.push(tick);
    }
    // A narrow card can drop every edge candidate; one honest centred date is
    // more useful than an axis with no time on it at all.
    if (xTicks.length === 0 && candidates.length > 0) {
      const middle = candidates[Math.floor(candidates.length / 2)] as AxisTick;
      const half = estimateLabelWidth(middle.label) / 2;
      if (middle.x - half >= 0 && middle.x + half <= width) xTicks.push(middle);
    }
  }

  const yTicks: AxisTick[] = [];
  if (bars.length > 0 && priceBox.h > 0) {
    const axisWidth = Math.max(0, width - (plot.x + plot.w));
    for (const value of yTickValues.values) {
      const label = axisPrice(value, step);
      if (estimateLabelWidth(label) > axisWidth - 8) continue;
      const tickY = y(value);
      if (tickY < priceBox.y - 0.5 || tickY > priceBox.y + priceBox.h + 0.5) continue;
      yTicks.push({ value, x: plot.x + plot.w + 6, y: tickY, label });
    }
  }

  // x() interpolates between the two bracketing bars. The positions are already
  // cumulative time, so a linear fraction of the raw interval lands exactly on
  // the weight the cumulative sum uses.
  const x = (epochSeconds: number): number => {
    if (bars.length === 0) return plot.x;
    if (bars.length === 1) return centre;
    if (!Number.isFinite(epochSeconds)) return centre;
    const firstBar = bars[0] as Bar;
    const lastBar = bars[bars.length - 1] as Bar;
    if (epochSeconds <= firstBar.t) return positions[0] as number;
    if (epochSeconds >= lastBar.t) return positions[positions.length - 1] as number;

    let low = 0;
    let high = bars.length - 1;
    while (high - low > 1) {
      const middle = (low + high) >> 1;
      if ((bars[middle] as Bar).t <= epochSeconds) low = middle;
      else high = middle;
    }
    const left = bars[low] as Bar;
    const right = bars[high] as Bar;
    const leftX = positions[low] as number;
    const rightX = positions[high] as number;
    if (right.t === left.t) return leftX;
    const fraction = (epochSeconds - left.t) / (right.t - left.t);
    return leftX + (rightX - leftX) * fraction;
  };

  const previous = input.previousClose;
  const previousCloseY =
    previous !== null && previous !== undefined && Number.isFinite(previous)
      ? y(previous)
      : null;

  return {
    width,
    height,
    plot,
    priceBox,
    volumeBox,
    bars,
    points,
    segments,
    candles,
    volume: { max: volumeMax, bars: volumeBars },
    price: { min, max, step },
    time: {
      from: bars[0]?.t ?? 0,
      to: bars[bars.length - 1]?.t ?? 0,
      medianStep,
      gapBreaks,
    },
    xTicks,
    yTicks,
    previousCloseY,
    barWidth,
    x,
    y,
  };
}
