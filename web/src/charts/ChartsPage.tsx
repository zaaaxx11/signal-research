/**
 * `/charts`: the major indices as a gallery, and one symbol when the reader
 * asks for one.
 *
 * The nav promises "Charts", so the page opens with the five instruments a
 * market reader checks first — S&P 500, Nasdaq 100, Dow, Russell 2000 and VIX —
 * each in a compact card with its 1-day bar chart and the change against the
 * prior close. The field at the top swaps the gallery for a single full-sized
 * chart; clearing it restores the gallery. That is the whole navigation model:
 * no dropdown, no second route, no modal.
 *
 * Each card owns its own read (`useChartData` through `Chart`) rather than
 * sharing one payload, because the five are five independent symbols and one
 * failing (VIX is the usual one) must not blank the other four. A card header
 * reads the series through `onData`, so the picture and the number beside it
 * are always the same payload.
 */

import * as React from "react";

import { formatChange, formatPrice } from "@/home/signals";
import { cn } from "@/lib/utils";

import { Chart } from "./Chart";
import { seriesChange, type ChartSeries } from "./types";

export interface ChartIndex {
  readonly symbol: string;
  readonly name: string;
}

/** The gallery, in the order the page states it. */
export const CHART_INDICES: readonly ChartIndex[] = [
  { symbol: "^GSPC", name: "S&P 500" },
  { symbol: "^NDX", name: "Nasdaq 100" },
  { symbol: "^DJI", name: "Dow" },
  { symbol: "^RUT", name: "Russell 2000" },
  { symbol: "^VIX", name: "VIX" },
];

export interface ChartsPageProps {
  /** The symbol the page opens on, for a deep link; the field owns it after. */
  readonly initialSymbol?: string;
}

function toneClass(percent: number | null): string {
  if (percent === null || percent === 0) return "text-flat";
  return percent > 0 ? "text-up" : "text-down";
}

function ChartCard({ index }: { readonly index: ChartIndex }) {
  const [series, setSeries] = React.useState<ChartSeries | null>(null);
  const onData = React.useCallback((next: ChartSeries | null) => setSeries(next), []);
  const change = series !== null ? seriesChange(series) : null;

  return (
    <article
      data-testid={`chart-card-${index.symbol}`}
      aria-label={`${index.name} chart`}
      className="flex min-w-0 flex-col overflow-hidden rounded-md border border-line bg-surface"
    >
      <header className="flex min-w-0 items-baseline gap-1.5 border-b border-line px-2 py-1">
        <h3
          data-testid={`chart-card-name-${index.symbol}`}
          className="min-w-0 truncate text-data font-semibold text-fg"
        >
          {index.name}
        </h3>
        <span className="shrink-0 font-mono text-label text-fg-subtle">{index.symbol}</span>
        <span
          data-testid={`chart-card-last-${index.symbol}`}
          className="nums ml-auto shrink-0 font-mono text-data text-fg"
        >
          {change?.last !== null && change?.last !== undefined ? formatPrice(change.last) : "—"}
        </span>
        <span
          data-testid={`chart-card-change-${index.symbol}`}
          className={cn("nums shrink-0 font-mono text-label", toneClass(change?.percent ?? null))}
        >
          {change !== null ? formatChange(change.percent) : "—"}
        </span>
      </header>

      <Chart symbol={index.symbol} height={180} compact renderer="bars" onData={onData} />
    </article>
  );
}

export function ChartsPage({ initialSymbol }: ChartsPageProps) {
  const [symbol, setSymbol] = React.useState<string | null>(
    initialSymbol !== undefined && initialSymbol.trim().length > 0
      ? initialSymbol.trim().toUpperCase()
      : null,
  );
  const [draft, setDraft] = React.useState(symbol ?? "");
  const inputId = React.useId();

  const submit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const next = draft.trim().toUpperCase();
    if (next.length === 0) return;
    setSymbol(next);
    setDraft(next);
  };

  return (
    <main
      data-testid="charts-page"
      data-view={symbol === null ? "gallery" : "single"}
      className="flex min-h-0 flex-1 flex-col overflow-hidden bg-bg"
    >
      <header className="flex h-8 shrink-0 items-center gap-2 border-b border-line bg-surface px-2">
        <h1 className="shrink-0 text-ui font-semibold text-fg">Charts</h1>
        <p className="min-w-0 truncate text-data text-fg-muted">
          Index charts, and one symbol at a time
        </p>
      </header>

      <div className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto p-2">
        <form
          onSubmit={submit}
          data-testid="charts-symbol-form"
          className="flex shrink-0 flex-wrap items-center gap-2"
        >
          <label
            htmlFor={inputId}
            className="text-label uppercase tracking-[0.1em] text-fg-subtle"
          >
            Symbol
          </label>
          <input
            id={inputId}
            data-testid="charts-symbol"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder="AAPL or ^GSPC"
            autoComplete="off"
            spellCheck={false}
            className="h-6 w-44 rounded-sm border border-line-control bg-surface-sunken px-1.5 font-mono text-data uppercase text-fg placeholder:normal-case placeholder:text-fg-disabled focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-focus"
          />
          <button
            type="submit"
            className="h-6 rounded-sm border border-line-control px-2 text-label uppercase tracking-[0.08em] text-fg transition-colors hover:bg-row-hover focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-focus"
          >
            Chart
          </button>
          {symbol !== null ? (
            <button
              type="button"
              data-testid="charts-back"
              onClick={() => {
                setSymbol(null);
                setDraft("");
              }}
              className="h-6 rounded-sm px-1.5 text-label text-accent-text transition-colors hover:text-fg focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-focus"
            >
              Back to indices
            </button>
          ) : null}
        </form>

        {symbol === null ? (
          <section data-testid="charts-gallery" aria-label="Major indices" className="flex flex-col gap-1.5">
            <div className="flex items-baseline gap-2">
              <h2 className="text-label uppercase tracking-[0.1em] text-fg-subtle">Major indices</h2>
              <p className="text-label text-fg-subtle">1-day · hover for OHLCV</p>
            </div>
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-3">
              {CHART_INDICES.map((index) => (
                <ChartCard key={index.symbol} index={index} />
              ))}
            </div>
          </section>
        ) : (
          <section
            data-testid="charts-single"
            aria-label={`Chart for ${symbol}`}
            className="flex min-h-0 flex-1 flex-col gap-1.5"
          >
            <div className="flex items-baseline gap-2">
              <h2 className="font-mono text-title font-semibold text-fg">{symbol}</h2>
              <p className="text-label text-fg-subtle">Price chart</p>
            </div>
            <Chart
              symbol={symbol}
              height={360}
              renderer="bars"
              showVolume
              className="rounded-md border border-line"
            />
          </section>
        )}
      </div>
    </main>
  );
}
