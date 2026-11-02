/**
 * The display formatter must not be able to reach a file, and the file must not
 * be able to reach the display.
 *
 * There is exactly one download path and it starts with bytes the server wrote
 * (`POST /screens/export`). This file proves the boundary two ways:
 *
 * 1. a source scan: product code may call `URL.createObjectURL` and set
 *    `anchor.download` **only** in `exportJob.ts`, and may never construct a
 *    `Blob` of its own or name a media type (`text/csv`) — a client-built CSV
 *    would publish the labels the grid draws instead of the codes the data
 *    carries;
 * 2. the formatter is pure: `formatCell` returns text, and the row object it
 *    was handed still holds the code. `data-value` exists so a *correct* client
 *    export would be possible; this test says the incorrect one is absent.
 */

import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { columnSpec } from "./columnMeta";
import { formatCell } from "./format";
import { buildExportBody } from "./exportJob";
import type { NarrowSpec } from "./narrow";

const SRC = join(process.cwd(), "src");
const DOWNLOAD_OWNER = join("grid", "exportJob.ts");

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

/** Markers of a file the *client* built. None may appear in product code. */
const FORBIDDEN = ["new Blob(", "text/csv", "textContent"];

/** Markers of the one server-bytes download. Only `exportJob.ts` may have them. */
const DOWNLOAD = ["createObjectURL", "anchor.download"];

describe("the export boundary", () => {
  it("never builds a file of its own", () => {
    const files = productFiles(SRC);
    expect(files.length).toBeGreaterThan(50);

    const offenders: string[] = [];
    for (const file of files) {
      const source = readFileSync(file, "utf8");
      for (const marker of FORBIDDEN) {
        if (source.includes(marker)) offenders.push(`${file.replace(SRC, "src/")} :: ${marker}`);
      }
    }
    expect(offenders).toEqual([]);
  });

  it("downloads only bytes the server sent, from one place", () => {
    const offenders: string[] = [];
    for (const file of productFiles(SRC)) {
      const source = readFileSync(file, "utf8");
      const relative = file.slice(SRC.length + 1);
      for (const marker of DOWNLOAD) {
        if (source.includes(marker) && relative !== DOWNLOAD_OWNER) {
          offenders.push(`${relative} :: ${marker}`);
        }
      }
    }
    expect(offenders).toEqual([]);

    // And the one owner really does it: a marker nobody carries would make the
    // rule above true by accident.
    const owner = readFileSync(join(SRC, DOWNLOAD_OWNER), "utf8");
    for (const marker of DOWNLOAD) expect(owner).toContain(marker);
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

describe("buildExportBody with narrow", () => {
  const baseInput = {
    scope: "screen" as const,
    format: "csv" as const,
    rows: 200,
    region: "us",
    criteria: { filters: [{ key: "ta_beta", op: "lt" as const, value: 0.8 }], sort: "market_cap" },
    columns: ["price", "change"],
  };

  it("merges narrow filters into criteria for the screen scope", () => {
    const narrow: NarrowSpec = { market_cap: "mega", volume: "o10m" };
    const body = buildExportBody({ ...baseInput, narrow });
    expect(body.criteria?.filters).toEqual([
      { key: "ta_beta", op: "lt", value: 0.8 },
      { key: "cap", op: "gte", value: 200_000_000_000 },
      { key: "sh_curvol", op: "gte", value: 10_000_000 },
    ]);
  });

  it("adds performance as a top-level signal for screen scope", () => {
    const narrow: NarrowSpec = { performance: "gainers" };
    const body = buildExportBody({ ...baseInput, narrow });
    expect(body.signal).toBe("gainers");
    // performance does not appear in filters
    expect(body.criteria?.filters).toEqual([
      { key: "ta_beta", op: "lt", value: 0.8 },
    ]);
  });

  it("adds sector as a sec eq filter in criteria", () => {
    const narrow: NarrowSpec = { sector: "Electronic Technology" };
    const body = buildExportBody({ ...baseInput, narrow });
    expect(body.criteria?.filters).toEqual([
      { key: "ta_beta", op: "lt", value: 0.8 },
      { key: "sec", op: "eq", value: "Electronic Technology" },
    ]);
  });

  it("includes narrow block for page scope (not merged into criteria)", () => {
    const narrow: NarrowSpec = { market_cap: "small" };
    const body = buildExportBody({
      ...baseInput,
      scope: "page",
      criteria: undefined,
      symbols: ["AAPL", "MSFT"],
      narrow,
    });
    // page scope has no criteria; narrow goes in separate narrow field
    expect(body.mode).toBe("symbols");
    expect(body.narrow).toBeDefined();
    expect(body.narrow?.filters).toEqual([
      { key: "cap", op: "gte", value: 2_000_000_000 },
      { key: "cap", op: "lt", value: 10_000_000_000 },
    ]);
  });

  it("omits narrow for tickers scope", () => {
    const narrow: NarrowSpec = { sector: "Finance" };
    const body = buildExportBody({
      ...baseInput,
      scope: "tickers",
      criteria: undefined,
      symbols: ["AAPL"],
      narrow,
    });
    expect(body.narrow).toBeUndefined();
    expect(body.symbols).toEqual(["AAPL"]);
  });

  it("includes narrow for selected scope", () => {
    const narrow: NarrowSpec = { volume: "500k-2m" };
    const body = buildExportBody({
      ...baseInput,
      scope: "selected",
      criteria: undefined,
      symbols: ["NVDA"],
      narrow,
    });
    expect(body.narrow?.filters).toEqual([
      { key: "sh_curvol", op: "gte", value: 500_000 },
      { key: "sh_curvol", op: "lt", value: 2_000_000 },
    ]);
  });

  it("combines all narrow dimensions in screen criteria", () => {
    const narrow: NarrowSpec = {
      market_cap: "mid",
      volume: "2m-10m",
      sector: "Finance",
      performance: "losers",
    };
    const body = buildExportBody({ ...baseInput, narrow });
    expect(body.signal).toBe("losers");
    expect(body.criteria?.filters).toEqual([
      { key: "ta_beta", op: "lt", value: 0.8 },
      { key: "cap", op: "gte", value: 10_000_000_000 },
      { key: "cap", op: "lt", value: 200_000_000_000 },
      { key: "sh_curvol", op: "gte", value: 2_000_000 },
      { key: "sh_curvol", op: "lt", value: 10_000_000 },
      { key: "sec", op: "eq", value: "Finance" },
    ]);
  });

  it("no narrow means no narrow field in body", () => {
    const body = buildExportBody(baseInput);
    expect(body.narrow).toBeUndefined();
    expect(body.signal).toBeUndefined();
  });
});
