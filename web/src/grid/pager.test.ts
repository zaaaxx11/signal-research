import { describe, expect, it } from "vitest";

import { clampPage, countReadout, pageItems, pagesOf } from "./pager";

/**
 * The pager's arithmetic, without a DOM.
 *
 * The window is the whole rule — seven items at most, a gap wherever numbers
 * were skipped, never a gap of one — and a page the reader types is clamped to
 * what exists instead of becoming a dead request.
 */

describe("pageItems", () => {
  it("draws every page when they fit", () => {
    expect(pageItems(1, 1)).toEqual([1]);
    expect(pageItems(3, 7)).toEqual([1, 2, 3, 4, 5, 6, 7]);
  });

  it("windows a long screen around the current page", () => {
    // 1 … 4 5 6 … 281: first, last, the current page and one neighbour each
    // side, with an elision where the numbers were skipped.
    expect(pageItems(5, 281)).toEqual([1, "gap", 4, 5, 6, "gap", 281]);
  });

  it("widens the near edge rather than drawing a lopsided bar", () => {
    expect(pageItems(1, 281)).toEqual([1, 2, 3, 4, "gap", 281]);
    expect(pageItems(2, 281)).toEqual([1, 2, 3, 4, "gap", 281]);
    expect(pageItems(281, 281)).toEqual([1, "gap", 278, 279, 280, 281]);
  });

  it("prints one skipped page instead of eliding it", () => {
    // 1 2 3 4 5 … 8: page 2 is one page away from page 3, so it is printed
    // rather than elided; a run of five skipped pages is one gap.
    expect(pageItems(4, 8)).toEqual([1, 2, 3, 4, 5, "gap", 8]);
    expect(pageItems(7, 12)).toEqual([1, "gap", 6, 7, 8, "gap", 12]);
  });

  it("clamps a current page outside the range and survives an empty screen", () => {
    expect(pageItems(0, 5)).toEqual([1, 2, 3, 4, 5]);
    expect(pageItems(99, 5)).toEqual([1, 2, 3, 4, 5]);
    expect(pageItems(1, 0)).toEqual([]);
  });
});

describe("clampPage", () => {
  it("lands a typed number on a page that exists", () => {
    expect(clampPage(5, 281)).toBe(5);
    expect(clampPage(999, 281)).toBe(281);
    expect(clampPage(0, 281)).toBe(1);
    expect(clampPage(-4, 281)).toBe(1);
    expect(clampPage(2.9, 281)).toBe(2);
    expect(clampPage(Number.NaN, 281)).toBe(1);
    expect(clampPage(3, 0)).toBe(1);
  });
});

describe("pagesOf", () => {
  it("never returns zero pages, and rounds the last partial page up", () => {
    expect(pagesOf(0, 20)).toBe(1);
    expect(pagesOf(20, 20)).toBe(1);
    expect(pagesOf(21, 20)).toBe(2);
    expect(pagesOf(5_620, 20)).toBe(281);
    expect(pagesOf(100, 0)).toBe(100);
  });
});

describe("countReadout", () => {
  it("is the page position over the filtered total, in en-US grouping", () => {
    expect(countReadout(0, 5_600)).toBe("1/5,600 Total");
    expect(countReadout(3, 5_600)).toBe("4/5,600 Total");
    expect(countReadout(0, 137)).toBe("1/137 Total");
    expect(countReadout(0, 0)).toBe("—");
  });
});
