import { describe, expect, it } from "vitest";

import { API_PREFIXES } from "../vite.prefixes";

/**
 * F4 — `vite dev` must serve the two routes the phase-5/6 features added.
 *
 * `API_PREFIXES` is the proxy table: anything outside it falls through to the
 * SPA and answers `index.html` with a 404-shaped body, which is exactly what the
 * report measured for `GET /news/AAPL` (looked like an HTML route, not the
 * API's JSON) and for every `/logos/*` image. The list lives in its own module
 * because the config cannot be imported from a jsdom test (its Tailwind plugin
 * loads esbuild, which refuses to start there — measured), and a proxy table
 * nobody can assert on is a proxy table that rots.
 */

/** The paths the shell asks for, as the modules that ask for them build them. */
const REQUESTED = {
  screens: "/screens/select",
  market: "/market/breadth?region=us&asset_class=stock",
  symbols: "/symbols?q=app",
  engine: "/engine/fundamentals/AAPL",
  health: "/health",
  news: "/news/AAPL?limit=20",
  logos: "/logos/AAPL",
};

function prefixOf(path: string): string {
  const [root] = path.split(/[/?]/).filter((part) => part.length > 0);
  return `/${root}`;
}

describe("the dev proxy table (F4)", () => {
  it("still covers every route the shell had before", () => {
    for (const name of ["screens", "market", "symbols", "engine", "health"] as const) {
      expect(API_PREFIXES).toContain(prefixOf(REQUESTED[name]));
    }
  });

  it("covers /news and /logos, the two routes phase 5/6 added", () => {
    expect(prefixOf(REQUESTED.news)).toBe("/news");
    expect(prefixOf(REQUESTED.logos)).toBe("/logos");
    expect(API_PREFIXES).toContain("/news");
    expect(API_PREFIXES).toContain("/logos");
  });

  it("has a prefix for every path the shell requests", () => {
    for (const path of Object.values(REQUESTED)) {
      expect(API_PREFIXES).toContain(prefixOf(path));
    }
  });

  it("lists rooted paths only, with no duplicates", () => {
    for (const prefix of API_PREFIXES) {
      expect(prefix.startsWith("/")).toBe(true);
      expect(prefix.length).toBeGreaterThan(1);
      expect(prefix.endsWith("/")).toBe(false);
    }
    expect(new Set(API_PREFIXES).size).toBe(API_PREFIXES.length);
  });
});
