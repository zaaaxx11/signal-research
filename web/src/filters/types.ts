/**
 * The vocabulary the filter surface shares.
 *
 * `FilterOp` is the API's operator set, copied once. `ActiveFilter` is the one
 * state a chip, the drawer and the request body all read — there is no second
 * copy of "what is filtered" anywhere in this app.
 */

/** `app/screen/filters.py:OPERATORS`, in the order the UI offers them. */
export const FILTER_OPS = [
  "eq",
  "ne",
  "gt",
  "gte",
  "lt",
  "lte",
  "between",
  "in",
  "contains",
] as const;

export type FilterOp = (typeof FILTER_OPS)[number];

/** A value the API accepts for one operator (arrays for `in`/`between`). */
export type FilterValue = number | string | boolean | number[] | string[] | boolean[];

/** One filter on screen: the id is local to the session, never sent. */
export interface ActiveFilter {
  id: string;
  key: string;
  op: FilterOp;
  value: FilterValue;
}

/** One predicate as `criteria.filters` takes it. */
export interface WireFilter {
  key: string;
  op: FilterOp;
  value: FilterValue;
}

/** One clickable bucket, e.g. Market Cap → "$2–10B". */
export interface PresetBucket {
  id: string;
  label: string;
  /** One bucket may need two predicates to express a half-open range. */
  filters: WireFilter[];
  /**
   * True when the bucket names a sub-population of a band rather than a band of
   * its own — `Pays nothing (0.0)` sits inside `Under 1% (reported)`. Detail
   * buckets are excluded from the "adjacent bands leave no gap" proof, because
   * they are supposed to overlap.
   */
  detail?: boolean;
}

export interface PresetGroup {
  /** The Finviz key the buckets compare. */
  key: string;
  label: string;
  /** `usd`/`price`/`compact`/`percent`/`ratio` drive chip formatting. */
  unit: Unit;
  buckets: PresetBucket[];
  /** The first eight groups stay visible; the rest sit behind a disclosure. */
  primary: boolean;
  /**
   * One sentence under the group's header, for a choice the labels cannot carry
   * on their own — which population a bucket is *not* counting, and why.
   */
  hint?: string;
}

export type Unit = "usd" | "price" | "compact" | "percent" | "ratio" | "plain";

/** The presentation facts a control needs, derived from the schema entry. */
export type ControlType = "buckets" | "number" | "text" | "boolean" | "choice";
