/**
 * Groups: one group per row, with breadth and multi-timeframe performance.
 *
 * A table, because the question is comparative ("which sector is strongest, and
 * is it broad?") and a table is the only layout that keeps figures in a
 * column and lets the eye run down it. Density follows `SignalCard`: 12 px type,
 * 24 px rows, one hairline between rows, no card per group.
 *
 * This page adds a **group-by tab bar** above the table, five **sortable
 * performance columns** (1D, 1W, 1M, 3M, 6M) with ↑↓ indicators, a
 * **breadth bar** per row, a dynamic **group count label**, and optional
 * **quoteProps** on rows for the global HoverBubble.
 *
 * The page is one box in every state — skeleton, table, empty, error — so the
 * answer landing cannot move what is below it.
 *
 * Section title: variant 7 (lime). Background: black (polos).
 */

import * as React from "react";

import { formatCount } from "@/breadth/format";
import { CellValue } from "@/grid/cellRenderers";
import { formatMoney } from "@/grid/format";
import type { ColumnSpec } from "@/grid/columnMeta";
import { ErrorState } from "@/components/ErrorState";
import { SectionTitle } from "@/components/SectionTitle";
import { cn } from "@/lib/utils";
import { quoteProps } from "@/lib/quoteAttrs";
import { Link, navigate } from "@/shell/routes";

import {
  advanceShare,
  breadthLabel,
  screenerHref,
  type GroupBy,
  type GroupRow,
  type GroupsReport,
  type GroupsSort,
} from "./groups";
import { useGroups } from "./useGroups";

/* ---------------------------------------------------------------------------
 * Column specs
 * ------------------------------------------------------------------------ */

const CHANGE_SPEC: ColumnSpec = {
  key: "avg_change",
  label: "Change %",
  kind: "delta",
  weight: 1,
  min: 70,
  precision: 2,
};

function perfSpec(key: string, label: string, precision = 1): ColumnSpec {
  return { key, label, kind: "delta", weight: 1, min: 54, precision };
}

const PERF_COLUMNS: readonly { spec: ColumnSpec; sortKey: GroupsSort }[] = [
  { spec: perfSpec("perf_1d", "1D"), sortKey: "perf_1d" },
  { spec: perfSpec("perf_1w", "1W"), sortKey: "perf_1w" },
  { spec: perfSpec("perf_1m", "1M"), sortKey: "perf_1m" },
  { spec: perfSpec("perf_3m", "3M"), sortKey: "perf_3m" },
  { spec: perfSpec("perf_6m", "6M"), sortKey: "perf_6m" },
];

/* ---------------------------------------------------------------------------
 * Constants
 * ------------------------------------------------------------------------ */

export const GROUPS_SKELETON_ROWS = 12;
/** 26 px header + 12 rows × 24 px + 2 px border. */
export const GROUPS_RESERVED_HEIGHT = 26 + GROUPS_SKELETON_ROWS * 24 + 2;

const GROUP_BY_TABS: readonly { key: GroupBy; label: string }[] = [
  { key: "sector", label: "Sector" },
  { key: "industry", label: "Industry" },
  { key: "country", label: "Country" },
  { key: "capitalization", label: "Market Cap" },
];

const PLURAL: Record<string, string> = {
  Sector: "sectors",
  Industry: "industries",
  Country: "countries",
  "Market Cap": "Market Cap groups",
};

/* ---------------------------------------------------------------------------
 * Helpers
 * ------------------------------------------------------------------------ */

function Absent() {
  return (
    <span className="text-fg-subtle" title="Not available" aria-label="Not available">
      —
    </span>
  );
}

function Count({ value }: { readonly value: number | null }) {
  if (value === null) return <Absent />;
  return <span className="nums">{formatCount(value)}</span>;
}

function MarketCap({ value }: { readonly value: number | null }) {
  if (value === null) return <Absent />;
  return <span className="nums text-fg-muted">{formatMoney(value, true)}</span>;
}

