import { describe, expect, it } from "vitest";

import { filterSchemaFixture } from "./__fixtures__/schema";
import { buildRegistry } from "./registry";
import {
  PRESETS,
  allBuckets,
  bucketAccepts,
  PRIMARY_PRESET_COUNT,
} from "./presets";
import type { PresetBucket, PresetGroup } from "./types";
import { validateDraft } from "./validation";

/**
 * Every bucket must be a predicate the API accepts *by construction*, and it
 * must mean what its label says.
 *
 * The shape half is cheap: the key exists in the schema, the operator is in
 * that key's operator set, the value is finite and a `between` is ordered. The
 * membership half is the one that matters — a bucket that returns 200 with the
 * wrong rows is exactly the failure these tests were added for (phase 3
 * findings F1, F2 and F9). The live half (`presets.live.test.ts`,
 * `LIVE_API=1`) posts each bucket to :8099 and checks every sampled row against
 * the bucket's own predicate.
 */

function group(key: string): PresetGroup {
  const found = PRESETS.find((candidate) => candidate.key === key);
  if (!found) throw new Error(`no preset group ${key}`);
  return found;
}

function bucket(key: string, id: string): PresetBucket {
  const found = group(key).buckets.find((candidate) => candidate.id === id);
  if (!found) throw new Error(`no bucket ${key}/${id}`);
  return found;
}

interface Interval {
  low: number;
  lowClosed: boolean;
  high: number;
  highClosed: boolean;
}

/** The half-open interval a bucket's predicates describe. */
function intervalOf(candidate: PresetBucket): Interval {
  let low = Number.NEGATIVE_INFINITY;
  let lowClosed = false;
  let high = Number.POSITIVE_INFINITY;
  let highClosed = false;
  for (const filter of candidate.filters) {
    const value = filter.value as number;
    if (filter.op === "gte") {
      low = value;
      lowClosed = true;
    } else if (filter.op === "gt") {
      low = value;
      lowClosed = false;
    } else if (filter.op === "lte") {
      high = value;
      highClosed = true;
    } else if (filter.op === "lt") {
      high = value;
      highClosed = false;
    } else if (filter.op === "eq") {
      low = value;
      high = value;
      lowClosed = true;
      highClosed = true;
    }
  }
  return { low, lowClosed, high, highClosed };
}

describe("preset buckets", () => {
  const registry = buildRegistry(filterSchemaFixture());

  it("covers ten numeric keys, eight of them visible", () => {
    expect(PRESETS).toHaveLength(10);
    expect(PRIMARY_PRESET_COUNT).toBe(8);
    // 35 bands + the dividend "Pays nothing (0.0)" detail bucket (F2).
    expect(allBuckets()).toHaveLength(36);
  });

  it("every bucket is a filter the registry's own validator accepts", () => {
    for (const { group: preset, bucket: candidate } of allBuckets()) {
      expect(registry.byKey.has(preset.key), `${preset.key} is not usable`).toBe(true);
      expect(candidate.filters.length, `${candidate.id} has no predicate`).toBeGreaterThan(0);
      expect(candidate.filters.length, `${candidate.id} is more than a range`).toBeLessThanOrEqual(2);

      for (const filter of candidate.filters) {
        expect(filter.key).toBe(preset.key);
        const result = validateDraft(registry, filter);
        expect(result.ok, `${preset.label} / ${candidate.label}: ${result.ok ? "" : result.error}`).toBe(
          true,
        );
        if (result.ok) {
          if (filter.op === "between") {
            const [low, high] = result.value as number[];
            expect(low).toBeLessThanOrEqual(high as number);
          }
          expect(typeof result.value === "number" ? Number.isFinite(result.value) : true).toBe(true);
        }
      }
    }
  });

  it("adjacent bands leave no gap and no overlap", () => {
    // A `detail` bucket names a sub-population (e.g. exactly 0.0) and is not a
    // band, so it takes no part in the partition proof.
    for (const preset of PRESETS) {
      const bands = preset.buckets.filter((candidate) => !candidate.detail);
      const ranges = bands.map(intervalOf);

      for (let index = 1; index < ranges.length; index += 1) {
        const previous = ranges[index - 1] as Interval;
        const current = ranges[index] as Interval;
        expect(previous.high, `${preset.label} has a gap or overlap`).toBe(current.low);
        expect(
          previous.highClosed && current.lowClosed,
          `${preset.label} counts its boundary ${current.low} twice`,
        ).toBe(false);
      }
    }
  });
});

// ---------------------------------------------------------------------------
// F1 — a "cheap earnings" bucket must not be two-thirds loss-makers
// ---------------------------------------------------------------------------

