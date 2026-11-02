/**
 * What the comparison prints, in what order, and which number wins a row.
 *
 * Metrics are rows and companies are columns, so this table *is* the page's
 * structure: seven sections in the order a reader evaluates a company — what it
 * costs, whether it is growing, whether it earns, whether it can pay, who owns
 * it, how it has traded, and where it stands today.
 *
 * Two rules keep the table honest:
 *
 * * **Every key here is a catalogue column.** `POST /screens/select` refuses a
 *   name it does not publish, so the request and the render read one list — a
 *   metric cannot appear on screen that was not asked for, and a requested
 *   column cannot be dropped on the way in.
 * * **"Best" is a stated direction, not a colour.** A cheaper P/E is better; a
 *   bigger market cap is merely bigger. A metric with no direction gets no
 *   marker, which is why the Price section stays unmarked: a share price is not
 *   a score.
 */

import type { MetricUnit } from "./format";

/** Which end of a row is the better answer; `null` means neither. */
export type MetricDirection = "high" | "low" | null;

export interface Metric {
  /** The catalogue column the API is asked for. */
  readonly key: string;
  readonly label: string;
  readonly unit: MetricUnit;
  /** Money printed compactly (`$5.03T`); only meaningful with `usd`. */
  readonly compact?: boolean;
  /** `high`/`low` where better is a direction, `null` where it is not. */
  readonly direction?: MetricDirection;
  /**
   * Only positive values can win a `low` row.
   *
   * A P/E of `0` is a missing number wearing a number's clothes and a negative
   * one means the company lost money — neither is "cheaper" than a real
   * multiple. Debt/equity is not a multiple, and zero debt is a genuine best.
   */
  readonly positiveOnly?: boolean;
}

export interface MetricGroup {
  /** A stable slug for test ids and anchors. */
  readonly id: string;
  readonly label: string;
  readonly metrics: readonly Metric[];
}

/**
 * The API's own ceiling for one selection's `columns` list (40 cells). Keeping
 * the table under it is a design constraint, not a wish: the page prints what
 * it requests, so a 41st row would be a row nobody could fetch.
 */
export const MAX_REQUEST_COLUMNS = 40;

