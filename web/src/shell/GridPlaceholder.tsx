import { LayersIcon } from "lucide-react";

import { cn } from "@/lib/utils";

import type { ViewId } from "./ViewTabs";

/**
 * Column budgets, in the shape Phase 2's solver will consume: a weight per
 * column, `fr` units so the header's widths sum to the container by
 * construction (plan §3, R3). The placeholder renders the real header of the
 * active view rather than a grey box, so the shell's geometry is the grid's
 * geometry — nothing to redo when the rows arrive.
 */
const HEADERS: Record<ViewId, string[]> = {
  today: ["Ticker", "Last", "Chg", "Chg %", "Volume", "Avg Vol", "Rel Vol", "Gap", "Open", "High", "Low"],
  value: ["Ticker", "Mkt Cap", "P/E", "Fwd P/E", "PEG", "P/S", "P/B", "EV/EBITDA", "Target", "Rec"],
  quality: ["Ticker", "ROE", "ROA", "ROIC", "Curr Ratio", "Quick", "D/E", "Gross M", "Op M", "Net M"],
  growth: ["Ticker", "EPS", "EPS next Y", "EPS this Y", "EPS past 5Y", "Sales", "Sales Q/Q", "EPS Q/Q"],
  momentum: ["Ticker", "Perf W", "Perf M", "Perf Q", "Perf H", "Perf Y", "YTD", "Perf 3Y", "RSI"],
  trend: ["Ticker", "SMA20", "SMA50", "SMA200", "52W High", "52W Low", "ATR", "Beta", "Vol W"],
  ownership: ["Ticker", "Shares Out", "Float", "Float %", "Insider %", "Inst %", "Short Float", "Short Ratio"],
  income: ["Ticker", "Dividend", "Yield", "Payout", "Ex-Date", "Div 1Y", "Div 3Y", "Div 5Y"],
  profile: ["Ticker", "Company", "Sector", "Industry", "Country", "Exchange", "Employees", "IPO"],
};

const WEIGHTS: Record<ViewId, string[]> = {
  today: ["1.5fr", "0.9fr", "0.8fr", "0.9fr", "1fr", "1.1fr", "0.9fr", "0.8fr", "0.9fr", "0.9fr", "0.9fr"],
  value: ["1.4fr", "1.2fr", "0.9fr", "0.9fr", "0.8fr", "0.8fr", "0.8fr", "1.1fr", "1fr", "0.9fr"],
  quality: ["1.4fr", "0.9fr", "0.9fr", "0.9fr", "1.1fr", "0.9fr", "0.8fr", "0.9fr", "0.9fr", "0.9fr"],
  growth: ["1.4fr", "0.9fr", "1.1fr", "1.1fr", "1.2fr", "1.1fr", "1fr", "1fr"],
  momentum: ["1.4fr", "0.9fr", "0.9fr", "0.9fr", "0.9fr", "0.9fr", "0.9fr", "0.9fr", "0.8fr"],
  trend: ["1.4fr", "0.9fr", "0.9fr", "0.9fr", "0.9fr", "0.9fr", "0.8fr", "0.8fr", "0.9fr"],
  ownership: ["1.4fr", "1.1fr", "1fr", "0.9fr", "0.9fr", "0.9fr", "1fr", "1fr"],
  income: ["1.4fr", "0.9fr", "0.9fr", "0.9fr", "1fr", "0.9fr", "0.9fr", "0.9fr"],
  profile: ["1.4fr", "2.4fr", "1.3fr", "1.5fr", "1fr", "1fr", "1fr", "1fr"],
};

/**
 * Ghost cell widths, by column index. Varied on purpose: an identical bar in
 * every cell reads as a loading state, while a ticker-shaped wide bar and
 * narrower numeric bars read as a table that is about to fill.
 */
const SKELETON_WIDTHS = ["84%", "56%", "42%", "50%", "68%", "62%", "48%", "36%", "54%", "54%", "58%"];

