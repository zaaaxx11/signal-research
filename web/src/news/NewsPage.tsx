/**
 * News: four category tabs over one feed, plus a crypto tape.
 *
 * The document is `GET /market/news?limit=60` — the largest index funds and
 * companies, newest first, deduped by the server. This page:
 *
 * * **Tabs** split the feed into MARKET NEWS (whole), MARKET PULSE (empty
 *   note — no feed yet), STOCKS (items whose symbols contain a non-index-fund
 *   ticker), ETF (items w/ index fund symbols SPY/QQQ/DIA/IWM), and CRYPTO
 *   (fetched from `GET /market/alt?class=crypto` as a tape).
 * * **Time window** filters rows by timestamp.
 * * **Source filter** narrows to one outlet.
 * * **Source chips** are colour-coded per outlet.
 * * **Times** are relative ("2 hours ago") with absolute UTC tooltip.
 * * **Filter count** reads "N of M headlines".
 * * **Symbol chips** carry `quoteProps` so the global HoverBubble fires.
 */

import * as React from "react";

import { formatCount } from "@/breadth/format";
import { ErrorState } from "@/components/ErrorState";
import { SectionTitle } from "@/components/SectionTitle";
import { Link } from "@/shell/routes";
import { quoteProps } from "@/lib/quoteAttrs";
import { notify } from "@/lib/notify";

import { relativeTime, safeExternalHref, todayUtc } from "./format";
import {
  filterByCategory,
  filterByTime,
  groupByDay,
  isIndexFundSymbol,
  sourceColor,
  type CategoryTab,
  type CryptoRow,
  type NewsGroup,
  type NewsItem,
  type TimeWindow,
} from "./news";
import { useCrypto, useNews, type CryptoView, type NewsView } from "./useNews";

/* ------------------------------------------------------------------ */
/*  Constants                                                          */
/* ------------------------------------------------------------------ */

const CATEGORIES: { key: CategoryTab; label: string }[] = [
  { key: "market-news", label: "MARKET NEWS" },
  { key: "market-pulse", label: "MARKET PULSE" },
  { key: "stocks", label: "STOCKS" },
  { key: "etf", label: "ETF" },
  { key: "crypto", label: "CRYPTO" },
];

const TIME_WINDOWS: { key: TimeWindow; label: string }[] = [
  { key: "all", label: "All" },
  { key: "1h", label: "Last hour" },
  { key: "24h", label: "24 hours" },
  { key: "3d", label: "3 days" },
  { key: "7d", label: "7 days" },
];

export const NEWS_SKELETON_ROWS = 8;

/* ------------------------------------------------------------------ */
/*  Skeleton                                                           */
/* ------------------------------------------------------------------ */

