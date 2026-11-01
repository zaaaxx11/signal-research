/**
 * The map's palette: the tokens, read once, and a ramp between them.
 *
 * A canvas cannot use Tailwind classes, so it is the one surface where a colour
 * could quietly stop being a token. This module closes that hole from the other
 * side: every colour the painter uses is *read from* the stylesheet at run time
 * (`getComputedStyle` on `--up`, `--down`, `--flat`, …). There is no hex value
 * in this file, so the token audit has nothing to allow and a future palette
 * change repaints the map without touching the map.
 *
 * The ramp itself is `--flat` at zero and `--up` / `--down` at ±`saturation`
 * percent, mixed in sRGB. sRGB is deliberate: it is the space the browser
 * composites in, so a tile that is 40 % of the way from grey to green looks
 * like 40 %, and the legend swatch — the same function at the sector's average
 * — cannot disagree with the block it sits beside.
 *
 * Text on a tile is not a fixed ink. A tile near zero is `--flat`, a light
 * grey; a strong gain is a saturated green. Both exist, so `inkFor` picks
 * between `--fg` (light ink) and `--accent-fg` (the dark ink already measured
 * at 5.43:1 on the rose fill) by the fill's relative luminance. Two tokens,
 * chosen by arithmetic — not a third colour invented for contrast.
 */

export interface MapTheme {
  /** The canvas plane, behind everything. */
  readonly surface: string;
  /** The sector block behind its tiles (visible where tiles were omitted). */
  readonly raised: string;
  readonly border: string;
  readonly fg: string;
  readonly muted: string;
  /** Ink for a light tile: `--accent-fg`, the dark weight. */
  readonly ink: string;
  readonly up: string;
  readonly down: string;
  readonly flat: string;
  readonly focus: string;
}

/** Token name per slot, so the reader cannot drift from `tokens.css`. */
const VARS: Readonly<Record<keyof MapTheme, string>> = {
  surface: "--surface",
  raised: "--surface-raised",
  border: "--border",
  fg: "--fg",
  muted: "--fg-muted",
  ink: "--accent-fg",
  up: "--up",
  down: "--down",
  flat: "--flat",
  focus: "--focus",
};

const SLOTS = Object.keys(VARS) as (keyof MapTheme)[];

/**
 * Read the palette from the live document.
 *
 * `host` is the element the map is mounted in, so a future theme scope (a
 * `[data-theme]` block) resolves to the values that actually apply there. When
 * a variable is missing — jsdom, or a stylesheet that failed to load — the slot
 * stays empty and the painter treats it as transparent rather than inventing a
 * colour; the text and table are still there, which is the part that matters.
 */
export function readTheme(host?: Element | null): MapTheme {
  if (typeof window === "undefined" || typeof document === "undefined") {
    return emptyTheme();
  }
  const target = host ?? document.documentElement;
  const computed = window.getComputedStyle(target);
  const root = target === document.documentElement ? computed : window.getComputedStyle(document.documentElement);
  const theme = {} as Record<keyof MapTheme, string>;
  for (const slot of SLOTS) {
    const name = VARS[slot];
    const local = computed.getPropertyValue(name).trim();
    theme[slot] = local.length > 0 ? local : root.getPropertyValue(name).trim();
  }
  return theme;
}

function emptyTheme(): MapTheme {
  return { surface: "", raised: "", border: "", fg: "", muted: "", ink: "", up: "", down: "", flat: "", focus: "" };
}

/**
 * `base`, with every slot `override` actually resolved replacing it.
 *
 * `readTheme` cannot distinguish "this document has no `--up`" from "this
 * document is not styled" (jsdom, a stylesheet that failed), and in both cases
 * the right answer is the same: keep the caller's colour rather than paint
 * with an empty string. An unchanged result is returned by identity so the
 * caller's memo does not repaint the canvas.
 */
export function mergeTheme(base: MapTheme, override: MapTheme): MapTheme {
  let changed = false;
  const merged = { ...base } as Record<keyof MapTheme, string>;
  for (const slot of SLOTS) {
    const value = override[slot];
    if (value.length > 0 && value !== base[slot]) {
      merged[slot] = value;
      changed = true;
    }
  }
  return changed ? merged : base;
}

