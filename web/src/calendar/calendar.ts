/**
 * The calendar document: `GET /market/calendar?region=us&days=21`.
 *
 * A day bucket is a calendar day, not a timestamp: `date` is `YYYY-MM-DD` and
 * it is read, compared and printed in UTC. Parsing it with the local zone would
 * move a date by one for everyone west of Greenwich, and a Monday earnings
 * report filed on Tuesday is a different page.
 *
 * Three things this module refuses to do:
 *
 * * **Draw a day that has already happened.** A server may include the whole
 *   scan window rather than the forward one; a past date is not a schedule.
 * * **Draw a day with no names.** `count === 0` is a section with nothing under
 *   it — it is dropped, and the page says the window is empty instead.
 * * **Confuse the sample with the total.** `count` is every report the server
 *   counted; `symbols` is at most fifty of them. The list is capped in
 *   `shownSymbols` and the remainder is arithmetic over the true total, so
 *   "+87 more" means eighty-seven reports that exist, not rows that were cut.
 *
 * Within those rules it is deliberately forgiving: one unreadable bucket is
 * dropped, not turned into a page-wide failure, and a repeated date keeps its
 * first bucket rather than producing two sections with the same anchor.
 */

import { formatCount } from "@/breadth/format";

export const CALENDAR_REGION = "us";
/** The forward window the page asks for, in days. */
export const CALENDAR_DAYS = 21;
/** The contract's per-bucket sample. `count` is the true total. */
export const CALENDAR_CAP = 50;

export interface CalendarSymbol {
  readonly s: string;
  readonly n: string;
  /** Market cap in USD, or null when the store carries no cap for this symbol. */
  readonly c: number | null;
}

export interface CalendarBucket {
  readonly date: string;
  /** Every report on this day; `symbols` is at most `CALENDAR_CAP` of them. */
  readonly count: number;
  readonly symbols: readonly CalendarSymbol[];
}

export interface CalendarReport {
  readonly region: string;
  readonly from: string | null;
  readonly to: string | null;
  /** Forward in time; past, empty and out-of-window days are already gone. */
  readonly buckets: readonly CalendarBucket[];
  /** ``false`` when the server does not have a source for this calendar kind. */
  readonly available: boolean;
  /** The reason when ``available`` is false. */
  readonly reason: string | null;
}

/** The payload is not a calendar document. Distinct from a transport failure. */
export class CalendarMalformed extends Error {
  constructor(message: string) {
    super(message);
    this.name = "CalendarMalformed";
  }
}

export type CalendarKind = "earnings" | "dividends" | "economic";

export const CALENDAR_KINDS: readonly CalendarKind[] = [
  "earnings",
  "dividends",
  "economic",
];

/** The one GET: the region, the window, and the kind, all named. */
export function calendarPath(
  region: string = CALENDAR_REGION,
  days: number = CALENDAR_DAYS,
  kind: CalendarKind = "earnings",
): string {
  const params = new URLSearchParams({ region, days: String(days), kind });
  return `/market/calendar?${params.toString()}`;
}

/** Today as a calendar date in UTC. */
export function todayUtc(now: Date = new Date()): string {
  return now.toISOString().slice(0, 10);
}

const DATE = /^(\d{4})-(\d{2})-(\d{2})$/;

/**
 * A real `YYYY-MM-DD` date: the shape *and* the month, so `2026-13-40` is not
 * silently read as February.
 */
export function isDateString(value: unknown): value is string {
  if (typeof value !== "string") return false;
  const match = DATE.exec(value);
  if (!match) return false;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const parsed = new Date(Date.UTC(year, month - 1, day));
  return parsed.toISOString().slice(0, 10) === value;
}

const DAY_LABEL = new Intl.DateTimeFormat("en-GB", {
  weekday: "short",
  day: "numeric",
  month: "short",
  timeZone: "UTC",
});

