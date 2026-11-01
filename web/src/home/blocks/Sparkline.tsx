/**
 * The one sparkline on the page, used by the index strip and the spark cards.
 *
 * A canvas, because a 30-point trend is a hundred DOM nodes otherwise and Home
 * draws eight of them. Five decisions are load-bearing:
 *
 * * **No axes, no grid, no labels.** The number beside the line says what the
 *   series ended at; the line's only job is shape. A y-axis on a 28 px strip is
 *   noise pretending to be rigour.
 * * **DPR is a transform, not a CSS box.** The backing store is `css × dpr`, the
 *   element stays CSS-sized, and the context is scaled once — sharp at DPR 2
 *   instead of upscaled.
 * * **Tone comes from the change, not from the line.** A rising series is
 *   `--up`, a falling one `--down`, flat `--flat`; the colour is *repeated* by
 *   the delta beside it, never the only carrier (the delta's sign and arrow are
 *   in the text).
 * * **Fewer than two points is a dot, never a division by zero.** One close has
 *   a price but no shape, so the canvas draws a single marker; an empty series
 *   draws the same marker and says so in its label.
 * * **The canvas is an image with a name.** `role="img"` and an `aria-label`
 *   that states the symbol, the last price and the change are the only way a
 *   screen reader can know what the picture shows.
 *
 * Colours are read from the live stylesheet with `getComputedStyle`, exactly as
 * `charts/theme.ts` does, so there is no hex value in this file and a palette
 * change repaints the strip without touching it. In jsdom (or before the
 * stylesheet applies) the slots are empty and the painter draws nothing; the
 * label still carries every fact.
 */

import * as React from "react";

import { formatMoney, formatSignedPercent } from "@/grid/format";
import { cn } from "@/lib/utils";

export interface SparkInk {
  readonly up: string;
  readonly down: string;
  readonly flat: string;
}

const EMPTY_INK: SparkInk = { up: "", down: "", flat: "" };

let rootInk: SparkInk | null = null;

function resolve(name: string): string {
  return window.getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/** The three tones, read once per document. */
export function readSparkInk(): SparkInk {
  if (typeof window === "undefined" || typeof document === "undefined") return EMPTY_INK;
  if (rootInk === null) {
    rootInk = { up: resolve("--up"), down: resolve("--down"), flat: resolve("--flat") };
  }
  return rootInk;
}

/** Test hook: forget the cached palette (a stylesheet swapped in a test). */
export function resetSparkInk(): void {
  rootInk = null;
}

export type SparkShape = "line" | "dot" | "none";

export interface PaintSparkOptions {
  readonly points: readonly number[];
  /** CSS pixels; the context is already scaled by DPR. */
  readonly width: number;
  readonly height: number;
  readonly colour: string;
}

/**
 * Draw one series. Returns what was drawn, so a test can assert the shape
 * without pixels.
 *
 * `points.length < 2` is the case that used to be a `NaN` y-scale: one close (or
 * none) cannot define a span, so the painter draws a marker at the right edge
 * at mid-height instead of dividing by zero.
 */
export function paintSparkline(
  ctx: CanvasRenderingContext2D,
  { points, width, height, colour }: PaintSparkOptions,
): SparkShape {
  if (!(width > 0) || !(height > 0)) return "none";
  ctx.clearRect(0, 0, width, height);
  if (colour.length === 0) return "none";

  if (points.length < 2) {
    ctx.fillStyle = colour;
    ctx.beginPath();
    ctx.arc(width - 2.5, height / 2, 2, 0, Math.PI * 2);
    ctx.fill();
    return "dot";
  }

  let min = points[0] ?? 0;
  let max = min;
  for (const point of points) {
    if (point < min) min = point;
    if (point > max) max = point;
  }
  const span = max - min;
  const stepX = (width - 2) / (points.length - 1);
  const yOf = (value: number) =>
    span === 0 ? height / 2 : height - 1 - ((value - min) / span) * (height - 2);

  ctx.strokeStyle = colour;
  ctx.lineWidth = 1.25;
  ctx.lineJoin = "round";
  ctx.lineCap = "round";
  ctx.beginPath();
  points.forEach((value, index) => {
    const x = 1 + index * stepX;
    const y = yOf(value);
    if (index === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();
  return "line";
}

export interface SparklineProps {
  readonly closes: readonly number[];
  readonly symbol: string;
  readonly price: number | null;
  readonly changePct: number | null;
  /** CSS height of the strip. 22 in a spark card, 18 under an index card. */
  readonly height?: number;
  readonly className?: string;
}

/** The sentence a screen reader hears instead of the picture. */
export function sparkLabel(
  symbol: string,
  price: number | null,
  changePct: number | null,
  count: number,
): string {
  const last = price === null ? "last price unavailable" : `last ${formatMoney(price)}`;
  const change =
    changePct === null ? "change unavailable" : `change ${formatSignedPercent(changePct)}`;
  const series =
    count === 0
      ? "no price series to draw"
      : count === 1
        ? "one close, drawn as a dot"
        : `${count}-point trend`;
  return `${symbol}: ${last}, ${change}, ${series}`;
}

function toneOf(changePct: number | null): keyof SparkInk {
  if (changePct === null || changePct === 0) return "flat";
  return changePct > 0 ? "up" : "down";
}

export function Sparkline({
  closes,
  symbol,
  price,
  changePct,
  height = 22,
  className,
}: SparklineProps) {
  const hostRef = React.useRef<HTMLDivElement>(null);
  const canvasRef = React.useRef<HTMLCanvasElement>(null);
  const [width, setWidth] = React.useState(0);
  const label = sparkLabel(symbol, price, changePct, closes.length);

  // Measure the box, not the canvas: the canvas is `size-full`, so its own
  // width is zero before the first paint and would never trigger a second one.
  React.useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const apply = (value: number) => {
      const next = Math.max(0, Math.round(value));
      setWidth((previous) => (previous === next ? previous : next));
    };
    const measure = () => {
      apply(host.getBoundingClientRect().width || host.clientWidth);
    };
    measure();

    if (typeof ResizeObserver === "undefined") {
      window.addEventListener("resize", measure);
      return () => window.removeEventListener("resize", measure);
    }
    const observer = new ResizeObserver((entries) => {
      const entry = entries[entries.length - 1];
      if (entry) apply(entry.contentRect.width);
    });
    observer.observe(host);
    return () => observer.disconnect();
  }, []);

  React.useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || width <= 0) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const dpr = Math.min(typeof window !== "undefined" ? window.devicePixelRatio || 1 : 1, 3);
    canvas.width = Math.max(1, Math.round(width * dpr));
    canvas.height = Math.max(1, Math.round(height * dpr));
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    const ink = readSparkInk();
    paintSparkline(ctx, {
      points: closes,
      width,
      height,
      colour: ink[toneOf(changePct)],
    });
  }, [closes, changePct, width, height]);

  return (
    <div
      ref={hostRef}
      data-testid={`spark-host-${symbol}`}
      style={{ height }}
      className={cn("pointer-events-none relative w-full", className)}
    >
      <canvas
        ref={canvasRef}
        role="img"
        aria-label={label}
        data-testid={`sparkline-${symbol}`}
        data-shape={closes.length < 2 ? "dot" : "line"}
        className="block size-full"
      />
    </div>
  );
}
