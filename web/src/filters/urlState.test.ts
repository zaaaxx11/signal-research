import { beforeEach, describe, expect, it } from "vitest";

import { requestBody } from "@/data/screens";

import { filterSchemaFixture } from "./__fixtures__/schema";
import { buildRegistry } from "./registry";
import { resetFilterIds, toWire } from "./state";
import {
  MAX_PAGE,
  droppedNotice,
  encodeScreenState,
  parseScreenState,
  wireFilters,
  type ScreenState,
} from "./urlState";

const VIEWS = ["today", "momentum", "value"];

function state(filters: ScreenState["filters"]): ScreenState {
  return { view: "momentum", sort: "gainers", page: 2, filters };
}

function parse(search: string) {
  return parseScreenState(search, buildRegistry(filterSchemaFixture()), VIEWS);
}

describe("URL state", () => {
  beforeEach(() => {
    resetFilterIds();
  });

  it("round-trips filters + view + sort + page into the same request body", () => {
    const registry = buildRegistry(filterSchemaFixture());
    const original: ScreenState = {
      view: "momentum",
      sort: "gainers",
      page: 2,
      filters: [
        { id: "a", key: "cap", op: "gte", value: 2_000_000_000 },
        { id: "b", key: "sec", op: "in", value: ["Technology", "Healthcare"] },
        { id: "c", key: "sh_price", op: "between", value: [5, 20] },
        { id: "d", key: "ta_perf", op: "eq", value: "1w10u" },
      ],
    };

    const query = encodeScreenState(original);
    expect(query).toContain("view=momentum");
    expect(query).toContain("sort=gainers");
    expect(query).toContain("page=3");
    expect(new URLSearchParams(query).getAll("f")).toContain("cap:gte:2000000000");

    const parsed = parseScreenState(`?${query}`, registry, VIEWS);
    expect(parsed.dropped).toEqual([]);
    expect(parsed.state.view).toBe("momentum");
    expect(parsed.state.sort).toBe("gainers");
    expect(parsed.state.page).toBe(2);
    expect(toWire(parsed.state.filters)).toEqual(toWire(original.filters));

    const before = requestBody({
      view: original.view,
      columns: ["price"],
      sort: original.sort,
      limit: 100,
      offset: original.page * 100,
      filters: toWire(original.filters),
    });
    const after = requestBody({
      view: parsed.state.view,
      columns: ["price"],
      sort: parsed.state.sort,
      limit: 100,
      offset: parsed.state.page * 100,
      filters: wireFilters(parsed.state),
    });
    expect(after).toEqual(before);
  });

  it("(e) ignores a key this build cannot apply, and says which", () => {
    const parsed = parse("?view=today&f=idx:eq:sp500&f=cap:lt:2000000000");
    expect(parsed.dropped).toEqual(["idx"]);
    expect(toWire(parsed.state.filters)).toEqual([{ key: "cap", op: "lt", value: 2_000_000_000 }]);
    expect(droppedNotice(parsed.dropped)).toBe(
      "Ignored 1 filter from this link: idx — this build cannot apply it.",
    );
  });

  it("ignores an unknown spelling and an operator the field does not take", () => {
    const unknown = parse("?f=not_a_key:eq:1&f=cap:gte:1");
    expect(unknown.dropped).toEqual(["not_a_key"]);

    const wrongOp = parse("?f=cap:contains:cheap&f=sec:eq:Technology");
    expect(wrongOp.dropped).toEqual(["cap"]);
    expect(toWire(wrongOp.state.filters)).toEqual([{ key: "sec", op: "eq", value: "Technology" }]);
  });

  it("ignores an inverted range and a malformed value", () => {
    const inverted = parse("?f=cap:between:%5B10%2C1%5D&f=cap:gte:1");
    expect(inverted.dropped).toEqual(["cap"]);
    // The second predicate on the same key is still applied: dropping one
    // spelling must not drop the field.
    expect(toWire(inverted.state.filters)).toEqual([{ key: "cap", op: "gte", value: 1 }]);

    const bogus = parse("?f=cap:gte:abc");
    expect(bogus.dropped).toEqual(["cap"]);
    expect(bogus.state.filters).toEqual([]);
  });

  it("canonicalizes alias spellings to the key the chip can label", () => {
    const parsed = parse("?f=pe:lt:20&f=market_cap:gte:2000000000&f=sector:eq:Technology");
    expect(parsed.dropped).toEqual([]);
    expect(toWire(parsed.state.filters)).toEqual([
      { key: "fa_pe", op: "lt", value: 20 },
      { key: "cap", op: "gte", value: 2_000_000_000 },
      { key: "sec", op: "eq", value: "Technology" },
    ]);
  });

  it("defaults sort/page/view rather than refusing the link", () => {
    const parsed = parse("?view=nope&sort=sideways&page=0");
    expect(parsed.state.view).toBe("");
    // A sort token is a catalogue key, and the parse does not hold the
    // catalogue: an unknown-but-well-formed key travels as written, so the
    // API's own `unknown_sort` refusal is what the reader sees — never a
    // silently different ordering than the link named.
    expect(parsed.state.sort).toBe("sideways");
    expect(parsed.state.page).toBe(0);
    // A token that is not a key at all is a dead link, not a sort.
    expect(parse("?sort=not%20a%20key!").state.sort).toBe("market_cap");
    expect(parse("?sort=-country").state.sort).toBe("-country");

    expect(parse("?page=3").state.page).toBe(2);
    expect(parse("?page=abc").state.page).toBe(0);
  });

  it("keeps a readable spelling for a person debugging a link", () => {
    const query = encodeScreenState(
      state([{ id: "a", key: "sec", op: "in", value: ["Technology", "Healthcare"] }]),
    );
    const spellings = new URLSearchParams(query).getAll("f");
    expect(spellings).toEqual(['sec:in:["Technology","Healthcare"]']);
  });

  it("pluralizes the ignored notice", () => {
    expect(droppedNotice([])).toBeNull();
    expect(droppedNotice(["idx", "theme"])).toMatch(/Ignored 2 filters/);
  });
});

