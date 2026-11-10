import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { cacheClear, cacheGet, cacheSet } from "./queryCache";

describe("queryCache", () => {
  beforeEach(() => { cacheClear(); });
  afterEach(() => { cacheClear(); });

  it("returns null on cold cache", () => {
    expect(cacheGet("key")).toBeNull();
  });

  it("round-trips data", () => {
    cacheSet("a", { rows: [1, 2] });
    expect(cacheGet("a")).toEqual({ rows: [1, 2] });
  });

  it("respects TTL", () => {
    cacheSet("b", { x: 1 });
    expect(cacheGet("b", 0)).toBeNull(); // expired immediately
  });

  it("evicts oldest above max entries", () => {
    for (let i = 0; i < 200; i += 1) cacheSet(`k${i}`, i);
    // max 128 entries — oldest 72 should be gone
    expect(cacheGet("k0")).toBeNull();
    expect(cacheGet("k199")).toBe(199);
  });

  it("handles sessionStorage corruption gracefully", () => {
    sessionStorage.setItem("signal-research:query-cache", "not-json");
    expect(cacheGet("any")).toBeNull();
    expect(cacheGet("another")).toBeNull(); // store still functional after corruption
  });
});