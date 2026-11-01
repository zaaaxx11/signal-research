/**
 * The filing document: `GET /engine/insider/{symbol}`.
 *
 * The ranked columns answer "how much did insiders net change, in shares, over
 * the period"; this document answers a different question — "which Form 4
 * filings are behind this company, and what does each one say". The two are
 * never merged: a filing has its own `filed` date and its own
 * `transaction_date`, and neither is the period the columns cover.
 *
 * The reader keeps every scalar the filing carries, in a stable order, and
 * gives it a label. It drops only what has no text form — a missing field, an
 * empty string, an object — because `undefined` and `[object Object]` are not
 * what the SEC said. In particular a price is printed only when the filing
 * reported one: shares times a missing price is not `$0.00`.
 */

import { formatMoney, formatNumber } from "@/grid/format";

import { dayText, safeHref } from "./format";

/** One labelled fact from a filing, already formatted as text. */
export interface InsiderField {
  /** The payload's own key, kept for `data-field` and for tests. */
  readonly key: string;
  readonly label: string;
  readonly text: string;
}

export interface InsiderFiling {
  /** Unique within the document: an accession can cover several transactions. */
  readonly key: string;
  readonly accession: string | null;
  readonly filed: string | null;
  readonly ownerName: string | null;
  readonly role: string | null;
  /** Absolute http(s) EDGAR URL, or null when the payload carried none. */
  readonly url: string | null;
  /** Every other scalar the filing carried, formatted, in a stable order. */
  readonly fields: readonly InsiderField[];
}

export interface InsiderFilings {
  readonly symbol: string;
  readonly asOf: string | null;
  /** The endpoint's own row count; null when it did not state one. */
  readonly count: number | null;
  readonly items: readonly InsiderFiling[];
  /** Filing bodies that could not be read. Rendered, never swallowed. */
  readonly providerErrors: readonly string[];
}

/** The payload is not a filing document. Distinct from a transport failure. */
export class FilingsMalformed extends Error {
  constructor(message: string) {
    super(message);
    this.name = "FilingsMalformed";
  }
}

export function filingsPath(symbol: string): string {
  return `/engine/insider/${encodeURIComponent(symbol)}`;
}

/**
 * Header keys are rendered by the panel itself (filed date, owner, role, the
 * accession link), so they are not repeated as facts.
 */
const HEADER_KEYS: ReadonlySet<string> = new Set([
  "accession",
  "filed",
  "owner_name",
  "role",
  "url",
]);

/** The order the facts are printed in, when the filing carries them. */
const FIELD_ORDER: readonly string[] = [
  "owner_cik",
  "period_of_report",
  "security",
  "transaction_date",
  "code",
  "shares",
  "price",
  "acquired_disposed",
  "shares_after",
];

const LABELS: Readonly<Record<string, string>> = {
  accession: "Accession",
  filed: "Filed",
  period_of_report: "Period of report",
  owner_name: "Owner",
  owner_cik: "Owner CIK",
  role: "Role",
  security: "Security",
  transaction_date: "Transaction date",
  code: "Code",
  shares: "Shares",
  price: "Price",
  acquired_disposed: "Acquired / disposed",
  shares_after: "Shares after",
  url: "Filing URL",
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function text(value: unknown): string | null {
  if (typeof value !== "string") return null;
  return value.trim().length > 0 ? value.trim() : null;
}

function whole(value: unknown): number | null {
  if (typeof value !== "number" || !Number.isFinite(value)) return null;
  return Number.isInteger(value) && value >= 0 ? value : null;
}

/**
 * One value as text. Numbers keep the payload's digits: `price` is money, an
 * integral count is grouped, anything else prints two decimals. Never a `$`
 * on a field the payload did not report as a price.
 */
function fieldText(key: string, value: unknown): string | null {
  if (typeof value === "string") {
    const trimmed = value.trim();
    return trimmed.length > 0 ? trimmed : null;
  }
  if (typeof value === "number") {
    if (!Number.isFinite(value)) return null;
    if (key === "price") return formatMoney(value);
    return formatNumber(value, Number.isInteger(value) ? 0 : 2);
  }
  if (typeof value === "boolean") return value ? "yes" : "no";
  // An object or an array has no faithful text form. Printing JSON would be a
  // shape the reader invented, not a field the filing carried.
  return null;
}

function labelOf(key: string): string {
  const known = LABELS[key];
  if (known) return known;
  const spaced = key.replace(/[_-]+/g, " ").trim();
  if (spaced.length === 0) return key;
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

function fieldsOf(record: Record<string, unknown>): InsiderField[] {
  const fields: InsiderField[] = [];
  const seen = new Set<string>();

  const push = (key: string) => {
    if (seen.has(key) || HEADER_KEYS.has(key)) return;
    const value = fieldText(key, record[key]);
    if (value === null) return;
    seen.add(key);
    fields.push({ key, label: labelOf(key), text: value });
  };

  for (const key of FIELD_ORDER) push(key);
  // Whatever else the payload carried, in its own order. A field added to the
  // contract later shows up without a code change here.
  for (const key of Object.keys(record)) push(key);

  return fields;
}

function readFiling(record: Record<string, unknown>, index: number): InsiderFiling {
  const accession = text(record["accession"]);
  return {
    key: `${accession ?? "filing"}:${index}`,
    accession,
    filed: dayText(record["filed"]),
    ownerName: text(record["owner_name"]),
    role: text(record["role"]),
    url: safeHref(record["url"]),
    fields: fieldsOf(record),
  };
}

export function readFilings(body: unknown): InsiderFilings {
  if (!isRecord(body) || !Array.isArray(body["items"])) {
    throw new FilingsMalformed("The payload carries no filings array.");
  }

  const items = body["items"]
    .filter(isRecord)
    .map((record, index) => readFiling(record, index));

  const errors = Array.isArray(body["provider_errors"])
    ? body["provider_errors"]
        .map((error) => (typeof error === "string" ? error.trim() : ""))
        .filter((error) => error.length > 0)
    : [];

  return {
    symbol: text(body["symbol"]) ?? "",
    asOf: dayText(body["as_of"]),
    count: whole(body["count"]),
    items,
    providerErrors: errors,
  };
}
