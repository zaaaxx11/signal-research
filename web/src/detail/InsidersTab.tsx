/**
 * Insiders: Form 4 transactions as a compact table.
 *
 * The empty answer is the common one — a foreign private issuer files no Form 4
 * at all — so it is a sentence, not an error: "There are no Form 4 filings".
 * A trade whose price the filing did not report shows `—` in the value column;
 * shares × missing is not zero dollars.
 */

import * as React from "react";

import { formatDate, formatShares, formatTradeMoney, insiderType, tradeValue } from "./format";
import { TabEmpty, TabError, TabSkeleton } from "./TabStates";

import type { InsiderData, Resource } from "./useDetail";

export interface InsidersTabProps {
  symbol: string;
  state: Resource<InsiderData>;
  onRetry: () => void;
}

export function InsidersTab({
  symbol,
  state,
  onRetry,
}: InsidersTabProps): React.ReactElement {
  if (state.status === "loading") {
    return <TabSkeleton label={`insider filings for ${symbol}`} testId="insiders-loading" rows={9} />;
  }

  if (state.status === "error") {
    return (
      <TabError
        label="insider filings"
        testId="insiders-error"
        message={state.error}
        onRetry={onRetry}
      />
    );
  }

  if (state.status === "empty" || !state.data) {
    return (
      <TabEmpty testId="insiders-empty">
        There are no Form 4 filings for {symbol}.
      </TabEmpty>
    );
  }

  return (
    <div data-testid="insiders-loaded" className="flex flex-col gap-2">
      <p className="nums text-label uppercase tracking-[0.08em] text-fg-subtle">
        {state.data.items.length} transactions · as of {formatDate(state.data.asOf)}
      </p>

      <table className="w-full table-fixed border-collapse text-data">
        <caption className="sr-only">
          Form 4 insider transactions for {symbol}
        </caption>
        <colgroup>
          <col style={{ width: "62px" }} />
          <col />
          <col style={{ width: "46px" }} />
          <col style={{ width: "52px" }} />
          <col style={{ width: "58px" }} />
        </colgroup>
        <thead>
          <tr className="border-b border-line text-label uppercase tracking-[0.06em] text-fg-subtle">
            <th scope="col" className="py-1 pr-1 text-left font-medium">
              Date
            </th>
            <th scope="col" className="py-1 pr-1 text-left font-medium">
              Insider
            </th>
            <th scope="col" className="py-1 pr-1 text-left font-medium">
              Type
            </th>
            <th scope="col" className="py-1 pr-1 text-right font-medium">
              Shares
            </th>
            <th scope="col" className="py-1 text-right font-medium">
              Value
            </th>
          </tr>
        </thead>
        <tbody>
          {state.data.items.map((item, index) => {
            const type = insiderType(item.code);
            const value = tradeValue(item.shares, item.price);
            return (
              <tr
                key={`${item.accession}-${item.transactionDate}-${index}`}
                data-testid="insider-row"
                className="border-b border-line last:border-b-0"
              >
                <th scope="row" className="nums py-1 pr-1 text-left font-normal text-fg-muted">
                  <time dateTime={item.transactionDate || undefined}>
                    {formatDate(item.transactionDate)}
                  </time>
                </th>
                <td className="truncate py-1 pr-1 text-fg" title={item.owner || undefined}>
                  {item.owner || "—"}
                </td>
                <td
                  className="whitespace-nowrap py-1 pr-1 text-fg-muted"
                  title={`${item.code || "—"} · ${type.long}${
                    item.acquiredDisposed === "D"
                      ? " · disposed"
                      : item.acquiredDisposed === "A"
                        ? " · acquired"
                        : ""
                  }`}
                >
                  {type.short}
                </td>
                <td className="nums py-1 pr-1 text-right text-fg-muted">
                  {formatShares(item.shares)}
                </td>
                <td className="nums py-1 text-right text-fg">
                  {formatTradeMoney(value)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
