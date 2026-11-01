/**
 * Presentation metadata for the catalogue's column keys.
 *
 * The **list** of columns in a view is never defined here — it arrives from
 * `GET /screens/select/schema` (`views[].columns`). This file only says how a
 * key should look once the server names it: a human label, a short label for a
 * narrow header, a numeric kind, a width weight and a minimum width.
 *
 * The split matters. Adding a column to a view is a server change; the grid
 * picks it up and renders it with the fallback below. Renaming a label is a
 * client change and cannot make a column disappear.
 */

import { exchangeLabel } from "@/lib/exchanges";

export type ColumnKind =
  | "money"
  | "compactMoney"
  | "number"
  | "delta"
  | "percent"
  | "integer"
  | "text"
  | "date"
  | "timestamp"
  | "badge";

export interface ColumnSpec {
  key: string;
  label: string;
  /** Used when the full label cannot fit the column at 10 px uppercase. */
  short?: string;
  kind: ColumnKind;
  /** Share of the free width after every column has its minimum. */
  weight: number;
  /** The floor the solver may not go below (at 1280 px). */
  min: number;
  /** Decimal places for number/percent kinds. */
  precision?: number;
  /** A unit suffix such as `x` (relative volume). */
  suffix?: string;
  /**
   * Display-only text for a raw value.
   *
   * Never changes the value that is sent, the value a cell carries in
   * `data-value`, or the value a filter matches on: `format.ts` applies it and
   * puts the raw value back into the cell's title as `LABEL (RAW)`. The data
   * lives in a leaf module (`lib/exchanges.ts`), not here, because the detail
   * chunk reads it too and must not pull this 12 KB table in to do it.
   */
  display?: (value: string) => string;
}

/** The pinned identity lane. 101 px is the measured Finviz value. */
export const IDENTITY_WIDTH = 101;

/**
 * The row-number column: `1..n` for the page, drawn by the client.
 *
 * It is not a catalogue key — the server has no per-row ordinal and would
 * refuse `no` in a `columns` list — so every request builder removes it and
 * every renderer derives it from the page offset. It is spelled here, once,
 * because the column picker, the solver and the table all have to agree that
 * it exists.
 */
export const ROW_NUMBER_KEY = "no";

/** Columns the client draws but never asks the API for. */
export const CLIENT_COLUMNS: readonly string[] = [ROW_NUMBER_KEY];

/** Is this key drawn from the page's own geometry rather than a row cell? */
export function isClientColumn(key: string): boolean {
  return CLIENT_COLUMNS.includes(key);
}

type Meta = Omit<ColumnSpec, "key">;

/**
 * The money floor is 96 px, not 76: `$823,210.89` — a real target price — needs
 * 76 px of text at 12 px, and a cell spends 8 px of padding on each side, so a
 * 76 px column had 60 px for the number and cut it (`$823,21…`, a 1000×
 * misread). 76 + 16 = 92, and 96 leaves the rounding a little room.
 */
const MONEY = { kind: "money" as const, weight: 1, min: 96 };
const COMPACT = { kind: "compactMoney" as const, weight: 1.1, min: 84 };
const RATIO = { kind: "number" as const, weight: 1, min: 64, precision: 2 };
const PCT = { kind: "percent" as const, weight: 1, min: 72, precision: 2 };
const DELTA = { kind: "delta" as const, weight: 1, min: 74, precision: 2 };
const TEXT = { kind: "text" as const, weight: 1.5, min: 96 };

/**
 * One entry per catalogue key. Order is irrelevant; the view supplies order.
 * `short` is mandatory wherever the full label is wider than the minimum
 * column, which a unit test enforces — a header that ellipsises is a header
 * that can mean something else.
 */
