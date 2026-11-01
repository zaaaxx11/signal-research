/**
 * One alt table, three markets: Crypto, Futures, Forex.
 *
 * The shell mounts one component with a `marketClass` prop, so the three pages
 * cannot drift into three interpretations of the same quote. What the page
 * draws:
 *
 * * a title and one line saying what the class is and how its price is quoted;
 * * a filter box that narrows locally (focus never leaves it, and it never
 *   asks the server again);
 * * a table — Symbol, Name, Last, Change, Volume/Contract, Currency — whose
 *   sortable headers carry `aria-sort` and whose change cell is the grid's own
 *   delta renderer (arrow, sign, `sr-only` word), so flat is visibly flat and
 *   colour is never the only signal;
 * * a footer stating when the data was read and that a closed market shows its
 *   last session — this page never calls a quote live.
 *
 * One reserved box holds loading, error, empty and the table, so the answer
 * landing cannot move what is below it.
 */

import * as React from "react";

import { CellValue } from "@/grid/cellRenderers";
import { LogoMark } from "@/grid/LogoMark";
import { Link } from "@/shell/routes";
import { cn } from "@/lib/utils";

import {
  CHANGE_SPEC,
  classCopy,
  formatPrice,
  formatReadAt,
  formatVolume,
  nextSort,
  stockHref,
  visibleRows,
  type AltRow,
  type AltReport,
  type MarketClass,
  type SortKey,
  type SortState,
} from "./alt";
import { useAlt } from "./useAlt";

/** Skeleton rows: the first screen of a full page, not a fake table. */
export const ALT_SKELETON_ROWS = 20;

/**
 * The box every state occupies, in px: a 26 px header row plus twenty 24 px
 * rows plus the frame's border. A full page is taller; the floor is what stops
 * the answer from starting higher up the page than the placeholder did.
 */
export const ALT_RESERVED_HEIGHT = 26 + ALT_SKELETON_ROWS * 24 + 2;

const COLUMNS: readonly {
  readonly key: SortKey;
  /** The DOM's name for the column: `last` reads better than `price` in a test id. */
  readonly id: string;
  readonly label: string;
  readonly align: "left" | "right";
}[] = [
  { key: "symbol", id: "symbol", label: "Symbol", align: "left" },
  { key: "price", id: "last", label: "Last", align: "right" },
  { key: "change", id: "change", label: "Change", align: "right" },
];

function arrowFor(sort: SortState | null, key: SortKey): string {
  if (sort === null || sort.key !== key) return "";
  return sort.dir === "asc" ? "▲" : "▼";
}

function ariaSortFor(sort: SortState | null, key: SortKey): "none" | "ascending" | "descending" {
  if (sort === null || sort.key !== key) return "none";
  return sort.dir === "asc" ? "ascending" : "descending";
}

/** An absent figure prints an em dash with a name, never a zero and never blank. */
function Absent() {
  return (
    <span className="text-fg-subtle" title="Not available" aria-label="Not available">
      —
    </span>
  );
}

function Skeleton() {
  return (
    <div data-testid="alt-skeleton" aria-busy="true">
      <span className="sr-only">Loading quotes</span>
      <div aria-hidden="true" className="h-[26px] border-b border-line-strong" />
      {Array.from({ length: ALT_SKELETON_ROWS }, (_, index) => (
        <div
          key={index}
          aria-hidden="true"
          data-testid="alt-skeleton-row"
          className="flex h-6 items-center gap-2 border-b border-line/60 px-2"
        >
          <span className="size-4 shrink-0 rounded-[2px] bg-surface-raised" />
          <span
            className={cn(
              "block h-[9px] rounded-[1px] bg-surface-raised",
              index % 3 === 0 ? "w-[14%]" : index % 3 === 1 ? "w-[18%]" : "w-[11%]",
            )}
          />
          <span className="block h-[9px] flex-1 rounded-[1px] bg-surface-raised" />
          <span className="block h-[9px] w-[9%] rounded-[1px] bg-surface-raised" />
          <span className="block h-[9px] w-[7%] rounded-[1px] bg-surface-raised" />
        </div>
      ))}
    </div>
  );
}

function HeadCell({
  column,
  sort,
  onSort,
}: {
  readonly column: (typeof COLUMNS)[number];
  readonly sort: SortState | null;
  readonly onSort: (key: SortKey) => void;
}) {
  const active = sort !== null && sort.key === column.key;
  const arrow = arrowFor(sort, column.key);

  return (
    <th
      scope="col"
      data-testid={`alt-th-${column.id}`}
      aria-sort={ariaSortFor(sort, column.key)}
      className={cn(
        "px-2 font-medium",
        column.align === "right" ? "text-right" : "text-left",
      )}
    >
      <button
        type="button"
        data-testid={`alt-sort-${column.id}`}
        onClick={() => onSort(column.key)}
        className={cn(
          "focus-ring flex w-full items-center gap-1 rounded-[1px] py-0.5 uppercase tracking-[0.05em] transition-colors hover:text-fg",
          column.align === "right" ? "justify-end" : "justify-start",
          active ? "text-fg" : "text-fg-subtle",
        )}
      >
        <span>{column.label}</span>
        {/* The glyph repeats `aria-sort`, so it is hidden from a screen reader. */}
        <span aria-hidden="true" className="w-2 text-[0.75em] leading-none">
          {arrow}
        </span>
      </button>
    </th>
  );
}

