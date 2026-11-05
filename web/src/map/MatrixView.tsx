/**
 * MatrixView: a grid of colored squares, one per stock, ticker + change% inside.
 *
 * Inspired by Finviz /map?view=matrix. Each cell is a canvas-rendered square
 * colored by the stock's percent change. Ticker and change% are drawn on top
 * when the cell is large enough. Hover highlights the cell and shows a tooltip.
 * Click navigates to the stock page.
 */

import * as React from "react";

import { signedPercent } from "./format";
import { prepareTheme, rampFill, readTheme } from "./theme";
import type { MapPayload, MapStock } from "./types";

export interface MatrixViewProps {
  readonly payload: MapPayload;
  readonly onOpenSymbol?: (symbol: string) => void;
  readonly className?: string;
}

const CELL_PX = 10;
const CELL_GAP = 1;
const MIN_CELL_FOR_TEXT = 24;

function flattenStocks(payload: MapPayload): MapStock[] {
  return payload.sectors.flatMap((sector) =>
    [...sector.stocks].sort((a, b) => b.cap - a.cap),
  );
}

export function MatrixView({ payload, onOpenSymbol, className }: MatrixViewProps) {
  const canvasRef = React.useRef<HTMLCanvasElement>(null);
  const wrapRef = React.useRef<HTMLDivElement>(null);
  const [size, setSize] = React.useState({ w: 0, h: 0 });
  const [hover, setHover] = React.useState<{
    stock: MapStock;
    x: number;
    y: number;
  } | null>(null);
  const palette = React.useMemo(() => prepareTheme(readTheme()), []);

  const stocks = React.useMemo(() => flattenStocks(payload), [payload]);

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

    const cols = Math.max(1, Math.floor((size.w + CELL_GAP) / (CELL_PX + CELL_GAP)));

    for (let i = 0; i < stocks.length; i++) {
      const stock = stocks[i] as MapStock;
      const col = i % cols;
      const row = Math.floor(i / cols);
      const x = col * (CELL_PX + CELL_GAP);
      const y = row * (CELL_PX + CELL_GAP);
      if (y + CELL_PX > size.h) break;

      const fill = rampFill(stock.change, palette);
      ctx.fillStyle = fill.fill;
      ctx.fillRect(x, y, CELL_PX, CELL_PX);

      if (CELL_PX >= MIN_CELL_FOR_TEXT) {
        ctx.fillStyle = fill.ink;
        ctx.font = '500 5px ui-monospace, "SF Mono", monospace';
        ctx.textBaseline = "top";
        ctx.textAlign = "left";
        ctx.fillText(stock.symbol.slice(0, 3), x + 1, y + 1);
      }
    }
  }, [stocks, size, palette]);

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
        data-testid="matrix-canvas"
        className="block h-full w-full"
        onPointerMove={(e) => {
          const rect = canvasRef.current?.getBoundingClientRect();
          if (!rect) return;
          const cx = e.clientX - rect.left;
          const cy = e.clientY - rect.top;
          const col = Math.floor(cx / (CELL_PX + CELL_GAP));
          const row = Math.floor(cy / (CELL_PX + CELL_GAP));
          const cols = Math.max(1, Math.floor((rect.width + CELL_GAP) / (CELL_PX + CELL_GAP)));
          const idx = row * cols + col;
          if (idx >= 0 && idx < stocks.length) {
            setHover({ stock: stocks[idx] as MapStock, x: e.clientX, y: e.clientY });
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
          const col = Math.floor(cx / (CELL_PX + CELL_GAP));
          const row = Math.floor(cy / (CELL_PX + CELL_GAP));
          const cols = Math.max(1, Math.floor((rect.width + CELL_GAP) / (CELL_PX + CELL_GAP)));
          const idx = row * cols + col;
          if (idx >= 0 && idx < stocks.length) {
            const stock = stocks[idx] as MapStock;
            if (onOpenSymbol) onOpenSymbol(stock.symbol);
          }
        }}
      />
      {tooltip}
    </div>
  );
}