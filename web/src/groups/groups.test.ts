import { describe, expect, it } from "vitest";

import {
  GROUPS_REGION,
  GroupsMalformed,
  advanceShare,
  breadthLabel,
  groupsPath,
  readGroups,
  screenerHref,
  type GroupRow,
} from "./groups";

/**
 * The groups document's contract, pinned without a DOM:
 *
 * * one path, and the region is in it;
 * * the server's sort is honoured and made explicit (change descending, an
 *   unmeasured average last), never recomputed;
 * * a sector label survives the trip into the screener filter — the href is
 *   encoded and decodes back to the exact string the server sent;
 * * the bar's proportion is arithmetic over counts, so it cannot claim a
 *   precision the counts do not have.
 */

const ROWS = [
  {
    id: "energy",
    label: "Energy",
    count: 300,
    avg_change: -1.2,
    adv: 90,
    dec: 200,
    unchanged: 10,
    market_cap: 2_300_000_000_000,
  },
  {
    id: "technology",
    label: "Technology",
    count: 700,
    avg_change: 1.42,
    adv: 400,
    dec: 300,
    unchanged: 0,
    market_cap: 15_200_000_000_000,
  },
  {
    id: "health",
    label: "Health Care",
    count: 600,
    avg_change: 0,
    adv: 210,
    dec: 380,
    unchanged: 10,
    market_cap: 8_100_000_000_000,
  },
];

function document(overrides: Record<string, unknown> = {}) {
  return {
    as_of: "2026-11-02T14:30:00Z",
    region: "us",
    universe_total: 5_678,
    groups: ROWS,
    ...overrides,
  };
}

function labels(rows: readonly GroupRow[]): string[] {
  return rows.map((row) => row.label);
}

describe("groupsPath", () => {
  it("asks for one region and the default group-by/sort", () => {
    expect(GROUPS_REGION).toBe("us");
    expect(groupsPath()).toBe(
      "/market/groups?region=us&group_by=sector&sort=avg_change&direction=desc",
    );
  });

  it("carries a custom group-by group and sort direction", () => {
    expect(groupsPath("eu", "industry", "count", "asc")).toBe(
      "/market/groups?region=eu&group_by=industry&sort=count&direction=asc",
    );
    expect(groupsPath("us", "capitalization", "market_cap", "desc")).toBe(
      "/market/groups?region=us&group_by=capitalization&sort=market_cap&direction=desc",
    );
  });
});

describe("readGroups", () => {
  it("keeps every row the server sent, in change-descending order", () => {
    const report = readGroups(document());
    expect(report.region).toBe("us");
    expect(report.universeTotal).toBe(5_678);
    expect(labels(report.rows)).toEqual(["Technology", "Health Care", "Energy"]);
    expect(report.rows[0]).toEqual({
      id: "technology",
      label: "Technology",
      count: 700,
      avgChange: 1.42,
      adv: 400,
      dec: 300,
      unchanged: 0,
      marketCap: 15_200_000_000_000,
      perf1w: null,
      perf1m: null,
      perf3m: null,
    });
  });

  it("puts an unmeasured average last instead of treating it as flat", () => {
    const report = readGroups(
      document({
        groups: [
          { id: "unmeasured", label: "Unmeasured", count: 5, avg_change: null, adv: 1, dec: 0, unchanged: 4, market_cap: null },
          ...ROWS,
        ],
      }),
    );
    expect(labels(report.rows)).toEqual(["Technology", "Health Care", "Energy", "Unmeasured"]);
    expect(report.rows[3]?.avgChange).toBeNull();
    expect(report.rows[3]?.marketCap).toBeNull();
  });

  it("keeps a missing count missing rather than printing a zero it never received", () => {
    const report = readGroups(
      document({ groups: [{ id: "x", label: "X", count: null, avg_change: 0.5, adv: null, dec: null }] }),
    );
    expect(report.rows[0]?.count).toBeNull();
    expect(report.rows[0]?.adv).toBeNull();
    expect(report.rows[0]?.unchanged).toBeNull();

    // An empty string is a blank field, not `Number("") === 0`.
    const blank = readGroups(
      document({ groups: [{ id: "y", label: "Y", count: "", avg_change: "", adv: " ", dec: 3 }] }),
    );
    expect(blank.rows[0]?.count).toBeNull();
    expect(blank.rows[0]?.avgChange).toBeNull();
    expect(blank.rows[0]?.adv).toBeNull();
    expect(blank.rows[0]?.dec).toBe(3);
  });

  it("keeps the label verbatim, because that is the string the screener filters on", () => {
    const report = readGroups(
      document({ groups: [{ id: "a", label: "Health Care", count: 1, avg_change: 1 }] }),
    );
    expect(report.rows[0]?.label).toBe("Health Care");
  });

  it("drops a group with no id and no label: a row with no identity cannot be keyed or read", () => {
    const report = readGroups(
      document({ groups: [{ count: 4, avg_change: 1 }, { label: "Kept", id: "kept", count: 1, avg_change: 2 }] }),
    );
    expect(labels(report.rows)).toEqual(["Kept"]);
  });

  it("refuses a payload that is not a groups document", () => {
    expect(() => readGroups(null)).toThrow(GroupsMalformed);
    expect(() => readGroups({})).toThrow(GroupsMalformed);
    expect(() => readGroups({ groups: "nope" })).toThrow(GroupsMalformed);
  });
});

