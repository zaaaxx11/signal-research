import { describe, expect, it } from "vitest";

import {
  COMPARE_COLUMNS,
  COMPARE_GROUPS,
  MAX_REQUEST_COLUMNS,
  bestValue,
  isBest,
  metricLabels,
} from "./metrics";
import { formatAsOf, formatMetric, formatPrice } from "./format";

/**
 * The page's data contract, without a DOM:
 *
 * * seven named sections, metrics as rows, companies as columns;
 * * every unit the catalogue produces has exactly one printed form;
 * * absent is an em dash, and a real zero is a number;
 * * "best" only exists where better is a direction, and the marker follows it.
 */

describe("the comparison's metric catalogue", () => {
  it("publishes the seven sections in reading order", () => {
    expect(COMPARE_GROUPS.map((group) => group.label)).toEqual([
      "Valuation",
      "Growth",
      "Profitability",
      "Financial health",
      "Ownership",
      "Performance",
      "Price",
    ]);
  });

  it("asks for every printed column plus the company name, once", () => {
    const keys = COMPARE_COLUMNS;
    expect(new Set(keys).size).toBe(keys.length);
    expect(keys[0]).toBe("company");
    for (const group of COMPARE_GROUPS) {
      for (const metric of group.metrics) expect(keys).toContain(metric.key);
    }
    // The API refuses more than forty cells; the page must fit its own table.
    expect(keys.length).toBeLessThanOrEqual(MAX_REQUEST_COLUMNS);
  });

  it("has a label for every column and no empty one", () => {
    const labels = metricLabels();
    for (const key of COMPARE_COLUMNS) {
      if (key === "company") continue;
      expect(labels.get(key)?.length ?? 0).toBeGreaterThan(0);
    }
  });
});

describe("formatMetric", () => {
  it("prints a ratio at two places", () => {
    expect(formatMetric(39.5068, "ratio")).toBe("39.51");
    expect(formatMetric(46.8, "ratio")).toBe("46.80");
  });

  it("prints a percent with its sign and unit, keeping a real minus", () => {
    expect(formatMetric(48.6529, "percent")).toBe("48.65%");
    expect(formatMetric(-1.2266, "percent")).toBe("-1.23%");
  });

  it("prints dollars, and compact dollars for a market cap", () => {
    expect(formatMetric(344.63, "usd")).toBe("$344.63");
    expect(formatMetric(344.63, "usd", true)).toBe("$344.63");
    expect(formatMetric(5_029_592_216_964, "usd", true)).toBe("$5.03T");
  });

  it("prints a count the way the grid does", () => {
    expect(formatMetric(63_020_000, "count")).toBe("63.02M");
    expect(formatMetric(0, "count")).toBe("0");
  });

  it("prints an em dash for every kind of absent, and zero as a number", () => {
    expect(formatMetric(null, "ratio")).toBe("—");
    expect(formatMetric(undefined, "percent")).toBe("—");
    expect(formatMetric("", "usd")).toBe("—");
    expect(formatMetric(Number.NaN, "count")).toBe("—");
    expect(formatMetric(0, "ratio")).toBe("0.00");
  });

  it("prints a price for the column header", () => {
    expect(formatPrice(344.63)).toBe("$344.63");
    expect(formatPrice(null)).toBe("—");
  });

  it("prints the server's stamp as a clock, and an unparseable one as served", () => {
    expect(formatAsOf("2026-11-02T11:02:25.845291+00:00")).toBe("11:02 UTC");
    expect(formatAsOf("2026-11-02T23:59:00Z")).toBe("23:59 UTC");
    expect(formatAsOf("not a date")).toBe("not a date");
    expect(formatAsOf(null)).toBeNull();
    expect(formatAsOf("  ")).toBeNull();
  });
});

describe("bestValue / isBest", () => {
  it("finds the cheapest multiple when lower is better", () => {
    const best = bestValue([39.5, 36.1, null, 30.1], "low");
    expect(best).toBe(30.1);
    expect(isBest(30.1, best)).toBe(true);
    expect(isBest(36.1, best)).toBe(false);
    expect(isBest(null, best)).toBe(false);
  });

  it("never calls a zero or negative multiple the cheapest valuation", () => {
    // A P/E of 0 is a missing number wearing a number's clothes, and a negative
    // one means losses — neither is "cheaper" than a real multiple.
    expect(bestValue([0, 36.07], "low", true)).toBe(36.07);
    expect(bestValue([-4.2, 12.1], "low", true)).toBe(12.1);
    expect(bestValue([0, -4.2], "low", true)).toBeNull();
    // Debt/equity is not a multiple: zero debt really is the best answer.
    expect(bestValue([0, 1.4], "low")).toBe(0);
  });

  it("finds the highest margin when higher is better", () => {
    expect(bestValue([48.6, 68.7, -3], "high")).toBe(68.7);
  });

  it("does not name a winner in a one-company or empty row", () => {
    expect(bestValue([42], "high")).toBeNull();
    expect(bestValue([null, null], "low")).toBeNull();
  });

  it("has no direction for a metric where better means nothing", () => {
    expect(bestValue([1, 2, 3], null)).toBeNull();
  });
});
