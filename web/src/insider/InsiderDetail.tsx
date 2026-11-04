/**
 * The filings panel: the Form 4 rows behind one company.
 *
 * These are individual filings, each with its own filing and transaction
 * date. The panel says so in words, and it never adds them up: a sum across
 * filings would be a number nobody reported.
 *
 * Every fact comes from the filing's own payload. A field the filing did not
 * carry is absent, not `0` and not `$0.00`; a role it did not state reads
 * "Role not reported", never `undefined`. The `count` line reports the
 * endpoint's own count and says `shown of total` when the two disagree.
 */

import * as React from "react";

import { formatCount } from "@/breadth/format";
import { formatMoney, formatNumber } from "@/grid/format";
import { cn } from "@/lib/utils";

import type { InsiderFiling, InsiderField } from "./filings";
import { useInsiderFilings } from "./useInsiderFilings";

export const INSIDER_DETAIL_TITLE_ID = "insider-detail-title";

/** Named in the panel and in its tests: the rows are filings, not totals. */
export const INSIDER_DETAIL_NOTE =
  "Individual filings as reported to the SEC, each with its own filing and transaction date.";

/* ── Helpers ──────────────────────────────────────────────────────────── */

function fieldText(fields: readonly InsiderField[], key: string): string | null {
  const field = fields.find((f) => f.key === key);
  return field?.text ?? null;
}

function fieldNumber(fields: readonly InsiderField[], key: string): number | null {
  const text = fieldText(fields, key);
  if (text === null) return null;
  const cleaned = text.replace(/[,%$]/g, "");
  const n = Number(cleaned);
  return Number.isFinite(n) ? n : null;
}

/** The transaction code letter: P, S, or null for other. */
function codeLetter(fields: readonly InsiderField[]): string | null {
  const code = fieldText(fields, "code");
  if (code === null) return null;
  const letter = code.trim().toUpperCase()[0];
  return letter === "P" || letter === "S" ? letter : null;
}

type TypeFilter = "all" | "buy" | "sale";

type SortKey = "ticker" | "owner" | "date" | "type" | "shares" | "value";
type SortDir = "asc" | "desc";

interface SortConfig {
  key: SortKey;
  dir: SortDir;
}

/* ── Sort helpers ─────────────────────────────────────────────────────── */

function sortValue(filing: InsiderFiling, symbol: string, key: SortKey): string | number {
  switch (key) {
    case "ticker":
      return symbol;
    case "owner":
      return filing.ownerName ?? "";
    case "date":
      return filing.filed ?? "";
    case "type": {
      const code = fieldText(filing.fields, "code");
      return code ?? "";
    }
    case "shares":
      return fieldNumber(filing.fields, "shares") ?? 0;
    case "value": {
      const s = fieldNumber(filing.fields, "shares");
      const p = fieldNumber(filing.fields, "price");
      return s !== null && p !== null ? s * p : 0;
    }
  }
}

function sortFilings(
  items: readonly InsiderFiling[],
  symbol: string,
  config: SortConfig | null,
): InsiderFiling[] {
  if (config === null) return [...items];
  const { key, dir } = config;
  const multiplier = dir === "asc" ? 1 : -1;

  return [...items].sort((a, b) => {
    const va = sortValue(a, symbol, key);
    const vb = sortValue(b, symbol, key);

    if (typeof va === "string" && typeof vb === "string") {
      return va.localeCompare(vb) * multiplier;
    }
    if (typeof va === "number" && typeof vb === "number") {
      return (va - vb) * multiplier;
    }
    // Mixed types: string before number
    if (typeof va === "string") return -1 * multiplier;
    return 1 * multiplier;
  });
}

/* ── Inline formatters ────────────────────────────────────────────────── */

function formatSharesDisplay(value: number | null): string {
  if (value === null) return "—";
  return formatNumber(value, 0);
}

function formatValueDisplay(shares: number | null, price: number | null): string {
  if (shares === null || price === null) return "—";
  return formatMoney(shares * price, Math.abs(shares * price) >= 10_000);
}

function typeDisplay(fields: readonly InsiderField[]): { label: string; tone: "buy" | "sell" | "other" } {
  const letter = codeLetter(fields);
  if (letter === "P") return { label: "Buy", tone: "buy" };
  if (letter === "S") return { label: "Sale", tone: "sell" };
  const code = fieldText(fields, "code");
  return { label: code ?? "—", tone: "other" };
}

/* ── Sub-components ──────────────────────────────────────────────────── */

function Skeleton({ symbol }: { readonly symbol: string }) {
  return (
    <div data-testid="insider-detail-loading" role="status" aria-busy="true" className="px-2 py-2">
      <span className="sr-only">Loading Form 4 filings for {symbol}</span>
      {Array.from({ length: 5 }, (_, index) => (
        <div key={index} aria-hidden="true" className="mb-2 last:mb-0">
          <div className="flex items-center gap-2">
            <span className="block h-[9px] w-[64px] rounded-[1px] bg-surface-raised" />
            <span className="block h-[9px] w-[38%] rounded-[1px] bg-surface-raised" />
          </div>
          <div className="mt-1 flex items-center gap-2">
            <span className="block h-[9px] w-[28%] rounded-[1px] bg-surface-raised" />
            <span className="block h-[9px] w-[20%] rounded-[1px] bg-surface-raised" />
          </div>
        </div>
      ))}
    </div>
  );
}

