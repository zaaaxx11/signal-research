/**
 * The shell: nav, one route, nothing else.
 *
 *   nav        the fourteen sections, the symbol search, the clock
 *   content    Home, the screener, a ticker page, a "next" page or a 404
 *
 * There is no status bar and no health read. Both used to sit here — a
 * breadcrumb with three engine checks, a version, a UTC timestamp — and both
 * were operator-facing diagnostics wearing the clothes of product chrome. The
 * deployment still has `GET /health`; the UI no longer asks, so nothing on
 * screen can go stale, lie about "ok", or cost a request on first paint.
 *
 * One route table (`shell/routes`) owns the URLs, so the nav, the ticker links
 * and the browser's own history cannot disagree about where a link goes. A deep
 * link is a real URL: the server's `/app` mount answers any path with the
 * shell, and `parseRoute` decides which page it is.
 *
 * The detail chunk is lazy and shared: docked beside the grid on the screener,
 * full-page on `/stock/{symbol}`. A chunk that never arrives is a panel fault,
 * not a page fault, and the boundary names it and offers one retry.
 */

import * as React from "react";

import { ErrorBoundary } from "@/grid/ErrorBoundary";
import { HomePage } from "@/home/HomePage";
import { ScreenerScreen } from "@/screener/ScreenerScreen";
import { GRID_REGION_ID } from "@/shell/ids";
import { NotFoundPage } from "@/shell/NotFoundPage";
import { SiteNav } from "@/shell/SiteNav";
import { SoonPage } from "@/shell/SoonPage";
import { StockPage } from "@/stock/StockPage";
import { DETAIL_CHUNK_ATTEMPTS, detailChunk, loadDetailPanel } from "@/detail/load";
import type { SymbolHit } from "@/lib/api";
import { navigate, useRoute, useSearch } from "@/shell/routes";
import {
  loadCalendarPage,
  loadChartsPage,
  loadCryptoPage,
  loadForexPage,
  loadFuturesPage,
  loadGroupsPage,
  loadInsiderPage,
  loadMapPage,
  loadPortfolioPage,
  loadWatchlistPage,
} from "@/shell/lazyPages";

// The chunk's loader and retry budget are re-exported here because this is
// where the panel first lived and where its tests point; the implementation is
// shared with `/stock/{symbol}` (see `detail/load.ts`).
export { DETAIL_CHUNK_ATTEMPTS, detailChunk };
export { GRID_REGION_ID };

/**
 * The first focusable element in the document.
 *
 * The chrome in front of the grid is a nav and about twenty controls, so
 * without this a keyboard user pays ten-plus Tab presses before the first row.
 *
 * It is an `<a href>` and not a button, so it appears in a screen reader's list
 * of links and works with the browser's own fragment navigation; the click
 * handler adds only the focus, which is the half a URL cannot do (a fragment
 * scrolls the box, it does not move the caret). `preventDefault` keeps the
 * address bar clean — the app already writes view/page/sort into the query
 * string, and a `#grid-region` in the middle of that is noise a reload would
 * carry.
 *
 * Adversarial (d): this is not a second `<main>`. The one `<main>` below owns
 * the content band and the link targets a named region inside it. A landmark
 * that existed only to be skipped to would give the document two mains and make
 * "go to main content" ambiguous.
 */
function SkipLink() {
  return (
    <a
      href={`#${GRID_REGION_ID}`}
      data-testid="skip-link"
      onClick={(event) => {
        const target = document.getElementById(GRID_REGION_ID);
        // No grid yet: let the browser's own fragment jump happen instead of
        // swallowing the click.
        if (!target) return;
        event.preventDefault();
        target.focus();
      }}
      // Off-screen until focused, then a 28 px rose-bordered pill at the top
      // left. `fixed` (not `sr-only`) so focusing it cannot shift the layout:
      // the link is painted above the nav for as long as it holds focus.
      className="-translate-y-[200%] fixed left-2 top-2 z-[100] rounded-sm border border-focus bg-surface px-3 py-1.5 text-ui font-medium text-fg transition-transform focus:translate-y-0"
    >
      Skip to the stock grid
    </a>
  );
}

/**
 * What a lazy page shows while its chunk travels.
 *
 * Reserves the viewport under the nav and says what is coming, so the shell does
 * not jump when the page arrives and a slow network is not mistaken for a blank
 * page. `role="status"` is the one live region on the page at that moment.
 */
function PageSkeleton({ label }: { label: string }) {
  return (
    <main className="flex min-h-0 flex-1 items-center justify-center" aria-busy="true">
      <p role="status" className="text-ui text-fg-subtle">
        {label}…
      </p>
    </main>
  );
}

