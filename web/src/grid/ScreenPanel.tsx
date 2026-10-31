/**
 * One panel: toolbar, grid, footer. The data hook lives here so the table
 * stays a pure renderer of rows it was given.
 *
 * Two visible truths the toolbar owes the reader:
 *
 * * **where the numbers came from** — `as of`, how many matched, how many this
 *   page shows, and whether the API's 60 KB budget cut anything;
 * * **whether the last refresh actually refreshed** — a `304` is a success, but
 *   it did not bring new rows, so it says so instead of pretending;
 * * **whether a page turn is still on its way** — the rows stay, the table
 *   fades, a rose bar sweeps the panel's top edge and the footer names the
 *   range being fetched. A three-second answer must look like work in
 *   progress, not like a click that did nothing.
 */

import * as React from "react";

import {
  MAX_COLUMNS,
  PANEL_LIMIT,
  SORTS,
  queryKey,
  useScreens,
  type ScreenSort,
  type ViewDefinition,
} from "@/data/screens";

import { DataTable } from "./DataTable";
import { ColumnPicker } from "./ColumnPicker";
import { PendingBar } from "./PendingBar";
import { pendingLabel, useDelayedFlag, useReducedMotion } from "./pending";
import { sameColumns } from "./columnSelection";
import { SectorChip, SectorSelect, type SectorSource } from "./SectorSelect";
import { activeSector, isSectorChoice, sectorNamesFromRows } from "./sectors";
import { ExchangeChip, ExchangeSelect, type ExchangeSource } from "./ExchangeSelect";
import {
  activeExchanges,
  exchangeVenuesFromRows,
  isExchangeChoice,
  type VenueBucket,
} from "./exchanges";
import { VIEW_PANEL_ID, viewTabId } from "./ViewTabs";

import type { ActiveFilter } from "@/filters/types";
import type { FilterRegistry } from "@/filters/registry";
import { ChipBar } from "@/filters/ChipBar";
import { filtersSignature, toWire } from "@/filters/state";

export interface ScreenPanelProps {
  view: ViewDefinition;
  selectedSymbol: string | null;
  onActivate: (symbol: string) => void;
  onClear: () => void;
  /** Test seam; production measures the scroll box. */
  containerWidth?: number;
  /** Controlled when the shell keeps sort/page in the URL. */
  sort?: ScreenSort;
  page?: number;
  onSortChange?: (sort: ScreenSort) => void;
  onPageChange?: (page: number) => void;
  /** The one filter state; this panel only renders it and sends it. */
  filters?: readonly ActiveFilter[];
  registry?: FilterRegistry | null;
  onRemoveFilter?: (id: string) => void;
  onClearFilters?: () => void;
  onOpenFilters?: () => void;
  filterNotice?: string | null;
  onDismissFilterNotice?: () => void;
  /** Every key the schema serves; the picker offers exactly these. */
  catalogue?: readonly string[];
  /** The selection to draw; the view's own columns when omitted. */
  columns?: readonly string[];
  onToggleColumn?: (key: string) => void;
  onResetColumns?: () => void;
  /** The cap sentence from the shell, shown above the grid and in the picker. */
  columnNotice?: string | null;
  onDismissColumnNotice?: () => void;
  /**
   * The market's sector labels, from the shell's `GET /market/breadth` read.
   *
   * The shell owns that read so this panel stays a pure renderer of what it was
   * given (and so a panel test never spends a request on a menu). When the list
   * is empty the panel derives one from the `sector` cells of its own rows and
   * says so — a page is not the market.
   */
  sectorOptions?: readonly string[];
  /** Whether those names are the market's, still loading, or unavailable. */
  sectorSource?: "breadth" | "loading" | "unavailable";
  /**
   * Choosing a sector, or clearing one (`null`).
   *
   * Supplying this is what mounts the control: the panel writes `sec eq name`
   * into the *one* filter list through this callback, so the chip bar, the URL
   * and the request body all keep reading a single state.
   */
  onSectorChange?: (name: string | null) => void;
  /**
   * The market's venues, from the shell's `GET /market/exchanges` read.
   *
   * Same split as `sectorOptions`: the shell owns the one request, the panel
   * renders what it was given, and an empty list falls back to the `exchange`
   * cells of the page — labelled as such, because a page is not the market.
   */
  exchangeOptions?: readonly VenueBucket[];
  /** Whether that list is the market's, still loading, or unavailable. */
  exchangeSource?: "market" | "loading" | "unavailable";
  /**
   * Choosing a set of exchange codes, or clearing it (`[]`).
   *
   * Supplying this is what mounts the control: the panel writes one `exch`
   * predicate (`eq` for one code, `in` for many) into the *one* filter list
   * through this callback, so the chip, the URL and the request body all keep
   * reading a single state.
   */
  onExchangeChange?: (codes: string[]) => void;
}

