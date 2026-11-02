/**
 * Six parallel snapshot tables, fourteen rows each.
 *
 * Six `<table>`s rather than one 84-column grid: every cell here is a
 * label/value pair, and a pair is a row. That gives a screen reader a caption
 * per block, a row header per label and a value cell per number without any
 * invented ARIA. Visually the six blocks are one responsive grid — two columns
 * below 900 px, three to 1199 px, six above — while the DOM stays six honest
 * tables.
 *
 * Every cell is drawn, including the absent ones. A symbol that reports half
 * the catalogue still shows 84 rows; the missing half is the em dash, which is
 * the only way the reader can tell "not reported" from "not asked".
 */

import * as React from "react";

import { cn } from "@/lib/utils";

import {
  SNAPSHOT_COLUMNS,
  SNAPSHOT_ROWS,
  snapshotCell,
  snapshotLabel,
  type SnapshotSources,
} from "./snapshot";

export interface SnapshotGridProps {
  readonly sources: SnapshotSources;
  readonly className?: string;
}

export function SnapshotGrid({ sources, className }: SnapshotGridProps): React.ReactElement {
  return (
    <div
      data-testid="snapshot-grid"
      data-columns={SNAPSHOT_COLUMNS.length}
      data-rows={SNAPSHOT_ROWS}
      className={cn(
        "grid grid-cols-2 gap-x-5 gap-y-4 min-[900px]:grid-cols-3 min-[1200px]:grid-cols-6",
        className,
      )}
    >
      {SNAPSHOT_COLUMNS.map((column) => (
        <table
          key={column.id}
          data-snapshot-column={column.id}
          className="w-full min-w-0 table-fixed border-collapse"
        >
          <caption className="sr-only">{column.heading} snapshot</caption>
          <colgroup>
            <col className="w-[52%]" />
            <col className="w-[48%]" />
          </colgroup>
          <thead>
            <tr>
              <th
                colSpan={2}
                scope="colgroup"
                className="border-b border-line pb-1 text-left text-label font-medium uppercase tracking-[0.08em] text-fg-subtle"
              >
                {column.heading}
              </th>
            </tr>
          </thead>
          <tbody>
            {column.keys.map((key) => {
              const cell = snapshotCell(key, sources);
              return (
                <tr key={key} data-snapshot-row={key} className="h-5 align-middle">
                  <th
                    scope="row"
                    title={snapshotLabel(key)}
                    className="max-w-0 truncate pr-2 text-left text-data font-normal leading-5 text-fg-muted"
                  >
                    {snapshotLabel(key)}
                  </th>
                  <td
                    data-snapshot-cell={key}
                    data-absent={cell.absent ? "true" : "false"}
                    data-tone={cell.tone ?? "none"}
                    title={`${snapshotLabel(key)}: ${cell.text}`}
                    className={cn(
                      "nums truncate text-right font-mono text-data leading-5 tabular-nums",
                      cell.absent && "text-fg-subtle",
                      cell.tone === "up" && "text-up",
                      cell.tone === "down" && "text-down",
                      cell.tone === null && !cell.absent && "text-fg",
                    )}
                  >
                    {cell.text}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      ))}
    </div>
  );
}
