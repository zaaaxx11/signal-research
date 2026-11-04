/**
 * `/charts`: five index cards, and one symbol in a full instrument when asked.
 *
 * The rework: the single-symbol view is a charting page, not one canvas —
 * the six named timeframes (15M, 4H, D, W, M, KALENDER) are tabs above it, the
 * box carries the product's automated colour rule (up green / down red, the
 * move's size drives the tint), and every card header and card element is a
 * HoverBubble target through `quoteProps`, so a ticker beside a chart costs
 * nothing (data exists) and answers (one attribute).
 */
import * as React from "react";

import { SectionTitle } from "@/components/SectionTitle";
import { quoteProps } from "@/lib/quoteAttrs";
import { notify } from "@/lib/notify";
import { formatChange, formatPrice } from "@/home/signals";
import { cn } from "@/lib/utils";

import { Chart } from "./Chart";
import { CHART_TIMEFRAMES, isChartTimeframe } from "./timeframes";
import type { ChartTimeframeId } from "./timeframes";
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

/** The automated tint rule, applied to box backgrounds: same curve as the bubble. */
export function tintAlpha(changePct: number | null | undefined): number {
  if (changePct == null || !Number.isFinite(changePct)) return 0.05;
  return Math.min(0.6, Math.max(0.05, Math.abs(changePct) / 4));
}

/** The automated tint rule, applied to box backgrounds: same curve as the bubble. */
export function boxTintStyle(percent: number | null): React.CSSProperties {
  const alpha = tintAlpha(percent);
  if (percent === null) return {};
  const key = percent >= 0 ? "var(--up)" : "var(--down)";
  return {
    background: `color-mix(in srgb, ${key} ${(alpha * 100).toFixed(0)}%, var(--surface))`,
  };
}

function ChartCard({ index }: { readonly index: ChartIndex }) {
  const [series, setSeries] = React.useState<ChartSeries | null>(null);
  const onData = React.useCallback((next: ChartSeries | null) => setSeries(next), []);
  const change = series !== null ? seriesChange(series) : null;
  const percent = change?.percent ?? null;

  return (
    <article
      data-testid={`chart-card-${index.symbol}`}
      aria-label={`${index.name} chart`}
      style={boxTintStyle(percent)}
      className="flex min-w-0 flex-col overflow-hidden rounded-md border border-line transition-[background-color] duration-300"
      {...quoteProps({
        symbol: index.symbol,
        name: index.name,
        price: change?.last ?? null,
        changePct: percent,
        kind: "stock",
      })}
    >
      <header
        className="flex min-w-0 items-baseline gap-1.5 border-b border-line px-2 py-1"
        style={boxTintStyle(percent)}
      >
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
          className={cn("nums shrink-0 font-mono text-label", toneClass(percent))}
        >
          {change !== null ? formatChange(percent) : "—"}
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
  const [timeframe, setTimeframe] = React.useState<ChartTimeframeId>("1d");
  const wasError = React.useRef(false);
  const inputId = React.useId();
  const [singleSeries, setSingleSeries] = React.useState<ChartSeries | null>(null);
  const [singleError, setSingleError] = React.useState<string | null>(null);

  const submit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const next = draft.trim().toUpperCase();
    if (next.length === 0) return;
    setSymbol(next);
    setDraft(next);
  };

  // The single instrument reports through onData/error props; a failed read
  // that stays failed is one toast, not one per render (parent's dedupe knob).
  React.useEffect(() => {
    if (symbol === null) {
      wasError.current = false;
      setSingleError(null);
      return;
    }
    if (singleError !== null && !wasError.current) {
      wasError.current = true;
      notify.error(`Chart ${symbol} failed`, singleError);
    }
    if (singleError === null) {
      wasError.current = false;
    }
  }, [symbol, singleError]);

  const singleChange = singleSeries !== null ? seriesChange(singleSeries) : null;
  const singlePercent = singleChange?.percent ?? null;

  return (
    <main
      data-testid="charts-page"
      data-view={symbol === null ? "gallery" : "single"}
      data-timeframe={timeframe}
      className="flex min-h-0 flex-1 flex-col overflow-hidden bg-bg"
    >
      <header className="shrink-0">
        <h1 className="sr-only">Charts</h1>
        <SectionTitle variant={5} testId="charts-page-title">
          Charts
        </SectionTitle>
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
            <div
              className="flex items-baseline gap-2"
              {...quoteProps({
                symbol,
                name: symbol,
                price: singleChange?.last ?? null,
                changePct: singlePercent,
                kind: "stock",
              })}
            >
              <h2 className="font-mono text-title font-semibold text-fg">{symbol}</h2>
              <p className="text-label text-fg-subtle">Price chart</p>
            </div>
            <div
              role="group"
              aria-label="Timeframe"
              data-testid="charts-timeframes"
              className="flex shrink-0 items-center gap-px"
            >
              {CHART_TIMEFRAMES.map((spec) => {
                const active = spec.id === timeframe;
                return (
                  <button
                    key={spec.id}
                    type="button"
                    data-testid={`charts-tf-${spec.id}`}
                    aria-pressed={active}
                    aria-label={spec.name}
                    onClick={() => setTimeframe(spec.id)}
                    className={cn(
                      "rounded-sm px-2 py-1 font-mono text-label uppercase tracking-[0.08em] transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-focus",
                      active
                        ? "bg-row-selected text-accent-text"
                        : "text-fg-subtle hover:bg-row-hover hover:text-fg",
                    )}
                  >
                    {spec.label}
                  </button>
                );
              })}
            </div>
            <article
              data-testid={`charts-single-card-${symbol}`}
              style={boxTintStyle(singlePercent)}
              className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-md border border-line transition-[background-color] duration-300"
            >
              <Chart
                symbol={symbol}
                height={400}
                timeframeId={isChartTimeframe(timeframe) ? timeframe : null}
                renderer="bars"
                showVolume
                onData={(next) => {
                  setSingleSeries(next);
                  setSingleError(next === null ? singleError : null);
                }}
                onError={(message) => setSingleError(message)}
                className="min-h-0 flex-1"
              />
            </article>
          </section>
        )}
      </div>
    </main>
  );
}
