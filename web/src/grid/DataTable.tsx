/**
 * The grid: one `<table>`, fixed layout, a generated `<colgroup>`.
 *
 * Why fixed layout: the solver already knows every width. If the browser were
 * allowed to measure content, the header and the body would disagree the moment
 * a company name was long, and no amount of CSS would put them back together.
 * The `<colgroup>` is the single source of geometry.
 *
 * Why the identity lane is sticky: at 100 rows the symbol must remain visible
 * while the eye travels right. It is column 1, pinned through TanStack's
 * `columnPinning` state, and pinned again in CSS with `position: sticky; left:0`
 * — the state is what a later "unpin" control will read, the CSS is what the
 * browser paints. `bg-inherit` lets the zebra/hover colour of the `<tr>` show
 * through without a second background rule.
 *
 * The states are body rows, not overlays: an error keeps the header's geometry
 * and says what the API said; an empty result says it matched nothing; loading
 * draws the same columns as ghost bars.
 */

import {
  columnPinningFeature,
  rowPaginationFeature,
  rowSortingFeature,
  tableFeatures,
  useTable,
  type ColumnDef,
} from "@tanstack/react-table";
import * as React from "react";

import type { ScreenError, ScreenRow, ScreenStatus, ViewDefinition } from "@/data/screens";
import { cn } from "@/lib/utils";

import { CellValue, IdentityCell } from "./cellRenderers";
import { columnSpec, labelFor, type ColumnSpec } from "./columnMeta";
import { solveView } from "./solver";

/** The CSS contract the browser is given; exported so a test can assert it. */
export const STICKY_HEADER = { position: "sticky", top: 0 } as const;
export const STICKY_LANE = { position: "sticky", left: 0 } as const;
/** The note a table points at when it is wider than the panel it sits in. */
export const OVERFLOW_NOTE_ID = "grid-overflow-note";

const features = tableFeatures({
  columnPinningFeature,
  rowPaginationFeature,
  rowSortingFeature,
});

const HEADER_HEIGHT = 24;
const ROW_HEIGHT = 28;

export interface DataTableProps {
  view: ViewDefinition;
  rows: ScreenRow[];
  status: ScreenStatus;
  error: ScreenError | null;
  /** The last refresh was answered 304; rows are kept and labelled. */
  revalidated: boolean;
  pageIndex: number;
  pageSize: number;
  rowCount: number;
  onRetry: () => void;
  onActivate?: (symbol: string) => void;
  onClear?: () => void;
  selectedSymbol?: string | null;
  /** Test seam: jsdom has no layout. Production measures the scroll box. */
  containerWidth?: number;
}

function useContainerWidth(ref: React.RefObject<HTMLDivElement | null>, override?: number) {
  const [measured, setMeasured] = React.useState(0);

  React.useEffect(() => {
    if (override !== undefined) return;
    const element = ref.current;
    if (!element) return;

    const update = () => {
      const width = element.clientWidth || element.getBoundingClientRect().width || 0;
      if (width > 0) setMeasured(Math.floor(width));
    };
    update();

    if (typeof ResizeObserver === "undefined") {
      window.addEventListener("resize", update);
      return () => window.removeEventListener("resize", update);
    }
    const observer = new ResizeObserver(update);
    observer.observe(element);
    return () => observer.disconnect();
  }, [ref, override]);

  return override ?? measured;
}

/**
 * One id per row, unique even when the server repeats a symbol.
 *
 * Rows are their symbols: position is not identity and order is not data. A
 * duplicated symbol is a server-side oddity, not a reason to drop a row: both
 * are rendered, and the later occurrences take a `#n` suffix so React's key and
 * `data-row-id` stay unique. Without this, a reorder could reuse a DOM node for
 * the other duplicate's numbers (`Encountered two children with the same key`).
 */
export function uniqueRowIds(rows: readonly ScreenRow[]): string[] {
  const seen = new Map<string, number>();
  return rows.map((row) => {
    const occurrence = (seen.get(row.symbol) ?? 0) + 1;
    seen.set(row.symbol, occurrence);
    return occurrence === 1 ? row.symbol : `${row.symbol}#${occurrence}`;
  });
}

