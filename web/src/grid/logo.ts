/**
 * Where a symbol's brand mark lives.
 *
 * The mark is **not** part of the row payload. A monogram is a base64 data URI
 * of roughly 640 B; a hundred of them is 64 KB, more than the API's whole 60 KB
 * panel budget, so a panel that asked for `logo` would pay for it in rows. The
 * lane therefore holds an `<img>` that names the symbol, and the API serves the
 * bytes once — from a route whose real marks revalidate on their digest —
 * instead of a hundred times inside a table.
 *
 * Encoding: `encodeURIComponent`, one path segment. A dot is a legal path
 * character and is deliberately left alone, so `BRK.B` asks for `/logos/BRK.B`
 * — the spelling the endpoint's router expects. Anything that would change the
 * *structure* of the path (`/`, `?`, `#`, a space) is percent-encoded, so a
 * symbol can never splice a second segment or a query onto the request.
 *
 * `logoPath(symbol, bust)` appends `?v=…`, which the endpoint ignores. It exists
 * so a failed `<img>` can be asked again under a URL the browser's cache has
 * not already answered; an un-busted request keeps the bare path so the common
 * case is the one the server and its ETag were built for.
 */

/** The reserved box, in px. The lane's own height is 28; this leaves 4px a side. */
export const LOGO_SIZE = 20;

export function logoPath(symbol: string, bust?: string | number): string {
  const path = `/logos/${encodeURIComponent(symbol)}`;
  return bust === undefined ? path : `${path}?v=${encodeURIComponent(String(bust))}`;
}

/* ---------------------------------------------------------------------------
 * The session cache
 *
 * A row asks for the same forty marks every time the table re-renders, and a
 * navigation unmounts every <img> it drew. Without a memo the next mount starts
 * from the skeleton again and the reader sees the lane flash empty — the bug
 * that made logos look "sometimes missing". The map remembers which URL the
 * browser has already decoded, so a revisit paints on the first frame.
 *
 * An error deletes the entry: a broken mark must be allowed to try again on the
 * next mount instead of freezing a failure for the whole session.
 */
const loaded = new Map<string, string>();

/** The URL that already painted for ``symbol``, if any. */
export function cachedLogo(symbol: string): string | undefined {
  return loaded.get(symbol);
}

/** Remember that ``src`` painted, so the next mount skips the skeleton. */
export function rememberLogo(symbol: string, src: string): void {
  loaded.set(symbol, src);
}

/** Forget a mark that failed, so a later mount may retry it. */
export function forgetLogo(symbol: string): void {
  loaded.delete(symbol);
}

/** Warm the pipeline for the symbols a page is about to draw. */
export function preloadLogos(symbols: readonly string[]): void {
  if (typeof Image === "undefined") return;
  for (const symbol of symbols) {
    const src = cachedLogo(symbol) ?? logoPath(symbol);
    if (cachedLogo(symbol) !== undefined) continue;
    const image = new Image();
    image.decoding = "async";
    image.onload = () => rememberLogo(symbol, src);
    image.src = src;
  }
}
