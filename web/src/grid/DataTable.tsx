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
import { visibleRange } from "./window";
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

/**
 * How far the table fades while a page turn is in flight.
 *
 * The number is the deepest fade that still keeps every data colour at or
 * above 4:1 on an ordinary row, computed (not eyeballed) against the tokens:
 * at 0.78 over `--surface`, a `--fg` cell reads 9.65:1, `--up` 6.70:1,
 * `--flat` 4.90:1 and a `--down` loss 4.17:1 — so 0.78 is the last step before
 * a falling number drops through 4:1. Dimming further is what would make the
 * grid read as *disabled* rather than *stale*, and the rows on screen are
 * exactly the rows the reader is still reading.
 *
 * It is also the reason the dim is not the only signal: a 22 % fade is a depth
 * cue, and the bar and the label are what say "new numbers are on the way".
 */
export const STALE_OPACITY = 0.78;

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
  /**
   * Why a cell was never answered, or null when it was.
   *
   * The all-columns destination is assembled from three requests; when one
   * chunk fails, the cells it owned are not "null from the API" — they are
   * missing, and the sentence plus the `title` says which chunk. The grid
   * renders an em dash either way; the difference is that this one names the
   * gap instead of shrugging at it.
   */
  absent?: (symbol: string, key: string) => string | null;
  /**
   * Render only the columns the viewport can see, with fixed-width spacers
   * standing in for the rest (the wide destination: 116 columns × 20 rows).
   *
   * The `<colgroup>` still declares every width, so the table keeps its full
   * 9 639 px, the scrollbar is the same, and the sticky lanes are untouched —
   * what changes is how many cells exist. Without this, Chrome spends ~330 ms
   * laying out columns nobody can see.
   */
  windowed?: boolean;
  /**
   * A selection is in flight and these rows are the previous answer: the table
   * fades to `STALE_OPACITY` and says so in its `data-stale` attribute.
   *
   * The rows are kept — never blanked — because the reader is mid-sentence on
   * them; dimming is the smallest honest signal that they are about to be
   * replaced. They stay operable: a stale row still hovers, focuses and opens.
   */
  stale?: boolean;
  /**
   * Whether the region is being updated, for `aria-busy`.
   *
   * Defaults to the first-load status so an existing caller is unchanged; a
   * page turn sets it while the *previous* rows are still on screen, which is
   * exactly the state `status` alone cannot describe.
   */
  busy?: boolean;
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
  absent,
  windowed = false,
  stale = false,
  busy,
}: DataTableProps) {
  const scrollRef = React.useRef<HTMLDivElement>(null);
  const width = useContainerWidth(scrollRef, containerWidth);
  const [focused, setFocused] = React.useState(-1);
  const [scrollLeft, setScrollLeft] = React.useState(0);
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


  /**
   * The column window: identity is always drawn (it is pinned, so it is on
   * screen at every scroll offset), and the rest follow the viewport. `null`
   * means "every column" — the ordinary view, or a container not yet measured.
   */
  const range = React.useMemo(() => {
    if (!windowed || !solved) return null;
    const columns = visibleRange(solved.widths.slice(1), scrollLeft, width);
    if (columns.start === 0 && columns.end === view.columns.length - 1) return null;
    return columns;
  }, [windowed, solved, scrollLeft, width, view.columns.length]);

  const columns = React.useMemo(
    () => (range ? view.columns.slice(range.start, range.end + 1) : view.columns),
    [range, view.columns],
  );
  const lead = range ? range.start : 0;
  const trail = range ? view.columns.length - range.end - 1 : 0;


  const onScroll = (event: React.UIEvent<HTMLDivElement>) => {
    if (!windowed) return;
    // One state update per scroll event: browsers fire these at most once a
    // frame, and the window that changes is ~12 cells, not 116.
    setScrollLeft(event.currentTarget.scrollLeft);
  };

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

  /**
   * Stable row callbacks.
   *
   * The wide view renders 20 rows × 117 cells; a re-render of every row on
   * every hover, focus or selection is a measured 110 ms of jank in Chrome.
   * Rows are memoised, so the callbacks they receive must keep their identity
   * across renders — the latest values live in refs, not in closures.
   */
  const rowsRef = React.useRef(rows);
  rowsRef.current = rows;
  const activateRef = React.useRef(onActivate);
  activateRef.current = onActivate;
  const clearRef = React.useRef(onClear);
  clearRef.current = onClear;
  // Read inside the stable row key handler, so "is a panel open?" is the
  // latest selection rather than the one captured at row creation.
  const selectedRef = React.useRef(selectedSymbol);
  selectedRef.current = selectedSymbol;

  const activate = React.useCallback((symbol: string) => activateRef.current?.(symbol), []);
  const setRowRef = React.useCallback((index: number, element: HTMLTableRowElement | null) => {
    rowRefs.current[index] = element;
  }, []);

  const focusRow = React.useCallback((index: number) => {
    const bounded = Math.max(0, Math.min(rowsRef.current.length - 1, index));
    setFocused(bounded);
    rowRefs.current[bounded]?.focus();
  }, []);

  const onRowKeyDown = React.useCallback(
    (event: React.KeyboardEvent<HTMLTableRowElement>, index: number) => {
      const row = rowsRef.current[index];
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
          focusRow(rowsRef.current.length - 1);
          break;
        case "Enter":
          event.preventDefault();
          activateRef.current?.(row.symbol);
          break;
        case "Escape": {
          event.preventDefault();
          // F6: when a panel is open, Escape is the panel's key. The panel
          // closes through the same `onClear` the parent owns, and focus stays
          // on this row — where the reader was, and where the panel's own
          // handler would have put it. Blurring here used to drop focus to
          // `<body>`: the row blurred itself before the panel's document
          // listener ran, and that listener then stood down on the row's
          // `defaultPrevented`.
          const panelOpen = Boolean(selectedRef.current);
          clearRef.current?.();
          if (panelOpen) {
            event.currentTarget.focus();
            break;
          }
          // No panel: Escape clears the roving cursor, as it always has.
          setFocused(-1);
          event.currentTarget.blur();
          break;
        }
        default:
          break;
      }
    },
    [focusRow],
  );

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
      onScroll={onScroll}
      data-window={range ? `${range.start}:${range.end}` : "every"}
      aria-busy={busy ?? status === "loading"}
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
          data-stale={stale ? "true" : "false"}
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
            // The stale fade, inline rather than a utility so the effective
            // value is readable from the DOM (and so it cannot be confused
            // with the `disabled:` treatment of a control). The transition is
            // the crossing only — the global reduced-motion rule collapses it
            // to 0.01 ms for a reader who asked for that.
            opacity: stale ? STALE_OPACITY : 1,
            transition: "opacity 120ms linear",
          }}
          className="w-full text-data text-fg"
        >
          <colgroup>
            {solved.columns.map((column, index) => (
              <col key={column.key} style={{ width: solved.widths[index] ?? 0 }} />
            ))}
          </colgroup>

          <thead>
            {table.getHeaderGroups().map((group) => {
              const identityHeader = group.headers.find((header) => header.column.id === "identity");
              const rest = group.headers.filter(
                (header) =>
                  header.column.id !== "identity" &&
                  (!range || columns.includes(header.column.id)),
              );
              const draw = (header: (typeof group.headers)[number]) => {
                const key = header.column.id;
                const meta = headers.get(key);
                const pinned = header.column.getIsPinned() === "start";
                return (
                  <th
                    key={header.id}
                    scope="col"
                    data-key={key}
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
              };
              return (
                <tr key={group.id}>
                  {identityHeader ? draw(identityHeader) : null}
                  {lead > 0 ? (
                    <th
                      aria-hidden="true"
                      colSpan={lead}
                      style={{ ...STICKY_HEADER, height: HEADER_HEIGHT, zIndex: 20 }}
                      className="border-b border-line-strong bg-surface-raised"
                    />
                  ) : null}
                  {rest.map(draw)}
                  {trail > 0 ? (
                    <th
                      aria-hidden="true"
                      colSpan={trail}
                      style={{ ...STICKY_HEADER, height: HEADER_HEIGHT, zIndex: 20 }}
                      className="border-b border-line-strong bg-surface-raised"
                    />
                  ) : null}
                </tr>
              );
            })}
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

            {rows.map((row, index) => {
              const symbol = row.symbol;
              return (
                <GridRow
                  key={rowIds[index] ?? String(index)}
                  symbol={symbol}
                  data={row}
                  rowId={rowIds[index] ?? String(index)}
                  cells={columns}
                  lead={lead}
                  trail={trail}
                  index={index}
                  selected={selectedSymbol === symbol}
                  tabbable={focused === index || (focused === -1 && index === 0)}
                  specs={specs}
                  absent={absent}
                  onActivate={activate}
                  onFocusRow={focusRow}
                  onKeyDown={onRowKeyDown}
                  setRowRef={setRowRef}
                />
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

/**
 * One memoised body row.
 *
 * It exists for the wide view: 116 columns × 20 rows is 2 340 cells, and
 * re-rendering all of them because one row's selection changed measured at
 * ~110 ms in Chrome — a visible stall on a click. With identity-stable
 * callbacks and a shallow comparison, a click re-renders the row that gained
 * the selection and the row that lost it, and nothing else.
 *
 * It reads the cells from the view's column list rather than from TanStack's
 * cell model: the same order, without allocating 2 340 cell objects per paint.
 */
interface GridRowProps {
  symbol: string;
  data: ScreenRow;
  rowId: string;
  cells: readonly string[];
  /** Hidden columns before and after the window, as colspan spacers. */
  lead: number;
  trail: number;
  index: number;
  selected: boolean;
  tabbable: boolean;
  specs: Map<string, ColumnSpec>;
  absent?: (symbol: string, key: string) => string | null;
  onActivate: (symbol: string) => void;
  onFocusRow: (index: number) => void;
  onKeyDown: (event: React.KeyboardEvent<HTMLTableRowElement>, index: number) => void;
  setRowRef: (index: number, element: HTMLTableRowElement | null) => void;
}

const GridRow = React.memo(function GridRow({
  symbol,
  data,
  rowId,
  cells,
  lead,
  trail,
  index,
  selected,
  tabbable,
  specs,
  absent,
  onActivate,
  onFocusRow,
  onKeyDown,
  setRowRef,
}: GridRowProps) {
  return (
    <tr
      ref={(element) => setRowRef(index, element)}
      data-symbol={symbol}
      data-row-id={rowId}
      data-selected={selected ? "true" : "false"}
      // F3: `aria-selected` on a `<tr>` is only mapped inside `role=grid` /
      // `treegrid`; this table is a plain `table`, so Chrome dropped it and the
      // open row reached assistive tech with no state at all. `aria-current`
      // is valid on any element in a table and is what the browser's AX tree
      // keeps — one current item per document, and the tab strip marks itself
      // with `aria-selected` (role=tab), so the two cannot be confused.
      aria-current={selected ? "true" : undefined}
      tabIndex={tabbable ? 0 : -1}
      onFocus={() => onFocusRow(index)}
      onKeyDown={(event) => onKeyDown(event, index)}
      onClick={() => onActivate(symbol)}
      className={cn(
        "h-7 cursor-default outline-offset-[-2px] odd:bg-row even:bg-row-alt",
        "hover:bg-row-hover focus-visible:outline-2 focus-visible:outline-focus",
        "data-[selected=true]:bg-row-selected",
      )}
      style={{ height: ROW_HEIGHT }}
    >
      <td
        style={{ ...STICKY_LANE, height: ROW_HEIGHT, zIndex: 2 }}
        className="overflow-hidden bg-inherit px-2 py-0 text-left align-middle shadow-[inset_-1px_0_0_var(--border-strong)]"
      >
        <IdentityCell
          symbol={symbol}
          company={typeof data["company"] === "string" ? data["company"] : null}
        />
      </td>
      {lead > 0 ? (
        <td aria-hidden="true" colSpan={lead} style={{ height: ROW_HEIGHT }} className="p-0" />
      ) : null}
      {cells.map((key) => {
        const gap = absent?.(symbol, key) ?? null;
        return (
          <td
            key={`${rowId}:${key}`}
            style={{ height: ROW_HEIGHT }}
            className="overflow-hidden px-2 py-0 align-middle"
          >
            {gap !== null ? (
              <AbsentCell reason={gap} />
            ) : (
              <CellValue spec={specs.get(key) ?? columnSpec(key)} value={data[key]} />
            )}
          </td>
        );
      })}
      {trail > 0 ? (
        <td aria-hidden="true" colSpan={trail} style={{ height: ROW_HEIGHT }} className="p-0" />
      ) : null}
    </tr>
  );
});

/**
 * A cell the merge could not fill, with the reason attached.
 *
 * It looks like `CellValue`'s absent state (a muted em dash) but carries
 * `data-absent` and the chunk sentence, so "we never got chunk 2" is not
 * mistakable for "the company has no P/E".
 */
function AbsentCell({ reason }: { reason: string }) {
  return (
    <span
      className="block text-right text-fg-subtle"
      data-absent="true"
      aria-label={`Absent: ${reason}`}
      title={reason}
    >
      —
    </span>
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
