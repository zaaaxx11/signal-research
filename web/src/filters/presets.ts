/**
 * Preset buckets for the numeric keys a screener reaches for first.
 *
 * The point of a bucket is that it is one click and needs no Apply: choosing
 * "$2–10B" *is* the request. Every bucket therefore states the API's own
 * operator and value — never Finviz's display token (`mega`, `u5`), which the
 * API does not translate (app/screen/filter_fields.py says so by name).
 *
 * Three rules make a label and its request mean the same thing:
 *
 * **A band is half-open, and two predicates to say so.** The API's `between` is
 * inclusive on both ends, so `$2–10B` is `>= 2e9` AND `< 10e9`: exactly $2B
 * belongs to one bucket and not to two. The four market-cap buckets are then an
 * exact partition of the universe (3,472 + 1,126 + 879 + 99 = 5,576).
 *
 * **A boundary is inclusive, so the label says "and up".** `gte` matches a row
 * sitting exactly on the bound. The top bucket of every group is therefore
 * written "$200B and up" / "40 and up" rather than "Over $200B" / "Over 40":
 * "over 40" reads as strict, and a stock priced at exactly 40 would then be in
 * no bucket at all (the band below stops at `< 40`). Phase 3's F9 found the
 * mismatch latent — no live row sits on an `Over` bound today — and the fix is
 * to name the inclusive side rather than to introduce a one-point hole.
 *
 * **"Under X" starts at zero, not at minus infinity.** A negative P/E is a
 * company without earnings; it is not a cheap multiple. Every P/E bucket
 * therefore carries `pe >= 0` where it would otherwise reach below zero, and
 * the bucket that did (`Under 10`) says so in its label. The cost is stated:
 * 1,367 loss-makers drop out of "cheap P/E", which is what the label always
 * promised. The same rule as a hint: `P/E ≥ 0` is on every chip the bucket
 * writes, so the guard is visible in the answer, not buried here.
 *
 * A bucket may also be a `detail`: a sub-population inside a band, like
 * `Pays nothing (0.0)` inside `Under 1% (reported)`. Detail buckets overlap
 * their band on purpose and are excluded from the partition proof.
 */

import type { PresetBucket, PresetGroup, Unit } from "./types";

/** A billion, a million and a thousand, spelled once. */
const B = 1_000_000_000;
const M = 1_000_000;
const K = 1_000;

