/**
 * The screen in the query string.
 *
 * A screen is four things — view, sort, page, filters — and all four travel:
 * `?view=momentum&sort=gainers&page=2&f=cap:gte:2000000000&f=sec:in:%5B%22Technology%22%5D`.
 * The filter spelling is deliberately readable (`key:op:value`) so a link can
 * be diagnosed by eye; the value is the API's own JSON scalar, and `in` /
 * `between` are JSON arrays.
 *
 * Parsing is **validated against the live registry**, not trusted. A key the
 * schema no longer offers, an operator the field does not take, a reversed
 * range, a non-number — each is dropped and reported by name. The screen still
 * loads, unfiltered on that one predicate, and says what it ignored. A shared
 * link must never be able to 422 the panel in a loop.
 */

import type { FilterOp, FilterValue, ActiveFilter } from "./types";
import { makeFilter, toWire } from "./state";
import { resolveKey } from "./registry";
import type { FilterRegistry, PublishedFilter } from "./registry";
import { toFiniteNumber, validateDraft } from "./validation";

export interface ScreenState {
  view: string;
  /** A catalogue key, or `-key` for lowest-first; see `data/screens`. */
  sort: string;
  /** Zero-based, the API's `criteria.offset / limit`; the URL spells it 1-based. */
  page: number;
  filters: ActiveFilter[];
  /**
   * The named screen the request asks the API to compile, or null.
   *
   * Optional in the type because a state built by a caller (a test, a preset)
   * may simply not have one; every reader treats absence as "no signal". It is
   * in the URL (`?signal=top-gainers`) because a reload must reproduce the
   * screen, and a signal changes which rows exist.
   */
  signal?: string | null;
}

export const DEFAULT_SORT = "market_cap";

/**
 * The largest page a URL may name, 1-based.
 *
 * `page` is user-controlled and shared in links. Without a bound,
 * `?page=999999999999999` was multiplied into an offset of 999 999 999 999 998
 * within the JS safe-integer range, the API answered `200` with zero rows, the
 * pager rendered "999999999999999/280" and the poison was written back into
 * the address bar — a link you could not recover from. 10 000 pages is 200 000
 * rows at the panel's 20-row page: far past anything this store holds, and
 * small enough that `page × limit` is an exact, sane integer. The panel also
 * reconciles the page against the answer's `total` once it knows it.
 */
export const MAX_PAGE = 10_000;

export function emptyScreenState(): ScreenState {
  return { view: "", sort: DEFAULT_SORT, page: 0, filters: [], signal: null };
}

function encodeValue(value: FilterValue): string {
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}

/** The query string (no leading `?`). */
export function encodeScreenState(state: ScreenState): string {
  const params = new URLSearchParams();
  if (state.view) params.set("view", state.view);
  params.set("sort", state.sort);
  params.set("page", String(state.page + 1));
  if (state.signal) params.set("signal", state.signal);
  for (const filter of state.filters) {
    params.append("f", `${filter.key}:${filter.op}:${encodeValue(filter.value)}`);
  }
  return params.toString();
}

function splitFilter(entry: string): { key: string; op: string; raw: string } | null {
  const first = entry.indexOf(":");
  if (first <= 0) return null;
  const second = entry.indexOf(":", first + 1);
  if (second < 0) return null;
  return {
    key: entry.slice(0, first),
    op: entry.slice(first + 1, second),
    raw: entry.slice(second + 1),
  };
}

function parseNumberList(raw: string): unknown {
  try {
    return JSON.parse(raw) as unknown;
  } catch {
    return null;
  }
}

function coerce(registry: FilterRegistry, key: string, op: FilterOp, raw: string): unknown {
  const entry = registry.byKey.get(key);
  if (!entry) return undefined;

  if (op === "in" || op === "between") {
    const parsed = parseNumberList(raw);
    return parsed ?? undefined;
  }

  if (entry.kind === "number") return toFiniteNumber(raw) ?? undefined;
  if (entry.kind === "bool") {
    if (raw === "true") return true;
    if (raw === "false") return false;
    return undefined;
  }
  return raw;
}

export interface ParsedScreenState {
  state: ScreenState;
  /** Filter spellings the link carried that this build cannot apply. */
  dropped: string[];
}

/**
 * A value the panel document publishes for one of its own keys.
 *
 * `in` is a JSON array on the wire and every member must be published; a single
 * value is the API's token. Anything else is `undefined`, which the caller
 * treats exactly like a key it does not know.
 */
