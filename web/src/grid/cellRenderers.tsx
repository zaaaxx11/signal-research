/**
 * Cell renderers. One component per kind, one rule per component:
 *
 * * numbers are right aligned and tabular (`nums`), so decimal points line up
 *   down a panel of rows;
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
import { titleVar } from "@/components/SectionTitle";

import type { ColumnSpec } from "./columnMeta";
import { formatCell } from "./format";
import { LogoMark } from "./LogoMark";

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

/**
 * The palette slot for an arbitrary constant string, hashed — never invented.
 *
 * WHAT: a stable 1–8 variant for any string key (a sector, a filter key).
 * WHY: the grid's rule is that a colour must come from a token; the eight
 *      title fills are the spare palette the product already keeps, so a
 *      swatch is `--title-N` picked by a hash of the name — no new token, and
 *      the same label is the same colour everywhere (GICS sectors repeat
 *      across every ETF, country and list page; the chip bar shares it).
 */
export function swatchVariant(label: string): 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 {
  let hash = 0;
  for (const char of label.trim().toLowerCase()) {
    hash = (hash * 31 + (char.codePointAt(0) ?? 0)) >>> 0;
  }
  return (((hash % 8) + 1) as 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8);
}

/** The token a hashed swatch paints with (`var(--title-N)`). */
export function swatchColor(label: string): string {
  return titleVar(swatchVariant(label));
}

/** The sector lane's swatch is the hashed palette slot of the sector name. */
export const sectorSwatchColor = swatchColor;

/**
 * The datum token an expense ratio prints in, by the fee bands the ETF deck
 * reads: under 0.10% is cheap (up), 0.10–0.50% is ordinary (alert), above
 * 0.50% is expensive (down). The value still carries the band in its title —
 * colour is never the only channel.
 */
export function expenseRatioTone(ratio: number): "text-up" | "text-alert" | "text-down" {
  if (ratio < 0.1) return "text-up";
  if (ratio > 0.5) return "text-down";
  return "text-alert";
}

function asNumber(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string") {
    const parsed = Number(value.replace(/[%,\s]/g, ""));
    if (Number.isFinite(parsed) && value.trim() !== "") return parsed;
  }
  return null;
}

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

  // The sector lane carries a small solid swatch in the sector's hashed
  // title-palette colour, so a page of mixed-sector rows can be scanned by
  // colour without any text change (the value itself is unchanged in `title`
  // and in any copy). Colour is decorative here — the words are the data.
  if (spec.key === "sector" && typeof value === "string" && value.trim().length > 0) {
    return (
      <span
        className="flex min-w-0 items-center gap-1.5 text-left"
        title={cell.title}
        data-kind={spec.kind}
        dir="auto"
      >
        <span
          aria-hidden="true"
          data-sector-swatch={value}
          className="size-1.5 shrink-0 rounded-[1px]"
          style={{ background: sectorSwatchColor(value) }}
        />
        <span className="min-w-0 truncate">{cell.text}</span>
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
    // The ETF fee bands: the expense-ratio column prints in the band's token
    // colour (up / alert / down) while keeping its numeric form and title.
    const expense = spec.key === "expense_ratio" ? asNumber(value) : null;
    return (
      <span
        className={cn(
          "block truncate text-right nums",
          expense !== null && expenseRatioTone(expense),
        )}
        title={cell.title}
        data-kind={spec.kind}
      >
        {cell.text}
      </span>
    );
  }

  // A column that prints a *label* for a raw value keeps the value in the DOM:
  // `data-value` is the code the API filters on, so a copy, a test or a future
  // export can recover it without a second visible column. Columns with no
  // `display` formatter get no attribute at all, so their markup is unchanged.
  const rawValue = spec.display ? String(value) : undefined;

  return (
    <span
      className="block truncate text-left"
      title={cell.title}
      data-kind={spec.kind}
      data-value={rawValue}
      dir="auto"
    >
      {cell.text}
    </span>
  );
}

export interface IdentityCellProps {
  symbol: string;
  company?: string | null;
}

/**
 * The pinned lane's contents: the brand mark, then the symbol.
 *
 * The mark comes from `/logos/{SYMBOL}` as an image — the row payload never
 * carries it (see `logo.ts`). `min-w-0` on the flex row and `truncate` on the
 * symbol keep a pathological symbol from pushing the lane past its column; the
 * company name never enters the flow at all.
 */
export function IdentityCell({ symbol, company }: IdentityCellProps) {
  const title = company ? `${symbol} — ${company}` : symbol;

  return (
    <span className="flex min-w-0 items-center gap-1.5" title={title}>
      <LogoMark symbol={symbol} />
      <span className="min-w-0 truncate font-mono text-data font-medium text-fg">
        {symbol}
      </span>
      {company ? <span className="sr-only">, {company}</span> : null}
    </span>
  );
}
