// @vitest-environment node
/**
 * The live half of the news proof: the real `GET /market/news` answer, read by
 * the same `readNews` the page uses, audited for the promises the UI makes.
 *
 * Skipped unless `LIVE_API=1` so the default suite never depends on a running
 * deployment (the providers behind the feed can be slow or down):
 *
 *   LIVE_API=1 npx vitest run src/news/news.live.test.ts
 *
 * It is read-only: news is a GET of a cached feed.
 */

import { describe, expect, it } from "vitest";

import { safeExternalHref } from "./format";
import { readNews } from "./news";

const LIVE = process.env["LIVE_API"] === "1";
const ORIGIN = process.env["LIVE_API_ORIGIN"] ?? "http://127.0.0.1:8099";

describe.skipIf(!LIVE)("news, live", () => {
  it("answers with one document whose count is its rows, newest first", async () => {
    const response = await fetch(`${ORIGIN}/market/news?limit=60`);
    expect(response.status).toBe(200);

    const report = readNews(await response.json());
    expect(report.items.length).toBe(60);
    expect(report.count).toBe(report.items.length);
    expect(Number.isNaN(Date.parse(report.asOf))).toBe(false);

    let previous = Number.POSITIVE_INFINITY;
    for (const item of report.items) {
      expect(item.headline.length).toBeGreaterThan(0);
      expect(item.symbols.length).toBeGreaterThan(0);
      // Every URL the list would link is one the guard admits.
      expect(safeExternalHref(item.url)).not.toBeNull();
      if (item.publishedAt === null) continue;
      const instant = Date.parse(item.publishedAt);
      expect(Number.isNaN(instant)).toBe(false);
      expect(instant).toBeLessThanOrEqual(previous);
      previous = instant;
    }
  });

  it("dedupes on the server: no URL appears twice", async () => {
    const response = await fetch(`${ORIGIN}/market/news?limit=60`);
    const report = readNews(await response.json());
    const urls = report.items.map((item) => item.url);
    expect(new Set(urls).size).toBe(urls.length);
  });
});
