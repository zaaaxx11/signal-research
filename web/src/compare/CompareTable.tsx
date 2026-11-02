/**
 * The comparison table: metrics as rows, companies as columns.
 *
 * Geometry is the argument. A reader compares a *metric* across companies, so
 * the metric names run down the left, where the eye can follow one line across
 * every company, and each company is a column that can be removed from its own
 * header. The first column is pinned: at ten companies the table is wider than
 * the viewport, and a number whose label has scrolled away is a number without
 * a meaning.
 *
 * One accent marker, and only one: the best cell in a row gets the raised rose
 * surface. It is never the only signal — the number is printed either way, and
 * a row without a direction (a share price is not a score) gets no marker at
 * all. Covering the table in green would make every column look like advice.
 */

import { cn } from "@/lib/utils";
import type { ScreenRow } from "@/data/screens";

import { NULL_CELL, formatChange, formatMetric, formatPrice, isMissing } from "./format";
import { COMPARE_GROUPS, bestValue, isBest, type Metric } from "./metrics";
import type { CompareCompany } from "./useCompare";

/** One column's width at desktop; the sticky label column is a little narrower. */
const COLUMN_WIDTH = "w-[10.5rem] min-w-[10.5rem]";
const LABEL_WIDTH = "w-[9.5rem] min-w-[9.5rem]";
/** Under 900 px the company columns tighten instead of stacking. */
const NARROW_COLUMN = "max-[900px]:w-[8.5rem] max-[900px]:min-w-[8.5rem]";
const NARROW_LABEL = "max-[900px]:w-[8rem] max-[900px]:min-w-[8rem]";
/** The pinned label's own surface, so numbers scroll behind it, not through it. */
const PINNED = "sticky left-0 z-10 bg-surface";

function companyName(row: ScreenRow | null): string {
  if (!row) return NULL_CELL;
  const value = row["company"];
  return typeof value === "string" && value.trim().length > 0 ? value : NULL_CELL;
}

function cellValue(row: ScreenRow | null, key: string): unknown {
  return row ? row[key] : null;
}

function Quote({ row }: { readonly row: ScreenRow | null }) {
  const price = formatPrice(cellValue(row, "price"));
  const change = cellValue(row, "change");
  const direction = isMissing(change) ? 0 : Math.sign(Number(change));

  return (
    <span className="mt-0.5 flex items-baseline gap-1.5">
      <span className={cn("nums text-data", row ? "text-fg" : "text-fg-subtle")}>{price}</span>
      <span
        className={cn(
          "nums text-data",
          direction > 0 ? "text-up" : direction < 0 ? "text-down" : "text-fg-subtle",
        )}
      >
        {formatChange(change)}
      </span>
    </span>
  );
}

function HeaderCell({
  company,
  onRemove,
}: {
  readonly company: CompareCompany;
  readonly onRemove: (symbol: string) => void;
}) {
  return (
    <th
      scope="col"
      data-testid="compare-col"
      data-symbol={company.symbol}
      className={cn(
        "border-l border-line align-bottom",
        COLUMN_WIDTH,
        NARROW_COLUMN,
        "max-[900px]:px-1.5",
      )}
    >
      <button
        type="button"
        data-testid={`compare-remove-${company.symbol}`}
        aria-label={`Remove ${company.symbol} from the comparison`}
        title={`Remove ${company.symbol} from the comparison`}
        onClick={() => onRemove(company.symbol)}
        className="group relative flex w-full flex-col items-start gap-0.5 rounded-sm px-2 py-1.5 text-left transition-colors hover:bg-row-hover focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-focus"
      >
        <span className="flex w-full items-baseline justify-between gap-1">
          <span className="font-mono text-data font-semibold tracking-[0.02em] text-fg">
            {company.symbol}
          </span>
          {/*
            Hover is a desktop luxury; the glyph is always visible below 900 px,
            where there is no hover to reveal it. The accessible name is the
            button's, not this span's.
          */}
          <span
            aria-hidden="true"
            className="text-fg-subtle opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100 max-[900px]:opacity-100"
          >
            ×
          </span>
        </span>
        <span className="w-full truncate text-label text-fg-muted">{companyName(company.row)}</span>
        <Quote row={company.row} />
      </button>
    </th>
  );
}

