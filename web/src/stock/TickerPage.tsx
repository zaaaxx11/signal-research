/**
 * `/app/stock/{symbol}` — the ticker page.
 *
 * The band and the tab strip are the page's frame; the panel below is one of
 * seven sections, and the address bar names which (`?tab=statements`). The
 * strip is the only navigator: there is no local `useState` for the active tab,
 * so Back, a shared link and a reload all land on the same section.
 *
 * Each tab draws only what it needs. Overview is the composite (chart,
 * snapshot, peers, held-by, insider, news); Statements mounts the collector;
 * Ownership/Insider/News reuse the detail panel's own components so a value
 * cannot be formatted two ways on two surfaces; Filings lists the Form 4
 * documents the engine actually reads; Chart is the same instrument at a
 * taller height.
 */

import * as React from "react";

import { InsidersTab } from "@/detail/InsidersTab";
import { NewsTab } from "@/detail/NewsTab";
import { OwnershipTab } from "@/detail/OwnershipTab";
import { navigate, useSearch } from "@/shell/routes";

import { FilingsTab } from "./FilingsTab";
import { Overview } from "./Overview";
import { TickerChart } from "./TickerChart";
import { TickerHeader } from "./TickerHeader";
import { TickerTabs, TICKER_PANEL_ID, tabFromSearch, tabHref, tabId } from "./TickerTabs";
import type { SnapshotSources } from "./snapshot";
import { headerView, useTicker } from "./useTicker";

export interface TickerPageProps {
  readonly symbol: string;
}

export function TickerPage({ symbol }: TickerPageProps): React.ReactElement {
  const search = useSearch();
  const active = tabFromSearch(search);
  const resources = useTicker(symbol, active);
  const header = headerView(resources, symbol);

  const sources = React.useMemo<SnapshotSources>(
    () => ({
      metrics: resources.fundamentals.data?.metrics ?? null,
      row: resources.row.data,
      profile: resources.profile.data?.raw ?? null,
    }),
    [resources.fundamentals.data, resources.row.data, resources.profile.data],
  );

  const onChange = React.useCallback(
    (tab: typeof active) => navigate(tabHref(symbol, tab)),
    [symbol],
  );

  const content = (() => {
    switch (active) {
      case "statements":
        return <StatementsSection symbol={symbol} />;
      case "ownership":
        return (
          <OwnershipTab
            symbol={symbol}
            state={resources.row}
            onRetry={resources.row.retry}
          />
        );
      case "insider":
        return (
          <InsidersTab
            symbol={symbol}
            state={resources.insiders}
            onRetry={resources.insiders.retry}
          />
        );
      case "filings":
        return <FilingsTab symbol={symbol} state={resources.insiders} />;
      case "news":
        return (
          <NewsTab symbol={symbol} state={resources.news} onRetry={resources.news.retry} />
        );
      case "chart":
        return <TickerChart symbol={symbol} height={360} />;
      case "overview":
      default:
        return (
          <Overview
            symbol={symbol}
            sources={sources}
            peers={resources.peers}
            insiders={resources.insiders}
            news={resources.news}
          />
        );
    }
  })();

  return (
    <div
      data-testid="ticker-page"
      data-symbol={symbol}
      data-tab={active}
      className="flex min-h-0 flex-1 flex-col overflow-hidden bg-bg"
    >
      <TickerHeader header={header} />
      <TickerTabs active={active} onChange={onChange} />
      <div
        id={TICKER_PANEL_ID}
        role="tabpanel"
        aria-labelledby={tabId(active)}
        tabIndex={0}
        className="focus-ring min-h-0 flex-1 overflow-y-auto overscroll-contain px-4 py-3"
      >
        {content}
      </div>
    </div>
  );
}

/**
 * The statements chunk is split from the page for the same reason the chart
 * is: a reader who came for the quote should not download a collector UI.
 */
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

export default TickerPage;
