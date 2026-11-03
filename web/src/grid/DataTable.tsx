/**
 * The grid: one `<table>`, fixed layout, a generated `<colgroup>`.
 *
 * Why fixed layout: the solver already knows every width. If the browser were
 * allowed to measure content, the header and the body would disagree the moment
 * a company name was long, and no amount of CSS would put them back together.
 * The `<colgroup>` is the single source of geometry.
 *
 * Why the identity lane is sticky: once the rows scroll, the symbol must stay visible
 * while the eye travels right. It is the lead lane — the second one when the
 * reader's set starts with the row number — pinned through TanStack's
 * `columnPinning` state, and pinned again in CSS with `position: sticky; left`
 * — the state is what a later "unpin" control will read, the CSS is what the
 * browser paints. `bg-inherit` lets the zebra/hover colour of the `<tr>` show
 * through without a second background rule.
 *
 * The headers are the sort controls: each sortable column is a button that
 * rewrites the one `criteria.sort` token, and `aria-sort` plus an arrow say
 * which end of that column is on top. The rows themselves are the server's
 * ordering, never a re-sort of the page.
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
import { sortDirection, sortKey } from "@/data/screens";
import { Icon } from "@/components/Icon";
import { Link } from "@/shell/routes";
import { cn } from "@/lib/utils";

import { CellValue, IdentityCell } from "./cellRenderers";
import {
  ROW_NUMBER_KEY,
  columnSpec,
  isClientColumn,
  labelFor,
  type ColumnSpec,
} from "./columnMeta";
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

/** Hardcoded row and header dimensions so the solver and browser agree. */
const ROW_HEIGHT = 24;
const HEADER_HEIGHT = 24;

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

/** The two lists a row's star can write to. */
export type RowActionList = "watchlist" | "portfolio";

/** Which of a row's two menus is open above it. */
export type RowMenuKind = "add" | "rows";

/**
 * The actions a row offers at its right edge.
 *
 * The grid owns the menu chrome and the focus movement; the panel owns what
 * the actions mean (a storage write, a dialog seeded with this symbol). The
 * handlers receive the row's symbol, never an index, so a reorder cannot make
 * the click land on another company.
 */
export interface RowActions {
  onAddTo: (symbol: string, list: RowActionList) => void;
  onAlert: (symbol: string) => void;
}

/**
 * Compare's transient checkbox lane.
 *
 * Present only while the toolbar is in compare mode: outside it the table has
 * no checkbox column at all. The grid draws the boxes and reports a tick; the
 * panel caps the set and navigates when the reader confirms.
 */
export interface CompareSelection {
  selected: readonly string[];
  onToggle: (symbol: string) => void;
  /** The most symbols a comparison may hold. */
  max: number;
}

