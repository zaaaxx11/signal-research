/**
 * The breadth document: the shape `GET /market/breadth` answers with, and the
 * three things we are allowed to do to it.
 *
 * The rule this module exists to enforce is the one the old client-side
 * breadth broke: **the server counts, the browser formats.** Nothing here
 * averages a change, re-counts a bucket or re-derives a denominator. The only
 * arithmetic in this file is (a) adding the three buckets the endpoint already
 * split out, so a disagreement with `universe_total` can be *reported*, and
 * (b) ordering sectors for display.
 *
 * `parseBreadth` is deliberately strict. A payload missing a count is not a
 * market with zero of that thing: rendering `0` for an absent field is a claim
 * the server never made (grid/format.ts's first rule), so an unreadable answer
 * becomes an error state that says so.
 */

/** One provider sector bucket. `name` is null for rows the scan could not label. */
export interface SectorBucket {
  readonly name: string | null;
  /** Arithmetic mean over the bucket's rows that carried a numeric change; null when none did. */
  readonly avg_change_pct: number | null;
  /** Every row in the bucket, including rows with no reported change. */
  readonly count: number;
  readonly advancing: number;
  readonly declining: number;
}

/** The document `GET /market/breadth` answers with (app/api/discovery.py:breadth_of). */
export interface BreadthReport {
  readonly as_of: string;
  readonly region: string;
  readonly asset_class: string;
  readonly source: string;
  readonly stale: boolean;
  readonly universe_total: number;
  readonly advancing: number;
  readonly declining: number;
  /**
   * Flat names. The server puts a row with **no reported change** in this
   * bucket too (never in `declining`) and reports those separately as
   * `unchanged_missing`; `unchanged_reported` are the rows that really printed
   * `0.00`.
   */
  readonly unchanged: number;
  readonly unchanged_reported: number | null;
  readonly unchanged_missing: number | null;
  readonly new_highs: number;
  readonly new_lows: number;
  /** Rows carrying both a price and a 52-week high; null when the server did not say. */
  readonly new_highs_denominator: number | null;
  readonly new_lows_denominator: number | null;
  /** Rows with no provider sector label (they still sit in a null-named bucket). */
  readonly sector_missing: number | null;
  /** The server's own prose definition of each figure, keyed by field name. */
  readonly method: Readonly<Record<string, string>>;
  readonly sectors: readonly SectorBucket[];
}

/** The payload is not a breadth document. Distinct from a transport failure. */
export class BreadthMalformed extends Error {
  constructor(message: string) {
    super(message);
    this.name = "BreadthMalformed";
  }
}

function record(value: unknown, where: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new BreadthMalformed(`${where} is not an object`);
  }
  return value as Record<string, unknown>;
}

/**
 * A count is a non-negative finite number, or it is not a count.
 *
 * Written as a refusal rather than `Number(value) || 0`: coercing a missing
 * field to zero is exactly the failure ("0 advancing") this component is
 * replacing.
 */
function count(value: unknown, field: string): number {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) {
    throw new BreadthMalformed(`"${field}" is not a non-negative number`);
  }
  return value;
}

function optionalCount(value: unknown, field: string): number | null {
  if (value === undefined || value === null) return null;
  return count(value, field);
}

function text(value: unknown, field: string): string {
  if (typeof value !== "string" || value.trim().length === 0) {
    throw new BreadthMalformed(`"${field}" is not a non-empty string`);
  }
  return value;
}

function optionalText(value: unknown, fallback: string): string {
  return typeof value === "string" && value.length > 0 ? value : fallback;
}

function sector(value: unknown, index: number): SectorBucket {
  const where = `sectors[${index}]`;
  const raw = record(value, where);
  // A null label is a real bucket (the scan could not attribute the rows), so
  // it is preserved rather than dropped; the renderer names it "Unspecified".
  const name = typeof raw["name"] === "string" && raw["name"].trim().length > 0 ? raw["name"] : null;
  const average = raw["avg_change_pct"];
  return {
    name,
    // `null` is the server saying "nothing measurable in this bucket". Anything
    // that is not a finite number is treated the same way, never as 0.
    avg_change_pct:
      typeof average === "number" && Number.isFinite(average) ? average : null,
    count: count(raw["count"], `${where}.count`),
    advancing: count(raw["advancing"], `${where}.advancing`),
    declining: count(raw["declining"], `${where}.declining`),
  };
}

