// @vitest-environment node
/**
 * The live half of the signals proof: the real `GET /market/signals` document,
 * read by the same `readSignals` the Home page uses.
 *
 * Skipped unless `LIVE_API=1`, so the default suite never depends on a running
 * scanner:
 *
 *   LIVE_API=1 LIVE_API_ORIGIN=http://127.0.0.1:8099 npx vitest run src/home/signals.live.test.ts
 *
 * It is read-only (one GET) and it asserts the two things the client assumes:
 * the group ids are the ones the cards are keyed by, and a row has a symbol.
 */

import { describe, expect, it } from "vitest";

import {
  AVAILABLE_GROUPS,
  SIGNAL_GROUPS,
  SIGNAL_LIMIT,
  UNAVAILABLE_GROUPS,
  readSignals,
  signalsPath,
} from "./signals";

const LIVE = process.env["LIVE_API"] === "1";
const ORIGIN = process.env["LIVE_API_ORIGIN"] ?? "http://127.0.0.1:8099";

describe.skipIf(!LIVE)("market signals, live", () => {
  it("answers thirteen groups, eleven with rows, in one document", async () => {
    const response = await fetch(`${ORIGIN}${signalsPath()}`);
    expect(response.status).toBe(200);

    const report = readSignals(await response.json());
    expect(report.asOf).toMatch(/^\d{4}-\d{2}-\d{2}T/);

    for (const group of AVAILABLE_GROUPS) {
      const rows = report.groups[group.key];
      expect(rows, `${group.key} is missing from the document`).toBeDefined();
      expect(rows?.length ?? 0).toBeLessThanOrEqual(SIGNAL_LIMIT);
    }
    // The document is a page, not a token: at least one table is full.
    const full = AVAILABLE_GROUPS.filter(
      (group) => (report.groups[group.key]?.length ?? 0) === SIGNAL_LIMIT,
    );
    expect(full.length).toBeGreaterThan(0);

    // The two refusals are present and empty — never invented.
    for (const group of UNAVAILABLE_GROUPS) {
      expect(report.groups[group.key]).toEqual([]);
    }

    for (const rows of Object.values(report.groups)) {
      for (const row of rows) {
        expect(row.symbol.length).toBeGreaterThan(0);
        expect(typeof row.price === "number" || row.price === null).toBe(true);
      }
    }

    expect(SIGNAL_GROUPS).toHaveLength(13);
  }, 30_000);
});