/** The width the transient compare column claims, in px. */
export const COMPARE_LANE_WIDTH = 30;

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
  /**
   * The row's ticker page, as a route path (`/stock/AAPL`).
   *
   * When set, the identity lane is a real `<a href>` and a plain click or
   * Enter on the row is a client-side navigation; a modified click is left to
   * the browser, which is what makes middle-click and ⌘/Ctrl-click open a new
   * tab. When absent the grid stays a pure report of rows it was given.
   */
  rowTo?: (symbol: string) => string;
  /**
   * The row actions drawn at the right edge: star, compare, bell and "…".
   *
   * Absent means a read-only grid (the wide destination, a detail preview):
   * no lane, no extra buttons. The lane is absolutely positioned inside the
   * row's last cell, so it never claims a column and rows do not shift.
   */
  rowActions?: RowActions;
  /**
   * The row a pointer is over or the keyboard is on, or null when neither.
   *
   * The toolbar uses it as the subject of Add to / Set alert. Null is not
   * "no-op": the toolbar says which row is missing, rather than guessing which
   * of the twenty rows was meant.
   */
  onRowPoint?: (symbol: string | null) => void;
  /**
   * The symbols already on the watchlist or in the portfolio, so a row's star
   * can be drawn filled. The panel reads both stores and keeps this list
   * current; the grid never opens storage itself.
   */
  starredSymbols?: readonly string[];
  /**
   * How many saved alerts each symbol has, for the row bells.
   *
   * A count rather than a flag because the bell's name states it ("2 active"),
   * and a symbol with none is simply absent from the map. The panel reads the
   * alert store and keeps this current; the grid never opens storage itself.
   */
  alertCounts?: ReadonlyMap<string, number>;
  /**
   * Compare mode, or undefined for the ordinary grid.
   *
   * When present the grid draws one transient checkbox lane in front of the
   * columns and reports ticks; when absent no checkbox exists in the DOM at
   * all — the table is not a selection surface by default.
   */
  compareSelection?: CompareSelection;
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
   * The table's accessible name, spoken once when a screen reader enters the
   * grid: the view, the slice on screen and the ordering in force.
   *
   * A `<caption>` rather than `aria-label` because a caption is what HTML says
   * a table is called, and Chrome exposes it as the table node's name in the AX
   * tree either way — but only the caption also survives when the table is
   * reached in browse mode, and only the caption can be read as the table's
   * description without stealing the name slot.
   *
   * It carries the sort in *words* ("sorted by market cap, highest first") as
   * well as the header's `aria-sort`: the header says which column, the caption
   * says the ordering the whole page is in, and neither depends on colour.
   */
  caption?: string;
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
  /**
   * The ordering in force, as the API's own token: `country` is highest first,
   * `-country` lowest first.
   *
   * `aria-sort` and the arrow are read from it, and a header press writes it
   * through `onSort` — one state with the masthead's Order by select, never a
   * second copy. The rows are ordered by the server; the grid only marks which
   * header asked for them.
   */
  sort?: string;
  /** A column header was pressed: ask the shell's one sort state to change. */
  onSort?: (key: string) => void;
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
  rowTo,
  rowActions,
  onRowPoint,
  starredSymbols,
  alertCounts,
  compareSelection,
  containerWidth,
  absent,
  caption,
  windowed = false,
  stale = false,
  busy,
  sort,
  onSort,
}: DataTableProps) {
  const scrollRef = React.useRef<HTMLDivElement>(null);
  const width = useContainerWidth(scrollRef, containerWidth);
  const [focused, setFocused] = React.useState(-1);
  const [scrollLeft, setScrollLeft] = React.useState(0);
  const [menu, setMenu] = React.useState<{ rowId: string; kind: RowMenuKind } | null>(null);
  const rowRefs = React.useRef<Array<HTMLTableRowElement | null>>([]);

  /** The row number is drawn from the page offset; it is never requested. */
  const showNumber = view.columns.includes(ROW_NUMBER_KEY);
  const dataColumns = React.useMemo(
    () => view.columns.filter((key) => !isClientColumn(key)),
    [view.columns],
  );
  const sortedKey = sort ? sortKey(sort) : null;
  const sortedDirection = sortDirection(sort ?? "market_cap");

  /** The transient compare lane: its boxes, its cap, and the star's fill. */
  const compareActive = compareSelection !== undefined;
  const compareSelected = React.useMemo(
    () => new Set(compareSelection?.selected ?? []),
    [compareSelection?.selected],
  );
  const compareMax = compareSelection?.max ?? 0;
  const starred = React.useMemo(() => new Set(starredSymbols ?? []), [starredSymbols]);

  /** The one row `aria-current` may name: the panel's open row, if any. */
  const currentSymbol = selectedSymbol ?? null;

  const solved = React.useMemo(
    () =>
      width > 0
        ? solveView(view.columns, Math.max(0, width - (compareActive ? COMPARE_LANE_WIDTH : 0)))
        : null,
    [view.columns, width, compareActive],
  );

  const rowIds = React.useMemo(() => uniqueRowIds(rows), [rows]);

  const specs = React.useMemo(() => {
    const map = new Map<string, ColumnSpec>();
    map.set("identity", columnSpec("identity"));
    map.set(ROW_NUMBER_KEY, columnSpec(ROW_NUMBER_KEY));
    for (const key of view.columns) map.set(key, columnSpec(key));
    return map;
  }, [view.columns]);


  /**
   * The column window: identity is always drawn (it is pinned, so it is on
   * screen at every scroll offset), and the rest follow the viewport. `null`
   * means "every column" — the ordinary view, or a container not yet measured.
   */
  const pinned = 1 + (showNumber ? 1 : 0);
  const range = React.useMemo(() => {
    if (!windowed || !solved) return null;
    const columns = visibleRange(solved.widths.slice(pinned), scrollLeft, width);
    if (columns.start === 0 && columns.end === dataColumns.length - 1) return null;
    return columns;
  }, [windowed, solved, scrollLeft, width, dataColumns.length, pinned]);

  const columns = React.useMemo(
    () => (range ? dataColumns.slice(range.start, range.end + 1) : dataColumns),
    [range, dataColumns],
  );
  const lead = range ? range.start : 0;
  const trail = range ? dataColumns.length - range.end - 1 : 0;


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
    const number: ColumnDef<typeof features, ScreenRow> = {
      id: ROW_NUMBER_KEY,
      accessorFn: () => null,
      header: columnSpec(ROW_NUMBER_KEY).label,
      enableSorting: false,
      enablePinning: true,
    };
    const identity: ColumnDef<typeof features, ScreenRow> = {
      id: "identity",
      accessorFn: (row) => row.symbol,
      header: columnSpec("identity").label,
      // The identity lane is the ticker column: pressing it sorts by `ticker`,
      // the catalogue's own spelling for the row's symbol.
      enableSorting: true,
      enablePinning: true,
    };
    const rest = dataColumns.map((key) => {
      const spec = specs.get(key) ?? columnSpec(key);
      const column: ColumnDef<typeof features, ScreenRow> = {
        id: key,
        accessorFn: (row) => row[key],
        header: spec.label,
        enableSorting: true,
      };
      return column;
    });
    return [...(showNumber ? [number] : []), identity, ...rest];
  }, [dataColumns, specs, showNumber]);

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
      columnPinning: { start: showNumber ? [ROW_NUMBER_KEY, "identity"] : ["identity"], end: [] },
      pagination: { pageIndex, pageSize },
    },
  });

  /**
   * Stable row callbacks.
   *
   * The wide view renders 20 rows × 117 cells; a re-render of every row on
   * every hover, focus or menu change is a measured 110 ms of jank in Chrome.
   * Rows are memoised, so the callbacks they receive must keep their identity
   * across renders — the latest values live in refs, not in closures.
   */
  const rowsRef = React.useRef(rows);
  rowsRef.current = rows;
  const activateRef = React.useRef(onActivate);
  activateRef.current = onActivate;
  const clearRef = React.useRef(onClear);
  clearRef.current = onClear;
  // Read inside the stable row key handler, so "is the row open somewhere?"
  // is the latest value rather than the one captured at row creation.
  const selectedRef = React.useRef(selectedSymbol);
  selectedRef.current = selectedSymbol;
  const pointRef = React.useRef(onRowPoint);
  pointRef.current = onRowPoint;

  const activate = React.useCallback((symbol: string) => activateRef.current?.(symbol), []);
  const point = React.useCallback((symbol: string | null) => pointRef.current?.(symbol), []);
  const setRowRef = React.useCallback((index: number, element: HTMLTableRowElement | null) => {
    rowRefs.current[index] = element;
  }, []);
  /** One menu at a time: opening another row's menu closes the first. */
  const toggleMenu = React.useCallback((rowId: string, kind: RowMenuKind) => {
    setMenu((current) =>
      current && current.rowId === rowId && current.kind === kind ? null : { rowId, kind },
    );
  }, []);
  const closeMenu = React.useCallback(() => setMenu(null), []);

  const focusRow = React.useCallback((index: number) => {
    const bounded = Math.max(0, Math.min(rowsRef.current.length - 1, index));
    setFocused(bounded);
    rowRefs.current[bounded]?.focus();
  }, []);

  const onRowKeyDown = React.useCallback(
    (event: React.KeyboardEvent<HTMLTableRowElement>, index: number) => {
      // Keys belong to the row only while the row itself has focus. A button
      // inside it (the "…" menu, a micro-action) owns its own Enter and Space;
      // letting them bubble would open the ticker page on the same press.
      if (event.target !== event.currentTarget) return;
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
          // F6: with the row's detail panel open, Escape closes it through the
          // same `onClear` the parent owns, and focus stays on this row — where
          // the reader was. Blurring here used to drop focus to `<body>`: the
          // row blurred itself before the parent's document listener ran, and
          // that listener then stood down on the row's `defaultPrevented`.
          const panelOpen = Boolean(selectedRef.current);
          clearRef.current?.();
          if (panelOpen) {
            event.currentTarget.focus();
            break;
          }
          // Nothing open: Escape clears the roving cursor, as it always has.
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
    setMenu(null);
  }, [view.name]);

  const hasRows = rows.length > 0;
  const columnCount = view.columns.length + 1 + (compareActive ? 1 : 0);
  // F7: the grid announces the whole screen, not the page it happens to hold,
  // plus the header row that `aria-rowcount` counts. Never fewer than the rows
  // actually rendered — a body that under-reports itself is worse than a stale
  // total.
  const announcedRows = Math.max(rowCount, rows.length) + 1;
  /** The table's own width: the solved columns, plus compare's lane in mode. */
  const tableWidth = (solved?.total ?? 0) + (compareActive ? COMPARE_LANE_WIDTH : 0);

  return (
    <div
      ref={scrollRef}
      data-testid="grid-scroll"
      // The grid is not a scroll box: the page owns the vertical scroll, so
      // every row of the page keeps its natural height and nothing is clipped
      // behind an inner viewport. Only a table wider than its panel scrolls
      // sideways, and even then its height is its content's.
      //
      // When the solver reports `overflow`, the table keeps its declared
      // minimums and this box scrolls horizontally; otherwise the widths are
      // exact and it has nothing to scroll (adversarial: no view scrolls at
      // 1280).
      className={cn("relative bg-surface", solved?.overflow && "overflow-x-auto")}
      data-overflow={solved ? (solved.overflow ? "true" : "false") : undefined}
      data-scroll="page"
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
        // Every header is a sort control, and `aria-sort` says so.
        //
        // Pressing a header rewrites the one ordering token the request carries
        // (`criteria.sort`), so the server orders the whole filtered universe
        // before the page is cut — never the 20 rows on screen. `descending`
        // and `ascending` are the truth for the one column in force; `none`
        // marks the others as sortable without claiming a direction. The
        // caption still says the ordering in words, because a screen reader
        // user who lands in the body has not necessarily heard a header.
        <table
          data-testid="grid-table"
          data-stale={stale ? "true" : "false"}
          aria-rowcount={hasRows ? announcedRows : undefined}
          aria-colcount={columnCount}
          aria-describedby={solved.overflow ? OVERFLOW_NOTE_ID : undefined}
          style={{
            // `minWidth` is belt and braces for the fixed layout: the browser
            // may never lay this table out narrower than the widths it was
            // given, so a numeric cell cannot be squeezed into an ellipsis.
            width: tableWidth,
            minWidth: tableWidth,
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
          {/* Visually hidden, but a real caption: it is the table's name in the
              accessibility tree, and it is the only place the ordering is
              stated in words. `sr-only` takes it out of the fixed layout, so
              it costs no column width. */}
          <caption className="sr-only" data-testid="grid-caption">
            {caption ??
              `${view.label}: ${hasRows ? `${rows.length} rows on this page` : "no rows"}.`}
          </caption>
          <colgroup>
            {compareActive ? <col style={{ width: COMPARE_LANE_WIDTH }} /> : null}
            {solved.columns.map((column, index) => (
              <col key={column.key} style={{ width: solved.widths[index] ?? 0 }} />
            ))}
          </colgroup>

          {/* Sticky header: the column names stay at the top of the scroll box
              while the rows travel under them. Each `<th>` still carries its
              own `position: sticky; top: 0` inline (the pinned lanes need
              per-cell offsets); the wrapper makes the row paint as one band. */}
          <thead className="sticky top-0 z-10">
            {table.getHeaderGroups().map((group) => {
              const byId = (id: string) =>
                group.headers.find((header) => header.column.id === id);
              const numberHeader = byId(ROW_NUMBER_KEY);
              const identityHeader = byId("identity");
              const rest = group.headers.filter(
                (header) =>
                  header.column.id !== "identity" &&
                  header.column.id !== ROW_NUMBER_KEY &&
                  (!range || columns.includes(header.column.id)),
              );
              const draw = (header: (typeof group.headers)[number]) => {
                const key = header.column.id;
                const meta = headers.get(key);
                const sticky = header.column.getIsPinned() === "start";
                // The identity lane draws the ticker, so its sort token is the
                // catalogue's spelling for the symbol — never "identity". The
                // row number is geometry, not a field, and cannot be sorted.
                const token =
                  key === "identity" ? "ticker" : isClientColumn(key) ? null : key;
                const active = token !== null && sortedKey === token;
                const ariaSort =
                  token === null
                    ? undefined
                    : active
                      ? sortedDirection === "desc"
                        ? "descending"
                        : "ascending"
                      : "none";
                const label = meta?.text ?? key;
                const lane = key === "identity" || key === ROW_NUMBER_KEY;
                return (
                  <th
                    key={header.id}
                    scope="col"
                    data-key={key}
                    aria-sort={ariaSort}
                    style={{
                      ...STICKY_HEADER,
                      ...(sticky ? STICKY_LANE : {}),
                      ...(key === ROW_NUMBER_KEY
                        ? { left: compareActive ? COMPARE_LANE_WIDTH : 0 }
                        : {}),
                      ...(key === "identity"
                        ? {
                            left:
                              (compareActive ? COMPARE_LANE_WIDTH : 0) +
                              (showNumber ? columnSpec(ROW_NUMBER_KEY).min : 0),
                          }
                        : {}),
                      height: HEADER_HEIGHT,
                      zIndex: sticky ? 40 : 20,
                      padding: 0,
                    }}
                    className={cn(
                      "border-b border-line-strong bg-surface-raised text-left font-mono text-label uppercase tracking-[0.08em] text-fg-muted",
                      sticky && "bg-surface-raised shadow-[inset_-1px_0_0_var(--border-strong)]",
                    )}
                    title={meta?.full ?? key}
                  >
                    {token !== null && onSort ? (
                      <button
                        type="button"
                        data-testid={`sort-${token}`}
                        onClick={() => onSort(token)}
                        title={`Sort by ${meta?.full ?? key}, ${
                          active && sortedDirection === "asc" ? "lowest" : "highest"
                        } first.`}
                        className={cn(
                          "flex h-full w-full min-w-0 items-center gap-1 px-2 py-0 uppercase tracking-[0.08em] transition-colors hover:text-fg",
                          lane ? "justify-start" : "justify-end",
                          active && "text-fg",
                        )}
                      >
                        <span className="block truncate">{label}</span>
                        {active ? (
                          <span
                            aria-hidden="true"
                            data-testid={`sort-arrow-${token}`}
                            data-direction={sortedDirection}
                            className="shrink-0 text-accent-text"
                          >
                            {sortedDirection === "desc" ? "▾" : "▴"}
                          </span>
                        ) : null}
                      </button>
                    ) : (
                      <span className="block truncate px-2">{label}</span>
                    )}
                  </th>
                );
              };
              return (
                <tr key={group.id}>
                  {compareActive ? (
                    <th
                      scope="col"
                      data-testid="compare-lane-header"
                      style={{
                        ...STICKY_HEADER,
                        ...STICKY_LANE,
                        left: 0,
                        width: COMPARE_LANE_WIDTH,
                        height: HEADER_HEIGHT,
                        zIndex: 45,
                        padding: 0,
                      }}
                      className="border-b border-line-strong bg-surface-raised shadow-[inset_-1px_0_0_var(--border-strong)]"
                    >
                      <span className="sr-only">Compare</span>
                    </th>
                  ) : null}
                  {numberHeader ? draw(numberHeader) : null}
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
              <SkeletonRows
                columns={solved.columns.length + (compareActive ? 1 : 0)}
                widths={
                  compareActive ? [COMPARE_LANE_WIDTH, ...solved.widths] : solved.widths
                }
              />
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
                  rowNumber={pageIndex * pageSize + index + 1}
                  showNumber={showNumber}
                  current={currentSymbol === symbol}
                  linkTo={rowTo ? rowTo(symbol) : null}
                  tabbable={focused === index || (focused === -1 && index === 0)}
                  specs={specs}
                  absent={absent}
                  rowActions={rowActions}
                  starred={starred.has(symbol)}
                  alertCount={alertCounts?.get(symbol) ?? 0}
                  compareChecked={compareSelected.has(symbol)}
                  compareDisabled={
                    compareActive && !compareSelected.has(symbol) && compareSelected.size >= compareMax
                  }
                  onToggleCompare={compareSelection?.onToggle}
                  menuKind={menu?.rowId === (rowIds[index] ?? String(index)) ? menu.kind : null}
                  onToggleMenu={toggleMenu}
                  onCloseMenu={closeMenu}
                  onPoint={point}
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
 * re-rendering all of them because one row's menu opened measured at
 * ~110 ms in Chrome — a visible stall on a click. With identity-stable
 * callbacks and a shallow comparison, opening a row's menu re-renders that
 * row and nothing else.
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
  /** The page-global ordinal: page offset + row index + 1. */
  rowNumber: number;
  /** Whether the view draws the row-number lane. */
  showNumber: boolean;
  /** The row is the one `aria-current` names — at most one per table. */
  current: boolean;
  /** The ticker page's route path, or null when the grid is not a door. */
  linkTo: string | null;
  tabbable: boolean;
  specs: Map<string, ColumnSpec>;
  absent?: (symbol: string, key: string) => string | null;
  rowActions?: RowActions;
  /** The symbol is on the watchlist or in the portfolio. */
  starred: boolean;
  /** How many saved alerts this symbol has; zero means an outline bell. */
  alertCount: number;
  /** Compare mode's box for this row; absent outside compare mode. */
  compareChecked: boolean;
  compareDisabled: boolean;
  onToggleCompare?: (symbol: string) => void;
  menuKind: RowMenuKind | null;
  onToggleMenu: (rowId: string, kind: RowMenuKind) => void;
  onCloseMenu: () => void;
  onPoint: (symbol: string | null) => void;
  onActivate: (symbol: string) => void;
  onFocusRow: (index: number) => void;
  onKeyDown: (event: React.KeyboardEvent<HTMLTableRowElement>, index: number) => void;
  setRowRef: (index: number, element: HTMLTableRowElement | null) => void;
}

const ACTION_BUTTON =
  "flex size-6 items-center justify-center rounded-sm text-fg-muted transition-colors hover:bg-row-hover hover:text-fg focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-focus disabled:opacity-40";

const ROW_MENU_ITEM =
  "flex w-full items-center gap-1.5 px-2.5 py-1 text-left font-mono text-label uppercase tracking-[0.08em] text-fg-muted transition-colors hover:bg-row-hover hover:text-fg";

/**
 * The star, the bell and the "…" door, absolutely positioned over the row's
 * last cell.
 *
 * The lane is `absolute; right: 0` so it claims no width: the solver's columns
 * stay exactly where they were, and the row never shifts as the pointer moves.
 * Its fade is a gradient of the row's own surface, so the numbers behind it
 * remain readable at the left edge; the icons are 20 px, the hit targets 24 px.
 *
 * Visibility is CSS, not state: a row that is hovered or has focus within
 * shows the lane, and below 900 px it is always visible — that is the touch
 * case, where hover does not exist. The "…" button is the one Tab stop; the
 * two glyphs are pointer accelerators (`tabIndex={-1}`), because twenty rows
 * × three buttons of tab stops would make the grid a maze.
 */
function RowActionLane({
  symbol,
  rowId,
  actions,
  starred,
  alertCount,
  menuKind,
  tabbable,
  onToggleMenu,
  onCloseMenu,
  onReturnFocus,
}: {
  symbol: string;
  rowId: string;
  actions: RowActions;
  starred: boolean;
  /** How many saved alerts this symbol has; zero means an outline bell. */
  alertCount: number;
  menuKind: RowMenuKind | null;
  tabbable: boolean;
  onToggleMenu: (rowId: string, kind: RowMenuKind) => void;
  onCloseMenu: () => void;
  onReturnFocus: () => void;
}) {
  const menuRef = React.useRef<HTMLDivElement>(null);

  // Move focus to the first item on open, and close on a pointer elsewhere.
  React.useEffect(() => {
    if (menuKind === null) return;
    menuRef.current?.querySelector<HTMLElement>('[role="menuitem"]')?.focus();
    const onPointerDown = (event: PointerEvent) => {
      if (!menuRef.current?.contains(event.target as Node)) onCloseMenu();
    };
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [menuKind, onCloseMenu]);

  const menu = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== "Escape") return;
    event.preventDefault();
    // The row owns Escape while a menu is open: close first, and put the
    // keyboard back where it was, rather than dropping focus to <body>.
    event.stopPropagation();
    onCloseMenu();
    onReturnFocus();
  };

  const menuStyle =
    "absolute right-0 top-full z-40 mt-0.5 min-w-[150px] overflow-hidden rounded-sm border border-line-strong bg-surface-raised py-0.5 shadow-lg";

  return (
    <div
      data-row-actions="true"
      data-testid={`row-actions-${symbol}`}
      data-menu={menuKind ?? undefined}
      onClick={(event) => event.stopPropagation()}
      className={cn(
        // Absolute over the row's right edge: no column, no layout shift.
        "absolute right-0 top-0 z-20 flex h-full items-center gap-0.5 rounded-l-sm pl-5 pr-1",
        // A soft fade of the row's own surface keeps the numbers legible
        // where the lane ends, instead of a hard rectangle.
        "bg-gradient-to-l from-surface via-surface/95 to-transparent",
        "group-hover:from-row-hover group-hover:via-row-hover/95",
        "pointer-events-none opacity-0 transition-opacity duration-100",
        "group-hover:pointer-events-auto group-hover:opacity-100",
        "group-focus-within:pointer-events-auto group-focus-within:opacity-100",
        // Touch: there is no hover, so the lane is simply there.
        "max-[900px]:pointer-events-auto max-[900px]:opacity-100",
        menuKind !== null && "pointer-events-auto opacity-100",
      )}
    >
      <button
        type="button"
        tabIndex={-1}
        data-starred={starred ? "true" : "false"}
        aria-label={
          starred
            ? `Add ${symbol} to a watchlist or portfolio (on a list)`
            : `Add ${symbol} to a watchlist or portfolio`
        }
        aria-haspopup="menu"
        aria-expanded={menuKind === "add"}
        title={starred ? `${symbol} is on a list — add another or pick a list` : `Add ${symbol} to a list`}
        onClick={(event) => {
          event.stopPropagation();
          onToggleMenu(rowId, "add");
        }}
        className={ACTION_BUTTON}
      >
        <Icon name="star" size={20} className={starred ? "fill-current text-alert" : undefined} />
      </button>
      <button
        type="button"
        tabIndex={-1}
        data-alerted={alertCount > 0 ? "true" : "false"}
        data-alert-count={alertCount}
        aria-label={
          alertCount > 0
            ? `Set an alert for ${symbol} (${alertCount} active)`
            : `Set an alert for ${symbol}`
        }
        title={
          alertCount > 0
            ? `${alertCount} alert${alertCount === 1 ? "" : "s"} for ${symbol} — review or add another`
            : `Set a price alert for ${symbol}`
        }
        onClick={(event) => {
          event.stopPropagation();
          onCloseMenu();
          actions.onAlert(symbol);
        }}
        className={ACTION_BUTTON}
      >
        <Icon
          name="bell"
          size={20}
          className={alertCount > 0 ? "fill-current text-accent-text" : undefined}
        />
      </button>
      <button
        type="button"
        data-testid={`row-menu-button-${symbol}`}
        tabIndex={tabbable ? 0 : -1}
        aria-label={`Row actions for ${symbol}`}
        aria-haspopup="menu"
        aria-expanded={menuKind === "rows"}
        title={`Row actions for ${symbol}`}
        onClick={(event) => {
          event.stopPropagation();
          onToggleMenu(rowId, "rows");
        }}
        className={ACTION_BUTTON}
      >
        <Icon name="more-horizontal" size={20} />
      </button>

      {menuKind === "add" ? (
        <div ref={menuRef} role="menu" aria-label="Add to" className={menuStyle} onKeyDown={menu}>
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              onCloseMenu();
              actions.onAddTo(symbol, "watchlist");
            }}
            className={ROW_MENU_ITEM}
          >
            <Icon name="star" />
            Watchlist
          </button>
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              onCloseMenu();
              actions.onAddTo(symbol, "portfolio");
            }}
            className={ROW_MENU_ITEM}
          >
            <Icon name="list" />
            Portfolio
          </button>
        </div>
      ) : null}

      {menuKind === "rows" ? (
        <div
          ref={menuRef}
          role="menu"
          aria-label="Row actions"
          className={menuStyle}
          onKeyDown={menu}
        >
          <button
            type="button"
            role="menuitem"
            aria-haspopup="menu"
            onClick={() => onToggleMenu(rowId, "add")}
            className={ROW_MENU_ITEM}
          >
            <Icon name="star" />
            Add to
          </button>
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              onCloseMenu();
              actions.onAlert(symbol);
            }}
            className={ROW_MENU_ITEM}
          >
            <Icon name="bell" />
            Set alert
          </button>
        </div>
      ) : null}
    </div>
  );
}

