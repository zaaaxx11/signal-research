/**
 * The screener: a masthead and one content band.
 *
 *   masthead    the h1, Column preset / Order by / Signal / ticker, the Filter
 *               disclosure (five server-driven group selects) and the
 *               always-present column-preset bar
 *   content     the chip bar (when filters exist) and the 20-row grid
 *
 * A row is a door, not a selection: every ticker navigates to its own page at
 * `/stock/{symbol}`, and the table takes the content band's full width. The
 * only thing that selects a row is its checkbox, which is what the toolbar's
 * Add to / Compare / Set alert act on.
 *
 * Two boot reads feed it — `GET /screens/select/schema` for views and filter
 * keys, `GET /screens/filters` for the panel's vocabulary — and one
 * `useScreenState` owns view + sort + signal + page + filters and writes them
 * to the query string. The header, the chips and the request body all read that
 * one state; there is no second copy to drift.
 *
 * The nav and the clock live in `shell/SiteNav`; this component is the content
 * of `/screener` and `/screener/etf`, and the only difference between those two
 * routes is the `asset_class` every request carries.
 *
 * Two destinations share the content band:
 *
 * * a **view** — what the preset bar selects, at most 40 columns, one request;
 * * the **all-columns** mode — the served wide view, 116 keys, three chunked
 *   requests, reachable from a control beside the presets and never from them.
 *   It is not a preset because the API cannot answer it as one.
 */

import * as React from "react";

import { CommandPalette } from "@/filters/CommandPalette";
import { FilterDrawer } from "@/filters/FilterDrawer";
import { buildRegistry } from "@/filters/registry";
import { useFilterSchema } from "@/filters/schema";
import { useScreenState } from "@/filters/useScreenState";
import { AllColumnsPanel } from "@/grid/AllColumnsPanel";
import { ColumnPicker } from "@/grid/ColumnPicker";
import { ErrorBoundary } from "@/grid/ErrorBoundary";
import { ScreenPanel } from "@/grid/ScreenPanel";
import { splitCatalogue } from "@/grid/catalogue";
import { CLIENT_COLUMNS } from "@/grid/columnMeta";
import { sameColumns, useColumnSelection } from "@/grid/columnSelection";
import { GRID_REGION_ID } from "@/shell/ids";
import { navigate } from "@/shell/routes";

import { ScreenerHeader } from "./ScreenerHeader";
import type { ResolvedPreset } from "./columnPresets";
import { defaultColumns } from "./defaultColumns";
import { publishedFilters, useFiltersDocument } from "./filtersDocument";
import { useUserColumnPresets } from "./userPresets";

export interface ScreenerScreenProps {
  /** `stock` for `/screener`, `etf` for `/screener/etf`. */
  assetClass: "stock" | "etf";
}

