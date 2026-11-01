import { describe, expect, it } from "vitest";

import { axisPrice, axisTime, barTime, estimateLabelWidth, pricePrecision } from "./format";

/** 2026-01-02T14:30:00Z, the fixtures' clock. */
const T = 1_767_364_200;

describe("axisTime", () => {
  it("uses a clock for 1D, a date for the multi-day ranges, a month for 1Y", () => {
    expect(axisTime(T, "1d", "UTC")).toBe("14:30");
    expect(axisTime(T, "5d", "UTC")).toBe("Jan 2");
    expect(axisTime(T, "1mo", "UTC")).toBe("Jan 2");
    expect(axisTime(T, "6mo", "UTC")).toBe("Jan 2");
    expect(axisTime(T, "1y", "UTC")).toBe("Jan");
  });

  it("renders in the payload's zone, not the runner's", () => {
    expect(axisTime(T, "1d", "America/New_York")).toBe("09:30");
  });

  it("falls back to UTC for a zone the runtime does not know", () => {
    expect(axisTime(T, "1d", "Mars/Olympus_Mons")).toBe("14:30");
    expect(axisTime(T, "1d", "")).toBe("14:30");
  });

  it("answers an em dash rather than a crash for a broken timestamp", () => {
    expect(barTime(Number.NaN, "1d", "UTC")).toBe("—");
  });
});

describe("barTime", () => {
  it("carries the day and the clock for an intraday range", () => {
    expect(barTime(T, "1d", "UTC")).toBe("Jan 2, 14:30");
    expect(barTime(T, "1y", "UTC")).toBe("Jan 2, 2026");
  });
});

describe("axisPrice", () => {
  it("prints decimals as a function of the grid step", () => {
    expect(axisPrice(6800, 50)).toBe("6,800");
    expect(axisPrice(18.25, 0.05)).toBe("18.25");
    expect(axisPrice(125, 1)).toBe("125");
    expect(axisPrice(0.012, 0.001)).toBe("0.012");
    expect(axisPrice(2.5, 2.5)).toBe("2.5");
  });

  it("has a precision for every step", () => {
    expect(pricePrecision(50)).toBe(0);
    expect(pricePrecision(5)).toBe(0);
    expect(pricePrecision(0.5)).toBe(1);
    expect(pricePrecision(0.05)).toBe(2);
    expect(pricePrecision(0.005)).toBe(3);
    expect(pricePrecision(0)).toBe(2);
    expect(pricePrecision(Number.NaN)).toBe(2);
  });

  it("estimates label widths monotonically, so the axis filter is honest", () => {
    expect(estimateLabelWidth("Jan 2")).toBeGreaterThan(0);
    expect(estimateLabelWidth("Jan 22")).toBeGreaterThan(estimateLabelWidth("Jan 2"));
  });
});
