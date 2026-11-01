/**
 * The proof harness: the real `MapPage`, the real stylesheet, 5,576 real rows.
 *
 * `npm run build` never sees this file (it has its own entry), so it costs the
 * product nothing. It exists because jsdom can prove the layout but not the
 * frame: the draw time, the painted pixels, the pointer latency and the DPR
 * backing store all need a browser, and a browser needs an entry that mounts
 * the page without the shell's route table.
 *
 * The payload is generated in-page from the same seeded fixture the tests use,
 * so the proof is reproducibility rather than a download.
 *
 * `window.__mapProof` is the measurement hook. It runs the *shipped* layout and
 * painter — not a copy — against a canvas of the on-screen size, and flushes the
 * raster (`getImageData`) so the number includes pixels rather than just the
 * command queue.
 */

import { createRoot } from "react-dom/client";

import "@/styles/index.css";
import { MapPage, layoutMap, paintMap, parseMap, prepareTheme, readTheme } from "@/map";
import { generateWireMap } from "@/map/__fixtures__/payload";

const payload = generateWireMap();
const parsed = parseMap(payload);

declare global {
  interface Window {
    __mapProof?: {
      bench: (runs?: number) => Record<string, unknown>;
    };
  }
}

// The endpoint is not needed for a rendering proof; the hook's one read is
// answered with the fixture, shaped exactly like the route's JSON.
const realFetch = window.fetch.bind(window);
window.fetch = (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
  const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
  if (url.startsWith("/market/map")) {
    return Promise.resolve(
      new Response(JSON.stringify(payload), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
  }
  return realFetch(input, init);
};

function percentile(values: number[], fraction: number): number {
  const sorted = [...values].sort((left, right) => left - right);
  return sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * fraction))] as number;
}

window.__mapProof = {
  bench(runs = 24) {
    const onScreen = document.querySelector('[data-testid="map-canvas"]') as HTMLCanvasElement | null;
    const box = onScreen?.getBoundingClientRect() ?? { width: 1204, height: 868 };
    const width = Math.round(box.width);
    const height = Math.round(box.height);

    const layoutTimes: number[] = [];
    let layout = layoutMap(parsed, width, height);
    for (let index = 0; index < 8; index += 1) {
      const started = performance.now();
      layout = layoutMap(parsed, width, height);
      layoutTimes.push(performance.now() - started);
    }

    const palette = prepareTheme(readTheme(onScreen));
    const offscreen = document.createElement("canvas");
    offscreen.width = width;
    offscreen.height = height;
    const ctx = offscreen.getContext("2d", { alpha: false }) as CanvasRenderingContext2D;

    const paint: number[] = [];
    const flush: number[] = [];
    for (let index = 0; index < runs; index += 1) {
      const started = performance.now();
      paintMap(ctx, layout, palette, { dpr: 1, width, height });
      const issued = performance.now();
      // Force the raster to finish so the sample includes pixels, not just the
      // commands queued for the compositor.
      ctx.getImageData(0, 0, 1, 1);
      flush.push(performance.now() - started);
      paint.push(issued - started);
    }

    return {
      size: { width, height },
      tiles: layout.drawn,
      sectors: layout.sectors.length,
      layout: { median: percentile(layoutTimes, 0.5), min: Math.min(...layoutTimes) },
      paint: {
        median: percentile(paint, 0.5),
        min: Math.min(...paint),
        p95: percentile(paint, 0.95),
      },
      paintWithRaster: {
        median: percentile(flush, 0.5),
        min: Math.min(...flush),
        p95: percentile(flush, 0.95),
      },
    };
  },
};

const root = document.getElementById("root");
if (root) {
  createRoot(root).render(
    <div style={{ position: "fixed", inset: 0, display: "flex" }}>
      <MapPage />
    </div>,
  );
}