const SORT_LABELS: Record<ScreenSort, string> = {
  market_cap: "Market cap",
  turnover: "Turnover",
  gainers: "Gainers",
};

function timeOf(iso: string): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return `${date.toISOString().slice(11, 19)}Z`;
}

export function ScreenPanel({
  view,
  selectedSymbol,
  onActivate,
  onClear,
  containerWidth,
  sort: sortProp,
  page: pageProp,
  onSortChange,
  onPageChange,
  filters,
  registry,
  onRemoveFilter,
  onClearFilters,
  onOpenFilters,
  filterNotice,
  onDismissFilterNotice,
  catalogue,
  columns,
  onToggleColumn,
  onResetColumns,
  columnNotice,
  onDismissColumnNotice,
  sectorOptions,
  sectorSource,
  onSectorChange,
  exchangeOptions,
  exchangeSource,
  onExchangeChange,
}: ScreenPanelProps) {
  const activeFilters = filters ?? [];
  const filterKey = filtersSignature(activeFilters);
  const [internalSort, setInternalSort] = React.useState<ScreenSort>("market_cap");
  const sort = sortProp ?? internalSort;
  const [pickerOpen, setPickerOpen] = React.useState(false);

  /**
   * Per-view page state.
   *
   * A page belongs to the screen it was chosen on. Keeping the number in a
   * plain `useState` and resetting it in an effect meant the commit that
   * switched view still carried the old page — the panel asked for the new
   * view at the previous view's offset, then corrected itself: one request too
   * many, and the auditor saw it. The page is therefore *derived* against the
   * state key it belongs to, so a view (or sort, or filter) switch reads page 0
   * in the very render that changed the view, and exactly one request is sent.
   */
  const pageKey = `${view.name}\u0000${sort}\u0000${filterKey}`;
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
   * the API pays for it by dropping rows. A link that names `logo` therefore
   * asks for nothing: the lane already has its mark.
   */
  const selection = React.useMemo(
    () => (columns && columns.length > 0 ? columns : view.columns).filter((key) => key !== "logo"),
    [columns, view.columns],
  );
  const effective = React.useMemo(
    () => (sameColumns(selection, view.columns) ? view.columns : [...selection]),
    [selection, view.columns],
  );
  const custom = !sameColumns(effective, view.columns);
  const panelView = React.useMemo<ViewDefinition>(
    () => (custom ? { ...view, columns: [...effective] } : view),
    [custom, effective, view],
  );

  const supported = effective.length > 0 && effective.length <= MAX_COLUMNS;

  const query = React.useMemo(
    () =>
      supported
        ? {
            view: panelView.name,
            columns: panelView.columns,
            limit: PANEL_LIMIT,
            offset: pageIndex * PANEL_LIMIT,
            sort,
            filters: toWire(activeFilters),
          }
        : null,
    [supported, panelView.name, panelView.columns, pageIndex, sort, filterKey],
  );

  const screens = useScreens(query);

  // F8: a view switch is answered by an effect, so for one committed frame the
  // hook still holds the previous view's rows. They may never be painted under
  // this view's headers — a switch shows the loading state, and the old rows
  // are handed to the table only while the *query* is still this view's (a
  // page turn, a sort change), where they are the same screen's rows.
  const sameView = screens.view === view.name;
  const rows = sameView ? screens.rows : [];
  const meta = sameView ? screens.meta : null;
  const limit = meta?.limit ?? PANEL_LIMIT;
  const total = meta?.total ?? 0;
  const pageCount = Math.max(1, Math.ceil(total / limit));
  const first = rows.length > 0 ? (meta?.offset ?? 0) + 1 : 0;
  const last = (meta?.offset ?? 0) + rows.length;
  const status = sameView && screens.status !== "idle" ? screens.status : "loading";

  /**
   * Is a selection in flight? — a different question from `status`, which is
   * the *table's* state.
   *
   * `status` is "loading" only once the hook's effect has run and blanked its
   * own answer; between the click that turned the page and that effect there
   * is one committed frame where the hook still says "ready" while a request
   * is already owed. The rows are not the answer to the current query in that
   * frame, and the paging controls must already be shut — otherwise a fast
   * second click queues page 3 behind page 2.
   *
   * So the state is derived from two facts that cannot drift, because neither
   * is stored here: the hook is loading, or the rows it holds answer a query
   * other than the one this render asks for. An error is excluded on purpose:
   * a failed request has settled, and a bar that keeps sweeping over a failure
   * is a lie the reader would wait on forever.
   *
   * It is true from the click to the settle — a `304` settles it (the hook
   * returns to "ready"), and an aborted request settles it too (the abort
   * belongs to a query that is no longer current, and the query that replaced
   * it owns the flag from its own first render).
   */
  const currentKey = React.useMemo(() => (query ? queryKey(query) : null), [query]);
  const isFetching =
    query !== null &&
    (screens.status === "loading" ||
      (screens.status !== "error" && screens.queryKey !== currentKey));

  /**
   * The chrome waits; the controls do not.
   *
   * `isFetching` shuts Next/Prev on the click itself. The bar, the fade and
   * the sentence are held back by `PENDING_FLASH_DELAY_MS` so a fast answer
   * never paints them — see `pending.ts` for the threshold and why 120 ms.
   */
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
    // request either. The target page is derived from `pageIndex`, so two
    // clicks in one batch ask for the same page and collapse into one request.
    if (isFetching) return;
    goToPage(next);
  };

  const changeSort = (next: ScreenSort) => {
    // A sort is not queued behind the page turn: it replaces it, and the hook
    // aborts the request it supersedes.
    if (sortProp === undefined) {
      setInternalSort(next);
      setCursor({ key: pageKey, page: 0 });
    }
    onSortChange?.(next);
  };

  const label =
    showPending
      ? pendingLabel({
          // A re-ask of the page already on screen is a refresh; anything else
          // names the rows that are on their way.
          refreshing: screens.queryKey === currentKey,
          pageIndex,
          limit,
          total: meta?.total ?? null,
        })
      : null;

  // --- the sector menu ----------------------------------------------------
  //
  // The menu's population is the shell's breadth read: 21 labels counted over
  // the whole universe. When that read has not landed (or failed) the fallback
  // is the `sector` cell of the rows on this page, and the control prints which
  // of the two it is showing — a page is not the market, and the reader must be
  // able to tell.
  const sectorCapable = onSectorChange !== undefined;
  const offeredSectors = React.useMemo(
    () => (sectorOptions ?? []).filter((name) => typeof name === "string" && name.trim().length > 0),
    [sectorOptions],
  );
  const derivedSectors = React.useMemo(() => sectorNamesFromRows(rows), [rows]);
  const sectorNames = offeredSectors.length > 0 ? offeredSectors : derivedSectors;
  const sectorValue = sectorCapable ? activeSector(activeFilters) : null;
  const sectorPopulation: SectorSource =
    offeredSectors.length > 0
      ? "breadth"
      : derivedSectors.length > 0
        ? "rows"
        : sectorSource === "loading"
          ? "loading"
          : "none";

  // --- the exchange menu -------------------------------------------------
  //
  // Same five expressions as the sector menu, for the same reason: the
  // population is the market's (`GET /market/exchanges`), the fallback is the
  // page's own `exchange` cells, and the control says which one it is showing.
  // The control mounts only when a callback was passed, so every panel test
  // that does not wire one keeps its toolbar byte-identical — and no panel
  // spends a request on a menu it does not draw.
  const exchangeCapable = onExchangeChange !== undefined;
  const offeredVenues = React.useMemo(
    () =>
      (exchangeOptions ?? []).filter(
        (venue) => typeof venue?.code === "string" && venue.code.trim().length > 0,
      ),
    [exchangeOptions],
  );
  const derivedVenues = React.useMemo(() => exchangeVenuesFromRows(rows), [rows]);
  const venues = offeredVenues.length > 0 ? offeredVenues : derivedVenues;
  const exchangeValue = exchangeCapable ? activeExchanges(activeFilters) : [];
  const exchangePopulation: ExchangeSource =
    offeredVenues.length > 0
      ? "market"
      : derivedVenues.length > 0
        ? "rows"
        : exchangeSource === "loading"
          ? "loading"
          : "none";

  const barFilters = activeFilters.filter(
    (filter) =>
      !(sectorCapable && isSectorChoice(filter)) &&
      !(exchangeCapable && isExchangeChoice(filter)),
  );

  // The menus draw the one predicate each owns; the chip bar draws the rest.
  // A panel with no controls hides nothing.
  const showControlChips =
    (sectorCapable && sectorValue !== null) || (exchangeCapable && exchangeValue.length > 0);

  // A page is a request, and the URL may name one that cannot exist (a shared
  // link, a stale tab, a filter that shrank the result set). `parseScreenState`
  // bounds the input to MAX_PAGE; this reconciles the page against the answer's
  // own total once there is one, so the footer never reads "999999/56" with a
  // dead Next. It runs only when the page is genuinely past the end: the last
  // valid page (pageIndex === pageCount - 1) is left alone, so paging and the
  // last-page case are exactly as they were.
  const maxPage = Math.max(0, pageCount - 1);
  React.useEffect(() => {
    if (!meta) return;
    if (pageIndex <= maxPage) return;
    // Deliberately not `changePage`: this correction is not a click, and it
    // must not be dropped because a request happens to be in flight.
    goToPage(maxPage);
    // `goToPage` closes over the controlled/uncontrolled switch, not over
    // `pageIndex`; re-running on a new page is what stops the loop at maxPage.
  }, [meta, pageIndex, maxPage]);

  // The other half of the tablist contract: this is the panel the tabs control,
  // and it names the tab that selected it.
  const panel = {
    role: "tabpanel" as const,
    id: VIEW_PANEL_ID,
    "aria-labelledby": viewTabId(view.name),
  };

  if (!supported) {
    return (
      <section
        {...panel}
        className="flex min-h-0 flex-1 flex-col rounded-md border border-line bg-surface"
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
      // `relative` is for the pending bar, which is positioned on the top edge
      // so that appearing and disappearing costs no layout.
      className="relative flex min-h-0 flex-1 flex-col overflow-hidden rounded-md border border-line bg-surface"
    >
      {showPending ? <PendingBar reducedMotion={reducedMotion} /> : null}

      <div className="flex h-9 shrink-0 items-center justify-between gap-3 border-b border-line-strong px-3">
        <div className="flex min-w-0 items-baseline gap-2">
          <h2 className="truncate text-ui font-medium text-fg">{view.label}</h2>
          <span className="hidden truncate text-data text-fg-subtle sm:inline">{view.job}</span>
        </div>

        <div className="flex shrink-0 items-center gap-3">
          {sameView && screens.revalidated ? (
            <span
              data-testid="stale-badge"
              title={`The server answered 304 Not Modified at ${timeOf(
                screens.checkedAt ?? "",
              )}: these rows are unchanged, not re-fetched.`}
              className="rounded-sm border border-line px-1.5 py-0.5 font-mono text-label uppercase tracking-[0.08em] text-fg-subtle"
            >
              not refreshed · 304
            </span>
          ) : null}

          <button
            type="button"
            onClick={onOpenFilters}
            aria-label="Filters"
            className="rounded-sm border border-line px-2 py-0.5 font-mono text-label uppercase tracking-[0.08em] text-fg-muted transition-colors hover:border-line-strong hover:text-fg"
          >
            Filters{activeFilters.length > 0 ? ` · ${activeFilters.length}` : ""}
          </button>

          {catalogue && catalogue.length > 0 ? (
            <button
              type="button"
              onClick={() => setPickerOpen(true)}
              aria-label="Columns"
              data-testid="columns-button"
              className="rounded-sm border border-line px-2 py-0.5 font-mono text-label uppercase tracking-[0.08em] text-fg-muted transition-colors hover:border-line-strong hover:text-fg"
            >
              Columns{custom ? ` · ${effective.length}` : ""}
            </button>
          ) : null}

          {sectorCapable ? (
            <SectorSelect
              names={sectorNames}
              source={sectorPopulation}
              value={sectorValue}
              onChange={(name) => onSectorChange?.(name)}
            />
          ) : null}

          {exchangeCapable ? (
            <ExchangeSelect
              venues={venues}
              source={exchangePopulation}
              value={exchangeValue}
              onChange={(codes) => onExchangeChange?.(codes)}
            />
          ) : null}

          {/* Three toggle buttons, not a `<select>`.
              The sort is a mode the toolbar is *in*, not a field being filled:
              `aria-pressed` says which one is on, so a screen reader hears
              "Market cap, pressed, button" without opening anything. A native
              select has no pressed state — the only way to learn the current
              ordering was to open it, and the control's own name ("Sort by")
              never said what it was set to. The group is named once; each
              button keeps its own label. */}
          <div
            role="group"
            aria-label="Sort by"
            data-testid="sort-group"
            className="flex shrink-0 items-center gap-0.5"
          >
            <span
              aria-hidden="true"
              className="pr-1 font-mono text-label uppercase tracking-[0.08em] text-fg-subtle"
            >
              sort
            </span>
            {SORTS.map((option) => (
              <button
                key={option}
                type="button"
                aria-pressed={sort === option}
                data-sort={option}
                onClick={() => changeSort(option)}
                className="rounded-sm border border-transparent px-1.5 py-0.5 font-mono text-label uppercase tracking-[0.08em] text-fg-muted transition-colors hover:border-line-strong hover:text-fg aria-pressed:border-brand aria-pressed:bg-row-selected aria-pressed:text-fg"
              >
                {SORT_LABELS[option]}
              </button>
            ))}
          </div>

          <button
            type="button"
            onClick={screens.refresh}
            className="rounded-sm border border-line px-2 py-0.5 font-mono text-label uppercase tracking-[0.08em] text-fg-muted transition-colors hover:border-line-strong hover:text-fg"
          >
            Check again
          </button>
        </div>
      </div>

      {/* The toolbar's own chips, in one bar: the sector menu and the exchange
          menu each own exactly one predicate, and neither is printed in
          `ChipBar` — that bar draws what the controls do *not* own, and the same
          filter in two places reads as two filters. */}
      {showControlChips ? (
        <div
          data-testid="control-chip-bar"
          role="group"
          aria-label="Toolbar filters"
          className="flex flex-wrap items-center gap-1 border-b border-line bg-surface-sunken px-2 py-1"
        >
          {sectorCapable && sectorValue !== null ? (
            <SectorChip bare name={sectorValue} onClear={() => onSectorChange?.(null)} />
          ) : null}
          {exchangeCapable && exchangeValue.length > 0 ? (
            <ExchangeChip codes={exchangeValue} onClear={() => onExchangeChange?.([])} />
          ) : null}
        </div>
      ) : null}

      {registry ? (
        <ChipBar
          registry={registry}
          filters={barFilters}
          onRemove={onRemoveFilter ?? (() => undefined)}
          onClear={onClearFilters ?? (() => undefined)}
          notice={filterNotice}
          onDismissNotice={onDismissFilterNotice}
        />
      ) : null}

      {columnNotice !== null && columnNotice !== undefined ? (
        // An alert, not a status: this sentence appears only when the reader
        // asked for a column the panel cannot carry, and it must interrupt —
        // the checkbox they just ticked is not going to stay ticked. The one
        // `role="status"` in the app belongs to the row range below.
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

      <div className="flex h-6 shrink-0 items-center justify-between gap-3 border-b border-line px-3 font-mono text-label uppercase tracking-[0.08em] text-fg-subtle">
        <span className="truncate" data-testid="column-count">
          {effective.length} columns{custom ? " · custom" : ""} · {rows.length} rows
        </span>
        <span className="truncate" data-testid="as-of">
          {meta ? `as of ${timeOf(meta.asOf)}` : "waiting for the engine"}
          {meta?.truncated ? " · budget cut rows" : ""}
        </span>
      </div>

      <DataTable
        view={panelView}
        rows={rows}
        status={status}
        error={sameView ? screens.error : null}
        revalidated={sameView && screens.revalidated}
        pageIndex={pageIndex}
        pageSize={limit}
        rowCount={total}
        // What the table is called, and the two facts that are not otherwise
        // said anywhere: which slice is on screen and how it is ordered. The
        // direction is in words because the grid never draws an arrow: the API
        // sorts every one of the three descending (`app/screen/rank.py`), ties
        // broken by symbol.
        caption={
          `${view.label}: ` +
          (rows.length === 0
            ? "no rows on this page"
            : `rows ${first}–${last} of ${total.toLocaleString("en-US")}`) +
          `, sorted by ${SORT_LABELS[sort].toLowerCase()}, highest first.`
        }
        onRetry={screens.refresh}
        onActivate={onActivate}
        onClear={onClear}
        selectedSymbol={selectedSymbol}
        containerWidth={containerWidth}
        stale={stale}
        busy={isFetching}
      />

      <div className="flex h-7 shrink-0 items-center justify-between gap-3 border-t border-line bg-surface-sunken px-3 font-mono text-label uppercase tracking-[0.08em] text-fg-subtle">
        <span className="flex min-w-0 items-center gap-2">
          <span data-testid="range" className="shrink-0">
            {total > 0 ? `${first}–${last} of ${total.toLocaleString("en-US")}` : "—"}
          </span>
          {/*
            The **one** `role="status"` in the document.
            It is mounted empty and filled by a state change, because a region
            that appears *with* its sentence is not reliably announced.
            `aria-atomic` re-reads the whole sentence rather than a diff of it.
            Every other announcement in the app is either a `role="alert"`
            (a refusal or a warning) or a plain `aria-live="polite"` node — see
            `a11y.test.tsx`, which counts them on the rendered shell.
          */}
          <span
            role="status"
            aria-live="polite"
            aria-atomic="true"
            data-testid="fetch-status"
            className="min-w-0 truncate normal-case tracking-normal text-accent-text"
          >
            {label}
          </span>
        </span>
        <span className="flex shrink-0 items-center gap-1">
          <button
            type="button"
            onClick={() => changePage(Math.max(0, pageIndex - 1))}
            disabled={pageIndex === 0 || isFetching}
            className="rounded-sm border border-line px-1.5 py-0.5 transition-colors hover:text-fg disabled:opacity-40"
          >
            Prev
          </button>
          <span className="px-1">
            {pageIndex + 1}/{pageCount}
          </span>
          <button
            type="button"
            onClick={() => changePage(Math.min(pageCount - 1, pageIndex + 1))}
            disabled={pageIndex + 1 >= pageCount || isFetching}
            className="rounded-sm border border-line px-1.5 py-0.5 transition-colors hover:text-fg disabled:opacity-40"
          >
            Next
          </button>
        </span>
      </div>

      {catalogue && catalogue.length > 0 ? (
        <ColumnPicker
          open={pickerOpen}
          onClose={() => setPickerOpen(false)}
          keys={catalogue}
          selected={effective}
          onToggle={(key) => onToggleColumn?.(key)}
          onReset={() => onResetColumns?.()}
          custom={custom}
          notice={columnNotice ?? null}
        />
      ) : null}
    </section>
  );
}
