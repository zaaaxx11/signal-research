/**
 * GridView: uniform grid sorted by market cap descending.
 * Each cell is a colored rectangle with ticker and change% inside.
 * Compact, tight cells — no spacing, just borders.
 */

import * as React from "react";

import { Link } from "@/shell/routes";

import { signedPercent } from "./format";
import { rampColor, readTheme } from "./theme";
import type { MapPayload, MapStock } from "./types";

export interface GridViewProps {
  readonly payload: MapPayload;
  readonly onOpenSymbol?: (symbol: string) => void;
  readonly className?: string;
  /** Grid columns — defaults to auto-fill. */
  readonly columns?: number;
}

function flattenSorted(payload: MapPayload): MapStock[] {
  return payload.sectors.flatMap((s) => [...s.stocks]).sort((a, b) => b.cap - a.cap);
}

export function GridView({ payload, onOpenSymbol, columns, className }: GridViewProps) {
  const stocks = React.useMemo(() => flattenSorted(payload), [payload]);
  const theme = React.useMemo(() => readTheme(), []);
  const [hovered, setHovered] = React.useState<string | null>(null);

  const gridStyle = columns
    ? { gridTemplateColumns: `repeat(${columns}, 1fr)` }
    : { gridTemplateColumns: "repeat(auto-fill, minmax(80px, 1fr))" };

  return (
    <div
      data-testid="grid-view"
      className={`${className ?? ""} overflow-auto`}
      style={{
        display: "grid",
        gap: 1,
        background: "var(--line)",
        ...gridStyle,
      }}
    >
      {stocks.map((stock) => {
        const sp = signedPercent(stock.change);
        const bg = rampColor(stock.change, theme);
        const tone =
          sp.sign === 1 ? "text-up" : sp.sign === -1 ? "text-down" : "text-flat";
        const hov = hovered === stock.symbol;

        return (
          <Link
            key={stock.symbol}
            to={`/stock/${encodeURIComponent(stock.symbol)}`}
            onClick={(e) => {
              if (onOpenSymbol) {
                e.preventDefault();
                onOpenSymbol(stock.symbol);
              }
            }}
            data-testid={`grid-cell-${stock.symbol}`}
            className="flex flex-col items-center justify-center px-1 py-2 text-center transition-opacity hover:opacity-80"
            style={{
              background: bg,
              minHeight: 52,
              outline: hov ? "2px solid var(--focus)" : undefined,
              outlineOffset: -2,
            }}
            onPointerEnter={() => setHovered(stock.symbol)}
            onPointerLeave={() => setHovered(null)}
          >
            <span className="font-mono text-[11px] font-semibold leading-tight text-white mix-blend-difference">
              {stock.symbol}
            </span>
            <span className={`nums mt-0.5 font-mono text-[10px] leading-tight ${tone} mix-blend-difference`}>
              {sp.text}
            </span>
          </Link>
        );
      })}
    </div>
  );
}