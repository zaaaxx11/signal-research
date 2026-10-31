import { describe, expect, it } from "vitest";

import {
  groupMetricRows,
  humanizeKey,
  METRIC_COUNT,
  METRIC_DEFINITIONS,
  METRIC_GROUPS,
  metricCell,
} from "./metrics";
import { fundamentalsPayload, sparseFundamentalsPayload } from "./__fixtures__/detail";

describe("the 43-key catalogue", () => {
  it("lists every metric exactly once, each in a known group", () => {
    expect(METRIC_COUNT).toBe(43);
    const keys = METRIC_DEFINITIONS.map((definition) => definition.key);
    expect(new Set(keys).size).toBe(43);
    for (const definition of METRIC_DEFINITIONS) {
      expect(METRIC_GROUPS).toContain(definition.group);
      expect(definition.label.length).toBeGreaterThan(0);
    }
  });

  it("has the four requested groups plus profile and other", () => {
    const groups = new Set(METRIC_DEFINITIONS.map((definition) => definition.group));
    for (const group of ["valuation", "growth", "quality", "income"] as const) {
      expect(groups.has(group)).toBe(true);
    }
    expect(groups.has("profile")).toBe(true);
    expect(groups.has("other")).toBe(false);
  });

  it("draws all 43 rows even when the payload answers three fields", () => {
    const sections = groupMetricRows(
      (sparseFundamentalsPayload()["metrics"] ?? {}) as Record<
        string,
        { value: unknown; unit: string }
      >,
    );
    const rows = sections.flatMap((section) => section.rows);
    expect(rows).toHaveLength(43);

    const reported = rows.filter((row) => !metricCell(row.value, row.unit).absent);
    expect(reported.map((row) => row.key).sort()).toEqual(["company", "exchange", "ticker"]);
    expect(rows.find((row) => row.key === "assets")?.value).toBeUndefined();
  });

  it("appends an unknown payload key to Other instead of dropping it", () => {
    const metrics = (fundamentalsPayload({ brand_new_metric: 7 })["metrics"] ?? {}) as Record<
      string,
      { value: unknown; unit: string }
    >;
    const sections = groupMetricRows(metrics);
    const other = sections.find((section) => section.group === "other");
    expect(other?.rows.map((row) => row.key)).toEqual(["brand_new_metric"]);
    expect(other?.rows[0]?.label).toBe("Brand new metric");
    expect(sections.flatMap((section) => section.rows)).toHaveLength(44);
  });

  it("humanises an unknown key without losing its words", () => {
    expect(humanizeKey("a_very_long_metric_key")).toBe("A very long metric key");
    expect(humanizeKey("x")).toBe("X");
  });
});

describe("metric cells carry their unit", () => {
  it("formats money, percent, ratio and share units differently", () => {
    expect(metricCell(466_823_000_000, "USD")).toEqual({
      text: "466.82B",
      unit: "USD",
      absent: false,
    });
    expect(metricCell(48.65291555900202, "pct")).toEqual({
      text: "48.65",
      unit: "%",
      absent: false,
    });
    expect(metricCell(1.0032948046555858, "ratio")).toEqual({
      text: "1.00",
      unit: "×",
      absent: false,
    });
    expect(metricCell(14_608_963_000, "shares")).toEqual({
      text: "14.61B",
      unit: "sh",
      absent: false,
    });
    expect(metricCell(8.72, "unknown")).toEqual({ text: "8.72", unit: "", absent: false });
    expect(metricCell("Apple Inc.", "text")).toEqual({
      text: "Apple Inc.",
      unit: "",
      absent: false,
    });
  });

  it("never prints a missing value as zero", () => {
    expect(metricCell(null, "USD")).toEqual({ text: "—", unit: "USD", absent: true });
    expect(metricCell(undefined, "pct")).toEqual({ text: "—", unit: "%", absent: true });
    expect(metricCell("", "USD").absent).toBe(true);
    expect(metricCell(Number.NaN, "USD").absent).toBe(true);
  });

  it("keeps the sub-cent honesty rule after moving the $ to the unit column", () => {
    expect(metricCell(0.004, "USD").text).toBe("<0.01");
    expect(metricCell(0, "USD").text).toBe("0.00");
  });
});

/**
 * The detail panel's exchange cell.
 *
 * The grid shows codes, the security master sends names, and both must read the
 * same way. The label comes from `lib/exchanges.ts` — the same leaf the grid
 * uses — because this panel is a lazy chunk and pulling `grid/columnMeta` in to
 * read one map would put the whole 12 KB column table in it.
 */
describe("the profile group's exchange", () => {
  function exchangeRow(payload: Record<string, { value: unknown; unit: string }>) {
    const rows = groupMetricRows(payload).flatMap((section) => section.rows);
    const row = rows.find((candidate) => candidate.key === "exchange");
    return { rows, row };
  }

  it("labels a code the same way the grid cell does", () => {
    const { row } = exchangeRow({ exchange: { value: "XNAS", unit: "text" } });
    expect(row?.value).toBe("NASDAQ");
    expect(metricCell(row?.value, row?.unit ?? "text").text).toBe("NASDAQ");
  });

  it("leaves a name the master already spelled alone", () => {
    const { row } = exchangeRow({ exchange: { value: "Nasdaq", unit: "text" } });
    expect(metricCell(row?.value, "text").text).toBe("Nasdaq");
  });

  it("still draws all 43 rows when the payload has no exchange", () => {
    const { rows, row } = exchangeRow({});
    expect(rows).toHaveLength(43);
    expect(metricCell(row?.value, "text").absent).toBe(true);
  });
});
