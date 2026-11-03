/**
 * One panel: toolbar, grid, pager, and the dialogs the toolbar opens.
 *
 * The data hook lives here so the table stays a pure renderer of rows it was
 * given. Two visible truths the panel owes the reader:
 *
 * * **where the numbers came from** — the count readout, the page readout, and
 *   whether the API's 60 KB budget cut anything;
 * * **whether the last refresh actually refreshed** — a `304` is a success, but
 *   it did not bring new rows, so the badge says so instead of pretending;
 * * **whether a page turn is still on its way** — the rows stay, the table
 *   fades, a rose bar sweeps the panel's top edge and the status line names the
 *   range being fetched. A three-second answer must look like work in progress,
 *   not like a click that did nothing.
 *
 * The chrome is one toolbar and one pager, both minimal: no card wrapper, one
 * border per control, centred on the content width. Everything the old toolbar
 * carried (a view label, filter and column buttons, sector and exchange menus,
 * a three-button sort, a refresh label) now lives in the header or in the
 * toolbar's own controls.
 */

import * as React from "react";

import {
  MAX_COLUMNS,
  PANEL_LIMIT,
  queryKey,
  nextSort,
  sortDirection,
  sortLabel,
  useScreens,
  type ViewDefinition,
} from "@/data/screens";

import { DataTable } from "./DataTable";
import { isClientColumn } from "./columnMeta";
import { PendingBar } from "./PendingBar";
import { pendingLabel, useDelayedFlag, useReducedMotion } from "./pending";
import { sameColumns } from "./columnSelection";
import { AlertDialog } from "./AlertDialog";
import { alertsStore } from "./alerts";
import { AddToDialog, type AddToInput } from "./AddToDialog";
import { ExportDialog, type ExportChoice } from "./ExportDialog";
import { Pager } from "./Pager";
import { TableToolbar, type AddToList } from "./TableToolbar";
import { VIEW_PANEL_ID, viewTabId } from "./ViewTabs";
import { addHolding, addSymbol, MAX_SYMBOLS, portfolioStore, watchlistStore } from "@/lists/storage";
import { COMPARE_MAX, compareHref } from "@/compare/symbols";
import { navigate } from "@/shell/routes";

import type { ActiveFilter } from "@/filters/types";
import type { FilterRegistry } from "@/filters/registry";
import { ChipBar } from "@/filters/ChipBar";
import { filtersSignature, toWire } from "@/filters/state";

export interface ScreenPanelProps {
  view: ViewDefinition;
  /** `etf` on `/screener/etf`; every request carries it. */
  assetClass?: "stock" | "etf";
  /**
   * A row was opened: the screener is a door to `/stock/{symbol}`, never a
   * docked panel. The caller owns the navigation so this stays a renderer of
   * rows and requests.
   */
  onActivate?: (symbol: string) => void;
  /**
   * The ticker page's route path for a row (`/stock/AAPL`).
   *
   * The identity lane becomes a real anchor with this href, so middle-click
   * and ⌘/Ctrl-click open a new tab through the browser rather than a
   * synthetic window.
   */
  symbolHref?: (symbol: string) => string;
  /** Test seam; production measures the scroll box. */
  containerWidth?: number;
  /**
   * The ordering the shell's Order by select (or a header press) chose: a
   * catalogue key, `-key` for lowest first. One string, one state.
   */
  sort?: string;
  /**
   * The named screen the request asks the API to compile, or null.
   *
   * A signal is not one more predicate: it replaces the where-clause and the
   * ordering with its own, so it travels beside `criteria` rather than inside
   * it. It is part of the page key for the same reason a sort is — a new signal
   * is a new screen, at the first page.
   */
  signal?: string | null;
  page?: number;
  /** A grid header was pressed: the shell's one sort state changes. */
  onSortChange?: (sort: string) => void;
  onPageChange?: (page: number) => void;
  /** The one filter state; this panel only renders it and sends it. */
  filters?: readonly ActiveFilter[];
  registry?: FilterRegistry | null;
  onRemoveFilter?: (id: string) => void;
  onClearFilters?: () => void;
  filterNotice?: string | null;
  onDismissFilterNotice?: () => void;
  /** The selection to draw; the view's own columns when omitted. */
  columns?: readonly string[];
  /** The cap sentence from the shell, shown above the grid and in the picker. */
  columnNotice?: string | null;
  onDismissColumnNotice?: () => void;
}