/**
 * Validate and normalise one breadth payload.
 *
 * `region`/`asset_class` are optional because a 200 from a proxy that renames
 * them is still readable; the counts are not optional, because the strip's
 * whole job is to print them.
 */
export function parseBreadth(value: unknown): BreadthReport {
  const raw = record(value, "breadth payload");

  const sectors = raw["sectors"];
  if (!Array.isArray(sectors)) {
    throw new BreadthMalformed('"sectors" is not an array');
  }

  const method: Record<string, string> = {};
  if (typeof raw["method"] === "object" && raw["method"] !== null) {
    for (const [key, prose] of Object.entries(raw["method"] as Record<string, unknown>)) {
      if (typeof prose === "string") method[key] = prose;
    }
  }

  return {
    as_of: text(raw["as_of"], "as_of"),
    region: optionalText(raw["region"], ""),
    asset_class: optionalText(raw["asset_class"], ""),
    source: optionalText(raw["source"], ""),
    stale: raw["stale"] === true,
    universe_total: count(raw["universe_total"], "universe_total"),
    advancing: count(raw["advancing"], "advancing"),
    declining: count(raw["declining"], "declining"),
    unchanged: count(raw["unchanged"], "unchanged"),
    unchanged_reported: optionalCount(raw["unchanged_reported"], "unchanged_reported"),
    unchanged_missing: optionalCount(raw["unchanged_missing"], "unchanged_missing"),
    new_highs: count(raw["new_highs"], "new_highs"),
    new_lows: count(raw["new_lows"], "new_lows"),
    new_highs_denominator: optionalCount(
      raw["new_highs_denominator"],
      "new_highs_denominator",
    ),
    new_lows_denominator: optionalCount(
      raw["new_lows_denominator"],
      "new_lows_denominator",
    ),
    sector_missing: optionalCount(raw["sector_missing"], "sector_missing"),
    method,
    sectors: sectors.map(sector),
  };
}

export interface SumCheck {
  /** advancing + declining + unchanged, as the endpoint reported each. */
  readonly sum: number;
  readonly total: number;
  /** sum − total: positive when the buckets double-count. */
  readonly delta: number;
  readonly ok: boolean;
}

/**
 * Do the three buckets account for the universe?
 *
 * This is not a re-count: it is an audit of the server's own arithmetic, so a
 * snapshot whose parts disagree with its whole can be *shown as inconsistent*
 * instead of quietly presented as fact.
 */
export function sumCheck(report: BreadthReport): SumCheck {
  const sum = report.advancing + report.declining + report.unchanged;
  return {
    sum,
    total: report.universe_total,
    delta: sum - report.universe_total,
    ok: sum === report.universe_total,
  };
}

function isMeasured(average: number | null): average is number {
  return typeof average === "number" && Number.isFinite(average);
}

/**
 * Strongest average first; buckets without a measurable average last.
 *
 * A null average is not a zero and must not sort as one — a sector whose rows
 * carried no `change_pct` would otherwise sit in the middle of the tape with a
 * fabricated "0.00%". Ties fall back to size, then to the label, so the order
 * is total: the same payload always renders in the same order.
 */
export function sortSectors(sectors: readonly SectorBucket[]): SectorBucket[] {
  return [...sectors].sort((left, right) => {
    const leftMeasured = isMeasured(left.avg_change_pct);
    const rightMeasured = isMeasured(right.avg_change_pct);
    if (leftMeasured !== rightMeasured) return leftMeasured ? -1 : 1;

    if (leftMeasured && rightMeasured) {
      const difference = (right.avg_change_pct as number) - (left.avg_change_pct as number);
      if (difference !== 0) return difference;
    }

    if (right.count !== left.count) return right.count - left.count;
    return (left.name ?? "").localeCompare(right.name ?? "");
  });
}
