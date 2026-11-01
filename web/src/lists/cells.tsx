/**
 * Cells these two pages need that the grid's `CellValue` does not draw.
 *
 * `CellValue` is a column renderer: it takes a `ColumnSpec`, right-aligns, and
 * assumes a value the screen already measured. These tables are hand-built (five
 * to nine columns, one of which is a pair of buttons), and two of their cells are
 * money with a *sign* — a P&L and a per-share delta — which `CellValue`'s `money`
 * kind prints without a `+`.
 *
 * The rules are the grid's, unchanged:
 *
 * * an absent figure is a muted em dash with a name — never a blank, never a 0;
 * * a signed figure carries its direction in the text (`+$100.00`, `▲ +1.23%`)
 *   and in `data-sign`, so colour is never the only channel;
 * * a figure that rounds to zero claims no direction: the printed text decides
 *   the sign, and `--flat` (the achromatic token) is what a zero is painted in.
 *   A P&L of exactly 0.00 must not look like a gain or a loss.
 */

import { formatMoney, formatNumber, formatSignedPercent, signOf } from "@/grid/format";
import { cn } from "@/lib/utils";

import type { QuoteRow } from "./quotes";
import type { QuotesStatus } from "./useQuotes";

/** How a quote-dependent cell should read, from the request's own state. */
export type QuoteCellState = "priced" | "loading" | "failed" | "missing";

export function quoteCellState(status: QuotesStatus, quote: QuoteRow | null): QuoteCellState {
  if (status === "error") return "failed";
  if (status === "ready") return quote === null ? "missing" : "priced";
  return "loading";
}

export function Absent({ title = "Not available" }: { readonly title?: string }) {
  return (
    <span className="text-fg-subtle" title={title} aria-label={title}>
      —
    </span>
  );
}

function Pending() {
  return (
    <span className="text-fg-subtle" aria-busy="true" title="Loading quote">
      …
    </span>
  );
}

/** The one word a row says when the market has no price for it. */
export function Unavailable() {
  return (
    <span className="text-fg-subtle" title="No quote for this symbol">
      Unavailable
    </span>
  );
}

/**
 * The last price.
 *
 * A quote in a currency that is not the dollar is not printed with a dollar
 * sign: `formatNumber` plus the code is the honest rendering, and the title
 * carries the same. Everything the product actually quotes is USD, which goes
 * through `formatMoney`.
 */
export function priceText(quote: QuoteRow): string {
  if (quote.currency !== null && quote.currency !== "USD") {
    return `${formatNumber(quote.price)} ${quote.currency}`;
  }
  return formatMoney(quote.price);
}

export function LastPrice({
  state,
  quote,
}: {
  readonly state: QuoteCellState;
  readonly quote: QuoteRow | null;
}) {
  if (state === "loading") return <Pending />;
  if (state === "failed") return <Absent title="Quotes did not load" />;
  if (state === "missing" || quote === null) return <Unavailable />;
  return (
    <span className="nums text-fg" title={priceText(quote)}>
      {priceText(quote)}
    </span>
  );
}

/** Money the reader entered or the market produced; `null` is an em dash. */
export function Money({
  value,
  state = "priced",
}: {
  readonly value: number | null;
  readonly state?: QuoteCellState;
}) {
  if (state === "loading") return <Pending />;
  if (state === "failed") return <Absent title="Quotes did not load" />;
  if (state === "missing" || value === null) return <Absent />;
  return <span className="nums text-fg">{formatMoney(value)}</span>;
}

/** A signed amount: `+$100.00`, `-$25.15`, `$0.00` when it is exactly zero. */
export function PnlMoney({
  value,
  state = "priced",
}: {
  readonly value: number | null;
  readonly state?: QuoteCellState;
}) {
  if (state === "loading") return <Pending />;
  if (state === "failed") return <Absent title="Quotes did not load" />;
  if (state === "missing" || value === null) return <Absent />;

  const sign = signOf(value);
  return (
    <span
      data-sign={sign}
      className={cn(
        "nums",
        sign === 1 ? "text-up" : sign === -1 ? "text-down" : "text-flat",
      )}
    >
      {sign === 1 ? `+${formatMoney(value)}` : formatMoney(value)}
    </span>
  );
}

/** A signed percentage, with the arrow and the spoken word the grid uses. */
export function Delta({
  value,
  state = "priced",
}: {
  readonly value: number | null;
  readonly state?: QuoteCellState;
}) {
  if (state === "loading") return <Pending />;
  if (state === "failed") return <Absent title="Quotes did not load" />;
  if (state === "missing" || value === null) return <Absent />;

  const text = formatSignedPercent(value);
  const sign = text.startsWith("+") ? 1 : text.startsWith("-") ? -1 : 0;
  return (
    <span
      data-sign={sign}
      title={text}
      className={cn(
        "nums",
        sign === 1 ? "text-up" : sign === -1 ? "text-down" : "text-flat",
      )}
    >
      <span aria-hidden="true" className="mr-0.5 text-[0.75em] leading-none">
        {sign === 1 ? "▲" : sign === -1 ? "▼" : "–"}
      </span>
      <span className="sr-only">
        {sign === 1 ? "up" : sign === -1 ? "down" : "unchanged"}{" "}
      </span>
      {text}
    </span>
  );
}

/** A signed percentage without the arrow: `+3.74%`, `-2.51%`, `0.00%`. */
export function PnlPercent({
  value,
  state = "priced",
}: {
  readonly value: number | null;
  readonly state?: QuoteCellState;
}) {
  if (state === "loading") return <Pending />;
  if (state === "failed") return <Absent title="Quotes did not load" />;
  if (state === "missing" || value === null) return <Absent />;

  const text = formatSignedPercent(value);
  const sign = text.startsWith("+") ? 1 : text.startsWith("-") ? -1 : 0;
  return (
    <span
      data-sign={sign}
      className={cn(
        "nums",
        sign === 1 ? "text-up" : sign === -1 ? "text-down" : "text-flat",
      )}
    >
      {text}
    </span>
  );
}

/**
 * A share count. An integer prints without decimals; a fractional lot prints
 * the digits it has (`2.5`, not `2.5000`), because a quantity is what the reader
 * typed and trailing zeros would claim a precision they did not give.
 */
export function Quantity({ value }: { readonly value: number }) {
  const text = Number.isInteger(value)
    ? formatNumber(value, 0)
    : formatNumber(value, 4).replace(/0+$/, "").replace(/\.$/, "");
  return <span className="nums text-fg-muted">{text}</span>;
}

/** The request failed: say so once, above the table, with a way to try again. */
export function QuotesFailure({
  error,
  onRetry,
}: {
  readonly error: string | null;
  readonly onRetry: () => void;
}) {
  return (
    <div
      role="alert"
      data-testid="quotes-error"
      className="mb-1.5 flex items-center justify-between gap-3 rounded-md border border-line border-l-2 border-l-alert bg-surface px-3 py-1.5"
    >
      <p className="min-w-0 truncate text-data text-fg">Quotes did not load. {error}</p>
      <button
        type="button"
        onClick={onRetry}
        className="focus-ring shrink-0 rounded-sm border border-line px-2 py-0.5 text-data text-fg-muted transition-colors hover:border-line-strong hover:text-fg"
      >
        Retry
      </button>
    </div>
  );
}
