/**
 * The overview snapshot: six columns of fourteen label/value pairs.
 *
 * The shape is the reference surface's (84 cells in six parallel columns), but
 * the **keys** are this product's own catalogue keys: the grid labels a column
 * with `COLUMN_META` and reads the value from whatever the ticker page already
 * holds — the fundamentals document, the selection row, the optional profile
 * document. Nothing is fetched for this grid and nothing is derived, so a cell
 * is either a value the API sent or the em dash.
 *
 * Three rules are load-bearing:
 *
 * 1. **The list is fixed.** A symbol that reports four fields still draws 84
 *    rows: 80 of them an honest `—`. That is the point of a snapshot — the
 *    reader sees what is missing, not a shorter table that implies the rest
 *    does not exist.
 * 2. **Absent is not zero.** `null`, `undefined`, `""` and non-finite numbers
 *    print the placeholder; a reported `0` prints `0`.
 * 3. **Units come from the catalogue.** The selection row carries naked
 *    numbers, so the column kind decides the format (money, percent, ratio,
 *    count); a fundamentals cell's own unit wins when it has one.
 */

import {
  formatCompact,
  formatDateValue,
  formatMoney,
  formatNumber,
  formatPercent,
  formatSignedPercent,
  isAbsent,
  NULL_PLACEHOLDER,
} from "@/grid/format";
import { COLUMN_META, type ColumnKind } from "@/grid/columnMeta";

export interface SnapshotColumn {
  /** Stable id for `data-*` and tests; not a catalogue key. */
  readonly id: string;
  readonly heading: string;
  /** Fourteen catalogue keys, in draw order. */
  readonly keys: readonly string[];
}

/**
 * Six columns, fourteen keys each. The grouping follows the reference
 * surface's six blocks (profile/dividends, valuation, growth, ownership and
 * quality, shares and technicals, performance), using only keys this product
 * actually serves.
 */
export const SNAPSHOT_COLUMNS: readonly SnapshotColumn[] = [
  {
    id: "profile",
    heading: "Profile",
    keys: [
      "index_membership",
      "market_cap",
      "enterprise_value",
      "net_income",
      "sales",
      "book_per_share",
      "cash_per_share",
      "dividend",
      "dividend_ttm",
      "dividend_ex_date",
      "dividend_growth_3y",
      "payout_ratio",
      "employees",
      "ipo_date",
    ],
  },
  {
    id: "valuation",
    heading: "Valuation",
    keys: [
      "pe",
      "forward_pe",
      "peg",
      "ps",
      "pb",
      "pc",
      "pfcf",
      "ev_ebitda",
      "ev_sales",
      "quick_ratio",
      "current_ratio",
      "debt_to_equity",
      "lt_debt_to_equity",
      "optionable",
    ],
  },
  {
    id: "growth",
    heading: "Growth",
    keys: [
      "eps",
      "eps_next_q",
      "eps_next_y",
      "eps_this_y",
      "eps_past_3y",
      "eps_past_5y",
      "eps_qoq",
      "eps_yoy_ttm",
      "sales_qoq",
      "sales_yoy_ttm",
      "earnings_date",
      "recommendation",
      "target_price",
      "dividend_growth_5y",
    ],
  },
  {
    id: "quality",
    heading: "Quality",
    keys: [
      "insider_own",
      "insider_trans",
      "inst_own",
      "inst_trans",
      "roa",
      "roe",
      "roic",
      "gross_margin",
      "operating_margin",
      "net_margin",
      "sma20",
      "sma50",
      "sma200",
      "float_pct",
    ],
  },
  {
    id: "technicals",
    heading: "Shares & technicals",
    keys: [
      "shares_outstanding",
      "shares_float",
      "short_float",
      "short_ratio",
      "short_interest",
      "high_52w",
      "low_52w",
      "volatility_1w",
      "volatility_4w",
      "atr",
      "rsi",
      "beta",
      "average_volume",
      "volume",
    ],
  },
  {
    id: "performance",
    heading: "Performance",
    keys: [
      "perf_1w",
      "perf_4w",
      "perf_13w",
      "perf_26w",
      "perf_ytd",
      "perf_52w",
      "perf_3y",
      "perf_5y",
      "perf_10y",
      "change",
      "change_from_open",
      "gap",
      "prev_close",
      "price",
    ],
  },
];

/** Fourteen rows; asserted by the tests so a column cannot silently grow. */
export const SNAPSHOT_ROWS = 14;

/** Six columns × fourteen rows. */
export const SNAPSHOT_CELLS = SNAPSHOT_COLUMNS.length * SNAPSHOT_ROWS;

/** Cases where the selection row spells a catalogue key differently. */
const ROW_ALIASES: Readonly<Record<string, readonly string[]>> = {
  shares_float: ["shares_float", "float_shares"],
  dividend_ex_date: ["dividend_ex_date", "ex_dividend_date"],
  index_membership: ["index_membership", "index"],
};

export interface MetricValue {
  readonly value: unknown;
  readonly unit: string;
}

