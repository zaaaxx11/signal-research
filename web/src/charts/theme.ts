/**
 * The chart's palette: the shell's tokens, read from the live stylesheet.
 *
 * A canvas cannot use Tailwind classes, so it is the one surface where a colour
 * can quietly stop being a token. This module closes that from the other side,
 * exactly as `map/theme.ts` does: every colour the painter uses is read with
 * `getComputedStyle` from `tokens.css`, and there is no hex literal in this
 * directory for the audit to allow. A future palette edit repaints the chart
 * without touching the chart.
 *
 * The map's reader supplies the ten slots both canvases share (surface, border,
 * fg, muted, up, down, flat, focus, …). A chart additionally needs `--accent`
 * (the line's rose) and `--fg-subtle` (the previous-close hairline); those two
 * are resolved here with the same local-then-root precedence, so a theme scope
 * on the chart's own element wins.
 *
 * When the document cannot resolve a variable — jsdom, a stylesheet that failed
 * to load — the slot stays empty. The painter treats an empty colour as "do not
 * change the ink" rather than inventing one; the axis text and the summary
 * table are still there, which is the part a reader needs.
 */

import { readTheme, type MapTheme } from "@/map/theme";

export interface ChartTheme extends MapTheme {
  /** `--accent`: the series line, the crosshair, an active range's edge. */
  readonly accent: string;
  /** `--fg-subtle`: the previous close and the quietest axis text. */
  readonly subtle: string;
}

const EXTRA_VARS = {
  accent: "--accent",
  subtle: "--fg-subtle",
} as const;

type ExtraSlot = keyof typeof EXTRA_VARS;

const EXTRA_SLOTS = Object.keys(EXTRA_VARS) as ExtraSlot[];

function resolve(host: Element | null | undefined, name: string): string {
  const local = window.getComputedStyle(host ?? document.documentElement).getPropertyValue(name).trim();
  if (local.length > 0) return local;
  return window
    .getComputedStyle(document.documentElement)
    .getPropertyValue(name)
    .trim();
}

/**
 * The root palette, read once.
 *
 * `getComputedStyle` is the expensive part of a token read, and the gallery
 * mounts five charts in one commit: five uncached reads of twelve variables
 * spent ~100 ms of the first frame resolving values that cannot have changed.
 * The tokens are static for a document (a future light theme would be a
 * `[data-theme]` scope, not a mutation of `:root`), so one cached read is the
 * honest cost. A chart inside an explicit theme scope still reads its own
 * element, where the local values actually apply.
 */
let rootTheme: ChartTheme | null = null;

function scoped(host: Element | null | undefined): Element | null {
  if (host === null || host === undefined) return null;
  return host.closest("[data-theme]");
}

/** Read the chart palette from the live document (or `host`'s theme scope). */
export function readChartTheme(host?: Element | null): ChartTheme {
  if (typeof window === "undefined" || typeof document === "undefined") {
    return emptyChartTheme();
  }
  const scope = scoped(host);
  if (scope === null) {
    if (rootTheme === null) rootTheme = readAt(document.documentElement);
    return rootTheme;
  }
  return readAt(scope);
}

function readAt(element: Element): ChartTheme {
  const base = readTheme(element);
  const accent = resolve(element, EXTRA_VARS.accent) || base.focus;
  const subtle = resolve(element, EXTRA_VARS.subtle) || base.muted;
  return { ...base, accent, subtle };
}

/** Test hook: forget the cached root palette (a stylesheet swapped in a test). */
export function resetChartThemeCache(): void {
  rootTheme = null;
}

function emptyChartTheme(): ChartTheme {
  return {
    surface: "",
    raised: "",
    border: "",
    fg: "",
    muted: "",
    ink: "",
    up: "",
    down: "",
    flat: "",
    focus: "",
    accent: "",
    subtle: "",
  };
}

/**
 * `base`, with every slot `override` actually resolved replacing it.
 *
 * An unchanged result is returned by identity, so the caller's memo does not
 * repaint a canvas for a theme read that learned nothing (the jsdom case, and
 * the first frame before a scoped stylesheet applies).
 */
export function mergeChartTheme(base: ChartTheme, override: ChartTheme): ChartTheme {
  let changed = false;
  const merged = { ...base } as Record<keyof ChartTheme, string>;
  const slots = [...(Object.keys(base) as (keyof ChartTheme)[]), ...EXTRA_SLOTS];
  for (const slot of new Set(slots)) {
    const value = override[slot];
    if (typeof value === "string" && value.length > 0 && value !== base[slot]) {
      merged[slot] = value;
      changed = true;
    }
  }
  return changed ? (merged as ChartTheme) : base;
}
