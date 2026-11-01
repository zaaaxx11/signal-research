/**
 * The control builds for the bundle delta.
 *
 *   node vite build --config src/map/proof/vite.control.config.ts
 *     → baseline.html: shared app dependencies, no map
 *
 *   MAP_PROOF_ENTRY=network node vite build --config src/map/proof/vite.control.config.ts
 *     → network.html: the page with a hand-written payload, no proof generator
 *
 * Same plugins, same target, same stylesheet; only the entry differs, so the
 * gzip difference between two outputs is the code the entry added.
 */

import { fileURLToPath, URL } from "node:url";

import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const SCRATCH = "/home/ubuntu/.hermes/cache/scratch/map-proof";
const NETWORK = process.env["MAP_PROOF_ENTRY"] === "network";
const entry = NETWORK ? "network.html" : "baseline.html";

export default defineConfig({
  root: fileURLToPath(new URL(".", import.meta.url)),
  base: "/",
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { "@": fileURLToPath(new URL("../../", import.meta.url)) },
  },
  build: {
    outDir: `${SCRATCH}/${NETWORK ? "dist-network" : "dist-baseline"}`,
    emptyOutDir: true,
    sourcemap: false,
    target: "es2022",
    rollupOptions: { input: fileURLToPath(new URL(`./${entry}`, import.meta.url)) },
  },
});
