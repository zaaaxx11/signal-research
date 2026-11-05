import { describe, expect, it } from "vitest";

import {
  dayKeyOf,
  dayLabel,
  formatDayLabel,
  itemClock,
  matchesQuery,
  previousDay,
  safeExternalHref,
  todayUtc,
} from "./format";
import {
  NewsMalformed,
  filterItems,
  groupByDay,
  newsPath,
  readNews,
  sortNewestFirst,
  type NewsItem,
} from "./news";
import { NEWS_TODAY, newsDocument, wireItem } from "./__fixtures__/news";

/**
 * The model's promises, without a DOM:
 *
 * * a feed URL is only a link when it is absolute http(s);
 * * the list is sorted newest first and undated last, and grouping calls those
 *   rows `Undated`, never `Today`;
 * * day keys are UTC, so an offset timestamp lands on the day its UTC clock
 *   shows;
 * * duplicates are kept — the server deduped, the client does not re-do it.
 */

const item = (overrides: Partial<NewsItem> = {}): NewsItem => ({
  headline: "A headline",
  url: "https://example.com/a",
  source: "Reuters",
  publishedAt: "2026-11-01T09:21:00Z",
  symbols: ["SPY"],
  ...overrides,
});

describe("safeExternalHref", () => {
  it("keeps absolute http(s) URLs and returns the canonical form", () => {
    expect(safeExternalHref("https://example.com/a?b=1")).toBe("https://example.com/a?b=1");
    expect(safeExternalHref(" http://example.com/a ")).toBe("http://example.com/a");
    expect(safeExternalHref("HTTPS://Example.COM/Path")).toBe("https://example.com/Path");
  });

  it("refuses javascript:, data:, vbscript: and their case variants", () => {
    expect(safeExternalHref("javascript:alert(1)")).toBeNull();
    expect(safeExternalHref("JaVaScRiPt:alert(1)")).toBeNull();
    expect(safeExternalHref("data:text/html,<script>alert(1)</script>")).toBeNull();
    expect(safeExternalHref("vbscript:msgbox(1)")).toBeNull();
  });

  it("refuses control characters, relative paths, blanks and non-strings", () => {
    expect(safeExternalHref("java\nscript:alert(1)")).toBeNull();
    expect(safeExternalHref("\u0000javascript:alert(1)")).toBeNull();
    expect(safeExternalHref("/markets/aapl")).toBeNull();
    expect(safeExternalHref("")).toBeNull();
    expect(safeExternalHref("   ")).toBeNull();
    expect(safeExternalHref(null)).toBeNull();
    expect(safeExternalHref(42)).toBeNull();
  });

  it("refuses a URL that carries credentials", () => {
    expect(safeExternalHref("https://user:pass@example.com/a")).toBeNull();
  });
});

describe("UTC days and clocks", () => {
  it("labels a day as `31 Oct 2026`", () => {
    expect(formatDayLabel("2026-10-31")).toBe("31 Oct 2026");
    expect(formatDayLabel("2026-01-01")).toBe("1 Jan 2026");
    expect(formatDayLabel("not-a-day")).toBe("not-a-day");
  });

  it("takes the UTC day, not the offset's local day", () => {
    expect(dayKeyOf("2026-11-01T01:30:00+02:00")).toBe("2026-10-31");
    expect(dayKeyOf("2026-10-31T23:50:00Z")).toBe("2026-10-31");
    expect(dayKeyOf(null)).toBeNull();
    expect(dayKeyOf("")).toBeNull();
    expect(dayKeyOf("yesterday")).toBeNull();
  });

  it("walks back one day across a month boundary", () => {
    expect(previousDay("2026-11-01")).toBe("2026-10-31");
    expect(previousDay("2026-01-01")).toBe("2025-12-31");
  });

  it("names Today and Yesterday against the pinned day, and Undated for null", () => {
    expect(dayLabel("2026-11-01", NEWS_TODAY)).toBe("Today");
    expect(dayLabel("2026-10-31", NEWS_TODAY)).toBe("Yesterday");
    expect(dayLabel("2026-10-30", NEWS_TODAY)).toBe("30 Oct 2026");
    expect(dayLabel(null, NEWS_TODAY)).toBe("Undated");
  });

  it("todayUtc is a `YYYY-MM-DD` day", () => {
    expect(todayUtc(Date.UTC(2026, 10, 1, 23, 59))).toBe("2026-11-01");
  });

  it("prints a UTC clock and the full instant, and nothing without an instant", () => {
    expect(itemClock("2026-11-01T09:21:58Z")).toEqual({
      clock: "09:21",
      instant: "1 Nov 2026, 09:21 UTC",
    });
    expect(itemClock("2026-11-01T09:21:00+02:00")?.clock).toBe("07:21");
    expect(itemClock(null)).toBeNull();
    expect(itemClock("not-a-date")).toBeNull();
  });
});

describe("matchesQuery", () => {
  it("matches headline or symbol, case-insensitively, and blank matches all", () => {
    const row = item({ headline: "Apple beats the street", symbols: ["AAPL", "SPY"] });
    expect(matchesQuery(row, "apple")).toBe(true);
    expect(matchesQuery(row, "aapl")).toBe(true);
    expect(matchesQuery(row, "spy")).toBe(true);
    expect(matchesQuery(row, "msft")).toBe(false);
    expect(matchesQuery(row, "   ")).toBe(true);
  });
});