function Skeleton() {
  return (
    <div
      data-testid="news-skeleton"
      aria-busy="true"
      className="flex flex-col gap-0 px-1"
    >
      <span className="sr-only">Loading the news feed</span>
      {Array.from({ length: NEWS_SKELETON_ROWS }, (_, i) => (
        <div
          key={i}
          aria-hidden="true"
          className="grid grid-cols-[48px_minmax(0,1fr)] items-center gap-x-2 border-b border-line/60 py-2 last:border-b-0"
        >
          <span className="block h-[10px] w-[36px] rounded-[1px] bg-surface-raised" />
          <span className="flex min-w-0 flex-col gap-1.5">
            <span
              className={
                i % 3 === 0
                  ? "block h-[10px] w-[78%] rounded-[1px] bg-surface-raised"
                  : i % 3 === 1
                    ? "block h-[10px] w-[62%] rounded-[1px] bg-surface-raised"
                    : "block h-[10px] w-[46%] rounded-[1px] bg-surface-raised"
              }
            />
            <span className="block h-[8px] w-[22%] rounded-[1px] bg-surface-raised" />
          </span>
        </div>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  News Row                                                           */
/* ------------------------------------------------------------------ */

function NewsRow({ item }: { readonly item: NewsItem }) {
  const rel = relativeTime(item.publishedAt);
  const href = safeExternalHref(item.url);

  const context = [
    item.source.length > 0 ? `Source: ${item.source}.` : "",
    item.symbols.length > 0 ? `Symbols: ${item.symbols.join(", ")}.` : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <li
      data-testid="news-row"
      data-symbols={item.symbols.join(" ")}
      className="grid grid-cols-[48px_minmax(0,1fr)] items-start gap-x-2 border-b border-line/60 py-2 last:border-b-0"
    >
      {rel ? (
        <time
          data-testid="news-time"
          dateTime={item.publishedAt ?? undefined}
          title={rel.absolute}
          className="nums pt-[2px] text-[11px] leading-tight text-fg-subtle"
        >
          {rel.relative}
        </time>
      ) : (
        <span
          aria-hidden="true"
          data-testid="news-no-time"
          className="pt-[2px] text-[11px] text-fg-subtle"
        >
          —
        </span>
      )}

      <div className="min-w-0">
        {href ? (
          <a
            data-testid="news-headline-link"
            href={href}
            target="_blank"
            rel="noopener noreferrer"
            className="break-words text-[12px] leading-snug text-fg underline-offset-2 hover:text-accent-text hover:underline focus-visible:text-accent-text"
          >
            {item.headline}
            {context.length > 0 ? (
              <span className="sr-only">. {context}</span>
            ) : null}
          </a>
        ) : (
          <span
            data-testid="news-plain"
            className="break-words text-[12px] leading-snug text-fg"
          >
            {item.headline}
            {context.length > 0 ? (
              <span className="sr-only">. {context}</span>
            ) : null}
          </span>
        )}

        <div className="mt-1 flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
          {item.source.length > 0 ? (
            <SourceChip source={item.source} />
          ) : null}
          {item.symbols.map((symbol) => {
            const fund = isIndexFundSymbol(symbol);
            return (
              <Link
                key={symbol}
                to={`/stock/${encodeURIComponent(symbol)}`}
                data-testid="news-chip"
                aria-label={`${symbol} stock page`}
                {...quoteProps({
                  symbol,
                  kind: fund ? "etf" : "stock",
                })}
                className="focus-ring rounded-[2px] border border-line bg-surface-raised px-1 py-px font-mono text-[10px] leading-none text-fg-muted no-underline hover:border-line-strong hover:text-fg"
              >
                {symbol}
              </Link>
            );
          })}
        </div>
      </div>
    </li>
  );
}

/* ------------------------------------------------------------------ */
/*  Source Chip                                                        */
/* ------------------------------------------------------------------ */

function SourceChip({ source }: { readonly source: string }) {
  const color = sourceColor(source);
  return (
    <span
      data-testid="news-source"
      className="inline-flex items-center rounded-[2px] px-1.5 py-px text-[10px] font-medium uppercase tracking-[0.03em]"
      style={{ background: color.bg, color: color.text }}
    >
      {source}
    </span>
  );
}

/* ------------------------------------------------------------------ */
/*  Day Group                                                          */
/* ------------------------------------------------------------------ */

function Day({ group }: { readonly group: NewsGroup }) {
  const headingId = `news-day-${group.key ?? "undated"}`;
  return (
    <section
      data-testid={`news-group-${group.key ?? "undated"}`}
      data-day={group.key ?? undefined}
      aria-labelledby={headingId}
      className="mb-1 last:mb-0"
    >
      <h2
        id={headingId}
        className="flex min-w-0 items-baseline gap-1.5 px-1 py-1 text-[11px] font-medium text-fg"
      >
        {group.key === null ? (
          <span>{group.label}</span>
        ) : (
          <time dateTime={group.key}>{group.label}</time>
        )}
        <span aria-hidden="true" className="text-fg-subtle">
          ·
        </span>
        <span className="nums text-[10px] text-fg-subtle">
          {formatCount(group.items.length)}
        </span>
      </h2>
      <ul data-testid="news-list" className="flex flex-col">
        {group.items.map((item, index) => (
          <NewsRow
            key={`${group.key ?? "undated"}:${index}:${item.url}`}
            item={item}
          />
        ))}
      </ul>
    </section>
  );
}

/* ------------------------------------------------------------------ */
/*  Category Tabs                                                      */
/* ------------------------------------------------------------------ */

function CategoryTabs({
  active,
  onSelect,
}: {
  readonly active: CategoryTab;
  readonly onSelect: (tab: CategoryTab) => void;
}) {
  return (
    <nav
      data-testid="news-tabs"
      aria-label="News categories"
      className="mb-2 flex gap-0"
    >
      {CATEGORIES.map((cat) => (
        <button
          key={cat.key}
          type="button"
          data-testid={`news-tab-${cat.key}`}
          data-active={active === cat.key ? "true" : "false"}
          onClick={() => onSelect(cat.key)}
          className="border-b-2 px-3 py-1.5 text-[11px] font-semibold uppercase tracking-[0.04em] transition-colors"
          style={{
            color:
              active === cat.key
                ? "var(--fg)"
                : "var(--fg-muted)",
            borderColor:
              active === cat.key
                ? "var(--accent-text)"
                : "transparent",
          }}
        >
          {cat.label}
        </button>
      ))}
    </nav>
  );
}

/* ------------------------------------------------------------------ */
/*  Filters: time + source + count                                     */
/* ------------------------------------------------------------------ */

function Filters({
  timeWindow,
  onTimeWindow,
  sourceFilter,
  onSourceFilter,
  sources,
  visibleCount,
  totalCount,
}: {
  readonly timeWindow: TimeWindow;
  readonly onTimeWindow: (w: TimeWindow) => void;
  readonly sourceFilter: string;
  readonly onSourceFilter: (s: string) => void;
  readonly sources: readonly string[];
  readonly visibleCount: number;
  readonly totalCount: number;
}) {
  const filtered = sourceFilter !== "" || timeWindow !== "all";
  return (
    <div
      data-testid="news-filters"
      className="mb-2 flex flex-wrap items-center gap-2"
    >
      <label htmlFor="news-time-filter" className="sr-only">
        View by time
      </label>
      <select
        id="news-time-filter"
        data-testid="news-time-select"
        value={timeWindow}
        onChange={(e) => onTimeWindow(e.target.value as TimeWindow)}
        className="focus-ring h-6 rounded-sm border border-line bg-surface px-2 text-[11px] text-fg"
      >
        {TIME_WINDOWS.map((w) => (
          <option key={w.key} value={w.key}>
            {w.label}
          </option>
        ))}
      </select>

      <label htmlFor="news-source-filter" className="sr-only">
        View by source
      </label>
      <select
        id="news-source-filter"
        data-testid="news-source-select"
        value={sourceFilter}
        onChange={(e) => onSourceFilter(e.target.value)}
        className="focus-ring h-6 rounded-sm border border-line bg-surface px-2 text-[11px] text-fg"
      >
        <option value="">All sources</option>
        {sources.map((s) => (
          <option key={s} value={s}>
            {s}
          </option>
        ))}
      </select>

      <p
        data-testid="news-filter-status"
        role="status"
        aria-live="polite"
        aria-atomic="true"
        className="min-w-0 text-[11px] text-fg-subtle"
      >
        {filtered
          ? `${formatCount(visibleCount)} of ${formatCount(totalCount)} headlines`
          : `${formatCount(totalCount)} headlines`}
      </p>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Feed List (Market News / Stocks / ETF)                             */
/* ------------------------------------------------------------------ */

function FeedList({
  view,
  category,
  timeWindow,
  sourceFilter,
  today,
}: {
  readonly view: NewsView;
  readonly category: CategoryTab;
  readonly timeWindow: TimeWindow;
  readonly sourceFilter: string;
  readonly today: string;
}) {
  const items = React.useMemo(() => view.report?.items ?? [], [view.report]);

  // 1. Category filter
  const byCategory = React.useMemo(
    () => filterByCategory(items, category),
    [items, category],
  );

  // 2. Time filter
  const byTime = React.useMemo(
    () => filterByTime(byCategory, timeWindow),
    [byCategory, timeWindow],
  );

  // 3. Source filter
  const visible = React.useMemo(() => {
    if (sourceFilter === "") return byTime;
    return byTime.filter((item) => item.source === sourceFilter);
  }, [byTime, sourceFilter]);

  const groups = React.useMemo(
    () => groupByDay(visible, today),
    [visible, today],
  );

  if (view.status === "loading") return <Skeleton />;

  if (view.status === "error") {
    return (
      <ErrorState
        title="The news feed did not load."
        error={view.error ?? "Unknown error"}
        onRetry={view.retry}
        testId="news-error"
      />
    );
  }

  if (items.length === 0) {
    return (
      <div data-testid="news-empty" className="px-3 py-2">
        <p className="text-[12px] text-fg">The feeds returned no headlines.</p>
        <p className="mt-0.5 text-[11px] text-fg-muted">
          Nothing arrived from the index or company feeds on this refresh.
        </p>
      </div>
    );
  }

  if (visible.length === 0) {
    return (
      <div data-testid="news-filter-empty" className="px-3 py-2">
        <p className="text-[12px] text-fg">No headlines match the current filters.</p>
        <p className="mt-0.5 text-[11px] text-fg-muted">
          Try a different time window or source.
        </p>
      </div>
    );
  }

  return (
    <>
      <div data-testid="news-groups" className="px-1 py-0.5">
        {groups.map((group) => (
          <Day key={group.key ?? "undated"} group={group} />
        ))}
        {view.report && view.report.providerErrors.length > 0 ? (
          <p
            data-testid="news-provider-errors"
            className="px-1 py-1 text-[10px] text-fg-subtle"
          >
            {formatCount(view.report.providerErrors.length)}{" "}
            {view.report.providerErrors.length === 1 ? "feed" : "feeds"} did not
            answer; this list may be missing headlines.
          </p>
        ) : null}
      </div>
    </>
  );
}

/* ------------------------------------------------------------------ */
/*  Market Pulse (empty note — no feed yet)                            */
/* ------------------------------------------------------------------ */

function MarketPulsePanel() {
  return (
    <div
      data-testid="news-pulse-empty"
      className="flex flex-col items-center justify-center gap-2 px-3 py-12"
    >
      <p className="text-[13px] font-medium text-fg">Market Pulse</p>
      <p className="max-w-sm text-center text-[12px] leading-snug text-fg-muted">
        No pulse feed is available yet. Market Pulse will deliver real-time
        sentiment, breadth, and unusual activity when the data pipeline is
        complete.
      </p>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Crypto Tape                                                        */
/* ------------------------------------------------------------------ */

function CryptoRowView({ row }: { readonly row: CryptoRow }) {
  const up = (row.changePct ?? 0) >= 0;
  const pct =
    row.changePct != null && Number.isFinite(row.changePct)
      ? `${row.changePct > 0 ? "+" : ""}${row.changePct.toFixed(2)}%`
      : "—";

  return (
    <tr
      data-testid="crypto-row"
      data-crypto-symbol={row.symbol}
      {...quoteProps({
        symbol: row.symbol,
        name: row.name,
        price: row.price,
        changePct: row.changePct,
        kind: "crypto",
      })}
      className="border-b border-line/60 transition-colors hover:bg-row-hover"
    >
      <td className="px-2 py-1.5">
        <span className="flex min-w-0 items-center gap-1.5">
          <span className="min-w-0 truncate text-[11px] font-medium text-fg">
            {row.name}
          </span>
          <span className="shrink-0 font-mono text-[10px] text-fg-subtle">
            {row.symbol}
          </span>
        </span>
      </td>
      <td className="nums px-2 py-1.5 text-right text-[11px] text-fg">
        $
        {row.price.toLocaleString(undefined, {
          minimumFractionDigits: 2,
          maximumFractionDigits: row.price < 1 ? 6 : 2,
        })}
      </td>
      <td
        className="nums px-2 py-1.5 text-right text-[11px]"
        style={{ color: up ? "var(--up)" : "var(--down)" }}
      >
        {pct}
      </td>
    </tr>
  );
}

function CryptoTape({ crypto }: { readonly crypto: CryptoView }) {
  if (crypto.status === "loading") {
    return (
      <div
        data-testid="crypto-skeleton"
        aria-busy="true"
        className="flex flex-col gap-2 px-2 py-3"
      >
        <span className="sr-only">Loading crypto tape</span>
        {Array.from({ length: 6 }, (_, i) => (
          <span
            key={i}
            aria-hidden="true"
            className="block h-[12px] animate-pulse rounded-[1px] bg-surface-raised"
            style={{ width: `${84 - i * 9}%` }}
          />
        ))}
      </div>
    );
  }

  if (crypto.status === "error") {
    return (
      <ErrorState
        title="Crypto tape did not load."
        error={crypto.error ?? "Unknown error"}
        onRetry={crypto.retry}
        testId="crypto-error"
      />
    );
  }

  if (crypto.rows.length === 0) {
    return (
      <div data-testid="crypto-empty" className="px-3 py-2">
        <p className="text-[12px] text-fg">No crypto prices in this response.</p>
      </div>
    );
  }

  return (
    <div
      data-testid="crypto-tape"
      className="min-h-0 min-w-0 overflow-y-auto [scrollbar-width:thin]"
    >
      <table className="w-full table-fixed border-collapse text-[11px]">
        <caption className="sr-only">
          Crypto tape: symbol, price, 24h change
        </caption>
        <thead>
          <tr className="border-b border-line text-[10px] uppercase tracking-[0.05em] text-fg-subtle">
            <th scope="col" className="px-2 py-1 text-left font-medium">
              Coin
            </th>
            <th scope="col" className="px-2 py-1 text-right font-medium">
              Price
            </th>
            <th scope="col" className="px-2 py-1 text-right font-medium">
              24h
            </th>
          </tr>
        </thead>
        <tbody>
          {crypto.rows.map((row) => (
            <CryptoRowView key={row.symbol} row={row} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  NewsPage                                                           */
/* ------------------------------------------------------------------ */

export interface NewsPageProps {
  readonly today?: string;
  readonly limit?: number;
}

export function NewsPage({ today, limit }: NewsPageProps = {}) {
  const [category, setCategory] = React.useState<CategoryTab>("market-news");
  const [timeWindow, setTimeWindow] = React.useState<TimeWindow>("all");
  const [sourceFilter, setSourceFilter] = React.useState("");

  const day = today ?? todayUtc();
  const news = useNews({ limit });
  const crypto = useCrypto();

  // Notification on error
  React.useEffect(() => {
    if (news.status === "error" && news.error) {
      notify.warn("News feed failed", news.error);
    }
  }, [news.status, news.error]);

  const isNewsTab = category === "market-news" || category === "stocks" || category === "etf";

  // Compute sources list for the active feed
  const items = React.useMemo(() => news.report?.items ?? [], [news.report]);
  const byCategory = React.useMemo(
    () => filterByCategory(items, category),
    [items, category],
  );
  const byTime = React.useMemo(
    () => filterByTime(byCategory, timeWindow),
    [byCategory, timeWindow],
  );
  const visible = React.useMemo(() => {
    if (sourceFilter === "") return byTime;
    return byTime.filter((item) => item.source === sourceFilter);
  }, [byTime, sourceFilter]);
  const sources = React.useMemo(() => {
    const set = new Set<string>();
    for (const item of byTime) {
      if (item.source.length > 0) set.add(item.source);
    }
    return [...set].sort();
  }, [byTime]);

  return (
    <main
      data-testid="news-page"
      className="min-h-0 flex-1 overflow-y-auto px-2 py-3"
      style={{ background: "#000" }}
    >
      <SectionTitle variant={6} count={category !== "crypto" ? formatCount(items.length) : undefined}>
        News
      </SectionTitle>

      <CategoryTabs active={category} onSelect={setCategory} />

      {/* Filters only for news tabs */}
      {isNewsTab && news.status !== "loading" && news.status !== "error" && items.length > 0 && (
        <Filters
          timeWindow={timeWindow}
          onTimeWindow={setTimeWindow}
          sourceFilter={sourceFilter}
          onSourceFilter={setSourceFilter}
          sources={sources}
          visibleCount={visible.length}
          totalCount={byCategory.length}
        />
      )}

      <div
        data-testid="news-frame"
        className="rounded-md border border-line bg-surface"
      >
        {category === "market-pulse" ? (
          <MarketPulsePanel />
        ) : category === "crypto" ? (
          <CryptoTape crypto={crypto} />
        ) : (
          <FeedList
            view={news}
            category={category}
            timeWindow={timeWindow}
            sourceFilter={sourceFilter}
            today={day}
          />
        )}
      </div>
    </main>
  );
}