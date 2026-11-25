/**
 * BubbleView: a packed bubble chart — radius = market cap, fill = change%.
 *
 * Bubbles are placed largest-first with a spiral search so they never
 * overlap (collision avoidance), and any bubble with r >= 8 gets a ticker
 * label painted on the canvas so the chart is readable without hovering.
 */

import * as React from "react";

import { signedPercent } from "./format";
import { prepareTheme, rampFill, readTheme } from "./theme";
import type { MapPayload, MapStock } from "./types";

export interface BubbleViewProps {
  readonly payload: MapPayload;
  readonly onOpenSymbol?: (symbol: string) => void;
  readonly className?: string;
}

interface BubblePoint {
  readonly stock: MapStock;
  readonly cx: number;
  readonly cy: number;
  readonly r: number;
}

const MIN_RADIUS = 2;
const MAX_RADIUS = 32;

const LABEL_MIN_RADIUS = 8;

/** Maximum spiral steps to attempt for one bubble before giving up. */
const MAX_SPIRAL_STEPS = 400;

function flattenSorted(payload: MapPayload): MapStock[] {
  return payload.sectors.flatMap((s) => [...s.stocks]).sort((a, b) => b.cap - a.cap);
}

function mapRadius(cap: number, maxCap: number): number {
  if (maxCap <= 0) return MIN_RADIUS;
  const log = Math.log1p(cap) / Math.log1p(maxCap);
  return MIN_RADIUS + log * (MAX_RADIUS - MIN_RADIUS);
}

/**
 * Place one bubble with spiral collision avoidance.
 *
 * Starts at the ideal (x₀, y₀) and walks an Archimedean spiral outward
 * until a position is found whose distance to every already-placed circle
 * is >= r + that circle's radius, or until MAX_SPIRAL_STEPS is exhausted.
 */
function placeBubble(
  x0: number,
  y0: number,
  r: number,
  placed: readonly BubblePoint[],
): { cx: number; cy: number } | null {
  // The ideal spot works in a sparse chart; try it first.
  if (!collision(x0, y0, r, placed)) return { cx: x0, cy: y0 };

  // Spiral: radius grows linearly, each lap has ~2π * radius steps at density 1/r.
  // Step size is max(2, r * 0.7) so packed bubbles resolve quickly.
  const step = Math.max(2, r * 0.7);
  let angle = 0;
  let spiralR = step;
  for (let i = 0; i < MAX_SPIRAL_STEPS; i += 1) {
    angle += step / Math.max(1, spiralR);
    spiralR = step * Math.sqrt(i + 1);
    const dx = spiralR * Math.cos(angle);
    const dy = spiralR * Math.sin(angle);
    if (!collision(x0 + dx, y0 + dy, r, placed)) {
      return { cx: x0 + dx, cy: y0 + dy };
    }
  }
  return null;
}

function collision(
  cx: number,
  cy: number,
  r: number,
  placed: readonly BubblePoint[],
): boolean {
  for (const p of placed) {
    const dx = p.cx - cx;
    const dy = p.cy - cy;
    const minDist = p.r + r + 1; // 1 px gap
    if (dx * dx + dy * dy < minDist * minDist) return true;
  }
  return false;
}

