/**
 * The ticker's price chart: one daily series, three bar sizes.
 *
 * The read is `GET /market/chart/{symbol}?range=1y` — daily bars, one request.
 * The D/W/M chips re-bucket that series in the browser (`aggregate.ts`); they
 * do not refetch, because the endpoint has no weekly or monthly interval and
 * three more requests would buy nothing. Daily is the default.
 *
 * `ChartBars` is reached through its lazy door: the renderer is ~55 KB gzip
 * and a reader who came for the statements should not download it. The chart's
 * own states are four, not two — loading, a 404 with a sentence, an empty
 * series, and bars — because axes drawn around nothing claim the symbol traded
 * and merely had no prints.
 */

import * as React from "react";

import { toBars } from "@/charts/bars";
import { ChartBarsLazy } from "@/charts/lazy";
import { useChartData } from "@/charts/useChartData";
import type { ChartRange } from "@/charts/types";
import { RANGE_LABELS, RANGE_NAMES } from "@/charts/types";
import { cn } from "@/lib/utils";

import { aggregateBars, INTERVALS, type BarInterval } from "./aggregate";

/** The chart ranges shown in the ticker range selector. */
const TICKER_RANGES: readonly ChartRange[] = ["1y", "5y", "max"];

export interface TickerChartProps {
  readonly symbol: string;
  readonly height?: number;
  readonly className?: string;
}

export function TickerChart({
  symbol,
  height = 240,
  className,
}: TickerChartProps): React.ReactElement {
  const [range, setRange] = React.useState<ChartRange>("1y");
  const [interval, setInterval] = React.useState<BarInterval>("D");
  const view = useChartData(symbol, range);

  const daily = React.useMemo(
    () => (view.series ? toBars(view.series) : []),
    [view.series],
  );
  const bars = React.useMemo(() => aggregateBars(daily, interval), [daily, interval]);
  const name = INTERVALS.find((entry) => entry.id === interval)?.name ?? "Daily";
  const label = `${symbol} chart, ${name.toLowerCase()} bars, ${bars.length} ${
    bars.length === 1 ? "bar" : "bars"
  }`;

  return (
    <section
      data-testid="ticker-chart"
      data-interval={interval}
      data-bars={bars.length}
      aria-labelledby="ticker-chart-heading"
      className={cn("min-w-0", className)}
    >
      <div className="mb-1 flex items-center justify-between gap-2">
        <h2
          id="ticker-chart-heading"
          className="text-label font-medium uppercase tracking-[0.08em] text-fg-subtle"
        >
          Price · {RANGE_NAMES[range]}
        </h2>
        <div className="flex items-center gap-2">
          <div role="group" aria-label="Chart range" className="flex items-center gap-0.5">
            {TICKER_RANGES.map((option) => {
              const active = option === range;
              return (
                <button
                  key={option}
                  type="button"
                  aria-pressed={active}
                  aria-label={RANGE_NAMES[option]}
                  title={RANGE_NAMES[option]}
                  data-testid={`chart-range-${option}`}
                  onClick={() => setRange(option)}
                  className={cn(
                    "focus-ring h-6 rounded-sm px-2 text-data font-medium transition-colors",
                    active
                      ? "border-line-control bg-surface-raised text-fg"
                      : "border-line text-fg-muted hover:text-fg",
                  )}
                >
                  {RANGE_LABELS[option]}
                </button>
              );
            })}
          </div>
          <div role="group" aria-label="Bar interval" className="flex items-center gap-0.5">
          {INTERVALS.map((option) => {
            const active = option.id === interval;
            return (
              <button
                key={option.id}
                type="button"
                aria-pressed={active}
                aria-label={option.name}
                title={option.name}
                data-testid={`chart-interval-${option.id}`}
                onClick={() => setInterval(option.id)}
                className={cn(
                  "focus-ring h-6 w-7 rounded-sm border text-data font-medium transition-colors",
                  active
                    ? "border-line-control bg-surface-raised text-fg"
                    : "border-line text-fg-muted hover:text-fg",
                )}
              >
                {option.label}
              </button>
            );
          })}
        </div>
        </div>
      </div>

      {view.status === "loading" ? (
        <div
          aria-busy="true"
          aria-label={`Loading the chart for ${symbol}`}
          style={{ height }}
          className="w-full border border-line bg-surface-sunken"
        />
      ) : view.status === "error" ? (
        <div
          role="status"
          style={{ minHeight: height }}
          className="flex flex-col items-start justify-center gap-2 border border-line bg-surface-sunken px-3 py-2"
        >
          <p className="text-data text-fg-muted">{view.error ?? `No chart for ${symbol}.`}</p>
          <button
            type="button"
            onClick={view.reload}
            className="focus-ring h-6 rounded-sm border border-line-control px-2 text-data text-fg-muted hover:text-fg"
          >
            Retry
          </button>
        </div>
      ) : bars.length === 0 ? (
        <p
          style={{ minHeight: height }}
          className="flex items-center border border-line bg-surface-sunken px-3 text-data text-fg-muted"
        >
          No bars were reported for {symbol} in this period.
        </p>
      ) : (
        <ChartBarsLazy
          bars={bars}
          height={height}
          label={label}
          showVolume
          className="rounded-sm border border-line"
        />
      )}
    </section>
  );
}
