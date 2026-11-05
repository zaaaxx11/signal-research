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

import { CardGrid, CardGridCard } from "@/components/CardGrid";
import { SectionTitle } from "@/components/SectionTitle";
import { CellValue } from "@/grid/cellRenderers";
import { LogoMark } from "@/grid/LogoMark";
import { Link } from "@/shell/routes";
import { cn } from "@/lib/utils";
import { quoteProps } from "@/lib/quoteAttrs";
import { notify } from "@/lib/notify";

import {
  CATEGORY_TABS,
  CHANGE_SPEC,
  categoryCounts,
  categoryRows,
  classCopy,
  flagForPair,
  forexStrength,
  forexStrengthLabel,
  formatPrice,
  formatReadAt,
  formatVolume,
  nextSort,
  stockHref,
  strengthScale,
  visibleRows,
  type AltRow,
  type AltReport,
  type CategoryTab,
  type MarketClass,
  type SortKey,
  type SortState,
} from "./alt";
import { useAlt } from "./useAlt";
import {
  useCryptoEnrich,
  useSparkline,
  useSparklineCache,
} from "./useCryptoEnrich";
import {
  formatCount,
  formatDominance,
  formatMarketCap,
  formatSignedPct,
  type CryptoMetaRow,
  type CryptoMetaDoc,
} from "./cryptoEnrich";

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

