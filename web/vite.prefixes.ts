/**
 * Same-origin prefixes the shell talks to.
 *
 * `vite dev` proxies each of these to the API; in production FastAPI serves
 * both the API and the SPA, so the list only exists for the dev harness. It
 * lives here rather than inside `vite.config.ts` so it can be asserted on: the
 * config cannot be imported from the test environment (its Tailwind plugin
 * loads esbuild, which refuses to start under jsdom), and a proxy table nobody
 * can test is exactly how `/news` and `/logos` fell through to the SPA and
 * answered `index.html` with a 404-shaped body in dev.
 *
 * A prefix is a **rooted path segment**, never `/`: the fallback to the SPA is
 * what serves `/app/*`.
 */
export const API_PREFIXES: readonly string[] = [
  "/screens",
  "/market",
  "/symbols",
  "/engine",
  "/health",
  // Phase 5/6: the detail panel's News tab and every logo image.
  "/news",
  "/logos",
];
