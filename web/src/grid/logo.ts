/**
 * Where a symbol's brand mark lives.
 *
 * The mark is **not** part of the row payload. A monogram is a base64 data URI
 * of roughly 640 B; a hundred of them is 64 KB, more than the API's whole 60 KB
 * panel budget, so a panel that asked for `logo` would pay for it in rows. The
 * lane therefore holds an `<img>` that names the symbol, and the API serves the
 * bytes once — from a route that caches for 30 days — instead of a hundred
 * times inside a table.
 *
 * Encoding: `encodeURIComponent`, one path segment. A dot is a legal path
 * character and is deliberately left alone, so `BRK.B` asks for `/logos/BRK.B`
 * — the spelling the endpoint's router expects. Anything that would change the
 * *structure* of the path (`/`, `?`, `#`, a space) is percent-encoded, so a
 * symbol can never splice a second segment or a query onto the request.
 */

/** The reserved box, in px. The lane's own height is 28; this leaves 4px a side. */
export const LOGO_SIZE = 20;

export function logoPath(symbol: string): string {
  return `/logos/${encodeURIComponent(symbol)}`;
}