export const COLUMN_META: Record<string, Meta> = {
  // The pinned lanes' own headers. Neither is a catalogue key: the solver
  // prepends them, and `no` is derived from the page offset.
  [ROW_NUMBER_KEY]: { label: "No", kind: "integer", weight: 0, min: 40 },
  identity: { label: "Ticker", kind: "text", weight: 0, min: IDENTITY_WIDTH },

  // today ---------------------------------------------------------------
  price: { label: "Price", ...MONEY },
  change: { label: "Change %", short: "Change", ...DELTA, min: 70, weight: 0.9 },
  change_from_open: { label: "Change from Open", short: "Chg Open", ...DELTA, min: 84, weight: 0.95 },
  gap: { label: "Gap", ...DELTA, min: 66, weight: 0.85 },
  prev_close: { label: "Prev Close", ...MONEY, min: 100 },
  open: { label: "Open", ...MONEY, min: 68 },
  high: { label: "High", ...MONEY, min: 68 },
  low: { label: "Low", ...MONEY, min: 68 },
  volume: { label: "Volume", kind: "integer", weight: 1.05, min: 78 },
  average_volume: { label: "Average Volume", short: "Avg Vol", kind: "integer", weight: 1.1, min: 86 },
  relative_volume: { label: "Relative Volume", short: "Rel Vol", kind: "number", precision: 2, suffix: "x", weight: 1, min: 80 },

  // value ---------------------------------------------------------------
  market_cap: { label: "Market Cap", short: "Mkt Cap", ...COMPACT, weight: 1.15 },
  enterprise_value: { label: "Enterprise Value", short: "EV", ...COMPACT, weight: 1 },
  pe: { label: "P/E", ...RATIO, min: 62 },
  forward_pe: { label: "Forward P/E", short: "Fwd P/E", ...RATIO, min: 74 },
  peg: { label: "PEG", ...RATIO, min: 60 },
  ps: { label: "P/S", ...RATIO, min: 58 },
  pb: { label: "P/B", ...RATIO, min: 58 },
  pc: { label: "P/C", ...RATIO, min: 58 },
  pfcf: { label: "P/FCF", ...RATIO, min: 64 },
  ev_ebitda: { label: "EV/EBITDA", ...RATIO, min: 92 },
  ev_sales: { label: "EV/Sales", short: "EV/S", ...RATIO, min: 72 },
  target_price: { label: "Target Price", short: "Target", ...MONEY },
  recommendation: { label: "Recommendation", short: "Rec", ...RATIO, min: 84 },

  // quality -------------------------------------------------------------
  roa: { label: "ROA", ...PCT, min: 64 },
  roe: { label: "ROE", ...PCT, min: 64 },
  roic: { label: "ROIC", ...PCT, min: 66 },
  current_ratio: { label: "Current Ratio", short: "Current", ...RATIO, min: 80 },
  quick_ratio: { label: "Quick Ratio", short: "Quick", ...RATIO, min: 78 },
  lt_debt_to_equity: { label: "LT Debt/Equity", short: "LT D/E", ...RATIO, min: 92 },
  debt_to_equity: { label: "Debt/Equity", short: "D/E", ...RATIO, min: 84 },
  book_per_share: { label: "Book/Share", short: "Book/sh", ...MONEY, min: 84 },
  cash_per_share: { label: "Cash/Share", short: "Cash/sh", ...MONEY, min: 84 },
  gross_margin: { label: "Gross Margin", short: "Gross M", ...PCT, min: 84 },
  operating_margin: { label: "Operating Margin", short: "Op Margin", ...PCT, min: 90 },
  net_margin: { label: "Net Margin", short: "Net M", ...PCT, min: 84 },

  // growth --------------------------------------------------------------
  // A money column narrower than 78 px (`$101.91` = 50 px of text + 16 px of
  // padding) prints a three-digit EPS as `$10…`, so both floors are 78.
  eps: { label: "EPS", ...MONEY, min: 78 },
  net_income: { label: "Net Income", short: "Net Inc", ...COMPACT },
  sales: { label: "Sales", ...COMPACT, min: 80 },
  eps_next_q: { label: "EPS Next Q", short: "EPS Q+1", ...MONEY, min: 78 },
  eps_this_y: { label: "EPS This Year", short: "EPS this Y", ...MONEY, min: 100 },
  eps_next_y: { label: "EPS Next Year", short: "EPS next Y", ...MONEY, min: 100 },
  eps_past_3y: { label: "EPS Past 3Y", short: "EPS 3Y", ...PCT, min: 76 },
  eps_past_5y: { label: "EPS Past 5Y", short: "EPS 5Y", ...PCT, min: 76 },
  eps_qoq: { label: "EPS Q/Q", ...PCT, min: 76 },
  sales_qoq: { label: "Sales Q/Q", ...PCT, min: 92 },
  eps_yoy_ttm: { label: "EPS YoY TTM", short: "EPS TTM", ...PCT, min: 80 },
  sales_yoy_ttm: { label: "Sales YoY TTM", short: "Sales TTM", ...PCT, min: 92 },

  // momentum ------------------------------------------------------------
  perf_1w: { label: "Perf Week", short: "1 Week", ...DELTA },
  perf_4w: { label: "Perf Month", short: "1 Month", ...DELTA, min: 78 },
  perf_13w: { label: "Perf Quarter", short: "3 Month", ...DELTA, min: 80 },
  perf_26w: { label: "Perf Half Y", short: "6 Month", ...DELTA, min: 80 },
  perf_52w: { label: "Perf Year", short: "1 Year", ...DELTA, min: 76 },
  perf_ytd: { label: "Perf YTD", short: "YTD", ...DELTA, min: 70 },
  perf_3y: { label: "Perf 3Y", ...DELTA, min: 76 },
  perf_5y: { label: "Perf 5Y", ...DELTA, min: 76 },
  perf_10y: { label: "Perf 10Y", ...DELTA, min: 84 },
  rsi: { label: "RSI (14)", short: "RSI", kind: "number", precision: 1, weight: 0.95, min: 64 },

  // trend ---------------------------------------------------------------
  sma20: { label: "SMA 20", ...MONEY, min: 70 },
  sma50: { label: "SMA 50", ...MONEY, min: 70 },
  sma200: { label: "SMA 200", ...MONEY, min: 76 },
  high_50d: { label: "50D High", ...MONEY, min: 84 },
  low_50d: { label: "50D Low", ...MONEY, min: 84 },
  high_52w: { label: "52W High", ...MONEY, min: 84 },
  low_52w: { label: "52W Low", ...MONEY, min: 84 },
  atr: { label: "ATR", ...MONEY, min: 78 },
  beta: { label: "Beta", kind: "number", precision: 2, weight: 0.9, min: 60 },
  volatility_1w: { label: "Volatility W", short: "Vol W", ...PCT, min: 78 },
  volatility_4w: { label: "Volatility M", short: "Vol M", ...PCT, min: 78 },
  all_time_high: { label: "All-Time High", short: "ATH", ...MONEY, min: 84 },

  // ownership -----------------------------------------------------------
  shares_outstanding: { label: "Shares Outstanding", short: "Shares Out", kind: "integer", weight: 1.1, min: 100 },
  shares_float: { label: "Shares Float", short: "Float", kind: "integer", weight: 1, min: 84 },
  float_pct: { label: "Float %", ...PCT, min: 76 },
  insider_own: { label: "Insider Own", short: "Insider", ...PCT, min: 80 },
  insider_trans: { label: "Insider Trans", short: "Ins Trans", ...DELTA, min: 92 },
  inst_own: { label: "Inst Own", short: "Inst", ...PCT, min: 74 },
  inst_trans: { label: "Institutional Trans", short: "Inst Trans", ...DELTA, min: 100 },
  short_float: { label: "Short Float", short: "Short Flt", ...PCT, min: 92 },
  short_ratio: { label: "Short Ratio", short: "Short Rat", ...RATIO, min: 92 },
  short_interest: { label: "Short Interest", short: "Short Int", kind: "integer", weight: 1, min: 90 },

  // income --------------------------------------------------------------
  dividend: { label: "Dividend", ...MONEY, min: 84 },
  dividend_yield: { label: "Dividend Yield", short: "Div Yield", ...PCT, min: 92 },
  dividend_ttm: { label: "Dividend TTM", short: "Div TTM", ...MONEY, min: 82 },
  payout_ratio: { label: "Payout Ratio", short: "Payout", ...PCT, min: 80 },
  dividend_ex_date: { label: "Ex-Dividend Date", short: "Ex-Date", kind: "date", weight: 1, min: 86 },
  dividend_growth_1y: { label: "Dividend Growth 1Y", short: "Div 1Y", ...PCT, min: 86 },
  dividend_growth_3y: { label: "Dividend Growth 3Y", short: "Div 3Y", ...PCT, min: 86 },
  dividend_growth_5y: { label: "Dividend Growth 5Y", short: "Div 5Y", ...PCT, min: 86 },

  // profile -------------------------------------------------------------
  company: { label: "Company", kind: "text", weight: 2.2, min: 120 },
  sector: { label: "Sector", kind: "text", weight: 1.5, min: 100 },
  industry: { label: "Industry", kind: "text", weight: 1.6, min: 110 },
  security_type: { label: "Security Type", short: "Type", ...TEXT, min: 84 },
  country: { label: "Country", ...TEXT, min: 84 },
  exchange: { label: "Exchange", ...TEXT, min: 104, display: exchangeLabel },
  index_membership: { label: "Index Membership", short: "Index", kind: "text", weight: 1.5, min: 100 },
  optionable: { label: "Optionable", short: "Options", kind: "badge", weight: 0.95, min: 76 },
  shortable: { label: "Shortable", kind: "badge", weight: 0.95, min: 92 },
  employees: { label: "Employees", kind: "integer", weight: 1, min: 92 },
  earnings_date: { label: "Earnings Date", short: "Earnings", kind: "timestamp", weight: 1, min: 86 },
  ipo_date: { label: "IPO Date", short: "IPO", kind: "date", weight: 1, min: 76 },
};