/**
 * `2026-11-02` → `Mon 2 Nov`, in UTC.
 *
 * A string that is not a date is returned as it arrived: a label we cannot parse
 * is shown, never replaced by today's date.
 */
export function dayLabel(date: string): string {
  if (!isDateString(date)) return date;
  return DAY_LABEL.format(new Date(`${date}T00:00:00Z`));
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function text(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : null;
}

function amount(value: unknown): number | null {
  if (typeof value === "number") {
    return Number.isFinite(value) && value >= 0 ? Math.trunc(value) : null;
  }
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  // An empty string is an absent count, not a zero: `Number("")` is 0.
  if (trimmed.length === 0) return null;
  const parsed = Number(trimmed);
  return Number.isFinite(parsed) && parsed >= 0 ? Math.trunc(parsed) : null;
}

function readSymbols(value: unknown): CalendarSymbol[] {
  if (!Array.isArray(value)) return [];
  const symbols: CalendarSymbol[] = [];
  for (const entry of value) {
    if (!isRecord(entry)) continue;
    const raw = entry["s"];
    // A ticker is a path segment and a link; surrounding space is not part of
    // it, and a blank one is no symbol at all.
    const symbol = typeof raw === "string" ? raw.trim() : "";
    if (symbol.length === 0) continue;
    const name = typeof entry["n"] === "string" ? entry["n"].trim() : "";
    const capRaw = entry["c"];
    const cap = typeof capRaw === "number" && Number.isFinite(capRaw) && capRaw > 0 ? capRaw : null;
    symbols.push({ s: symbol, n: name, c: cap });
  }
  return symbols;
}

export interface CalendarReadOptions {
  /** Today as `YYYY-MM-DD`, UTC. Injected so the window is testable. */
  readonly today: string;
}

export function readCalendar(body: unknown, options: CalendarReadOptions): CalendarReport {
  if (!isRecord(body) || !Array.isArray(body["buckets"])) {
    throw new CalendarMalformed("The payload carries no buckets array.");
  }

  const today = isDateString(options.today) ? options.today : todayUtc();
  const from = isDateString(body["from"]) ? body["from"] : null;
  const to = isDateString(body["to"]) ? body["to"] : null;

  const seen = new Set<string>();
  const buckets: CalendarBucket[] = [];

  for (const entry of body["buckets"]) {
    if (!isRecord(entry)) continue;
    const date = entry["date"];
    if (!isDateString(date)) continue;
    // A day that has passed is not a schedule, and a day beyond the window the
    // server itself named is not in this calendar.
    if (date < today) continue;
    if (to !== null && date > to) continue;

    const count = amount(entry["count"]);
    if (count === null || count === 0) continue;
    const symbols = readSymbols(entry["symbols"]);
    if (symbols.length === 0) continue;
    if (seen.has(date)) continue;

    seen.add(date);
    buckets.push({ date, count, symbols });
  }

  buckets.sort((left, right) => (left.date < right.date ? -1 : left.date > right.date ? 1 : 0));

  return {
    region: text(body["region"]) ?? CALENDAR_REGION,
    from,
    to,
    buckets,
    available: typeof body["available"] === "boolean" ? body["available"] : true,
    reason: text(body["reason"]),
  };
}

/**
 * The symbols a bucket actually lists: at most fifty, and never more rows than
 * the count printed in its heading.
 */
export function shownSymbols(bucket: CalendarBucket): CalendarSymbol[] {
  const limit = Math.min(bucket.symbols.length, CALENDAR_CAP, bucket.count);
  return bucket.symbols.slice(0, Math.max(0, limit));
}

/** Reports the day holds that it does not list. Zero when the sample is the whole day. */
export function overflowCount(bucket: CalendarBucket): number {
  return Math.max(0, bucket.count - shownSymbols(bucket).length);
}

/** `42 reports`, `1 report`. */
export function reportLabel(count: number): string {
  return `${formatCount(count)} ${count === 1 ? "report" : "reports"}`;
}