// ---------------------------------------------------------------------------
// F3 — a shared link must not be able to pin the panel to a hostile page
// ---------------------------------------------------------------------------

describe("F3 — page is bounded", () => {
  it("clamps a 15-digit page at parse, so the offset stays bounded", () => {
    const parsed = parse("?page=999999999999999");
    const limit = 100;

    expect(parsed.state.page).toBeLessThanOrEqual(MAX_PAGE - 1);
    expect(parsed.state.page).toBeGreaterThanOrEqual(0);
    // The number the panel will put in `criteria.offset`.
    expect(parsed.state.page * limit).toBeLessThanOrEqual(MAX_PAGE * limit);
    expect(Number.isSafeInteger(parsed.state.page * limit)).toBe(true);
  });

  it("never writes the poison back into the URL", () => {
    const parsed = parse("?page=999999999999999");
    expect(encodeScreenState(parsed.state)).toContain(`page=${MAX_PAGE}`);
  });

  it("leaves ordinary paging exactly as it was", () => {
    expect(parse("?page=1").state.page).toBe(0);
    expect(parse("?page=3").state.page).toBe(2);
    expect(parse("?page=56").state.page).toBe(55);
    expect(parse("?page=57").state.page).toBe(56);
    expect(parse(`?page=${MAX_PAGE}`).state.page).toBe(MAX_PAGE - 1);
  });

  it("refuses a negative, a fraction and a non-number without shifting the page", () => {
    expect(parse("?page=-4").state.page).toBe(0);
    expect(parse("?page=2.5").state.page).toBe(0);
    expect(parse("?page=abc").state.page).toBe(0);
  });
});
