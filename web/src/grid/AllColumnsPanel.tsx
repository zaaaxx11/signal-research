/**
 * The all-columns destination: 116 keys, 20 symbols, three requests.
 *
 * This is deliberately not a tab. A tab is a view the API can answer in one
 * request; this is wider than that, so it is its own mode with its own title,
 * its own page size and its own honest report of how the rows were assembled:
 * "All columns — 116, wide view, 20 rows", three requests of ≤40 columns, and
 * the number of cells the merge could not fill (if any).
 *
 * The table is the grid's own `DataTable`, so the identity lane is pinned, the
 * header is sticky and the widths come from the same solver. What is different
 * is only scale: 117 columns cannot fit 1280 px, the solver says `overflow`,
 * and the panel scrolls horizontally — which is the point of the destination.
 */

import * as React from "react";

import { MAX_COLUMNS, type ViewDefinition } from "@/data/screens";

import { DataTable } from "./DataTable";
import { ALL_COLUMNS_LIMIT } from "./catalogue";
import { absentCells, absenceReason, mergeNotices } from "./merge";
import { useAllColumns } from "./useAllColumns";

export interface AllColumnsPanelProps {
  /** The served wide view: its name, label and job are the server's words. */
  destination: ViewDefinition;
  keys: readonly string[];
  selectedSymbol: string | null;
  onActivate: (symbol: string) => void;
  onClear: () => void;
  onBack: () => void;
  /** Test seam; production measures the scroll box. */
  containerWidth?: number;
}

function timeOf(iso: string): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return `${date.toISOString().slice(11, 19)}Z`;
}

export function AllColumnsPanel({
  destination,
  keys,
  selectedSymbol,
  onActivate,
  onClear,
  onBack,
  containerWidth,
}: AllColumnsPanelProps) {
  const all = useAllColumns({
    view: destination.name,
    keys,
    limit: ALL_COLUMNS_LIMIT,
    sort: "market_cap",
  });

  const view = React.useMemo<ViewDefinition>(
    () => ({
      name: destination.name,
      label: destination.label,
      job: destination.job,
      columns: [...keys],
    }),
    [destination, keys],
  );

  const absent = React.useCallback(
    (symbol: string, key: string) => absenceReason(all.merged.absence, symbol, key),
    [all.merged.absence],
  );

  // The 116 × 20 budget is a measurement, not a slogan: this records the commit
  // that painted every cell (from the render that first carried the rows to the
  // effect after paint). The start time is keyed to the data stamp — not to
  // every render — so the state update that stores the number cannot pollute
  // the number it is storing.
  const stamp = `${all.rows.length}:${all.merged.absence.chunkOf.size}`;
  const start = React.useRef<{ stamp: string; at: number }>({ stamp: "", at: 0 });
  if (start.current.stamp !== stamp) {
    start.current = { stamp, at: typeof performance === "undefined" ? 0 : performance.now() };
  }
  const [measured, setMeasured] = React.useState<number | null>(null);
  React.useEffect(() => {
    if (all.rows.length === 0) return;
    setMeasured(Math.round((performance.now() - start.current.at) * 10) / 10);
  }, [stamp, all.rows.length]);

  const notices = mergeNotices(all.merged);
  const missing = absentCells(all.merged, keys);
  const chunkShape = all.chunks.map((chunk) => chunk.length).join(" + ");
  const failures = all.merged.failures.length > 0;

  return (
    <section
      role="region"
      aria-label={`${destination.label}, wide view`}
      data-testid="all-columns-panel"
      data-render-ms={measured === null ? undefined : String(measured)}
      className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-md border border-line bg-surface"
    >
      <div className="flex h-9 shrink-0 items-center justify-between gap-3 border-b border-line-strong px-3">
        <div className="flex min-w-0 items-baseline gap-2">
          <h2 data-testid="all-columns-title" className="truncate text-ui font-medium text-fg">
            {destination.label} — {keys.length}, wide view, {ALL_COLUMNS_LIMIT} rows
          </h2>
          <span className="hidden truncate text-data text-fg-subtle sm:inline">{destination.job}</span>
        </div>

        <div className="flex shrink-0 items-center gap-3">
          <button
            type="button"
            onClick={onBack}
            className="rounded-sm border border-line px-2 py-0.5 font-mono text-label uppercase tracking-[0.08em] text-fg-muted transition-colors hover:border-line-strong hover:text-fg"
          >
            ← Views
          </button>
          <button
            type="button"
            onClick={all.refresh}
            className="rounded-sm border border-line px-2 py-0.5 font-mono text-label uppercase tracking-[0.08em] text-fg-muted transition-colors hover:border-line-strong hover:text-fg"
          >
            Check again
          </button>
        </div>
      </div>

      <div className="flex h-6 shrink-0 items-center justify-between gap-3 border-b border-line px-3 font-mono text-label uppercase tracking-[0.08em] text-fg-subtle">
        <span data-testid="all-columns-requests" className="truncate">
          {all.chunks.length} requests · {chunkShape} columns
        </span>
        <span data-testid="all-columns-as-of" className="truncate">
          {all.meta ? `${all.rows.length} of ${all.meta.total.toLocaleString("en-US")} symbols · as of ${timeOf(all.meta.asOf)}` : "waiting for the engine"}
          {missing > 0 ? ` · ${missing} cells absent` : ""}
        </span>
      </div>

      {notices.length > 0 ? (
        <div
          data-testid="all-columns-notices"
          role={failures ? "alert" : "status"}
          className="shrink-0 border-b border-line bg-surface-raised px-3 py-1.5"
        >
          {notices.map((notice) => (
            <p key={notice} className="text-data text-alert/90">
              {notice}
            </p>
          ))}
        </div>
      ) : null}

      <DataTable
        view={view}
        rows={all.rows}
        status={all.status === "idle" ? "loading" : all.status}
        error={all.error ? { status: 0, message: all.error } : null}
        revalidated={false}
        pageIndex={0}
        pageSize={ALL_COLUMNS_LIMIT}
        rowCount={all.meta?.total ?? all.rows.length}
        onRetry={all.refresh}
        onActivate={onActivate}
        onClear={onClear}
        selectedSymbol={selectedSymbol}
        containerWidth={containerWidth}
        absent={absent}
        windowed={keys.length > MAX_COLUMNS}
      />

      <div className="flex h-7 shrink-0 items-center justify-between gap-3 border-t border-line bg-surface-sunken px-3 font-mono text-label uppercase tracking-[0.08em] text-fg-subtle">
        <span data-testid="all-columns-wide-note">
          every key the schema serves · scrolls sideways · identity lane pinned
        </span>
        <span>{all.meta?.truncated ? "budget cut cells" : "within the 60 kb panel budget"}</span>
      </div>
    </section>
  );
}
