/**
 * Three statement panels: rows are line items, columns are periods.
 *
 * The first column is sticky because a ten-year table is wider than the
 * content column and a label that scrolls away leaves the reader counting
 * columns. Numbers are right-aligned, tabular and mono; an absent cell is the
 * em dash, never `0` and never `0.00`.
 *
 * The document may carry annual and quarterly columns at once. The toggle
 * filters the columns the table draws — it never relabels one kind as the
 * other — and when a kind has no reported periods the panel says so instead of
 * drawing an empty frame.
 *
 * The YoY column is one per row: the collector's own annual figure when it
 * carries one, otherwise the newest column against the one before it. In
 * quarterly mode it compares the newest quarter with the same quarter a year
 * earlier. The TTM column is annual-only and is the collector's; a row with no
 * TTM prints the em dash rather than a nine-month figure in disguise.
 */

import * as React from "react";

import { formatSignedPercent } from "@/grid/format";
import { cn } from "@/lib/utils";

import {
  formatStatementValue,
  periodLabel,
  statementsReason,
  yearOverYear,
  yearOverYearQuarter,
  type StatementLine,
  type StatementMode,
  type StatementsDocument,
} from "./statements";

export interface StatementsTableProps {
  readonly document: StatementsDocument;
  readonly mode: StatementMode;
  readonly onModeChange: (mode: StatementMode) => void;
  readonly className?: string;
}

const MODES: readonly { id: StatementMode; label: string }[] = [
  { id: "annual", label: "Annual" },
  { id: "quarterly", label: "Quarterly" },
];

interface Column {
  readonly index: number;
  readonly label: string;
  readonly periodEnd: string;
}

