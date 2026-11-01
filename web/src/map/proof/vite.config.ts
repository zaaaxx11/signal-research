/**
 * Build config for the proof harness — not for the product.
 *
 * `root` is this directory, so `index.html` mounts the map alone; the alias
 * still points at `web/src`, which is what lets the entry import the page and
 * the real stylesheet. Output goes to the scratch directory, never `web/dist`:
 * a proof must not be able to overwrite the artefact the gate measures.
 */

import { fileURLToPath, URL } from "node:url";

import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const SCRATCH = "/home/ubuntu/.hermes/cache/scratch/map-proof";

export default defineConfig({
  root: fileURLToPath(new URL(".", import.meta.url)),
  base: "/",
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { "@": fileURLToPath(new URL("../../", import.meta.url)) },
  },
  build: {
    outDir: `${SCRATCH}/dist`,
    emptyOutDir: true,
    sourcemap: false,
    target: "es2022",
  },
});
