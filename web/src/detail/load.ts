/**
 * The one dynamic import of the detail panel, shared by its two mounts.
 *
 * `/screener` docks the panel beside the grid and `/stock/{symbol}` renders it
 * as the page. Both must load the same chunk — a second `import("@/detail")`
 * would still be one file on disk, but the retry budget and the test seam would
 * be two, and "the panel failed to load" would mean two different things
 * depending on where the reader was standing.
 *
 * A deploy that rotated the chunk's hash while a tab was open answers the
 * import with a 404. One retry covers a dropped response and a deploy that
 * finished mid-flight; the second failure belongs to the boundary, which names
 * it and offers a way back — never to a loop that keeps asking.
 */

import * as React from "react";

import type { DetailPanelProps } from "./DetailPanel";

/** How many times one load of the chunk may ask the network: import + retry. */
export const DETAIL_CHUNK_ATTEMPTS = 2;

/** One deploy's worth of a blip, not a backoff. */
const DETAIL_CHUNK_RETRY_DELAY_MS = 200;

export interface DetailChunkModule {
  default: React.ComponentType<DetailPanelProps>;
}

/**
 * The import, behind a property so a test can make it reject.
 *
 * A chunk 404 is the failure the boundary exists for, and jsdom cannot produce
 * one any other way; the rejection this hands back is the same
 * `TypeError: Failed to fetch dynamically imported module` a browser raises.
 */
export const detailChunk: { load: () => Promise<DetailChunkModule> } = {
  load: () => import("@/detail"),
};

export async function loadDetailPanel(): Promise<DetailChunkModule> {
  try {
    return await detailChunk.load();
  } catch {
    await new Promise((resolve) => setTimeout(resolve, DETAIL_CHUNK_RETRY_DELAY_MS));
    return detailChunk.load();
  }
}
