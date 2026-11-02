/**
 * Narrow — one optional dimension (or more) a reader applies to the export.
 *
 * Each dimension maps to the tokens the screener already knows:
 *   market_cap → the cap preset group (micro, small, mid, mega)
 *   performance → a signal slug the API compiles (gainers, losers, …)
 *   volume     → the volume preset group (u500k, 500k-2m, 2m-10m, o10m)
 *   sector     → a Finviz sector name, sent as sec eq
 *
 * The narrow is ANDed with the screen's criteria; the four scopes handle it
 * differently (see exportJob.ts and ExportDialog.tsx).
 */

import type { WireFilter } from "@/filters/types";

/** The four buckets of the market-cap preset group. */
export type MarketCapNarrow = "micro" | "small" | "mid" | "mega";

/** The signal slugs the screener knows. */
export type PerformanceNarrow =
  | "gainers"
  | "losers"
  | "new_high"
  | "new_low"
  | "overbought"
  | "oversold"
  | "unusual_volume"
  | "most_active"
  | "most_volatile"
  | "insider_buying"
  | "insider_selling";

/** The four buckets of the volume-today preset group. */
export type VolumeNarrow = "u500k" | "500k-2m" | "2m-10m" | "o10m";

export interface NarrowSpec {
  /** A market-cap bucket id from the cap preset group. */
  market_cap?: MarketCapNarrow;
  /** A performance signal slug. */
  performance?: PerformanceNarrow;
  /** A volume-today bucket id from the sh_curvol preset group. */
  volume?: VolumeNarrow;
  /** A Finviz sector name, exactly as the API matches it. */
  sector?: string;
}

export type NarrowKey = keyof NarrowSpec;

const B = 1_000_000_000;
const M = 1_000_000;
const K = 1_000;

/** The cap preset buckets, mirrored exactly from filters/presets.ts. */
const CAP_BUCKETS: Record<string, WireFilter[]> = {
  micro: [{ key: "cap", op: "lt", value: 2 * B }],
  small: [
    { key: "cap", op: "gte", value: 2 * B },
    { key: "cap", op: "lt", value: 10 * B },
  ],
  mid: [
    { key: "cap", op: "gte", value: 10 * B },
    { key: "cap", op: "lt", value: 200 * B },
  ],
  mega: [{ key: "cap", op: "gte", value: 200 * B }],
};

/** The volume preset buckets, mirrored exactly from filters/presets.ts. */
const VOLUME_BUCKETS: Record<string, WireFilter[]> = {
  "u500k": [{ key: "sh_curvol", op: "lt", value: 500 * K }],
  "500k-2m": [
    { key: "sh_curvol", op: "gte", value: 500 * K },
    { key: "sh_curvol", op: "lt", value: 2 * M },
  ],
  "2m-10m": [
    { key: "sh_curvol", op: "gte", value: 2 * M },
    { key: "sh_curvol", op: "lt", value: 10 * M },
  ],
  o10m: [{ key: "sh_curvol", op: "gte", value: 10 * M }],
};

/** True when the reader chose no dimension. */
export function narrowIsEmpty(narrow: NarrowSpec | undefined): boolean {
  if (narrow === undefined) return true;
  return (
    narrow.market_cap === undefined &&
    narrow.performance === undefined &&
    narrow.volume === undefined &&
    narrow.sector === undefined
  );
}

/**
 * The WireFilter[] this narrow ANDs into the criteria.
 * Performance is excluded: it travels as a signal, not a filter.
 */
export function narrowToFilters(
  narrow: NarrowSpec | undefined,
): WireFilter[] {
  if (narrow === undefined) return [];

  const filters: WireFilter[] = [];

  if (narrow.market_cap !== undefined) {
    const bucket = CAP_BUCKETS[narrow.market_cap];
    if (bucket) filters.push(...bucket.map((filter) => ({ ...filter })));
  }

  if (narrow.volume !== undefined) {
    const bucket = VOLUME_BUCKETS[narrow.volume];
    if (bucket) filters.push(...bucket.map((filter) => ({ ...filter })));
  }

  if (narrow.sector !== undefined && narrow.sector.trim().length > 0) {
    filters.push({ key: "sec", op: "eq", value: narrow.sector });
  }

  return filters;
}

/**
 * The signal token, or null when performance was not set.
 */
export function narrowToSignal(
  narrow: NarrowSpec | undefined,
): string | null {
  if (narrow === undefined || narrow.performance === undefined) return null;
  return narrow.performance;
}