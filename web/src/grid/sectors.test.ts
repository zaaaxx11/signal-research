import { describe, expect, it } from "vitest";

import { LIVE_BREADTH } from "@/breadth/__fixtures__/breadth";
import type { BreadthReport } from "@/breadth/report";
import { filterSchemaFixture } from "@/filters/__fixtures__/schema";
import { buildRegistry } from "@/filters/registry";
import { makeFilter, toWire } from "@/filters/state";
import { encodeScreenState, parseScreenState } from "@/filters/urlState";
import { requestBody } from "@/data/screens";

import {
  activeSector,
  isSectorChoice,
  sectorNamesFromReport,
  sectorNamesFromRows,
  SECTOR_KEY,
  withSector,
} from "./sectors";

/**
 * The sector vocabulary.
 *
 * Two claims are load-bearing here: the list is the market's (21 provider
 * labels, no null bucket), and the chosen label reaches the API **unchanged** —
 * the API matches `sec` case-sensitively against the Finviz spelling, so a
 * client that "tidied" `Electronic Technology` into `electronic technology`
 * would filter to nothing.
 */

const registry = buildRegistry(filterSchemaFixture());

describe("the sector list", () => {
  it("offers the 21 provider labels breadth counted, sorted, with no duplicates", () => {
    const names = sectorNamesFromReport(LIVE_BREADTH);

    expect(names).toHaveLength(21);
    expect(names).toContain("Electronic Technology");
    expect(names).toContain("Finance");
    expect([...names]).toEqual([...names].sort((left, right) => left.localeCompare(right)));
    expect(new Set(names).size).toBe(names.length);
  });

  it("(c) never offers a bucket that has no name", () => {
    const withUnnamed: BreadthReport = {
      ...LIVE_BREADTH,
      sectors: [
        ...LIVE_BREADTH.sectors,
        // The scan's null bucket: unlabelled rows still have to be counted, so
        // the bucket exists — but it is not a sector anyone can screen for.
        { name: null, avg_change_pct: null, count: 12, advancing: 6, declining: 6 },
        // A blank label is the same non-answer spelled differently.
        { name: "   ", avg_change_pct: 0.4, count: 3, advancing: 2, declining: 1 },
      ],
    };

    const names = sectorNamesFromReport(withUnnamed);
    expect(names).toHaveLength(21);
    expect(names).not.toContain("");
    expect(names.every((name) => name.trim().length > 0)).toBe(true);
  });

  it("is empty, not invented, when breadth did not answer", () => {
    expect(sectorNamesFromReport(null)).toEqual([]);
  });

  it("falls back to the distinct sector cells of the rows on screen", () => {
    const names = sectorNamesFromRows([
      { symbol: "AAPL", sector: "Electronic Technology" },
      { symbol: "NVDA", sector: "Electronic Technology" },
      { symbol: "JPM", sector: "Finance" },
      { symbol: "??", sector: null },
      { symbol: "??", sector: "" },
      { symbol: "??" },
    ]);

    expect(names).toEqual(["Electronic Technology", "Finance"]);
  });
});

describe("the sector predicate", () => {
  it("recognises only the equality the menu writes", () => {
    expect(isSectorChoice(makeFilter("sec", "eq", "Finance"))).toBe(true);
    expect(isSectorChoice(makeFilter("sec", "contains", "Fin"))).toBe(false);
    expect(isSectorChoice(makeFilter("industry", "eq", "Finance"))).toBe(false);
    expect(isSectorChoice(makeFilter("sec", "eq", "  "))).toBe(false);
  });

  it("replaces the choice without touching the reader's other filters", () => {
    const cap = makeFilter("cap", "gte", 2_000_000_000);
    const tech = makeFilter("sec", "eq", "Finance");

    const next = withSector([cap, tech], "Electronic Technology");
    expect(next.map((filter) => ({ key: filter.key, op: filter.op, value: filter.value }))).toEqual([
      { key: "cap", op: "gte", value: 2_000_000_000 },
      { key: "sec", op: "eq", value: "Electronic Technology" },
    ]);
    expect(activeSector(next)).toBe("Electronic Technology");

    const cleared = withSector(next, null);
    expect(toWire(cleared)).toEqual([{ key: "cap", op: "gte", value: 2_000_000_000 }]);
    expect(activeSector(cleared)).toBeNull();
  });

  it("keeps a `sec contains` predicate from the drawer — it is a different claim", () => {
    const contains = makeFilter("sec", "contains", "Tech");
    const next = withSector([contains], "Electronic Technology");
    expect(toWire(next)).toEqual([
      { key: "sec", op: "contains", value: "Tech" },
      { key: "sec", op: "eq", value: "Electronic Technology" },
    ]);
  });

  it("is idempotent: choosing the sector already shown does not stack predicates", () => {
    const once = withSector([], "Finance");
    const twice = withSector(once, "Finance");
    expect(toWire(twice)).toEqual([{ key: SECTOR_KEY, op: "eq", value: "Finance" }]);
  });
});

describe("the chosen name on the wire", () => {
  it("survives the URL unchanged, so a reload asks the same question", () => {
    const chosen = "Electronic Technology";
    const encoded = encodeScreenState({
      view: "today",
      sort: "market_cap",
      page: 0,
      filters: [makeFilter("sec", "eq", chosen)],
    });

    // The filter spelling is `key:op:value`, so the separators are escaped and
    // the label's own space is the form encoding's `+`.
    expect(encoded).toContain("f=sec%3Aeq%3AElectronic+Technology");

    const parsed = parseScreenState(`?view=today&${encoded}`, registry, ["today"]);
    expect(parsed.dropped).toEqual([]);
    expect(parsed.state.filters).toHaveLength(1);
    expect(parsed.state.filters[0]?.value).toBe(chosen);

    // And the request body carries it byte for byte — the API is case-sensitive.
    const body = requestBody({
      view: "today",
      columns: ["price"],
      limit: 100,
      offset: 0,
      filters: toWire(parsed.state.filters),
    });
    expect((body["criteria"] as { filters: unknown[] }).filters).toEqual([
      { key: "sec", op: "eq", value: "Electronic Technology" },
    ]);
  });

  it("encodes a label carrying a colon or a slash without corrupting it", () => {
    const chosen = "Software: Infrastructure/Devices";
    const encoded = encodeScreenState({
      view: "today",
      sort: "market_cap",
      page: 0,
      filters: [makeFilter("sec", "eq", chosen)],
    });

    expect(encoded).toContain("Software%3A+Infrastructure%2FDevices");
    expect(encoded).not.toContain("Software:");

    // The splitter reads the first two separators and treats the rest as the
    // value, so a colon inside a label is data, not syntax.
    const parsed = parseScreenState(`?${encoded}`, registry, ["today"]);
    expect(parsed.dropped).toEqual([]);
    expect(parsed.state.filters[0]?.value).toBe(chosen);

    expect(
      (
        requestBody({
          view: "today",
          columns: ["price"],
          filters: toWire(parsed.state.filters),
        })["criteria"] as { filters: unknown[] }
      ).filters,
    ).toEqual([{ key: "sec", op: "eq", value: chosen }]);
  });

  it("keeps a lower-case label lower-case: no client-side title casing", () => {
    const parsed = parseScreenState(
      "?view=today&f=sec%3Aeq%3Aelectronic+technology",
      registry,
      ["today"],
    );
    expect(parsed.dropped).toEqual([]);
    expect(parsed.state.filters[0]?.value).toBe("electronic technology");
  });
});
