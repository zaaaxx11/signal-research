/**
 * One panel: toolbar, grid, footer. The data hook lives here so the table
 * stays a pure renderer of rows it was given.
 *
 * Two visible truths the toolbar owes the reader:
 *
 * * **where the numbers came from** — `as of`, how many matched, how many this
 *   page shows, and whether the API's 60 KB budget cut anything;
 * * **whether the last refresh actually refreshed** — a `304` is a success, but
 *   it did not bring new rows, so it says so instead of pretending.
 */

import * as React from "react";

import {
  MAX_COLUMNS,
  PANEL_LIMIT,
  SORTS,
  useScreens,
  type ScreenSort,
  type ViewDefinition,
} from "@/data/screens";

import { DataTable } from "./DataTable";
import { VIEW_PANEL_ID, viewTabId } from "./ViewTabs";

export interface ScreenPanelProps {
  view: ViewDefinition;
  selectedSymbol: string | null;
  onActivate: (symbol: string) => void;
  onClear: () => void;
  /** Test seam; production measures the scroll box. */
  containerWidth?: number;
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
}: ScreenPanelProps) {
  const [pageIndex, setPageIndex] = React.useState(0);
  const [sort, setSort] = React.useState<ScreenSort>("market_cap");

  React.useEffect(() => {
    setPageIndex(0);
  }, [view.name, sort]);

  const supported = view.columns.length <= MAX_COLUMNS;

  const query = React.useMemo(
    () =>
      supported
        ? {
            view: view.name,
            columns: view.columns,
            limit: PANEL_LIMIT,
            offset: pageIndex * PANEL_LIMIT,
            sort,
          }
        : null,
    [supported, view.name, view.columns, pageIndex, sort],
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
  const checking = status === "loading" && rows.length > 0;

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
          <p className="text-ui font-medium text-fg">This view is too wide for one panel</p>
          <p className="mt-1 text-data text-fg-muted">
            {view.label} asks for {view.columns.length} columns; the API accepts at most{" "}
            {MAX_COLUMNS} per selection. The all-columns destination is Phase 4.
          </p>
        </div>
      </section>
    );
  }

  return (
    <section
      {...panel}
      className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-md border border-line bg-surface"
    >
      <div className="flex h-9 shrink-0 items-center justify-between gap-3 border-b border-line-strong px-3">
        <div className="flex min-w-0 items-baseline gap-2">
          <h2 className="truncate text-ui font-medium text-fg">{view.label}</h2>
          <span className="hidden truncate text-data text-fg-subtle sm:inline">{view.job}</span>
        </div>

        <div className="flex shrink-0 items-center gap-3">
          {checking ? (
            <span className="font-mono text-label uppercase tracking-[0.08em] text-fg-subtle">
              checking…
            </span>
          ) : null}

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

          <label className="flex items-center gap-1 font-mono text-label uppercase tracking-[0.08em] text-fg-subtle">
            sort
            <select
              aria-label="Sort by"
              value={sort}
              onChange={(event) => setSort(event.target.value as ScreenSort)}
              className="rounded-sm border border-line bg-surface-sunken px-1 py-0.5 font-mono text-label uppercase tracking-[0.08em] text-fg-muted"
            >
              {SORTS.map((option) => (
                <option key={option} value={option}>
                  {SORT_LABELS[option]}
                </option>
              ))}
            </select>
          </label>

          <button
            type="button"
            onClick={screens.refresh}
            className="rounded-sm border border-line px-2 py-0.5 font-mono text-label uppercase tracking-[0.08em] text-fg-muted transition-colors hover:border-line-strong hover:text-fg"
          >
            Check again
          </button>
        </div>
      </div>

      <div className="flex h-6 shrink-0 items-center justify-between gap-3 border-b border-line px-3 font-mono text-label uppercase tracking-[0.08em] text-fg-subtle">
        <span className="truncate">
          {view.columns.length} columns · {rows.length} rows
        </span>
        <span className="truncate" data-testid="as-of">
          {meta ? `as of ${timeOf(meta.asOf)}` : "waiting for the engine"}
          {meta?.truncated ? " · budget cut rows" : ""}
        </span>
      </div>

      <DataTable
        view={view}
        rows={rows}
        status={status}
        error={sameView ? screens.error : null}
        revalidated={sameView && screens.revalidated}
        pageIndex={pageIndex}
        pageSize={limit}
        rowCount={total}
        onRetry={screens.refresh}
        onActivate={onActivate}
        onClear={onClear}
        selectedSymbol={selectedSymbol}
        containerWidth={containerWidth}
      />

      <div className="flex h-7 shrink-0 items-center justify-between gap-3 border-t border-line bg-surface-sunken px-3 font-mono text-label uppercase tracking-[0.08em] text-fg-subtle">
        <span data-testid="range">
          {total > 0 ? `${first}–${last} of ${total.toLocaleString("en-US")}` : "—"}
        </span>
        <span className="flex items-center gap-1">
          <button
            type="button"
            onClick={() => setPageIndex((page) => Math.max(0, page - 1))}
            disabled={pageIndex === 0}
            className="rounded-sm border border-line px-1.5 py-0.5 transition-colors hover:text-fg disabled:opacity-40"
          >
            Prev
          </button>
          <span className="px-1">
            {pageIndex + 1}/{pageCount}
          </span>
          <button
            type="button"
            onClick={() => setPageIndex((page) => Math.min(pageCount - 1, page + 1))}
            disabled={pageIndex + 1 >= pageCount}
            className="rounded-sm border border-line px-1.5 py-0.5 transition-colors hover:text-fg disabled:opacity-40"
          >
            Next
          </button>
        </span>
      </div>
    </section>
  );
}
