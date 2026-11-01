/**
 * Portfolio arithmetic, in cents.
 *
 * The rules, and why each is a test rather than a comment:
 *
 * * **Both sides of a row are rounded to the cent before they are compared.**
 *   A price is quoted in cents; 2.5 shares × 400.10 is 1000.25, and a portfolio
 *   that carries `1000.2499999999998` will eventually print a P&L that is off by
 *   a cent.
 * * **A row without a quote has no market value and no P&L** — `null`, not 0.
 *   That is the difference between "we do not know" and "it is worth nothing".
 * * **The total sums only rows that have both**, and reports `covered`/`total` so
 *   the page can say "2 of 3 priced". Counting an unpriced holding's cost basis
 *   but not its value would manufacture a loss out of a missing answer.
 * * **A zero cost basis has no percentage.** Dividing by it is not a 0% return;
 *   it is undefined, and the cell prints an em dash.
 */

import type { Holding } from "./storage";
import type { QuoteRow } from "./quotes";

export interface HoldingPnl {
  readonly holding: Holding;
  /** The quote the numbers came from; `null` when the symbol is unpriced. */
  readonly quote: QuoteRow | null;
  readonly marketValue: number | null;
  /** Always known: it comes from what the reader typed, not from the market. */
  readonly costBasis: number;
  readonly pnl: number | null;
  readonly pnlPct: number | null;
}

/**
 * Round to the cent, half away from zero.
 *
 * `value * 100` is not exact: 1.005 becomes 100.49999999999999 and would round
 * *down*, losing a cent on a value that is exactly half. Nudging by the number's
 * own ulp (not by a fixed 0.5, which would move every number) puts the true tie
 * back on the tie before rounding.
 */
export function toCents(value: number): number {
  const scaled = value * 100;
  const nudged = scaled + Math.sign(scaled) * Number.EPSILON * Math.abs(scaled);
  return (Math.sign(nudged) * Math.round(Math.abs(nudged))) / 100;
}

export function priceHolding(holding: Holding, quote: QuoteRow | null): HoldingPnl {
  const costBasis = toCents(holding.quantity * holding.cost);
  if (quote === null || !Number.isFinite(quote.price)) {
    return { holding, quote: null, marketValue: null, costBasis, pnl: null, pnlPct: null };
  }
  const marketValue = toCents(holding.quantity * quote.price);
  const pnl = toCents(marketValue - costBasis);
  return {
    holding,
    quote,
    marketValue,
    costBasis,
    pnl,
    pnlPct: costBasis === 0 ? null : (pnl / costBasis) * 100,
  };
}

export function priceHoldings(
  holdings: readonly Holding[],
  quotes: ReadonlyMap<string, QuoteRow> | null,
): HoldingPnl[] {
  return holdings.map((holding) => priceHolding(holding, quotes?.get(holding.symbol) ?? null));
}

export interface PortfolioTotal {
  /** Over priced rows only. */
  readonly marketValue: number;
  /** Over priced rows only — never an unpriced holding's basis. */
  readonly costBasis: number;
  readonly pnl: number;
  readonly pnlPct: number | null;
  /** How many rows the total covers, and how many there are. */
  readonly covered: number;
  readonly total: number;
}

export function totalOf(rows: readonly HoldingPnl[]): PortfolioTotal {
  let marketValue = 0;
  let costBasis = 0;
  let pnl = 0;
  let covered = 0;

  for (const row of rows) {
    if (row.marketValue === null || row.pnl === null) continue;
    covered += 1;
    marketValue += row.marketValue;
    costBasis += row.costBasis;
    pnl += row.pnl;
  }

  const value = toCents(marketValue);
  const basis = toCents(costBasis);
  const gain = toCents(pnl);
  return {
    marketValue: value,
    costBasis: basis,
    pnl: gain,
    pnlPct: basis === 0 ? null : (gain / basis) * 100,
    covered,
    total: rows.length,
  };
}
