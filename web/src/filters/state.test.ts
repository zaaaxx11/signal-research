import { beforeEach, describe, expect, it } from "vitest";

import { filterSchemaFixture } from "./__fixtures__/schema";
import { buildRegistry } from "./registry";
import { PRESETS } from "./presets";
import {
  activeBucket,
  appendFilter,
  applyBucket,
  clearFilters,
  describeFilter,
  filtersSignature,
  formatValue,
  removeFilter,
  resetFilterIds,
  toWire,
} from "./state";
import type { ActiveFilter } from "./types";

describe("the one filter state", () => {
  const registry = buildRegistry(filterSchemaFixture());

  beforeEach(() => {
    resetFilterIds();
  });

  it("(d) two filters on one key are ANDed, not silently replaced", () => {
    const first = appendFilter([], { key: "cap", op: "gte", value: 2_000_000_000 });
    const both = appendFilter(first, { key: "cap", op: "lt", value: 10_000_000_000 });

    expect(both).toHaveLength(2);
    expect(both.map((filter) => filter.id)).toHaveLength(2);
    expect(new Set(both.map((filter) => filter.id)).size).toBe(2);
    expect(toWire(both)).toEqual([
      { key: "cap", op: "gte", value: 2_000_000_000 },
      { key: "cap", op: "lt", value: 10_000_000_000 },
    ]);

    // Removing one leaves the other: an id, not a value, is what a chip owns.
    const kept = removeFilter(both, both[0]?.id ?? "");
    expect(toWire(kept)).toEqual([{ key: "cap", op: "lt", value: 10_000_000_000 }]);
  });

  it("does not duplicate an identical predicate", () => {
    const once = appendFilter([], { key: "cap", op: "gte", value: 2_000_000_000 });
    const twice = appendFilter(once, { key: "cap", op: "gte", value: 2_000_000_000 });
    expect(twice).toHaveLength(1);
  });

  it("a preset bucket is a radio: it replaces its own key and leaves others", () => {
    const cap = PRESETS.find((group) => group.key === "cap");
    const price = PRESETS.find((group) => group.key === "sh_price");
    if (!cap || !price) throw new Error("fixture presets missing");

    let state: ActiveFilter[] = [];
    state = applyBucket(state, cap, cap.buckets[0] as (typeof cap.buckets)[number]);
    state = applyBucket(state, price, price.buckets[0] as (typeof price.buckets)[number]);
    expect(state.map((filter) => filter.key)).toEqual(["cap", "sh_price"]);

    const second = cap.buckets[1] as (typeof cap.buckets)[number];
    state = applyBucket(state, cap, second);
    expect(state.filter((filter) => filter.key === "cap")).toHaveLength(2);
    expect(state.filter((filter) => filter.key === "sh_price")).toHaveLength(1);
    expect(activeBucket(state, cap)).toBe(second.id);
    expect(activeBucket(state, price)).toBe(price.buckets[0]?.id);
  });

  it("clearing all restores the unfiltered panel", () => {
    const state = appendFilter(
      appendFilter([], { key: "sec", op: "eq", value: "Technology" }),
      { key: "cap", op: "gte", value: 1 },
    );
    expect(clearFilters()).toEqual([]);
    expect(filtersSignature(clearFilters())).toBe("[]");
    expect(state).toHaveLength(2);
  });

  it("describes a chip with the field's unit", () => {
    const cap = appendFilter([], { key: "cap", op: "gte", value: 2_000_000_000 });
    expect(describeFilter(registry, cap[0] as ActiveFilter)).toMatchObject({
      label: "Market Cap.",
      value: "≥ $2B",
    });

    const range = appendFilter([], { key: "cap", op: "between", value: [2_000_000_000, 10_000_000_000] });
    expect(describeFilter(registry, range[0] as ActiveFilter).value).toBe("$2B–$10B");

    const sector = appendFilter([], { key: "sec", op: "in", value: ["Technology", "Healthcare"] });
    expect(describeFilter(registry, sector[0] as ActiveFilter).value).toBe(
      "in Technology, Healthcare",
    );
    expect(describeFilter(registry, sector[0] as ActiveFilter).text).toBe(
      "Sector in Technology, Healthcare",
    );
  });

  it("formats values the way a reader expects", () => {
    expect(formatValue(registry, "cap", 200_000_000_000)).toBe("$200B");
    expect(formatValue(registry, "sh_price", 12.5)).toBe("$12.5");
    expect(formatValue(registry, "sh_curvol", 500_000)).toBe("500K");
    expect(formatValue(registry, "fa_div", 3)).toBe("3%");
    expect(formatValue(registry, "ta_beta", 0.8)).toBe("0.8");
  });
});

// ---------------------------------------------------------------------------
// F6 — a chip must name a choice option the way the control does
// ---------------------------------------------------------------------------

describe("F6 — a choice chip reads as a label, not as the wire token", () => {
  const registry = buildRegistry(filterSchemaFixture());

  it("renders '1w10u' as the drawer's option label", () => {
    const chip = describeFilter(registry, {
      id: "a",
      key: "ta_perf",
      op: "eq",
      value: "1w10u",
    });

    expect(chip.label).toBe("Performance");
    expect(chip.value).toBe("= 1W down 10%+");
    expect(chip.text).toBe("Performance = 1W down 10%+");
    expect(chip.text).not.toContain("1w10u");
  });

  it("keeps an unknown token visible instead of inventing a label", () => {
    expect(formatValue(registry, "ta_perf", "notatoken")).toBe("notatoken");
  });
});

// ---------------------------------------------------------------------------
// F8 — two different requests must not render as one identical chip
// ---------------------------------------------------------------------------

describe("F8 — distinct values render distinctly", () => {
  const registry = buildRegistry(filterSchemaFixture());

  it("two caps a hair apart do not collapse to the same chip", () => {
    expect(formatValue(registry, "cap", 1_000_000_000)).toBe("$1B");
    expect(formatValue(registry, "cap", 1_000_040_000)).toBe("$1.00004B");
    expect(formatValue(registry, "cap", 1_000_040_000)).not.toBe(
      formatValue(registry, "cap", 1_000_000_000),
    );

    const first = describeFilter(registry, { id: "a", key: "cap", op: "lt", value: 1_000_000_000 });
    const second = describeFilter(registry, { id: "b", key: "cap", op: "lt", value: 1_000_040_000 });
    expect(first.text).not.toBe(second.text);
  });

  it("two ratios a hair apart do not collapse either", () => {
    expect(formatValue(registry, "ta_beta", 0.8)).toBe("0.8");
    expect(formatValue(registry, "ta_beta", 0.80004)).toBe("0.80004");
  });

  it("adversarial (d): the precise form still fits a 12px chip", () => {
    // A chip is 340 px wide with a label in front; the value must stay a short
    // readable token, not a full float.
    for (const value of [1_000_040_000, 1_000_000_001, 99_999_999_999]) {
      const text = formatValue(registry, "cap", value);
      expect(text.length, text).toBeLessThanOrEqual(10);
      expect(text).toMatch(/^\$[\d.]+(K|M|B|T)?$/);
    }
    expect(formatValue(registry, "ta_beta", 0.80004).length).toBeLessThanOrEqual(8);
  });
});