describe("screenerHref", () => {
  it("round-trips a plain label", () => {
    const href = screenerHref("Technology");
    expect(href).toBe("/screener?f=sec:eq:Technology");
    expect(new URLSearchParams(href.slice(href.indexOf("?") + 1)).get("f")?.replace(/^sec:eq:/, "")).toBe("Technology");
  });

  it("round-trips a label with spaces, punctuation and reserved characters", () => {
    const label = 'Consumer Discretionary & Staples #2 + "Q" 100%';
    const href = screenerHref(label);

    // The label is encoded, not concatenated: no raw `&`, `#`, `+` or quote can
    // end the value early or smuggle a second parameter in.
    expect(href).toBe(`/screener?f=sec:eq:${encodeURIComponent(label)}`);
    expect(href).not.toMatch(/[#"]/);
    expect(href.split("&")).toHaveLength(1);

    // …and the round trip returns the same string, character for character.
    const [, query = ""] = href.split("?");
    expect(new URLSearchParams(query).get("f")?.replace(/^sec:eq:/, "")).toBe(label);
    expect(decodeURIComponent(href.split("f=sec:eq:")[1] ?? "")).toBe(label);
  });
});

describe("advanceShare", () => {
  it("takes its proportion from the counts", () => {
    expect(advanceShare(400, 300)).toBe(57.1);
    expect(advanceShare(3, 1)).toBe(75);
    expect(advanceShare(1, 3)).toBe(25);
    expect(advanceShare(0, 4)).toBe(0);
  });

  it("is flat, not NaN, when nothing moved", () => {
    expect(advanceShare(0, 0)).toBe(0);
    expect(advanceShare(null, 4)).toBe(0);
    expect(advanceShare(4, null)).toBe(0);
  });
});

describe("breadthLabel", () => {
  it("names both counts, so the bar is not the only channel", () => {
    const row: GroupRow = {
      id: "technology",
      label: "Technology",
      count: 700,
      avgChange: 1.42,
      adv: 400,
      dec: 300,
      unchanged: 0,
      marketCap: null,
      perf1w: null,
      perf1m: null,
      perf3m: null,
    };
    expect(breadthLabel(row)).toBe("Technology: 400 advancing, 300 declining");
  });

  it("says so when the server reported no split", () => {
    const row: GroupRow = {
      id: "x",
      label: "X",
      count: null,
      avgChange: null,
      adv: null,
      dec: null,
      unchanged: null,
      marketCap: null,
      perf1w: null,
      perf1m: null,
      perf3m: null,
    };
    expect(breadthLabel(row)).toMatch(/not reported/i);
  });
});