export interface SnapshotSources {
  /** `GET /engine/fundamentals/{symbol}` metrics, keyed by catalogue key. */
  readonly metrics?: Readonly<Record<string, MetricValue>> | null;
  /** The `POST /screens/select` row for this symbol. */
  readonly row?: Readonly<Record<string, unknown>> | null;
  /** `GET /engine/profile/{symbol}`, when the deployment serves it. */
  readonly profile?: Readonly<Record<string, unknown>> | null;
}

function fromRecord(
  record: Readonly<Record<string, unknown>> | null | undefined,
  key: string,
): unknown {
  if (!record) return undefined;
  const candidates = ROW_ALIASES[key] ?? [key];
  for (const candidate of candidates) {
    if (Object.prototype.hasOwnProperty.call(record, candidate)) {
      const value = record[candidate];
      if (value !== undefined && value !== null) return value;
    }
  }
  return undefined;
}

/**
 * The first source that states a value. Metrics carry a unit; the row and the
 * profile are naked numbers whose unit the catalogue supplies.
 */
export function snapshotSource(
  key: string,
  sources: SnapshotSources,
): { value: unknown; unit: string | null } {
  const metric = sources.metrics?.[key];
  if (metric && !isAbsent(metric.value)) return { value: metric.value, unit: metric.unit };

  const fromProfile = fromRecord(sources.profile, key);
  if (!isAbsent(fromProfile)) return { value: fromProfile, unit: null };

  const fromRow = fromRecord(sources.row, key);
  if (!isAbsent(fromRow)) return { value: fromRow, unit: null };

  return { value: null, unit: metric?.unit ?? null };
}

/** The label the catalogue gives the key, or a readable fallback. */
export function snapshotLabel(key: string): string {
  const meta = COLUMN_META[key];
  if (meta) return meta.label;
  const spaced = key.replace(/[_-]+/g, " ").trim();
  return spaced.length === 0 ? key : spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

/** Counts print compact (`24.15B`), not with three digits of grouping. */
const COUNT_KEYS: ReadonlySet<string> = new Set([
  "shares_outstanding",
  "shares_float",
  "short_interest",
  "average_volume",
  "volume",
  "employees",
]);

/** Percentages whose sign is a fact about the company, not a delta. */
const SIGNED_PERCENT_KEYS: ReadonlySet<string> = new Set([
  "insider_trans",
  "inst_trans",
]);

function kindOf(key: string): ColumnKind {
  return COLUMN_META[key]?.kind ?? "number";
}

export interface SnapshotCell {
  /** The printed text, without a unit suffix. */
  readonly text: string;
  /** True when the source did not state a value. */
  readonly absent: boolean;
  /** `up`/`down` for a signed value; `null` for a level. */
  readonly tone: "up" | "down" | null;
}

/**
 * One cell, formatted by the catalogue's kind.
 *
 * A fundamentals unit wins over the kind only where it is more specific
 * (`unknown` for a per-share line means "do not put a `$` in front of EPS");
 * otherwise the kind is the format, because the row has no units at all.
 */
export function snapshotCell(key: string, sources: SnapshotSources): SnapshotCell {
  const { value } = snapshotSource(key, sources);
  if (isAbsent(value)) return { text: NULL_PLACEHOLDER, absent: true, tone: null };

  if (typeof value === "boolean") {
    return { text: value ? "Yes" : "No", absent: false, tone: null };
  }
  if (typeof value === "string") {
    const trimmed = value.trim();
    if (trimmed.length === 0) return { text: NULL_PLACEHOLDER, absent: true, tone: null };
    return { text: trimmed, absent: false, tone: null };
  }
  // `index_membership` arrives as a list of index names on some routes.
  if (Array.isArray(value)) {
    const entries = value.filter((entry): entry is string => typeof entry === "string");
    return entries.length === 0
      ? { text: NULL_PLACEHOLDER, absent: true, tone: null }
      : { text: entries.join(", "), absent: false, tone: null };
  }
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return { text: NULL_PLACEHOLDER, absent: true, tone: null };
  }

  const meta = COLUMN_META[key];
  const kind = kindOf(key);
  const precision = meta?.precision ?? 2;
  const suffix = meta?.suffix ?? "";

  switch (kind) {
    case "money":
      return { text: formatMoney(value, false, precision), absent: false, tone: null };
    case "compactMoney":
      return { text: formatMoney(value, true), absent: false, tone: null };
    case "percent": {
      const signed = SIGNED_PERCENT_KEYS.has(key);
      const tone = signed && value !== 0 ? (value > 0 ? "up" : "down") : null;
      return {
        text: signed ? formatSignedPercent(value, precision) : formatPercent(value, precision),
        absent: false,
        tone,
      };
    }
    case "delta": {
      const tone = value === 0 ? null : value > 0 ? "up" : "down";
      return { text: formatSignedPercent(value, precision), absent: false, tone };
    }
    case "integer":
      return {
        text: COUNT_KEYS.has(key) ? formatCompact(value) : formatNumber(value, 0),
        absent: false,
        tone: null,
      };
    case "date":
    case "timestamp":
      return { text: formatDateValue(value), absent: false, tone: null };
    case "badge":
      return { text: value ? "Yes" : "No", absent: false, tone: null };
    case "text":
      return { text: String(value), absent: false, tone: null };
    case "number":
    default:
      return {
        text: `${formatNumber(value, precision)}${suffix}`,
        absent: false,
        tone: null,
      };
  }
}
