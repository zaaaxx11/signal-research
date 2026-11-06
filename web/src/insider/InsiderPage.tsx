/**
 * Insider Trading — Finviz-style page with owner-type tabs, transaction
 * filter, main table and two sidebar blocks.
 *
 * Fetches ``GET /market/insider?owner_type=…&transaction_type=…``. The page
 * is a single feed: tabs and filter change the query, not a local slice.
 */

import * as React from "react";

import { ErrorState } from "@/components/ErrorState";
import { SectionTitle } from "@/components/SectionTitle";
import { getJson, ApiError } from "@/lib/api";
import { notify } from "@/lib/notify";
import { quoteProps } from "@/lib/quoteAttrs";
import { cn } from "@/lib/utils";
import { Link } from "@/shell/routes";

import {
  type InsiderRow,
  type OwnerType,
  type TransactionFilter,
  insiderFeedPath,
  OWNER_TYPES,
  OWNER_TYPE_LABELS,
  TRANSACTION_FILTERS,
  TRANSACTION_FILTER_LABELS,
  readInsiderFeed,
} from "./insider";

/* ── Helpers ──────────────────────────────────────────────────────────── */

function formatShares(value: number | null): string {
  if (value === null) return "—";
  return value.toLocaleString("en-US", { maximumFractionDigits: 0 });
}

function formatValue(value: number | null): string {
  if (value === null) return "—";
  if (Math.abs(value) >= 1e6) return `$${(value / 1e6).toFixed(1)}M`;
  if (Math.abs(value) >= 1e3) return `$${(value / 1e3).toFixed(0)}K`;
  return `$${value.toLocaleString("en-US", { maximumFractionDigits: 0 })}`;
}

function formatDate(raw: string): string {
  if (/^\d{4}-\d{2}-\d{2}$/.test(raw)) {
    const d = new Date(raw + "T00:00:00");
    if (!Number.isNaN(d.getTime())) {
      return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
    }
  }
  return raw || "—";
}

/* ── Skeleton ─────────────────────────────────────────────────────────── */

function TableSkeleton({ rows }: { readonly rows: number }) {
  return (
    <div data-testid="insider-skeleton" role="status" aria-busy="true" className="animate-pulse">
      {Array.from({ length: rows }, (_, i) => (
        <div
          key={i}
          className="flex items-center gap-3 border-b border-white/5 px-3 py-2"
        >
          <span className="h-3 w-10 rounded-sm bg-white/10" />
          <span className="h-3 w-24 rounded-sm bg-white/10" />
          <span className="h-3 w-16 rounded-sm bg-white/10" />
          <span className="h-3 w-10 rounded-sm bg-white/10" />
          <span className="h-3 w-12 rounded-sm bg-white/10" />
          <span className="h-3 w-14 rounded-sm bg-white/10" />
          <span className="h-3 w-12 rounded-sm bg-white/10" />
        </div>
      ))}
    </div>
  );
}

