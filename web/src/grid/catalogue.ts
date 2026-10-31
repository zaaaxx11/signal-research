/**
 * The served catalogue, split into what a tab strip can hold and what it cannot.
 *
 * `GET /screens/select/schema` publishes ten views; the last one is the whole
 * catalogue. The API refuses a selection wider than 40 columns and caps the
 * panel at 60 KB, so a 116-key view can never be a tab — it is a destination.
 *
 * Nothing here spells a view name or a column: the split is *structural*. A
 * view whose column list exceeds `MAX_COLUMNS` cannot be answered in one
 * request, so it is the wide destination; every view that fits is a tab. Rename
 * a view on the server and this module follows without an edit — which is the
 * Phase 0 F1 lesson, tested in `views.test.tsx`.
 */

import { MAX_COLUMNS, type ViewDefinition } from "@/data/screens";

/** The all-columns page size: a small page is the point of a wide view. */
export const ALL_COLUMNS_LIMIT = 20;

export interface Catalogue {
  /** Views a single request can carry: the tab strip, in served order. */
  views: ViewDefinition[];
  /** The view too wide for one request, or null when the server serves none. */
  destination: ViewDefinition | null;
  /** Every key the destination asks for, deduplicated, in served order. */
  keys: string[];
}

/** Order-preserving dedupe: a repeated key is one column, not two requests. */
export function uniqueKeys(columns: readonly string[]): string[] {
  const seen = new Set<string>();
  const keys: string[] = [];
  for (const key of columns) {
    if (typeof key !== "string" || key.length === 0 || seen.has(key)) continue;
    seen.add(key);
    keys.push(key);
  }
  return keys;
}

/**
 * Split `keys` into requests the API will accept.
 *
 * 116 keys at the 40-column cap is 40 + 40 + 36: three requests, no request
 * over the cap, no key asked for twice. The last chunk carries the remainder,
 * and an empty list asks for nothing at all (never one empty request).
 */
export function chunkColumns(keys: readonly string[], size: number = MAX_COLUMNS): string[][] {
  const width = Math.max(1, Math.floor(size));
  const chunks: string[][] = [];
  for (let start = 0; start < keys.length; start += width) {
    chunks.push(keys.slice(start, start + width));
  }
  return chunks;
}

/**
 * The one split every consumer reads.
 *
 * A wide view is the destination and its column list is the catalogue order;
 * when the server serves no wide view, the keys are the union of what it did
 * serve so the picker can still offer every key that exists.
 */
export function splitCatalogue(served: readonly ViewDefinition[]): Catalogue {
  const usable = served.filter((view) => Array.isArray(view.columns));
  const views = usable.filter((view) => view.columns.length <= MAX_COLUMNS);
  const wide = usable
    .filter((view) => view.columns.length > MAX_COLUMNS)
    .sort((left, right) => right.columns.length - left.columns.length);

  const destination = wide[0] ?? null;
  const keys = destination
    ? uniqueKeys(destination.columns)
    : uniqueKeys(usable.flatMap((view) => view.columns));

  return { views, destination, keys };
}
