/**
 * Overview: the band, the chart, the 84-cell snapshot, and the four lists.
 *
 * The order is the reference surface's because it is also the reading order:
 * identity, price, the numbers that describe the company, who is like it, who
 * holds it, what insiders did, what the wire said. Every list is bounded — ten
 * peers, three funds, ten insider rows, ten headlines — and every panel says
 * the truth when it has nothing: an empty sentence, never an empty frame.
 *
 * The snapshot is drawn from `GET /engine/snapshot/{symbol}` — one call for all
 * 84 cells, with the endpoint's own unit per key. When that read fails the grid
 * falls back to the fundamentals document and the scan row and says why in one
 * line above itself.
 */

import * as React from "react";

import type { InsiderData, NewsData, Resource } from "@/detail/useDetail";
import {
  formatDate,
  formatShares,
  formatTradeMoney,
  formatRelativeTime,
  insiderType,
  safeHref,
  tradeValue,
} from "@/detail/format";
import { Link } from "@/shell/routes";

import type { PeersData } from "./useTicker";
import { CompanyProfile } from "./CompanyProfile";
import { SnapshotGrid } from "./SnapshotGrid";
import type { SnapshotSources } from "./snapshot";
import { TickerChart } from "./TickerChart";
import { tabHref } from "./TickerTabs";

/** The three funds whose constituent files this product actually collects. */
export const HELD_BY_FUNDS = [
  { symbol: "SPY", name: "SPDR S&P 500 ETF Trust" },
  { symbol: "DIA", name: "SPDR Dow Jones Industrial Average ETF Trust" },
  { symbol: "IWM", name: "iShares Russell 2000 ETF" },
] as const;

export interface OverviewProps {
  readonly symbol: string;
  readonly sources: SnapshotSources;
  /** One line above the snapshot when its own read failed, else null. */
  readonly notice?: string | null;
  readonly peers: Resource<PeersData>;
  readonly insiders: Resource<InsiderData>;
  readonly news: Resource<NewsData>;
}

function PanelHeading({
  id,
  title,
  href,
  linkLabel,
}: {
  id: string;
  title: string;
  href?: string;
  linkLabel?: string;
}): React.ReactElement {
  return (
    <div className="mb-1 flex items-baseline justify-between gap-2">
      <h2
        id={id}
        className="text-label font-medium uppercase tracking-[0.08em] text-fg-subtle"
      >
        {title}
      </h2>
      {href !== undefined ? (
        <Link to={href} className="text-label uppercase tracking-[0.06em] text-accent-text hover:text-brand">
          {linkLabel ?? "See all"}
        </Link>
      ) : null}
    </div>
  );
}

function PeerList({ state }: { state: Resource<PeersData> }): React.ReactElement {
  if (state.status === "loading") {
    return <p className="py-2 text-data text-fg-subtle">Loading peers…</p>;
  }
  if (state.status === "error") {
    return (
      <div role="alert" className="py-2 text-data text-fg-muted">
        <p>Could not load peers.</p>
        {state.error ? <p className="mt-0.5 text-fg-subtle">{state.error}</p> : null}
        <button
          type="button"
          onClick={state.retry}
          className="focus-ring mt-1 h-6 rounded-sm border border-line-control px-2 text-data text-fg-muted hover:text-fg"
        >
          Retry
        </button>
      </div>
    );
  }
  if (!state.data || state.data.peers.length === 0) {
    return (
      <p data-testid="peers-empty" className="py-2 text-data text-fg-muted">
        No industry peers are in the loaded identity master.
      </p>
    );
  }

  return (
    <ul data-testid="peers-list" className="flex flex-col">
      {state.data.peers.slice(0, 10).map((peer) => (
        <li key={peer.symbol} className="border-b border-line/70 last:border-b-0">
          <Link
            to={`/stock/${encodeURIComponent(peer.symbol)}`}
            data-peer={peer.symbol}
            className="grid h-5 grid-cols-[68px_minmax(0,1fr)_auto] items-center gap-2 text-data hover:bg-row-hover"
          >
            <span className="nums font-mono font-medium text-accent-text">{peer.symbol}</span>
            <span className="truncate text-fg-muted" title={peer.name}>
              {peer.name}
            </span>
            <span className="truncate text-label uppercase tracking-[0.04em] text-fg-subtle">
              {peer.exchange ?? "—"}
            </span>
          </Link>
        </li>
      ))}
    </ul>
  );
}

function HeldBy({ symbol }: { symbol: string }): React.ReactElement {
  return (
    <div>
      <ul data-testid="held-by-list" className="flex flex-col">
        {HELD_BY_FUNDS.filter((fund) => fund.symbol !== symbol).map((fund) => (
          <li key={fund.symbol} className="border-b border-line/70 last:border-b-0">
            <Link
              to={`/stock/${fund.symbol}`}
              data-fund={fund.symbol}
              className="grid h-5 grid-cols-[52px_minmax(0,1fr)_auto] items-center gap-2 text-data hover:bg-row-hover"
            >
              <span className="nums font-mono font-medium text-accent-text">{fund.symbol}</span>
              <span className="truncate text-fg-muted" title={fund.name}>
                {fund.name}
              </span>
              <span className="text-label uppercase tracking-[0.04em] text-fg-subtle">ETF</span>
            </Link>
          </li>
        ))}
      </ul>
      <p className="mt-1 text-label text-fg-subtle">
        Funds whose published holdings are collected. Membership is not inferred.
      </p>
    </div>
  );
}

