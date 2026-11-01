import { describe, expect, it } from "vitest";

import { clockLabel, formatCount, marketCapLabel, metricLabel, signedPercent, toneClass } from "./format";

/**
 * The map's words. The three rules the rest of the product holds itself to are
 * restated here because a map is the surface most likely to break them: a
 * legend that prints `-0.00%`, or a colour that claims a direction the digits
 * do not.
 */

describe("signedPercent", () => {
  it("puts the sign in the string, not only in the colour", () => {
    expect(signedPercent(2.1)).toEqual({ text: "+2.10%", sign: 1, absent: false });
    expect(signedPercent(-1.4)).toEqual({ text: "-1.40%", sign: -1, absent: false });
  });

  it("claims no direction for a magnitude that rounds to zero", () => {
    expect(signedPercent(-0.001).text).toBe("0.00%");
    expect(signedPercent(-0.001).sign).toBe(0);
  });

  it("prints n/a for an absent value, never 0.00%", () => {
    expect(signedPercent(null)).toEqual({ text: "n/a", sign: null, absent: true });
    expect(signedPercent(Number.NaN).absent).toBe(true);
  });

  it("maps a sign to a token class", () => {
    expect(toneClass(1)).toBe("text-up");
    expect(toneClass(-1)).toBe("text-down");
    expect(toneClass(0)).toBe("text-flat");
    expect(toneClass(null)).toBe("text-fg-subtle");
  });
});

describe("labels", () => {
  it("counts with commas, never compacted", () => {
    expect(formatCount(5576)).toBe("5,576");
  });

  it("states market cap in the unit the payload uses: billions", () => {
    expect(marketCapLabel(5034.7)).toBe("$5.03T");
    expect(marketCapLabel(612.4)).toBe("$612.4B");
    expect(marketCapLabel(0.4)).toBe("$400M");
    expect(marketCapLabel(0)).toBe("—");
  });

  it("names the metric, and does not mislabel an unknown one", () => {
    expect(metricLabel("change")).toBe("1-Day performance");
    expect(metricLabel("change_1w")).toBe("1-Week performance");
    expect(metricLabel("rsi")).toBe("rsi");
  });

  it("prints a zoned clock in UTC and never invents a zone", () => {
    expect(clockLabel("2026-10-31T20:58:12+00:00")).toBe("20:58:12Z");
    expect(clockLabel("2026-10-31T20:58:12")).toBe("20:58:12 (unzoned)");
    expect(clockLabel("")).toBe("—");
  });
});