export const PRESETS: PresetGroup[] = [
  {
    key: "cap",
    label: "Market cap",
    unit: "usd",
    primary: true,
    buckets: [
      { id: "micro", label: "Under $2B", filters: [{ key: "cap", op: "lt", value: 2 * B }] },
      {
        id: "small",
        label: "$2–10B",
        filters: [
          { key: "cap", op: "gte", value: 2 * B },
          { key: "cap", op: "lt", value: 10 * B },
        ],
      },
      {
        id: "mid",
        label: "$10–200B",
        filters: [
          { key: "cap", op: "gte", value: 10 * B },
          { key: "cap", op: "lt", value: 200 * B },
        ],
      },
      { id: "mega", label: "$200B and up", filters: [{ key: "cap", op: "gte", value: 200 * B }] },
    ],
  },
  {
    key: "sh_price",
    label: "Price",
    unit: "price",
    primary: true,
    buckets: [
      { id: "u5", label: "Under $5", filters: [{ key: "sh_price", op: "lt", value: 5 }] },
      {
        id: "5-20",
        label: "$5–20",
        filters: [
          { key: "sh_price", op: "gte", value: 5 },
          { key: "sh_price", op: "lt", value: 20 },
        ],
      },
      {
        id: "20-100",
        label: "$20–100",
        filters: [
          { key: "sh_price", op: "gte", value: 20 },
          { key: "sh_price", op: "lt", value: 100 },
        ],
      },
      { id: "o100", label: "$100 and up", filters: [{ key: "sh_price", op: "gte", value: 100 }] },
    ],
  },
  {
    key: "sh_curvol",
    label: "Volume today",
    unit: "compact",
    primary: true,
    buckets: [
      { id: "u500k", label: "Under 500K", filters: [{ key: "sh_curvol", op: "lt", value: 500 * K }] },
      {
        id: "500k-2m",
        label: "500K–2M",
        filters: [
          { key: "sh_curvol", op: "gte", value: 500 * K },
          { key: "sh_curvol", op: "lt", value: 2 * M },
        ],
      },
      {
        id: "2m-10m",
        label: "2M–10M",
        filters: [
          { key: "sh_curvol", op: "gte", value: 2 * M },
          { key: "sh_curvol", op: "lt", value: 10 * M },
        ],
      },
      { id: "o10m", label: "10M and up", filters: [{ key: "sh_curvol", op: "gte", value: 10 * M }] },
    ],
  },
  {
    key: "fa_pe",
    label: "P/E",
    unit: "ratio",
    primary: true,
    hint:
      "A loss-maker has a negative P/E, which is the opposite of a cheap multiple. " +
      "These buckets count earnings-positive companies only: P/E ≥ 0.",
    buckets: [
      {
        id: "u10",
        label: "Under 10 (P/E ≥ 0)",
        filters: [
          { key: "fa_pe", op: "gte", value: 0 },
          { key: "fa_pe", op: "lt", value: 10 },
        ],
      },
      {
        id: "10-20",
        label: "10–20",
        filters: [
          { key: "fa_pe", op: "gte", value: 10 },
          { key: "fa_pe", op: "lt", value: 20 },
        ],
      },
      {
        id: "20-40",
        label: "20–40",
        filters: [
          { key: "fa_pe", op: "gte", value: 20 },
          { key: "fa_pe", op: "lt", value: 40 },
        ],
      },
      { id: "o40", label: "40 and up", filters: [{ key: "fa_pe", op: "gte", value: 40 }] },
    ],
  },
  {
    key: "fa_div",
    label: "Dividend yield (reported)",
    unit: "percent",
    primary: true,
    hint:
      "Only yields the provider reports are counted. A stock whose dividend cell is " +
      "missing (null) matches none of these buckets: the API treats an absent cell as " +
      "unknown, not as 0.0, so it cannot be selected here — and it is not a non-payer.",
    buckets: [
      {
        id: "u1",
        label: "Under 1% (reported)",
        filters: [
          { key: "fa_div", op: "gte", value: 0 },
          { key: "fa_div", op: "lt", value: 1 },
        ],
      },
      {
        id: "1-3",
        label: "1–3%",
        filters: [
          { key: "fa_div", op: "gte", value: 1 },
          { key: "fa_div", op: "lt", value: 3 },
        ],
      },
      {
        id: "3-6",
        label: "3–6%",
        filters: [
          { key: "fa_div", op: "gte", value: 3 },
          { key: "fa_div", op: "lt", value: 6 },
        ],
      },
      { id: "o6", label: "6% and up", filters: [{ key: "fa_div", op: "gte", value: 6 }] },
      {
        // A detail of the under-1% band, not a fifth band: 0.0 is a reported
        // yield, so it is also "under 1%", and saying both is the point. The
        // 78 rows here are why "Under 1%" can no longer read as "pays nothing".
        id: "none",
        label: "Pays nothing (0.0)",
        detail: true,
        filters: [{ key: "fa_div", op: "eq", value: 0 }],
      },
    ],
  },
  {
    key: "ta_beta",
    label: "Beta",
    unit: "ratio",
    primary: true,
    buckets: [
      { id: "low", label: "Under 0.8", filters: [{ key: "ta_beta", op: "lt", value: 0.8 }] },
      {
        id: "market",
        label: "0.8–1.2",
        filters: [
          { key: "ta_beta", op: "gte", value: 0.8 },
          { key: "ta_beta", op: "lt", value: 1.2 },
        ],
      },
      { id: "high", label: "1.2 and up", filters: [{ key: "ta_beta", op: "gte", value: 1.2 }] },
    ],
  },
  {
    key: "ta_rsi",
    label: "RSI (14)",
    unit: "plain",
    primary: true,
    buckets: [
      { id: "oversold", label: "Oversold · under 30", filters: [{ key: "ta_rsi", op: "lt", value: 30 }] },
      {
        id: "neutral",
        label: "Neutral · 30–70",
        filters: [
          { key: "ta_rsi", op: "gte", value: 30 },
          { key: "ta_rsi", op: "lt", value: 70 },
        ],
      },
      { id: "overbought", label: "Overbought · 70 and up", filters: [{ key: "ta_rsi", op: "gte", value: 70 }] },
    ],
  },
  {
    key: "sh_relvol",
    label: "Relative volume",
    unit: "ratio",
    primary: true,
    buckets: [
      { id: "quiet", label: "Under 1×", filters: [{ key: "sh_relvol", op: "lt", value: 1 }] },
      {
        id: "active",
        label: "1–2×",
        filters: [
          { key: "sh_relvol", op: "gte", value: 1 },
          { key: "sh_relvol", op: "lt", value: 2 },
        ],
      },
      { id: "hot", label: "2× and up", filters: [{ key: "sh_relvol", op: "gte", value: 2 }] },
    ],
  },
  {
    key: "sh_avgvol",
    label: "Average volume",
    unit: "compact",
    primary: false,
    buckets: [
      { id: "u500k", label: "Under 500K", filters: [{ key: "sh_avgvol", op: "lt", value: 500 * K }] },
      {
        id: "500k-2m",
        label: "500K–2M",
        filters: [
          { key: "sh_avgvol", op: "gte", value: 500 * K },
          { key: "sh_avgvol", op: "lt", value: 2 * M },
        ],
      },
      { id: "o2m", label: "2M and up", filters: [{ key: "sh_avgvol", op: "gte", value: 2 * M }] },
    ],
  },
  {
    key: "fa_roe",
    label: "Return on equity",
    unit: "percent",
    primary: false,
    buckets: [
      { id: "negative", label: "Under 0%", filters: [{ key: "fa_roe", op: "lt", value: 0 }] },
      {
        id: "0-15",
        label: "0–15%",
        filters: [
          { key: "fa_roe", op: "gte", value: 0 },
          { key: "fa_roe", op: "lt", value: 15 },
        ],
      },
      { id: "o15", label: "15% and up", filters: [{ key: "fa_roe", op: "gte", value: 15 }] },
    ],
  },
];