function BreadthBar({ row }: { readonly row: GroupRow }) {
  const up = advanceShare(row.adv, row.dec);
  const down = Math.round((100 - up) * 10) / 10;
  const moved = row.adv !== null && row.dec !== null && row.adv + row.dec > 0;

  return (
    <span
      role="img"
      aria-label={breadthLabel(row)}
      data-testid={`group-bar-${row.id}`}
      className="relative block h-1.5 w-full overflow-hidden rounded-[1px] bg-surface-raised"
    >
      {moved ? (
        <>
          <span
            aria-hidden="true"
            data-testid={`group-bar-up-${row.id}`}
            className="absolute inset-y-0 left-0 bg-up"
            style={{ width: `${up}%` }}
          />
          <span
            aria-hidden="true"
            data-testid={`group-bar-down-${row.id}`}
            className="absolute inset-y-0 bg-down"
            style={{ left: `${up}%`, width: `${down}%` }}
          />
        </>
      ) : (
        <span aria-hidden="true" className="absolute inset-0 bg-flat" />
      )}
    </span>
  );
}

/* ---------------------------------------------------------------------------
 * Sortable header
 * ------------------------------------------------------------------------ */

function SortableTh({
  sortKey,
  active,
  direction,
  onSort,
  children,
  className,
}: {
  readonly sortKey: GroupsSort;
  readonly active: boolean;
  readonly direction: "asc" | "desc";
  readonly onSort: (key: GroupsSort) => void;
  readonly children: React.ReactNode;
  readonly className?: string;
}) {
  return (
    <th scope="col" className={cn("px-2 text-right font-medium", className)}>
      <button
        type="button"
        onClick={() => onSort(sortKey)}
        className={cn(
          "inline-flex items-center gap-0.5 text-label uppercase tracking-[0.05em] transition-colors",
          active ? "text-accent-text" : "text-fg-subtle hover:text-fg",
        )}
      >
        {children}
        <span aria-hidden="true" className="text-[10px] w-[10px] text-center">
          {active ? (direction === "asc" ? "↑" : "↓") : " "}
        </span>
      </button>
    </th>
  );
}

/* ---------------------------------------------------------------------------
 * Skeleton
 * ------------------------------------------------------------------------ */

function Skeleton() {
  return (
    <div data-testid="groups-skeleton" aria-busy="true">
      <span className="sr-only">Loading groups</span>
      <div aria-hidden="true" className="h-[26px] border-b border-line-strong" />
      {Array.from({ length: GROUPS_SKELETON_ROWS }, (_, index) => (
        <div
          key={index}
          aria-hidden="true"
          data-testid="groups-skeleton-row"
          className="flex h-6 items-center gap-2 border-b border-line/60 px-2"
        >
          <span
            className={cn(
              "block h-[9px] rounded-[1px] bg-surface-raised",
              index % 3 === 0 ? "w-[12%]" : index % 3 === 1 ? "w-[16%]" : "w-[10%]",
            )}
          />
          <span className="block h-[9px] flex-1 rounded-[1px] bg-surface-raised" />
          <span className="block h-[9px] w-[7%] rounded-[1px] bg-surface-raised" />
        </div>
      ))}
    </div>
  );
}

/* ---------------------------------------------------------------------------
 * Perf cell
 * ------------------------------------------------------------------------ */

function PerfCell({ spec, value }: { readonly spec: ColumnSpec; readonly value: number | null }) {
  if (value === null) return <Absent />;
  return <CellValue spec={spec} value={value} />;
}

/* ---------------------------------------------------------------------------
 * Rows
 * ------------------------------------------------------------------------ */

