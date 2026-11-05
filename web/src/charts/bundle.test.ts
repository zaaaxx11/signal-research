/**
 * The bundle gate, as a test.
 *
 * `scripts/check-budget.mjs` is the authoritative measurement: it runs after
 * `vite build` and fails `npm run check` on the artefact the server ships. This
 * file adds the half a size number cannot express — **where** the weight is:
 *
 *   * `lightweight-charts` must not be reachable from `main.tsx` through
 *     static imports. A single `import { createChart } from "lightweight-charts"`
 *     in a module Home's chunk pulls in would put ~62 KB gzip in front of first
 *     paint and the budget would fail, but the cause would be a line in a file
 *     far from the number. This walks the static graph from the entry and names
 *     the culprit.
 *   * `ChartBars` must be reached through `import()`. The walker never follows a
 *     dynamic import, so its absence from the graph *is* the proof.
 *   * and, when a build is present, the same gzip arithmetic the script does:
 *     initial scripts in `dist/index.html` (entry + modulepreload) must stay
 *     under 180 KB. A clean checkout has no `dist/` when `npm test` runs — the
 *     script still gates the real build — so this half skips rather than fails
 *     on an absent artefact.
 */

import { existsSync, readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { gzipSync } from "node:zlib";

import { describe, expect, it } from "vitest";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, "../..");
const SRC = path.join(ROOT, "src");
const DIST = path.join(ROOT, "dist");

/** 180 KB, to the byte: the number `check-budget.mjs` enforces. */
const BUDGET_BYTES = 180 * 1024;

/** Remove comments so an `import()` named in prose is not read as code. */
function stripComments(source: string): string {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/^[ \t]*\/\/.*$/gm, "")
    .replace(/\s\/\/[^\n"'`]*$/gm, "");
}

/** Every module a file imports (or re-exports) statically. */
export function staticSpecifiers(source: string): string[] {
  const code = stripComments(source);
  const specs: string[] = [];
  const patterns = [
    // `import x from "…"`, `import { a, b } from "…"`, `import * as ns from "…"`
    /\bimport\s+(?!type\s)[^;]*?\bfrom\s*["']([^"']+)["']/g,
    // `export { x } from "…"`, `export * from "…"`
    /\bexport\s+(?!type\s)[^;]*?\bfrom\s*["']([^"']+)["']/g,
    // `import "…"` (side effects only)
    /\bimport\s*["']([^"']+)["']/g,
  ];
  for (const pattern of patterns) {
    for (const match of code.matchAll(pattern)) {
      if (match[1] !== undefined) specs.push(match[1]);
    }
  }
  return specs;
}

/** Resolve a specifier the way Vite does for this project (`@/` → `src/`). */
export function resolveSpecifier(from: string, specifier: string): string | null {
  if (specifier.startsWith(".")) return path.resolve(path.dirname(from), specifier);
  if (specifier.startsWith("@/")) return path.join(SRC, specifier.slice(2));
  return null; // a package or a built-in: not part of this walk
}

function withExtension(candidate: string): string | null {
  const attempts = [
    candidate,
    `${candidate}.ts`,
    `${candidate}.tsx`,
    path.join(candidate, "index.ts"),
    path.join(candidate, "index.tsx"),
  ];
  return attempts.find((file) => existsSync(file)) ?? null;
}

export interface StaticGraph {
  readonly files: string[];
  readonly packages: string[];
}

/** The static import graph rooted at a file. Dynamic `import()` is not followed. */
export function staticGraph(entry: string): StaticGraph {
  const files: string[] = [];
  const packages: string[] = [];
  const seen = new Set<string>();
  const queue = [entry];

  while (queue.length > 0) {
    const file = queue.shift();
    if (file === undefined || seen.has(file)) continue;
    seen.add(file);
    files.push(file);

    for (const specifier of staticSpecifiers(readFileSync(file, "utf8"))) {
      const resolved = resolveSpecifier(file, specifier);
      if (resolved === null) {
        packages.push(specifier);
        continue;
      }
      const target = withExtension(resolved);
      if (target !== null) queue.push(target);
    }
  }

  return { files, packages };
}

describe("bundle — the chart library stays behind a dynamic import", () => {
  const graph = staticGraph(path.join(SRC, "main.tsx"));
  const relative = graph.files.map((file) => path.relative(ROOT, file));

  it("walks a real graph from the entry", () => {
    expect(relative).toContain(path.join("src", "main.tsx"));
    // Home, the stock detail and the screener are now their own lazy chunks, so
    // the static graph is shallower by design: it stops at the shell chrome and
    // the page loaders, and the pages themselves travel via import().
    expect(relative).toContain(path.join("src", "shell", "HeaderBar.tsx"));
    expect(relative).toContain(path.join("src", "components", "ToastCenter.tsx"));
    expect(relative).toContain(path.join("src", "shell", "lazyPages.ts"));
    // And the lazy leaves prove the split actually happened.
    expect(relative).not.toContain(path.join("src", "home", "HomePage.tsx"));
    expect(relative).not.toContain(path.join("src", "stock", "StockPage.tsx"));
    expect(relative.length).toBeGreaterThan(20);
  });

  it("never reaches lightweight-charts statically", () => {
    expect(graph.packages.filter((name) => name.startsWith("lightweight-charts"))).toEqual([]);
  });

  it("never reaches ChartBars statically", () => {
    expect(relative.filter((file) => file.endsWith(path.join("charts", "ChartBars.tsx")))).toEqual(
      [],
    );
  });

  it("reaches a chart component only through import()", () => {
    const lazy = readFileSync(path.join(SRC, "charts", "lazy.tsx"), "utf8");
    expect(lazy).toMatch(/import\(["']\.\/ChartBars["']\)/);
    // The type import is allowed; it is erased at build time.
    expect(lazy).not.toMatch(/^import\s+\{[^}]*ChartBars[^}]*\}\s+from/m);
  });
});

describe("bundle — the built initial chunk stays within budget", () => {
  const index = path.join(DIST, "index.html");

  it("measures dist/index.html when a build exists", () => {
    if (!existsSync(index)) {
      // `npm test` runs before `npm run build`; on a clean checkout there is
      // nothing to measure and `npm run budget` is the gate.
      console.info("[bundle] no dist/index.html — size check deferred to `npm run budget`");
      return;
    }

    const html = readFileSync(index, "utf8");
    const initial = new Set<string>();
    for (const match of html.matchAll(/<script[^>]+src="([^"]+\.js)"/g)) {
      if (match[1] !== undefined) initial.add(match[1]);
    }
    for (const match of html.matchAll(
      /<link[^>]+rel="modulepreload"[^>]+href="([^"]+\.js)"/g,
    )) {
      if (match[1] !== undefined) initial.add(match[1]);
    }

    expect(initial.size).toBeGreaterThan(0);

    let gzip = 0;
    for (const url of initial) {
      const marker = url.indexOf("/assets/");
      expect(marker, `cannot map ${url} onto dist/`).toBeGreaterThan(-1);
      gzip += gzipSync(readFileSync(path.join(DIST, url.slice(marker + 1))), { level: 9 }).length;
    }

    expect(gzip).toBeLessThanOrEqual(BUDGET_BYTES);

    // And the chart chunk is a chunk of its own: something in dist/assets
    // carries the library, and index.html does not preload it.
    const assets = readdirSync(path.join(DIST, "assets")).filter((name) => name.endsWith(".js"));
    expect(assets.length).toBeGreaterThan(initial.size);
  });
});