function InsiderOverview({
  state,
}: {
  state: Resource<InsiderData>;
}): React.ReactElement {
  if (state.status === "loading") {
    return <p className="py-2 text-data text-fg-subtle">Loading insider filings…</p>;
  }
  if (state.status === "error") {
    return <p className="py-2 text-data text-fg-muted">Could not load insider filings.</p>;
  }
  const items = state.data?.items.slice(0, 10) ?? [];
  if (items.length === 0) {
    return <p className="py-2 text-data text-fg-muted">No recent insider filings.</p>;
  }

  return (
    <div data-testid="insider-overview" className="overflow-x-auto">
      <table className="w-full border-collapse text-data">
        <caption className="sr-only">
          The ten most recent insider filings, newest first: date, insider, transaction
          type, shares and reported value.
        </caption>
        <thead>
          <tr className="border-b border-line">
            <th scope="col" className="py-1 pr-2 text-left text-label font-medium uppercase tracking-[0.06em] text-fg-subtle">
              Date
            </th>
            <th scope="col" className="py-1 pr-2 text-left text-label font-medium uppercase tracking-[0.06em] text-fg-subtle">
              Insider
            </th>
            <th scope="col" className="py-1 pr-2 text-left text-label font-medium uppercase tracking-[0.06em] text-fg-subtle">
              Type
            </th>
            <th scope="col" className="py-1 pr-2 text-right text-label font-medium uppercase tracking-[0.06em] text-fg-subtle">
              Shares
            </th>
            <th scope="col" className="py-1 text-right text-label font-medium uppercase tracking-[0.06em] text-fg-subtle">
              Value
            </th>
          </tr>
        </thead>
        <tbody>
          {items.map((item, index) => {
            const href = safeHref(item.url);
            const date = formatDate(item.transactionDate || item.filed);
            return (
              <tr key={`${item.accession}-${index}`} className="h-5 border-b border-line/70 last:border-b-0">
                <td className="nums whitespace-nowrap pr-2 font-mono text-fg-muted">
                  {href ? (
                    <a
                      href={href}
                      target="_blank"
                      rel="noopener noreferrer"
                      title="Open the filing on EDGAR"
                      className="text-accent-text hover:text-brand"
                    >
                      {date}
                    </a>
                  ) : (
                    date
                  )}
                </td>
                <td className="max-w-[160px] truncate pr-2 text-fg" title={item.owner}>
                  {item.owner || "—"}
                </td>
                <td className="whitespace-nowrap pr-2 text-fg-muted">
                  {insiderType(item.code).short}
                </td>
                <td className="nums whitespace-nowrap pr-2 text-right font-mono text-fg-muted">
                  {formatShares(item.shares)}
                </td>
                <td className="nums whitespace-nowrap text-right font-mono text-fg">
                  {formatTradeMoney(tradeValue(item.shares, item.price))}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function NewsOverview({ state }: { state: Resource<NewsData> }): React.ReactElement {
  if (state.status === "loading") {
    return <p className="py-2 text-data text-fg-subtle">Loading news…</p>;
  }
  if (state.status === "error") {
    return <p className="py-2 text-data text-fg-muted">Could not load news.</p>;
  }
  const items = state.data?.items.slice(0, 10) ?? [];
  if (items.length === 0) {
    return <p className="py-2 text-data text-fg-muted">No recent news.</p>;
  }

  return (
    <ul data-testid="news-overview" className="flex flex-col">
      {items.map((item, index) => {
        const href = safeHref(item.url);
        return (
          <li key={`${item.headline}-${index}`} className="border-b border-line/70 py-1 last:border-b-0">
            <h3 className="text-data font-normal leading-5">
              {href ? (
                <a
                  href={href}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-fg hover:text-brand"
                >
                  {item.headline}
                </a>
              ) : (
                <span className="text-fg">{item.headline}</span>
              )}
            </h3>
            <p className="flex items-baseline gap-1.5 text-label text-fg-subtle">
              <span className="truncate">{item.source || "Unknown source"}</span>
              <span aria-hidden="true">·</span>
              <span className="shrink-0">
                {item.publishedAt ? formatRelativeTime(item.publishedAt) : "—"}
              </span>
            </p>
          </li>
        );
      })}
    </ul>
  );
}

export function Overview({
  symbol,
  sources,
  notice = null,
  peers,
  insiders,
  news,
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

      <div className="grid grid-cols-1 gap-4 min-[900px]:grid-cols-2">
        <section aria-labelledby="peers-heading">
          <PanelHeading id="peers-heading" title="Peers" />
          <PeerList state={peers} />
        </section>
        <section aria-labelledby="held-by-heading">
          <PanelHeading id="held-by-heading" title="Held by" />
          <HeldBy symbol={symbol} />
        </section>
      </div>

      <div className="grid grid-cols-1 gap-4 min-[900px]:grid-cols-2">
        <section aria-labelledby="insider-heading">
          <PanelHeading id="insider-heading" title="Insider trading" href={tabHref(symbol, "insider")} />
          <InsiderOverview state={insiders} />
        </section>
        <section aria-labelledby="news-heading">
          <PanelHeading id="news-heading" title="News" href={tabHref(symbol, "news")} />
          <NewsOverview state={news} />
        </section>
      </div>
    </div>
  );
}
