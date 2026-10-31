import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ALL_COLUMNS_LIMIT, chunkColumns, splitCatalogue, uniqueKeys } from "./catalogue";
import { absenceReason, mergeChunks, mergeNotices, absentCells } from "./merge";
import { useAllColumns } from "./useAllColumns";

import type { ViewDefinition } from "@/data/screens";

/**
 * The chunked read, proved against a mocked transport.
 *
 * The API refuses a selection wider than 40 columns, so "all 116" is three
 * requests. These tests hold the three promises that make the merge trustworthy:
 *
 * * exactly three requests, none over 40 columns, 116 keys asked for once each;
 * * rows joined by symbol — chunk 2 answering in reverse order changes nothing;
 * * a chunk that fails, or a symbol a chunk omits, is a visible gap with the
 *   chunk named — never a half-filled row that looks complete.
 */

const KEYS = Array.from({ length: 116 }, (_, index) => `k${String(index).padStart(3, "0")}`);
const SYMBOLS = Array.from({ length: ALL_COLUMNS_LIMIT }, (_, index) => `S${index}`);

function answer(columns: readonly string[], symbols: readonly string[] = SYMBOLS) {
  return new Response(
    JSON.stringify({
      as_of: "2026-10-31T01:28:01Z",
      limit: ALL_COLUMNS_LIMIT,
      total: 5_576,
      universe_total: 5_576,
      returned: symbols.length,
      columns,
      rows: symbols.map((symbol) => {
        const row: Record<string, unknown> = { symbol };
        for (const key of columns) row[key] = `${key}@${symbol}`;
        return row;
      }),
      budget: { truncated: false, dropped_fields: [] },
    }),
    { status: 200, headers: { "content-type": "application/json" } },
  );
}

interface Call {
  columns: string[];
  limit: number;
  offset: number;
}

function stubSelects(
  handler: (call: Call, index: number) => Response | Promise<Response> = (call) => answer(call.columns),
) {
  const calls: Call[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input) !== "/screens/select") throw new Error(`unexpected ${String(input)}`);
    const body = JSON.parse(String(init?.body ?? "{}")) as {
      columns: string[];
      criteria: { limit: number; offset: number };
    };
    const call = {
      columns: body.columns,
      limit: body.criteria.limit,
      offset: body.criteria.offset,
    };
    calls.push(call);
    return handler(call, calls.length - 1);
  });
  vi.stubGlobal("fetch", fetchMock);
  return { calls, fetchMock };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("the catalogue split", () => {
  const served: ViewDefinition[] = [
    { name: "one", label: "One", job: "", columns: ["a", "b"] },
    { name: "wide", label: "Wide", job: "", columns: KEYS },
  ];

  it("treats a view over the 40-column cap as the destination, not a tab", () => {
    const catalogue = splitCatalogue(served);
    expect(catalogue.views.map((view) => view.name)).toEqual(["one"]);
    expect(catalogue.destination?.name).toBe("wide");
    expect(catalogue.keys).toHaveLength(116);
  });

  it("chunks 116 keys as 40 + 40 + 36 and never one empty request", () => {
    expect(chunkColumns(KEYS).map((chunk) => chunk.length)).toEqual([40, 40, 36]);
    expect(chunkColumns([])).toEqual([]);
    expect(chunkColumns(["a"])).toEqual([["a"]]);
  });

  it("keeps order and drops a repeated key rather than asking twice", () => {
    expect(uniqueKeys(["b", "a", "b", "c", "a"])).toEqual(["b", "a", "c"]);
  });
});

