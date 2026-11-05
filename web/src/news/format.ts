/**
 * The news list's pure strings, and the one dangerous slot it owns.
 *
 * A headline arrives from a third-party RSS document and its `url` goes
 * straight into an `href`. `safeExternalHref` is the same rule the detail
 * panel's news tab applies (`detail/format.ts`): absolute `http(s)` only,
 * parsed so `JaVaScRiPt:` and a smuggled control character are refused rather
 * than canonicalised into something the browser will run. A refused URL is not
 * a dropped headline — the row still renders, as text.
 *
 * Everything else here is UTC on purpose. `published_at` is ISO-8601 UTC, the
 * day headings are UTC days, and the clock beside each headline is a UTC clock:
 * a list that grouped in the browser's local zone but printed UTC times would
 * put a 23:50 story under the next day's heading for half the world.
 */

const MONTHS = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
] as const;

const DAY_MS = 86_400_000;

/**
 * An href a browser may follow: absolute http(s) only.
 *
 * Returns `null` for anything else — `javascript:`, `data:`, `vbscript:`, a
 * relative path, a non-string. `new URL` lowercases and canonicalises the
 * scheme first, so `JaVaScRiPt:` is rejected, and returning the parsed `href`
 * means the anchor carries the canonical URL rather than the raw feed string.
 */
export function safeExternalHref(raw: unknown): string | null {
  if (typeof raw !== "string") return null;
  const trimmed = raw.trim();
  if (trimmed.length === 0) return null;
  // A control character inside a scheme is stripped by browsers before parsing;
  // reject instead of letting `java\tscript:` become executable.
  if (/[\u0000-\u001f\u007f]/.test(trimmed)) return null;
  let parsed: URL;
  try {
    parsed = new URL(trimmed);
  } catch {
    return null;
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return null;
  if (parsed.username || parsed.password) return null;
  return parsed.href;
}

/** `2026-10-31` → `31 Oct 2026`; an unparseable key is shown as written. */
export function formatDayLabel(key: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(key);
  if (!match) return key;
  const year = match[1] ?? "";
  const month = Number(match[2]);
  const day = Number(match[3]);
  const monthName = MONTHS[month - 1];
  if (!monthName || day < 1 || day > 31) return key;
  return `${day} ${monthName} ${year}`;
}

/**
 * The UTC day an instant falls on, or `null` when there is no usable instant.
 *
 * An ISO string with an offset is converted to UTC before the date is taken:
 * `2026-11-01T01:30:00+02:00` is `2026-10-31` in UTC, and that is the day the
 * grouping — and the clock beside it — will name.
 */
export function dayKeyOf(iso: string | null): string | null {
  if (iso === null || iso.trim().length === 0) return null;
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return null;
  return parsed.toISOString().slice(0, 10);
}

/** The UTC day before `key`, as `YYYY-MM-DD`; `key` unchanged when unparseable. */
export function previousDay(key: string): string {
  const parsed = new Date(`${key}T00:00:00Z`);
  if (Number.isNaN(parsed.getTime())) return key;
  return new Date(parsed.getTime() - DAY_MS).toISOString().slice(0, 10);
}

/** Today in UTC, as `YYYY-MM-DD`. */
export function todayUtc(now: number = Date.now()): string {
  return new Date(now).toISOString().slice(0, 10);
}

/**
 * The word over a day's rows: `Today`, `Yesterday`, or the date.
 *
 * `null` is the undated bucket and is never called a day — an item the feeds
 * did not stamp must not be filed under today.
 */
export function dayLabel(key: string | null, today: string): string {
  if (key === null) return "Undated";
  if (key === today) return "Today";
  if (key === previousDay(today)) return "Yesterday";
  return formatDayLabel(key);
}

export interface ItemClock {
  /** `09:21` — the UTC clock, matching the day headings. */
  readonly clock: string;
  /** `31 Oct 2026, 09:21 UTC` — the full instant, for the `title`. */
  readonly instant: string;
}

/**
 * The clock part of a feed timestamp, or `null` when there is no timestamp.
 *
 * `null` is the whole point: the row prints no time and the sort puts it last.
 * An invalid string is treated exactly like an absent one rather than being
 * printed as `Invalid Date`.
 */
export function itemClock(iso: string | null): ItemClock | null {
  if (iso === null || iso.trim().length === 0) return null;
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return null;
  const utc = parsed.toISOString();
  const date = utc.slice(0, 10);
  const clock = utc.slice(11, 16);
  return { clock, instant: `${formatDayLabel(date)}, ${clock} UTC` };
}

export interface FilterableItem {
  readonly headline: string;
  readonly symbols: readonly string[];
}

/**
 * Does an item answer the filter box? Headline or symbol, case-insensitive,
 * substring — the same text the chips print, so typing what is on screen
 * narrows the list.
 *
 * A blank query matches everything, which is what makes clearing the box a
 * restore rather than a special case.
 */
export function matchesQuery(item: FilterableItem, rawQuery: string): boolean {
  const query = rawQuery.trim().toLowerCase();
  if (query.length === 0) return true;
  if (item.headline.toLowerCase().includes(query)) return true;
  return item.symbols.some((symbol) => symbol.toLowerCase().includes(query));
}

/**
 * Human relative time: "2 hours ago", absolute instant in the title.
 * Returns null for undated items — no relative claim can be made.
 */
export function relativeTime(
  iso: string | null,
  now: number = Date.now(),
): { relative: string; absolute: string } | null {
  if (iso === null || iso.trim().length === 0) return null;
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return null;

  const ms = now - parsed.getTime();
  const seconds = Math.floor(ms / 1000);
  const minutes = Math.floor(seconds / 60);
  const hours = Math.floor(minutes / 60);
  const days = Math.floor(hours / 24);

  let relative: string;
  if (seconds < 60) relative = "just now";
  else if (minutes < 60) relative = `${minutes} minute${minutes !== 1 ? "s" : ""} ago`;
  else if (hours < 24) relative = `${hours} hour${hours !== 1 ? "s" : ""} ago`;
  else if (days < 7) relative = `${days} day${days !== 1 ? "s" : ""} ago`;
  else relative = formatDayLabel(dayKeyOf(iso) ?? "");

  const key = dayKeyOf(iso);
  const clock = iso.slice(11, 16);
  const absolute = key ? `${formatDayLabel(key)}, ${clock} UTC` : `${iso}`;

  return { relative, absolute };
}