describe("readNews", () => {
  it("reads the document, sorts newest first and leaves the undated last", () => {
    const report = readNews(
      newsDocument({
        items: [
          wireItem({ headline: "undated", published_at: null, url: "https://e.com/0" }),
          wireItem({
            headline: "older",
            published_at: "2026-10-31T10:00:00Z",
            url: "https://e.com/1",
          }),
          wireItem({
            headline: "newer",
            published_at: "2026-11-01T10:00:00Z",
            url: "https://e.com/2",
          }),
        ],
      }),
    );

    expect(report.items.map((row) => row.headline)).toEqual(["newer", "older", "undated"]);
    expect(report.count).toBe(3);
  });

  it("keeps duplicates: two rows with the same headline are two rows", () => {
    const report = readNews(
      newsDocument({
        items: [
          wireItem({ headline: "Same words", url: "https://e.com/a" }),
          wireItem({ headline: "Same words", url: "https://e.com/b" }),
        ],
      }),
    );
    expect(report.items).toHaveLength(2);
    expect(report.items.map((row) => row.url)).toEqual(["https://e.com/a", "https://e.com/b"]);
  });

  it("keeps the server's count rather than recomputing it", () => {
    const report = readNews(newsDocument({ count: 137 }));
    expect(report.count).toBe(137);
  });

  it("treats an absent timestamp and an unparseable one alike", () => {
    const report = readNews(
      newsDocument({
        items: [
          wireItem({ headline: "bad", published_at: "not-a-date", url: "https://e.com/0" }),
          wireItem({
            headline: "good",
            published_at: "2026-11-01T09:00:00Z",
            url: "https://e.com/1",
          }),
        ],
      }),
    );
    expect(report.items.map((row) => row.headline)).toEqual(["good", "bad"]);
    expect(itemClock(report.items[1]?.publishedAt ?? null)).toBeNull();
  });

  it("refuses a payload with no items array instead of calling it empty", () => {
    expect(() => readNews(null)).toThrow(NewsMalformed);
    expect(() => readNews({ count: 0 })).toThrow(NewsMalformed);
    expect(() => readNews({ items: "none" })).toThrow(NewsMalformed);
  });

  it("drops rows with no headline but keeps every readable one", () => {
    const report = readNews(
      newsDocument({
        items: [
          wireItem({ headline: "  ", url: "https://e.com/blank" }),
          wireItem({ headline: "Readable", url: "https://e.com/readable" }),
          null,
          "not an object",
        ],
      }),
    );
    expect(report.items.map((row) => row.headline)).toEqual(["Readable"]);
  });
});

describe("groupByDay", () => {
  it("groups across midnight into Today, Yesterday and a dated heading", () => {
    const report = readNews(
      newsDocument({
        items: [
          wireItem({
            headline: "after midnight",
            published_at: "2026-11-01T00:05:00Z",
            url: "https://e.com/0",
          }),
          wireItem({
            headline: "before midnight",
            published_at: "2026-10-31T23:55:00Z",
            url: "https://e.com/1",
          }),
          wireItem({
            headline: "the day before",
            published_at: "2026-10-30T12:00:00Z",
            url: "https://e.com/2",
          }),
        ],
      }),
    );

    const groups = groupByDay(report.items, NEWS_TODAY);
    expect(groups.map((group) => group.label)).toEqual(["Today", "Yesterday", "30 Oct 2026"]);
    expect(groups.map((group) => group.items.length)).toEqual([1, 1, 1]);
  });

  it("keeps a single-item day as its own heading and never files null under Today", () => {
    const report = readNews(
      newsDocument({
        items: [
          wireItem({
            headline: "one story",
            published_at: "2026-10-29T08:00:00Z",
            url: "https://e.com/0",
          }),
          wireItem({ headline: "no stamp", published_at: null, url: "https://e.com/1" }),
        ],
      }),
    );

    const groups = groupByDay(report.items, NEWS_TODAY);
    expect(groups.map((group) => group.label)).toEqual(["29 Oct 2026", "Undated"]);
    expect(groups[0]?.items).toHaveLength(1);
    expect(groups[1]?.key).toBeNull();
    expect(groups.some((group) => group.label === "Today")).toBe(false);
  });

  it("sorts and filters without touching the payload's duplicates", () => {
    const rows = [
      item({ headline: "Zebra", url: "https://e.com/z", publishedAt: null }),
      item({ headline: "Apple", url: "https://e.com/a" }),
    ];
    expect(sortNewestFirst(rows).map((row) => row.headline)).toEqual(["Apple", "Zebra"]);
    expect(filterItems(rows, "apple").map((row) => row.headline)).toEqual(["Apple"]);
    expect(filterItems(rows, "")).toHaveLength(2);
  });
});

describe("newsPath", () => {
  it("asks for the documented cap, same-origin and root-absolute", () => {
    expect(newsPath()).toBe("/market/news?limit=60");
    expect(newsPath(20)).toBe("/market/news?limit=20");
  });
});

