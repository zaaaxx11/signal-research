/**
 * The painter: one 2D context, one pass, no element per bar.
 *
 * The chart must feel like the same instrument as the rest of the terminal: a
 * rose line on the page plane, a volume strip under it, a dashed previous close,
 * and axis labels only where they can be read. Four decisions carry that:
 *
 * **The context is scaled, the box is not.** `setTransform(dpr, …)` makes one
 * CSS pixel the unit of every coordinate and font size, so a 10 px label is
 * drawn at 20 device pixels on a retina display rather than upscaled — the
 * devicePixelRatio-2 adversarial case. The canvas's CSS size never changes.
 *
 * **A gap is a new path.** The geometry already split the points into runs; the
 * painter calls `moveTo` at the start of each and never bridges two. That is
 * the difference between "the stock halted" and "the stock fell 40 %".
 *
 * **The previous close is dashed, never a solid band.** It is a reference, not
 * a series; the dash pattern is the only axis-independent way to say so, and
 * the "prev" word keeps it readable in greyscale.
 *
 * **Candles batch by direction.** `strokeStyle` changes are the expensive part,
 * not the paths: the wicks are drawn in two passes (up, down) and the bodies in
 * two fills, which is what keeps 250 candles inside a hover frame.
 */

import type { ChartGeometry } from "./geometry";
import type { ChartTheme } from "./theme";

export type ChartKind = "line" | "candle";

export interface PaintOptions {
  /** Device pixel ratio; the canvas backing store is already sized for it. */
  readonly dpr: number;
  readonly kind: ChartKind;
  /** Index into `geometry.points`, or null for no crosshair. */
  readonly hoverIndex?: number | null;
  readonly showVolume?: boolean;
}

export interface PaintStats {
  readonly points: number;
  readonly segments: number;
  readonly candles: number;
  readonly labels: number;
  readonly volumeBars: number;
}

const MONO =
  'ui-monospace, "SF Mono", "JetBrains Mono", Menlo, Consolas, "Liberation Mono", monospace';
const AXIS_FONT = `500 10px ${MONO}`;

function half(value: number): number {
  return Math.round(value) + 0.5;
}

function colour(ctx: CanvasRenderingContext2D, slot: "fillStyle" | "strokeStyle", value: string): void {
  // An unresolved token is not "black": leaving the previous style in place
  // would paint a line in the wrong colour. Skipping the assignment keeps the
  // last deliberate one, and every caller guards the draw itself with the same
  // truthiness check.
  if (value.length > 0) ctx[slot] = value;
}

function hLine(ctx: CanvasRenderingContext2D, x1: number, x2: number, y: number, stroke: string): void {
  if (stroke.length === 0) return;
  colour(ctx, "strokeStyle", stroke);
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(x1, half(y));
  ctx.lineTo(x2, half(y));
  ctx.stroke();
}

function vLine(ctx: CanvasRenderingContext2D, x: number, y1: number, y2: number, stroke: string): void {
  if (stroke.length === 0) return;
  colour(ctx, "strokeStyle", stroke);
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(half(x), y1);
  ctx.lineTo(half(x), y2);
  ctx.stroke();
}

/**
 * Paint one frame and report what was drawn.
 *
 * The stats are the component's `data-*` contract: a test and the proof harness
 * can say "250 bars, two runs, six labels" without reading pixels, and the
 * browser proof can time the same call the reader's hover uses.
 */
