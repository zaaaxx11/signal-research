import { describe, expect, it } from "vitest";

import {
  CHOICE_KEYS,
  REFUSED_KEYS,
  USABLE_KEYS,
  filterSchemaFixture,
} from "./__fixtures__/schema";
import { buildRegistry, choiceTokenLabel, resolveKey } from "./registry";
import { describeFilter, formatValue } from "./state";
import { isUsableEntry, parseFilterSchema, usableKeys } from "./schema";

/**
 * The registry is the schema, not a list someone maintains. These assertions
 * are written against the *served document* (as a fixture): the usable set is
 * computed from entry shapes, and the registry must match it exactly — 50 keys
 * today, and the same 50 tomorrow if the server changes its mind.
 */
describe("the filter registry", () => {
  const schema = filterSchemaFixture();
  const registry = buildRegistry(schema);

  const schemaUsable = new Set(
    Object.entries(schema.keys)
      .filter(([, entry]) => isUsableEntry(entry))
      .map(([key]) => key),
  );

  it("offers exactly the schema's usable keys, and no refused one", () => {
    expect(registry.entries).toHaveLength(50);
    expect(new Set(registry.entries.map((entry) => entry.key))).toEqual(schemaUsable);
    expect(new Set(registry.entries.map((entry) => entry.key))).toEqual(
      new Set([...USABLE_KEYS.map((row) => row[0]), ...CHOICE_KEYS.map((row) => row[0])]),
    );
    expect(registry.usableKeys).toEqual(schemaUsable);
    expect(usableKeys(schema)).toHaveLength(50);

    for (const [key] of REFUSED_KEYS) {
      expect(registry.byKey.has(key)).toBe(false);
      expect(registry.usableKeys.has(key)).toBe(false);
      expect(registry.refused[key]).toBeTruthy();
    }
    expect(Object.keys(registry.refused)).toHaveLength(32);
  });

  it("derives the control and operator set from the kind", () => {
    const cap = registry.byKey.get("cap");
    expect(cap?.control).toBe("buckets");
    expect(cap?.kind).toBe("number");
    expect(cap?.ops).toEqual(["eq", "ne", "gt", "gte", "lt", "lte", "between", "in"]);

    const peg = registry.byKey.get("fa_peg");
    expect(peg?.control).toBe("number");
    expect(peg?.presets).toBeNull();

    const sector = registry.byKey.get("sec");
    expect(sector?.control).toBe("text");
    expect(sector?.ops).toEqual(["eq", "ne", "in", "contains"]);

    const performance = registry.byKey.get("ta_perf");
    expect(performance?.control).toBe("choice");
    expect(performance?.ops).toEqual(["eq"]);
    expect(performance?.options.map((option) => option.token)).toContain("1w10u");
    expect(performance?.options.find((option) => option.token === "1w10u")?.label).toBe(
      "1W down 10%+",
    );
  });

  it("normalizes two-line labels and indexes aliases onto real keys only", () => {
    expect(registry.byKey.get("sh_insiderown")?.label).toBe("Insider Ownership");
    expect(resolveKey(registry, "pe")).toBe("fa_pe");
    expect(resolveKey(registry, "market_cap")).toBe("cap");
    expect(resolveKey(registry, "sector")).toBe("sec");
    expect(resolveKey(registry, "roic")).toBe("fa_roi");
    expect(resolveKey(registry, "company")).toBeNull();
    expect(resolveKey(registry, "idx")).toBeNull();
    expect(registry.aliases.every((alias) => registry.byKey.has(alias.targetKey))).toBe(true);
    expect(registry.byKey.get("fa_pe")?.search).toContain("pe");
  });

  it("labels tokens the schema publishes without a sentence", () => {
    expect(choiceTokenLabel("ta_perf", "dup")).toBe("Today up");
    expect(choiceTokenLabel("ta_perf", "52wdown")).toBe("52W down");
    expect(choiceTokenLabel("ta_perf", "ytd5o")).toBe("YTD up 5%+");
    expect(choiceTokenLabel("ta_volatility", "wo2")).toBe("Weekly ≥2%");
  });

  it("refuses a document with an empty vocabulary instead of drawing an empty drawer", () => {
    expect(() => parseFilterSchema({ views: [] })).toThrow(/x-filter-keys/);
    expect(() =>
      parseFilterSchema({ "x-filter-keys": { cap: { label: "Market Cap." } }, views: [{ name: "v", label: "V", job: "j", columns: [] }] }),
    ).toThrow(/usable filter entries/);
  });
});

// ---------------------------------------------------------------------------
// F7 — every key carries the unit its column is measured in, not just presets
// ---------------------------------------------------------------------------

describe("F7 — units come from the column, not from the preset list", () => {
  const registry = buildRegistry(filterSchemaFixture());

  it("a non-preset percentage key renders with its %", () => {
    // Short Float is percentage points and has no preset group.
    expect(registry.byKey.get("sh_short")?.presets).toBeNull();
    expect(registry.byKey.get("sh_short")?.unit).toBe("percent");
    expect(formatValue(registry, "sh_short", 5)).toBe("5%");
    expect(describeFilter(registry, { id: "a", key: "sh_short", op: "gte", value: 5 }).text).toBe(
      "Short Float ≥ 5%",
    );
  });

  it("a non-preset dollar key renders with its $", () => {
    expect(registry.byKey.get("targetprice")?.unit).toBe("price");
    expect(formatValue(registry, "targetprice", 200)).toBe("$200");
    expect(describeFilter(registry, { id: "a", key: "targetprice", op: "gte", value: 200 }).text).toBe(
      "Target Price ≥ $200",
    );
  });

  it("leaves no numeric key unit-less by accident", () => {
    // The keys whose column is a bare count/score: a unit would be a lie.
    const plain = new Set(["an_recom", "earningsdate", "ta_rsi"]);
    for (const entry of registry.entries) {
      if (entry.kind !== "number") continue;
      if (plain.has(entry.key)) {
        expect(entry.unit, entry.key).toBe("plain");
        continue;
      }
      expect(entry.unit, `${entry.key} (${entry.field}) has no unit`).not.toBe("plain");
    }
  });

  it("does not need a preset group to know a unit", () => {
    expect(registry.byKey.get("fa_roa")?.presets).toBeNull();
    expect(formatValue(registry, "fa_roa", 7.5)).toBe("7.5%");
    expect(formatValue(registry, "sh_outstanding", 2_000_000)).toBe("2M");
    expect(formatValue(registry, "ta_sma50", 101.25)).toBe("$101.25");
  });
});

describe("the Tickers filter's key", () => {
  const registry = buildRegistry(filterSchemaFixture());

  it("resolves the schema's symbol alias so a reloaded link keeps the filter", () => {
    const entry = registry.byKey.get("symbol");
    expect(entry?.label).toBe("Tickers");
    expect(entry?.kind).toBe("text");
    expect(entry?.ops).toContain("in");
  });

  it("stays out of the picker's key list", () => {
    expect(registry.entries.some((entry) => entry.key === "symbol")).toBe(false);
  });
});
