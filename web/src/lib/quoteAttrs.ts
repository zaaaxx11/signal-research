/**
 * The data-quote attribute contract: any element carrying these props makes a
 * ticker a HoverBubble target — one attribute, globally honoured, no prop
 * drilling through every page's components.
 *
 * The value is JSON-encoded and HTML-attribute-safe (React escapes it), and a
 * missing field is simply absent: the bubble renders what exists, which is why
 * the calendar can quote an earnings date that has no price yet.
 */
export interface QuoteInfo {
  /** The ticket a chart can be drawn for: BTC-USD, ES=F, AAPL. */
  readonly symbol: string;
  readonly name?: string;
  readonly price?: number | null;
  /** Percent, already multiplied: 0.26 is +0.26%. */
  readonly changePct?: number | null;
  /** Which market: bubbles special-case only styling, not fetching. */
  readonly kind?: "stock" | "etf" | "crypto" | "futures" | "forex";
  /** Extra line the calling page has that the chart does not — calendar's cap. */
  readonly note?: string;
}

export const QUOTE_ATTR = "data-quote";

export function quoteProps(info: QuoteInfo): { [QUOTE_ATTR]: string } {
  return { [QUOTE_ATTR]: JSON.stringify(info) } as { [QUOTE_ATTR]: string };
}

export function parseQuoteAttr(value: string | null): QuoteInfo | null {
  if (!value) return null;
  try {
    const parsed = JSON.parse(value) as unknown;
    if (typeof parsed !== "object" || parsed === null) return null;
    const info = parsed as Record<string, unknown>;
    if (typeof info.symbol !== "string" || info.symbol.length === 0) return null;
    return parsed as QuoteInfo;
  } catch {
    return null;
  }
}