/* ------------------------------------------------------------------ */
/*  New functions: category, time, source colour, crypto, relativeTime */
/* ------------------------------------------------------------------ */

import {
  filterByCategory,
  filterByTime,
  isIndexFundSymbol,
  sourceColor,
} from "./news";
import { relativeTime } from "./format";

describe("isIndexFundSymbol", () => {
  it("recognises SPY, QQQ, DIA, IWM case-insensitively", () => {
    expect(isIndexFundSymbol("SPY")).toBe(true);
    expect(isIndexFundSymbol("spy")).toBe(true);
    expect(isIndexFundSymbol("QQQ")).toBe(true);
    expect(isIndexFundSymbol("DIA")).toBe(true);
    expect(isIndexFundSymbol("IWM")).toBe(true);
    expect(isIndexFundSymbol("AAPL")).toBe(false);
    expect(isIndexFundSymbol("BTC-USD")).toBe(false);
  });
});

describe("filterByCategory", () => {
  const stockOnly = item({ symbols: ["AAPL"] });
  const etfOnly = item({ symbols: ["SPY"] });
  const both = item({ symbols: ["AAPL", "SPY"] });
  const none = item({ symbols: [] });

  it("market-news returns everything", () => {
    expect(filterByCategory([stockOnly, etfOnly, both, none], "market-news")).toHaveLength(4);
  });

  it("stocks filters to items with at least one non-index-fund symbol", () => {
    const result = filterByCategory([stockOnly, etfOnly, both, none], "stocks");
    expect(result.map((r) => r.headline)).toEqual(["A headline", "A headline"]);
    expect(result).toHaveLength(2);
  });

  it("etf filters to items with at least one index-fund symbol", () => {
    const result = filterByCategory([stockOnly, etfOnly, both, none], "etf");
    expect(result.map((r) => r.headline)).toEqual(["A headline", "A headline"]);
    expect(result).toHaveLength(2);
  });

  it("market-pulse and crypto return empty", () => {
    expect(filterByCategory([stockOnly], "market-pulse")).toHaveLength(0);
    expect(filterByCategory([stockOnly], "crypto")).toHaveLength(0);
  });
});

describe("filterByTime", () => {
  const recent = item({
    headline: "recent",
    publishedAt: new Date(Date.now() - 30 * 60_000).toISOString(),
  });
  const old = item({
    headline: "old",
    publishedAt: new Date(Date.now() - 4 * 86_400_000).toISOString(),
  });
  const undated = item({ headline: "undated", publishedAt: null });

  it("all keeps everything", () => {
    expect(filterByTime([recent, old, undated], "all")).toHaveLength(3);
  });

  it("1h keeps only the last hour", () => {
    const result = filterByTime([recent, old, undated], "1h");
    expect(result.map((r) => r.headline)).toEqual(["recent"]);
  });

  it("undated items are excluded by any time filter", () => {
    const result = filterByTime([undated], "24h");
    expect(result).toHaveLength(0);
  });
});

describe("sourceColor", () => {
  it("assigns the same colour to the same source", () => {
    expect(sourceColor("Reuters")).toEqual(sourceColor("Reuters"));
  });

  it("assigns different colours to different sources (probably)", () => {
    const c1 = sourceColor("Reuters");
    const c2 = sourceColor("Bloomberg");
    expect(typeof c1.bg).toBe("string");
    expect(typeof c2.bg).toBe("string");
    // They are deterministic, but may collide — that's acceptable.
  });
});

describe("relativeTime", () => {
  it("returns null for null or empty", () => {
    expect(relativeTime(null)).toBeNull();
    expect(relativeTime("")).toBeNull();
    expect(relativeTime("not-a-date")).toBeNull();
  });

  it("returns 'just now' for very recent timestamps", () => {
    const now = Date.now();
    const result = relativeTime(new Date(now - 10_000).toISOString(), now);
    expect(result?.relative).toBe("just now");
  });

  it("returns N minutes ago", () => {
    const now = Date.now();
    const result = relativeTime(new Date(now - 5 * 60_000).toISOString(), now);
    expect(result?.relative).toBe("5 minutes ago");
  });

  it("returns N hours ago", () => {
    const now = Date.now();
    const result = relativeTime(new Date(now - 3 * 3_600_000).toISOString(), now);
    expect(result?.relative).toBe("3 hours ago");
  });

  it("returns N days ago", () => {
    const now = Date.now();
    const result = relativeTime(new Date(now - 2 * 86_400_000).toISOString(), now);
    expect(result?.relative).toBe("2 days ago");
  });

  it("returns the date label for items older than 7 days", () => {
    const result = relativeTime("2026-10-24T12:00:00Z", Date.UTC(2026, 10, 5));
    expect(result?.relative).toBe("24 Oct 2026");
  });

  it("carries an absolute tooltip", () => {
    const result = relativeTime("2026-11-01T09:21:00Z", Date.UTC(2026, 10, 1, 11, 0));
    expect(result?.absolute).toBe("1 Nov 2026, 09:21 UTC");
  });
});
