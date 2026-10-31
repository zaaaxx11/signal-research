import * as React from "react";

import { BreadthStrip } from "@/breadth";
import { CommandPalette } from "@/filters/CommandPalette";
import { FilterDrawer } from "@/filters/FilterDrawer";
import { buildRegistry } from "@/filters/registry";
import { useFilterSchema } from "@/filters/schema";
import { useScreenState } from "@/filters/useScreenState";
import { AllColumnsPanel } from "@/grid/AllColumnsPanel";
import { ErrorBoundary } from "@/grid/ErrorBoundary";
import { ScreenPanel } from "@/grid/ScreenPanel";
import { ViewTabs } from "@/grid/ViewTabs";
import { splitCatalogue } from "@/grid/catalogue";
import { useColumnSelection } from "@/grid/columnSelection";
import { withSector } from "@/grid/sectors";
import { useSectorOptions } from "@/grid/useSectors";
import { StatusLine } from "@/shell/StatusLine";
import type { DetailPanelProps } from "@/detail";
import { TopBar } from "@/shell/TopBar";
import { useHealth } from "@/shell/useHealth";

/**
 * How many times one load of the detail chunk may ask the network: the first
 * `import()`, then exactly **one** retry. Two is the whole budget — a chunk
 * that is permanently gone (a 404 on a rotated hash) must land in the boundary
 * with a message, not in a retry loop that never stops asking.
 */
export const DETAIL_CHUNK_ATTEMPTS = 2;

/** How long the retry waits: one deploy's worth of a blip, not a backoff. */
const DETAIL_CHUNK_RETRY_DELAY_MS = 200;

export interface DetailChunkModule {
  default: React.ComponentType<DetailPanelProps>;
}

/**
 * The one dynamic import, behind a property so a test can make it reject.
 *
 * A chunk 404 is the failure the detail boundary exists for, and jsdom cannot
 * produce one any other way; the rejection this hands back is the same
 * `TypeError: Failed to fetch dynamically imported module` a browser raises.
 * The panel itself stays out of first paint, which is why it is split at all.
 */
export const detailChunk: { load: () => Promise<DetailChunkModule> } = {
  load: () => import("@/detail"),
};

async function loadDetailPanel(): Promise<DetailChunkModule> {
  try {
    return await detailChunk.load();
  } catch {
    // Bounded on purpose: one retry covers a dropped response or a deploy that
    // finished mid-flight; the second failure is the boundary's to name.
    await new Promise((resolve) => setTimeout(resolve, DETAIL_CHUNK_RETRY_DELAY_MS));
    return detailChunk.load();
  }
}

/**
 * The shell: one screen, four bands, no routing.
 *
 *   top bar     identity + the symbol search
 *   tab strip   the views the schema served — never a hardcoded list
 *   status line breadcrumb + GET /health
 *   content     the chip bar (when filters exist) and the 100-row grid
 *
 * One schema read at boot feeds the view catalogue and the filter registry; one
 * `useScreenState` owns view + sort + page + filters and writes them to the
 * query string. The drawer, the chips and the request body all read that one
 * state — there is no second copy to drift.
 *
 * Two destinations share the content band:
 *
 * * a **view** — a tab, at most 40 columns, one request;
 * * the **all-columns** mode — the served wide view, 116 keys, three chunked
 *   requests, reachable from a control beside the strip and never from it. It
 *   is not a tab because the API cannot answer it as one.
 *
 * The layout also docks two bands that arrive from separate reads:
 *
 * * the **breadth strip** above the grid, one `GET /market/breadth`, with its
 *   settled height reserved so a slow read cannot move the grid (F8);
 * * the **detail panel** in the right column, only while a symbol is open —
 *   lazily imported, so it costs nothing on first paint, and wrapped in an
 *   error boundary, so a chunk that never arrives costs the panel and not the
 *   session (F1). Nothing is rendered while it is closed: an empty `<aside>`
 *   is an unnamed landmark, which is worse than no landmark at all (F5).
 */
