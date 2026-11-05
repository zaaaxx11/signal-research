/**
 * `/app/stock/{symbol}` — the ticker page.
 *
 * The band and the tab strip are the page's frame; the panel below is one of
 * five sections, and the address bar names which (`?tab=statements`). The
 * strip is the only navigator: there is no local `useState` for the active tab,
 * so Back, a shared link and a reload all land on the same section.
 *
 * Each tab draws only what it needs. Overview is the composite (chart,
 * snapshot, company profile, technicals); Statements mounts the collector;
 * Peers/Insider/News each own their own read so a deep link never fetches data
 * for a tab the reader is not looking at.
 */

import * as React from "react";

import { cn } from "@/lib/utils";
import { navigate, useSearch } from "@/shell/routes";

import { Overview } from "./Overview";
import { TickerActions, type TickerNotice } from "./TickerActions";
import { TickerHeader } from "./TickerHeader";
import { TickerTabs, TICKER_PANEL_ID, tabFromSearch, tabHref, tabId } from "./TickerTabs";
import type { SnapshotSources } from "./snapshot";
import { headerView, useSnapshot, useTicker } from "./useTicker";

export interface TickerPageProps {
  readonly symbol: string;
}

export function TickerPage({ symbol }: TickerPageProps): React.ReactElement {
  const search = useSearch();
  const active = tabFromSearch(search);
  const resources = useTicker(symbol, active);
  const header = headerView(resources, symbol);
  const [notice, setNotice] = React.useState<TickerNotice | null>(null);
  // The snapshot is its own read, and only Overview draws it: a deep link to
  // Financials must not pay for 84 values nobody will see.
  const snapshot = useSnapshot(symbol, active === "overview");

  const sources = React.useMemo<SnapshotSources>(
    () => ({
      snapshot: snapshot.data,
      metrics: resources.fundamentals.data?.metrics ?? null,
      row: resources.row.data,
      profile: resources.profile.data?.raw ?? null,
    }),
    [snapshot.data, resources.fundamentals.data, resources.row.data, resources.profile.data],
  );

  const onChange = React.useCallback(
    (tab: typeof active) => navigate(tabHref(symbol, tab)),
    [symbol],
  );

  const content = (() => {
    switch (active) {
      case "statements":
        return <StatementsSection symbol={symbol} />;
      case "peers":
        return <PeersSection symbol={symbol} />;
      case "insider":
        return <InsiderSection symbol={symbol} state={resources.insiders} />;
      case "news":
        return <NewsSection symbol={symbol} state={resources.news} />;
      case "overview":
      default:
        return (
          <Overview
            symbol={symbol}
            sources={sources}
            notice={snapshot.notice}
          />
        );
    }
  })();

  return (
    <div
      data-testid="ticker-page"
      data-symbol={symbol}
      data-tab={active}
      className="flex flex-col bg-bg"
    >
      <TickerHeader
        header={header}
        actions={
          <TickerActions
            symbol={symbol}
            name={header.company}
            exchange={resources.security.data?.exchange ?? null}
            row={resources.row.data}
            onNotice={setNotice}
          />
        }
      />
      {notice === null ? null : (
        <p
          role="status"
          data-testid="ticker-action-notice"
          className={cn(
            "border-b border-line bg-surface px-4 pb-1.5 text-data",
            notice.ok ? "text-fg-muted" : "text-alert",
          )}
        >
          {notice.message}
        </p>
      )}
      <TickerTabs active={active} onChange={onChange} />
      <div
        id={TICKER_PANEL_ID}
        role="tabpanel"
        aria-labelledby={tabId(active)}
        tabIndex={0}
        className="focus-ring px-4 py-3"
      >
        {content}
      </div>
    </div>
  );
}

/** The statements chunk is split from the page for code splitting. */
const StatementsTab = React.lazy(() =>
  import("./StatementsTab").then((module) => ({ default: module.StatementsTab })),
);

function StatementsSection({ symbol }: { symbol: string }): React.ReactElement {
  return (
    <React.Suspense
      fallback={
        <div
          aria-busy="true"
          aria-label={`Loading statements for ${symbol}`}
          className="h-40"
        />
      }
    >
      <StatementsTab symbol={symbol} />
    </React.Suspense>
  );
}

/** The peers tab is its own lazy chunk — the full table. */
const PeersTab = React.lazy(() =>
  import("./PeersTab").then((module) => ({ default: module.PeersTab })),
);

function PeersSection({ symbol }: { symbol: string }): React.ReactElement {
  return (
    <React.Suspense
      fallback={
        <div
          aria-busy="true"
          aria-label={`Loading peers for ${symbol}`}
          className="h-40"
        />
      }
    >
      <PeersTab symbol={symbol} />
    </React.Suspense>
  );
}

/** The insider tab shows the Form 4 filings from the engine. */
import type { InsiderData, Resource } from "@/detail/useDetail";
import { FilingsTab } from "./FilingsTab";

function InsiderSection({
  symbol,
  state,
}: {
  symbol: string;
  state: Resource<InsiderData>;
}): React.ReactElement {
  return <FilingsTab symbol={symbol} state={state} />;
}

/** The news tab shows headlines from /news/{symbol}. */
import type { NewsData } from "@/detail/useDetail";
import { NewsTab } from "@/detail/NewsTab";

function NewsSection({
  symbol,
  state,
}: {
  symbol: string;
  state: Resource<NewsData>;
}): React.ReactElement {
  return (
    <NewsTab symbol={symbol} state={state} onRetry={state.retry} />
  );
}

export default TickerPage;