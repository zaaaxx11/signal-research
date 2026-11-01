/**
 * Groups: one sector per row, with breadth.
 *
 * A table, because the question is comparative ("which sector is strongest, and
 * is it broad?") and a table is the only layout that keeps seven figures in a
 * column and lets the eye run down it. Density follows `SignalCard`: 12 px type,
 * 24 px rows, one hairline between rows, no card per sector.
 *
 * Four rules this page keeps:
 *
 * * **The change cell is the grid's own delta renderer** (`CellValue` with a
 *   `delta` spec): an arrow glyph, an explicit sign and an `sr-only` word, so a
 *   falling sector is still falling in greyscale, to a screen reader, and to a
 *   reader who cannot separate the two reds.
 * * **Colour never carries a datum alone.** `text-up` / `text-down` appear only
 *   inside that renderer and inside the breadth bar, and the bar is a labelled
 *   `role="img"` that speaks both counts.
 * * **The bar's width is the count ratio**, rounded to one decimal. Nothing is
 *   measured in pixels, so the drawing claims exactly the precision of the two
 *   integers it came from.
 * * **A row leads to the screener** with the label the server sent, encoded
 *   into `?sector=`. The label itself is a real `<a>` (keyboard, middle-click,
 *   copy-link); the rest of the row is a mouse convenience that defers to it.
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
  type GroupRow,
  type GroupsReport,
} from "./groups";
import { useGroups } from "./useGroups";

/**
 * The delta cell's spec. A `delta` kind is what buys the arrow, the sign and
 * the spoken word from `CellValue`; the width fields are unused here (the table
 * owns its own columns) but a `ColumnSpec` requires them.
 */
const CHANGE_SPEC: ColumnSpec = {
  key: "avg_change",
  label: "Change %",
  kind: "delta",
  weight: 1,
  min: 70,
  precision: 2,
};

/** Skeleton rows: enough to look like the table it will become. */
export const GROUPS_SKELETON_ROWS = 12;

/**
 * The box every state occupies, in px: a 26 px header row plus twelve 24 px
 * rows plus the frame's own border. The skeleton draws the same twelve rows, so
 * the table cannot land shorter or taller than the place reserved for it.
 */
export const GROUPS_RESERVED_HEIGHT = 26 + GROUPS_SKELETON_ROWS * 24 + 2;

/** An absent figure prints an em dash with a name, never a zero and never blank. */
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

/**
 * Advancing against declining, as two segments of one track.
 *
 * The track is neutral; the segments are `--up` and `--down`, and both are
 * `aria-hidden` because the wrapping `role="img"` carries the whole sentence
 * ("Technology: 400 advancing, 300 declining"). When nothing moved there is no
 * direction to draw, so the track stays flat — a grey bar, not two empty ones.
 */
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
      <span className="sr-only">Loading sector groups</span>
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

function Rows({ report }: { readonly report: GroupsReport }) {
  /**
   * A click anywhere in a row opens the screener, but a click that the label's
   * own anchor has already claimed (or a modified click, which the browser must
   * handle as "open elsewhere") is left alone.
   */
  const onRowClick = (href: string) => (event: React.MouseEvent<HTMLTableRowElement>) => {
    if (event.defaultPrevented) return;
    if (event.button !== 0) return;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    navigate(href);
  };

  return (
    <div className="overflow-x-auto">
      <table
        data-testid="groups-table"
        className="w-full min-w-[720px] table-fixed border-collapse text-data"
      >
        <caption className="sr-only">Sectors, sorted by average change, strongest first</caption>
        <colgroup>
          <col className="w-[24%]" />
          <col className="w-[11%]" />
          <col className="w-[10%]" />
          <col className="w-[10%]" />
          <col className="w-[11%]" />
          <col className="w-[9%]" />
          <col className="w-[13%]" />
          <col className="w-[12%]" />
        </colgroup>
        <thead>
          <tr className="h-[26px] border-b border-line-strong text-label uppercase tracking-[0.05em] text-fg-subtle">
            <th scope="col" className="px-2 text-left font-medium">
              Sector
            </th>
            <th scope="col" className="px-2 text-right font-medium">
              Change %
            </th>
            <th scope="col" className="px-2 text-right font-medium">
              Advancing
            </th>
            <th scope="col" className="px-2 text-right font-medium">
              Declining
            </th>
            <th scope="col" className="px-2 text-right font-medium">
              Unchanged
            </th>
            <th scope="col" className="px-2 text-right font-medium">
              Names
            </th>
            <th scope="col" className="px-2 text-right font-medium">
              Market cap
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
  const groups = useGroups(region);

  return (
    <main
      data-testid="groups-page"
      className="min-h-0 flex-1 overflow-y-auto bg-bg px-2 py-2"
    >
      <div className="mb-1.5 flex min-w-0 items-baseline justify-between gap-3 px-0.5">
        <h1 className="text-ui font-medium text-fg">Sector performance</h1>
        <p className="font-mono text-label uppercase tracking-[0.08em] text-fg-subtle">
          Sorted by change
        </p>
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
              <p className="text-data text-fg">Sector performance did not load.</p>
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
            No sector groups in this snapshot.
          </p>
        ) : (
          <Rows report={groups.report} />
        )}
      </div>
    </main>
  );
}