function Row({ row, hasAmount }: { readonly row: AltRow; readonly hasAmount: boolean }) {
  const href = stockHref(row.symbol);
  const name = row.name ?? row.symbol;
  // Formatted once: the title and the cell text are the same string, and a
  // 40-row table should not build 80 `Intl` results per render.
  const price = formatPrice(row.price);
  // A class that does not report an amount shows "—", never the sentinel zero
  // the quote stack puts in `volume` when it has nothing to count.
  const volume = hasAmount ? formatVolume(row.volume) : null;

  const identity = (
    <>
      <LogoMark symbol={row.symbol} size={16} />
      <span className="min-w-0 truncate font-mono text-data font-medium text-fg">{row.symbol}</span>
    </>
  );

  return (
    <tr
      data-testid="alt-row"
      data-symbol={row.symbol}
      className="h-6 border-b border-line/60 odd:bg-row-alt hover:bg-row-hover"
    >
      <td data-testid="alt-symbol" className="min-w-0 px-2">
        {/* No detail page exists for this class (see `stockHref`): the symbol
            is text, not a link that would land on "not found". */}
        {href === null ? (
          <span className="flex min-w-0 items-center gap-1.5">{identity}</span>
        ) : (
          <Link
            to={href}
            title={`${row.symbol} — open${row.name ? `: ${row.name}` : ""}`}
            className="flex min-w-0 items-center gap-1.5 hover:text-accent-text"
          >
            {identity}
          </Link>
        )}
      </td>
      <td
        data-testid="alt-name"
        data-name-missing={row.name === null ? "true" : undefined}
        title={row.name ?? `${row.symbol} (the endpoint sent no name)`}
        className={cn("min-w-0 px-2", row.name === null ? "font-mono text-fg-subtle" : "text-fg-muted")}
      >
        <span className="block truncate">{name}</span>
      </td>
      <td data-testid="alt-last" className="px-2 text-right nums" title={price}>
        {price}
      </td>
      <td data-testid="alt-change" className="px-2">
        <CellValue spec={CHANGE_SPEC} value={row.changePct} />
      </td>
      <td
        data-testid="alt-volume"
        title={hasAmount ? undefined : "Spot forex has no consolidated volume"}
        className="px-2 text-right text-fg-muted nums"
      >
        {volume === null ? <Absent /> : volume}
      </td>
      <td
        data-testid="alt-currency"
        className="px-2 font-mono text-label uppercase tracking-[0.06em] text-fg-subtle"
      >
        {row.currency ?? <Absent />}
      </td>
    </tr>
  );
}

