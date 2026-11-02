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
  requestBody,
  nextSort,
  sortDirection,
  sortLabel,
  useScreens,
  type ScreenRow,
  type ViewDefinition,
} from "@/data/screens";

import { DataTable } from "./DataTable";
import { isClientColumn } from "./columnMeta";
import { PendingBar } from "./PendingBar";
import { pendingLabel, useDelayedFlag, useReducedMotion } from "./pending";
import { sameColumns } from "./columnSelection";
import { AlertDialog } from "./AlertDialog";
import { AddToDialog, type AddToInput } from "./AddToDialog";
import { CompareDialog } from "./CompareDialog";
import { ExportDialog, type ExportChoice } from "./ExportDialog";
import { Pager } from "./Pager";
import { TableToolbar, type AddToList } from "./TableToolbar";
import { VIEW_PANEL_ID, viewTabId } from "./ViewTabs";
import { addHolding, addSymbol, MAX_SYMBOLS, portfolioStore, watchlistStore } from "@/lists/storage";

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
  const [comparing, setComparing] = React.useState(false);
  const [alerting, setAlerting] = React.useState(false);
  const [exporting, setExporting] = React.useState<ExportChoice | null>(null);
  /** The list whose search-first box is open, or null. */
  const [addList, setAddList] = React.useState<AddToList | null>(null);
  /**
   * The checkbox selection, by symbol.
   *
   * The toolbar's three row actions (Add to, Compare, Set alert) each want one
   * named ticker, and the grid no longer has a docked panel to carry it. The
   * boxes are the only way a row is *selected*; a click on the row itself is a
   * navigation and never touches this list.
   */
  const [selected, setSelected] = React.useState<string[]>([]);

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
   * The ticker the toolbar's row actions act on: the first *selected* row in
   * the order the page draws it. A selection is a symbol, and the row it names
   * must still be on this page — the boxes are cleared whenever the page, the
   * view or the ordering changes.
   */
  const selectedSet = React.useMemo(() => new Set(selected), [selected]);
  const primary = React.useMemo(
    () => rows.find((row) => selectedSet.has(row.symbol))?.symbol ?? null,
    [rows, selectedSet],
  );

  const toggleSelected = React.useCallback((symbol: string) => {
    setSelected((current) =>
      current.includes(symbol)
        ? current.filter((entry) => entry !== symbol)
        : [...current, symbol],
    );
  }, []);

  const toggleAllSelected = React.useCallback(
    (checked: boolean) => {
      const pageSymbols = rows.map((row) => row.symbol);
      setSelected((current) =>
        checked
          ? [...new Set([...current, ...pageSymbols])]
          : current.filter((symbol) => !pageSymbols.includes(symbol)),
      );
    },
    [rows],
  );

  const clearSelected = React.useCallback(
    () => setSelected((current) => (current.length === 0 ? current : [])),
    [],
  );

  // A new page is a different set of rows: keeping a selection the reader can
  // no longer see would let Add to write a ticker that is not on screen.
  React.useEffect(() => {
    setSelected([]);
  }, [view.name, sort, signal, filterKey, pageIndex]);

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
   * The toolbar's Add to. A checked row is added straight away; nothing
   * selected means the reader has not said which ticker, so the search opens —
   * except for a portfolio, which needs a quantity and a cost from the box
   * whether or not a row was selected.
   */
  const addTo = (list: AddToList) => {
    if (list === "watchlist" && primary !== null) {
      const result = addTicker({ symbol: primary, list });
      setNotice(result.message);
      return;
    }
    setAddList(list);
  };

  /** The selected row, as the add box's pre-chosen ticker. */
  const addSeed = React.useMemo(() => {
    if (primary === null) return null;
    const row = rows.find((entry) => entry.symbol === primary);
    return {
      symbol: primary,
      name: row && typeof row["company"] === "string" ? row["company"] : "",
      exchange: row && typeof row["exchange"] === "string" ? row["exchange"] : "",
    };
  }, [primary, rows]);

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

  const exportBody = query ? requestBody(query) : {};

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
        selectedSymbol={primary}
        onCompare={() => setComparing(true)}
        onAlert={() => setAlerting(true)}
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
        // asked for a column the panel cannot carry, and it must interrupt —
        // the checkbox they just ticked is not going to stay ticked.
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
        onClear={clearSelected}
        rowTo={symbolHref}
        selection={{
          selected,
          onToggle: toggleSelected,
          onToggleAll: toggleAllSelected,
        }}
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

      <CompareDialog
        open={comparing}
        onClose={() => setComparing(false)}
        rows={rows as ScreenRow[]}
        columns={effective}
        viewLabel={view.label}
        selectedSymbol={primary}
      />

      <AlertDialog
        open={alerting}
        onClose={() => setAlerting(false)}
        symbol={primary ?? rows[0]?.symbol ?? null}
      />

      {addList ? (
        <AddToDialog
          open
          list={addList}
          initialSymbol={addSeed}
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
        region="us"
        sort={sort}
        apiBody={exportBody}
        scopeNote="The server writes this file from the saved screen for this market and ordering."
      />
    </section>
  );
}