function timeOf(iso: string): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return `${date.toISOString().slice(11, 19)}Z`;
}

export function ScreenPanel({
  view,
  assetClass = "stock",
  onActivate,
  symbolHref,
  containerWidth,
  sort: sortProp,
  signal,
  page: pageProp,
  onSortChange,
  onPageChange,
  filters,
  registry,
  onRemoveFilter,
  onClearFilters,
  filterNotice,
  onDismissFilterNotice,
  columns,
  columnNotice,
  onDismissColumnNotice,
}: ScreenPanelProps) {
  const activeFilters = filters ?? [];
  const filterKey = filtersSignature(activeFilters);
  const sort = sortProp ?? "market_cap";

  /** Rows per request: the toolbar's 20 / 50 / 100. */
  const [limit, setLimit] = React.useState<number>(PANEL_LIMIT);
  /** Auto-refresh cadence, or null when off. */
  const [autoRefreshMs, setAutoRefreshMs] = React.useState<number | null>(null);
  const [notice, setNotice] = React.useState<string | null>(null);
  const [alerting, setAlerting] = React.useState(false);
  const [exporting, setExporting] = React.useState<ExportChoice | null>(null);
  /** The list whose search-first box is open, or null. */
  const [addList, setAddList] = React.useState<AddToList | null>(null);
  /**
   * The row the pointer is over or the keyboard is on.
   *
   * It is the one subject of the toolbar's Add to / Set alert. There is no
   * selection list outside compare mode: a row is never ticked, and a symbol
   * that has left the page is ignored by `pointedSymbol` below.
   */
  const [pointed, setPointed] = React.useState<string | null>(null);
  /** Compare mode: boxes exist only while this is true. */
  const [compareMode, setCompareMode] = React.useState(false);
  /** The symbols ticked in compare mode, capped at COMPARE_MAX. */
  const [compareSelected, setCompareSelected] = React.useState<string[]>([]);
  /** The symbol the alert form opens with; the row or page always names it. */
  const [alertSymbol, setAlertSymbol] = React.useState<string | null>(null);

  /**
   * Per-screen page state.
   *
   * A page belongs to the screen it was chosen on. Keeping the number in a
   * plain `useState` and resetting it in an effect meant the commit that
   * switched view still carried the old page — the panel asked for the new
   * view at the previous view's offset, then corrected itself: one request too
   * many. The page is therefore *derived* against the state key it belongs to,
   * so a view (or sort, or filter) switch reads page 0 in the very render that
   * changed the view, and exactly one request is sent.
   */
  const pageKey = `${view.name}\u0000${sort}\u0000${signal ?? ""}\u0000${filterKey}\u0000${limit}`;
  const [cursor, setCursor] = React.useState<{ key: string; page: number }>({ key: pageKey, page: 0 });
  const internalPage = cursor.key === pageKey ? cursor.page : 0;
  const pageIndex = pageProp ?? internalPage;

  /**
   * The columns this panel may ask for.
   *
   * `logo` is removed here, once, at the boundary every request crosses. The
   * identity lane draws the mark from `/logos/{SYMBOL}` (see `logo.ts`); a row
   * that also carried the monogram as a base64 data URI would spend ~640 B per
   * row — 64 KB per hundred rows — against the API's 60 KB panel budget, and
   * the API pays for it by dropping rows.
   */
  const chosen = React.useMemo(
    () =>
      (columns && columns.length > 0 ? columns : view.columns).filter(
        // `logo` is drawn from `/logos/{SYMBOL}`, never requested.
        (key) => key !== "logo",
      ),
    [columns, view.columns],
  );
  /**
   * What the request asks for.
   *
   * The row number is drawn from the page offset and the ticker from the
   * pinned lane: neither is a catalogue key, and sending one would be a 422.
   * Everything else the reader selected travels as-is.
   */
  const selection = React.useMemo(
    () => chosen.filter((key) => !isClientColumn(key)),
    [chosen],
  );
  const effective = React.useMemo(
    () => (sameColumns(selection, view.columns) ? view.columns : [...selection]),
    [selection, view.columns],
  );
  const panelView = React.useMemo<ViewDefinition>(
    () => ({ ...view, columns: [...chosen] }),
    [chosen, view],
  );

  const supported = effective.length > 0 && effective.length <= MAX_COLUMNS;

  const query = React.useMemo(
    () =>
      supported
        ? {
            view: panelView.name,
            // What the API is asked for (never the client-drawn lanes), while
            // the table below draws every column the reader selected.
            columns: effective,
            limit,
            offset: pageIndex * limit,
            sort,
            assetClass,
            filters: toWire(activeFilters),
            signal: signal ?? null,
          }
        : null,
    [supported, panelView.name, effective, pageIndex, limit, sort, assetClass, filterKey, signal],
  );

  const screens = useScreens(query);
  const refresh = screens.refresh;

  // F8: a view switch is answered by an effect, so for one committed frame the
  // hook still holds the previous view's rows. They may never be painted under
  // this view's headers — a switch shows the loading state, and the old rows
  // are handed to the table only while the *query* is still this view's.
  const sameView = screens.view === view.name;
  const rows = sameView ? screens.rows : [];
  const meta = sameView ? screens.meta : null;
  const rowLimit = meta?.limit ?? limit;
  const total = meta?.total ?? 0;
  const pageCount = Math.max(1, Math.ceil(total / rowLimit));
  const status = sameView && screens.status !== "idle" ? screens.status : "loading";
  const firstRow = rows.length > 0 ? (meta?.offset ?? 0) + 1 : 0;

  /**
   * The ticker the toolbar's row actions act on: the row the pointer is over
   * or the keyboard is on, and only while that row is still on this page.
   *
   * A page turn drops the old symbol by derivation, not by an effect: there is
   * no selection list to reset, and no frame in which the toolbar names a row
   * the reader can no longer see.
   */
  const pointedSymbol = React.useMemo(
    () => (pointed !== null && rows.some((row) => row.symbol === pointed) ? pointed : null),
    [pointed, rows],
  );

  /**
   * The symbols already on either list, for the row stars.
   *
   * `useSyncExternalStore` is the whole truth: a write from the row, the
   * ticker page, the watchlist page or another tab notifies every subscriber,
   * so a star fills the moment the symbol is stored and empties when it is
   * removed. No local "just added" flag can drift from storage.
   */
  const watchlist = React.useSyncExternalStore(
    watchlistStore.subscribe,
    watchlistStore.read,
    watchlistStore.read,
  );
  const holdings = React.useSyncExternalStore(
    portfolioStore.subscribe,
    portfolioStore.read,
    portfolioStore.read,
  );
  const starredSymbols = React.useMemo(
    () => [...watchlist, ...holdings.map((holding) => holding.symbol)],
    [watchlist, holdings],
  );

  /**
   * The saved alerts, per symbol, for the row bells.
   *
   * Same rule as the stars: storage is the truth, and the panel subscribes
   * once for the page instead of every row opening the key. The dialog writes
   * this store, so a save updates the row behind it without a reload.
   */
  const alerts = React.useSyncExternalStore(
    alertsStore.subscribe,
    alertsStore.read,
    alertsStore.read,
  );
  const alertCounts = React.useMemo(() => {
    const counts = new Map<string, number>();
    for (const alert of alerts) {
      counts.set(alert.symbol, (counts.get(alert.symbol) ?? 0) + 1);
    }
    return counts;
  }, [alerts]);

  const clearPointed = React.useCallback(() => setPointed(null), []);

  /**
   * Is a selection in flight? — a different question from `status`, which is
   * the *table's* state. It is true from the click to the settle: a `304`
   * settles it, and an aborted request settles it too.
   */
  const currentKey = React.useMemo(() => (query ? queryKey(query) : null), [query]);
  const isFetching =
    query !== null &&
    (screens.status === "loading" ||
      (screens.status !== "error" && screens.queryKey !== currentKey));

  const pending = isFetching && status !== "error";
  const showPending = useDelayedFlag(pending);
  const reducedMotion = useReducedMotion();
  /** Only rows that exist can be dimmed; a first load keeps its skeleton. */
  const stale = showPending && rows.length > 0;

  /**
   * The page change itself. Unguarded, because the reconciliation effect below
   * is not a click: a page named by the URL that cannot exist must still be
   * corrected on the answer that proves it.
   */
  const goToPage = (next: number) => {
    if (pageProp === undefined) setCursor({ key: pageKey, page: next });
    onPageChange?.(next);
  };

  const changePage = (next: number) => {
    // The button is disabled while a request is out, but a synthetic click (or
    // a double-tap that lands inside one commit) must not queue a second
    // request either.
    if (isFetching) return;
    goToPage(next);
  };

  const changeLimit = (next: number) => {
    const size = Math.max(1, Math.min(100, Math.floor(next)));
    setLimit(size);
    // A new page size is a new set of pages: the first page is the only one
    // whose offset means the same thing at both sizes.
    setCursor({
      key: `${view.name}\u0000${sort}\u0000${signal ?? ""}\u0000${filterKey}\u0000${size}`,
      page: 0,
    });
    onPageChange?.(0);
  };

  const label =
    showPending
      ? pendingLabel({
          // A re-ask of the page already on screen is a refresh; anything else
          // names the rows that are on their way.
          refreshing: screens.queryKey === currentKey,
          pageIndex,
          limit: rowLimit,
          total: meta?.total ?? null,
        })
      : null;

  // A page is a request, and the URL may name one that cannot exist (a shared
  // link, a stale tab, a filter that shrank the result set). `parseScreenState`
  // bounds the input to MAX_PAGE; this reconciles the page against the answer's
  // own total once there is one, so the position never reads "999999/56" with a
  // dead Next.
  const maxPage = Math.max(0, pageCount - 1);
  React.useEffect(() => {
    if (!meta) return;
    if (pageIndex <= maxPage) return;
    goToPage(maxPage);
    // `goToPage` closes over the controlled/uncontrolled switch, not over
    // `pageIndex`; re-running on a new page is what stops the loop at maxPage.
  }, [meta, pageIndex, maxPage]);

  // Auto refresh: one timer, cleared by the select, by a view change or by the
  // panel unmounting. `refresh` is stable, so the interval does not restart on
  // every render.
  React.useEffect(() => {
    if (autoRefreshMs === null) return;
    const timer = window.setInterval(() => refresh(), autoRefreshMs);
    return () => window.clearInterval(timer);
  }, [autoRefreshMs, refresh]);

  /**
   * One add, to one list, through the store that owns it.
   *
   * The watchlist is a symbol list, so `addSymbol` decides what a legal ticker
   * is and whether it is already there. The portfolio is holdings — a symbol,
   * a quantity and a cost — so it goes through `addHolding`, the same validator
   * the Portfolio page uses; the dialog supplies the two numbers because a
   * holding invented at cost 0 would read as a 100% gain.
   */
  const addTicker = React.useCallback((input: AddToInput) => {
    if (input.list === "watchlist") {
      const edit = addSymbol(watchlistStore.read(), input.symbol, MAX_SYMBOLS);
      if (!edit.ok) return { ok: false, message: edit.error };
      watchlistStore.write(edit.symbols);
      return { ok: true, message: `${input.symbol} is on the watchlist.` };
    }
    const edit = addHolding(portfolioStore.read(), {
      symbol: input.symbol,
      quantity: input.quantity === undefined ? "" : String(input.quantity),
      cost: input.cost === undefined ? "" : String(input.cost),
    });
    if (!edit.ok) return { ok: false, message: edit.error };
    portfolioStore.write(edit.holdings);
    return { ok: true, message: `${input.symbol} is in the portfolio.` };
  }, []);

  /**
   * A row's star: both choices write immediately, with no dialog detour.
   *
   * A watchlist row is one symbol. A portfolio holding needs numbers, so the
   * star opens the holding with one share at the row's last price — a real
   * basis the reader can edit on the Portfolio page, never a zero-cost holding
   * that would read as a 100% gain. The star's filled state comes from the
   * store subscription, not from a local flag.
   */
  const addRowTo = React.useCallback(
    (symbol: string, list: AddToList) => {
      if (list === "watchlist") {
        const result = addTicker({ symbol, list });
        setNotice(result.message);
        return;
      }
      const raw = rows.find((row) => row.symbol === symbol)?.["price"];
      const price = typeof raw === "number" && Number.isFinite(raw) && raw >= 0 ? raw : 0;
      const edit = addHolding(portfolioStore.read(), {
        symbol,
        quantity: "1",
        cost: String(price),
      });
      if (!edit.ok) {
        setNotice(edit.error);
        return;
      }
      portfolioStore.write(edit.holdings);
      setNotice(`${symbol} is in the portfolio.`);
    },
    [addTicker, rows],
  );

  /**
   * The toolbar's Add to. With a row hovered or focused it acts on that row;
   * with nothing pointed at, choosing a list opens the ticker search — the
   * toolbar is never a dead button.
   */
  const addTo = React.useCallback(
    (list: AddToList) => {
      if (pointedSymbol !== null) {
        addRowTo(pointedSymbol, list);
        return;
      }
      setAddList(list);
    },
    [pointedSymbol, addRowTo],
  );

  /** Enter compare mode: the grid starts drawing its transient boxes. */
  const openCompare = React.useCallback(() => {
    setNotice(null);
    setCompareMode(true);
  }, []);

  /** One tick, never past ten: the extra press is ignored, not queued. */
  const toggleCompare = React.useCallback((symbol: string) => {
    setCompareSelected((current) =>
      current.includes(symbol)
        ? current.filter((entry) => entry !== symbol)
        : current.length >= COMPARE_MAX
          ? current
          : [...current, symbol],
    );
  }, []);

  const cancelCompare = React.useCallback(() => {
    setCompareMode(false);
    setCompareSelected([]);
  }, []);

  const confirmCompare = React.useCallback(() => {
    if (compareSelected.length === 0) return;
    navigate(compareHref(compareSelected));
    setCompareMode(false);
    setCompareSelected([]);
  }, [compareSelected]);

  // Escape leaves compare mode from anywhere on the panel. A dialog's own
  // Escape stops propagation before this listener, so it closes the dialog
  // first; the row's Escape only clears its cursor and does not stop the event.
  React.useEffect(() => {
    if (!compareMode) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") cancelCompare();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [compareMode, cancelCompare]);

  // A new page is a different set of rows: a tick the reader can no longer see
  // must not travel into the comparison.
  React.useEffect(() => {
    setCompareSelected([]);
  }, [view.name, pageIndex, sort, filterKey]);

  const alertOn = React.useCallback((symbol: string | null) => {
    setAlertSymbol(symbol);
    setAlerting(true);
  }, []);

  const pointRow = React.useCallback((symbol: string | null) => setPointed(symbol), []);

  const rowActions = React.useMemo(
    () => ({ onAddTo: addRowTo, onAlert: alertOn }),
    [addRowTo, alertOn],
  );

  const panel = {
    role: "tabpanel" as const,
    id: VIEW_PANEL_ID,
    "aria-labelledby": viewTabId(view.name),
  };

  if (!supported) {
    return (
      <section
        {...panel}
        className="flex flex-col rounded-md border border-line bg-surface"
      >
        <div role="alert" className="m-3 rounded-md border border-line border-l-2 border-l-alert bg-surface-raised p-4">
          <p className="text-ui font-medium text-fg">This selection is too wide for one panel</p>
          <p className="mt-1 text-data text-fg-muted">
            {view.label} asks for {effective.length} columns; the API accepts at most{" "}
            {MAX_COLUMNS} per selection. The wide catalogue still exists — it is the
            all-columns destination, not a tab.
          </p>
        </div>
      </section>
    );
  }

  return (
    <section
      {...panel}
      data-testid="table-frame"
      // The card is the content band now: one frame, full width, and the page
      // owns the vertical scroll. Nothing scrolls this box.
      className="relative flex min-h-0 flex-1 flex-col overflow-hidden rounded-md border border-line bg-surface"
    >
      {showPending ? <PendingBar reducedMotion={reducedMotion} /> : null}

      <TableToolbar
        pageIndex={pageIndex}
        total={total}
        pageCount={pageCount}
        rowsPerPage={limit}
        onRowsPerPage={changeLimit}
        onPrev={() => changePage(Math.max(0, pageIndex - 1))}
        onNext={() => changePage(Math.min(pageCount - 1, pageIndex + 1))}
        canPrev={pageIndex > 0 && !isFetching}
        canNext={pageIndex + 1 < pageCount && !isFetching}
        onRefresh={refresh}
        refreshing={isFetching}
        autoRefreshMs={autoRefreshMs}
        onAutoRefresh={(ms) => {
          setAutoRefreshMs(ms);
          setNotice(
            ms === null ? "Auto refresh off." : `Auto refresh every ${Math.round(ms / 60_000)} min.`,
          );
        }}
        onAddTo={addTo}
        selectedSymbol={pointedSymbol}
        onCompare={openCompare}
        compareMode={compareMode}
        compareCount={compareSelected.length}
        compareMax={COMPARE_MAX}
        onCompareDone={confirmCompare}
        onCompareCancel={cancelCompare}
        onAlert={() => {
          if (pointedSymbol === null) {
            // The alert names one ticker and has no search of its own: without
            // a pointed row there is nothing to prefill, so say that rather
            // than open a form with an invented symbol.
            setNotice("Hover a row or open a ticker first — an alert needs a symbol.");
            return;
          }
          alertOn(pointedSymbol);
        }}
        onExport={(choice) => setExporting(choice)}
        cached={screens.fromCache && sameView}
        statusText={label ?? ""}
        notice={notice}
      />

      {sameView && screens.revalidated ? (
        <p
          data-testid="stale-badge"
          title={`The server answered 304 Not Modified at ${timeOf(screens.checkedAt ?? "")}: these rows are unchanged, not re-fetched.`}
          className="sr-only"
        >
          not refreshed · 304
        </p>
      ) : null}

      {registry ? (
        <ChipBar
          registry={registry}
          filters={activeFilters}
          onRemove={onRemoveFilter ?? (() => undefined)}
          onClear={onClearFilters ?? (() => undefined)}
          notice={filterNotice}
          onDismissNotice={onDismissFilterNotice}
        />
      ) : null}

      {columnNotice !== null && columnNotice !== undefined ? (
        // An alert, not a status: this sentence appears only when the reader
        // asked for a column the panel cannot carry, and a quiet notice would
        // leave them believing the column arrived.
        <div
          role="alert"
          data-testid="column-notice"
          className="flex shrink-0 items-start justify-between gap-3 border-b border-line bg-surface-raised px-3 py-1.5"
        >
          <p className="text-data text-alert">{columnNotice}</p>
          <button
            type="button"
            onClick={onDismissColumnNotice}
            aria-label="Dismiss column notice"
            className="shrink-0 rounded-sm border border-line px-1 text-label text-fg-muted hover:text-fg"
          >
            ✕
          </button>
        </div>
      ) : null}

      {meta?.truncated ? (
        // Only said when it is true: the byte budget is an implementation
        // detail until the page is short because of it.
        <p
          data-testid="truncated-notice"
          className="shrink-0 border-b border-line bg-surface-raised px-3 py-1 text-data text-alert"
        >
          The API's byte budget cut rows from this page.
        </p>
      ) : null}

      <DataTable
        view={panelView}
        rows={rows}
        status={status}
        error={sameView ? screens.error : null}
        revalidated={sameView && screens.revalidated}
        pageIndex={pageIndex}
        pageSize={rowLimit}
        rowCount={total}
        sort={sort}
        onSort={(key) => onSortChange?.(nextSort(sort, key))}
        caption={
          `${view.label}: ` +
          (rows.length === 0
            ? "no rows on this page"
            : `rows ${firstRow}–${firstRow + rows.length - 1} of ${total.toLocaleString("en-US")}`) +
          (signal
            ? `, ordered by the “${signal}” signal.`
            : `, sorted by ${sortLabel(sort)}, ${
                sortDirection(sort) === "desc" ? "highest first" : "lowest first"
              }.`)
        }
        onRetry={refresh}
        onActivate={onActivate}
        onClear={clearPointed}
        rowTo={symbolHref}
        rowActions={rowActions}
        onRowPoint={pointRow}
        starredSymbols={starredSymbols}
        alertCounts={alertCounts}
        compareSelection={
          compareMode
            ? { selected: compareSelected, onToggle: toggleCompare, max: COMPARE_MAX }
            : undefined
        }
        containerWidth={containerWidth}
        stale={stale}
        busy={isFetching}
      />

      <Pager
        pageIndex={pageIndex}
        pageCount={pageCount}
        onPage={changePage}
        busy={isFetching}
        onPrev={() => changePage(Math.max(0, pageIndex - 1))}
        onNext={() => changePage(Math.min(pageCount - 1, pageIndex + 1))}
        canPrev={pageIndex > 0}
        canNext={pageIndex + 1 < pageCount}
      />

      <AlertDialog
        open={alerting}
        onClose={() => setAlerting(false)}
        symbol={alertSymbol}
      />

      {addList ? (
        <AddToDialog
          open
          list={addList}
          onClose={() => setAddList(null)}
          onAdd={(input) => {
            const result = addTicker(input);
            setNotice(result.message);
            return result;
          }}
        />
      ) : null}

      <ExportDialog
        open={exporting !== null}
        onClose={() => setExporting(null)}
        choice={exporting ?? "csv"}
        columns={selection}
        region="us"
      />
    </section>
  );
}