function Table({
  report,
  rows,
  sort,
  amountLabel,
  hasAmount,
  onSort,
}: {
  readonly report: AltReport;
  readonly rows: readonly AltRow[];
  readonly sort: SortState | null;
  readonly amountLabel: string;
  readonly hasAmount: boolean;
  readonly onSort: (key: SortKey) => void;
}) {
  const order =
    sort === null
      ? "in the order the endpoint sent them"
      : `sorted by ${sort.key === "price" ? "last" : sort.key}, ${sort.dir === "asc" ? "ascending" : "descending"}`;

  return (
    <div className="overflow-x-auto">
      <table
        data-testid="alt-table"
        className="w-full min-w-[720px] table-fixed border-collapse text-data"
      >
        <caption className="sr-only">
          {report.class} quotes, {rows.length} rows, {order}
        </caption>
        <colgroup>
          <col className="w-[17%]" />
          <col className="w-[35%]" />
          <col className="w-[14%]" />
          <col className="w-[11%]" />
          <col className="w-[14%]" />
          <col className="w-[9%]" />
        </colgroup>
        <thead>
          <tr className="h-[26px] border-b border-line-strong text-label uppercase tracking-[0.05em] text-fg-subtle">
            {COLUMNS.map((column) => (
              <HeadCell key={column.key} column={column} sort={sort} onSort={onSort} />
            ))}
            <th
              scope="col"
              data-testid="alt-th-volume"
              className="px-2 text-right font-medium"
            >
              {amountLabel}
            </th>
            <th scope="col" data-testid="alt-th-currency" className="px-2 text-left font-medium">
              Currency
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <Row key={row.symbol} row={row} hasAmount={hasAmount} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

export interface AltPageProps {
  readonly marketClass: MarketClass;
}

export function AltPage({ marketClass }: AltPageProps) {
  const alt = useAlt(marketClass);
  const copy = classCopy(marketClass);
  const [query, setQuery] = React.useState("");
  const [sort, setSort] = React.useState<SortState | null>(null);

  const report = alt.report;
  const total = report?.rows.length ?? 0;
  const rows = React.useMemo(
    () => (report === null ? [] : visibleRows(report.rows, query, sort)),
    [report, query, sort],
  );

  const onSort = React.useCallback((key: SortKey) => {
    setSort((current) => nextSort(current, key));
  }, []);

  const stamp = report === null ? null : formatReadAt(report.asOf);
  const universe = report?.universeTotal ?? null;
  const measured = report?.measured ?? null;

  return (
    <main
      data-testid="alt-page"
      data-class={marketClass}
      className="min-h-0 flex-1 overflow-y-auto bg-bg px-2 py-2"
    >
      <div className="mb-1 flex min-w-0 items-baseline justify-between gap-3 px-0.5">
        <h1 className="text-ui font-medium text-fg">{copy.title}</h1>
        <p className="font-mono text-label uppercase tracking-[0.08em] text-fg-subtle">
          {universe === null ? `${total} symbols` : `${universe} symbols in this class`}
        </p>
      </div>

      <p className="mb-1.5 max-w-[78ch] px-0.5 text-data text-fg-muted">{copy.blurb}</p>

      {/* The filter lives outside the state box on purpose: a box that sits
          inside the branch would be unmounted when the table becomes empty,
          and the reader would lose both their place and the text they typed. */}
      <div className="mb-1.5 flex flex-wrap items-center gap-2 px-0.5">
        <label htmlFor="alt-filter" className="sr-only">
          Filter {copy.title} quotes by symbol or name
        </label>
        <input
          id="alt-filter"
          data-testid="alt-filter"
          type="search"
          autoComplete="off"
          spellCheck={false}
          placeholder="Filter symbol or name"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          className="focus-ring h-6 w-[220px] rounded-sm border border-line-control bg-surface px-1.5 text-data text-fg placeholder:text-fg-disabled"
        />
        <span data-testid="alt-count" className="nums text-label text-fg-subtle">
          {query.trim().length === 0 ? `${total} rows` : `${rows.length} of ${total} rows`}
        </span>
      </div>

      <div
        data-testid="alt-frame"
        style={{ minHeight: ALT_RESERVED_HEIGHT }}
        className="overflow-hidden rounded-md border border-line bg-surface"
      >
        {alt.status === "loading" ? (
          <Skeleton />
        ) : alt.status === "error" ? (
          <div
            data-testid="alt-error"
            role="alert"
            className="flex items-start justify-between gap-3 px-3 py-2"
          >
            <div className="min-w-0">
              <p className="text-data text-fg">{copy.title} quotes did not load.</p>
              <p className="mt-0.5 text-data text-fg-muted">{alt.error}</p>
            </div>
            <button
              type="button"
              data-testid="alt-retry"
              onClick={alt.retry}
              className="focus-ring shrink-0 rounded-sm border border-line px-2 py-0.5 text-data text-fg-muted transition-colors hover:border-line-strong hover:text-fg"
            >
              Retry
            </button>
          </div>
        ) : report === null || report.rows.length === 0 ? (
          <p data-testid="alt-empty" className="px-3 py-2 text-data text-fg-muted">
            No {copy.title.toLowerCase()} quotes right now.
          </p>
        ) : rows.length === 0 ? (
          <div data-testid="alt-filter-empty" className="flex items-center gap-2 px-3 py-2 text-data">
            <p className="min-w-0 text-fg-muted">Nothing matches “{query.trim()}”.</p>
            <button
              type="button"
              data-testid="alt-filter-clear"
              onClick={() => setQuery("")}
              className="focus-ring shrink-0 rounded-sm border border-line px-2 py-0.5 text-data text-fg-muted transition-colors hover:border-line-strong hover:text-fg"
            >
              Clear filter
            </button>
          </div>
        ) : (
          <Table
            report={report}
            rows={rows}
            sort={sort}
            amountLabel={copy.amountLabel}
            hasAmount={copy.hasAmount}
            onSort={onSort}
          />
        )}
      </div>

      <footer data-testid="alt-footer" className="mt-1 flex flex-wrap gap-x-3 px-0.5 text-label text-fg-subtle">
        <span>
          {stamp === null ? "Read time unavailable" : `Read ${stamp}`}
        </span>
        <span aria-hidden="true">·</span>
        <span>A closed market shows its last session's price.</span>
        {measured !== null && universe !== null && measured < universe ? (
          <>
            <span aria-hidden="true">·</span>
            <span className="nums">
              {measured} of {universe} symbols priced
            </span>
          </>
        ) : null}
      </footer>
    </main>
  );
}
