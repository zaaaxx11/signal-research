/**
 * The one filter state.
 *
 * A chip, the drawer's selection and the request body all read this array; none
 * of them keeps a copy. The id exists so a chip can be removed while two
 * filters compare the same key — the API ANDs them, and "remove the third one"
 * has to be answerable without guessing by value.
 *
 * **Two filters on one key are ANDed, never silently replaced.** Adding
 * `Market cap ≥ $2B` and then `Market cap < $10B` yields two chips and two
 * predicates; the API narrows. The single exception is a preset bucket: a
 * bucket row is a radio, so choosing another bucket in the same group replaces
 * that group's filters (`applyBucket`). That replacement is visible — the
 * previous chip disappears and the new one appears — which is exactly the
 * difference between a radio and a silent overwrite.
 */

import type { ActiveFilter, FilterOp, FilterValue, PresetBucket, PresetGroup, WireFilter } from "./types";
import type { FilterRegistry } from "./registry";

let sequence = 0;

/** Test seam: ids are session-local and only need to be unique, not stable. */
export function resetFilterIds(): void {
  sequence = 0;
}

export function nextFilterId(): string {
  sequence += 1;
  return `filter-${sequence}`;
}

export function makeFilter(key: string, op: FilterOp, value: FilterValue): ActiveFilter {
  return { id: nextFilterId(), key, op, value };
}

function sameValue(left: FilterValue, right: FilterValue): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}

export function sameFilter(left: { key: string; op: FilterOp; value: FilterValue }, right: { key: string; op: FilterOp; value: FilterValue }): boolean {
  return left.key === right.key && left.op === right.op && sameValue(left.value, right.value);
}

/** Append, unless the identical predicate is already on screen. */
export function appendFilter(filters: readonly ActiveFilter[], draft: WireFilter): ActiveFilter[] {
  if (filters.some((filter) => sameFilter(filter, draft))) return [...filters];
  return [...filters, makeFilter(draft.key, draft.op, draft.value)];
}

export function appendFilters(filters: readonly ActiveFilter[], drafts: readonly WireFilter[]): ActiveFilter[] {
  return drafts.reduce<ActiveFilter[]>((next, draft) => appendFilter(next, draft), [...filters]);
}

export function removeFilter(filters: readonly ActiveFilter[], id: string): ActiveFilter[] {
  return filters.filter((filter) => filter.id !== id);
}

export function clearFilters(): ActiveFilter[] {
  return [];
}

/** Remove every predicate a preset group owns, then add the chosen bucket. */
export function applyBucket(
  filters: readonly ActiveFilter[],
  group: PresetGroup,
  bucket: PresetBucket,
): ActiveFilter[] {
  const kept = filters.filter((filter) => filter.key !== group.key);
  return appendFilters(kept, bucket.filters);
}

/** Which bucket of a group is active, if the state is exactly one bucket. */
export function activeBucket(filters: readonly ActiveFilter[], group: PresetGroup): string | null {
  const onKey = filters.filter((filter) => filter.key === group.key);
  for (const bucket of group.buckets) {
    if (onKey.length !== bucket.filters.length) continue;
    const matches = bucket.filters.every((draft) =>
      onKey.some((filter) => sameFilter(filter, draft)),
    );
    if (matches) return bucket.id;
  }
  return null;
}

/** The exact array `criteria.filters` receives; ids never leave the client. */
export function toWire(filters: readonly ActiveFilter[]): WireFilter[] {
  return filters.map((filter) => ({ key: filter.key, op: filter.op, value: filter.value }));
}

/** Stable identity for effect dependencies and the ETag's query key. */
export function filtersSignature(filters: readonly ActiveFilter[]): string {
  return JSON.stringify(toWire(filters));
}

// ---------------------------------------------------------------------------
// Display
// ---------------------------------------------------------------------------

const SYMBOLS: Record<FilterOp, string> = {
  eq: "=",
  ne: "≠",
  gt: ">",
  gte: "≥",
  lt: "<",
  lte: "≤",
  between: "–",
  in: "in",
  contains: "contains",
};

/**
 * A number as short as it can be without merging two different values.
 *
 * Four decimal places is enough for money and ratios *until* two requests
 * differ in the fifth: `cap < 1_000_000_000` and `cap < 1_000_040_000` both
 * used to read "< $1B", so two chips asserted one predicate. The rule is:
 * render the 4-dp form when it is exact, otherwise keep six significant digits
 * — enough to separate 1 from 1.00004, short enough for a 12 px chip
 * ("$1.00004B" is 9 glyphs, and that is the longest compact form it can make).
 */
function trimNumber(value: number): string {
  if (!Number.isFinite(value)) return String(value);
  if (Number.isInteger(value)) return String(value);
  const rounded = Number(value.toFixed(4));
  if (rounded === value) return String(rounded);
  return String(Number(value.toPrecision(6)));
}

/** `2_000_000_000` → `$2B`, `500_000` → `500K`. */
export function compactNumber(value: number): string {
  const absolute = Math.abs(value);
  const sign = value < 0 ? "-" : "";
  if (absolute >= 1e12) return `${sign}${trimNumber(absolute / 1e12)}T`;
  if (absolute >= 1e9) return `${sign}${trimNumber(absolute / 1e9)}B`;
  if (absolute >= 1e6) return `${sign}${trimNumber(absolute / 1e6)}M`;
  if (absolute >= 1e3) return `${sign}${trimNumber(absolute / 1e3)}K`;
  return `${sign}${trimNumber(absolute)}`;
}

/** A value as the chip reads it, using the field's unit. */
export function formatValue(registry: FilterRegistry, key: string, value: FilterValue): string {
  const entry = registry.byKey.get(key);
  const unit = entry?.unit ?? "plain";
  const one = (item: unknown): string => {
    if (typeof item === "boolean") return item ? "yes" : "no";
    if (typeof item === "string") {
      // A choice key's value is a wire token (`1w10u`). The chip must name the
      // same option the drawer's `<select>` names, or the two controls describe
      // one request in two languages.
      const option = entry?.options.find((candidate) => candidate.token === item);
      return option?.label ?? item;
    }
    if (typeof item !== "number") return String(item);
    if (unit === "usd") return `$${compactNumber(item)}`;
    if (unit === "price") return `$${trimNumber(item)}`;
    if (unit === "compact") return compactNumber(item);
    if (unit === "percent") return `${trimNumber(item)}%`;
    return trimNumber(item);
  };
  if (Array.isArray(value)) return value.map((item) => one(item as number | string)).join(", ");
  return one(value);
}

export interface DescribedFilter {
  label: string;
  value: string;
  text: string;
}

/** The chip: human label, human value, and the one-line sentence. */
export function describeFilter(registry: FilterRegistry, filter: ActiveFilter): DescribedFilter {
  const entry = registry.byKey.get(filter.key);
  const label = entry?.label ?? filter.key;
  let value: string;
  if (filter.op === "between") {
    const bounds = Array.isArray(filter.value) ? filter.value : [];
    value = bounds
      .map((bound) => formatValue(registry, filter.key, bound as FilterValue))
      .join("–");
  } else if (filter.op === "contains") {
    value = `contains “${String(filter.value)}”`;
  } else {
    value = `${SYMBOLS[filter.op]} ${formatValue(registry, filter.key, filter.value)}`;
  }
  return { label, value, text: `${label} ${value}` };
}
