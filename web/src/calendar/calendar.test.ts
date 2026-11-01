import { describe, expect, it } from "vitest";

import {
  CALENDAR_CAP,
  CALENDAR_DAYS,
  CalendarMalformed,
  calendarPath,
  dayLabel,
  overflowCount,
  readCalendar,
  shownSymbols,
  todayUtc,
} from "./calendar";

/**
 * The calendar document's contract, pinned without a DOM:
 *
 * * one request, with the window the page claims in its empty copy;
 * * a day is a YYYY-MM-DD string, rendered in UTC (a calendar day is not a
 *   timestamp, and a local-zone conversion can move it by one);
 * * a bucket that is in the past, empty, or outside the window the server
 *   itself named does not become a section;
 * * `count` is the true total and `symbols` is the sample: the cap and the
 *   "+N more" arithmetic run on both, so one cannot quietly become the other.
 */

function sym(index: number) {
  return { s: `SYM${index}`, n: `Company ${index}` };
}

function symbols(count: number) {
  return Array.from({ length: count }, (_, index) => sym(index));
}

function document(overrides: Record<string, unknown> = {}) {
  return {
    as_of: "2026-11-01T22:10:00Z",
    region: "us",
    from: "2026-11-02",
    to: "2026-11-23",
    buckets: [
      { date: "2026-11-05", count: 2, symbols: symbols(2) },
      { date: "2026-11-02", count: 42, symbols: symbols(42) },
    ],
    ...overrides,
  };
}

describe("calendarPath", () => {
  it("names the region and the window in the one request", () => {
    expect(CALENDAR_DAYS).toBe(21);
    expect(calendarPath()).toBe("/market/calendar?region=us&days=21");
    expect(calendarPath("eu", 7)).toBe("/market/calendar?region=eu&days=7");
  });
});

describe("dayLabel", () => {
  it("reads as a weekday and a day, in UTC", () => {
    expect(dayLabel("2026-11-02")).toBe("Mon 2 Nov");
    expect(dayLabel("2026-11-05")).toBe("Thu 5 Nov");
    expect(dayLabel("2026-01-01")).toBe("Thu 1 Jan");
  });

  it("does not invent a date for a string it cannot read", () => {
    expect(dayLabel("not-a-date")).toBe("not-a-date");
  });
});

describe("readCalendar", () => {
  it("orders days forward and keeps the counts the server sent", () => {
    const report = readCalendar(document(), { today: "2026-11-02" });
    expect(report.buckets.map((bucket) => bucket.date)).toEqual(["2026-11-02", "2026-11-05"]);
    expect(report.buckets[0]?.count).toBe(42);
    expect(report.buckets[1]?.symbols).toHaveLength(2);
  });

  it("drops a day in the past even when the server sent it", () => {
    const report = readCalendar(
      document({
        buckets: [
          { date: "2026-10-30", count: 5, symbols: symbols(5) },
          { date: "2026-11-02", count: 1, symbols: symbols(1) },
        ],
      }),
      { today: "2026-11-02" },
    );
    expect(report.buckets.map((bucket) => bucket.date)).toEqual(["2026-11-02"]);
  });

  it("drops an empty day: a section with no names is not a day", () => {
    const report = readCalendar(
      document({
        buckets: [
          { date: "2026-11-03", count: 0, symbols: [] },
          { date: "2026-11-04", count: 1, symbols: symbols(1) },
        ],
      }),
      { today: "2026-11-02" },
    );
    expect(report.buckets.map((bucket) => bucket.date)).toEqual(["2026-11-04"]);
  });

  it("does not go beyond the window the server named", () => {
    const report = readCalendar(
      document({
        buckets: [
          { date: "2026-11-23", count: 1, symbols: symbols(1) },
          { date: "2026-11-24", count: 1, symbols: symbols(1) },
        ],
      }),
      { today: "2026-11-02" },
    );
    expect(report.buckets.map((bucket) => bucket.date)).toEqual(["2026-11-23"]);
  });

  it("drops an unreadable date or count instead of inventing one", () => {
    const report = readCalendar(
      document({
        buckets: [
          { date: "02/11/2026", count: 3, symbols: symbols(3) },
          { date: "2026-13-40", count: 3, symbols: symbols(3) },
          { date: "2026-11-06", count: "many", symbols: symbols(1) },
          { date: "2026-11-09", count: "", symbols: symbols(1) },
          { date: "2026-11-07", count: 1, symbols: [{ s: "", n: "no ticker" }] },
          { date: "2026-11-08", count: 1, symbols: symbols(1) },
        ],
      }),
      { today: "2026-11-02" },
    );
    expect(report.buckets.map((bucket) => bucket.date)).toEqual(["2026-11-08"]);
  });

  it("keeps one section per day when the server repeats a date", () => {
    const report = readCalendar(
      document({
        buckets: [
          { date: "2026-11-02", count: 2, symbols: symbols(2) },
          { date: "2026-11-02", count: 2, symbols: symbols(2) },
        ],
      }),
      { today: "2026-11-02" },
    );
    expect(report.buckets).toHaveLength(1);
  });

  it("refuses a payload that is not a calendar document", () => {
    expect(() => readCalendar(null, { today: "2026-11-02" })).toThrow(CalendarMalformed);
    expect(() => readCalendar({}, { today: "2026-11-02" })).toThrow(CalendarMalformed);
    expect(() => readCalendar({ buckets: {} }, { today: "2026-11-02" })).toThrow(CalendarMalformed);
  });
});

describe("the 50-name cap", () => {
  it("shows at most fifty symbols and counts the rest from the true total", () => {
    const bucket = { date: "2026-11-06", count: 137, symbols: symbols(CALENDAR_CAP) };
    expect(shownSymbols(bucket)).toHaveLength(50);
    expect(overflowCount(bucket)).toBe(87);
  });

  it("still caps a server that sent more names than the contract allows", () => {
    const bucket = { date: "2026-11-06", count: 60, symbols: symbols(60) };
    expect(shownSymbols(bucket)).toHaveLength(50);
    expect(overflowCount(bucket)).toBe(10);
  });

  it("says nothing more when the day fits", () => {
    expect(overflowCount({ date: "2026-11-06", count: 3, symbols: symbols(3) })).toBe(0);
    expect(overflowCount({ date: "2026-11-06", count: 50, symbols: symbols(50) })).toBe(0);
  });

  it("never shows more rows than the count the heading claims", () => {
    const bucket = { date: "2026-11-06", count: 2, symbols: symbols(5) };
    expect(shownSymbols(bucket)).toHaveLength(2);
    expect(overflowCount(bucket)).toBe(0);
  });
});

describe("todayUtc", () => {
  it("is a calendar date, not a timestamp", () => {
    expect(todayUtc()).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    expect(todayUtc(new Date("2026-11-02T23:30:00Z"))).toBe("2026-11-02");
    expect(todayUtc(new Date("2026-01-01T00:00:00Z"))).toBe("2026-01-01");
  });
});