/** Measured 10 px uppercase mono with 0.08em tracking: ~8.2 px per glyph. */
export const HEADER_CHAR_PX = 8.2;
/** The cell's horizontal padding, both sides (8 px each). */
const HEADER_PADDING = 16;

export function headerNeed(spec: ColumnSpec): number {
  return Math.ceil(spec.label.length * HEADER_CHAR_PX) + HEADER_PADDING;
}

/**
 * The label to paint at `width`, and whether it is the short one.
 *
 * This is the Finviz `data-label-short` rule: a label that cannot fit is
 * replaced by its declared short form *before* CSS gets a chance to ellipsise
 * it into a different meaning. When no short form exists the full label is
 * kept and exposed via `title` (never silently cut without a way to read it).
 */
export function labelFor(spec: ColumnSpec, width: number): { text: string; short: boolean } {
  if (spec.short && width < headerNeed(spec)) return { text: spec.short, short: true };
  return { text: spec.label, short: false };
}

function humanize(key: string): string {
  const words = key.replace(/[_-]+/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/**
 * A spec for any key, including one this build has never heard of. An unknown
 * column renders with a humanised label and a neutral numeric width rather than
 * vanishing: the server owns the column list, so a new key must still appear.
 */
export function columnSpec(key: string): ColumnSpec {
  const known = COLUMN_META[key];
  if (known) return { key, ...known };
  return {
    key,
    label: humanize(key),
    kind: "text",
    weight: 1,
    min: 88,
  };
}

