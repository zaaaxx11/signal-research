import { describe, expect, it } from "vitest";

import { LIVE_BREADTH } from "./__fixtures__/breadth";
import {
  BreadthMalformed,
  parseBreadth,
  sortSectors,
  sumCheck,
  type SectorBucket,
} from "./report";

/**
 * The model half of the strip: what a payload is allowed to say, what happens
 * when it says something else, and the two pieces of arithmetic the browser is
 * permitted to do (an audit of the buckets, and a sort).
 */

function without(document: object, field: string): Record<string, unknown> {
  const copy: Record<string, unknown> = { ...document };
  delete copy[field];
  return copy;
}

function sector(overrides: Partial<SectorBucket> = {}): SectorBucket {
  return { name: "Finance", avg_change_pct: -1.1, count: 10, advancing: 4, declining: 6, ...overrides };
}

describe("parseBreadth", () => {
  it("reads the live document without losing a field", () => {
    const report = parseBreadth(LIVE_BREADTH);

    expect(report.universe_total).toBe(5678);
    expect(report.advancing).toBe(1557);
    expect(report.declining).toBe(3932);
    expect(report.unchanged).toBe(189);
    expect(report.sectors).toHaveLength(21);
    expect(report.stale).toBe(false);
    // The prose the UI shows as a tooltip survived the trip.
    expect(report.method["new_highs"]).toContain("52w");
  });

  it("keeps a missing average null instead of inventing a zero", () => {
    const report = parseBreadth({
      ...LIVE_BREADTH,
      sectors: [{ name: "Finance", avg_change_pct: null, count: 3, advancing: 0, declining: 0 }],
    });

    expect(report.sectors[0]?.avg_change_pct).toBeNull();
  });

  it("keeps an unlabelled bucket as a null name rather than renaming it", () => {
    const report = parseBreadth({
      ...LIVE_BREADTH,
      sectors: [{ name: null, avg_change_pct: 0.5, count: 2, advancing: 1, declining: 1 }],
    });

    expect(report.sectors[0]?.name).toBeNull();
  });

  it("refuses a payload with an absent count instead of printing zero", () => {
    const payload = without(LIVE_BREADTH, "advancing");

    expect(() => parseBreadth(payload)).toThrow(BreadthMalformed);
    expect(() => parseBreadth(payload)).toThrow(/"advancing"/);
  });

  it("refuses a negative count and a non-array sector list", () => {
    expect(() => parseBreadth({ ...LIVE_BREADTH, declining: -1 })).toThrow(/"declining"/);
    expect(() => parseBreadth({ ...LIVE_BREADTH, sectors: {} })).toThrow(/"sectors"/);
  });

  it("treats a non-numeric average as absent, never as flat", () => {
    const report = parseBreadth({
      ...LIVE_BREADTH,
      sectors: [{ name: "X", avg_change_pct: "n/a", count: 1, advancing: 0, declining: 0 }],
    });

    expect(report.sectors[0]?.avg_change_pct).toBeNull();
  });
});

describe("sumCheck", () => {
  it("holds for the live fixture: the three buckets are the universe", () => {
    const check = sumCheck(LIVE_BREADTH);

    expect(check.sum).toBe(5678);
    expect(check.total).toBe(5678);
    expect(check.delta).toBe(0);
    expect(check.ok).toBe(true);
  });

  it("reports a snapshot whose parts disagree with its whole", () => {
    const check = sumCheck({ ...LIVE_BREADTH, unchanged: 182 });

    expect(check.sum).toBe(5671);
    expect(check.delta).toBe(-7);
    expect(check.ok).toBe(false);
  });

  it("counts a positive delta when the buckets double-count", () => {
    const check = sumCheck({ ...LIVE_BREADTH, universe_total: 5600 });

    expect(check.delta).toBe(78);
    expect(check.ok).toBe(false);
  });
});

describe("sortSectors", () => {
  it("puts the strongest average change first", () => {
    const rows = sortSectors(LIVE_BREADTH.sectors);
    const measured = LIVE_BREADTH.sectors.map((entry) => entry.avg_change_pct ?? Number.NEGATIVE_INFINITY);
    const strongest = Math.max(...measured);
    const weakest = Math.min(...measured);

    expect(rows[0]?.avg_change_pct).toBe(strongest);
    expect(rows.at(-1)?.avg_change_pct).toBe(weakest);
  });

  it("never lets an unmeasured sector sort as a measured one", () => {
    // The null-average bucket is also the largest, so a naive "biggest first"
    // tiebreak would float it to the top. It belongs last: it has no average.
    const rows = sortSectors([
      sector({ name: "Unmeasured", avg_change_pct: null, count: 9999 }),
      sector({ name: "Up", avg_change_pct: 2.5, count: 5 }),
      sector({ name: "Down", avg_change_pct: -4, count: 500 }),
    ]);

    expect(rows.map((row) => row.name)).toEqual(["Up", "Down", "Unmeasured"]);
  });

  it("breaks an average tie by size, then by label", () => {
    const rows = sortSectors([
      sector({ name: "Small", avg_change_pct: 1, count: 10 }),
      sector({ name: "Big", avg_change_pct: 1, count: 90 }),
      sector({ name: "Alpha", avg_change_pct: 1, count: 10 }),
    ]);

    expect(rows.map((row) => row.name)).toEqual(["Big", "Alpha", "Small"]);
  });

  it("returns a new array and leaves the payload's order alone", () => {
    const input = LIVE_BREADTH.sectors;
    const before = input.map((row) => row.name);

    const rows = sortSectors(input);

    expect(rows).not.toBe(input);
    expect(input.map((row) => row.name)).toEqual(before);
  });
});