describe("F1 — P/E buckets mean earnings-positive", () => {
  it("'Under 10' requires P/E ≥ 0 and still contains a genuine 0.01", () => {
    const under10 = bucket("fa_pe", "u10");

    expect(under10.filters).toEqual(
      expect.arrayContaining([{ key: "fa_pe", op: "gte", value: 0 }]),
    );

    // F: pe −6.55. A loss is not a cheap multiple.
    expect(bucketAccepts(under10, -6.55)).toBe(false);
    expect(bucketAccepts(under10, -483.97)).toBe(false);

    // Adversarial (a): excluding negatives must not exclude tiny positives.
    expect(bucketAccepts(under10, 0.01)).toBe(true);
    expect(bucketAccepts(under10, 9.99)).toBe(true);
    expect(bucketAccepts(under10, 10)).toBe(false);

    // A reported 0 is a real (if degenerate) multiple; a null cell is unknown
    // and the API never matches it.
    expect(bucketAccepts(under10, 0)).toBe(true);
    expect(bucketAccepts(under10, null)).toBe(false);
  });

  it("no P/E bucket contains a loss-maker", () => {
    for (const candidate of group("fa_pe").buckets) {
      expect(bucketAccepts(candidate, -31.46), `${candidate.label} includes INTC`).toBe(false);
      expect(bucketAccepts(candidate, -0.01), `${candidate.label} includes a loss`).toBe(false);
    }
  });

  it("the P/E group says out loud that loss-makers are excluded", () => {
    expect(group("fa_pe").hint ?? "").toMatch(/loss|negative/i);
  });
});

// ---------------------------------------------------------------------------
// F2 — a dividend bucket that counts only reported yields must say so
// ---------------------------------------------------------------------------

describe("F2 — dividend buckets count reported yields, and the zero is named", () => {
  it("labels the group and the 'under 1%' bucket as reported-only", () => {
    expect(group("fa_div").label).toMatch(/reported/i);
    expect(bucket("fa_div", "u1").label).toMatch(/reported/i);
    expect(group("fa_div").hint ?? "").toMatch(/no reported yield|missing|null/i);
  });

  it("a 0.0 payer is in 'Pays nothing' and in the reported under-1% bucket", () => {
    const paysNothing = bucket("fa_div", "none");
    expect(paysNothing.label).toMatch(/pays nothing/i);
    expect(paysNothing.filters).toEqual([{ key: "fa_div", op: "eq", value: 0 }]);
    expect(paysNothing.detail).toBe(true);

    // TDG reports exactly 0.0: it is a non-payer, so the zero bucket owns it.
    expect(bucketAccepts(paysNothing, 0)).toBe(true);
    // 0 < 1 is true, and 0 is a reported (not missing) yield.
    expect(bucketAccepts(bucket("fa_div", "u1"), 0)).toBe(true);
    expect(bucketAccepts(bucket("fa_div", "1-3"), 0)).toBe(false);
  });

  it("a null-yield symbol is in no dividend bucket", () => {
    // TSLA answers `dividend_yield: null`. The API treats a missing cell as
    // unknown — it matches no numeric predicate, so no filter can select it.
    // That is why the group names the population instead of pretending the
    // four reported buckets are exhaustive.
    for (const candidate of group("fa_div").buckets) {
      expect(bucketAccepts(candidate, null), `${candidate.label} includes TSLA`).toBe(false);
    }
  });

  it("the bands still leave no gap or overlap", () => {
    const bands = group("fa_div").buckets.filter((candidate) => !candidate.detail).map(intervalOf);
    for (let index = 1; index < bands.length; index += 1) {
      expect((bands[index - 1] as Interval).high).toBe((bands[index] as Interval).low);
    }
  });
});

// ---------------------------------------------------------------------------
// F9 — "…and up" is inclusive, and the boundary symbol proves it
// ---------------------------------------------------------------------------

describe("F9 — the top bucket includes its bound, and says 'and up'", () => {
  it("no bucket is labelled 'Over …' any more", () => {
    for (const { bucket: candidate } of allBuckets()) {
      expect(candidate.label, `${candidate.id} still says Over`).not.toMatch(/\bOver\b/);
    }
  });

  it("$200B and up is inclusive; the band below stops short of it", () => {
    const mega = bucket("cap", "mega");
    const mid = bucket("cap", "mid");
    expect(mega.label).toBe("$200B and up");
    expect(bucketAccepts(mega, 200_000_000_000)).toBe(true);
    expect(bucketAccepts(mid, 200_000_000_000)).toBe(false);
  });

  it("SAH's exact cap (1,999,003,845) sits in 'Under $2B', not in '$2–10B'", () => {
    // The symbol the review named: its cap is exactly $1,999,003,845, so
    // `gte 1999003845` includes it and `gt 1999003845` does not. Every bound
    // is stated inclusively ("and up") precisely because gte is inclusive.
    const sah = 1_999_003_845;
    expect(bucketAccepts(bucket("cap", "micro"), sah)).toBe(true);
    expect(bucketAccepts(bucket("cap", "small"), sah)).toBe(false);
    expect(bucketAccepts(bucket("cap", "mid"), sah)).toBe(false);
    expect(bucketAccepts(bucket("cap", "mega"), sah)).toBe(false);
  });

  it("every boundary belongs to exactly one cap bucket", () => {
    const buckets = group("cap").buckets;
    for (const value of [0, 1_999_003_845, 2_000_000_000, 10_000_000_000, 200_000_000_000, 5e12]) {
      const members = buckets.filter((candidate) => bucketAccepts(candidate, value));
      expect(members, `${value} is in ${members.length} cap buckets`).toHaveLength(1);
    }
  });
});