describe("three requests, one row set", () => {
  it("issues exactly 3 requests of ≤40 columns and merges 116 distinct keys", async () => {
    const { calls } = stubSelects();
    const { result } = renderHook(() =>
      useAllColumns({ view: "wide", keys: KEYS, limit: ALL_COLUMNS_LIMIT, sort: "market_cap" }),
    );

    await waitFor(() => expect(result.current.status).toBe("ready"));

    expect(calls).toHaveLength(3);
    for (const call of calls) {
      expect(call.columns.length).toBeLessThanOrEqual(40);
      expect(call.limit).toBe(20);
      expect(call.offset).toBe(0);
    }
    const asked = calls.flatMap((call) => call.columns);
    expect(asked).toHaveLength(116);
    expect(new Set(asked).size).toBe(116);

    expect(result.current.rows).toHaveLength(20);
    expect(new Set(result.current.rows.map((row) => row.symbol)).size).toBe(20);
    for (const row of result.current.rows) {
      const keys = Object.keys(row).filter((key) => key !== "symbol");
      expect(keys).toHaveLength(116);
    }
    // Every cell came from the chunk that owns its key.
    const first = result.current.rows[0];
    expect(first?.[KEYS[0] as string]).toBe(`k000@S0`);
    expect(first?.[KEYS[115] as string]).toBe(`k115@S0`);
  });

  it("joins by symbol even when a chunk answers in a different order", async () => {
    stubSelects((call, index) =>
      index === 1 ? answer(call.columns, [...SYMBOLS].reverse()) : answer(call.columns),
    );
    const { result } = renderHook(() =>
      useAllColumns({ view: "wide", keys: KEYS, limit: ALL_COLUMNS_LIMIT }),
    );

    await waitFor(() => expect(result.current.status).toBe("ready"));

    expect(result.current.rows.map((row) => row.symbol)).toEqual(SYMBOLS);
    for (const row of result.current.rows) {
      // Position in chunk 2 says nothing; the symbol does.
      expect(row[KEYS[40] as string]).toBe(`k040@${row.symbol}`);
      expect(row[KEYS[115] as string]).toBe(`k115@${row.symbol}`);
    }
    expect(result.current.merged.gaps).toEqual([]);
  });

  it("names the chunk that failed and leaves its cells absent, not blank-filled", async () => {
    stubSelects((call, index) =>
      index === 1 ? Promise.reject(new Error("network down")) : answer(call.columns),
    );
    const { result } = renderHook(() =>
      useAllColumns({ view: "wide", keys: KEYS, limit: ALL_COLUMNS_LIMIT }),
    );

    await waitFor(() => expect(result.current.status).toBe("ready"));

    // Chunk 1 and 3 answered: the rows exist, the middle band does not.
    expect(result.current.rows).toHaveLength(20);
    expect(result.current.merged.failures).toEqual([
      { chunk: 1, keys: 40, message: "network down" },
    ]);
    const row = result.current.rows[0]!;
    expect(row[KEYS[39] as string]).toBe(`k039@S0`);
    expect(row[KEYS[40] as string]).toBeUndefined();
    expect(absenceReason(result.current.merged.absence, "S0", KEYS[40] as string)).toBe(
      "chunk 2 of 3 failed",
    );
    expect(absenceReason(result.current.merged.absence, "S0", KEYS[39] as string)).toBeNull();

    const notices = mergeNotices(result.current.merged);
    expect(notices.join(" ")).toContain("Chunk 2 of 3 failed (network down)");
    expect(absentCells(result.current.merged, KEYS)).toBe(20 * 40);
  });

  it("reports a symbol missing from one chunk as a gap in that chunk", async () => {
    stubSelects((call, index) =>
      index === 1 ? answer(call.columns, SYMBOLS.filter((symbol) => symbol !== "S7")) : answer(call.columns),
    );
    const { result } = renderHook(() =>
      useAllColumns({ view: "wide", keys: KEYS, limit: ALL_COLUMNS_LIMIT }),
    );

    await waitFor(() => expect(result.current.status).toBe("ready"));

    expect(result.current.merged.gaps).toEqual([{ chunk: 1, symbols: ["S7"] }]);
    const s7 = result.current.rows.find((row) => row.symbol === "S7");
    expect(s7).toBeDefined();
    expect(s7?.[KEYS[0] as string]).toBe(`k000@S7`);
    expect(s7?.[KEYS[40] as string]).toBeUndefined();
    expect(absenceReason(result.current.merged.absence, "S7", KEYS[45] as string)).toBe(
      "chunk 2 of 3 answered without S7",
    );
    expect(mergeNotices(result.current.merged).join(" ")).toContain(
      "Chunk 2 of 3 answered without 1 symbol (S7)",
    );
  });

  it("is an error only when every chunk failed", async () => {
    stubSelects(() => Promise.reject(new Error("offline")));
    const { result } = renderHook(() =>
      useAllColumns({ view: "wide", keys: KEYS, limit: ALL_COLUMNS_LIMIT }),
    );
    await waitFor(() => expect(result.current.status).toBe("error"));
    expect(result.current.error).toContain("All 3 chunks failed");
    expect(result.current.rows).toEqual([]);
  });
});

describe("merging by hand", () => {
  it("never lets a duplicate symbol become two rows", () => {
    const merged = mergeChunks([
      {
        index: 0,
        keys: ["a"],
        rows: [
          { symbol: "X", a: 1 },
          { symbol: "X", a: 2 },
        ],
        error: null,
      },
    ]);
    expect(merged.rows).toEqual([{ symbol: "X", a: 2 }]);
  });

  it("marks a row that only a later chunk returned as partial", () => {
    const merged = mergeChunks([
      { index: 0, keys: ["a"], rows: [{ symbol: "X", a: 1 }], error: null },
      { index: 1, keys: ["b"], rows: [{ symbol: "X", b: 1 }, { symbol: "Y", b: 2 }], error: null },
    ]);
    expect(merged.partial).toEqual(["Y"]);
    expect(merged.rows.map((row) => row.symbol)).toEqual(["X", "Y"]);
    expect(absenceReason(merged.absence, "Y", "a")).toBe("chunk 1 of 2 answered without Y");
  });
});