const GridRow = React.memo(function GridRow({
  symbol,
  data,
  rowId,
  cells,
  lead,
  trail,
  index,
  rowNumber,
  showNumber,
  current,
  linkTo,
  tabbable,
  specs,
  absent,
  rowActions,
  starred,
  alertCount,
  compareChecked,
  compareDisabled,
  onToggleCompare,
  menuKind,
  onToggleMenu,
  onCloseMenu,
  onPoint,
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
      data-selected={current ? "true" : "false"}
      // F3: `aria-selected` on a `<tr>` is only mapped inside `role=grid` /
      // `treegrid`; this table is a plain `table`, so Chrome dropped it and the
      // open row reached assistive tech with no state at all. `aria-current`
      // is valid on any element in a table and is what the browser's AX tree
      // keeps — one current item per document, and the tab strip marks itself
      // with `aria-selected` (role=tab), so the two cannot be confused.
      aria-current={current ? "true" : undefined}
      // The row is a tab stop (one per row, never one per cell), so it is
      // focusable and therefore *named*: a focused `<tr>` with no accessible
      // name reaches a screen reader as "row", and the symbol the reader is on
      // — the one thing this grid is about — is not said. The cells keep their
      // own text; this only names the row. Adversarial (c): the symbol is
      // spoken, and no colour is involved.
      aria-label={symbol}
      tabIndex={tabbable ? 0 : -1}
      onMouseEnter={() => onPoint(symbol)}
      onMouseLeave={(event) => {
        // A keyboard cursor on this row outlives the pointer passing over it:
        // the toolbar's subject is still the focused row.
        if (event.currentTarget.contains(document.activeElement)) return;
        onPoint(null);
      }}
      onFocus={(event) => {
        if (event.target !== event.currentTarget) return;
        onFocusRow(index);
        onPoint(symbol);
      }}
      onBlur={(event) => {
        const next = event.relatedTarget as Node | null;
        if (next !== null && event.currentTarget.contains(next)) return;
        onPoint(null);
      }}
      onKeyDown={(event) => onKeyDown(event, index)}
      onClick={(event) => {
        // A link, a micro-action or the "…" menu owns its own click; a plain
        // press on any other cell is the same door as the ticker.
        if ((event.target as HTMLElement).closest("a, input, button, label")) return;
        onActivate(symbol);
      }}
      className={cn(
        "group relative cursor-pointer outline-offset-[-2px] odd:bg-row even:bg-row-alt",
        // One 2 px rose ring around the row, drawn inset so the sticky header
        // cannot clip it. `relative z-10` while focused lifts the ring above the
        // pinned lane's own background — without it the sticky `<td>` paints
        // over the ring's leading edge and the focus indicator looks like it
        // stops at the lane.
        "hover:bg-row-hover focus-visible:relative focus-visible:z-10 focus-visible:outline-2 focus-visible:outline-focus",
        "data-[selected=true]:bg-row-selected",
      )}
      style={{ height: ROW_HEIGHT }}
    >
      {onToggleCompare ? (
        <td
          onClick={(event) => event.stopPropagation()}
          style={{ ...STICKY_LANE, left: 0, height: ROW_HEIGHT, zIndex: 5 }}
          className="bg-inherit px-0 py-0 text-center align-middle"
        >
          <input
            type="checkbox"
            aria-label={`Compare ${symbol}`}
            checked={compareChecked}
            disabled={compareDisabled}
            onChange={() => onToggleCompare(symbol)}
            onClick={(event) => event.stopPropagation()}
            className="size-3 cursor-pointer align-middle accent-accent-fg"
          />
        </td>
      ) : null}
      {showNumber ? (
        <td
          data-row-number={rowNumber}
          style={{
            ...STICKY_LANE,
            left: onToggleCompare ? COMPARE_LANE_WIDTH : 0,
            height: ROW_HEIGHT,
            zIndex: 3,
          }}
          className="overflow-hidden bg-inherit px-2 py-0 text-left align-middle tabular-nums text-fg-subtle"
        >
          {rowNumber}
        </td>
      ) : null}
      <td
        style={{
          ...STICKY_LANE,
          left:
            (onToggleCompare ? COMPARE_LANE_WIDTH : 0) +
            (showNumber ? columnSpec(ROW_NUMBER_KEY).min : 0),
          height: ROW_HEIGHT,
          zIndex: 2,
        }}
        className="overflow-hidden bg-inherit px-2 py-0 text-left align-middle shadow-[inset_-1px_0_0_var(--border-strong)]"
      >
        {linkTo ? (
          <Link
            to={linkTo}
            data-testid={`symbol-link-${symbol}`}
            // Not a second tab stop: the row already is one, and 20 more per
            // page would make the grid a tab maze. The link is still a real
            // link to a screen reader and to the pointer.
            tabIndex={-1}
            className="block min-w-0 text-fg visited:text-fg hover:text-accent-text"
            onDragStart={(event) => event.preventDefault()}
          >
            <IdentityCell
              symbol={symbol}
              company={typeof data["company"] === "string" ? data["company"] : null}
            />
          </Link>
        ) : (
          <IdentityCell
            symbol={symbol}
            company={typeof data["company"] === "string" ? data["company"] : null}
          />
        )}
      </td>
      {lead > 0 ? (
        <td aria-hidden="true" colSpan={lead} style={{ height: ROW_HEIGHT }} className="p-0" />
      ) : null}
      {cells.map((key, cellIndex) => {
        const gap = absent?.(symbol, key) ?? null;
        const last = trail === 0 && cellIndex === cells.length - 1;
        return (
          <td
            key={`${rowId}:${key}`}
            style={{ height: ROW_HEIGHT }}
            // The last cell is the anchor: the action lane is positioned
            // against it and may hang over the cell's left edge, so that one
            // must not clip. Every other cell keeps its overflow guard.
            className={cn("overflow-hidden px-2 py-0 align-middle", last && "relative overflow-visible")}
          >
            {gap !== null ? (
              <AbsentCell reason={gap} />
            ) : (
              <CellValue spec={specs.get(key) ?? columnSpec(key)} value={data[key]} />
            )}
            {last && rowActions ? (
              <RowActionLane
                symbol={symbol}
                rowId={rowId}
                actions={rowActions}
                starred={starred}
                alertCount={alertCount}
                menuKind={menuKind}
                tabbable={tabbable}
                onToggleMenu={onToggleMenu}
                onCloseMenu={onCloseMenu}
                onReturnFocus={() => onFocusRow(index)}
              />
            ) : null}
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
          className={cn("h-6", row % 2 === 0 ? "bg-row" : "bg-row-alt")}
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