/** A 36×14 SVG directional indicator for the mini sparkline column. */
function MiniSparkline({ direction }: { readonly direction: "up" | "down" | "flat" }) {
  const stroke =
    direction === "up" ? "var(--up, #22c55e)" : direction === "down" ? "var(--down, #ef4444)" : "var(--fg-muted, #6b7280)";
  const d =
    direction === "up"
      ? "M2,12 L9,6 L16,7 L23,4 L30,8 L34,2"
      : direction === "down"
        ? "M2,2 L9,8 L16,7 L23,10 L30,6 L34,12"
        : "M2,7 L9,7 L16,7 L23,7 L30,7 L34,7";
  return (
    <svg
      width="36"
      height="14"
      viewBox="0 0 36 14"
      aria-hidden="true"
      className="inline-block align-middle"
    >
      <path
        d={d}
        fill="none"
        stroke={stroke}
        strokeWidth="1.25"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/** A 16px logo from CoinGecko's image URL, with monogram fallback on error. */
function CryptoLogo({ image, symbol }: { readonly image: string | null; readonly symbol: string }) {
  const [failed, setFailed] = React.useState(false);

  if (!image || failed) {
    return (
      <span
        aria-hidden="true"
        className="flex size-4 shrink-0 items-center justify-center rounded-[2px] border border-line bg-surface-raised"
      >
        <span className="font-mono text-[8px] leading-none text-fg-muted">
          {symbol.slice(0, 1).toUpperCase()}
        </span>
      </span>
    );
  }

  return (
    <img
      src={image}
      alt=""
      width={16}
      height={16}
      loading="lazy"
      onError={() => setFailed(true)}
      className="size-4 shrink-0 rounded-[2px] object-contain"
    />
  );
}

/** A 36×14 SVG sparkline drawn from an array of closes. */
function InlineSparkline({ closes, direction }: { readonly closes: readonly number[]; readonly direction: "up" | "down" | "flat" }) {
  if (closes.length < 2) return null;

  const stroke =
    direction === "up" ? "var(--up, #22c55e)" : direction === "down" ? "var(--down, #ef4444)" : "var(--fg-muted, #6b7280)";

  const min = Math.min(...closes);
  const max = Math.max(...closes);
  const range = max - min || 1;
  const w = 34;
  const h = 12;
  const pad = 1;

  const points = closes.map((v, i) => {
    const x = pad + (i / (closes.length - 1)) * (w - 2 * pad);
    const y = pad + (1 - (v - min) / range) * (h - 2 * pad);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });

  return (
    <svg
      width="36"
      height="14"
      viewBox="0 0 36 14"
      aria-hidden="true"
      className="inline-block align-middle"
    >
      <polyline
        points={points.join(" ")}
        fill="none"
        stroke={stroke}
        strokeWidth="1"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/** Sparkline cell: fetches lazily and renders when ready. */
function SparklineCell({ symbol, cache }: { readonly symbol: string; readonly cache: Map<string, import("./cryptoEnrich").SparklineSeries | null> }) {
  const series = useSparkline(cache, symbol);
  const sparkDir = React.useMemo(() => {
    if (!series) return null;
    return series.direction;
  }, [series]);

  if (!series) {
    // Still loading or failed — show static directional indicator
    return (
      <td data-testid="alt-sparkline" className="px-1 text-center" aria-hidden="true">
        <span className="text-fg-subtle text-label">—</span>
      </td>
    );
  }

  return (
    <td data-testid="alt-sparkline" className="px-1 text-center" aria-hidden="true">
      <InlineSparkline closes={series.closes} direction={sparkDir ?? "flat"} />
    </td>
  );
}

function Row({
  row,
  hasAmount,
  marketClass,
  meta,
  sparklineCache,
}: {
  readonly row: AltRow;
  readonly hasAmount: boolean;
  readonly marketClass: MarketClass;
  readonly meta: CryptoMetaDoc | null;
  readonly sparklineCache: Map<string, import("./cryptoEnrich").SparklineSeries | null>;
}) {
  const href = stockHref(row.symbol, marketClass);
  const name = row.name ?? row.symbol;
  // Formatted once: the title and the cell text are the same string, and a
  // 40-row table should not build 80 `Intl` results per render.
  const price = formatPrice(row.price);
  // A class that does not report an amount shows "—", never the sentinel zero
  // the quote stack puts in `volume` when it has nothing to count.
  const volume = hasAmount ? formatVolume(row.volume) : null;
  const flag = marketClass === "forex" ? flagForPair(row.symbol) : null;
  const sparkDir = row.changePct === null ? null : row.changePct > 0 ? "up" : row.changePct < 0 ? "down" : "flat";
  const isCrypto = marketClass === "crypto";
  const metaRow: CryptoMetaRow | null = isCrypto && meta ? (meta.rows[row.symbol] ?? null) : null;
  const quoteAttr = quoteProps({
    symbol: row.symbol,
    name: row.name ?? undefined,
    price: row.price,
    changePct: row.changePct,
    kind: marketClass,
  });

  const cryptoLogo = isCrypto && metaRow ? (
    <CryptoLogo image={metaRow.image} symbol={row.symbol} />
  ) : null;

  const identity = (
    <>
      {cryptoLogo ?? <LogoMark symbol={row.symbol} size={16} />}
      {flag ? <span className="text-[14px] leading-none" aria-hidden="true">{flag}</span> : null}
      <span className="min-w-0 truncate font-mono text-data font-medium text-fg">{row.symbol}</span>
    </>
  );

  return (
    <tr
      data-testid="alt-row"
      data-symbol={row.symbol}
      {...quoteAttr}
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
      {/* Enriched crypto columns */}
      {isCrypto ? (
        <td data-testid="alt-mcap" className="px-2 text-right nums text-fg-muted">
          {formatMarketCap(metaRow?.market_cap ?? null) ?? <Absent />}
        </td>
      ) : null}
      {isCrypto ? (
        <td data-testid="alt-rank" className="px-2 text-center nums text-fg-muted">
          {metaRow?.market_cap_rank != null ? `#${metaRow.market_cap_rank}` : <Absent />}
        </td>
      ) : null}
      {isCrypto ? (
        <td
          data-testid="alt-7d"
          className={cn(
            "px-2 text-right nums",
            metaRow?.pct_7d == null ? "text-fg-muted"
            : metaRow.pct_7d > 0 ? "text-up"
            : metaRow.pct_7d < 0 ? "text-down"
            : "text-fg-muted",
          )}
        >
          {formatSignedPct(metaRow?.pct_7d ?? null) ?? <Absent />}
        </td>
      ) : null}
      {isCrypto ? (
        <td data-testid="alt-tvl" className="px-2 text-right nums text-fg-muted">
          {formatMarketCap(metaRow?.tvl ?? null) ?? <Absent />}
        </td>
      ) : null}
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
      {isCrypto ? (
        <SparklineCell symbol={row.symbol} cache={sparklineCache} />
      ) : (
        <td data-testid="alt-sparkline" className="px-1 text-center" aria-hidden="true">
          {sparkDir !== null ? (
            <MiniSparkline direction={sparkDir} />
          ) : (
            <Absent />
          )}
        </td>
      )}
      {marketClass === "forex" ? (
        <td
          data-testid="alt-strength"
          title={forexStrengthLabel(row.changePct)}
          className={cn(
            "px-2 text-right text-label whitespace-nowrap",
            row.changePct === null
              ? "text-fg-subtle"
              : row.changePct >= 0
                ? "text-up"
                : "text-down",
          )}
        >
          {forexStrengthLabel(row.changePct)}
        </td>
      ) : null}
    </tr>
  );
}

function Table({
  report,
  rows,
  sort,
  amountLabel,
  hasAmount,
  marketClass,
  meta,
  sparklineCache,
  onSort,
}: {
  readonly report: AltReport;
  readonly rows: readonly AltRow[];
  readonly sort: SortState | null;
  readonly amountLabel: string;
  readonly hasAmount: boolean;
  readonly marketClass: MarketClass;
  readonly meta: CryptoMetaDoc | null;
  readonly sparklineCache: Map<string, import("./cryptoEnrich").SparklineSeries | null>;
  readonly onSort: (key: SortKey) => void;
}) {
  const isForex = marketClass === "forex";
  const isCrypto = marketClass === "crypto";
  const order =
    sort === null
      ? "in the order the endpoint sent them"
      : `sorted by ${sort.key === "price" ? "last" : sort.key}, ${sort.dir === "asc" ? "ascending" : "descending"}`;

  return (
    <div className="overflow-x-auto">
      <table
        data-testid="alt-table"
        className={cn("w-full min-w-[720px] table-fixed border-collapse text-data", isCrypto && "min-w-[900px]")}
      >
        <caption className="sr-only">
          {report.class} quotes, {rows.length} rows, {order}
        </caption>
        <colgroup>
          <col className="w-[14%]" />
          <col className="w-[22%]" />
          {isCrypto ? <col className="w-[8%]" /> : null}
          {isCrypto ? <col className="w-[5%]" /> : null}
          {isCrypto ? <col className="w-[6%]" /> : null}
          {isCrypto ? <col className="w-[6%]" /> : null}
          <col className="w-[11%]" />
          <col className="w-[9%]" />
          <col className="w-[11%]" />
          <col className="w-[7%]" />
          <col className="w-[5%]" />
          {isForex ? <col className="w-[13%]" /> : null}
        </colgroup>
        <thead>
          <tr className="h-[26px] border-b border-line-strong text-label uppercase tracking-[0.05em] text-fg-subtle">
            {COLUMNS.map((column) => (
              <HeadCell key={column.key} column={column} sort={sort} onSort={onSort} />
            ))}
            {isCrypto ? (
              <th scope="col" data-testid="alt-th-mcap" className="px-2 text-right font-medium">
                Cap
              </th>
            ) : null}
            {isCrypto ? (
              <th scope="col" data-testid="alt-th-rank" className="px-2 text-center font-medium">
                #
              </th>
            ) : null}
            {isCrypto ? (
              <th scope="col" data-testid="alt-th-week" className="px-2 text-right font-medium">
                7D
              </th>
            ) : null}
            {isCrypto ? (
              <th scope="col" data-testid="alt-th-tvl" className="px-2 text-right font-medium">
                TVL
              </th>
            ) : null}
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
            <th scope="col" data-testid="alt-th-sparkline" className="px-1 text-center font-medium" aria-label="Trend">
              <span aria-hidden="true" className="text-[0.7em]">━</span>
            </th>
            {isForex ? (
              <th scope="col" data-testid="alt-th-strength" className="px-2 text-right font-medium">
                Strength
              </th>
            ) : null}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <Row key={row.symbol} row={row} hasAmount={hasAmount} marketClass={marketClass} meta={meta} sparklineCache={sparklineCache} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

type ViewMode = "cards" | "table";

export interface AltPageProps {
  readonly marketClass: MarketClass;
}

/* ---------------------------------------------------------------------------
 * Category tabs
 * ------------------------------------------------------------------------ */

function CategoryTabs({
  tabs,
  active,
  counts,
  total,
  onSelect,
}: {
  readonly tabs: readonly CategoryTab[];
  readonly active: string;
  readonly counts: Readonly<Record<string, number>>;
  readonly total: number;
  readonly onSelect: (id: string) => void;
}) {
  // Only the `all` tab is always drawn; named tabs appear even when empty so
  // the reader knows they exist and can see that nothing is priced in them.
  return (
    <nav aria-label="Category">
      <div
        data-testid="alt-category-tabs"
        className="mb-2 flex flex-wrap gap-1"
        role="tablist"
      >
        {tabs.map((tab) => {
          const count = tab.id === "all" ? total : (counts[tab.id] ?? 0);
          const pressed = active === tab.id;
          return (
            <button
              key={tab.id}
              type="button"
              role="tab"
              data-testid={`alt-category-${tab.id}`}
              aria-selected={pressed}
              aria-pressed={pressed}
              onClick={() => onSelect(tab.id)}
              className={cn(
                "focus-ring rounded-sm border px-2 py-0.5 text-label transition-colors",
                pressed
                  ? "border-brand bg-brand/10 text-fg"
                  : "border-line text-fg-muted hover:border-line-strong hover:text-fg",
              )}
            >
              {tab.label}
              <span className="ml-1 text-fg-subtle nums">{count}</span>
            </button>
          );
        })}
      </div>
    </nav>
  );
}

/* ---------------------------------------------------------------------------
 * Forex performance chart
 * ------------------------------------------------------------------------ */

function ForexChart({ rows }: { readonly rows: readonly AltRow[] }) {
  const entries = React.useMemo(() => forexStrength(rows), [rows]);
  const scale = React.useMemo(() => strengthScale(entries), [entries]);
  const hasData = entries.length > 0 && scale > 0;

  return (
    <div
      data-testid="forex-chart"
      className="mb-3 rounded-md border border-line bg-surface p-3"
    >
      <h2 className="mb-2 text-label font-medium uppercase tracking-[0.06em] text-fg-muted">
        Currency Strength vs USD
      </h2>

      {!hasData ? (
        <p className="text-data text-fg-subtle">
          No dollar-quoted pairs with a priced change.
        </p>
      ) : (
        <div className="flex flex-col gap-1.5">
          {entries.map((entry) => {
            const pct = entry.changePct;
            const width = (Math.abs(pct) / scale) * 100;
            const barCls = cn(
              "h-[14px] rounded-[2px] transition-all",
              pct >= 0 ? "bg-up" : "bg-down",
            );

            return (
              <div key={entry.code} className="flex items-center gap-2">
                <span className="w-10 shrink-0 text-right font-mono text-label text-fg">
                  {entry.code}
                </span>
                <span
                  className={cn(
                    "w-[120px] shrink-0 text-right text-label whitespace-nowrap",
                    pct >= 0 ? "text-up" : "text-down",
                  )}
                >
                  {pct >= 0 ? "Stronger vs USD →" : "← Weaker vs USD"}
                </span>
                <div className="flex-1">
                  <div className="h-[14px] relative bg-line/30 rounded-[2px] overflow-hidden">
                    {/* Axis at 50% */}
                    <div className="absolute top-0 bottom-0 left-1/2 w-px bg-line-strong z-10" />
                    <div
                      className={barCls}
                      style={{
                        width: `${width}%`,
                        marginLeft: pct >= 0 ? "50%" : `${50 - width}%`,
                      }}
                    />
                  </div>
                </div>
                <span
                  className={cn(
                    "w-16 shrink-0 text-right text-data nums",
                    pct >= 0 ? "text-up" : "text-down",
                  )}
                >
                  {pct >= 0 ? "+" : ""}
                  {pct.toFixed(2)}%
                </span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

/* ---------------------------------------------------------------------------
 * Card grid view
 * ------------------------------------------------------------------------ */

function CardRow({
  row,
  hasAmount,
  marketClass,
  meta,
}: {
  readonly row: AltRow;
  readonly hasAmount: boolean;
  readonly marketClass: MarketClass;
  readonly meta: CryptoMetaDoc | null;
}) {
  const name = row.name ?? row.symbol;
  const priceText = formatPrice(row.price);
  const changePct = row.changePct;
  const changeSign = changePct === null ? null : changePct >= 0 ? "+" : "";
  const changeDirection =
    changePct === null ? "muted" : changePct > 0 ? "up" : changePct < 0 ? "down" : "muted";
  const changeLabel =
    changePct === null
      ? "—"
      : `${changeSign}${changePct.toFixed(2)}%`;
  const volume = hasAmount && row.volume !== null ? formatVolume(row.volume) : null;
  const flag = marketClass === "forex" ? flagForPair(row.symbol) : null;
  const sparkDir = row.changePct === null ? null : row.changePct > 0 ? "up" : row.changePct < 0 ? "down" : "flat";
  const isCrypto = marketClass === "crypto";
  const metaRow: CryptoMetaRow | null = isCrypto && meta ? (meta.rows[row.symbol] ?? null) : null;
  const quoteAttr = quoteProps({
    symbol: row.symbol,
    name: row.name ?? undefined,
    price: row.price,
    changePct: row.changePct,
    kind: marketClass,
  });

  const cryptoLogo = isCrypto && metaRow ? (
    <CryptoLogo image={metaRow.image} symbol={row.symbol} />
  ) : null;

  const inner = (
    <>
      {/* Header: logo + flag + symbol */}
      <div className="mb-1.5 flex items-center gap-1.5 min-w-0">
        {cryptoLogo ?? <LogoMark symbol={row.symbol} size={16} />}
        {flag ? <span className="text-[14px] leading-none" aria-hidden="true">{flag}</span> : null}
        <span className="truncate font-mono text-label font-medium text-fg">
          {row.symbol}
        </span>
        {metaRow?.market_cap_rank != null ? (
          <span className="ml-auto shrink-0 text-label text-fg-subtle">#{metaRow.market_cap_rank}</span>
        ) : null}
      </div>

      {/* Name */}
      <p
        title={name}
        className={cn(
          "mb-1 truncate text-[11px] leading-snug",
          row.name === null ? "font-mono text-fg-subtle" : "text-fg-muted",
        )}
      >
        {name}
      </p>

      {/* Price */}
      <p className="text-data font-medium text-fg leading-tight nums" title={priceText}>
        {priceText}
      </p>

      {/* Change */}
      <p
        className={cn(
          "mt-0.5 text-data nums font-medium leading-tight",
          changeDirection === "up"
            ? "text-up"
            : changeDirection === "down"
              ? "text-down"
              : "text-fg-muted",
        )}
      >
        {changeLabel}
      </p>

      {/* Enriched stats: cap + 7d + TVL */}
      {isCrypto ? (
        <div className="mt-1 grid grid-cols-2 gap-x-1 gap-y-0.5 text-label">
          <span className="text-fg-subtle">Cap</span>
          <span className="text-right nums text-fg-muted">{formatMarketCap(metaRow?.market_cap ?? null) ?? "—"}</span>
          <span className="text-fg-subtle">7D</span>
          <span className={cn(
            "text-right nums",
            metaRow?.pct_7d == null ? "text-fg-muted"
            : metaRow.pct_7d > 0 ? "text-up"
            : metaRow.pct_7d < 0 ? "text-down"
            : "text-fg-muted",
          )}>{formatSignedPct(metaRow?.pct_7d ?? null) ?? "—"}</span>
          {metaRow?.tvl != null ? (
            <>
              <span className="text-fg-subtle">TVL</span>
              <span className="text-right nums text-fg-muted">{formatMarketCap(metaRow.tvl)}</span>
            </>
          ) : null}
        </div>
      ) : null}

      {/* Detail row: volume, currency */}
      <div className="mt-1.5 flex justify-between text-label text-fg-subtle">
        {volume !== null ? (
          <span>
            Vol: <span className="nums text-fg-muted">{volume}</span>
          </span>
        ) : (
          <span />
        )}
        {row.currency ? (
          <span className="font-mono uppercase tracking-[0.04em] text-fg-muted">
            {row.currency}
          </span>
        ) : null}
      </div>

      {/* Mini sparkline */}
      <div className="mt-1 flex items-center justify-between">
        {sparkDir !== null ? (
          <MiniSparkline direction={sparkDir} />
        ) : (
          <span className="text-fg-subtle text-label">—</span>
        )}
        {marketClass === "forex" ? (
          <span
            className={cn(
              "text-label whitespace-nowrap",
              row.changePct === null
                ? "text-fg-subtle"
                : row.changePct >= 0
                  ? "text-up"
                  : "text-down",
            )}
          >
            {forexStrengthLabel(row.changePct)}
          </span>
        ) : null}
      </div>
    </>
  );

  return (
    <CardGridCard className="cursor-default">
      <div data-testid="alt-row" data-symbol={row.symbol} {...quoteAttr}>
        {inner}
      </div>
    </CardGridCard>
  );
}

function CardView({
  rows,
  hasAmount,
  marketClass,
  meta,
  sort,
  onSort,
}: {
  readonly rows: readonly AltRow[];
  readonly hasAmount: boolean;
  readonly marketClass: MarketClass;
  readonly meta: CryptoMetaDoc | null;
  readonly sort: SortState | null;
  readonly onSort: (key: SortKey) => void;
}) {
  return (
    <div data-testid="alt-card-view" data-alt-ready="true">
      <div data-testid="alt-table" />
      {/* Sort controls: compact pill row */}
      <div className="mb-2 flex items-center gap-1" data-testid="alt-card-sort-bar">
        <span className="text-label text-fg-subtle mr-1">Sort:</span>
        {(
          [
            { key: "symbol" as SortKey, label: "Symbol" },
            { key: "price" as SortKey, label: "Last" },
            { key: "change" as SortKey, label: "Change" },
          ] as const
        ).map((item) => {
          const active = sort !== null && sort.key === item.key;
          const arrow =
            active
              ? sort!.dir === "asc"
                ? "▲"
                : "▼"
              : "";
          return (
            <button
              key={item.key}
              type="button"
              data-testid={`alt-card-sort-${item.key}`}
              onClick={() => onSort(item.key)}
              className={cn(
                "focus-ring rounded-sm px-1.5 py-0.5 text-label transition-colors",
                active
                  ? "border border-brand bg-brand/10 text-fg"
                  : "border border-line text-fg-muted hover:border-line-strong hover:text-fg",
              )}
            >
              {item.label}
              {arrow ? <span className="ml-0.5 text-[0.75em]">{arrow}</span> : null}
            </button>
          );
        })}
      </div>

      <CardGrid>
        {rows.map((row) => (
          <CardRow key={row.symbol} row={row} hasAmount={hasAmount} marketClass={marketClass} meta={meta} />
        ))}
      </CardGrid>
    </div>
  );
}

/* ---------------------------------------------------------------------------
 * View toggle
 * ------------------------------------------------------------------------ */

function ViewToggle({
  mode,
  onChange,
}: {
  readonly mode: ViewMode;
  readonly onChange: (mode: ViewMode) => void;
}) {
  return (
    <div data-testid="alt-view-toggle" className="flex items-center gap-0 rounded-sm border border-line overflow-hidden">
      {(["cards", "table"] as const).map((m) => (
        <button
          key={m}
          type="button"
          data-testid={`alt-view-${m}`}
          aria-pressed={mode === m}
          onClick={() => onChange(m)}
          className={cn(
            "px-2 py-0.5 text-label uppercase tracking-[0.05em] transition-colors",
            mode === m
              ? "bg-brand text-white"
              : "bg-surface text-fg-muted hover:text-fg",
          )}
        >
          {m}
        </button>
      ))}
    </div>
  );
}

/* ---------------------------------------------------------------------------
 * Global pulse strip — crypto only
 * ------------------------------------------------------------------------ */

function GlobalPulseStrip({ global }: { readonly global: import("./cryptoEnrich").CryptoGlobalDoc | null }) {
  if (!global || !global.available) {
    return (
      <div
        data-testid="alt-global-pulse"
        className="mb-2 flex flex-wrap items-center gap-x-4 gap-y-1 rounded-md border border-line bg-surface px-3 py-1.5 text-label"
      >
        <span className="text-fg-subtle">Market data unavailable</span>
      </div>
    );
  }

  const items: { label: string; value: string | null; testId: string }[] = [
    { label: "Total MCap", value: formatMarketCap(global.total_market_cap_usd), testId: "alt-pulse-cap" },
    { label: "BTC Dom", value: formatDominance(global.btc_dominance), testId: "alt-pulse-btc" },
    { label: "24h Δ", value: formatSignedPct(global.market_cap_change_pct_24h), testId: "alt-pulse-24h" },
    { label: "Coins", value: formatCount(global.active_cryptocurrencies), testId: "alt-pulse-coins" },
  ];

  return (
    <div
      data-testid="alt-global-pulse"
      className="mb-2 flex flex-wrap items-center gap-x-4 gap-y-1 rounded-md border border-line bg-surface px-3 py-1.5 text-label"
    >
      {items.map((item) => (
        <span key={item.testId} data-testid={item.testId} className="flex items-center gap-1">
          <span className="text-fg-muted">{item.label}</span>
          <span className="nums font-medium text-fg">{item.value ?? "—"}</span>
        </span>
      ))}
    </div>
  );
}

export function AltPage({ marketClass }: AltPageProps) {
  const isCrypto = marketClass === "crypto";
  const alt = useAlt(marketClass);
  const enrich = useCryptoEnrich(!isCrypto);
  const sparklineCache = useSparklineCache();
  const copy = classCopy(marketClass);
  const [query, setQuery] = React.useState("");
  const [sort, setSort] = React.useState<SortState | null>(null);
  const [category, setCategory] = React.useState("all");
  const [viewMode, setViewMode] = React.useState<ViewMode>("cards");

  const report = alt.report;
  const total = report?.rows.length ?? 0;
  const rows = React.useMemo(
    () =>
      report === null
        ? []
        : viewMode === "cards" || category !== "all"
          ? categoryRows(report.rows, marketClass, category, query, sort)
          : visibleRows(report.rows, query, sort),
    [report, query, sort, category, marketClass, viewMode],
  );

  const tabCounts = React.useMemo(
    () => (report === null ? { counts: {} as Record<string, number>, uncategorised: 0, total: 0 } : categoryCounts(report.rows, marketClass)),
    [report, marketClass],
  );

  const tabs = CATEGORY_TABS[marketClass];
  const hasTabs = tabs.length > 1;

  const onSort = React.useCallback((key: SortKey) => {
    setSort((current) => nextSort(current, key));
  }, []);

  const meta = enrich.data?.meta ?? null;
  const globalDoc = enrich.data?.global ?? null;

  // Notify on enrichment failure — once, not on every render
  React.useEffect(() => {
    if (enrich.status === "error" && isCrypto) {
      notify.error("Crypto market data unavailable", "Showing price-only table from Yahoo");
    }
  }, [enrich.status, isCrypto]);

  const stamp = report === null ? null : formatReadAt(report.asOf);
  const universe = report?.universeTotal ?? null;
  const measured = report?.measured ?? null;

  return (
    <main
      data-testid="alt-page"
      data-class={marketClass}
      className="min-h-0 flex-1 overflow-y-auto bg-black px-2 py-2"
    >
      <div className="mb-1 flex min-w-0 items-baseline justify-between gap-3 px-0.5">
        <SectionTitle
          variant={8}
          testId="alt-page-title"
          count={isCrypto && meta ? `${meta.measured === meta.universe_total ? meta.universe_total : `${meta.measured}/${meta.universe_total}`} coins` : undefined}
        >
          {copy.title}
        </SectionTitle>
        <div className="flex items-center gap-2">
          <p className="font-mono text-label uppercase tracking-[0.08em] text-fg-subtle">
            {universe === null ? `${total} symbols` : `${universe} symbols in this class`}
          </p>
          <ViewToggle mode={viewMode} onChange={setViewMode} />
        </div>
      </div>

      <p className="mb-1.5 max-w-[78ch] px-0.5 text-data text-fg-muted">{copy.blurb}</p>

      {/* Global pulse strip — crypto only */}
      {isCrypto ? <GlobalPulseStrip global={globalDoc} /> : null}

      {/* The filter lives outside the state box on purpose: a box that sits
          inside the branch would be unmounted when the Table/Cards become empty,
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
          {query.trim().length === 0 && category === "all" ? `${total} rows` : `${rows.length} of ${total} rows`}
        </span>
      </div>

      {/* Category tabs — client-side filtering, no API call */}
      {hasTabs && report !== null ? (
        <CategoryTabs
          tabs={tabs}
          active={category}
          counts={tabCounts.counts}
          total={total}
          onSelect={(id) => {
            setCategory(id);
            // Reset filter when switching tabs so the reader does not land on
            // an empty category and think it is the tab's fault.
            if (id !== "all" && query.trim().length > 0) setQuery("");
          }}
        />
      ) : null}

      {/* Forex performance chart — only for forex, in the card view */}
      {marketClass === "forex" && report !== null && viewMode === "cards" ? (
        <ForexChart rows={report.rows} />
      ) : null}

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
            <p className="min-w-0 text-fg-muted">Nothing matches "{query.trim()}".</p>
            <button
              type="button"
              data-testid="alt-filter-clear"
              onClick={() => setQuery("")}
              className="focus-ring shrink-0 rounded-sm border border-line px-2 py-0.5 text-data text-fg-muted transition-colors hover:border-line-strong hover:text-fg"
            >
              Clear filter
            </button>
          </div>
        ) : viewMode === "cards" ? (
          <div className={cn(marketClass === "forex" ? "p-2" : "p-0")}>
            <CardView rows={rows} hasAmount={copy.hasAmount} marketClass={marketClass} meta={meta} sort={sort} onSort={onSort} />
          </div>
        ) : (
          <Table
            report={report}
            rows={rows}
            sort={sort}
            amountLabel={copy.amountLabel}
            hasAmount={copy.hasAmount}
            marketClass={marketClass}
            meta={meta}
            sparklineCache={sparklineCache}
            onSort={onSort}
          />
        )}
      </div>

      <footer data-testid="alt-footer" className="mt-1 flex flex-wrap gap-x-3 px-0.5 text-label text-fg-subtle">
        <span>
          {stamp === null ? "Read time unavailable" : `As of ${stamp}`}
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
