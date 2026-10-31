import { describe, expect, it } from "vitest";

import type { ActiveFilter } from "@/filters/types";

import {
  EXCHANGE_KEY,
  activeExchanges,
  exchangeVenuesFromRows,
  isExchangeChoice,
  venueBucketsFromReport,
  withExchange,
} from "./exchanges";

/**
 * The exchange *vocabulary*: what the control reads, what it writes, and what
 * it refuses to own.
 *
 * The load-bearing rule is that a set of venues is **one** predicate. Two
 * `exch eq` rows are ANDed by the API into zero rows, so "NASDAQ or NYSE" is
 * impossible unless the control writes `in`. The second rule is that `ne` and
 * `contains` are somebody else's claim and survive untouched — the control
 * cannot express them, so it must not swallow their chips.
 */

const CAP: ActiveFilter = { id: "f-cap", key: "cap", op: "gte", value: 2_000_000_000 };

describe("EXCHANGE_KEY", () => {
  it("is the API's key for the column, not the label", () => {
    expect(EXCHANGE_KEY).toBe("exch");
  });
});

describe("activeExchanges", () => {
  it("reads an equality and a set", () => {
    expect(activeExchanges([{ id: "a", key: "exch", op: "eq", value: "XNAS" }])).toEqual(["XNAS"]);
    expect(
      activeExchanges([{ id: "a", key: "exch", op: "in", value: ["XNYS", "XNAS"] }]),
    ).toEqual(["XNAS", "XNYS"]);
  });

  it("ignores every operator the control does not own", () => {
    // A negative or a substring match is a different screen; the menu cannot
    // show it, so it must not claim it.
    expect(activeExchanges([{ id: "a", key: "exch", op: "ne", value: "XNAS" }])).toEqual([]);
    expect(activeExchanges([{ id: "a", key: "exch", op: "contains", value: "nas" }])).toEqual([]);
  });

  it("reads nothing from a blank or a non-string set element", () => {
    expect(activeExchanges([{ id: "a", key: "exch", op: "eq", value: "   " }])).toEqual([]);
    expect(activeExchanges([{ id: "a", key: "exch", op: "in", value: [] }])).toEqual([]);
    expect(activeExchanges([{ id: "a", key: "exch", op: "in", value: ["", "XNAS"] }])).toEqual([
      "XNAS",
    ]);
  });

  it("takes the last owned predicate — the one a choice replaced", () => {
    expect(
      activeExchanges([
        { id: "a", key: "exch", op: "eq", value: "XASE" },
        { id: "b", key: "exch", op: "in", value: ["XNAS", "XNYS"] },
      ]),
    ).toEqual(["XNAS", "XNYS"]);
  });
});

describe("withExchange", () => {
  it("writes an equality for one code and a set for several", () => {
    expect(withExchange([CAP], ["XNAS"])).toEqual([
      CAP,
      { id: expect.any(String), key: "exch", op: "eq", value: "XNAS" },
    ]);
    expect(withExchange([CAP], ["XNYS", "XNAS"])).toEqual([
      CAP,
      { id: expect.any(String), key: "exch", op: "in", value: ["XNAS", "XNYS"] },
    ]);
  });

  it("removes the predicate when the set is empty", () => {
    const filters = [CAP, { id: "a", key: "exch", op: "in", value: ["XNAS"] } as ActiveFilter];
    expect(withExchange(filters, [])).toEqual([CAP]);
    expect(withExchange([], [])).toEqual([]);
  });

  it("replaces the predicate it already wrote instead of stacking a second one", () => {
    const filters: ActiveFilter[] = [
      CAP,
      { id: "a", key: "exch", op: "eq", value: "XASE" },
    ];
    const next = withExchange(filters, ["XNAS", "XNYS"]);
    expect(next.filter((filter) => filter.key === "exch")).toHaveLength(1);
    expect(next).toEqual([CAP, { id: expect.any(String), key: "exch", op: "in", value: ["XNAS", "XNYS"] }]);
  });

  it("keeps the reader's other filters every time", () => {
    for (const codes of [[], ["XNAS"], ["XNAS", "XNYS"]]) {
      const next = withExchange([CAP], codes);
      expect(next[0]).toEqual(CAP);
    }
  });

  it("leaves `ne` and `contains` alone — they are not its claim", () => {
    const filters: ActiveFilter[] = [
      { id: "a", key: "exch", op: "ne", value: "XASE" },
      { id: "b", key: "exch", op: "contains", value: "nas" },
    ];
    const next = withExchange(filters, ["XNYS"]);
    expect(next.map((filter) => filter.op)).toEqual(["ne", "contains", "eq"]);
  });

  it("normalises the code it writes, because the API compares exactly", () => {
    expect(withExchange([], [" xnas "])).toEqual([
      { id: expect.any(String), key: "exch", op: "eq", value: "XNAS" },
    ]);
  });
});

describe("isExchangeChoice", () => {
  it("owns eq and in, and nothing else", () => {
    expect(isExchangeChoice({ id: "a", key: "exch", op: "eq", value: "XNAS" })).toBe(true);
    expect(isExchangeChoice({ id: "a", key: "exch", op: "in", value: ["XNAS"] })).toBe(true);
    expect(isExchangeChoice({ id: "a", key: "exch", op: "ne", value: "XNAS" })).toBe(false);
    expect(isExchangeChoice({ id: "a", key: "sec", op: "eq", value: "Finance" })).toBe(false);
    // A malformed equality is still this control's row to replace: it owns the
    // key+operator, not a particular value.
    expect(isExchangeChoice({ id: "a", key: "exch", op: "eq", value: "" })).toBe(true);
  });
});

describe("exchangeVenuesFromRows", () => {
  it("counts the page's venues, US majors first, and skips absent cells", () => {
    expect(
      exchangeVenuesFromRows([
        { symbol: "AAPL", exchange: "XNAS" },
        { symbol: "JPM", exchange: "XNYS" },
        { symbol: "MSFT", exchange: "XNAS" },
        { symbol: "X", exchange: null },
        { symbol: "Y" },
        { symbol: "Z", exchange: "  " },
      ]),
    ).toEqual([
      { code: "XNAS", count: 2 },
      { code: "XNYS", count: 1 },
    ]);
  });
});

describe("venueBucketsFromReport", () => {
  it("reads the route's document, in menu order", () => {
    expect(
      venueBucketsFromReport({
        universe_total: 5_576,
        exchanges: [
          { code: "XNYS", label: "NYSE", count: 2_009 },
          { code: "XNAS", label: "NASDAQ", count: 3_308 },
        ],
      }),
    ).toEqual([
      { code: "XNAS", count: 3_308 },
      { code: "XNYS", count: 2_009 },
    ]);
  });

  it("degrades to an empty list rather than throwing: a menu is not a headline", () => {
    // The facet must never take the grid down — a bad document means "derive
    // the menu from the rows on screen", not "fail the panel".
    for (const body of [null, undefined, 42, "x", {}, { exchanges: "no" }, { exchanges: [null, 7] }]) {
      expect(venueBucketsFromReport(body)).toEqual([]);
    }
  });

  it("skips a bucket without a code, floors a fractional count, and merges duplicates", () => {
    expect(
      venueBucketsFromReport({
        exchanges: [
          { code: "", count: 10 },
          { code: "XWAR", count: 3.7 },
          { code: "xwar", count: 9 },
          { code: "XNAS", count: "3308" },
        ],
      }),
    ).toEqual([
      { code: "XNAS", count: 0 },
      { code: "XWAR", count: 9 },
    ]);
  });
});
