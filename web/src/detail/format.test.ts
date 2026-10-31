import { describe, expect, it } from "vitest";

import {
  formatDate,
  formatRelativeTime,
  formatShares,
  formatTradeMoney,
  insiderType,
  safeHref,
  tradeValue,
} from "./format";

const NOW = Date.parse("2026-10-31T12:00:00Z");

describe("relative time", () => {
  it("walks from just now to years", () => {
    expect(formatRelativeTime("2026-10-31T11:59:40Z", NOW)).toBe("just now");
    expect(formatRelativeTime("2026-10-31T11:48:00Z", NOW)).toBe("12m ago");
    expect(formatRelativeTime("2026-10-31T09:00:00Z", NOW)).toBe("3h ago");
    expect(formatRelativeTime("2026-10-29T12:00:00Z", NOW)).toBe("2d ago");
    expect(formatRelativeTime("2026-10-10T12:00:00Z", NOW)).toBe("3w ago");
    expect(formatRelativeTime("2026-06-30T12:00:00Z", NOW)).toBe("4mo ago");
    expect(formatRelativeTime("2025-10-31T12:00:00Z", NOW)).toBe("1y ago");
  });

  it("calls clock skew just now, not a negative age", () => {
    expect(formatRelativeTime("2026-10-31T12:05:00Z", NOW)).toBe("just now");
  });

  it("prints the placeholder for an unparseable time", () => {
    expect(formatRelativeTime("", NOW)).toBe("—");
    expect(formatRelativeTime("not-a-date", NOW)).toBe("—");
  });
});

describe("safeHref", () => {
  it("admits absolute http and https and canonicalises them", () => {
    expect(safeHref("https://news.google.com/rss/articles/abc?oc=5")).toBe(
      "https://news.google.com/rss/articles/abc?oc=5",
    );
    expect(safeHref("http://example.com/a b")).toBe("http://example.com/a%20b");
  });

  it("refuses javascript:, data:, vbscript: and their case variants", () => {
    expect(safeHref("javascript:alert(1)")).toBeNull();
    expect(safeHref("JaVaScRiPt:alert(1)")).toBeNull();
    expect(safeHref("data:text/html,<script>alert(1)</script>")).toBeNull();
    expect(safeHref("vbscript:msgbox(1)")).toBeNull();
  });

  it("refuses a scheme split by a control character", () => {
    expect(safeHref("java\nscript:alert(1)")).toBeNull();
    expect(safeHref("java\tscript:alert(1)")).toBeNull();
    expect(safeHref("\u0000javascript:alert(1)")).toBeNull();
  });

  it("refuses relative links and embedded credentials", () => {
    expect(safeHref("/rss/articles/abc")).toBeNull();
    expect(safeHref("//news.google.com/a")).toBeNull();
    expect(safeHref("https://user:pass@example.com/a")).toBeNull();
    expect(safeHref(null)).toBeNull();
    expect(safeHref(42)).toBeNull();
  });
});

describe("insider helpers", () => {
  it("translates a code and keeps an unknown one visible", () => {
    expect(insiderType("S")).toEqual({ short: "Sale", long: "Open-market sale" });
    expect(insiderType("p").short).toBe("Buy");
    expect(insiderType("Q")).toEqual({ short: "Q", long: "Transaction code Q" });
    expect(insiderType(null).short).toBe("—");
  });

  it("multiplies shares by price, and refuses a missing leg", () => {
    expect(tradeValue(2399, 343.36)).toBeCloseTo(823_720.64, 2);
    expect(tradeValue(151, null)).toBeNull();
    expect(tradeValue(null, 12)).toBeNull();
    expect(tradeValue(Number.NaN, 12)).toBeNull();
  });

  it("formats shares and trade money with the placeholder for absence", () => {
    expect(formatShares(2399)).toBe("2,399");
    expect(formatShares(14_608_963_000)).toBe("14.61B");
    expect(formatShares(null)).toBe("—");
    expect(formatTradeMoney(823_720.64)).toBe("$823.72K");
    expect(formatTradeMoney(null)).toBe("—");
  });

  it("keeps a bare date bare and normalises an instant", () => {
    expect(formatDate("2026-10-27")).toBe("2026-10-27");
    expect(formatDate("2026-10-27T13:00:00Z")).toBe("2026-10-27");
    expect(formatDate(undefined)).toBe("—");
  });
});
