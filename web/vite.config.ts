import { readFileSync } from "node:fs";
import { fileURLToPath, URL } from "node:url";

import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import type { Plugin } from "vite";
import { defineConfig } from "vitest/config";

import { API_PREFIXES } from "./vite.prefixes";

const pkg = JSON.parse(
  readFileSync(new URL("./package.json", import.meta.url), "utf8"),
) as { version: string };

/**
 * The app is served by FastAPI under `/app`, so every generated URL must carry
 * that prefix. `base` is the single source of truth: dev server, asset URLs in
 * `index.html`, and the module graph all read it. Changing it here and nowhere
 * else is what keeps a hard refresh on a deep link from 404ing its own chunks.
 */
const BASE = "/app/";
const API_ORIGIN = "http://127.0.0.1:8099";

/**
 * The link-preview tags a static document cannot state: `og:url` and `og:image`
 * require absolute URLs, and this repository deliberately names no host (the
 * deployment can move domains, and the shell must not carry infrastructure).
 *
 * So the markup carries a marker, and a build that is told its public origin —
 * `VITE_PUBLIC_ORIGIN=https://… npm run build`, set by `scripts/deploy.sh` from
 * the box's own environment — replaces it with the two tags. A build without the
 * variable ships no absolute URL at all: no preview image, and nothing wrong.
 */
function socialPreviewTags(): Plugin {
  const origin = (process.env.VITE_PUBLIC_ORIGIN ?? "").trim().replace(/\/+$/, "");
  return {
    name: "ztockz-social-preview",
    transformIndexHtml: {
      order: "pre",
      handler(html: string) {
        if (!origin) return html;
        return html.replace(
          "<!-- og:origin -->",
          `<meta property="og:url" content="${origin}/" />\n` +
            `    <meta property="og:image" content="${origin}/favicon.svg" />`,
        );
      },
    },
  };
}

export default defineConfig({
  base: BASE,
  plugins: [react(), tailwindcss(), socialPreviewTags()],
  define: {
    __APP_VERSION__: JSON.stringify(pkg.version),
  },
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    port: 5173,
    strictPort: true,
    proxy: Object.fromEntries(
      API_PREFIXES.map((prefix) => [
        prefix,
        { target: API_ORIGIN, changeOrigin: false },
      ]),
    ),
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    // No source maps in the served artefact: they are source code, and the
    // mount is public. Debug builds use `vite dev`.
    sourcemap: false,
    target: "es2022",
    assetsInlineLimit: 2048,
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    css: false,
    restoreMocks: true,
  },
});
