/**
 * Cell renderers. One component per kind, one rule per component:
 *
 * * numbers are right aligned and tabular (`nums`), so decimal points line up
 *   down a 100-row panel;
 * * a delta carries its direction three ways — arrow glyph, explicit sign and
 *   colour — because colour alone is not information;
 * * an absent value is a muted em dash with an accessible name, never a blank
 *   cell and never a zero.
 *
 * The identity lane is its own renderer: a logo (or a monogram when the row
 * carries no mark) plus the symbol. A long company name is exposed as the
 * title and to screen readers, never as layout inside a 101 px lane.
 */

import { cn } from "@/lib/utils";

import type { ColumnSpec } from "./columnMeta";
import { formatCell } from "./format";

export interface CellValueProps {
  spec: ColumnSpec;
  value: unknown;
}

const NUMERIC: ReadonlySet<ColumnSpec["kind"]> = new Set([
  "money",
  "compactMoney",
  "number",
  "delta",
  "percent",
  "integer",
]);

export function CellValue({ spec, value }: CellValueProps) {
  const cell = formatCell(value, spec);

  if (cell.absent) {
    return (
      <span
        className="block text-right text-fg-subtle"
        data-null="true"
        aria-label="Not available"
        title="Not available"
      >
        {cell.text}
      </span>
    );
  }

  if (spec.kind === "delta") {
    const tone =
      cell.sign === 1 ? "text-up" : cell.sign === -1 ? "text-down" : "text-fg-muted";
    const arrow = cell.sign === 1 ? "▲" : cell.sign === -1 ? "▼" : "–";
    const spoken = cell.sign === 1 ? "up" : cell.sign === -1 ? "down" : "unchanged";
    return (
      <span
        className={cn("flex items-baseline justify-end gap-1 nums", tone)}
        data-sign={cell.sign ?? 0}
        title={cell.title}
      >
        <span aria-hidden="true" className="text-[0.75em] leading-none">
          {arrow}
        </span>
        <span className="sr-only">{spoken} </span>
        <span>{cell.text}</span>
      </span>
    );
  }

  if (spec.kind === "badge") {
    const yes = String(value).toLowerCase() === "yes";
    return (
      <span
        className={cn(
          "inline-flex max-w-full items-center truncate rounded-sm border px-1 text-label uppercase tracking-[0.06em]",
          yes
            ? "border-line-strong text-fg-muted"
            : "border-line text-fg-subtle",
        )}
        data-badge={yes ? "yes" : "no"}
        title={cell.title}
      >
        {cell.text}
      </span>
    );
  }

  if (NUMERIC.has(spec.kind)) {
    return (
      <span className="block truncate text-right nums" title={cell.title} data-kind={spec.kind}>
        {cell.text}
      </span>
    );
  }

  return (
    <span
      className="block truncate text-left"
      title={cell.title}
      data-kind={spec.kind}
      dir="auto"
    >
      {cell.text}
    </span>
  );
}

export interface IdentityCellProps {
  symbol: string;
  company?: string | null;
  logo?: string | null;
}

/**
 * The pinned lane's contents. `min-w-0` on the flex row and `truncate` on the
 * symbol keep a pathological symbol from pushing the lane past its column;
 * the company name never enters the flow at all.
 */
export function IdentityCell({ symbol, company, logo }: IdentityCellProps) {
  const title = company ? `${symbol} — ${company}` : symbol;
  const initial = symbol.slice(0, 1).toUpperCase();

  return (
    <span className="flex min-w-0 items-center gap-1.5" title={title}>
      {logo ? (
        <img
          src={logo}
          alt=""
          width={16}
          height={16}
          loading="lazy"
          className="size-4 shrink-0 rounded-[2px] object-contain"
        />
      ) : (
        <span
          aria-hidden="true"
          className="flex size-4 shrink-0 items-center justify-center rounded-[2px] border border-line bg-surface-raised font-mono text-[9px] leading-none text-fg-muted"
        >
          {initial}
        </span>
      )}
      <span className="min-w-0 truncate font-mono text-data font-medium text-fg">
        {symbol}
      </span>
      {company ? <span className="sr-only">, {company}</span> : null}
    </span>
  );
}
