import { describe, expect, it } from "vitest";

import {
  barLabel,
  barShare,
  extremeLabel,
  formatAsOf,
  formatCount,
  signedPercent,
  toneClass,
} from "./format";

/**
 * The formatting half: every rule here is one the old client-side breadth got
 * wrong — an absent value printed as zero, a direction carried by colour, a bar
 * width that became `NaN`.
 */

describe("formatCount", () => {
  it("groups thousands and never compacts", () => {
    expect(formatCount(5678)).toBe("5,678");
    expect(formatCount(189)).toBe("189");
    expect(formatCount(0)).toBe("0");
  });
});

describe("signedPercent", () => {
  it("carries the sign in the text", () => {
    expect(signedPercent(0.4765)).toEqual({ text: "+0.48%", sign: 1, absent: false });
    expect(signedPercent(-1.0998)).toEqual({ text: "-1.10%", sign: -1, absent: false });
  });

  it("prints an exact zero as flat, with no sign", () => {
    expect(signedPercent(0)).toEqual({ text: "0.00%", sign: 0, absent: false });
  });

  it("does not claim a direction for a magnitude that rounds to zero", () => {
    // `-0.00%` in red is a direction the digits deny.
    expect(signedPercent(-0.001)).toEqual({ text: "0.00%", sign: 0, absent: false });
    expect(toneClass(signedPercent(-0.001).sign)).toBe("text-flat");
  });

  it("prints n/a for an absent average and paints it as neither up nor down", () => {
    expect(signedPercent(null)).toEqual({ text: "n/a", sign: null, absent: true });
    expect(signedPercent(undefined).text).toBe("n/a");
    expect(signedPercent(Number.NaN).text).toBe("n/a");
    expect(toneClass(null)).toBe("text-fg-subtle");
  });

  it("agrees with its own sign for a value that rounds to the next digit", () => {
    expect(signedPercent(-4.999)).toEqual({ text: "-5.00%", sign: -1, absent: false });
    expect(signedPercent(4.999)).toEqual({ text: "+5.00%", sign: 1, absent: false });
  });
});

describe("toneClass", () => {
  it("maps the printed sign onto the token pairs", () => {
    expect(toneClass(1)).toBe("text-up");
    expect(toneClass(-1)).toBe("text-down");
    expect(toneClass(0)).toBe("text-flat");
  });
});

describe("extremeLabel", () => {
  it("states the denominator the count was measured against", () => {
    expect(extremeLabel(46, 5712)).toBe("46 / 5,712 reported");
    expect(extremeLabel(45, 5675)).toBe("45 / 5,675 reported");
  });

  it("says so when the server measured no basis at all", () => {
    expect(extremeLabel(0, 0)).toBe("0 / no basis");
    expect(extremeLabel(3, null)).toBe("3 / no basis");
  });
});

describe("formatAsOf", () => {
  it("prints the UTC clock of an offset timestamp", () => {
    expect(formatAsOf("2026-10-31T09:21:58+00:00")).toBe("09:21:58Z");
    expect(formatAsOf("2026-10-31T09:21:58Z")).toBe("09:21:58Z");
    expect(formatAsOf("2026-10-31T11:21:58+02:00")).toBe("09:21:58Z");
  });

  it("does not stamp Z on a timestamp that carries no zone", () => {
    expect(formatAsOf("2026-10-31T09:21:58")).toBe("09:21:58");
  });

  it("degrades honestly on garbage rather than inventing a time", () => {
    expect(formatAsOf("")).toBe("—");
    expect(formatAsOf("not a date")).toBe("not a date");
  });
});

describe("barShare", () => {
  it("divides the moving names by their own sum", () => {
    expect(barShare(1557, 5489)).toBeCloseTo(28.366, 3);
    expect(barShare(3932, 5489)).toBeCloseTo(71.634, 3);
  });

  it("never divides by zero when nothing moved", () => {
    expect(barShare(0, 0)).toBe(0);
    expect(Number.isNaN(barShare(0, 0))).toBe(false);
  });

  it("clamps a share the payload cannot legitimately produce", () => {
    expect(barShare(10, 5)).toBe(100);
    expect(barShare(-1, 5)).toBe(0);
    expect(barShare(1, -5)).toBe(0);
    expect(barShare(Number.NaN, 5)).toBe(0);
  });
});

describe("barLabel", () => {
  it("names the bar with both counts and the universe", () => {
    const label = barLabel({
      advancing: 1557,
      declining: 3932,
      unchanged: 189,
      universe_total: 5678,
    });

    expect(label).toBe("Advancing 1,557 versus declining 3,932 over 5,678 scanned; 189 unchanged.");
  });

  it("says nothing moved rather than describing a zero-width bar", () => {
    const label = barLabel({
      advancing: 0,
      declining: 0,
      unchanged: 5678,
      universe_total: 5678,
    });

    expect(label).toContain("No advancing or declining names");
    expect(label).toContain("5,678 unchanged");
  });
});