function Rows({
  report,
  groupBy,
  sortBy,
  sortDir,
  onSort,
}: {
  readonly report: GroupsReport;
  readonly groupBy: GroupBy;
  readonly sortBy: GroupsSort;
  readonly sortDir: "asc" | "desc";
  readonly onSort: (key: GroupsSort) => void;
}) {
  const onRowClick = (href: string) => (event: React.MouseEvent<HTMLTableRowElement>) => {
    if (event.defaultPrevented) return;
    if (event.button !== 0) return;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    navigate(href);
  };

  const groupLabel =
    GROUP_BY_TABS.find((t) => t.key === groupBy)?.label ?? "Group";

  return (
    <div className="overflow-x-auto">
      <table
        data-testid="groups-table"
        className="w-full min-w-[1160px] table-fixed border-collapse text-data"
      >
        <caption className="sr-only">
          {PLURAL[groupLabel] ?? `${groupLabel}s`}, sorted by{" "}
          {PERF_COLUMNS.find((c) => c.sortKey === sortBy)?.spec.label ??
            sortBy === "avg_change" ? "Change %" :
            sortBy === "name" ? "Name" :
            sortBy === "count" ? "Count" :
            sortBy === "market_cap" ? "Market Cap" : sortBy},{" "}
          {sortDir === "asc" ? "weakest" : "strongest"} first
        </caption>
        <colgroup>
          <col className="w-[15%]" />
          <col className="w-[7%]" />
          <col className="w-[5%]" />
          <col className="w-[5%]" />
          <col className="w-[5%]" />
          <col className="w-[5%]" />
          <col className="w-[5%]" />
          <col className="w-[5%]" />
          <col className="w-[5%]" />
          <col className="w-[5%]" />
          <col className="w-[6%]" />
          <col className="w-[8%]" />
          <col className="w-[9%]" />
        </colgroup>
        <thead>
          <tr className="h-[26px] border-b border-line-strong">
            <SortableTh
              sortKey="name"
              active={sortBy === "name"}
              direction={sortDir}
              onSort={onSort}
              className="px-2 text-left"
            >
              {groupLabel}
            </SortableTh>
            <SortableTh
              sortKey="avg_change"
              active={sortBy === "avg_change"}
              direction={sortDir}
              onSort={onSort}
            >
              Chg%
            </SortableTh>
            {PERF_COLUMNS.map(({ spec, sortKey }) => (
              <SortableTh
                key={sortKey}
                sortKey={sortKey}
                active={sortBy === sortKey}
                direction={sortDir}
                onSort={onSort}
              >
                {spec.label}
              </SortableTh>
            ))}
            <th scope="col" className="px-2 text-right font-medium text-label uppercase tracking-[0.05em] text-fg-subtle">
              Adv
            </th>
            <th scope="col" className="px-2 text-right font-medium text-label uppercase tracking-[0.05em] text-fg-subtle">
              Dec
            </th>
            <th scope="col" className="px-2 text-right font-medium text-label uppercase tracking-[0.05em] text-fg-subtle">
              Unch
            </th>
            <SortableTh
              sortKey="count"
              active={sortBy === "count"}
              direction={sortDir}
              onSort={onSort}
            >
              Names
            </SortableTh>
            <SortableTh
              sortKey="market_cap"
              active={sortBy === "market_cap"}
              direction={sortDir}
              onSort={onSort}
            >
              Cap
            </SortableTh>
            <th scope="col" className="px-2 text-left font-medium text-label uppercase tracking-[0.05em] text-fg-subtle">
              Breadth
            </th>
          </tr>
        </thead>
        <tbody>
          {report.rows.map((row) => {
            const href = screenerHref(row.label);
            const perfs: Record<string, number | null> = {
              perf_1d: row.perf1d,
              perf_1w: row.perf1w,
              perf_1m: row.perf1m,
              perf_3m: row.perf3m,
              perf_6m: row.perf6m,
            };
            return (
              <tr
                key={row.id}
                data-testid={`group-row-${row.id}`}
                data-sector-row={row.label}
                onClick={onRowClick(href)}
                className="h-6 cursor-pointer border-b border-line/60 odd:bg-row-alt hover:bg-row-hover"
                {...(row.symbol
                  ? quoteProps({
                      symbol: row.symbol,
                      name: row.label,
                      kind: "stock",
                    })
                  : {})}
              >
                <td className="min-w-0 px-2">
                  <Link
                    to={href}
                    title={`${row.label} — open in the screener`}
                    className="block truncate text-fg hover:text-accent-text"
                  >
                    {row.label}
                  </Link>
                </td>
                <td data-testid={`group-change-${row.id}`} className="px-2">
                  <CellValue spec={CHANGE_SPEC} value={row.avgChange} />
                </td>
                {PERF_COLUMNS.map(({ spec, sortKey }) => (
                  <td
                    key={sortKey}
                    data-testid={`group-${sortKey}-${row.id}`}
                    className="px-2 text-right"
                  >
                    <PerfCell spec={spec} value={perfs[sortKey] ?? null} />
                  </td>
                ))}
                <td data-testid={`group-adv-${row.id}`} className="px-2 text-right text-fg-muted">
                  <Count value={row.adv} />
                </td>
                <td data-testid={`group-dec-${row.id}`} className="px-2 text-right text-fg-muted">
                  <Count value={row.dec} />
                </td>
                <td
                  data-testid={`group-unchanged-${row.id}`}
                  className="px-2 text-right text-fg-muted"
                >
                  <Count value={row.unchanged} />
                </td>
                <td data-testid={`group-count-${row.id}`} className="px-2 text-right text-fg">
                  <Count value={row.count} />
                </td>
                <td
                  data-testid={`group-cap-${row.id}`}
                  className="px-2 text-right text-fg-muted"
                >
                  <MarketCap value={row.marketCap} />
                </td>
                <td className="px-2">
                  <BreadthBar row={row} />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/* ---------------------------------------------------------------------------
 * Page
 * ------------------------------------------------------------------------ */

export interface GroupsPageProps {
  readonly region?: string;
}

export function GroupsPage({ region }: GroupsPageProps = {}) {
  const [groupBy, setGroupBy] = React.useState<GroupBy>("sector");
  const [sortBy, setSortBy] = React.useState<GroupsSort>("avg_change");
  const [sortDir, setSortDir] = React.useState<"asc" | "desc">("desc");

  const groups = useGroups(region, groupBy, sortBy, sortDir);

  const handleTab = (key: GroupBy) => {
    setGroupBy(key);
    // Reset sort to default when changing group-by.
    setSortBy("avg_change");
    setSortDir("desc");
  };

  const handleSort = (key: GroupsSort) => {
    if (sortBy === key) {
      setSortDir((d) => (d === "desc" ? "asc" : "desc"));
    } else {
      setSortBy(key);
      setSortDir("desc");
    }
  };

  const groupLabel =
    GROUP_BY_TABS.find((t) => t.key === groupBy)?.label ?? "Group";
  const rowCount = groups.report?.rows.length ?? 0;
  const countLabel =
    groups.status === "ready" && rowCount > 0
      ? `${rowCount} ${PLURAL[groupLabel] ?? groupLabel.toLowerCase()}`
      : undefined;

  return (
    <main
      data-testid="groups-page"
      className="min-h-0 flex-1 overflow-y-auto bg-black px-2 py-2"
    >
      <SectionTitle
        variant={7}
        hint={countLabel}
        right={
          <span className="shrink-0 text-[10px] text-fg-subtle">
            {groups.status === "loading" ? "Loading…" : ""}
          </span>
        }
      >
        Group performance
      </SectionTitle>

      {/* Group-by tab bar */}
      <div
        role="tablist"
        aria-label="Group by"
        data-testid="groups-tabbar"
        className="mb-1.5 flex gap-1"
      >
        {GROUP_BY_TABS.map((tab) => (
          <button
            key={tab.key}
            role="tab"
            aria-selected={groupBy === tab.key}
            data-testid={`groups-tab-${tab.key}`}
            type="button"
            onClick={() => handleTab(tab.key)}
            className={cn(
              "focus-ring rounded-sm px-2 py-0.5 text-data transition-colors",
              groupBy === tab.key
                ? "bg-accent-text text-bg font-medium"
                : "text-fg-muted hover:text-fg",
            )}
          >
            {tab.label}
          </button>
        ))}
      </div>

      <div
        data-testid="groups-frame"
        style={{ minHeight: GROUPS_RESERVED_HEIGHT }}
        className="overflow-hidden rounded-md border border-line bg-surface"
      >
        {groups.status === "loading" ? (
          <Skeleton />
        ) : groups.status === "error" ? (
          <ErrorState
            testId="groups-error"
            title="Group performance did not load"
            error={groups.error ?? "Request failed"}
            onRetry={groups.retry}
          />
        ) : groups.report === null || groups.report.rows.length === 0 ? (
          <p data-testid="groups-empty" className="px-3 py-2 text-data text-fg-muted">
            No groups in this snapshot.
          </p>
        ) : (
          <Rows
            report={groups.report}
            groupBy={groupBy}
            sortBy={sortBy}
            sortDir={sortDir}
            onSort={handleSort}
          />
        )}
      </div>
    </main>
  );
}