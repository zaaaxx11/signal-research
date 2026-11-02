/**
 * Compare: up to five symbols, side by side.
 *
 * The flow starts with the same ticker search the header uses — the comparison
 * is about *symbols*, not about the twenty rows that happen to share a page.
 * A picked symbol the grid has already loaded is compared with the values on
 * screen, formatted by the cell formatter; one from another page still shows
 * its symbol, company and exchange, and its value cells say "—" rather than
 * inventing a number for a row this tab has never read.
 *
 * Five is the ceiling because six columns plus a label column is the widest a
 * comparison stays readable at this type size; the picker disables at five
 * instead of silently dropping the sixth.
 */

import * as React from "react";

import { Icon } from "@/components/Icon";
import type { SymbolHit } from "@/lib/api";
import type { ScreenRow } from "@/data/screens";
import { SymbolSearch } from "@/shell/SymbolSearch";
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

/** A row as the comparison header prints it: symbol, company, exchange. */
function hitFromRow(row: ScreenRow): SymbolHit {
  return {
    symbol: row.symbol,
    name: typeof row["company"] === "string" ? row["company"] : "",
    exchange: typeof row["exchange"] === "string" ? row["exchange"] : "",
  };
}

export function CompareDialog({
  open,
  onClose,
  rows,
  columns,
  viewLabel,
  selectedSymbol,
}: CompareDialogProps) {
  const [picked, setPicked] = React.useState<SymbolHit[]>([]);

  React.useEffect(() => {
    if (!open) return;
    const row = rows.find((entry) => entry.symbol === selectedSymbol);
    setPicked(row ? [hitFromRow(row)] : []);
  }, [open, selectedSymbol, rows]);

  const add = (hit: SymbolHit) => {
    setPicked((previous) =>
      previous.some((entry) => entry.symbol === hit.symbol) || previous.length >= COMPARE_LIMIT
        ? previous
        : [...previous, hit],
    );
  };

  const remove = (symbol: string) => {
    setPicked((previous) => previous.filter((entry) => entry.symbol !== symbol));
  };

  const full = picked.length >= COMPARE_LIMIT;
  const missing = picked.filter((hit) => !rows.some((row) => row.symbol === hit.symbol));

  return (
    <DialogShell
      open={open}
      onClose={onClose}
      size="lg"
      title={`Compare · ${viewLabel}`}
      description={`Search up to ${COMPARE_LIMIT} symbols; the comparison lines their values up column by column.`}
    >
      <div className="flex flex-col gap-3">
        <div className="flex flex-col gap-0.5">
          <span className="flex items-center gap-1 font-mono text-label uppercase tracking-[0.08em] text-fg-subtle">
            <Icon name="search" />
            Search a ticker
          </span>
          <SymbolSearch
            label="Search a ticker to compare"
            idPrefix="compare-search"
            placeholder={full ? "Five picked — remove one to add another" : "Search a ticker…"}
            showHint={false}
            onSelect={add}
          />
        </div>

        <ul aria-label="Symbols being compared" className="flex flex-wrap gap-1">
          {picked.map((hit) => (
            <li
              key={hit.symbol}
              data-testid={`compare-pick-${hit.symbol}`}
              className="flex items-center gap-1 rounded-sm border border-line bg-surface-raised px-1.5 py-0.5"
            >
              <span className="font-mono text-data font-semibold text-fg">{hit.symbol}</span>
              {hit.name ? (
                <span className="max-w-[14rem] truncate text-data text-fg-muted">{hit.name}</span>
              ) : null}
              <button
                type="button"
                onClick={() => remove(hit.symbol)}
                aria-label={`Remove ${hit.symbol} from the comparison`}
                title={`Remove ${hit.symbol}`}
                className="rounded-sm p-0.5 text-fg-subtle transition-colors hover:text-fg"
              >
                <Icon name="x" size={12} />
              </button>
            </li>
          ))}
        </ul>

        <p aria-live="polite" className="text-data text-fg-subtle">
          {picked.length === 0
            ? "Nothing picked yet — search a ticker to start."
            : `${picked.length} of ${COMPARE_LIMIT} picked.`}
        </p>

        {picked.length > 0 ? (
          <div className="max-h-[340px] overflow-auto rounded-sm border border-line">
            <table data-testid="compare-table" className="w-full border-collapse text-data">
              <caption className="sr-only">
                {viewLabel}: {picked.map((hit) => hit.symbol).join(", ")} compared column by
                column.
              </caption>
              <thead className="sticky top-0 bg-surface-raised">
                <tr>
                  <th scope="col" className="px-2 py-1 text-left font-mono text-label uppercase text-fg-subtle">
                    Column
                  </th>
                  {picked.map((hit) => (
                    <th
                      key={hit.symbol}
                      scope="col"
                      className="px-2 py-1 text-right font-mono text-data font-semibold text-fg"
                    >
                      {hit.symbol}
                      <span className="block truncate font-sans text-label font-normal normal-case text-fg-subtle">
                        {hit.exchange || "—"}
                      </span>
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
                      {picked.map((hit) => {
                        const row = rows.find((entry) => entry.symbol === hit.symbol);
                        let text = "—";
                        if (row) text = formatCell(row[key], spec).text;
                        else if (key === "symbol") text = hit.symbol;
                        else if (key === "company") text = hit.name || "—";
                        else if (key === "exchange") text = hit.exchange || "—";
                        return (
                          <td
                            key={hit.symbol}
                            className={cn("nums px-2 py-0.5 text-right", row ? "text-fg" : "text-fg-subtle")}
                          >
                            {text}
                          </td>
                        );
                      })}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : null}

        {missing.length > 0 ? (
          <p className="text-data text-fg-subtle">
            {missing.map((hit) => hit.symbol).join(", ")}{" "}
            {missing.length === 1 ? "is" : "are"} not on this page: identity only until that row is
            loaded.
          </p>
        ) : null}
      </div>
    </DialogShell>
  );
}