export function DataTable({
  view,
  rows,
  status,
  error,
  revalidated,
  pageIndex,
  pageSize,
  rowCount,
  onRetry,
  onActivate,
  onClear,
  selectedSymbol,
  containerWidth,
}: DataTableProps) {
  const scrollRef = React.useRef<HTMLDivElement>(null);
  const width = useContainerWidth(scrollRef, containerWidth);
  const [focused, setFocused] = React.useState(-1);
  const rowRefs = React.useRef<Array<HTMLTableRowElement | null>>([]);

  const solved = React.useMemo(
    () => (width > 0 ? solveView(view.columns, width) : null),
    [view.columns, width],
  );

  const rowIds = React.useMemo(() => uniqueRowIds(rows), [rows]);

  const specs = React.useMemo(() => {
    const map = new Map<string, ColumnSpec>();
    map.set("identity", columnSpec("identity"));
    for (const key of view.columns) map.set(key, columnSpec(key));
    return map;
  }, [view.columns]);

  const headers = React.useMemo(() => {
    const map = new Map<string, { text: string; short: boolean; full: string }>();
    if (!solved) return map;
    solved.columns.forEach((column, index) => {
      const spec = specs.get(column.key) ?? columnSpec(column.key);
      const label = labelFor(spec, solved.widths[index] ?? 0);
      map.set(column.key, { text: label.text, short: label.short, full: spec.label });
    });
    return map;
  }, [solved, specs]);

  const tanstackColumns = React.useMemo<ColumnDef<typeof features, ScreenRow>[]>(() => {
    const identity: ColumnDef<typeof features, ScreenRow> = {
      id: "identity",
      accessorFn: (row) => row.symbol,
      header: "Symbol",
      enableSorting: false,
      enablePinning: true,
    };
    const rest = view.columns.map((key) => {
      const spec = specs.get(key) ?? columnSpec(key);
      const column: ColumnDef<typeof features, ScreenRow> = {
        id: key,
        accessorFn: (row) => row[key],
        header: spec.label,
        enableSorting: false,
      };
      return column;
    });
    return [identity, ...rest];
  }, [view.columns, specs]);

  const table = useTable({
    features,
    columns: tanstackColumns,
    data: rows,
    // `index` is the row's position in the data this table was given, which is
    // the array `rowIds` was derived from: the ids and the rows cannot drift.
    getRowId: (_row, index) => rowIds[index] ?? String(index),
    manualSorting: true,
    manualPagination: true,
    pageCount: Math.max(1, Math.ceil(rowCount / pageSize)),
    state: {
      columnPinning: { start: ["identity"], end: [] },
      pagination: { pageIndex, pageSize },
    },
  });

  const focusRow = (index: number) => {
    const bounded = Math.max(0, Math.min(rows.length - 1, index));
    setFocused(bounded);
    rowRefs.current[bounded]?.focus();
  };

  const onRowKeyDown = (event: React.KeyboardEvent<HTMLTableRowElement>, index: number) => {
    const row = rows[index];
    if (!row) return;
    switch (event.key) {
      case "ArrowDown":
        event.preventDefault();
        focusRow(index + 1);
        break;
      case "ArrowUp":
        event.preventDefault();
        focusRow(index - 1);
        break;
      case "PageDown":
        event.preventDefault();
        focusRow(index + 10);
        break;
      case "PageUp":
        event.preventDefault();
        focusRow(index - 10);
        break;
      case "Home":
        event.preventDefault();
        focusRow(0);
        break;
      case "End":
        event.preventDefault();
        focusRow(rows.length - 1);
        break;
      case "Enter":
        event.preventDefault();
        onActivate?.(row.symbol);
        break;
      case "Escape":
        event.preventDefault();
        setFocused(-1);
        onClear?.();
        event.currentTarget.blur();
        break;
      default:
        break;
    }
  };

  React.useEffect(() => {
    setFocused(-1);
  }, [view.name]);

  const hasRows = rows.length > 0;
  const columnCount = view.columns.length + 1;
  // F7: the grid announces the whole screen, not the page it happens to hold,
  // plus the header row that `aria-rowcount` counts. Never fewer than the rows
  // actually rendered — a body that under-reports itself is worse than a stale
  // total.
  const announcedRows = Math.max(rowCount, rows.length) + 1;

  return (
    <div
      ref={scrollRef}
      data-testid="grid-scroll"
      // When the solver reports `overflow`, the table keeps its declared
      // minimums and this box scrolls; otherwise the widths are exact and it
      // has nothing to scroll (adversarial: no view scrolls at 1280).
      className={cn(
        "relative min-h-0 flex-1 overflow-auto bg-surface",
        solved?.overflow && "overflow-x-auto",
      )}
      data-overflow={solved ? (solved.overflow ? "true" : "false") : undefined}
      // Reserve the vertical scrollbar's width at all times. Without this the
      // first paint measures a container 15 px wider than the one that exists
      // once 100 rows make the scrollbar appear — and the table, solved once,
      // would overflow by exactly that much.
      style={{ scrollbarGutter: "stable" }}
      aria-busy={status === "loading"}
    >
      {!solved ? (
        <div className="space-y-1 p-2" aria-hidden="true">
          {Array.from({ length: 12 }, (_, index) => (
            <div
              key={index}
              className={cn("h-7 rounded-[1px]", index % 2 === 0 ? "bg-row" : "bg-row-alt")}
            />
          ))}
        </div>
      ) : (
        <table
          data-testid="grid-table"
          aria-label={`${view.label} screen`}
          aria-rowcount={hasRows ? announcedRows : undefined}
          aria-colcount={columnCount}
          aria-describedby={solved.overflow ? OVERFLOW_NOTE_ID : undefined}
          style={{
            // `minWidth` is belt and braces for the fixed layout: the browser
            // may never lay this table out narrower than the widths it was
            // given, so a numeric cell cannot be squeezed into an ellipsis.
            width: solved.total,
            minWidth: solved.total,
            tableLayout: "fixed",
            borderCollapse: "separate",
            borderSpacing: 0,
          }}
          className="w-full text-data text-fg"
        >
          <colgroup>
            {solved.columns.map((column, index) => (
              <col key={column.key} style={{ width: solved.widths[index] ?? 0 }} />
            ))}
          </colgroup>

          <thead>
            {table.getHeaderGroups().map((group) => (
              <tr key={group.id}>
                {group.headers.map((header) => {
                  const key = header.column.id;
                  const meta = headers.get(key);
                  const pinned = header.column.getIsPinned() === "start";
                  return (
                    <th
                      key={header.id}
                      scope="col"
                      style={{
                        ...STICKY_HEADER,
                        ...(pinned ? STICKY_LANE : {}),
                        height: HEADER_HEIGHT,
                        zIndex: pinned ? 40 : 20,
                      }}
                      className={cn(
                        "border-b border-line-strong bg-surface-raised px-2 py-0 text-left font-mono text-label uppercase tracking-[0.08em] text-fg-muted",
                        pinned && "bg-surface-raised shadow-[inset_-1px_0_0_var(--border-strong)]",
                        key !== "identity" && "text-right",
                      )}
                      title={meta?.full ?? key}
                    >
                      <span className="block truncate">{meta?.text ?? key}</span>
                    </th>
                  );
                })}
              </tr>
            ))}
          </thead>

          <tbody>
            {status === "error" ? (
              <tr>
                <td colSpan={columnCount} className="px-0 py-0">
                  <ApiErrorState error={error} onRetry={onRetry} />
                </td>
              </tr>
            ) : null}

            {status === "loading" && !hasRows ? (
              <SkeletonRows columns={solved.columns.length} widths={solved.widths} />
            ) : null}

            {status === "empty" || (status === "ready" && !hasRows) ? (
              <tr>
                <td colSpan={columnCount} className="px-0 py-0">
                  <div className="flex h-40 flex-col items-center justify-center gap-1 text-center">
                    <p className="text-ui font-medium text-fg">No rows in this view</p>
                    <p className="text-data text-fg-subtle">
                      The screen matched nothing at this page. Try another view.
                    </p>
                  </div>
                </td>
              </tr>
            ) : null}

            {table.getRowModel().rows.map((row, index) => {
              const symbol = row.original.symbol;
              const selected = selectedSymbol === symbol;
              const tabbable = focused === index || (focused === -1 && index === 0);
              return (
                <tr
                  key={row.id}
                  ref={(element) => {
                    rowRefs.current[index] = element;
                  }}
                  data-symbol={symbol}
                  data-row-id={row.id}
                  data-selected={selected ? "true" : "false"}
                  aria-selected={selected}
                  tabIndex={tabbable ? 0 : -1}
                  onFocus={() => setFocused(index)}
                  onKeyDown={(event) => onRowKeyDown(event, index)}
                  onClick={() => onActivate?.(symbol)}
                  className={cn(
                    "h-7 cursor-default outline-offset-[-2px] odd:bg-row even:bg-row-alt",
                    "hover:bg-row-hover focus-visible:outline-2 focus-visible:outline-focus",
                    "data-[selected=true]:bg-row-selected",
                  )}
                  style={{ height: ROW_HEIGHT }}
                >
                  {row.getAllCells().map((cell) => {
                    const key = cell.column.id;
                    const pinned = cell.column.getIsPinned() === "start";
                    const identity = key === "identity";
                    return (
                      <td
                        key={cell.id}
                        style={{
                          ...(pinned ? { ...STICKY_LANE, zIndex: 2 } : {}),
                          height: ROW_HEIGHT,
                        }}
                        className={cn(
                          "overflow-hidden px-2 py-0 align-middle",
                          pinned && "bg-inherit shadow-[inset_-1px_0_0_var(--border-strong)]",
                          identity && "text-left",
                        )}
                      >
                        {identity ? (
                          <IdentityCell
                            symbol={symbol}
                            company={typeof row.original["company"] === "string" ? row.original["company"] : null}
                            logo={typeof row.original["logo"] === "string" ? row.original["logo"] : null}
                          />
                        ) : (
                          <CellValue
                            spec={specs.get(key) ?? columnSpec(key)}
                            value={row.original[key]}
                          />
                        )}
                      </td>
                    );
                  })}
                </tr>
              );
            })}
          </tbody>
        </table>
      )}

      {solved?.overflow ? (
        <p id={OVERFLOW_NOTE_ID} data-testid="grid-overflow-note" className="sr-only">
          This table is wider than the panel. It scrolls horizontally, so every column keeps its
          declared width and no number is cut.
        </p>
      ) : null}

      {revalidated && hasRows ? (
        <span className="sr-only" data-testid="revalidated-announcer">
          Refresh not modified: showing the rows already on screen.
        </span>
      ) : null}
    </div>
  );
}