export interface GridPlaceholderProps {
  view: ViewId;
  viewLabel: string;
  symbol: string | null;
}

/**
 * The content area the grid will occupy. Ten ghost rows, the active view's real
 * column headers, and a note that states what is missing instead of faking
 * numbers — an empty state that draws plausible prices is a lie with a
 * skeleton on it.
 */
export function GridPlaceholder({ view, viewLabel, symbol }: GridPlaceholderProps) {
  const headers = HEADERS[view];
  const weights = WEIGHTS[view];
  const template = weights.join(" ");

  return (
    <section
      aria-label={`${viewLabel} grid`}
      className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-md border border-line bg-surface"
    >
      <div className="flex h-9 shrink-0 items-center justify-between border-b border-line-strong px-3">
        <div className="flex items-baseline gap-2">
          <h2 className="text-ui font-medium text-fg">{viewLabel}</h2>
          <span className="font-mono text-label uppercase tracking-[0.08em] text-fg-subtle">
            {headers.length} columns · 100 rows
          </span>
        </div>
        <span className="font-mono text-label uppercase tracking-[0.08em] text-fg-subtle">
          snapshot · as of last close
        </span>
      </div>

      <div className="relative min-h-0 flex-1 overflow-hidden">
        <div aria-hidden="true" className="opacity-50">
          <div
            className="grid h-7 items-center border-b border-line-strong bg-surface-raised px-3 font-mono text-label uppercase tracking-[0.08em] text-fg-muted"
            style={{ gridTemplateColumns: template }}
          >
            {headers.map((label) => (
              <span key={label} className="truncate pr-2">
                {label}
              </span>
            ))}
          </div>

          {Array.from({ length: 12 }, (_, row) => (
            <div
              key={row}
              className={cn(
                "grid h-[28px] items-center px-3",
                row % 2 === 1 ? "bg-row-alt" : "bg-row",
              )}
              style={{ gridTemplateColumns: template }}
            >
              {headers.map((label, column) => (
                <span key={label} className="pr-3">
                  <span
                    className={cn(
                      "block h-[6px] rounded-[1px]",
                      column === 0 ? "bg-line-strong" : "bg-line",
                    )}
                    style={{
                      // The last cell's bar runs to the column's edge: a short
                      // bar at the container's right side reads as a table that
                      // failed to fill its panel.
                      width:
                        column === headers.length - 1
                          ? "92%"
                          : (SKELETON_WIDTHS[column] ?? "40%"),
                    }}
                  />
                </span>
              ))}
            </div>
          ))}
        </div>

        {/* A note, not a modal: it annotates the empty grid and offers nothing
            to dismiss, so it has no shadow, no backdrop and no close control. */}
        <div className="absolute inset-x-0 bottom-0 top-7 flex items-center justify-center px-6">
          <div className="flex w-full max-w-[460px] items-start gap-3 rounded-md border border-line border-l-2 border-l-brand bg-surface-raised px-4 py-3">
            <LayersIcon
              aria-hidden="true"
              className="mt-0.5 size-4 shrink-0 text-brand"
            />
            <div className="min-w-0">
              <p className="text-ui font-medium text-fg">Grid arrives in Phase 2</p>
              <p className="mt-1 text-data leading-relaxed text-fg-muted">
                {headers.length} columns × 100 rows, widths solved to this
                container — no horizontal scroll.
                {symbol ? (
                  <>
                    {" "}
                    <span className="font-mono text-brand">{symbol}</span> stays
                    pinned while the rest moves.
                  </>
                ) : null}
              </p>
              <div className="mt-2.5 flex flex-wrap items-center gap-1.5 font-mono text-label uppercase tracking-[0.08em] text-fg-subtle">
                <span className="rounded-sm border border-line px-1.5 py-0.5">
                  limit 100
                </span>
                <span className="rounded-sm border border-line px-1.5 py-0.5">
                  ≤ 60 KB
                </span>
                <span className="rounded-sm border border-line px-1.5 py-0.5">
                  ETag 304
                </span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
