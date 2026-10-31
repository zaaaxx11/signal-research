/**
 * `/stock/{symbol}`: the detail panel as a page.
 *
 * The panel already exists — four tabs, its own reads, its own keyboard
 * contract — and this route is the smallest thing that can stand behind a
 * ticker link: the shell's nav, a way back, and the panel filling the content
 * column (`variant="page"`).
 *
 * The chunk is lazy for the same reason it is lazy in the dock: Home should not
 * pay for the detail surface's bundle to draw a table of tickers. A chunk that
 * cannot be downloaded costs the page's panel, not the route — the boundary
 * names it and retries once.
 */

import * as React from "react";

import { ErrorBoundary } from "@/grid/ErrorBoundary";
import { loadDetailPanel } from "@/detail/load";
import { Link } from "@/shell/routes";

export interface StockPageProps {
  symbol: string;
  /** Where the close button and Escape go; the route owns the address. */
  onClose: () => void;
}

export function StockPage({ symbol, onClose }: StockPageProps) {
  // Bumped by a failed load's "Try again": a new lazy type re-runs the import.
  const [attempt, setAttempt] = React.useState(0);
  const Panel = React.useMemo(() => React.lazy(loadDetailPanel), [attempt]);

  return (
    <main
      data-testid="stock-page"
      data-symbol={symbol}
      className="flex min-h-0 flex-1 flex-col overflow-hidden"
    >
      <div className="flex h-8 shrink-0 items-center gap-3 border-b border-line bg-surface px-3">
        <Link to="/" className="text-data text-accent-text hover:text-fg">
          ← Signals
        </Link>
        <span className="font-mono text-data font-semibold text-fg">{symbol}</span>
      </div>

      <div className="flex min-h-0 flex-1 flex-col">
        <ErrorBoundary
          resetKey={`${symbol}:${attempt}`}
          label={`Details for ${symbol}`}
          hint="Its code could not be downloaded — a deploy may have replaced this chunk. The rest of the page is still live."
          onRetry={() => setAttempt((value) => value + 1)}
          className="flex min-h-0 flex-1"
        >
          <React.Suspense
            fallback={
              <div
                aria-busy="true"
                aria-label={`Loading details for ${symbol}`}
                className="min-h-0 flex-1 bg-surface"
              />
            }
          >
            <Panel symbol={symbol} onClose={onClose} variant="page" />
          </React.Suspense>
        </ErrorBoundary>
      </div>
    </main>
  );
}