export function StatementsTable({
  document,
  mode,
  onModeChange,
  className,
}: StatementsTableProps): React.ReactElement {
  const reason = statementsReason(document);
  const columns: Column[] = document.periods
    .map((period, index) => ({
      index,
      label: periodLabel(period),
      periodEnd: period.periodEnd,
      kind: period.kind,
    }))
    .filter((column) => column.kind === mode)
    .map(({ index, label, periodEnd }) => ({ index, label, periodEnd }));

  const yoyFor = (line: StatementLine): number | null => {
    const newest = columns[0];
    if (!newest) return null;
    if (mode === "annual") {
      if (line.yoyPct !== null) return line.yoyPct;
      return yearOverYear(line.cells, newest.index);
    }
    return yearOverYearQuarter(line.cells, document.periods, newest.index);
  };

  return (
    <div data-testid="statements" data-mode={mode} className={cn("flex flex-col gap-4", className)}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-label uppercase tracking-[0.08em] text-fg-subtle">
          {columns.length} {columns.length === 1 ? "period" : "periods"}
          {document.asOf ? ` · newest ${document.asOf}` : ""}
        </p>
        <div role="group" aria-label="Statement period" className="flex items-center gap-0.5">
          {MODES.map((option) => {
            const active = option.id === mode;
            return (
              <button
                key={option.id}
                type="button"
                aria-pressed={active}
                onClick={() => onModeChange(option.id)}
                className={cn(
                  "focus-ring h-6 rounded-sm border px-2 text-data transition-colors",
                  active
                    ? "border-line-control bg-surface-raised text-fg"
                    : "border-line text-fg-muted hover:text-fg",
                )}
              >
                {option.label}
              </button>
            );
          })}
        </div>
      </div>

      {reason !== null && document.periods.length === 0 ? (
        <p
          data-testid="statements-empty"
          className="rounded-sm border border-line bg-surface-sunken px-3 py-2 text-data text-fg-muted"
        >
          {reason}
        </p>
      ) : columns.length === 0 ? (
        <p
          data-testid="statements-mode-empty"
          className="rounded-sm border border-line bg-surface-sunken px-3 py-2 text-data text-fg-muted"
        >
          No {mode} periods are reported for {document.symbol || "this symbol"}. The
          other period remains available above.
        </p>
      ) : (
        <div className="flex flex-col gap-4">
          {document.blocks.map((block) => (
            <section key={block.id} aria-labelledby={`statement-${block.id}`}>
              <h3
                id={`statement-${block.id}`}
                className="text-label font-medium uppercase tracking-[0.08em] text-fg-subtle"
              >
                {block.title}
              </h3>
              <div className="mt-1 overflow-x-auto border border-line">
                <table
                  data-testid={`statements-${block.id}`}
                  data-mode={mode}
                  className="w-full min-w-max border-collapse text-data"
                >
                  <caption className="sr-only">
                    {block.title}, {mode} periods newest first. An em dash means
                    the filing did not report that line for that period.
                  </caption>
                  <thead>
                    <tr className="border-b border-line bg-surface-sunken">
                      <th
                        scope="col"
                        className="sticky left-0 z-10 min-w-[150px] bg-surface-sunken px-2 py-1 text-left text-label font-medium uppercase tracking-[0.06em] text-fg-subtle"
                      >
                        Line item
                      </th>
                      {columns.map((column) => (
                        <th
                          key={`${column.label}-${column.index}`}
                          scope="col"
                          title={column.periodEnd}
                          className="nums whitespace-nowrap px-2 py-1 text-right text-label font-medium uppercase tracking-[0.06em] text-fg-subtle"
                        >
                          {column.label}
                        </th>
                      ))}
                      <th
                        scope="col"
                        className="whitespace-nowrap border-l border-line px-2 py-1 text-right text-label font-medium uppercase tracking-[0.06em] text-fg-subtle"
                      >
                        YoY
                      </th>
                      {mode === "annual" ? (
                        <th
                          scope="col"
                          className="whitespace-nowrap border-l border-line px-2 py-1 text-right text-label font-medium uppercase tracking-[0.06em] text-fg-subtle"
                        >
                          TTM
                        </th>
                      ) : null}
                    </tr>
                  </thead>
                  <tbody>
                    {block.lines.length === 0 ? (
                      <tr>
                        <td
                          colSpan={columns.length + (mode === "annual" ? 3 : 2)}
                          className="px-2 py-1 text-fg-subtle"
                        >
                          {block.title} is not reported.
                        </td>
                      </tr>
                    ) : (
                      block.lines.map((line) => {
                        const change = yoyFor(line);
                        return (
                          <tr
                            key={line.key}
                            data-statement-row={line.key}
                            className="h-5 border-b border-line/70 last:border-b-0"
                          >
                            <th
                              scope="row"
                              title={line.label}
                              className="sticky left-0 z-10 max-w-[220px] truncate bg-surface px-2 text-left text-data font-normal leading-5 text-fg-muted"
                            >
                              {line.label}
                            </th>
                            {columns.map((column) => {
                              const cell = line.cells[column.index];
                              const value = cell?.value ?? null;
                              return (
                                <td
                                  key={`${line.key}-${column.label}`}
                                  data-null={value === null ? "true" : "false"}
                                  className={cn(
                                    "nums whitespace-nowrap px-2 text-right font-mono leading-5 tabular-nums",
                                    value === null ? "text-fg-subtle" : "text-fg",
                                  )}
                                >
                                  {formatStatementValue(
                                    cell ?? { value: null, unit: line.unit, status: null },
                                  )}
                                </td>
                              );
                            })}
                            <td
                              data-testid={`statements-yoy-${line.key}`}
                              className={cn(
                                "nums whitespace-nowrap border-l border-line px-2 text-right font-mono leading-5 tabular-nums",
                                change === null && "text-fg-subtle",
                                change !== null && change > 0 && "text-up",
                                change !== null && change < 0 && "text-down",
                              )}
                            >
                              {change === null ? "—" : formatSignedPercent(change, 2)}
                            </td>
                            {mode === "annual" ? (
                              <td
                                data-testid={`statements-ttm-${line.key}`}
                                className={cn(
                                  "nums whitespace-nowrap border-l border-line px-2 text-right font-mono leading-5 tabular-nums",
                                  line.ttm === null ? "text-fg-subtle" : "text-fg",
                                )}
                              >
                                {formatStatementValue({
                                  value: line.ttm,
                                  unit: line.unit,
                                  status: "derived",
                                })}
                              </td>
                            ) : null}
                          </tr>
                        );
                      })
                    )}
                  </tbody>
                </table>
              </div>
            </section>
          ))}
        </div>
      )}
    </div>
  );
}
