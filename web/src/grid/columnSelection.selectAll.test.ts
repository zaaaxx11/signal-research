import { describe, expect, it } from "vitest";

import {
  COLUMN_CAP,
  IDENTITY_COLUMNS,
  identityColumns,
  selectAllColumns,
  toggleColumn,
} from "./columnSelection";

/**
 * "Select all" is a panel of at most 40 columns, chosen from a catalogue of 116
 * — so it is a *cut*, not a switch. These are the rules that keep the cut
 * honest: the catalogue's own order decides which 40, the identity columns are
 * never the ones that fall off the end, and one tick at a time still refuses a
 * 41st column.
 */

/** A wide catalogue, with the client row number last — the picker's own order. */
function wideCatalogue(size = 45): string[] {
  return [
    ...Array.from({ length: size - 2 }, (_, index) => `k${String(index).padStart(2, "0")}`),
    "company",
    "no",
  ];
}

describe("selectAllColumns", () => {
  it("selects the whole catalogue when it fits under the panel cap", () => {
    expect(selectAllColumns(["no", "company", "price", "pe"])).toEqual([
      "no",
      "company",
      "price",
      "pe",
    ]);
  });

  it("cuts a wide catalogue to the cap, in catalogue order", () => {
    const keys = Array.from({ length: 45 }, (_, index) => `k${index}`);
    const picked = selectAllColumns(keys);
    expect(picked).toHaveLength(COLUMN_CAP);
    expect(picked).toEqual(keys.slice(0, COLUMN_CAP));
  });

  it("keeps the identity columns even when the catalogue lists them last", () => {
    const keys = wideCatalogue();
    expect(keys.at(-1)).toBe("no");

    const picked = selectAllColumns(keys);
    expect(picked).toHaveLength(COLUMN_CAP);
    // Row number, then company: a panel without them is not a table.
    expect(picked.slice(0, 2)).toEqual(["no", "company"]);

    const rest = keys.filter((key) => !IDENTITY_COLUMNS.includes(key));
    expect(picked.slice(2)).toEqual(rest.slice(0, COLUMN_CAP - 2));
  });

  it("never repeats a key and honours an explicit cap", () => {
    expect(selectAllColumns(["no", "price", "no", "price"], 3)).toEqual(["no", "price"]);
    expect(selectAllColumns(["no", "price"], 1)).toEqual(["no"]);
  });
});

describe("identityColumns", () => {
  it("names the identity columns in table order, from what the picker offers", () => {
    expect(identityColumns(["pe", "company", "no", "price"])).toEqual(["no", "company"]);
    // The ticker lane is pinned and never requested, but the floor is spelled
    // with it: a build that offers it as a key must keep it.
    expect(identityColumns(["ticker", "company", "no"])).toEqual(["no", "ticker", "company"]);
    expect(identityColumns(["pe", "market_cap"])).toEqual([]);
  });
});

describe("toggleColumn at the cap", () => {
  it("still refuses a 41st column, one tick at a time", () => {
    const full = Array.from({ length: COLUMN_CAP }, (_, index) => `k${index}`);
    const result = toggleColumn(full, "extra");
    expect(result.keys).toHaveLength(COLUMN_CAP);
    expect(result.notice).toContain(`at most ${COLUMN_CAP}`);
  });
});
