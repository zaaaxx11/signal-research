/**
 * Groups: one group per row, with breadth and multi-timeframe performance.
 *
 * A table, because the question is comparative ("which sector is strongest, and
 * is it broad?") and a table is the only layout that keeps figures in a
 * column and lets the eye run down it. Density follows `SignalCard`: 12 px type,
 * 24 px rows, one hairline between rows, no card per group.
 *
 * This page adds a **group-by tab bar** and a **sort selector** above the
 * table, plus three **performance columns** (1W, 1M, 3M) between Change % and
 * Names. The four tabs re-fetch with `?group_by=` and the sort selector
 * re-fetches with `?sort=&direction=`.
 *
 * The page is one box in every state — skeleton, table, empty, error — so the
 * answer landing cannot move what is below it.
 */

import * as React from "react";

import { formatCount } from "@/breadth/format";
import { CellValue } from "@/grid/cellRenderers";
import { formatMoney } from "@/grid/format";
import type { ColumnSpec } from "@/grid/columnMeta";
import { cn } from "@/lib/utils";
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

const CHANGE_SPEC: ColumnSpec = {
  key: "avg_change",
  label: "Change %",
  kind: "delta",
  weight: 1,
  min: 70,
  precision: 2,
};

const PERF_1W_SPEC: ColumnSpec = {
  key: "perf_1w",
  label: "1W",
  kind: "delta",
  weight: 1,
  min: 54,
  precision: 1,
};
const PERF_1M_SPEC: ColumnSpec = {
  key: "perf_1m",
  label: "1M",
  kind: "delta",
  weight: 1,
  min: 54,
  precision: 1,
};
const PERF_3M_SPEC: ColumnSpec = {
  key: "perf_3m",
  label: "3M",
  kind: "delta",
  weight: 1,
  min: 54,
  precision: 1,
};

export const GROUPS_SKELETON_ROWS = 12;
export const GROUPS_RESERVED_HEIGHT = 26 + GROUPS_SKELETON_ROWS * 24 + 2;

const GROUP_BY_TABS: readonly { key: GroupBy; label: string }[] = [
  { key: "sector", label: "Sector" },
  { key: "industry", label: "Industry" },
  { key: "country", label: "Country" },
  { key: "capitalization", label: "Market Cap" },
];

const PLURAL: Record<string, string> = {
  Sector: "Sectors",
  Industry: "Industries",
  Country: "Countries",
  "Market Cap": "Market Cap groups",
};

const SORT_OPTIONS: readonly { key: GroupsSort; label: string }[] = [
  { key: "avg_change", label: "Change %" },
  { key: "name", label: "Name" },
  { key: "count", label: "Count" },
  { key: "market_cap", label: "Market Cap" },
];

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
              index % 3 === 0 ? "w-[16%]" : index % 3 === 1 ? "w-[21%]" : "w-[13%]",
            )}
          />
          <span className="block h-[9px] flex-1 rounded-[1px] bg-surface-raised" />
          <span className="block h-[9px] w-[9%] rounded-[1px] bg-surface-raised" />
        </div>
      ))}
    </div>
  );
}

function PerfCell({ spec, value }: { readonly spec: ColumnSpec; readonly value: number | null }) {
  if (value === null) return <Absent />;
  return <CellValue spec={spec} value={value} />;
}

