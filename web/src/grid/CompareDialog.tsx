/**
 * Compare: up to five rows, side by side.
 *
 * The dialog is the compare mode: tick rows from the page on screen (five at
 * most), and the table underneath lines their values up column by column. It
 * compares the rows the API already sent — no second request, no merged
 * ordering, and the values are the ones the grid holds, formatted by the same
 * formatter the cells use.
 */

import * as React from "react";

import type { ScreenRow } from "@/data/screens";
import { cn } from "@/lib/utils";

import { columnSpec } from "./columnMeta";
import { DialogShell } from "./DialogShell";
import { formatCell } from "./format";

/** The most rows one comparison may hold. */
export const COMPARE_LIMIT = 5;

export interface CompareDialogProps {
  open: boolean;
  onClose: () => void;
  rows: readonly ScreenRow[];
  columns: readonly string[];
  /** The view's name, for the title. */
  viewLabel: string;
  /** The row the reader has open, ticked first when it is on this page. */
  selectedSymbol: string | null;
}

export function CompareDialog({
  open,
  onClose,
  rows,
  columns,
  viewLabel,
  selectedSymbol,
}: CompareDialogProps) {
  const [chosen, setChosen] = React.useState<string[]>([]);

  React.useEffect(() => {
    if (!open) return;
    setChosen(selectedSymbol !== null && rows.some((row) => row.symbol === selectedSymbol)
      ? [selectedSymbol]
      : []);
  }, [open, selectedSymbol, rows]);

  const toggle = (symbol: string) => {
    setChosen((previous) =>
      previous.includes(symbol)
        ? previous.filter((entry) => entry !== symbol)
        : previous.length >= COMPARE_LIMIT
          ? previous
          : [...previous, symbol],
    );
  };

  const picked = chosen
    .map((symbol) => rows.find((row) => row.symbol === symbol))
    .filter((row): row is ScreenRow => row !== undefined);

  return (
    <DialogShell
      open={open}
      onClose={onClose}
      size="lg"
      title={`Compare · ${viewLabel}`}
      description={`Pick up to ${COMPARE_LIMIT} rows from this page; the comparison uses the values already on screen.`}
    >
      <div className="flex flex-col gap-3">
        <div className="max-h-[180px] overflow-y-auto rounded-sm border border-line">
          <ul aria-label="Rows to compare" className="divide-y divide-line">
            {rows.map((row) => {
              const checked = chosen.includes(row.symbol);
              const full = !checked && chosen.length >= COMPARE_LIMIT;
              return (
                <li key={row.symbol} className="flex items-center gap-2 px-2 py-1">
                  <input
                    id={`compare-${row.symbol}`}
                    type="checkbox"
                    checked={checked}
                    disabled={full}
                    onChange={() => toggle(row.symbol)}
                    aria-label={`Compare ${row.symbol}`}
                    className="size-3.5 accent-[var(--accent)]"
                  />
                  <label
                    htmlFor={`compare-${row.symbol}`}
                    className={cn(
                      "min-w-0 flex-1 truncate font-mono text-data",
                      full ? "text-fg-disabled" : "text-fg",
                    )}
                  >
                    {row.symbol}
                    <span className="ml-2 font-sans text-fg-subtle">
                      {typeof row["company"] === "string" ? row["company"] : ""}
                    </span>
                  </label>
                </li>
              );
            })}
          </ul>
        </div>

        <p aria-live="polite" className="text-data text-fg-subtle">
          {picked.length === 0
            ? "Nothing picked yet."
            : `${picked.length} of ${COMPARE_LIMIT} picked.`}
        </p>

        {picked.length > 0 ? (
          <div className="max-h-[320px] overflow-auto rounded-sm border border-line">
            <table data-testid="compare-table" className="w-full border-collapse text-data">
              <caption className="sr-only">
                {viewLabel}: {picked.map((row) => row.symbol).join(", ")} compared column by column.
              </caption>
              <thead className="sticky top-0 bg-surface-raised">
                <tr>
                  <th scope="col" className="px-2 py-1 text-left font-mono text-label uppercase text-fg-subtle">
                    Column
                  </th>
                  {picked.map((row) => (
                    <th
                      key={row.symbol}
                      scope="col"
                      className="px-2 py-1 text-right font-mono text-data font-semibold text-fg"
                    >
                      {row.symbol}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {columns.map((key) => {
                  const spec = columnSpec(key);
                  return (
                    <tr key={key} className="border-t border-line">
                      <th scope="row" className="px-2 py-0.5 text-left font-normal text-fg-muted" title={key}>
                        {spec.label}
                      </th>
                      {picked.map((row) => (
                        <td key={row.symbol} className="nums px-2 py-0.5 text-right text-fg">
                          {formatCell(row[key], spec).text}
                        </td>
                      ))}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : null}
      </div>
    </DialogShell>
  );
}
