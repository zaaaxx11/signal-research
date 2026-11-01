/**
 * The market news document, read once and grouped for reading.
 *
 * `GET /market/news?limit=60` is one consolidated feed: the largest index funds
 * and companies, newest first, already deduped by URL on the server because the
 * same story arrives on several providers. This module therefore **renders
 * what it is given and dedupes nothing**: two rows with the same headline and
 * different URLs are two stories, and the row count equals `items.length`. A
 * client that "cleaned up" the list would make the count a lie and could hide a
 * real second report.
 *
 * Two ordering rules the page depends on and cannot rediscover per render:
 *
 * * newest first, by the instant the feeds stamped;
 * * no timestamp is last, in the `Undated` bucket, never under today.
 *
 * Grouping is a pure function of the sorted rows and the day the page considers
 * today, so a pinned `today` in a test and the real clock in the app run the
 * same code.
 */

import { dayKeyOf, dayLabel, matchesQuery, type FilterableItem } from "./format";

/** The page's one request: sixty headlines is the endpoint's documented cap. */
export const NEWS_LIMIT = 60;

/** The route this page reads. Same-origin and root-absolute, like every API. */
export function newsPath(limit: number = NEWS_LIMIT): string {
  return `/market/news?limit=${limit}`;
}

export interface NewsItem extends FilterableItem {
  readonly headline: string;
  readonly url: string;
  readonly source: string;
  readonly publishedAt: string | null;
  readonly symbols: readonly string[];
}

export interface NewsGroup {
  /** `YYYY-MM-DD` in UTC, or `null` for the undated bucket. */
  readonly key: string | null;
  /** `Today`, `Yesterday`, `31 Oct 2026`, or `Undated`. */
  readonly label: string;
  readonly items: readonly NewsItem[];
}

export interface NewsReport {
  readonly asOf: string;
  /** The server's count of the document's rows, not a number we recomputed. */
  readonly count: number;
  readonly symbols: readonly string[];
  /** Providers that failed; named only as a number in the UI. */
  readonly providerErrors: readonly unknown[];
  readonly items: readonly NewsItem[];
}

/** A payload the page cannot read. Distinguished from a transport failure. */
export class NewsMalformed extends Error {
  constructor(message = "The news payload was not the expected document") {
    super(message);
    this.name = "NewsMalformed";
  }
}

function stringOr(value: unknown, fallback: string): string {
  return typeof value === "string" ? value : fallback;
}

function symbolsOf(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  const seen = new Set<string>();
  const symbols: string[] = [];
  for (const entry of value) {
    if (typeof entry !== "string") continue;
    const symbol = entry.trim();
    if (symbol.length === 0 || seen.has(symbol)) continue;
    seen.add(symbol);
    symbols.push(symbol);
  }
  return symbols;
}

function itemOf(value: unknown): NewsItem | null {
  if (typeof value !== "object" || value === null) return null;
  const record = value as Record<string, unknown>;
  if (typeof record["headline"] !== "string") return null;
  const headline = record["headline"].trim();
  if (headline.length === 0) return null;

  const raw = record["published_at"];
  const publishedAt = typeof raw === "string" && raw.trim().length > 0 ? raw.trim() : null;

  return {
    headline,
    url: stringOr(record["url"], ""),
    source: stringOr(record["source"], "").trim(),
    publishedAt,
    symbols: symbolsOf(record["symbols"]),
  };
}

/**
 * The instant a row sorts by. Absent, empty and unparseable all become
 * `-Infinity`, which is how "no timestamp sorts last" is enforced once here
 * instead of being repeated — or forgotten — by every reader.
 */
function instantOf(item: NewsItem): number {
  if (item.publishedAt === null) return Number.NEGATIVE_INFINITY;
  const parsed = Date.parse(item.publishedAt);
  return Number.isFinite(parsed) ? parsed : Number.NEGATIVE_INFINITY;
}

/**
 * Newest first, undated last, ties keeping the payload's order.
 *
 * `Array.prototype.sort` is stable in ES2019+, and the index tiebreak makes
 * that explicit rather than assumed: two headlines stamped the same second keep
 * the order the feed sent them in, which is the only order we have evidence
 * for.
 */
export function sortNewestFirst(items: readonly NewsItem[]): NewsItem[] {
  return items
    .map((item, index) => ({ item, index, instant: instantOf(item) }))
    .sort((left, right) => {
      if (left.instant !== right.instant) return right.instant > left.instant ? 1 : -1;
      return left.index - right.index;
    })
    .map((entry) => entry.item);
}

/**
 * The server's document, validated and sorted.
 *
 * A non-object or a missing `items` array is a malformed payload, not an empty
 * feed: an empty list has to mean the feeds answered with nothing, or the
 * empty state would hide a broken deploy. Rows without a headline are dropped
 * because there is nothing to read or link; every readable row is kept,
 * including duplicates.
 */
export function readNews(value: unknown): NewsReport {
  if (typeof value !== "object" || value === null) {
    throw new NewsMalformed("The news payload was not an object");
  }
  const record = value as Record<string, unknown>;
  const rawItems = record["items"];
  if (!Array.isArray(rawItems)) {
    throw new NewsMalformed("The news payload had no items array");
  }

  const items: NewsItem[] = [];
  for (const entry of rawItems) {
    const item = itemOf(entry);
    if (item !== null) items.push(item);
  }

  const rawCount = record["count"];
  const count =
    typeof rawCount === "number" && Number.isFinite(rawCount) ? rawCount : items.length;
  const rawSymbols = record["symbols"];
  const symbols = Array.isArray(rawSymbols)
    ? rawSymbols.filter((symbol): symbol is string => typeof symbol === "string")
    : [];
  const rawErrors = record["provider_errors"];

  return {
    asOf: stringOr(record["as_of"], ""),
    count,
    symbols,
    providerErrors: Array.isArray(rawErrors) ? rawErrors : [],
    items: sortNewestFirst(items),
  };
}

/**
 * Rows under day headings, in the order they arrived (already newest first).
 *
 * Days are UTC days, so the heading, the printed clock and the sort agree. The
 * undated bucket is appended last and labelled `Undated`; it is not a day and
 * is deliberately never labelled `Today`. A day with a single row still gets a
 * heading — the heading is the date, not a separator between items.
 */
export function groupByDay(items: readonly NewsItem[], today: string): NewsGroup[] {
  const buckets = new Map<string | null, NewsItem[]>();
  const order: (string | null)[] = [];

  for (const item of items) {
    const key = dayKeyOf(item.publishedAt);
    const bucket = buckets.get(key);
    if (bucket) {
      bucket.push(item);
      continue;
    }
    buckets.set(key, [item]);
    order.push(key);
  }

  const dated: NewsGroup[] = [];
  let undated: NewsGroup | null = null;
  for (const key of order) {
    const bucket = buckets.get(key);
    if (!bucket) continue;
    const group: NewsGroup = { key, label: dayLabel(key, today), items: bucket };
    if (key === null) undated = group;
    else dated.push(group);
  }
  if (undated !== null) dated.push(undated);
  return dated;
}

/** The rows the filter box keeps, in reading order. */
export function filterItems(items: readonly NewsItem[], query: string): NewsItem[] {
  if (query.trim().length === 0) return [...items];
  return items.filter((item) => matchesQuery(item, query));
}
