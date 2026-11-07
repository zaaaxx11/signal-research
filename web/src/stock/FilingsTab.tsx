/**
 * The Filings tab: what the product can actually cite.
 *
 * The engine reads Form 4 filings for the insider surface; it does not serve a
 * general filing index. So this panel shows the Form 4 rows it has, each with
 * its own EDGAR link, and says plainly that other form types are not wired —
 * rather than borrowing a peer page's 10-K list or inventing a pagination the
 * API cannot answer.
 */

import * as React from "react";

import { formatDate, safeHref } from "@/detail/format";
import type { InsiderData, Resource } from "@/detail/useDetail";

export interface FilingsTabProps {
  readonly symbol: string;
  readonly state: Resource<InsiderData>;
}

export function FilingsTab({ symbol, state }: FilingsTabProps): React.ReactElement {
  if (state.status === "loading") {
    return (
      <p aria-busy="true" data-testid="filings-loading" className="text-data text-fg-subtle">
        Loading filings for {symbol}…
      </p>
    );
  }
  if (state.status === "error") {
    return (
      <div role="alert" data-testid="filings-error" className="text-data text-fg-muted">
        <p>Could not load filings.</p>
        {state.error ? <p className="mt-1 text-fg-subtle">{state.error}</p> : null}
      </div>
    );
  }

  const items = state.data?.items.slice(0, 20) ?? [];
  if (items.length === 0) {
    return (
      <p data-testid="filings-empty" className="text-data text-fg-muted">
        No Form 4 filings for {symbol}.
      </p>
    );
  }

  return (
    <div data-testid="filings" className="overflow-x-auto">
      <table className="w-full border-collapse text-data">
        <caption className="sr-only">
          Form 4 filings for {symbol}, newest first. The link opens the filing
          itself. Other form types are not served by the API yet.
        </caption>
        <thead>
          <tr className="border-b border-line">
            <th scope="col" className="py-1 pr-3 text-left text-label font-medium uppercase tracking-[0.06em] text-fg-subtle">
              Filed
            </th>
            <th scope="col" className="py-1 pr-3 text-left text-label font-medium uppercase tracking-[0.06em] text-fg-subtle">
              Form
            </th>
            <th scope="col" className="py-1 pr-3 text-left text-label font-medium uppercase tracking-[0.06em] text-fg-subtle">
              Insider
            </th>
            <th scope="col" className="py-1 pr-3 text-left text-label font-medium uppercase tracking-[0.06em] text-fg-subtle">
              Transaction
            </th>
            <th scope="col" className="py-1 text-right text-label font-medium uppercase tracking-[0.06em] text-fg-subtle">
              Filing
            </th>
          </tr>
        </thead>
        <tbody>
          {items.map((item, index) => {
            const href = safeHref(item.url);
            return (
              <tr key={`${item.accession}-${index}`} className="h-5 border-b border-line/70 last:border-b-0">
                <td className="nums whitespace-nowrap pr-3 font-mono text-fg-muted">
                  {formatDate(item.filed || item.transactionDate)}
                </td>
                <td className="pr-3 text-fg">4</td>
                <td className="max-w-[220px] truncate pr-3 text-fg" title={item.owner}>
                  {item.owner || "—"}
                </td>
                <td className="nums whitespace-nowrap pr-3 font-mono text-fg-muted">
                  {item.code || "—"}
                </td>
                <td className="whitespace-nowrap text-right">
                  {href ? (
                    <a
                      href={href}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-accent-text hover:text-brand"
                    >
                      Open
                    </a>
                  ) : (
                    <span className="text-fg-subtle">—</span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="mt-2 text-label text-fg-subtle">
        Form 4 filings only. Other form types are not served by the API yet.
      </p>
    </div>
  );
}
