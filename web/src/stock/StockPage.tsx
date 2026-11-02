/**
 * `/stock/{symbol}`: a thin door onto the ticker page.
 *
 * The page itself is a lazy chunk. It owns the chart renderer, the statement
 * tables and seven reads; Home should not pay for any of that to draw a table
 * of tickers. This module only keeps the route's contract — the test id, the
 * symbol, one boundary that names a failed download and retries once — and
 * leaves every product decision to `TickerPage`.
 *
 * `onClose` stays in the props for the shell's call site, but a page with an
 * address does not close: the header's "← Signals" link is the way back, and
 * it is a real href a reader can middle-click.
 */

import * as React from "react";

import { ErrorBoundary } from "@/grid/ErrorBoundary";

export interface StockPageProps {
  readonly symbol: string;
  /** Kept for the route's call site; the page navigates with a link. */
  readonly onClose?: () => void;
}

const loadTickerPage = () => import("./TickerPage");

export function StockPage({ symbol }: StockPageProps): React.ReactElement {
  const [attempt, setAttempt] = React.useState(0);
  const Page = React.useMemo(() => React.lazy(loadTickerPage), [attempt]);

  return (
    <main
      data-testid="stock-page"
      data-symbol={symbol}
      className="flex min-h-0 flex-1 flex-col overflow-hidden"
    >
      <ErrorBoundary
        resetKey={`${symbol}:${attempt}`}
        label={`Ticker page for ${symbol}`}
        hint="Its code could not be downloaded — a deploy may have replaced this chunk. Retry, or go back to the signals."
        onRetry={() => setAttempt((value) => value + 1)}
        className="flex min-h-0 flex-1"
      >
        <React.Suspense
          fallback={
            <div
              aria-busy="true"
              aria-label={`Loading the ticker page for ${symbol}`}
              className="min-h-0 flex-1 bg-surface"
            />
          }
        >
          <Page key={symbol} symbol={symbol} />
        </React.Suspense>
      </ErrorBoundary>
    </main>
  );
}
