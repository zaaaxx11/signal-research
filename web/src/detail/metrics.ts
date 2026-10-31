/**
 * The fundamentals catalogue: 43 keys, five groups, one rendering rule.
 *
 * `GET /engine/fundamentals/{symbol}` annotates each metric with a value, a
 * unit and provenance — but **not** a category, and not always with all 43
 * keys. A foreign private issuer such as ASML answers with three text fields
 * and nothing else; a US filer answers with the full set. So the panel owns a
 * catalogue rather than iterating whatever arrived:
 *
 * * every key the endpoint has ever been observed to serve is listed here with
 *   its label, unit and group, so a symbol that reports three fields still
 *   draws 43 rows — 40 of them an honest `—`, not a missing section;
 * * a key the catalogue does not know is rendered in *Other* instead of being
 *   silently dropped (the endpoint may add a metric between releases);
 * * the payload's own unit wins over the catalogue's fallback when it has one.
 *
 * The group names follow the plan (§7) — valuation / growth / quality / income —
 * plus a *profile* group for the five identity fields the same payload carries
 * and an *other* bucket for unknown keys. Grouping is editorial: the endpoint
 * serves levels, not growth rates, so "growth" is the income-statement flow
 * set. Nothing is derived, nothing is invented.
 */

import {
  formatCompact,
  formatMoney,
  formatNumber,
  isAbsent,
  NULL_PLACEHOLDER,
} from "@/grid/format";

export const METRIC_GROUPS = [
  "valuation",
  "growth",
  "quality",
  "income",
  "profile",
  "other",
] as const;

export type MetricGroup = (typeof METRIC_GROUPS)[number];

export const METRIC_GROUP_LABELS: Record<MetricGroup, string> = {
  valuation: "Valuation",
  growth: "Growth",
  quality: "Quality",
  income: "Income",
  profile: "Profile",
  other: "Other",
};

export interface MetricDefinition {
  key: string;
  label: string;
  /** Fallback unit, used only when the payload does not carry one. */
  unit: string;
  group: MetricGroup;
}

/**
 * All 43 keys observed from the live endpoint (AAPL, 2026-06-27). The order
 * inside a group is the order the panel draws; it is not the API's order.
 */
export const METRIC_DEFINITIONS: readonly MetricDefinition[] = [
  // ---- valuation: the per-share set this endpoint actually serves ----------
  { key: "eps", label: "EPS (diluted, TTM)", unit: "unknown", group: "valuation" },
  { key: "eps_basic", label: "EPS (basic)", unit: "unknown", group: "valuation" },
  { key: "eps_diluted", label: "EPS (diluted)", unit: "unknown", group: "valuation" },
  { key: "book_per_share", label: "Book value per share", unit: "USD", group: "valuation" },
  { key: "cash_per_share", label: "Cash per share", unit: "USD", group: "valuation" },

  // ---- growth: the income-statement flows ----------------------------------
  { key: "revenue", label: "Revenue", unit: "USD", group: "growth" },
  { key: "sales", label: "Sales", unit: "USD", group: "growth" },
  { key: "cost_of_revenue", label: "Cost of revenue", unit: "USD", group: "growth" },
  { key: "gross_profit", label: "Gross profit", unit: "USD", group: "growth" },
  { key: "operating_income", label: "Operating income", unit: "USD", group: "growth" },
  { key: "net_income", label: "Net income", unit: "USD", group: "growth" },
  { key: "interest_expense", label: "Interest expense", unit: "USD", group: "growth" },
  {
    key: "depreciation_amortization",
    label: "Depreciation & amortisation",
    unit: "USD",
    group: "growth",
  },
  { key: "operating_cash_flow", label: "Operating cash flow", unit: "USD", group: "growth" },
  { key: "capex", label: "Capital expenditure", unit: "USD", group: "growth" },
  { key: "fcf", label: "Free cash flow", unit: "USD", group: "growth" },

  // ---- quality: the balance sheet and the ratios drawn from it -------------
  { key: "assets", label: "Total assets", unit: "USD", group: "quality" },
  { key: "liabilities", label: "Total liabilities", unit: "USD", group: "quality" },
  { key: "equity", label: "Total equity", unit: "USD", group: "quality" },
  { key: "current_assets", label: "Current assets", unit: "USD", group: "quality" },
  { key: "current_liabilities", label: "Current liabilities", unit: "USD", group: "quality" },
  { key: "cash_and_equivalents", label: "Cash & equivalents", unit: "USD", group: "quality" },
  { key: "inventory", label: "Inventory", unit: "USD", group: "quality" },
  { key: "receivables", label: "Receivables", unit: "USD", group: "quality" },
  { key: "ppe", label: "Property, plant & equipment", unit: "USD", group: "quality" },
  { key: "long_term_debt", label: "Long-term debt", unit: "USD", group: "quality" },
  { key: "total_debt", label: "Total debt", unit: "USD", group: "quality" },
  { key: "retained_earnings", label: "Retained earnings", unit: "USD", group: "quality" },
  { key: "shares_outstanding", label: "Shares outstanding", unit: "shares", group: "quality" },
  { key: "gross_margin", label: "Gross margin", unit: "pct", group: "quality" },
  { key: "operating_margin", label: "Operating margin", unit: "pct", group: "quality" },
  { key: "net_margin", label: "Net margin", unit: "pct", group: "quality" },
  { key: "roa", label: "Return on assets", unit: "pct", group: "quality" },
  { key: "roe", label: "Return on equity", unit: "pct", group: "quality" },
  { key: "current_ratio", label: "Current ratio", unit: "ratio", group: "quality" },
  { key: "debt_to_equity", label: "Debt to equity", unit: "pct", group: "quality" },

  // ---- income: what the company returned to holders ------------------------
  { key: "dividends_paid", label: "Dividends paid", unit: "USD", group: "income" },
  { key: "buybacks", label: "Share buybacks", unit: "USD", group: "income" },

  // ---- profile: identity, not finance --------------------------------------
  { key: "ticker", label: "Ticker", unit: "text", group: "profile" },
  { key: "company", label: "Company", unit: "text", group: "profile" },
  { key: "industry", label: "Industry", unit: "text", group: "profile" },
  { key: "country", label: "Country", unit: "text", group: "profile" },
  { key: "exchange", label: "Exchange", unit: "text", group: "profile" },
];

