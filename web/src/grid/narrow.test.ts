/**
 * The narrow module's promises, one test each.
 *
 * A NarrowSpec names one or more narrowing dimensions:
 *   market_cap → cap presets (micro, small, mid, mega)
 *   performance → signal tokens (gainers, losers, etc.)
 *   volume     → volume presets (u500k, 500k-2m, 2m-10m, o10m)
 *   sector     → a sector name
 *
 * narrowToFilters converts the narrow into WireFilter[]
 * to AND with criteria. narrowToSignal extracts the
 * performance dimension as a signal token (not a filter).
 * narrowIsEmpty answers whether no dimension was chosen.
 */

import { describe, expect, it } from "vitest";

import {
  narrowToFilters,
  narrowToSignal,
  narrowIsEmpty,
  type NarrowSpec,
} from "./narrow";

describe("narrowIsEmpty", () => {
  it("is true for undefined", () => {
    expect(narrowIsEmpty(undefined)).toBe(true);
  });

  it("is true for an empty object", () => {
    expect(narrowIsEmpty({})).toBe(true);
  });

  it("is false when market_cap is set", () => {
    expect(narrowIsEmpty({ market_cap: "mega" })).toBe(false);
  });

  it("is false when performance is set", () => {
    expect(narrowIsEmpty({ performance: "gainers" })).toBe(false);
  });

  it("is false when volume is set", () => {
    expect(narrowIsEmpty({ volume: "o10m" })).toBe(false);
  });

  it("is false when sector is set", () => {
    expect(narrowIsEmpty({ sector: "Electronic Technology" })).toBe(false);
  });

  it("is false when multiple dimensions are set", () => {
    expect(
      narrowIsEmpty({ market_cap: "mid", sector: "Finance" }),
    ).toBe(false);
  });
});

describe("narrowToFilters", () => {
  it("returns empty for undefined", () => {
    expect(narrowToFilters(undefined)).toEqual([]);
  });

  it("returns empty for an empty object", () => {
    expect(narrowToFilters({})).toEqual([]);
  });

  it("converts market_cap micro to a cap filter", () => {
    const filters = narrowToFilters({ market_cap: "micro" });
    expect(filters).toEqual([{ key: "cap", op: "lt", value: 2_000_000_000 }]);
  });

  it("converts market_cap small to two cap filters", () => {
    const filters = narrowToFilters({ market_cap: "small" });
    expect(filters).toEqual([
      { key: "cap", op: "gte", value: 2_000_000_000 },
      { key: "cap", op: "lt", value: 10_000_000_000 },
    ]);
  });

  it("converts market_cap mid to two cap filters", () => {
    const filters = narrowToFilters({ market_cap: "mid" });
    expect(filters).toEqual([
      { key: "cap", op: "gte", value: 10_000_000_000 },
      { key: "cap", op: "lt", value: 200_000_000_000 },
    ]);
  });

  it("converts market_cap mega to a cap filter", () => {
    const filters = narrowToFilters({ market_cap: "mega" });
    expect(filters).toEqual([{ key: "cap", op: "gte", value: 200_000_000_000 }]);
  });

  it("converts volume u500k to a volume filter", () => {
    const filters = narrowToFilters({ volume: "u500k" });
    expect(filters).toEqual([{ key: "sh_curvol", op: "lt", value: 500_000 }]);
  });

  it("converts volume 500k-2m to two volume filters", () => {
    const filters = narrowToFilters({ volume: "500k-2m" });
    expect(filters).toEqual([
      { key: "sh_curvol", op: "gte", value: 500_000 },
      { key: "sh_curvol", op: "lt", value: 2_000_000 },
    ]);
  });

  it("converts volume 2m-10m to two volume filters", () => {
    const filters = narrowToFilters({ volume: "2m-10m" });
    expect(filters).toEqual([
      { key: "sh_curvol", op: "gte", value: 2_000_000 },
      { key: "sh_curvol", op: "lt", value: 10_000_000 },
    ]);
  });

  it("converts volume o10m to a volume filter", () => {
    const filters = narrowToFilters({ volume: "o10m" });
    expect(filters).toEqual([{ key: "sh_curvol", op: "gte", value: 10_000_000 }]);
  });

  it("converts sector to a sec eq filter", () => {
    const filters = narrowToFilters({ sector: "Electronic Technology" });
    expect(filters).toEqual([
      { key: "sec", op: "eq", value: "Electronic Technology" },
    ]);
  });

  it("performance is not converted to a filter", () => {
    const filters = narrowToFilters({ performance: "gainers" });
    expect(filters).toEqual([]);
  });

  it("combines all filter dimensions", () => {
    const filters = narrowToFilters({
      market_cap: "mega",
      volume: "o10m",
      sector: "Finance",
      performance: "gainers", // should be ignored here
    });
    expect(filters).toEqual([
      { key: "cap", op: "gte", value: 200_000_000_000 },
      { key: "sh_curvol", op: "gte", value: 10_000_000 },
      { key: "sec", op: "eq", value: "Finance" },
    ]);
  });
});

describe("narrowToSignal", () => {
  it("returns null for undefined", () => {
    expect(narrowToSignal(undefined)).toBeNull();
  });

  it("returns null when performance is not set", () => {
    expect(narrowToSignal({ market_cap: "mega" })).toBeNull();
  });

  it("returns null when all dimensions are absent", () => {
    expect(narrowToSignal({})).toBeNull();
  });

  it("returns the signal token for gainers", () => {
    expect(narrowToSignal({ performance: "gainers" })).toBe("gainers");
  });

  it("returns the signal token for unusual_volume", () => {
    expect(narrowToSignal({ performance: "unusual_volume" })).toBe(
      "unusual_volume",
    );
  });

  it("returns the signal token alongside other dimensions", () => {
    expect(
      narrowToSignal({ performance: "losers", market_cap: "small" }),
    ).toBe("losers");
  });
});