function SkeletonRows({ columns, widths }: { columns: number; widths: number[] }) {
  const patterns = [0.84, 0.56, 0.42, 0.5, 0.68, 0.62, 0.48, 0.36, 0.54, 0.54, 0.58, 0.62, 0.5];
  return (
    <>
      {Array.from({ length: 12 }, (_, row) => (
        <tr
          key={row}
          aria-hidden="true"
          className={cn("h-7", row % 2 === 0 ? "bg-row" : "bg-row-alt")}
          style={{ height: ROW_HEIGHT }}
        >
          {Array.from({ length: columns }, (_, column) => (
            <td
              key={column}
              className="overflow-hidden px-2 py-0"
              style={{ height: ROW_HEIGHT, width: widths[column] ?? 0 }}
            >
              <span
                className={cn("block h-1.5 rounded-[1px]", column === 0 ? "bg-line-strong" : "bg-line")}
                style={{ width: `${(patterns[column % patterns.length] ?? 0.4) * 100}%` }}
              />
            </td>
          ))}
        </tr>
      ))}
    </>
  );
}

function ApiErrorState({ error, onRetry }: { error: ScreenError | null; onRetry: () => void }) {
  return (
    <div
      role="alert"
      className="mx-3 my-3 flex items-start gap-3 rounded-md border border-line border-l-2 border-l-alert bg-surface-raised px-4 py-3"
    >
      <div className="min-w-0 flex-1">
        <p className="text-ui font-medium text-fg">
          {error && error.status > 0 ? `The screen request failed (${error.status})` : "The screen request failed"}
        </p>
        <p className="mt-1 break-words text-data text-fg-muted">
          {error?.message ?? "The API returned an error without a message."}
        </p>
        <p className="mt-1 font-mono text-label uppercase tracking-[0.08em] text-fg-subtle">
          POST /screens/select
        </p>
      </div>
      <button
        type="button"
        onClick={onRetry}
        className="shrink-0 rounded-sm border border-line-strong px-2 py-1 text-ui text-fg-muted transition-colors hover:text-fg"
      >
        Retry
      </button>
    </div>
  );
}
