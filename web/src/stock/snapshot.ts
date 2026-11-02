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
 * 3. **Units come from the source.** `GET /engine/snapshot/{symbol}` states a
 *    unit per key (`ratio`, `percent`, `usd`, `count`, `bool`) and that unit is
 *    the format: a `ratio` of 0.7654 prints `0.77`, a `percent` of 76.54 prints
 *    `76.54%`. Only when a document does not carry a unit (a date, a list, the
 *    older fundamentals metrics, the scan row) does the catalogue's column kind
 *    decide.
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
 * The unit `GET /engine/snapshot/{symbol}` attaches to a key that the client
 * formats as a quantity.
 *
 * `ratio` and `percent` used to be indistinguishable on this page: the same
 * debt/equity fact arrives as `0.7654` with `ratio` or `76.54` with `percent`,
 * and only the unit says which one the number means. `usd` is money, `count`
 * is a quantity of things, and `bool` is a yes/no flag. Dates and the index
 * list carry no unit: their values are already text.
 */
export type SnapshotUnit = "ratio" | "percent" | "usd" | "count" | "bool";

const SNAPSHOT_UNITS: ReadonlySet<string> = new Set<string>([
  "ratio",
  "percent",
  "usd",
  "count",
  "bool",
]);

/** The document `GET /engine/snapshot/{symbol}` answers with. */
export interface SnapshotDocument {
  readonly symbol: string;
  readonly asOf: string | null;
  /** Canonical for its unit: 0.7654 for a ratio, 76.54 for a percent. */
  readonly values: Readonly<Record<string, unknown>>;
  readonly units: Readonly<Record<string, SnapshotUnit>>;
  /** Keys the engine knows it cannot answer; their value is `null`. */
  readonly missing: readonly string[];
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

/**
 * The snapshot document, or null when the answer cannot describe a symbol.
 *
 * A body with no `values` at all is not an empty snapshot — an all-null
 * `values` map is. The difference is the one the page paints: a document of
 * nulls is 84 honest em dashes, while a body with nothing in it is a failed
 * read that owes the reader a sentence.
 */
export function parseSnapshot(body: unknown, symbol = ""): SnapshotDocument | null {
  if (!isRecord(body)) return null;
  const rawValues = body["values"];
  if (!isRecord(rawValues)) return null;
  const values: Record<string, unknown> = { ...rawValues };
  if (Object.keys(values).length === 0) return null;

  const units: Record<string, SnapshotUnit> = {};
  const rawUnits = body["units"];
  if (isRecord(rawUnits)) {
    for (const [key, unit] of Object.entries(rawUnits)) {
      if (typeof unit === "string" && SNAPSHOT_UNITS.has(unit)) {
        units[key] = unit as SnapshotUnit;
      }
    }
  }

  const missing: string[] = [];
  if (Array.isArray(body["missing"])) {
    for (const key of body["missing"]) {
      if (typeof key === "string") missing.push(key);
    }
  }

  const declared = typeof body["symbol"] === "string" ? body["symbol"].trim() : "";
  return {
    symbol: (declared.length > 0 ? declared : symbol).toUpperCase(),
    asOf: typeof body["as_of"] === "string" ? body["as_of"] : null,
    values,
    units,
    missing,
  };
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
  /**
   * `GET /engine/snapshot/{symbol}`. When present it is the **only** source:
   * the endpoint states a value and a unit for every key it knows, so mixing
   * it with the older documents would let a stale metric fill a cell the
   * endpoint deliberately answered `null` for.
   */
  readonly snapshot?: SnapshotDocument | null;
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
 * profile are naked numbers whose unit the catalogue supplies. The snapshot
 * document, when present, answers alone — see `SnapshotSources.snapshot`.
 */
export function snapshotSource(
  key: string,
  sources: SnapshotSources,
): { value: unknown; unit: string | null } {
  const document = sources.snapshot;
  if (document) {
    return {
      value: document.values[key] ?? null,
      unit: document.units[key] ?? null,
    };
  }

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
 * The endpoint's four units, plus the spellings the older fundamentals
 * metrics use. Anything else (`unknown`, `text`) returns null, and the
 * catalogue's column kind decides the shape.
 */
function unitOf(unit: string | null): SnapshotUnit | null {
  switch (unit) {
    case "ratio":
    case "x":
      return "ratio";
    case "percent":
    case "pct":
    case "%":
      return "percent";
    case "usd":
    case "USD":
      return "usd";
    case "count":
    case "shares":
    case "sh":
      return "count";
    case "bool":
      // A boolean is the value itself; no numeric shape is needed.
      return null;
    default:
      return null;
  }
}

/**
 * One numeric cell, shaped by the source's unit.
 *
 * The unit wins over the catalogue for the one decision that has two plausible
 * answers: `ratio` is dimensionless (`0.77`) while `percent` carries its mark
 * (`76.54%`), and only the source knows which the number means. `usd` still
 * reads the catalogue for compactness — a market cap must print `$5.18T` in a
 * 70 px cell — and `count` for the share quantities.
 */
function cellForUnit(
  value: number,
  key: string,
  unit: SnapshotUnit | null,
): SnapshotCell | null {
  const precision = COLUMN_META[key]?.precision ?? 2;
  switch (unit) {
    case "ratio":
      // The unit's contract is two decimals for every multiple. The catalogue's
      // per-column precision is a grid hint (RSI asks for one), not a licence
      // to print a ratio at a different scale here.
      return { text: formatNumber(value, 2), absent: false, tone: null };
    case "percent": {
      const signed = SIGNED_PERCENT_KEYS.has(key) || kindOf(key) === "delta";
      const tone = signed && value !== 0 ? (value > 0 ? "up" : "down") : null;
      return {
        text: signed ? formatSignedPercent(value, precision) : formatPercent(value, precision),
        absent: false,
        tone,
      };
    }
    case "usd": {
      const compact = kindOf(key) === "compactMoney" || Math.abs(value) >= 1_000_000;
      return { text: formatMoney(value, compact, precision), absent: false, tone: null };
    }
    case "count":
      return {
        text: COUNT_KEYS.has(key) ? formatCompact(value) : formatNumber(value, 0),
        absent: false,
        tone: null,
      };
    default:
      return null;
  }
}

/**
 * One cell, formatted by the source's unit and then the catalogue's kind.
 *
 * A fundamentals unit is only believed where it says something the catalogue
 * cannot (`pct` for a ratio-shaped column, `shares` for a count); a fully
 * unlabelled value falls back to the kind, because the scan row has no units
 * at all.
 */
export function snapshotCell(key: string, sources: SnapshotSources): SnapshotCell {
  const { value, unit } = snapshotSource(key, sources);
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
    // An empty list is an answer ("in none of the tracked indices"), not an
    // absence; only a null value earns the em dash.
    return entries.length === 0
      ? { text: "None", absent: false, tone: null }
      : { text: entries.join(", "), absent: false, tone: null };
  }
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return { text: NULL_PLACEHOLDER, absent: true, tone: null };
  }

  const meta = COLUMN_META[key];
  const kind = kindOf(key);
  const precision = meta?.precision ?? 2;
  const suffix = meta?.suffix ?? "";

  // A date, a badge or a string is not a quantity: a unit cannot reshape it.
  switch (kind) {
    case "date":
    case "timestamp":
      return { text: formatDateValue(value), absent: false, tone: null };
    case "badge":
      return { text: value ? "Yes" : "No", absent: false, tone: null };
    case "text":
      return { text: String(value), absent: false, tone: null };
    default:
      break;
  }

  const byUnit = cellForUnit(value, key, unitOf(unit));
  if (byUnit) return byUnit;

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
    case "number":
    default:
      return {
        text: `${formatNumber(value, precision)}${suffix}`,
        absent: false,
        tone: null,
      };
  }
}