/**
 * Key → the unit a bare number needs. Only the keys that have a preset group;
 * `registry.ts` derives a unit for every other key from its column.
 */
export const PRESET_UNITS: Record<string, Unit> = Object.fromEntries(
  PRESETS.map((group) => [group.key, group.unit]),
);

/** The number of preset groups a first-time reader sees before the disclosure. */
export const PRIMARY_PRESET_COUNT = PRESETS.filter((group) => group.primary).length;

/** Flattened, for a test or a script that proves every bucket is accepted. */
export function allBuckets(): Array<{ group: PresetGroup; bucket: PresetGroup["buckets"][number] }> {
  return PRESETS.flatMap((group) =>
    group.buckets.map((bucket) => ({ group, bucket })),
  );
}

/** The preset group that owns a key, if any. */
export function presetGroupFor(key: string): PresetGroup | undefined {
  return PRESETS.find((group) => group.key === key);
}

/**
 * Does `value` satisfy every predicate in `bucket`?
 *
 * The same semantics as `app/screen/filters.py`: a null (missing) cell matches
 * nothing, and every bound is compared the way the API compares it. Used by the
 * membership tests and the live preset proof; a bucket is only as honest as the
 * set of values it accepts, so that set is expressible in code.
 */
export function bucketAccepts(bucket: PresetBucket, value: number | null | undefined): boolean {
  if (typeof value !== "number" || !Number.isFinite(value)) return false;
  return bucket.filters.every((filter) => {
    const bound = filter.value as number;
    switch (filter.op) {
      case "eq":
        return value === bound;
      case "ne":
        return value !== bound;
      case "gt":
        return value > bound;
      case "gte":
        return value >= bound;
      case "lt":
        return value < bound;
      case "lte":
        return value <= bound;
      case "between": {
        const [low, high] = filter.value as number[];
        return value >= low && value <= high;
      }
      default:
        return false;
    }
  });
}
