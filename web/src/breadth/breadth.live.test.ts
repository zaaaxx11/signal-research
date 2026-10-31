// @vitest-environment node
/**
 * The live half of the breadth proof: the real `GET /market/breadth` answer,
 * parsed by the same `parseBreadth` the strip uses, audited for the promises
 * the UI makes.
 *
 * The fixture in `__fixtures__/breadth.ts` is a snapshot of one answer; this
 * test is the answer itself. It is skipped unless `LIVE_API=1` so the default
 * suite never depends on a running scanner (which is a network call and can be
 * down):
 *
 *   LIVE_API=1 npx vitest run src/breadth/breadth.live.test.ts
 *
 * It is read-only: breadth is a GET, and the route serves a cached scan.
 */

import { describe, expect, it } from "vitest";

import { parseBreadth, sumCheck } from "./report";

const LIVE = process.env["LIVE_API"] === "1";
const ORIGIN = process.env["LIVE_API_ORIGIN"] ?? "http://127.0.0.1:8099";

describe.skipIf(!LIVE)("breadth, live", () => {
  it("answers with a document whose parts account for its whole", async () => {
    const response = await fetch(`${ORIGIN}/market/breadth?region=us&asset_class=stock`);
    expect(response.status).toBe(200);

    const report = parseBreadth(await response.json());

    expect(report.universe_total).toBeGreaterThan(0);
    expect(report.as_of).toMatch(/^\d{4}-\d{2}-\d{2}T/);
    expect(Number.isNaN(Date.parse(report.as_of))).toBe(false);

    // The strip's headline assertion, against the real numbers.
    const check = sumCheck(report);
    expect(check.sum).toBe(check.total);
    expect(check.ok).toBe(true);

    // Denominators are real denominators: a count never exceeds its basis.
    expect(report.new_highs_denominator).not.toBeNull();
    expect(report.new_lows_denominator).not.toBeNull();
    expect(report.new_highs).toBeLessThanOrEqual(report.new_highs_denominator ?? 0);
    expect(report.new_lows).toBeLessThanOrEqual(report.new_lows_denominator ?? 0);
    expect(report.new_highs_denominator ?? 0).toBeLessThanOrEqual(report.universe_total);
  });

  it("buckets every row exactly once, with averages that are really absent when null", async () => {
    const response = await fetch(`${ORIGIN}/market/breadth?region=us&asset_class=stock`);
    const report = parseBreadth(await response.json());

    expect(report.sectors.length).toBeGreaterThan(0);

    let sectorTotal = 0;
    for (const sector of report.sectors) {
      expect(sector.count).toBeGreaterThanOrEqual(0);
      expect(sector.advancing + sector.declining).toBeLessThanOrEqual(sector.count);
      if (sector.avg_change_pct !== null) {
        expect(Number.isFinite(sector.avg_change_pct)).toBe(true);
      }
      sectorTotal += sector.count;
    }

    // `method.sectors`: "one bucket per provider sector label plus a null
    // bucket …, so sector counts sum to universe_total".
    expect(sectorTotal).toBe(report.universe_total);
  });
});