export function paintChart(
  ctx: CanvasRenderingContext2D,
  geometry: ChartGeometry,
  theme: ChartTheme,
  options: PaintOptions,
): PaintStats {
  const { width, height, plot, priceBox, volumeBox, points } = geometry;
  const dpr = Number.isFinite(options.dpr) && options.dpr > 0 ? options.dpr : 1;
  const showVolume = options.showVolume !== false;

  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);
  if (theme.surface.length > 0) {
    ctx.fillStyle = theme.surface;
    ctx.fillRect(0, 0, width, height);
  }

  if (width <= 0 || height <= 0 || plot.w <= 0 || priceBox.h <= 0) {
    return { points: 0, segments: 0, candles: 0, labels: 0, volumeBars: 0 };
  }

  // --- grid ---------------------------------------------------------------
  for (const tick of geometry.yTicks) {
    hLine(ctx, plot.x, plot.x + plot.w, tick.y, theme.border);
  }
  for (const tick of geometry.xTicks) {
    vLine(ctx, tick.x, plot.y, plot.y + plot.h, theme.border);
  }

  // --- previous close -----------------------------------------------------
  let labels = 0;
  const previousY = geometry.previousCloseY;
  if (
    previousY !== null &&
    previousY >= priceBox.y &&
    previousY <= priceBox.y + priceBox.h &&
    theme.subtle.length > 0
  ) {
    colour(ctx, "strokeStyle", theme.subtle);
    ctx.lineWidth = 1;
    ctx.setLineDash([4, 4]);
    ctx.beginPath();
    ctx.moveTo(plot.x, half(previousY));
    ctx.lineTo(plot.x + plot.w, half(previousY));
    ctx.stroke();
    ctx.setLineDash([]);

    ctx.font = AXIS_FONT;
    ctx.textAlign = "left";
    ctx.textBaseline = "bottom";
    colour(ctx, "fillStyle", theme.subtle);
    ctx.fillText("prev", plot.x + 2, previousY - 2);
    labels += 1;
  }

  // --- volume -------------------------------------------------------------
  let volumeBars = 0;
  if (showVolume && volumeBox.h > 0) {
    // One style per direction, not per bar: setting `fillStyle` is the
    // expensive canvas call, and 250 alternating directions would pay it 250
    // times for two colours.
    ctx.globalAlpha = 0.35;
    for (const direction of [true, false]) {
      colour(ctx, "fillStyle", direction ? theme.up : theme.down);
      for (const entry of geometry.volume.bars) {
        if (entry.up !== direction || entry.height <= 0) continue;
        const width = Math.max(1, Math.round(entry.width));
        ctx.fillRect(entry.x - width / 2, entry.y, width, Math.max(1, entry.height));
        volumeBars += 1;
      }
    }
    ctx.globalAlpha = 1;
  }

  // --- series -------------------------------------------------------------
  let segments = 0;
  let candles = 0;
  const accent = theme.accent.length > 0 ? theme.accent : theme.focus;

  if (options.kind === "candle") {
    for (const direction of [true, false]) {
      ctx.beginPath();
      let any = false;
      for (const candle of geometry.candles) {
        if (candle.up !== direction) continue;
        ctx.moveTo(half(candle.x), candle.wickTop);
        ctx.lineTo(half(candle.x), candle.wickBottom);
        any = true;
      }
      if (any) {
        colour(ctx, "strokeStyle", direction ? theme.up : theme.down);
        ctx.lineWidth = 1;
        ctx.stroke();
      }
    }

    for (const direction of [true, false]) {
      colour(ctx, "fillStyle", direction ? theme.up : theme.down);
      for (const candle of geometry.candles) {
        if (candle.up !== direction) continue;
        ctx.fillRect(
          candle.x - Math.max(1, candle.width) / 2,
          candle.bodyTop,
          Math.max(1, candle.width),
          Math.max(1, candle.bodyBottom - candle.bodyTop),
        );
        candles += 1;
      }
    }
  } else {
    for (const segment of geometry.segments) {
      if (segment.length === 0) continue;
      segments += 1;
      if (segment.length === 1) {
        const only = segment[0] as (typeof segment)[number];
        colour(ctx, "fillStyle", accent);
        ctx.beginPath();
        ctx.arc(only.x, only.y, 2.5, 0, Math.PI * 2);
        ctx.fill();
        continue;
      }
      colour(ctx, "strokeStyle", accent);
      ctx.lineWidth = 1.6;
      ctx.lineJoin = "round";
      ctx.beginPath();
      const first = segment[0] as (typeof segment)[number];
      ctx.moveTo(first.x, first.y);
      for (let index = 1; index < segment.length; index += 1) {
        const point = segment[index] as (typeof segment)[number];
        ctx.lineTo(point.x, point.y);
      }
      ctx.stroke();
    }
  }

  // --- axes ---------------------------------------------------------------
  ctx.font = AXIS_FONT;
  ctx.textAlign = "left";
  ctx.textBaseline = "middle";
  colour(ctx, "fillStyle", theme.muted);
  for (const tick of geometry.yTicks) {
    ctx.fillText(tick.label, tick.x, tick.y);
    labels += 1;
  }

  ctx.textAlign = "center";
  ctx.textBaseline = "top";
  colour(ctx, "fillStyle", theme.muted);
  for (const tick of geometry.xTicks) {
    ctx.fillText(tick.label, tick.x, tick.y);
    labels += 1;
  }

  // --- crosshair ----------------------------------------------------------
  const hoverIndex = options.hoverIndex;
  if (hoverIndex !== null && hoverIndex !== undefined) {
    const point = points[hoverIndex];
    if (point !== undefined && accent.length > 0) {
      ctx.globalAlpha = 0.5;
      colour(ctx, "strokeStyle", accent);
      ctx.lineWidth = 1;
      ctx.setLineDash([3, 3]);
      ctx.beginPath();
      ctx.moveTo(half(point.x), priceBox.y);
      ctx.lineTo(half(point.x), priceBox.y + priceBox.h);
      ctx.moveTo(plot.x, half(point.y));
      ctx.lineTo(plot.x + plot.w, half(point.y));
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.globalAlpha = 1;

      colour(ctx, "fillStyle", accent);
      ctx.beginPath();
      ctx.arc(point.x, point.y, 2.5, 0, Math.PI * 2);
      ctx.fill();
    }
  }

  return {
    points: points.length,
    segments,
    candles,
    labels,
    volumeBars,
  };
}