export function BubbleView({ payload, onOpenSymbol, className }: BubbleViewProps) {
  const canvasRef = React.useRef<HTMLCanvasElement>(null);
  const wrapRef = React.useRef<HTMLDivElement>(null);
  const [size, setSize] = React.useState({ w: 0, h: 0 });
  const [hover, setHover] = React.useState<{
    stock: MapStock;
    x: number;
    y: number;
  } | null>(null);
  const palette = React.useMemo(() => prepareTheme(readTheme()), []);

  const stocks = React.useMemo(() => flattenSorted(payload), [payload]);

  // Layout with collision avoidance
  const points = React.useMemo(() => {
    if (stocks.length === 0 || size.w <= 0 || size.h <= 0) return [];
    const maxCap = stocks[0]?.cap ?? 1;
    const pad = MAX_RADIUS + 4;
    const plotW = size.w - pad * 2;
    const plotH = size.h - pad * 2;
    if (plotW <= 0 || plotH <= 0) return [];

    const result: BubblePoint[] = [];
    for (const stock of stocks) {
      const r = mapRadius(stock.cap, maxCap);
      // Ideal position: x = change% (-6..+6), y = log(cap) descending
      const x0 = pad + ((Math.max(-6, Math.min(6, stock.change)) + 6) / 12) * plotW;
      const y0 = pad + (1 - Math.log1p(stock.cap) / Math.log1p(maxCap)) * plotH;
      const placed = placeBubble(x0, y0, r, result);
      if (placed) {
        result.push({ stock, cx: placed.cx, cy: placed.cy, r });
      }
    }
    return result;
  }, [stocks, size]);

  // Paint
  React.useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || size.w <= 0 || size.h <= 0) return;
    const dpr = Math.min(3, Math.max(1, window.devicePixelRatio || 1));
    const cw = Math.round(size.w * dpr);
    const ch = Math.round(size.h * dpr);
    if (canvas.width !== cw) canvas.width = cw;
    if (canvas.height !== ch) canvas.height = ch;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, size.w, size.h);

    for (const p of points) {
      const fill = rampFill(p.stock.change, palette);
      ctx.fillStyle = fill.fill;
      ctx.beginPath();
      ctx.arc(p.cx, p.cy, p.r, 0, Math.PI * 2);
      ctx.fill();

      // Ticker label for larger bubbles
      if (p.r >= LABEL_MIN_RADIUS) {
        ctx.fillStyle = fill.ink;
        ctx.font = fontForRadius(p.r);
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        const text = p.stock.symbol.slice(0, 6);
        ctx.fillText(text, p.cx, p.cy);
      }
    }
  }, [points, size, palette]);

  // Resize
  React.useEffect(() => {
    const node = wrapRef.current;
    if (!node) return;
    const apply = (w: number, h: number) => {
      setSize((prev) => {
        const nw = Math.max(0, Math.round(w));
        const nh = Math.max(0, Math.round(h));
        return prev.w === nw && prev.h === nh ? prev : { w: nw, h: nh };
      });
    };
    const measure = () => {
      const r = node.getBoundingClientRect();
      apply(r.width, r.height);
    };
    measure();
    const obs = new ResizeObserver((entries) => {
      const e = entries[entries.length - 1];
      if (e) apply(e.contentRect.width, e.contentRect.height);
    });
    obs.observe(node);
    return () => obs.disconnect();
  }, []);

  const tooltip = hover ? (
    <div
      style={{
        position: "fixed",
        left: Math.min(hover.x + 12, window.innerWidth - 220),
        top: Math.min(hover.y + 12, window.innerHeight - 120),
        width: 200,
        pointerEvents: "none",
      }}
      className="pointer-events-none z-50 rounded-md border border-line-strong bg-surface-raised p-2 shadow-lg"
    >
      <p className="font-mono text-data font-semibold text-fg">{hover.stock.symbol}</p>
      {hover.stock.name ? (
        <p className="mt-0.5 text-label text-fg-muted">{hover.stock.name}</p>
      ) : null}
      {hover.stock.price !== null ? (
        <p className="mt-0.5 font-mono text-data text-fg">
          ${hover.stock.price.toFixed(2)}
        </p>
      ) : null}
      <p
        className={`nums mt-0.5 font-mono text-data ${
          signedPercent(hover.stock.change).sign === 1
            ? "text-up"
            : signedPercent(hover.stock.change).sign === -1
              ? "text-down"
              : "text-flat"
        }`}
      >
        {signedPercent(hover.stock.change).text}
      </p>
    </div>
  ) : null;

  return (
    <div ref={wrapRef} className={className ?? "relative min-h-0 flex-1"}>
      <canvas
        ref={canvasRef}
        data-testid="bubble-canvas"
        className="block h-full w-full"
        onPointerMove={(e) => {
          const rect = canvasRef.current?.getBoundingClientRect();
          if (!rect) return;
          const cx = e.clientX - rect.left;
          const cy = e.clientY - rect.top;
          // Find the closest point
          let best: BubblePoint | null = null;
          let bestDist = Infinity;
          for (const p of points) {
            const dx = p.cx - cx;
            const dy = p.cy - cy;
            const dist = dx * dx + dy * dy;
            if (dist < bestDist && dist <= p.r * p.r * 4) {
              bestDist = dist;
              best = p;
            }
          }
          if (best) {
            setHover({ stock: best.stock, x: e.clientX, y: e.clientY });
          } else {
            setHover(null);
          }
        }}
        onPointerLeave={() => setHover(null)}
        onClick={(e) => {
          const rect = canvasRef.current?.getBoundingClientRect();
          if (!rect) return;
          const cx = e.clientX - rect.left;
          const cy = e.clientY - rect.top;
          for (const p of points) {
            const dx = p.cx - cx;
            const dy = p.cy - cy;
            if (dx * dx + dy * dy <= p.r * p.r) {
              if (onOpenSymbol) onOpenSymbol(p.stock.symbol);
              break;
            }
          }
        }}
      />
      {tooltip}
    </div>
  );
}

/**
 * Font sizing for ticker labels painted inside bubbles.
 *
 * Radius r → a font-size string that fits comfortably.
 */
function fontForRadius(r: number): string {
  if (r >= 20) return "bold 9px monospace";
  if (r >= 14) return "bold 7px monospace";
  return "bold 6px monospace";
}