/* ── Table header ────────────────────────────────────────────────────── */

const COLUMNS: { key: SortKey; label: string; testId: string }[] = [
  { key: "ticker", label: "Ticker", testId: "insider-sort-ticker" },
  { key: "owner", label: "Owner", testId: "insider-sort-owner" },
  { key: "date", label: "Date", testId: "insider-sort-date" },
  { key: "type", label: "Type", testId: "insider-sort-type" },
  { key: "shares", label: "Shares", testId: "insider-sort-shares" },
  { key: "value", label: "Value", testId: "insider-sort-value" },
];

function SortHeader({
  sortConfig,
  onSort,
}: {
  sortConfig: SortConfig | null;
  onSort: (config: SortConfig) => void;
}) {
  return (
    <thead>
      <tr className="border-b border-line text-label uppercase tracking-[0.06em] text-fg-subtle">
        {COLUMNS.map((col) => {
          const active = sortConfig?.key === col.key;
          const nextDir: SortDir = active && sortConfig.dir === "desc" ? "asc" : "desc";
          return (
            <th
              key={col.key}
              scope="col"
              className="cursor-pointer select-none py-1 pr-1 text-left font-medium hover:text-fg"
              data-testid={col.testId}
              onClick={() => onSort({ key: col.key, dir: nextDir })}
              aria-sort={active ? (sortConfig.dir === "asc" ? "ascending" : "descending") : "none"}
            >
              <span className="inline-flex items-center gap-0.5">
                {col.label}
                {active ? (
                  <span aria-hidden="true" className="text-[10px]">
                    {sortConfig.dir === "asc" ? "▲" : "▼"}
                  </span>
                ) : null}
              </span>
            </th>
          );
        })}
      </tr>
    </thead>
  );
}

/* ── Filing row ───────────────────────────────────────────────────────── */

function FilingRow({
  filing,
  symbol,
}: {
  readonly filing: InsiderFiling;
  readonly symbol: string;
}) {
  const shares = fieldNumber(filing.fields, "shares");
  const price = fieldNumber(filing.fields, "price");
  const type = typeDisplay(filing.fields);

  return (
    <tr
      data-testid="insider-filing"
      data-accession={filing.accession ?? undefined}
      className="border-b border-line/60 last:border-b-0"
    >
      <td className="py-1 pr-1 font-mono text-data text-fg-muted">{symbol}</td>
      <td className="truncate py-1 pr-1 text-data text-fg" title={filing.ownerName ?? undefined}>
        {filing.ownerName ?? "Owner not reported"}
      </td>
      <td className="nums whitespace-nowrap py-1 pr-1 text-data text-fg-muted">
        <time dateTime={filing.filed ?? undefined}>{filing.filed ?? "—"}</time>
      </td>
      <td
        className={cn(
          "whitespace-nowrap py-1 pr-1 text-data",
          type.tone === "buy" && "text-positive",
          type.tone === "sell" && "text-negative",
          type.tone === "other" && "text-fg-muted",
        )}
      >
        {type.label}
      </td>
      <td className="nums py-1 pr-1 text-right text-data text-fg-muted">
        {formatSharesDisplay(shares)}
      </td>
      <td className="nums py-1 text-right text-data text-fg">
        {formatValueDisplay(shares, price)}
      </td>
    </tr>
  );
}

/* ── Type filter bar ─────────────────────────────────────────────────── */

function TypeFilterBar({
  filter,
  onChange,
  buyCount,
  saleCount,
}: {
  filter: TypeFilter;
  onChange: (f: TypeFilter) => void;
  buyCount: number;
  saleCount: number;
}) {
  const buttons: { value: TypeFilter; label: string }[] = [
    { value: "all", label: "All" },
    { value: "buy", label: "Buy" },
    { value: "sale", label: "Sale" },
  ];

  return (
    <div className="flex items-center gap-1.5 border-b border-line/60 px-2 py-1">
      {buttons.map((btn) => (
        <button
          key={btn.value}
          type="button"
          onClick={() => onChange(btn.value)}
          className={cn(
            "rounded-sm border px-2 py-0.5 text-label transition-colors",
            filter === btn.value
              ? "border-accent bg-accent/10 text-accent-text"
              : "border-line text-fg-muted hover:border-line-strong hover:text-fg",
          )}
        >
          {btn.label}
        </button>
      ))}
      <span data-testid="insider-type-count" className="ml-auto text-label text-fg-subtle">
        {buyCount} {buyCount === 1 ? "Buy" : "Buys"}, {saleCount} {saleCount === 1 ? "Sale" : "Sales"}
      </span>
    </div>
  );
}