function publishedValue(
  entry: PublishedFilter,
  op: FilterOp,
  raw: string,
): FilterValue | undefined {
  if (op === "in") {
    const parsed = parseNumberList(raw);
    if (!Array.isArray(parsed) || parsed.length === 0) return undefined;
    const values = parsed.filter(
      (item): item is string => typeof item === "string" && entry.values.has(item),
    );
    return values.length === parsed.length ? values : undefined;
  }
  return entry.values.has(raw) ? raw : undefined;
}

export function parseScreenState(
  search: string,
  registry: FilterRegistry,
  viewNames: readonly string[],
): ParsedScreenState {
  const query = search.includes("?") ? search.slice(search.indexOf("?") + 1) : search.replace(/^\?/, "");
  const params = new URLSearchParams(query);
  const dropped: string[] = [];
  const filters: ActiveFilter[] = [];

  const rawSort = params.get("sort");
  const sort =
    rawSort !== null && /^-?[a-z0-9][a-z0-9_]{0,39}$/i.test(rawSort) ? rawSort : DEFAULT_SORT;

  const rawPage = params.get("page");
  const requestedPage = rawPage === null ? 1 : Number(rawPage);
  // Clamped here, not trusted: the URL is the one input a stranger writes.
  const pageNumber = Number.isInteger(requestedPage)
    ? Math.min(Math.max(requestedPage, 1), MAX_PAGE)
    : 1;
  const page = pageNumber - 1;

  const rawView = params.get("view") ?? "";
  const view = viewNames.includes(rawView) ? rawView : "";

  // A signal id is a short slug (`top-gainers`, `unusual-volume`). The shape is
  // checked, not the vocabulary: this parse runs before the document that
  // publishes the valid ids has arrived, and a link naming a signal this build
  // does not know is the API's 403/422 to refuse, shown as a message — never a
  // silent "no signal" that would answer a different screen than the link says.
  const rawSignal = params.get("signal") ?? "";
  const signal = /^[a-z0-9][a-z0-9._-]{0,63}$/i.test(rawSignal) ? rawSignal : null;

  const seen = new Set<string>();
  const added = new Set<string>();
  for (const spelling of params.getAll("f")) {
    const parts = splitFilter(spelling);
    if (!parts) {
      dropped.push(spelling);
      continue;
    }

    const key = resolveKey(registry, parts.key);
    if (key === null) {
      if (!seen.has(parts.key)) {
        seen.add(parts.key);
        dropped.push(parts.key);
      }
      continue;
    }

    const entry = registry.byKey.get(key);
    const op = parts.op as FilterOp;

    // A value the panel document publishes is the value its select wrote, even
    // when the schema describes the same key as a number (`cap` is a numeric
    // key whose options are the words `mega`, `micro`…). The document is the
    // surface that offered the choice, so it is the surface that recognises it
    // on the way back — otherwise every option the header writes would be
    // dropped the moment the reader reloaded the link.
    const published = registry.published.get(key);
    const publishedToken =
      published && published.op === op ? publishedValue(published, op, parts.raw) : undefined;
    if (publishedToken !== undefined) {
      const signature = JSON.stringify([key, op, publishedToken]);
      if (!added.has(signature)) {
        added.add(signature);
        filters.push(makeFilter(key, op, publishedToken));
      }
      continue;
    }

    if (!entry) {
      if (!seen.has(key)) {
        seen.add(key);
        dropped.push(key);
      }
      continue;
    }
    if (!entry.ops.includes(op)) {
      if (!seen.has(key)) {
        seen.add(key);
        dropped.push(key);
      }
      continue;
    }

    const value = coerce(registry, key, op, parts.raw);
    const result = validateDraft(registry, { key, op, value });
    if (!result.ok) {
      if (!seen.has(key)) {
        seen.add(key);
        dropped.push(key);
      }
      continue;
    }

    const signature = JSON.stringify([result.key, result.op, result.value]);
    if (added.has(signature)) continue;
    added.add(signature);
    filters.push(makeFilter(result.key, result.op, result.value));
  }
  return {
    state: { view, sort, page, filters, signal },
    dropped,
  };
}

/** The sentence the shell shows for ignored link state. */
export function droppedNotice(dropped: readonly string[]): string | null {
  if (dropped.length === 0) return null;
  const list = dropped.join(", ");
  if (dropped.length === 1) {
    return `Ignored 1 filter from this link: ${list} — this build cannot apply it.`;
  }
  return `Ignored ${dropped.length} filters from this link: ${list} — this build cannot apply them.`;
}

/** The wire body's filter list, for a test that compares a URL to a request. */
export function wireFilters(state: ScreenState): ReturnType<typeof toWire> {
  return toWire(state.filters);
}