export const COMPARE_GROUPS: readonly MetricGroup[] = [
  {
    id: "valuation",
    label: "Valuation",
    metrics: [
      { key: "market_cap", label: "Market cap", unit: "usd", compact: true },
      { key: "pe", label: "P/E", unit: "ratio", direction: "low", positiveOnly: true },
      { key: "forward_pe", label: "Forward P/E", unit: "ratio", direction: "low", positiveOnly: true },
      { key: "ps", label: "P/S", unit: "ratio", direction: "low", positiveOnly: true },
      { key: "pb", label: "P/B", unit: "ratio", direction: "low", positiveOnly: true },
      { key: "ev_ebitda", label: "EV/EBITDA", unit: "ratio", direction: "low", positiveOnly: true },
    ],
  },
  {
    id: "growth",
    label: "Growth",
    metrics: [
      { key: "eps_qoq", label: "EPS Q/Q", unit: "percent", direction: "high" },
      { key: "sales_qoq", label: "Sales Q/Q", unit: "percent", direction: "high" },
      { key: "eps_yoy_ttm", label: "EPS YoY", unit: "percent", direction: "high" },
      { key: "sales_yoy_ttm", label: "Sales YoY", unit: "percent", direction: "high" },
      { key: "eps_next_y", label: "EPS next Y", unit: "percent", direction: "high" },
    ],
  },
  {
    id: "profitability",
    label: "Profitability",
    metrics: [
      { key: "gross_margin", label: "Gross margin", unit: "percent", direction: "high" },
      { key: "operating_margin", label: "Operating margin", unit: "percent", direction: "high" },
      { key: "net_margin", label: "Net margin", unit: "percent", direction: "high" },
      { key: "roe", label: "ROE", unit: "percent", direction: "high" },
      { key: "roic", label: "ROIC", unit: "percent", direction: "high" },
    ],
  },
  {
    id: "financial-health",
    label: "Financial health",
    metrics: [
      { key: "current_ratio", label: "Current ratio", unit: "ratio", direction: "high" },
      { key: "quick_ratio", label: "Quick ratio", unit: "ratio", direction: "high" },
      { key: "debt_to_equity", label: "Debt/Equity", unit: "ratio", direction: "low" },
      // A payout ratio is a policy, not a score: a young compounder and a
      // mature utility can both be right.
      { key: "payout_ratio", label: "Payout ratio", unit: "percent", direction: null },
    ],
  },
  {
    id: "ownership",
    label: "Ownership",
    metrics: [
      { key: "insider_own", label: "Insider owned", unit: "percent", direction: null },
      { key: "inst_own", label: "Institutional owned", unit: "percent", direction: null },
      { key: "short_float", label: "Short float", unit: "percent", direction: null },
      { key: "short_ratio", label: "Short ratio", unit: "ratio", direction: null },
    ],
  },
  {
    id: "performance",
    label: "Performance",
    metrics: [
      { key: "perf_1w", label: "1 week", unit: "percent", direction: "high" },
      { key: "perf_4w", label: "1 month", unit: "percent", direction: "high" },
      { key: "perf_13w", label: "3 months", unit: "percent", direction: "high" },
      { key: "perf_ytd", label: "Year to date", unit: "percent", direction: "high" },
      { key: "perf_52w", label: "1 year", unit: "percent", direction: "high" },
    ],
  },
  {
    id: "price",
    label: "Price",
    metrics: [
      { key: "price", label: "Price", unit: "usd", direction: null },
      { key: "change", label: "Change", unit: "percent", direction: null },
      { key: "prev_close", label: "Previous close", unit: "usd", direction: null },
      { key: "volume", label: "Volume", unit: "count", direction: null },
      { key: "relative_volume", label: "Relative volume", unit: "ratio", direction: null },
    ],
  },
] as const;

/** Every metric, flattened in reading order. */
export const COMPARE_METRICS: readonly Metric[] = COMPARE_GROUPS.flatMap(
  (group) => group.metrics,
);

/**
 * The request's `columns`: the company name (the header's second line) plus one
 * cell per printed metric, deduplicated, in table order.
 */
export const COMPARE_COLUMNS: readonly string[] = [
  "company",
  ...COMPARE_METRICS.map((metric) => metric.key),
];

/** Key → label, for a renderer that only has a cell's key. */
export function metricLabels(): Map<string, string> {
  return new Map(COMPARE_METRICS.map((metric) => [metric.key, metric.label]));
}

/**
 * The best value in one row, or `null` when the question has no answer.
 *
 * A row with one company, no values, or no direction has no winner: marking a
 * lone column "best" would be a claim about a comparison that did not happen.
 * Absent cells never win, and ties are all kept by {@link isBest} — two
 * companies with the same margin are both the best of that row.
 *
 * `positiveOnly` is the valuation rule: a zero or negative multiple is not a
 * cheap one. It may still be the *only* number in a row, in which case there is
 * no comparison to win and no marker.
 */
export function bestValue(
  values: readonly unknown[],
  direction: MetricDirection | undefined,
  positiveOnly = false,
): number | null {
  if (direction !== "high" && direction !== "low") return null;

  let best: number | null = null;
  /** Companies that reported a number at all — the comparison's size. */
  let reported = 0;

  for (const value of values) {
    if (value === null || value === undefined || value === "") continue;
    const number = Number(value);
    if (!Number.isFinite(number)) continue;
    reported += 1;
    if (positiveOnly && number <= 0) continue;
    if (best === null) best = number;
    else if (direction === "high" ? number > best : number < best) best = number;
  }

  return reported > 1 ? best : null;
}

/** Is this cell the row's winner? Absent cells never are. */
export function isBest(value: unknown, best: number | null): boolean {
  if (best === null || value === null || value === undefined || value === "") return false;
  const number = Number(value);
  return Number.isFinite(number) && number === best;
}