function MetricRow({
  metric,
  companies,
}: {
  readonly metric: Metric;
  readonly companies: readonly CompareCompany[];
}) {
  const best = bestValue(
    companies.map((company) => cellValue(company.row, metric.key)),
    metric.direction,
    metric.positiveOnly,
  );

  return (
    <tr data-testid={`compare-row-${metric.key}`} className="border-t border-line/60">
      <th
        scope="row"
        className={cn(
          "px-2 py-1 text-left text-data font-normal text-fg-muted",
          PINNED,
          LABEL_WIDTH,
          NARROW_LABEL,
          "max-[900px]:px-1.5",
        )}
      >
        {metric.label}
      </th>
      {companies.map((company) => {
        const value = cellValue(company.row, metric.key);
        const absent = isMissing(value);
        const winner = isBest(value, best);
        return (
          <td
            key={company.symbol}
            data-testid={`compare-cell-${metric.key}-${company.symbol}`}
            data-best={winner ? "true" : undefined}
            className={cn(
              // `relative` is load-bearing: the sr-only "best" marker is
              // absolutely positioned, and without a positioned cell it is laid
              // out against the initial containing block — at the far right of
              // a wide table it widened the whole document instead of the cell.
              "relative nums border-l border-line/60 px-2 py-1 text-right",
              absent ? "text-fg-subtle" : "text-fg",
              winner && "bg-accent-soft/60 font-medium",
            )}
          >
            {formatMetric(value, metric.unit, metric.compact)}
            {winner ? <span className="sr-only"> (best)</span> : null}
          </td>
        );
      })}
    </tr>
  );
}

export interface CompareTableProps {
  readonly companies: readonly CompareCompany[];
  readonly onRemove: (symbol: string) => void;
}

export function CompareTable({ companies, onRemove }: CompareTableProps) {
  const symbols = companies.map((company) => company.symbol).join(", ");

  return (
    <div
      data-testid="compare-scroll"
      className="overflow-x-auto overscroll-x-contain rounded-md border border-line bg-surface [scrollbar-color:var(--border-strong)_transparent] [scrollbar-width:thin]"
    >
      <table
        data-testid="compare-table"
        className="w-max min-w-full border-collapse text-data"
      >
        <caption className="sr-only">
          {symbols} compared across {COMPARE_GROUPS.length} sections. Metrics are rows, companies
          are columns.
        </caption>
        <thead>
          <tr>
            <th
              scope="col"
              data-testid="compare-metric-head"
              className={cn(
                "z-20 px-2 py-1 text-left align-bottom font-mono text-label uppercase tracking-[0.08em] text-fg-subtle",
                PINNED,
                LABEL_WIDTH,
                NARROW_LABEL,
                "max-[900px]:px-1.5",
              )}
            >
              Metric
            </th>
            {companies.map((company) => (
              <HeaderCell key={company.symbol} company={company} onRemove={onRemove} />
            ))}
          </tr>
        </thead>
        {COMPARE_GROUPS.map((group) => (
          <tbody key={group.id}>
            <tr data-testid={`compare-group-${group.id}`}>
              <th
                scope="colgroup"
                colSpan={companies.length + 1}
                // A group label is a landmark on the left edge: pinned for the
                // same reason the metric names are, so a reader scrolled to the
                // tenth company still knows which section they are in.
                className={cn(
                  "sticky left-0 z-10 border-t border-line bg-surface-raised px-2 pb-1 pt-2 text-left font-mono text-label uppercase tracking-[0.08em] text-fg-subtle",
                  "max-[900px]:px-1.5",
                )}
              >
                {group.label}
              </th>
            </tr>
            {group.metrics.map((metric) => (
              <MetricRow key={metric.key} metric={metric} companies={companies} />
            ))}
          </tbody>
        ))}
      </table>
    </div>
  );
}
