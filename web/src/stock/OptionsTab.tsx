import * as React from "react";

import { cn } from "@/lib/utils";
import {
  type OptionsChain,
  strikesForExpiry,
  formatGreek,
  formatImpliedVol,
  formatPremium,
} from "./options";

export interface OptionsTabProps {
  readonly symbol: string;
  readonly state: {
    readonly status: "loading" | "ready" | "empty" | "error";
    readonly data: OptionsChain | null;
    readonly error: string | null;
    readonly retry: () => void;
  };
}

export function OptionsTab({ symbol, state }: OptionsTabProps): React.ReactElement {
  const [selectedExpiry, setSelectedExpiry] = React.useState<string | null>(null);

  if (state.status === "loading") {
    return (
      <div
        data-testid="options-loading"
        aria-busy="true"
        aria-label={`Loading options for ${symbol}`}
        className="flex items-center justify-center py-12"
      >
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-brand border-t-transparent" />
      </div>
    );
  }

  if (state.status === "error") {
    return (
      <div
        data-testid="options-error"
        role="alert"
        className="rounded border border-alert bg-alert/10 px-4 py-3 text-alert"
      >
        <p className="text-sm">{state.error}</p>
        <button
          type="button"
          onClick={state.retry}
          className="mt-2 rounded bg-alert px-3 py-1.5 text-sm font-medium text-white hover:bg-alert/90"
        >
          Retry
        </button>
      </div>
    );
  }

  if (state.status === "empty" || state.data === null) {
    return (
      <div
        data-testid="options-empty"
        className="py-8 text-center text-fg-muted"
      >
        No options for this symbol.
      </div>
    );
  }

  const chain = state.data;
  const activeExpiry = selectedExpiry ?? chain.selectedExpiry;
  const strikes = strikesForExpiry(chain, activeExpiry);

  React.useEffect(() => {
    setSelectedExpiry(null);
  }, [chain.symbol, chain.asOf]);

  return (
    <div className="flex flex-col">
      <div data-testid="options-header" className="mb-2 text-sm text-fg-muted">
        Expiry: {activeExpiry} | As of: {chain.asOf}
      </div>
      <p data-testid="options-caption" className="mb-3 text-xs text-fg-muted">
        Delayed {chain.delayedMinutes} minutes
      </p>
      <div
        role="tablist"
        aria-label="Expiry dates"
        className="mb-4 flex flex-wrap gap-2"
      >
        {chain.expiryDates.map((expiry) => (
          <button
            key={expiry}
            type="button"
            
            aria-selected={expiry === activeExpiry}
            aria-pressed={expiry === activeExpiry}
            onClick={() => setSelectedExpiry(expiry)}
            className={cn(
              "rounded border px-3 py-1.5 text-sm transition-colors",
              expiry === activeExpiry
                ? "border-brand bg-brand/10 text-brand"
                : "border-line text-fg-muted hover:text-fg",
            )}
          >
            {expiry}
          </button>
        ))}
      </div>
      <div className="overflow-x-auto">
        <table
          data-testid="options-table"
          className="md:table-fixed min-w-full border-collapse text-sm"
        >
          <thead data-testid="options-thead" className="sticky top-0 bg-surface">
            <tr>
              <th className="border-b border-line px-2 py-2 text-left text-xs font-medium uppercase text-fg-muted">
                Strike
              </th>
              <th className="border-b border-line px-2 py-2 text-left text-xs font-medium uppercase text-fg-muted">
                Call Premium
              </th>
              <th className="border-b border-line px-2 py-2 text-left text-xs font-medium uppercase text-fg-muted">
                Call Delta
              </th>
              <th className="border-b border-line px-2 py-2 text-left text-xs font-medium uppercase text-fg-muted">
                Call Gamma
              </th>
              <th className="border-b border-line px-2 py-2 text-left text-xs font-medium uppercase text-fg-muted">
                Call Theta
              </th>
              <th className="border-b border-line px-2 py-2 text-left text-xs font-medium uppercase text-fg-muted">
                Call Vega
              </th>
              <th className="border-b border-line px-2 py-2 text-left text-xs font-medium uppercase text-fg-muted">
                Put Premium
              </th>
              <th className="border-b border-line px-2 py-2 text-left text-xs font-medium uppercase text-fg-muted">
                Put Delta
              </th>
              <th className="border-b border-line px-2 py-2 text-left text-xs font-medium uppercase text-fg-muted">
                Put Gamma
              </th>
              <th className="border-b border-line px-2 py-2 text-left text-xs font-medium uppercase text-fg-muted">
                Put Theta
              </th>
              <th className="border-b border-line px-2 py-2 text-left text-xs font-medium uppercase text-fg-muted">
                Put Vega
              </th>
              <th className="border-b border-line px-2 py-2 text-left text-xs font-medium uppercase text-fg-muted">
                Volume
              </th>
              <th className="border-b border-line px-2 py-2 text-left text-xs font-medium uppercase text-fg-muted">
                Open Interest
              </th>
              <th className="border-b border-line px-2 py-2 text-left text-xs font-medium uppercase text-fg-muted">
                IV
              </th>
            </tr>
          </thead>
          <tbody>
            {strikes.map((row) => (
              <tr key={row.strike} data-strike-row={row.strike}>
                <td className="border-b border-line px-2 py-2 font-medium">{row.strike}</td>
                <td className="border-b border-line px-2 py-2" data-testid="call-premium">
                  {formatPremium(row.call.premium)}
                </td>
                <td className="border-b border-line px-2 py-2" data-testid="call-delta">
                  {formatGreek(row.call.delta)}
                </td>
                <td className="border-b border-line px-2 py-2" data-testid="call-gamma">
                  {formatGreek(row.call.gamma)}
                </td>
                <td className="border-b border-line px-2 py-2" data-testid="call-theta">
                  {formatGreek(row.call.theta)}
                </td>
                <td className="border-b border-line px-2 py-2" data-testid="call-vega">
                  {formatGreek(row.call.vega)}
                </td>
                <td className="border-b border-line px-2 py-2" data-testid="put-premium">
                  {formatPremium(row.put.premium)}
                </td>
                <td className="border-b border-line px-2 py-2" data-testid="put-delta">
                  {formatGreek(row.put.delta)}
                </td>
                <td className="border-b border-line px-2 py-2" data-testid="put-gamma">
                  {formatGreek(row.put.gamma)}
                </td>
                <td className="border-b border-line px-2 py-2" data-testid="put-theta">
                  {formatGreek(row.put.theta)}
                </td>
                <td className="border-b border-line px-2 py-2" data-testid="put-vega">
                  {formatGreek(row.put.vega)}
                </td>
                <td className="border-b border-line px-2 py-2">
                  {row.call.volume?.toLocaleString() ?? "—"}
                </td>
                <td className="border-b border-line px-2 py-2">
                  {row.call.openInterest?.toLocaleString() ?? "—"}
                </td>
                <td className="border-b border-line px-2 py-2" data-testid="implied-vol">
                  {formatImpliedVol(row.call.impliedVolatility)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