function SidebarSkeleton() {
  return (
    <div data-testid="insider-sidebar-skeleton" className="animate-pulse space-y-3">
      {[1, 2].map((block) => (
        <div key={block} className="rounded-md border border-white/10 p-3">
          <span className="block h-3 w-28 rounded-sm bg-white/10" />
          <div className="mt-2 space-y-1.5">
            {Array.from({ length: 5 }, (_, i) => (
              <div key={i} className="flex justify-between">
                <span className="h-2.5 w-16 rounded-sm bg-white/10" />
                <span className="h-2.5 w-12 rounded-sm bg-white/10" />
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

/* ── Sidebar blocks ──────────────────────────────────────────────────── */

function SidebarBlock({
  title,
  rows,
  testId,
}: {
  readonly title: string;
  readonly rows: readonly InsiderRow[];
  readonly testId: string;
}) {
  if (rows.length === 0) {
    return (
      <div data-testid={testId} className="rounded-md border border-white/10 p-3">
        <h3 className="text-[11px] font-semibold uppercase tracking-wide text-white/60">
          {title}
        </h3>
        <p className="mt-2 text-[11px] text-white/30">No trades this week</p>
      </div>
    );
  }
  return (
    <div data-testid={testId} className="rounded-md border border-white/10 p-3">
      <h3 className="text-[11px] font-semibold uppercase tracking-wide text-white/60">
        {title}
      </h3>
      <div className="mt-2 space-y-1">
        {rows.map((row, i) => (
          <div
            key={`${row.symbol}-${row.owner}-${i}`}
            {...quoteProps({ symbol: row.symbol })}
            className="flex items-center justify-between text-[11px]"
          >
            <span className="truncate">
              <span className="font-mono text-white/80">{row.symbol}</span>{" "}
              <span className="text-white/40">{row.owner}</span>
            </span>
            <span
              className={cn(
                "ml-2 shrink-0 tabular-nums",
                row.transaction_type === "Buy" ? "text-green-400" : "text-red-400",
              )}
            >
              {row.transaction_type === "Buy" ? "+" : "-"}
              {formatValue(row.value)}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ── Transaction badge ────────────────────────────────────────────────── */

function TransactionBadge({ type }: { readonly type: "Buy" | "Sale" }) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded px-1.5 py-px text-[11px] font-medium",
        type === "Buy" ? "bg-green-500/15 text-green-400" : "bg-red-500/15 text-red-400",
      )}
    >
      {type}
    </span>
  );
}

/* ── Main table ──────────────────────────────────────────────────────── */

function InsiderTable({ rows }: { readonly rows: readonly InsiderRow[] }) {
  if (rows.length === 0) {
    return (
      <p data-testid="insider-empty" className="px-3 py-4 text-[12px] text-white/30">
        No transactions match the current filters.
      </p>
    );
  }

  return (
    <div className="min-w-0 overflow-x-auto">
      <table
        data-testid="insider-table"
        className="w-full border-collapse text-[12px]"
      >
        <thead>
          <tr className="border-b border-white/10 text-left text-[11px] font-medium uppercase tracking-wide text-white/40">
            <th scope="col" className="px-3 py-2 w-[70px]">Symbol</th>
            <th scope="col" className="px-3 py-2">Owner</th>
            <th scope="col" className="px-3 py-2 hidden md:table-cell">Relationship</th>
            <th scope="col" className="px-3 py-2 w-[72px]">Transaction</th>
            <th scope="col" className="px-3 py-2 w-[80px] text-right">Shares</th>
            <th scope="col" className="px-3 py-2 w-[90px] text-right">Value</th>
            <th scope="col" className="px-3 py-2 w-[100px]">Date</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr
              key={`${row.symbol}-${row.owner}-${row.filing_date}-${i}`}
              data-testid="insider-row"
              {...quoteProps({ symbol: row.symbol })}
              className="border-b border-white/5 transition-colors hover:bg-white/[0.03]"
            >
              <td className="px-3 py-2 font-mono">
                <Link
                  to={`/insider/${encodeURIComponent(row.symbol)}`}
                  data-testid="insider-row-link"
                  className="text-white/80 underline-offset-2 transition-colors hover:text-white hover:underline"
                >
                  {row.symbol}
                </Link>
              </td>
              <td className="truncate px-3 py-2 text-white/70 max-w-[180px]" title={row.owner}>
                {row.owner}
              </td>
              <td className="hidden px-3 py-2 text-white/40 md:table-cell max-w-[200px] truncate" title={row.relationship ?? undefined}>
                {row.relationship ?? "—"}
              </td>
              <td className="px-3 py-2">
                <TransactionBadge type={row.transaction_type} />
              </td>
              <td className="px-3 py-2 text-right tabular-nums text-white/60">
                {formatShares(row.shares)}
              </td>
              <td className="px-3 py-2 text-right tabular-nums text-white/80">
                {formatValue(row.value)}
              </td>
              <td className="px-3 py-2 text-white/40 tabular-nums">
                {formatDate(row.filing_date)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ── Page ────────���────────────────────────────────────────────────────── */

type PageStatus = "loading" | "ready" | "error";

export function InsiderPage() {
  const [ownerType, setOwnerType] = React.useState<OwnerType>("insiders");
  const [txFilter, setTxFilter] = React.useState<TransactionFilter>("all");
  const [rows, setRows] = React.useState<readonly InsiderRow[]>([]);
  const [topWeekly, setTopWeekly] = React.useState<readonly InsiderRow[]>([]);
  const [top10Owner, setTop10Owner] = React.useState<readonly InsiderRow[]>([]);
  const [asOf, setAsOf] = React.useState<string | null>(null);
  const [status, setStatus] = React.useState<PageStatus>("loading");
  const [error, setError] = React.useState<string | null>(null);
  const [attempt, setAttempt] = React.useState(0);

  const fetchData = React.useCallback(() => {
    const controller = new AbortController();
    let live = true;
    setStatus("loading");
    setError(null);

    getJson(insiderFeedPath(ownerType, txFilter), controller.signal)
      .then((body) => {
        if (!live) return;
        const feed = readInsiderFeed(body);
        setRows(feed.rows);
        setTopWeekly(feed.top_weekly);
        setTop10Owner(feed.top_10_owner_weekly);
        setAsOf(feed.as_of);
        setStatus("ready");
      })
      .catch((cause: unknown) => {
        if (!live) return;
        if (controller.signal.aborted) return;
        if (cause instanceof ApiError && cause.name === "AbortError") return;
        const msg = cause instanceof Error ? cause.message : "Request failed";
        setError(msg);
        setStatus("error");
        notify.error("Insider data failed to load", msg);
      });

    return () => {
      live = false;
      controller.abort();
    };
  }, [ownerType, txFilter, attempt]);

  React.useEffect(() => {
    const cleanup = fetchData();
    return cleanup;
  }, [fetchData]);

  const handleRetry = React.useCallback(() => {
    setAttempt((v) => v + 1);
  }, []);

  const isLoading = status === "loading";

  return (
    <main className="min-h-screen bg-black text-white">
      <header className="border-b border-white/10 px-4 py-3">
        <SectionTitle variant={7} count={asOf ? `as of ${asOf}` : undefined}>
          Insider Trading
        </SectionTitle>
      </header>

      {/* Tabs: Insiders / Managers / Funds */}
      <div role="tablist" aria-label="Owner type" className="flex border-b border-white/10">
        {OWNER_TYPES.map((ot) => (
          <button
            key={ot}
            role="tab"
            aria-selected={ownerType === ot}
            data-testid={`insider-tab-${ot}`}
            onClick={() => setOwnerType(ot)}
            className={cn(
              "px-4 py-2 text-[12px] font-medium uppercase tracking-wide transition-colors",
              ownerType === ot
                ? "border-b-2 border-white text-white"
                : "text-white/40 hover:text-white/70",
            )}
          >
            {OWNER_TYPE_LABELS[ot]}
          </button>
        ))}
      </div>

      {/* Transaction filter bar */}
      <div
        role="radiogroup"
        aria-label="Transaction type"
        className="flex items-center gap-2 border-b border-white/10 px-4 py-1.5"
      >
        {TRANSACTION_FILTERS.map((filter) => (
          <button
            key={filter}
            role="radio"
            aria-checked={txFilter === filter}
            data-testid={`insider-filter-${filter}`}
            onClick={() => setTxFilter(filter)}
            className={cn(
              "rounded border px-2.5 py-0.5 text-[11px] font-medium transition-colors",
              txFilter === filter
                ? "border-white/30 bg-white/10 text-white"
                : "border-white/10 text-white/40 hover:border-white/20 hover:text-white/60",
            )}
          >
            {TRANSACTION_FILTER_LABELS[filter]}
          </button>
        ))}
        {!isLoading && status !== "error" ? (
          <span data-testid="insider-row-count" className="ml-auto text-[11px] text-white/30">
            {rows.length} {rows.length === 1 ? "transaction" : "transactions"}
          </span>
        ) : null}
      </div>

      {/* Main area */}
      <div className="flex flex-col gap-0 lg:flex-row">
        {/* Table */}
        <div className="min-w-0 flex-1">
          {status === "error" ? (
            <div className="p-4">
              <ErrorState
                title="Insider data could not be loaded"
                error={error ?? "Unknown error"}
                onRetry={handleRetry}
                testId="insider-error"
              />
            </div>
          ) : isLoading ? (
            <TableSkeleton rows={10} />
          ) : (
            <InsiderTable rows={rows} />
          )}
        </div>

        {/* Sidebar */}
        <aside className="shrink-0 border-t border-white/10 p-4 lg:w-[280px] lg:border-l lg:border-t-0">
          {isLoading ? (
            <SidebarSkeleton />
          ) : (
            <div className="space-y-4">
              <SidebarBlock
                title="Top Insider Trading — Recent Week"
                rows={topWeekly}
                testId="insider-top-weekly"
              />
              <SidebarBlock
                title="Top 10% Owner Trading — Recent Week"
                rows={top10Owner}
                testId="insider-top-10-owner"
              />
            </div>
          )}
        </aside>
      </div>
    </main>
  );
}