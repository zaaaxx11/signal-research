/**
 * `GET /engine/statements/{symbol}` — one issuer's three statements.
 *
 * Two wire shapes are accepted, because the engine has published both:
 *
 *   * **The ticker table** (current): `periods: [{period, kind, end, …}]` and
 *     `statements.income: [{key, label, unit, values: {label: value|null},
 *     yoy_pct, ttm}]`. Annual and quarterly columns arrive together, values
 *     are keyed by the period label, and the row already carries the YoY the
 *     collector computed.
 *   * **The audit table** (the `/annual` route): `periods: [{period_end,
 *     fiscal_year}]` and `statements.income: {revenue: [{value, unit}, …]}`,
 *     one array per line aligned to the columns.
 *
 * This module is only the reader. It keeps the document's order, labels the
 * keys it knows, humanises the ones it does not, and never turns an absent
 * cell into a zero.
 *
 * **A gap is visible.** A missing value stays `null` and the table prints the
 * em dash; a reported zero prints `$0.00`. **YoY is computed, not guessed** —
 * the row's own `yoy_pct` when the document carries it, otherwise the newest
 * column against the one before it, and a same-quarter comparison for the
 * quarterly mode. With one end missing there is no ratio.
 */

import { formatCompact, formatMoney, formatNumber, isAbsent, NULL_PLACEHOLDER } from "@/grid/format";

export type StatementId = "income" | "balance" | "cash_flow";
export type StatementMode = "annual" | "quarterly";

export const STATEMENT_IDS: readonly StatementId[] = ["income", "balance", "cash_flow"];

export const STATEMENT_TITLES: Readonly<Record<StatementId, string>> = {
  income: "Income statement",
  balance: "Balance sheet",
  cash_flow: "Cash flow statement",
};

/** The lines the ticker table publishes, plus the aliases older rows use. */
const LINE_LABELS: Readonly<Record<string, string>> = {
  revenue: "Revenue",
  gross_profit: "Gross profit",
  operating_income: "Operating income",
  net_income: "Net income",
  eps_diluted: "EPS (diluted)",
  assets: "Total assets",
  liabilities: "Total liabilities",
  equity: "Total equity",
  cash_and_equivalents: "Cash & equivalents",
  total_debt: "Total debt",
  current_assets: "Current assets",
  current_liabilities: "Current liabilities",
  long_term_debt: "Long-term debt",
  shares_outstanding: "Shares outstanding",
  operating_cash_flow: "Operating cash flow",
  investing_cash_flow: "Investing cash flow",
  financing_cash_flow: "Financing cash flow",
  capex: "Capital expenditure",
  fcf: "Free cash flow",
  free_cash_flow: "Free cash flow",
};

