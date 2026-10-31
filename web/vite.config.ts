import { readFileSync } from "node:fs";
import { fileURLToPath, URL } from "node:url";

import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
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

export default defineConfig({
  base: BASE,
  plugins: [react(), tailwindcss()],
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