export function ScreenerScreen({ assetClass }: ScreenerScreenProps) {
  const schema = useFilterSchema();
  // The filter document is one more boot read. It is memoised in its module, so
  // this hook, a StrictMode double-effect and a retry share one request; the
  // vocabulary it carries also names the values a chip prints.
  const vocabulary = useFiltersDocument();
  const registry = React.useMemo(
    () =>
      schema.schema
        ? buildRegistry(schema.schema, publishedFilters(vocabulary.doc))
        : null,
    [schema.schema, vocabulary.doc],
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

  const screen = useScreenState(
    registry,
    viewNames,
    // The link's filters are checked against both boot documents; see the
    // hook's third parameter.
    vocabulary.status !== "loading",
  );
  // The picker offers every column on screen — including the client-drawn row
  // number — so one vocabulary names everything the table can draw.
  const pickerColumns = React.useMemo(
    () => [...catalogue.keys, ...CLIENT_COLUMNS],
    [catalogue.keys],
  );
  /**
   * Each view's own columns, with the *first* view replaced by the product's
   * default set. The first view is what a reader who has chosen nothing sees;
   * every tab they press afterwards is a preset and brings its own columns.
   */
  const defaults = React.useMemo(() => {
    const byView = Object.fromEntries(
      catalogue.views.map((entry) => [entry.name, entry.columns]),
    ) as Record<string, readonly string[]>;
    const first = catalogue.views[0];
    if (first) byView[first.name] = defaultColumns(first.columns, catalogue.keys);
    return byView;
  }, [catalogue.views, catalogue.keys]);
  const columnSelection = useColumnSelection({
    viewNames,
    catalogue: pickerColumns,
    defaults,
    ready: screen.ready,
    // Declared after `useScreenState` on purpose: its writer runs first and
    // replaces the whole query string, and this hook's writer then merges the
    // `cols` parameters back in. See `useColumnSelection`.
    revision: screen.state,
  });
  // The reader's own column sets live in this browser; the screen only applies
  // the one they pick.
  const userPresets = useUserColumnPresets();
  const [wide, setWide] = React.useState(false);
  const [drawerOpen, setDrawerOpen] = React.useState(false);
  const [paletteOpen, setPaletteOpen] = React.useState(false);
  const [pickerOpen, setPickerOpen] = React.useState(false);
  const [focusKey, setFocusKey] = React.useState<string | null>(null);

  /**
   * Every ticker in the grid is a door to its own page.
   *
   * One handler for the row, Enter on a focused row, and the header's ticker
   * box: the client router writes the URL and the ticker page renders. The
   * href is the same path, so a middle-click or ⌘/Ctrl-click is a real new tab
   * the server also answers.
   */
  const openSymbol = React.useCallback((symbol: string) => {
    navigate(`/stock/${encodeURIComponent(symbol)}`);
  }, []);
  const symbolHref = React.useCallback(
    (symbol: string) => `/stock/${encodeURIComponent(symbol)}`,
    [],
  );

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

  const summaryColumns = active ? defaults[active.name] ?? active.columns : [];
  const currentColumns = active
    ? columnSelection.columnsFor(active.name) ?? summaryColumns
    : [];

  const closeDrawer = () => {
    setDrawerOpen(false);
    setFocusKey(null);
  };

  /**
   * One preset click, applied to the one state.
   *
   * A view preset is `?view=`; a preset with its own columns is `?cols=` for
   * the view already on screen. Neither touches `?f=`, which is the whole
   * promise of the bar: columns change, rows do not. A disabled tab never gets
   * here — its view is gone and the header refuses the click.
   */
  const applyPreset = (entry: ResolvedPreset) => {
    if (entry.disabled) return;
    if (entry.viewName !== null) {
      // The tab promises this view's columns; a custom selection stored for it
      // would silently show a different set under the same label.
      if (columnSelection.columnsFor(entry.viewName) !== null) {
        columnSelection.reset(entry.viewName);
      }
      screen.setView(entry.viewName);
      return;
    }
    if (entry.columns !== null) {
      columnSelection.select(screen.state.view, entry.columns);
      return;
    }
    columnSelection.reset(screen.state.view);
  };

  return (
    <>
      <ScreenerHeader
        title={assetClass === "etf" ? "ETF screener" : "Stock screener"}
        views={catalogue.views}
        catalogue={pickerColumns}
        view={active?.name ?? ""}
        columns={currentColumns}
        defaults={defaults}
        onSelectPreset={applyPreset}
        userPresets={userPresets}
        onApplyColumns={(keys) => columnSelection.select(screen.state.view, keys)}
        onOpenColumnPicker={() => setPickerOpen(true)}
        sort={screen.state.sort}
        onSortChange={screen.setSort}
        signal={screen.state.signal ?? null}
        onSignalChange={screen.setSignal}
        filters={screen.state.filters}
        onAddFilter={screen.addFilter}
        onClearFilters={screen.clearFilters}
        vocabulary={vocabulary}
        onSelectSymbol={openSymbol}
        wide={
          destination
            ? {
                label: destination.label,
                count: catalogue.keys.length,
                active: wideActive,
                onToggle: () => setWide((value) => !value),
              }
            : null
        }
      />

      <main className="flex min-h-0 flex-1 flex-col gap-2 p-3">
        <div
          id={GRID_REGION_ID}
          // The skip link's destination. `tabIndex={-1}` makes it a programmatic
          // focus target (and not a tab stop); the region is named so the
          // landing is announced as something ("Stock grid region") rather
          // than as an anonymous box. It is not a landmark in its own right
          // beyond `region`, and it is inside the one `<main>`.
          //
          // It is the whole content band: the table has no second pane beside
          // it and no panel to equalise against, so it takes every pixel the
          // page gives it.
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
                selectedSymbol={null}
                onActivate={openSymbol}
                onClear={() => undefined}
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
                onActivate={openSymbol}
                symbolHref={symbolHref}
                sort={screen.state.sort}
                signal={screen.state.signal ?? null}
                page={screen.state.page}
                onSortChange={screen.setSort}
                onPageChange={screen.setPage}
                filters={screen.state.filters}
                registry={registry}
                onRemoveFilter={screen.removeFilter}
                onClearFilters={screen.clearFilters}
                filterNotice={screen.notice}
                onDismissFilterNotice={screen.dismissDropped}
                columns={
                  columnSelection.columnsFor(active.name) ??
                  defaults[active.name] ??
                  active.columns
                }
                columnNotice={columnSelection.notice}
                onDismissColumnNotice={columnSelection.dismiss}
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

      {/* The column picker belongs to the header's Columns control, not to the
          table view or to a tab. One dialog, opened from the masthead. */}
      {active && pickerColumns.length > 0 ? (
        <ColumnPicker
          open={pickerOpen}
          onClose={() => setPickerOpen(false)}
          keys={pickerColumns}
          selected={currentColumns}
          onToggle={(key) => columnSelection.toggle(active.name, key)}
          onReset={() => columnSelection.reset(active.name)}
          custom={!sameColumns(currentColumns, defaults[active.name] ?? active.columns)}
          notice={columnSelection.notice}
        />
      ) : null}
    </>
  );
}