export function statementLabel(key: string): string {
  const known = LINE_LABELS[key];
  if (known) return known;
  const spaced = key.replace(/[_-]+/g, " ").trim();
  return spaced.length === 0 ? key : spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

/** One aligned cell; `value` is null when the tag was not reported. */
export interface StatementCell {
  readonly value: number | null;
  readonly unit: string;
  /** The API's own status (`ok`, `not_reported`, `derived`), when it says. */
  readonly status: string | null;
}

export interface StatementPeriod {
  /** The column header: `2025`, `2025Q3`, or an ISO end date. */
  readonly label: string;
  readonly kind: StatementMode;
  readonly periodEnd: string;
  readonly fiscalYear: number | null;
  readonly form: string | null;
}

export interface StatementLine {
  readonly key: string;
  readonly label: string;
  readonly unit: string;
  /** One cell per period, in the document's order (newest first). */
  readonly cells: readonly StatementCell[];
  /** The collector's own annual YoY, when the ticker table carries it. */
  readonly yoyPct: number | null;
  /** The collector's trailing-twelve-month figure, when it can build one. */
  readonly ttm: number | null;
}

export interface StatementBlock {
  readonly id: StatementId;
  readonly title: string;
  readonly lines: readonly StatementLine[];
}

export interface StatementsDocument {
  readonly symbol: string;
  readonly asOf: string | null;
  readonly periods: readonly StatementPeriod[];
  readonly blocks: readonly StatementBlock[];
  /** Facts the collector could not read; rendered, never swallowed. */
  readonly providerErrors: readonly string[];
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function text(value: unknown): string | null {
  return typeof value === "string" && value.trim().length > 0 ? value.trim() : null;
}

function number(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function readCell(raw: unknown, fallbackUnit: string): StatementCell {
  if (isRecord(raw)) {
    const provenance = isRecord(raw["provenance"]) ? raw["provenance"] : null;
    return {
      value: number(raw["value"]),
      unit: text(raw["unit"]) ?? fallbackUnit,
      status: text(provenance?.["status"]),
    };
  }
  return { value: number(raw), unit: fallbackUnit, status: null };
}

function readPeriods(raw: unknown): StatementPeriod[] {
  if (!Array.isArray(raw)) return [];
  const periods: StatementPeriod[] = [];
  for (const entry of raw) {
    if (!isRecord(entry)) continue;
    const periodEnd = text(entry["period_end"]) ?? text(entry["end"]);
    if (!periodEnd) continue;
    const fiscalYear = number(entry["fiscal_year"]);
    const label =
      text(entry["period"]) ??
      (fiscalYear !== null ? `FY${fiscalYear}` : periodEnd);
    periods.push({
      label,
      kind: entry["kind"] === "quarterly" ? "quarterly" : "annual",
      periodEnd,
      fiscalYear,
      form: text(entry["form"]),
    });
  }
  return periods;
}

/** The ticker table: one object per line, values keyed by the period label. */
function readKeyedRows(raw: readonly unknown[], periods: readonly StatementPeriod[]): StatementLine[] {
  const lines: StatementLine[] = [];
  for (const entry of raw) {
    if (!isRecord(entry)) continue;
    const key = text(entry["key"]);
    const values = isRecord(entry["values"]) ? entry["values"] : null;
    if (key === null || values === null) continue;
    const unit = text(entry["unit"]) ?? "USD";
    const cells = periods.map((period) =>
      readCell(values[period.label] ?? values[period.periodEnd], unit),
    );
    lines.push({
      key,
      label: text(entry["label"]) ?? statementLabel(key),
      unit,
      cells,
      yoyPct: number(entry["yoy_pct"]),
      ttm: number(entry["ttm"]),
    });
  }
  return lines;
}

/** The audit table: one array per line, aligned to the period columns. */
function readAlignedRows(
  group: Record<string, unknown>,
  periods: readonly StatementPeriod[],
): StatementLine[] {
  const lines: StatementLine[] = [];
  for (const [key, raw] of Object.entries(group)) {
    if (!Array.isArray(raw)) continue;
    const cells = periods.map((_, index) =>
      readCell(raw[index], text((raw[index] as Record<string, unknown> | undefined)?.["unit"]) ?? "USD"),
    );
    lines.push({
      key,
      label: statementLabel(key),
      unit: cells.find((cell) => cell.value !== null)?.unit ?? "USD",
      cells,
      yoyPct: null,
      ttm: null,
    });
  }
  return lines;
}

/**
 * The document, in the shape the table draws.
 *
 * The three groups are emitted in a fixed order even when the wire omits one:
 * an empty balance sheet is a section with no rows, not a shorter page.
 */
export function parseStatements(body: unknown): StatementsDocument | null {
  if (!isRecord(body)) return null;
  const periods = readPeriods(body["periods"]);
  const groups = isRecord(body["statements"]) ? body["statements"] : {};

  const blocks: StatementBlock[] = STATEMENT_IDS.map((id) => {
    const group = groups[id];
    const lines = Array.isArray(group)
      ? readKeyedRows(group, periods)
      : isRecord(group)
        ? readAlignedRows(group, periods)
        : [];
    return { id, title: STATEMENT_TITLES[id], lines };
  });

  const providerErrors = Array.isArray(body["provider_errors"])
    ? body["provider_errors"].filter((entry): entry is string => typeof entry === "string")
    : [];

  return {
    symbol: text(body["symbol"]) ?? "",
    asOf: text(body["as_of"]),
    periods,
    blocks,
    providerErrors,
  };
}

/** The column header. `2025` is already the filing's own label. */
export function periodLabel(period: StatementPeriod): string {
  return period.label;
}

/**
 * The newest column against the one before it (annual order).
 *
 * `index` is the column receiving the YoY cell: index 0 compares with 1, and
 * the oldest column has no earlier peer. Missing ends and a zero base are the
 * em dash — a ratio with no denominator is not 0 %.
 */
export function yearOverYear(cells: readonly StatementCell[], index: number): number | null {
  const current = cells[index]?.value;
  const previous = cells[index + 1]?.value;
  if (current === null || current === undefined) return null;
  if (previous === null || previous === undefined) return null;
  if (!Number.isFinite(current) || !Number.isFinite(previous) || previous === 0) return null;
  return ((current - previous) / Math.abs(previous)) * 100;
}

/**
 * The same quarter one year earlier, for the quarterly mode.
 *
 * The label form (`2025Q3` → `2024Q3`) is the precise case. A column labelled
 * with an ISO end date falls back to the period whose end is 330–400 days
 * before it — the same window the collector uses for a fiscal year.
 */
export function yearOverYearQuarter(
  cells: readonly StatementCell[],
  periods: readonly StatementPeriod[],
  index: number,
): number | null {
  const current = periods[index];
  const value = cells[index]?.value;
  if (!current || value === null || value === undefined) return null;

  const match = /^(\d{4})Q([1-4])$/.exec(current.label);
  let priorIndex = -1;
  if (match) {
    const wanted = `${Number(match[1]) - 1}Q${match[2]}`;
    priorIndex = periods.findIndex((period) => period.label === wanted);
  } else {
    const target = new Date(`${current.periodEnd}T00:00:00Z`).getTime();
    if (Number.isFinite(target)) {
      priorIndex = periods.findIndex((period) => {
        const end = new Date(`${period.periodEnd}T00:00:00Z`).getTime();
        if (!Number.isFinite(end)) return false;
        const days = (target - end) / 86_400_000;
        return days >= 330 && days <= 400;
      });
    }
  }

  if (priorIndex < 0) return null;
  const previous = cells[priorIndex]?.value;
  if (previous === null || previous === undefined || previous === 0) return null;
  return ((value - previous) / Math.abs(previous)) * 100;
}

/** A statement number: compact money for USD, two decimals for per-share. */
export function formatStatementValue(cell: StatementCell): string {
  if (isAbsent(cell.value) || cell.value === null) return NULL_PLACEHOLDER;
  if (cell.unit === "USD") return formatMoney(cell.value, Math.abs(cell.value) >= 10_000);
  if (cell.unit === "shares") return formatCompact(cell.value);
  return formatNumber(cell.value, 2);
}

/** The sentence a 404 or an empty document owes the reader. */
export function statementsReason(document: StatementsDocument | null): string | null {
  if (!document) return null;
  if (document.providerErrors.length > 0) return document.providerErrors.join("; ");
  if (document.periods.length === 0) return "The collector returned no annual periods.";
  return null;
}
