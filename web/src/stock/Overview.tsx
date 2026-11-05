/**
 * Overview: the chart, the 84-cell snapshot, the company profile and the
 * technicals block.
 *
 * The order is the reading order: identity, price, the numbers that describe
 * the company, then the derived technicals. There are no preview lists here —
 * Peers, Insider and News each have their own tab that shows every row.
 *
 * The snapshot is drawn from `GET /engine/snapshot/{symbol}` — one call for all
 * 84 cells, with the endpoint's own unit per key. When that read fails the grid
 * falls back to the fundamentals document and the scan row and says why in one
 * line above itself.
 *
 * Below the company profile, the technicals block prints the mentor's derived
 * numbers (`/engine/technicals` and `/engine/risk`): the moving averages,
 * RSI, momentum, WTD and MTD — the reading that used to exist only in the API.
 */

import * as React from "react";

import { CompanyProfile } from "./CompanyProfile";
import { SnapshotGrid } from "./SnapshotGrid";
import type { SnapshotSources } from "./snapshot";
import { TechnicalsBlock } from "./TechnicalsBlock";
import { TickerChart } from "./TickerChart";

export interface OverviewProps {
  readonly symbol: string;
  readonly sources: SnapshotSources;
  /** One line above the snapshot when its own read failed, else null. */
  readonly notice?: string | null;
}

export function Overview({
  symbol,
  sources,
  notice = null,
}: OverviewProps): React.ReactElement {
  return (
    <div className="flex flex-col gap-4">
      <TickerChart symbol={symbol} />

      <section aria-labelledby="snapshot-heading">
        <div className="mb-1 flex items-baseline justify-between gap-2">
          <h2
            id="snapshot-heading"
            className="text-label font-medium uppercase tracking-[0.08em] text-fg-subtle"
          >
            Snapshot
          </h2>
          <span className="text-label text-fg-subtle">
            Fundamentals and the latest scan row
          </span>
        </div>
        <SnapshotGrid sources={sources} notice={notice} />
      </section>

      <CompanyProfile symbol={symbol} />

      <TechnicalsBlock symbol={symbol} />
    </div>
  );
}