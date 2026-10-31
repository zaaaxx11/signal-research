import { describe, expect, it } from "vitest";

import {
  AVAILABLE_GROUPS,
  SIGNAL_GROUPS,
  SIGNAL_LIMIT,
  UNAVAILABLE_GROUPS,
  UNAVAILABLE_REASON,
  changeTone,
  formatChange,
  formatPrice,
  formatVolume,
  readSignalRow,
  readSignals,
  signalsPath,
} from "./signals";

/**
 * The signals document, read tolerantly and without invention.
 *
 * The route is new; the client must not be the thing that decides a spelling
 * was wrong. It also must not fill a gap: a row without a symbol is dropped,
 * a missing number stays null and prints as an em dash, and a group the server
 * did not send is empty rather than fabricated.
 */

const row = {
  symbol: "AAPL",
  name: "Apple Inc.",
  price: 211.5,
  change_pct: 1.25,
  volume: 42_000_000,
};

describe("the group list", () => {
  it("has thirteen groups: eleven sourced and two honest refusals", () => {
    expect(SIGNAL_GROUPS).toHaveLength(13);
    expect(AVAILABLE_GROUPS.map((group) => group.key)).toEqual([
      "gainers",
      "losers",
      "new_high",
      "new_low",
      "overbought",
      "oversold",
      "unusual_volume",
      "most_active",
      "most_volatile",
      "insider_buying",
      "insider_selling",
    ]);
    expect(UNAVAILABLE_GROUPS.map((group) => group.key)).toEqual(["upgrades", "downgrades"]);
    for (const group of UNAVAILABLE_GROUPS) {
      expect(group.reason).toBe(UNAVAILABLE_REASON);
    }
    expect(UNAVAILABLE_REASON).toBe("not available — no analyst ratings source");
  });
});

describe("signalsPath", () => {
  it("asks for one document of twenty-row groups", () => {
    expect(SIGNAL_LIMIT).toBe(20);
    expect(signalsPath()).toBe("/market/signals?region=us&per=20");
  });
});

describe("readSignalRow", () => {
  it("reads the contract's five fields", () => {
    expect(readSignalRow(row)).toEqual({
      symbol: "AAPL",
      name: "Apple Inc.",
      price: 211.5,
      changePct: 1.25,
      volume: 42_000_000,
    });
  });

  it("accepts the camelCase spelling of the change", () => {
    expect(readSignalRow({ symbol: "MSFT", changePct: -2 })).toMatchObject({
      symbol: "MSFT",
      changePct: -2,
    });
  });

  it("drops anything it cannot key, and keeps a missing number missing", () => {
    expect(readSignalRow({ name: "No ticker" })).toBeNull();
    expect(readSignalRow(null)).toBeNull();
    const sparse = readSignalRow({ symbol: "XYZ" });
    expect(sparse).toEqual({
      symbol: "XYZ",
      name: "",
      price: null,
      changePct: null,
      volume: null,
    });
  });
});

describe("readSignals", () => {
  it("reads a list of groups keyed by name", () => {
    const report = readSignals({
      as_of: "2026-10-31T20:00:00Z",
      groups: [
        { key: "gainers", rows: [row] },
        { key: "losers", rows: [{ ...row, symbol: "XYZ", change_pct: -3 }] },
      ],
    });
    expect(report.asOf).toBe("2026-10-31T20:00:00Z");
    expect(report.groups["gainers"]).toHaveLength(1);
    expect(report.groups["losers"]?.[0]?.symbol).toBe("XYZ");
  });

  it("reads a map of groups, and rows under `items`", () => {
    const report = readSignals({ groups: { gainers: { items: [row] } } });
    expect(report.groups["gainers"]?.[0]?.symbol).toBe("AAPL");
  });

  it("reads groups at the top level, and ignores unknown keys", () => {
    const report = readSignals({ gainers: [row], made_up: [row] });
    expect(Object.keys(report.groups)).toEqual(["gainers"]);
    expect(report.groups["made_up"]).toBeUndefined();
  });

  it("caps a group at twenty rows", () => {
    const many = Array.from({ length: 35 }, (_, index) => ({ symbol: `S${index}` }));
    const report = readSignals({ groups: { gainers: many } });
    expect(report.groups["gainers"]).toHaveLength(SIGNAL_LIMIT);
  });

  it("does not invent a missing group", () => {
    const report = readSignals({ groups: [] });
    expect(report.groups).toEqual({});
    expect(report.notes).toEqual({});
  });

  it("keeps the server's sentence for a group it answered empty", () => {
    const report = readSignals({
      groups: [
        { id: "overbought", rows: [], note: "no rows matched: rsi_14 >= 70; 2 of 2 rows carry rsi_14" },
        { id: "gainers", rows: [row], note: null },
      ],
    });
    expect(report.notes["overbought"]).toContain("rsi_14 >= 70");
    expect(report.notes["gainers"]).toBeUndefined();
  });
});

describe("formatting", () => {
  it("prints prices, signed changes and compact volumes", () => {
    expect(formatPrice(211.5)).toBe("$211.50");
    expect(formatPrice(4.2)).toBe("$4.20");
    expect(formatPrice(null)).toBe("—");
    expect(formatChange(1.25)).toBe("+1.25%");
    expect(formatChange(-3)).toBe("-3.00%");
    expect(formatChange(null)).toBe("—");
    expect(formatVolume(42_000_000)).toBe("42.00M");
    expect(formatVolume(1_500)).toBe("1.5K");
    expect(formatVolume(null)).toBe("—");
  });

  it("tones a change by sign, never by colour alone", () => {
    expect(changeTone(1)).toBe("up");
    expect(changeTone(-1)).toBe("down");
    expect(changeTone(0)).toBe("flat");
    expect(changeTone(null)).toBe("flat");
  });
});