export function App() {
  const route = useRoute();
  const searchQuery = useSearch();
  const [symbol, setSymbol] = React.useState<string | null>(null);
  // Bumped by the boundary's "Try again": a new lazy type re-runs the import.
  // It starts at zero and only moves on an explicit press, so a broken chunk
  // can never retry on its own.
  const [detailAttempt, setDetailAttempt] = React.useState(0);
  const DetailPanel = React.useMemo(
    () => React.lazy(loadDetailPanel),
    // The dependency is the retry counter, not `symbol`: a new type per symbol
    // would re-suspend on every row click even after the chunk has landed.
    [detailAttempt],
  );
  const MapPage = React.useMemo(() => React.lazy(loadMapPage), []);
  const GroupsPage = React.useMemo(() => React.lazy(loadGroupsPage), []);
  const CalendarPage = React.useMemo(() => React.lazy(loadCalendarPage), []);
  const ChartsPage = React.useMemo(() => React.lazy(loadChartsPage), []);
  const InsiderPage = React.useMemo(() => React.lazy(loadInsiderPage), []);
  const CryptoPage = React.useMemo(() => React.lazy(loadCryptoPage), []);
  const FuturesPage = React.useMemo(() => React.lazy(loadFuturesPage), []);
  const ForexPage = React.useMemo(() => React.lazy(loadForexPage), []);
  const WatchlistPage = React.useMemo(() => React.lazy(loadWatchlistPage), []);
  const PortfolioPage = React.useMemo(() => React.lazy(loadPortfolioPage), []);
  const searchRef = React.useRef<HTMLInputElement>(null);

  // "/" focuses the one symbol search; the filter palette keeps ⌘K (it lives in
  // the screener, which is the only screen with filters to find).
  React.useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target;
      const typing =
        target instanceof HTMLElement &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.isContentEditable);
      if (event.key === "/" && !typing) {
        event.preventDefault();
        searchRef.current?.focus();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  // The docked panel belongs to the screener. Leaving it closes the selection,
  // so returning to the grid does not reopen a symbol the reader had moved on
  // from.
  React.useEffect(() => {
    if (route.kind !== "screener") setSymbol(null);
  }, [route.kind]);

  /** A search hit opens the panel in place on the grid, and the page elsewhere. */
  const onSearchSelect = (hit: SymbolHit) => {
    if (route.kind === "screener") setSymbol(hit.symbol);
    else navigate(`/stock/${encodeURIComponent(hit.symbol)}`);
  };

  const detail =
    route.kind === "screener" && symbol ? (
      // The detail panel is lazy: it is not in the first paint, and it is
      // docked beside the grid (never over it) so the comparison holds. A chunk
      // that never arrives is a panel fault, not a page fault: the boundary
      // owns it, names it, and leaves every other band — including the grid the
      // reader is still using — alive.
      <ErrorBoundary
        resetKey={symbol}
        label="Details panel"
        hint="Its code could not be downloaded — a deploy may have replaced this chunk. The grid, the views and the filters are still live."
        className="w-[420px] flex-none"
        onRetry={() => setDetailAttempt((attempt) => attempt + 1)}
      >
        <React.Suspense
          fallback={
            <aside
              id="detail-slot"
              aria-busy="true"
              aria-label="Loading details"
              className="w-[420px] flex-none rounded-md border border-line bg-surface"
            />
          }
        >
          <DetailPanel symbol={symbol} onClose={() => setSymbol(null)} />
        </React.Suspense>
      </ErrorBoundary>
    ) : null;

  return (
    <div className="flex h-dvh min-h-0 flex-col bg-bg">
      {route.kind === "screener" ? <SkipLink /> : null}

      <SiteNav
        route={route}
        search={searchQuery}
        onSelect={onSearchSelect}
        inputRef={searchRef}
      />

      {route.kind === "home" ? (
        <HomePage />
      ) : route.kind === "screener" ? (
        <ScreenerScreen
          assetClass={route.assetClass}
          symbol={symbol}
          onSelectSymbol={setSymbol}
          detail={detail}
        />
      ) : route.kind === "stock" ? (
        <StockPage symbol={route.symbol} onClose={() => navigate("/")} />
      ) : route.kind === "map" ? (
        <React.Suspense fallback={<PageSkeleton label="Drawing the market map" />}>
          <MapPage />
        </React.Suspense>
      ) : route.kind === "groups" ? (
        <React.Suspense fallback={<PageSkeleton label="Loading sector groups" />}>
          <GroupsPage />
        </React.Suspense>
      ) : route.kind === "calendar" ? (
        <React.Suspense fallback={<PageSkeleton label="Loading the earnings calendar" />}>
          <CalendarPage />
        </React.Suspense>
      ) : route.kind === "charts" ? (
        <React.Suspense fallback={<PageSkeleton label="Loading charts" />}>
          <ChartsPage />
        </React.Suspense>
      ) : route.kind === "insider" ? (
        <React.Suspense fallback={<PageSkeleton label="Loading insider activity" />}>
          <InsiderPage />
        </React.Suspense>
      ) : route.kind === "crypto" ? (
        <React.Suspense fallback={<PageSkeleton label="Loading crypto" />}>
          <CryptoPage />
        </React.Suspense>
      ) : route.kind === "futures" ? (
        <React.Suspense fallback={<PageSkeleton label="Loading futures" />}>
          <FuturesPage />
        </React.Suspense>
      ) : route.kind === "forex" ? (
        <React.Suspense fallback={<PageSkeleton label="Loading forex" />}>
          <ForexPage />
        </React.Suspense>
      ) : route.kind === "watchlist" ? (
        <React.Suspense fallback={<PageSkeleton label="Loading your watchlist" />}>
          <WatchlistPage />
        </React.Suspense>
      ) : route.kind === "portfolio" ? (
        <React.Suspense fallback={<PageSkeleton label="Loading your portfolio" />}>
          <PortfolioPage />
        </React.Suspense>
      ) : route.kind === "soon" ? (
        <SoonPage slug={route.slug} />
      ) : (
        <NotFoundPage path={route.path} />
      )}
    </div>
  );
}
