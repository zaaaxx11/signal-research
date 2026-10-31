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

import { SORTS, type ScreenSort } from "@/data/screens";
import type { FilterOp, FilterValue, ActiveFilter } from "./types";
import { makeFilter, toWire } from "./state";
import { resolveKey } from "./registry";
import type { FilterRegistry } from "./registry";
import { toFiniteNumber, validateDraft } from "./validation";

export interface ScreenState {
  view: string;
  sort: ScreenSort;
  /** Zero-based, the API's `criteria.offset / limit`; the URL spells it 1-based. */
  page: number;
  filters: ActiveFilter[];
}

export const DEFAULT_SORT: ScreenSort = "market_cap";

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
  return { view: "", sort: DEFAULT_SORT, page: 0, filters: [] };
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
  const sort = (SORTS as readonly string[]).includes(rawSort ?? "")
    ? (rawSort as ScreenSort)
    : DEFAULT_SORT;

  const rawPage = params.get("page");
  const requestedPage = rawPage === null ? 1 : Number(rawPage);
  // Clamped here, not trusted: the URL is the one input a stranger writes.
  const pageNumber = Number.isInteger(requestedPage)
    ? Math.min(Math.max(requestedPage, 1), MAX_PAGE)
    : 1;
  const page = pageNumber - 1;

  const rawView = params.get("view") ?? "";
  const view = viewNames.includes(rawView) ? rawView : "";

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
    if (!entry || !entry.ops.includes(op)) {
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
    state: { view, sort, page, filters },
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
