/**
 * The groups document: `GET /market/groups`.
 *
 * One request answers the whole page. The server counts each sector, averages
 * the changes it can see, and sorts the rows; this module reads that answer and
 * is allowed exactly three things:
 *
 * * **order** — it re-applies the sort the contract promises (change
 *   descending, an unmeasured average last) so a server that forgets cannot
 *   put a falling sector at the top of a "sorted" table;
 * * **format** — counts and money go through the grid's existing formatters;
 * * **link** — a sector becomes `/screener?sector=<label>`, encoded, because a
 *   label with a space or an `&` must survive the trip into the filter.
 *
 * It never averages, re-counts or re-derives a figure. A missing count stays
 * `null` and prints an em dash: absent is not zero (`grid/format.ts`'s first
 * rule), and a sector the server could not measure is not a flat sector.
 */

import { barShare, formatCount } from "@/breadth/format";

export const GROUPS_REGION = "us";

/** One row: a sector, its aggregate change and its breadth. */
export interface GroupRow {
  readonly id: string;
  readonly label: string;
  /** Every name in the sector, including one the scan could not price. */
  readonly count: number | null;
  /** Mean of the member changes the scan could read; null when none carried one. */
  readonly avgChange: number | null;
  readonly adv: number | null;
  readonly dec: number | null;
  readonly unchanged: number | null;
  /** Summed market capitalisation, in USD. */
  readonly marketCap: number | null;
}

export interface GroupsReport {
  readonly region: string;
  /** The population the sectors were counted over, when the server said. */
  readonly universeTotal: number | null;
  /** Sorted by average change, strongest first. */
  readonly rows: readonly GroupRow[];
}

/** The payload is not a groups document. Distinct from a transport failure. */
export class GroupsMalformed extends Error {
  constructor(message: string) {
    super(message);
    this.name = "GroupsMalformed";
  }
}

/** The one GET. `region` is named because a document about another market is another page. */
export function groupsPath(region: string = GROUPS_REGION): string {
  const params = new URLSearchParams({ region });
  return `/market/groups?${params.toString()}`;
}

/**
 * A sector's screener URL, with the label the server sent.
 *
 * `encodeURIComponent`, not string concatenation: "Health Care" must arrive as
 * `Health%20Care`, and a label carrying `&`, `#` or `+` must not be able to end
 * the value early or smuggle a second parameter in. The round trip is asserted
 * in `groups.test.ts`.
 */
export function screenerHref(label: string): string {
  // `?f=` is the screener's own spelling of a filter, and `sec` is the column
  // it filters on. Emitting a second, private parameter (`?sector=`) would give
  // the app two ways to say one thing and one of them would rot.
  return `/screener?f=sec:eq:${encodeURIComponent(label)}`;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function text(value: unknown): string | null {
  if (typeof value !== "string") return null;
  // Emptiness is judged on the trim; the value itself is kept verbatim, because
  // the screener filter must receive the label the server sent.
  return value.trim().length > 0 ? value : null;
}

/** A finite, non-negative number, from a number or a numeric string. */
function amount(value: unknown): number | null {
  if (typeof value === "number") return Number.isFinite(value) && value >= 0 ? value : null;
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  // An empty string is an absent field, not a zero: `Number("")` is 0.
  if (trimmed.length === 0) return null;
  const parsed = Number(trimmed);
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : null;
}

/** A missing average is `null`; a real `0` stays `0`. */
function signed(value: unknown): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  if (trimmed.length === 0) return null;
  const parsed = Number(trimmed);
  return Number.isFinite(parsed) ? parsed : null;
}

function readRow(value: unknown): GroupRow | null {
  if (!isRecord(value)) return null;
  const id = text(value["id"]) ?? text(value["label"]);
  if (id === null) return null;

  return {
    id,
    label: text(value["label"]) ?? id,
    count: amount(value["count"]),
    avgChange: signed(value["avg_change"]),
    adv: amount(value["adv"]),
    dec: amount(value["dec"]),
    unchanged: amount(value["unchanged"]),
    marketCap: amount(value["market_cap"]),
  };
}

/** Change descending; an unmeasured average sorts last rather than as zero. */
function byChangeDescending(left: GroupRow, right: GroupRow): number {
  if (left.avgChange === null && right.avgChange === null) return 0;
  if (left.avgChange === null) return 1;
  if (right.avgChange === null) return -1;
  return right.avgChange - left.avgChange;
}

export function readGroups(body: unknown): GroupsReport {
  if (!isRecord(body) || !Array.isArray(body["groups"])) {
    throw new GroupsMalformed("The payload carries no groups array.");
  }

  const rows = body["groups"]
    .map(readRow)
    .filter((row): row is GroupRow => row !== null)
    // `Array.prototype.sort` is stable, so equal averages keep server order.
    .sort(byChangeDescending);

  return {
    region: text(body["region"]) ?? GROUPS_REGION,
    universeTotal: amount(body["universe_total"]),
    rows,
  };
}

/**
 * The advancing share of the names that moved, in percent with one decimal.
 *
 * This is arithmetic over the two counts — no pixel is measured and no
 * denominator is invented. The rounding is stated: `400 / 700` is `57.1 %`,
 * which is what the counts support, and nothing finer.
 */
export function advanceShare(adv: number | null, dec: number | null): number {
  if (adv === null || dec === null) return 0;
  const movers = adv + dec;
  if (!(movers > 0)) return 0;
  return Math.round(barShare(adv, movers) * 10) / 10;
}

/** The spoken sentence behind a row's bar, so the split is not a drawing only. */
export function breadthLabel(row: GroupRow): string {
  if (row.adv === null || row.dec === null) {
    return `${row.label}: advancing and declining counts were not reported`;
  }
  if (row.adv + row.dec === 0) {
    return `${row.label}: no advancing or declining names`;
  }
  return `${row.label}: ${formatCount(row.adv)} advancing, ${formatCount(row.dec)} declining`;
}
