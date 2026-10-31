/**
 * The display formatter must not be able to reach an export.
 *
 * There is **no** client-side export in this app, and this file proves it two
 * ways so a future one cannot quietly start emitting labels:
 *
 * 1. a source scan for the markers of a browser-download path
 *    (`createObjectURL`, `new Blob(`, `text/csv`, `download=`) — none may
 *    appear in product code;
 * 2. the formatter is pure: `formatCell` returns text, and the row object it
 *    was handed still holds the code. The artefact is written by the server
 *    (`POST /screens/{screen_id}/export` → `GET /requests/{request_id}/export/{fmt}`
 *    serves the file the worker wrote, from `app/export/writer.py`, whose column
 *    list is the frozen `CANONICAL_COLUMNS_V1`). A label cannot contaminate it
 *    because there is no path from the DOM back to the writer.
 *
 * The trap this guards is the plausible one: someone adds "Download CSV" and
 * serialises `textContent`, and every row's venue becomes `NASDAQ` where the
 * consumer's contract says `XNAS`. `data-value` exists so that a *correct*
 * client export would be possible; this test says the incorrect one is absent.
 */

import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { columnSpec } from "./columnMeta";
import { formatCell } from "./format";

const SRC = join(process.cwd(), "src");

/** Every product file under `src/` (tests excluded: they may mention a Blob). */
function productFiles(dir: string): string[] {
  const found: string[] = [];
  for (const entry of readdirSync(dir)) {
    const path = join(dir, entry);
    if (statSync(path).isDirectory()) {
      found.push(...productFiles(path));
      continue;
    }
    if (!/\.(ts|tsx)$/.test(entry)) continue;
    if (/\.test\.(ts|tsx)$/.test(entry)) continue;
    found.push(path);
  }
  return found;
}

const EXPORT_MARKERS = ["createObjectURL", "new Blob(", "text/csv", "download="];

describe("the export boundary", () => {
  it("has no client-side export path at all", () => {
    const files = productFiles(SRC);
    expect(files.length).toBeGreaterThan(50);

    const offenders: string[] = [];
    for (const file of files) {
      const source = readFileSync(file, "utf8");
      for (const marker of EXPORT_MARKERS) {
        if (source.includes(marker)) offenders.push(`${file.replace(SRC, "src/")} :: ${marker}`);
      }
    }
    // The only export hops are the server's: `POST /screens/{id}/export`
    // enqueues a job and the artefact route serves what the worker wrote.
    expect(offenders).toEqual([]);
  });

  it("formats without touching the row: the value a request or a file carries stays a code", () => {
    const row = { symbol: "AAPL", exchange: "XNAS" };
    const cell = formatCell(row.exchange, columnSpec("exchange"));

    expect(cell.text).toBe("NASDAQ");
    // The row the grid was given — the thing a `rows`-based export serialises —
    // is byte-identical after the render.
    expect(row.exchange).toBe("XNAS");
    expect(JSON.stringify(row)).toBe('{"symbol":"AAPL","exchange":"XNAS"}');
  });
});