export function App() {
  const schema = useFilterSchema();
  const registry = React.useMemo(
    () => (schema.schema ? buildRegistry(schema.schema) : null),
    [schema.schema],
  );

  // The catalogue is split structurally, never by name: a view the API cannot
  // answer in one request is the wide destination, everything else is a tab.
  const catalogue = React.useMemo(
    () => splitCatalogue(schema.schema?.views ?? []),
    [schema.schema],
  );
  const viewNames = React.useMemo(
    () => catalogue.views.map((entry) => entry.name),
    [catalogue.views],
  );

  const screen = useScreenState(registry, viewNames);
  // The sector menu's population: the 21 labels `GET /market/breadth` counted
  // over the whole universe. Read here, not in the panel, so the panel stays a
  // renderer — and so the menu survives a panel that has not loaded yet.
  const sectors = useSectorOptions();
  const columnSelection = useColumnSelection({
    viewNames,
    catalogue: catalogue.keys,
    defaults: React.useMemo(
      () => Object.fromEntries(catalogue.views.map((entry) => [entry.name, entry.columns])),
      [catalogue.views],
    ),
    ready: screen.ready,
    // Declared after `useScreenState` on purpose: its writer runs first and
    // replaces the whole query string, and this hook's writer then merges the
    // `cols` parameters back in. See `useColumnSelection`.
    revision: screen.state,
  });
  const [wide, setWide] = React.useState(false);
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
  const [drawerOpen, setDrawerOpen] = React.useState(false);
  const [paletteOpen, setPaletteOpen] = React.useState(false);
  const [focusKey, setFocusKey] = React.useState<string | null>(null);
  const health = useHealth();
  const searchRef = React.useRef<HTMLInputElement>(null);

  // ⌘K is the filter palette; "/" stays the symbol search. Two chords, two
  // jobs — the previous binding sent both to the same input.
  React.useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target;
      const typing =
        target instanceof HTMLElement &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.isContentEditable);

      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setPaletteOpen(true);
        return;
      }
      if (event.key === "/" && !typing) {
        event.preventDefault();
        searchRef.current?.focus();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  const active = catalogue.views.find((entry) => entry.name === screen.state.view) ?? catalogue.views[0] ?? null;
  const destination = catalogue.destination;
  const wideActive = wide && destination !== null;
  // The panel must not ask before the link's column selection has been read:
  // a request with the view's columns followed by one with the selection would
  // be two requests for one screen.
  const columnsReady = columnSelection.ready || catalogue.keys.length === 0;

  const openDrawer = () => {
    setFocusKey(null);
    setDrawerOpen(true);
  };

  const closeDrawer = () => {
    setDrawerOpen(false);
    setFocusKey(null);
  };

  const selectView = (name: string) => {
    setWide(false);
    screen.setView(name);
  };

  return (
    <div className="flex h-dvh min-h-0 flex-col bg-bg">
      <TopBar onSelect={(hit) => setSymbol(hit.symbol)} inputRef={searchRef} />

      <div className="flex items-stretch justify-between gap-2 border-b border-line bg-surface px-3 pt-0.5">
        <div className="min-w-0 flex-1">
          {catalogue.views.length > 0 && active ? (
            <ViewTabs
              views={catalogue.views}
              active={wideActive ? "" : active.name}
              onChange={selectView}
            />
          ) : (
            <div className="h-8" aria-hidden="true" />
          )}
        </div>

        {destination ? (
          <button
            type="button"
            onClick={() => setWide((value) => !value)}
            aria-pressed={wideActive}
            data-testid="all-columns-toggle"
            title={`${destination.job} ${catalogue.keys.length} keys, fetched in chunks of at most 40 columns.`}
            className={`-mb-px shrink-0 border-b-2 px-2.5 pb-1.5 pt-2 text-ui transition-colors ${
              wideActive
                ? "border-brand text-fg"
                : "border-transparent text-fg-muted hover:text-fg"
            }`}
          >
            {destination.label}
            <span className="ml-1.5 font-mono text-label text-fg-subtle">{catalogue.keys.length}</span>
          </button>
        ) : null}
      </div>

      <StatusLine
        viewLabel={wideActive ? (destination?.label ?? "Views") : (active?.label ?? "Views")}
        symbol={symbol}
        health={health}
      />

      <main className="flex min-h-0 flex-1 flex-col gap-2 p-3">
        {/* Real market-wide breadth, not a count of the rows on screen. */}
        <BreadthStrip />

        <div className="flex min-h-0 flex-1 gap-2">
          <div className="flex min-h-0 min-w-0 flex-1 flex-col">
            {schema.status === "error" ? (
              <section
                role="alert"
                className="flex min-h-0 flex-1 flex-col justify-center rounded-md border border-line border-l-2 border-l-alert bg-surface p-6"
              >
                <p className="text-ui font-medium text-fg">The filter schema did not load</p>
                <p className="mt-1 max-w-[60ch] text-data text-fg-muted">{schema.error}</p>
                <p className="mt-1 max-w-[60ch] text-data text-fg-subtle">
                  Views, columns and filter keys all come from that one document; without it
                  there is nothing honest to draw.
                </p>
                <div className="mt-3">
                  <button
                    type="button"
                    onClick={schema.retry}
                    className="rounded-sm border border-line-strong px-2 py-1 text-ui text-fg-muted hover:text-fg"
                  >
                    Retry
                  </button>
                </div>
              </section>
            ) : wideActive && destination ? (
              // A render fault in the wide table costs that mode, never the shell.
              <ErrorBoundary resetKey={destination.name} label={`${destination.label} panel`}>
                <AllColumnsPanel
                  destination={destination}
                  keys={catalogue.keys}
                  selectedSymbol={symbol}
                  onActivate={setSymbol}
                  onClear={() => setSymbol(null)}
                  onBack={() => setWide(false)}
                />
              </ErrorBoundary>
            ) : active && registry && screen.ready && columnsReady ? (
              // A render fault in the grid costs the panel, never the shell: the
              // top bar, tabs and status line stay, and the boundary offers a way
              // back. API errors never arrive here — the hook renders those.
              <ErrorBoundary resetKey={active.name} label={`${active.label} panel`}>
                <ScreenPanel
                  view={active}
                  selectedSymbol={symbol}
                  onActivate={setSymbol}
                  onClear={() => setSymbol(null)}
                  sort={screen.state.sort}
                  page={screen.state.page}
                  onSortChange={screen.setSort}
                  onPageChange={screen.setPage}
                  filters={screen.state.filters}
                  registry={registry}
                  onRemoveFilter={screen.removeFilter}
                  onClearFilters={screen.clearFilters}
                  onOpenFilters={openDrawer}
                  filterNotice={screen.notice}
                  onDismissFilterNotice={screen.dismissDropped}
                  catalogue={catalogue.keys}
                  columns={columnSelection.columnsFor(active.name) ?? active.columns}
                  onToggleColumn={(key) => columnSelection.toggle(active.name, key)}
                  onResetColumns={() => columnSelection.reset(active.name)}
                  columnNotice={columnSelection.notice}
                  onDismissColumnNotice={columnSelection.dismiss}
                  sectorOptions={sectors.names}
                  sectorSource={
                    sectors.status === "ready"
                      ? "breadth"
                      : sectors.status === "loading"
                        ? "loading"
                        : "unavailable"
                  }
                  // A sector is one more predicate in the one filter list, so
                  // the menu, the chip, the URL and the request body read the
                  // same state — and `setFilters` is what turns the page back
                  // to 1.
                  onSectorChange={(name) =>
                    screen.setFilters(withSector(screen.state.filters, name))
                  }
                />
              </ErrorBoundary>
            ) : (
              <section
                aria-busy="true"
                aria-label="Loading views"
                className="flex min-h-0 flex-1 flex-col gap-1 rounded-md border border-line bg-surface p-3"
              >
                <div className="h-6 w-40 rounded-[1px] bg-surface-raised" />
                {Array.from({ length: 12 }, (_, index) => (
                  <div
                    key={index}
                    className={`h-7 rounded-[1px] ${index % 2 === 0 ? "bg-row" : "bg-row-alt"}`}
                  />
                ))}
              </section>
            )}
          </div>

          {/* The detail panel is lazy: it is not in the first paint, and it is
              docked beside the grid (never over it) so the comparison holds.
              A chunk that never arrives is a panel fault, not a page fault:
              the boundary owns it, names it, and leaves every other band —
              including the grid the reader is still using — alive. */}
          {symbol ? (
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
          ) : null}
        </div>
      </main>

      {registry ? (
        <FilterDrawer
          open={drawerOpen}
          registry={registry}
          filters={screen.state.filters}
          onClose={closeDrawer}
          onAdd={screen.addFilters}
          onRemove={screen.removeFilter}
          onClear={screen.clearFilters}
          onClearKey={(key) =>
            screen.setFilters(screen.state.filters.filter((filter) => filter.key !== key))
          }
          onApplyBucket={screen.applyBucket}
          focusKey={focusKey}
        />
      ) : null}

      {registry ? (
        <CommandPalette
          open={paletteOpen}
          registry={registry}
          onClose={() => setPaletteOpen(false)}
          onPick={(key) => {
            setPaletteOpen(false);
            setFocusKey(key);
            setDrawerOpen(true);
          }}
        />
      ) : null}

      <footer className="flex h-6 shrink-0 items-center justify-between border-t border-line bg-surface-sunken px-3 font-mono text-label uppercase tracking-[0.08em] text-fg-subtle">
        <span>same-origin api only · no telemetry</span>
        <span>
          web v{__APP_VERSION__} · api v{health.report?.version ?? "—"}
        </span>
      </footer>
    </div>
  );
}
