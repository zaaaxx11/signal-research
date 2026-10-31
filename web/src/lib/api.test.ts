import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, fetchHealth, searchSymbols } from "./api";

/** The API client is the only module allowed to touch the network: pin it down. */

function respond(body: unknown, status = 200) {
  const fetchMock = vi.fn(
    async (_input: RequestInfo | URL, _init?: RequestInit) =>
      new Response(typeof body === "string" ? body : JSON.stringify(body), {
        status,
        headers: { "content-type": "application/json" },
      }),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("searchSymbols", () => {
  it("encodes the query and pins the page size", async () => {
    const fetchMock = respond([]);
    await searchSymbols("  berkshire hathaway  ");

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "/symbols/search?q=berkshire+hathaway&limit=8",
    );
  });

  it("keeps only rows with the three contract fields", async () => {
    respond([
      { symbol: "AAPL", name: "Apple Inc", exchange: "NASDAQ" },
      { symbol: "BROKEN" },
      { symbol: "MSFT", name: "Microsoft", exchange: "NASDAQ", extra: 1 },
    ]);

    const hits = await searchSymbols("a");
    expect(hits.map((hit) => hit.symbol)).toEqual(["AAPL", "MSFT"]);
  });

  it("never calls fetch for a blank query", async () => {
    const fetchMock = respond([]);
    expect(await searchSymbols("   ")).toEqual([]);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("surfaces the house error envelope as an ApiError", async () => {
    respond({ error: "invalid_query", errorMessage: "q must not be blank" }, 422);

    await expect(searchSymbols("x")).rejects.toThrowError("q must not be blank");
    await expect(searchSymbols("x")).rejects.toBeInstanceOf(ApiError);
  });
});

describe("fetchHealth", () => {
  const report = {
    status: "failing",
    version: "0.1.0",
    checked_at: "2026-10-30T16:13:15+00:00",
    elapsed_ms: 4.2,
    startup_error: "SnapshotError: bad checksum",
    checks: { screen_db: { status: "corrupt", ok: false } },
  };

  it("reads a 503 that carries a health document instead of throwing", async () => {
    respond(report, 503);

    const health = await fetchHealth();
    expect(health.status).toBe("failing");
    expect(health.checks["screen_db"]?.status).toBe("corrupt");
    expect(health.startup_error).toContain("bad checksum");
  });

  it("throws when the body is not a health document", async () => {
    respond("<html>gateway</html>", 502);

    await expect(fetchHealth()).rejects.toBeInstanceOf(ApiError);
  });

  it("fills defaults rather than rendering undefined", async () => {
    respond({ status: "ok" });

    const health = await fetchHealth();
    expect(health.version).toBe("—");
    expect(health.checks).toEqual({});
    expect(health.startup_error).toBeNull();
  });
});
