/**
 * The two derived documents the ticker Overview reads: technicals and risk.
 *
 * `GET /engine/technicals/{symbol}` states a unit per key; `GET /engine/risk`
 * may not. Both are therefore parsed into one shape — `values` plus a `units`
 * map — and a missing unit is filled from the catalogue below, never guessed
 * from the number's magnitude. A percent is already in points (5.83 means
 * 5.83 %), so the formatter adds the mark and never the ×100.
 *
 * Absent is not zero: `null`, `undefined`, `""` and non-finite numbers print
 * the em dash. A reported zero prints `0.00%` and claims no direction.
 */

import {
  formatNumber,
  formatPercent,
  formatSignedPercent,
  isAbsent,
  NULL_PLACEHOLDER,
} from "@/grid/format";

/** The units either endpoint can attach to a published key. */
export type MetricUnit = "price" | "percent" | "index" | "ratio" | "label";

const METRIC_UNITS: ReadonlySet<string> = new Set<string>([
  "price",
  "percent",
  "index",
  "ratio",
  "label",
]);

/** One engine document, normalised: numbers plus what they mean. */
export interface MetricDocument {
  readonly symbol: string;
  readonly asOf: string | null;
  readonly values: Readonly<Record<string, unknown>>;
  readonly units: Readonly<Record<string, MetricUnit>>;
}

/** The moving-average windows the ticker page prints (the endpoint's default). */
export const MA_WINDOWS = [13, 21, 50, 100, 200] as const;

/** A label/value row inside one group. `signed` also claims up/down colour. */
export interface MetricRow {
  readonly key: string;
  readonly label: string;
  readonly signed?: boolean;
}

export const OSCILLATOR_ROWS: readonly MetricRow[] = [
  { key: "rsi_14", label: "RSI 14" },
  { key: "momentum_10", label: "Momentum 10", signed: true },
];

export const RETURN_ROWS: readonly MetricRow[] = [
  { key: "perf_wtd", label: "WTD", signed: true },
  { key: "perf_mtd", label: "MTD", signed: true },
];

/**
 * Alpha, volatility and max drawdown are percent points; beta, Sharpe and R²
 * are plain ratios. That is the shape the group is read in — 4.82 % alpha is
 * not a 0.05 ratio — and the endpoint may override it with its own `units`.
 */
export const RISK_ROWS: readonly MetricRow[] = [
  { key: "alpha", label: "Alpha", signed: true },
  { key: "beta", label: "Beta" },
  { key: "volatility", label: "Volatility" },
  { key: "sharpe", label: "Sharpe", signed: true },
  { key: "max_drawdown", label: "Max drawdown", signed: true },
  { key: "r_squared", label: "R²" },
];

const RISK_FALLBACK_UNITS: Readonly<Record<string, MetricUnit>> = {
  alpha: "percent",
  beta: "ratio",
  correlation: "ratio",
  r_squared: "ratio",
  volatility: "percent",
  sharpe: "ratio",
  max_drawdown: "percent",
};

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

/**
 * The unit a technicals key is written in when the document omits `units`.
 *
 * Derived from the key, not from the value: 1.08 is both a plausible beta and
 * a plausible price, so the only honest source is the key's own definition.
 */
function technicalUnit(key: string): MetricUnit | undefined {
  if (key.startsWith("sma_") || key.startsWith("ema_")) return "price";
  if (key.startsWith("rsi_")) return "index";
  if (key.startsWith("momentum_") || key === "perf_wtd" || key === "perf_mtd") {
    return "percent";
  }
  return undefined;
}

function parseDocument(
  body: unknown,
  symbol: string,
  fallback: (key: string) => MetricUnit | undefined,
): MetricDocument | null {
  if (!isRecord(body)) return null;
  const rawValues = body["values"];
  // A body with no `values` is not an empty document; it is a failed read and
  // the block owes the reader the reason rather than fourteen silent dashes.
  if (!isRecord(rawValues) || Object.keys(rawValues).length === 0) return null;

  const values: Record<string, unknown> = { ...rawValues };
  const units: Record<string, MetricUnit> = {};
  const rawUnits = body["units"];
  if (isRecord(rawUnits)) {
    for (const [key, unit] of Object.entries(rawUnits)) {
      if (typeof unit === "string" && METRIC_UNITS.has(unit)) {
        units[key] = unit as MetricUnit;
      }
    }
  }
  for (const key of Object.keys(values)) {
    if (units[key] === undefined) {
      const unit = fallback(key);
      if (unit !== undefined) units[key] = unit;
    }
  }

  const declared = typeof body["symbol"] === "string" ? body["symbol"].trim() : "";
  return {
    symbol: (declared.length > 0 ? declared : symbol).toUpperCase(),
    asOf: typeof body["as_of"] === "string" ? body["as_of"] : null,
    values,
    units,
  };
}

/** `GET /engine/technicals/{symbol}`, or null when the answer has no values. */
export function parseTechnicals(body: unknown, symbol = ""): MetricDocument | null {
  return parseDocument(body, symbol, technicalUnit);
}

/** `GET /engine/risk/{symbol}`, with the risk group's unit catalogue. */
export function parseRisk(body: unknown, symbol = ""): MetricDocument | null {
  return parseDocument(body, symbol, (key) => RISK_FALLBACK_UNITS[key]);
}

/** The text a metric cell paints, plus its direction and absence. */
export interface MetricCell {
  readonly text: string;
  readonly tone: "up" | "down" | null;
  readonly absent: boolean;
}

export interface MetricFormatOptions {
  /** Percent/ratio gets an explicit sign and an up/down tone. */
  readonly signed?: boolean;
}

/** A signed ratio: `+1.42`, `-0.31`, `0.00` when it rounds away. */
function formatSignedNumber(value: number, precision = 2): string {
  const magnitude = formatNumber(Math.abs(value), precision);
  const printed = Number(magnitude.replace(/,/g, ""));
  if (!(printed > 0)) return magnitude;
  return value > 0 ? `+${magnitude}` : `-${magnitude}`;
}

/**
 * One value in its unit's shape.
 *
 * The tone is read off the string that was actually printed, so a magnitude
 * that rounds to zero carries neither `+` nor green.
 */
export function formatMetric(
  value: unknown,
  unit: MetricUnit | undefined,
  options: MetricFormatOptions = {},
): MetricCell {
  if (isAbsent(value)) {
    return { text: NULL_PLACEHOLDER, tone: null, absent: true };
  }
  const number = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(number)) {
    return { text: NULL_PLACEHOLDER, tone: null, absent: true };
  }

  const signed = options.signed === true;
  let text: string;
  switch (unit) {
    case "price":
    case "index":
      text = formatNumber(number, 2);
      break;
    case "percent":
      text = signed ? formatSignedPercent(number, 2) : formatPercent(number, 2);
      break;
    case "ratio":
      text = signed ? formatSignedNumber(number, 2) : formatNumber(number, 2);
      break;
    case "label":
      text = String(value);
      break;
    default:
      text = formatNumber(number, 2);
  }

  const tone: MetricCell["tone"] = !signed
    ? null
    : text.startsWith("+")
      ? "up"
      : text.startsWith("-")
        ? "down"
        : null;
  return { text, tone, absent: false };
}

/** The cell for one key of a parsed document. */
export function metricCell(
  document: MetricDocument | null,
  key: string,
  options: MetricFormatOptions = {},
): MetricCell {
  return formatMetric(document?.values[key], document?.units[key], options);
}