function Rows({
  report,
  groupBy,
}: {
  readonly report: GroupsReport;
  readonly groupBy: GroupBy;
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
        className="w-full min-w-[960px] table-fixed border-collapse text-data"
      >
        <caption className="sr-only">
          {PLURAL[groupLabel] ?? `${groupLabel}s`}, sorted by average change, strongest first
        </caption>
        <colgroup>
          <col className="w-[18%]" />
          <col className="w-[8%]" />
          <col className="w-[6%]" />
          <col className="w-[6%]" />
          <col className="w-[6%]" />
          <col className="w-[6%]" />
          <col className="w-[6%]" />
          <col className="w-[8%]" />
          <col className="w-[7%]" />
          <col className="w-[10%]" />
          <col className="w-[11%]" />
        </colgroup>
        <thead>
          <tr className="h-[26px] border-b border-line-strong text-label uppercase tracking-[0.05em] text-fg-subtle">
            <th scope="col" className="px-2 text-left font-medium">
              {groupLabel}
            </th>
            <th scope="col" className="px-2 text-right font-medium">
              Change %
            </th>
            <th scope="col" className="px-2 text-right font-medium">
              1W
            </th>
            <th scope="col" className="px-2 text-right font-medium">
              1M
            </th>
            <th scope="col" className="px-2 text-right font-medium">
              3M
            </th>
            <th scope="col" className="px-2 text-right font-medium">
              Adv
            </th>
            <th scope="col" className="px-2 text-right font-medium">
              Dec
            </th>
            <th scope="col" className="px-2 text-right font-medium">
              Unch
            </th>
            <th scope="col" className="px-2 text-right font-medium">
              Names
            </th>
            <th scope="col" className="px-2 text-right font-medium">
              Cap
            </th>
            <th scope="col" className="px-2 text-left font-medium">
              Breadth
            </th>
          </tr>
        </thead>
        <tbody>
          {report.rows.map((row) => {
            const href = screenerHref(row.label);
            return (
              <tr
                key={row.id}
                data-testid={`group-row-${row.id}`}
                data-sector-row={row.label}
                onClick={onRowClick(href)}
                className="h-6 cursor-pointer border-b border-line/60 odd:bg-row-alt hover:bg-row-hover"
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
                <td data-testid={`group-perf1w-${row.id}`} className="px-2 text-right">
                  <PerfCell spec={PERF_1W_SPEC} value={row.perf1w} />
                </td>
                <td data-testid={`group-perf1m-${row.id}`} className="px-2 text-right">
                  <PerfCell spec={PERF_1M_SPEC} value={row.perf1m} />
                </td>
                <td data-testid={`group-perf3m-${row.id}`} className="px-2 text-right">
                  <PerfCell spec={PERF_3M_SPEC} value={row.perf3m} />
                </td>
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
  };

  const handleSort = (key: GroupsSort) => {
    setSortBy(key);
  };

  const toggleDir = () => {
    setSortDir((d) => (d === "desc" ? "asc" : "desc"));
  };

  return (
    <main
      data-testid="groups-page"
      className="min-h-0 flex-1 overflow-y-auto bg-bg px-2 py-2"
    >
      <div className="mb-1.5 flex min-w-0 items-baseline justify-between gap-3 px-0.5">
        <h1 className="text-ui font-medium text-fg">Group performance</h1>
        <p className="font-mono text-label uppercase tracking-[0.08em] text-fg-subtle">
          Sorted by {SORT_OPTIONS.find((o) => o.key === sortBy)?.label ?? "Change %"} {sortDir === "asc" ? "↑" : "↓"}
        </p>
      </div>

      {/* Group-by tab bar */}
      <div
        role="tablist"
        aria-label="Group by"
        data-testid="groups-tabbar"
        className="mb-1.5 flex gap-1 px-0.5"
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

      {/* Sort selector + direction toggle */}
      <div className="mb-1.5 flex items-center gap-2 px-0.5">
        <label htmlFor="groups-sort" className="text-label text-fg-subtle">
          Sort by:
        </label>
        <select
          id="groups-sort"
          data-testid="groups-sort"
          value={sortBy}
          onChange={(e) => handleSort(e.target.value as GroupsSort)}
          className="focus-ring rounded-sm border border-line bg-surface px-1.5 py-0.5 text-data text-fg"
        >
          {SORT_OPTIONS.map((opt) => (
            <option key={opt.key} value={opt.key}>
              {opt.label}
            </option>
          ))}
        </select>
        <button
          type="button"
          data-testid="groups-sort-dir"
          aria-label={`Sort ${sortDir === "asc" ? "ascending" : "descending"}`}
          onClick={toggleDir}
          className="focus-ring rounded-sm border border-line px-1.5 py-0.5 text-data text-fg-muted hover:text-fg"
        >
          {sortDir === "asc" ? "↑" : "↓"}
        </button>
      </div>

      <div
        data-testid="groups-frame"
        style={{ minHeight: GROUPS_RESERVED_HEIGHT }}
        className="overflow-hidden rounded-md border border-line bg-surface"
      >
        {groups.status === "loading" ? (
          <Skeleton />
        ) : groups.status === "error" ? (
          <div
            data-testid="groups-error"
            role="alert"
            className="flex items-start justify-between gap-3 px-3 py-2"
          >
            <div className="min-w-0">
              <p className="text-data text-fg">Group performance did not load.</p>
              <p className="mt-0.5 text-data text-fg-muted">{groups.error}</p>
            </div>
            <button
              type="button"
              onClick={groups.retry}
              className="focus-ring shrink-0 rounded-sm border border-line px-2 py-0.5 text-data text-fg-muted transition-colors hover:border-line-strong hover:text-fg"
            >
              Retry
            </button>
          </div>
        ) : groups.report === null || groups.report.rows.length === 0 ? (
          <p data-testid="groups-empty" className="px-3 py-2 text-data text-fg-muted">
            No groups in this snapshot.
          </p>
        ) : (
          <Rows report={groups.report} groupBy={groupBy} />
        )}
      </div>
    </main>
  );
}