/** What the endpoint says one metric is; absent keys default to `unknown`. */
export interface MetricPayload {
  value: unknown;
  unit: string;
}

export interface MetricRow {
  key: string;
  label: string;
  unit: string;
  group: MetricGroup;
  value: unknown;
}

const BY_KEY = new Map(METRIC_DEFINITIONS.map((definition) => [definition.key, definition]));

/** `depreciation_amortization` -> `Depreciation amortization`, for unknowns. */
export function humanizeKey(key: string): string {
  const spaced = key.replace(/[_-]+/g, " ").trim();
  return spaced.length === 0 ? key : spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

/**
 * One row per catalogue key plus one per unknown payload key, grouped.
 *
 * A definition the payload did not answer keeps `value: undefined` so the
 * renderer can print the placeholder — absence is data here, not a reason to
 * shorten the list.
 */
export function groupMetricRows(
  payload: Readonly<Record<string, MetricPayload>>,
): ReadonlyArray<{ group: MetricGroup; rows: MetricRow[] }> {
  const rows: MetricRow[] = METRIC_DEFINITIONS.map((definition) => {
    const entry = payload[definition.key];
    return {
      key: definition.key,
      label: definition.label,
      unit: entry?.unit || definition.unit,
      group: definition.group,
      value: entry?.value,
    };
  });

  for (const key of Object.keys(payload).sort()) {
    if (BY_KEY.has(key)) continue;
    const entry = payload[key];
    rows.push({
      key,
      label: humanizeKey(key),
      unit: entry?.unit || "unknown",
      group: "other",
      value: entry?.value,
    });
  }

  return METRIC_GROUPS.map((group) => ({
    group,
    rows: rows.filter((row) => row.group === group),
  })).filter((section) => section.rows.length > 0);
}

/** The number of rows a fully-reported symbol renders. Asserted by the tests. */
export const METRIC_COUNT = METRIC_DEFINITIONS.length;

function unitLabel(unit: string): string {
  switch (unit) {
    case "USD":
      return "USD";
    case "pct":
      return "%";
    case "ratio":
      return "×";
    case "shares":
      return "sh";
    case "text":
      return "";
    case "unknown":
      return "";
    default:
      return unit;
  }
}

export interface MetricCell {
  /** The printed number/text, without its unit. */
  text: string;
  /** The visible unit tag; empty when the unit is carried by the text. */
  unit: string;
  absent: boolean;
}

/**
 * One value + unit pair, formatted by the unit rather than by the key.
 *
 * Money keeps `formatMoney`'s honesty rule (`<$0.01` is not `$0.00`), with the
 * `$` moved into the unit column; percentages and ratios print two decimals.
 * A value the payload does not carry is the em dash — never a zero.
 */
export function metricCell(value: unknown, unit: string): MetricCell {
  const label = unitLabel(unit);
  if (isAbsent(value)) return { text: NULL_PLACEHOLDER, unit: label, absent: true };

  if (typeof value === "string") return { text: value, unit: label, absent: false };
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return { text: NULL_PLACEHOLDER, unit: label, absent: true };
  }

  switch (unit) {
    case "USD":
      return {
        text: formatMoney(value, Math.abs(value) >= 1_000_000).replace("$", ""),
        unit: label,
        absent: false,
      };
    case "pct":
      return { text: formatNumber(value, 2), unit: "%", absent: false };
    case "ratio":
      return { text: formatNumber(value, 2), unit: "×", absent: false };
    case "shares":
      return { text: formatCompact(value), unit: "sh", absent: false };
    default:
      return { text: formatNumber(value, 2), unit: label, absent: false };
  }
}
