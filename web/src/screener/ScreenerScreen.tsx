/**
 * The screener: one screen, three bands, no page chrome of its own.
 *
 *   tab strip   the views the schema served — never a hardcoded list
 *   content     the chip bar (when filters exist) and the 20-row grid
 *   detail      the docked panel, passed in by the shell so the shell owns the
 *               selection and the lazy chunk's boundary
 *
 * One schema read at boot feeds the view catalogue and the filter registry; one
 * `useScreenState` owns view + sort + page + filters and writes them to the
 * query string. The drawer, the chips and the request body all read that one
 * state — there is no second copy to drift.
 *
 * The top bar, the nav and the clock live in `shell/SiteNav`; this component is
 * the content of `/screener` and `/screener/etf`, and the only difference
 * between those two routes is the `asset_class` every request carries.
 *
 * Two destinations share the content band:
 *
 * * a **view** — a tab, at most 40 columns, one request;
 * * the **all-columns** mode — the served wide view, 116 keys, three chunked
 *   requests, reachable from a control beside the strip and never from it. It
 *   is not a tab because the API cannot answer it as one.
 */

import * as React from "react";

import { CommandPalette } from "@/filters/CommandPalette";
import { FilterDrawer } from "@/filters/FilterDrawer";
import { buildRegistry } from "@/filters/registry";
import { useFilterSchema } from "@/filters/schema";
import { useScreenState } from "@/filters/useScreenState";
import { AllColumnsPanel } from "@/grid/AllColumnsPanel";
import { ErrorBoundary } from "@/grid/ErrorBoundary";
import { ScreenPanel } from "@/grid/ScreenPanel";
import { splitCatalogue } from "@/grid/catalogue";
import { useColumnSelection } from "@/grid/columnSelection";
import { withExchange } from "@/grid/exchanges";
import { withSector } from "@/grid/sectors";
import { useExchangeOptions } from "@/grid/useExchanges";
import { useSectorOptions } from "@/grid/useSectors";
import { GRID_REGION_ID } from "@/shell/ids";

export interface ScreenerScreenProps {
  /** `stock` for `/screener`, `etf` for `/screener/etf`. */
  assetClass: "stock" | "etf";
  /** The symbol whose details are docked; owned by the shell. */
  symbol: string | null;
  onSelectSymbol: (symbol: string | null) => void;
  /** The docked panel, already lazy and bounded by the shell. */
  detail: React.ReactNode;
}

export function ScreenerScreen({
  assetClass,
  symbol,
  onSelectSymbol,
  detail,
}: ScreenerScreenProps) {
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
  // The exchange menu's population: the venues `GET /market/exchanges` counted
  // over the same universe the selection filters. A route of its own, read once
  // per mount — breadth keeps its two consumers and its reconcilable counts.
  const exchanges = useExchangeOptions();
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
  const [drawerOpen, setDrawerOpen] = React.useState(false);
  const [paletteOpen, setPaletteOpen] = React.useState(false);
  const [focusKey, setFocusKey] = React.useState<string | null>(null);

  // ⌘K is the filter palette. "/" belongs to the nav's symbol search and is
  // handled by the shell, so the two chords never contend for one input.
  React.useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setPaletteOpen(true);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  const active =
    catalogue.views.find((entry) => entry.name === screen.state.view) ??
    catalogue.views[0] ??
    null;
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

  return (
    <>
      <div className="flex items-stretch justify-between gap-2 border-b border-line bg-surface px-3 pt-0.5">
        <div className="min-w-0 flex-1" />

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
            <span className="ml-1.5 font-mono text-label text-fg-subtle">
              {catalogue.keys.length}
            </span>
          </button>
        ) : null}
      </div>

      <main className="flex min-h-0 flex-1 flex-col gap-2 p-3">
        <div className="flex min-h-0 flex-1 gap-2">
          <div
            id={GRID_REGION_ID}
            // The skip link's destination. `tabIndex={-1}` makes it a programmatic
            // focus target (and not a tab stop); the region is named so the
            // landing is announced as something ("Stock grid region") rather
            // than as an anonymous box. It is not a landmark in its own right
            // beyond `region`, and it is inside the one `<main>`.
            role="region"
            aria-label="Stock grid"
            tabIndex={-1}
            className="flex min-h-0 min-w-0 flex-1 flex-col focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-focus"
          >
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
                  onActivate={onSelectSymbol}
                  onClear={() => onSelectSymbol(null)}
                  onBack={() => setWide(false)}
                />
              </ErrorBoundary>
            ) : active && registry && screen.ready && columnsReady ? (
              // A render fault in the grid costs the panel, never the shell: the
              // nav, tabs and toolbar stay, and the boundary offers a way back.
              <ErrorBoundary resetKey={active.name} label={`${active.label} panel`}>
                <ScreenPanel
                  view={active}
                  assetClass={assetClass}
                  selectedSymbol={symbol}
                  onActivate={onSelectSymbol}
                  onClear={() => onSelectSymbol(null)}
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
                  exchangeOptions={exchanges.venues}
                  exchangeSource={
                    exchanges.status === "ready"
                      ? "market"
                      : exchanges.status === "loading"
                        ? "loading"
                        : "unavailable"
                  }
                  // One predicate per set: `eq` for a single venue, `in` for
                  // several. `setFilters` turns the page back to 1, so a choice
                  // is one request at offset 0, and the chip, the URL and the
                  // body all read the same array.
                  onExchangeChange={(codes) =>
                    screen.setFilters(withExchange(screen.state.filters, codes))
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

          {detail}
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
    </>
  );
}