/* ── Panel ────────────────────────────────────────────────────────────── */

export interface InsiderDetailProps {
  readonly symbol: string;
  readonly onClose: () => void;
}

export function InsiderDetail({ symbol, onClose }: InsiderDetailProps) {
  const view = useInsiderFilings(symbol);
  const [typeFilter, setTypeFilter] = React.useState<TypeFilter>("all");
  const [sortConfig, setSortConfig] = React.useState<SortConfig | null>({ key: "date", dir: "desc" });

  const filings = view.status === "ready" ? view.filings : null;
  const allItems = filings?.items ?? [];

  // Count buys and sales
  const buyCount = allItems.filter((f) => codeLetter(f.fields) === "P").length;
  const saleCount = allItems.filter((f) => codeLetter(f.fields) === "S").length;

  // Apply type filter
  const filtered =
    typeFilter === "all"
      ? allItems
      : typeFilter === "buy"
        ? allItems.filter((f) => codeLetter(f.fields) === "P")
        : allItems.filter((f) => codeLetter(f.fields) === "S");

  // Apply sort
  const sorted = sortFilings(filtered, symbol, sortConfig);

  const shown = sorted.length;
  const total = filings?.count ?? null;

  return (
    <aside
      data-testid="insider-detail"
      aria-labelledby={INSIDER_DETAIL_TITLE_ID}
      className="flex min-h-0 min-w-0 flex-col self-start overflow-hidden rounded-md border border-line bg-surface xl:sticky xl:top-2 xl:max-h-[calc(100vh-120px)]"
    >
      <div className="flex items-start justify-between gap-2 border-b border-line-strong px-2 py-1.5">
        <div className="min-w-0">
          <h2
            id={INSIDER_DETAIL_TITLE_ID}
            className="flex min-w-0 items-baseline gap-1.5 text-ui font-medium text-fg"
          >
            <span className="font-mono">{symbol}</span>
            <span className="text-fg-muted">Form 4 filings</span>
          </h2>
          <p data-testid="insider-detail-note" className="mt-0.5 text-label text-fg-subtle">
            {INSIDER_DETAIL_NOTE}
          </p>
        </div>
        <button
          type="button"
          data-testid="insider-detail-close"
          onClick={onClose}
          className="focus-ring shrink-0 rounded-sm border border-line px-2 py-0.5 text-data text-fg-muted transition-colors hover:border-line-strong hover:text-fg"
        >
          Close
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {view.status === "loading" ? (
          <Skeleton symbol={symbol} />
        ) : view.status === "error" ? (
          <div data-testid="insider-detail-error" role="alert" className="px-2 py-2">
            <p className="text-data text-fg">Form 4 filings for {symbol} did not load.</p>
            <p className="mt-0.5 text-data text-fg-muted">{view.error}</p>
            <button
              type="button"
              onClick={view.retry}
              className="focus-ring mt-1.5 rounded-sm border border-line px-2 py-0.5 text-data text-fg-muted transition-colors hover:border-line-strong hover:text-fg"
            >
              Retry
            </button>
          </div>
        ) : filings === null || allItems.length === 0 ? (
          <p data-testid="insider-detail-empty" className="px-2 py-2 text-data text-fg-muted">
            No Form 4 filings for {symbol}.
          </p>
        ) : (
          <>
            <p
              data-testid="insider-detail-count"
              className="nums border-b border-line/60 px-2 py-1 text-label uppercase tracking-[0.06em] text-fg-subtle"
            >
              {total !== null && total !== shown
                ? `${formatCount(shown)} of ${formatCount(total)} transactions`
                : `${formatCount(shown)} ${shown === 1 ? "transaction" : "transactions"}`}
              {filings.asOf === null ? null : ` · as of ${filings.asOf}`}
            </p>

            {filings.providerErrors.length === 0 ? null : (
              <p data-testid="insider-detail-errors" className="border-b border-line/60 px-2 py-1 text-label text-alert">
                Some filings could not be read: {filings.providerErrors.join("; ")}
              </p>
            )}

            <TypeFilterBar
              filter={typeFilter}
              onChange={setTypeFilter}
              buyCount={buyCount}
              saleCount={saleCount}
            />

            <div className="min-w-0 overflow-x-auto">
              <table className="w-full table-fixed border-collapse text-data" data-testid="insider-filings">
                <caption className="sr-only">
                  Form 4 insider transactions for {symbol}
                </caption>
                <colgroup>
                  <col style={{ width: "58px" }} />
                  <col />
                  <col style={{ width: "72px" }} />
                  <col style={{ width: "52px" }} />
                  <col style={{ width: "62px" }} />
                  <col style={{ width: "70px" }} />
                </colgroup>
                <SortHeader sortConfig={sortConfig} onSort={setSortConfig} />
                <tbody>
                  {sorted.map((filing) => (
                    <FilingRow key={filing.key} filing={filing} symbol={symbol} />
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>
    </aside>
  );
}