export interface Rgb {
  readonly r: number;
  readonly g: number;
  readonly b: number;
}

const HEX = /^#([0-9a-f]{3,8})$/i;
const FUNC = /^rgba?\(\s*([\d.]+)[\s,]+([\d.]+)[\s,]+([\d.]+)/i;

/**
 * `#3ddc97`, `#3dd`, `rgb(61, 220, 151)` and `rgba(…, .5)` → channels.
 *
 * A value this cannot read returns null. That is not an error to swallow: the
 * caller decides, and the ramp's caller answers "no fill" rather than guessing.
 */
export function parseColour(value: string): Rgb | null {
  const raw = value.trim();
  if (raw.length === 0) return null;

  const hex = HEX.exec(raw);
  if (hex) {
    let digits = (hex[1] as string).toLowerCase();
    if (digits.length === 3 || digits.length === 4) {
      digits = digits
        .slice(0, 3)
        .split("")
        .map((digit) => digit + digit)
        .join("");
    }
    if (digits.length !== 6 && digits.length !== 8) return null;
    return {
      r: parseInt(digits.slice(0, 2), 16),
      g: parseInt(digits.slice(2, 4), 16),
      b: parseInt(digits.slice(4, 6), 16),
    };
  }

  const fn = FUNC.exec(raw);
  if (fn) {
    return {
      r: Math.max(0, Math.min(255, Number(fn[1]))),
      g: Math.max(0, Math.min(255, Number(fn[2]))),
      b: Math.max(0, Math.min(255, Number(fn[3]))),
    };
  }
  return null;
}

/** The WCAG 2.x relative luminance, for choosing ink — not for contrast claims. */
export function relativeLuminance(rgb: Rgb): number {
  const channel = (value: number): number => {
    const c = value / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * channel(rgb.r) + 0.7152 * channel(rgb.g) + 0.0722 * channel(rgb.b);
}

function mix(from: Rgb, to: Rgb, t: number): Rgb {
  const ratio = Math.max(0, Math.min(1, t));
  return {
    r: Math.round(from.r + (to.r - from.r) * ratio),
    g: Math.round(from.g + (to.g - from.g) * ratio),
    b: Math.round(from.b + (to.b - from.b) * ratio),
  };
}

/** `{r,g,b}` → `rgb(r, g, b)`, the one string the canvas is given. */
export function cssRgb(rgb: Rgb): string {
  return `rgb(${rgb.r}, ${rgb.g}, ${rgb.b})`;
}

/**
 * The ramp's middle.
 *
 * `--flat` is the *text* token for "unchanged" (a light grey, 7.33:1 on a row).
 * A canvas painted 40 % `--flat` would read as a light-grey board with a dark
 * frame, which is the opposite of the product's plane. So the ramp's neutral is
 * `--surface-raised` — the token that already means "a plane with nothing on
 * it" — and `--flat` is kept for the one state that is genuinely unknown: a
 * sector whose average change is absent.
 */
function neutral(theme: MapTheme): Rgb {
  return (
    parseColour(theme.raised) ??
    parseColour(theme.surface) ??
    parseColour(theme.flat) ?? { r: 0, g: 0, b: 0 }
  );
}

/**
 * A change, as a colour: grey at 0 %, full `--up` at +3 %, full `--down` at
 * −3 %. The clamp is the useful part — a 12 % day and a 40 % day are both "the
 * strongest green on the map", or the rest of the market disappears next to one
 * outlier.
 */
export function rampRgb(change: number, theme: MapTheme, saturation = 3): Rgb {
  const base = neutral(theme);
  if (!Number.isFinite(change) || change === 0) return base;
  const extreme = parseColour(change > 0 ? theme.up : theme.down);
  if (extreme === null) return base;
  const span = Number.isFinite(saturation) && saturation > 0 ? saturation : 3;
  return mix(base, extreme, Math.abs(change) / span);
}

/** The fill for a change, as a canvas colour string. */
export function rampColor(change: number, theme: MapTheme, saturation = 3): string {
  return cssRgb(rampRgb(change, theme, saturation));
}

/**
 * WCAG 2.1's contrast ratio. Used here only to *choose between two ink tokens*
 * — the ratios themselves are measured in `styles/tokens.test.ts`.
 */
export function contrastRatio(left: Rgb, right: Rgb): number {
  const a = relativeLuminance(left);
  const b = relativeLuminance(right);
  const lighter = Math.max(a, b);
  const darker = Math.min(a, b);
  return (lighter + 0.05) / (darker + 0.05);
}

/**
 * Ink for a filled tile: whichever of `--fg` and `--accent-fg` contrasts more
 * with the fill.
 *
 * The ramp crosses the point where light ink stops working — a strong gain is
 * `--up`, a light mint, and white text on it is 1.5:1. Picking the better of
 * the two declared inks at every point keeps a 10 px symbol readable across the
 * whole scale without introducing a third colour, and without a threshold that
 * a future palette edit could invalidate.
 */
export function inkFor(fill: Rgb, theme: MapTheme): string {
  const light = parseColour(theme.fg);
  const dark = parseColour(theme.ink);
  if (light === null || dark === null) return theme.fg;
  return contrastRatio(fill, dark) >= contrastRatio(fill, light) ? theme.ink : theme.fg;
}

/**
 * The legend swatch for a sector's average change.
 *
 * Absent is `--flat`, not the ramp's zero: a sector nobody measured has no
 * direction, and painting it the same colour as "unchanged" would claim it
 * did not move.
 */
export function swatch(change: number | null, theme: MapTheme): string {
  if (change === null || !Number.isFinite(change)) return theme.flat || rampColor(0, theme);
  return rampColor(change, theme);
}

/**
 * A theme parsed once, for the painter's inner loop.
 *
 * `readTheme` returns CSS strings; `paintMap` runs over 5,576 tiles and cannot
 * afford to parse two hex values per tile. Preparing the palette up front turns
 * the per-tile work into arithmetic, and — more importantly — makes the
 * painter's colour decisions *data*, so `paint.test.ts` can assert them without
 * a stylesheet.
 */
export interface Palette {
  readonly surface: string;
  readonly raised: string;
  readonly border: string;
  readonly fg: string;
  readonly muted: string;
  readonly focus: string;
  readonly neutral: Rgb;
  readonly up: Rgb | null;
  readonly down: Rgb | null;
  readonly ink: string;
  readonly lightRgb: Rgb | null;
  readonly darkRgb: Rgb | null;
}

export function prepareTheme(theme: MapTheme): Palette {
  return {
    surface: theme.surface,
    raised: theme.raised,
    border: theme.border,
    fg: theme.fg,
    muted: theme.muted,
    focus: theme.focus,
    neutral: neutral(theme),
    up: parseColour(theme.up),
    down: parseColour(theme.down),
    ink: theme.ink,
    lightRgb: parseColour(theme.fg),
    darkRgb: parseColour(theme.ink),
  };
}

export interface RampFill {
  readonly fill: string;
  readonly ink: string;
  readonly rgb: Rgb;
  /** Contrast of `ink` on `rgb`; the painter refuses text below 4.5. */
  readonly contrast: number;
}

/** The painter's ramp: the same maths as `rampRgb`, with the palette prepared. */
export function rampFill(change: number, palette: Palette, saturation = 3): RampFill {
  const extreme = change > 0 ? palette.up : palette.down;
  const rgb =
    !Number.isFinite(change) || change === 0 || extreme === null
      ? palette.neutral
      : mix(palette.neutral, extreme, Math.abs(change) / (saturation > 0 ? saturation : 3));

  let ink = palette.fg;
  let contrast = 0;
  if (palette.lightRgb !== null && palette.darkRgb !== null) {
    const onDark = contrastRatio(rgb, palette.darkRgb);
    const onLight = contrastRatio(rgb, palette.lightRgb);
    ink = onDark >= onLight ? palette.ink : palette.fg;
    contrast = Math.max(onDark, onLight);
  }
  return { fill: cssRgb(rgb), ink, rgb, contrast };
}
