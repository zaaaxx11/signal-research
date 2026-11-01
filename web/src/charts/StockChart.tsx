/**
 * The stock page's chart: the instrument, 280 px tall, and nothing else.
 *
 * No `<main>`, no page heading, no symbol strip — `StockPage` already owns the
 * address and the ticker. This component only reserves the height the detail
 * panel lays out and delegates every decision to `Chart`. Candles are the
 * default here because a stock page is read by people who expect them; the
 * gallery uses lines, which are legible at card size.
 */

import { cn } from "@/lib/utils";

import { Chart } from "./Chart";
import type { ChartRange } from "./types";

export interface StockChartProps {
  readonly symbol: string;
  readonly initialRange?: ChartRange;
  readonly height?: number;
  readonly className?: string;
}

/** 280 px: the height the detail panel reserves for the instrument. */
export const STOCK_CHART_HEIGHT = 280;

export function StockChart({
  symbol,
  initialRange = "1d",
  height = STOCK_CHART_HEIGHT,
  className,
}: StockChartProps) {
  return (
    <div
      data-testid="stock-chart"
      data-symbol={symbol}
      className={cn("w-full min-w-0", className)}
    >
      <Chart
        symbol={symbol}
        initialRange={initialRange}
        height={height}
        kind="candle"
        className="rounded-md border border-line"
      />
    </div>
  );
}
