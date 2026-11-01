import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

/**
 * WCAG 2.1 contrast gate for BOTH token scopes.
 *
 * The tokens are parsed from `tokens.css` — the file the app actually ships,
 * not a copy — so a colour edited in CSS is measured here on the next run.
 * Nothing in this file knows a hex by heart: if `--up` becomes `#0b5c3a`, the
 * test fails because the arithmetic says so, not because someone remembered.
 *
 * Two blocks, one variable vocabulary
 *   `:root` is the dark set (the shipped default); `[data-theme="light"]` is
 *   the day set. The light block must be a *full restatement*: it may not leave
 *   a name to be inherited from dark, and it may not reach into the dark block
 *   through `var()` — a scope that resolves half its values from the other one
 *   is not a theme, it is an override, and it fails the moment a dark value
 *   moves. So the parser is scope-aware (`parseScope`), the key sets are
 *   compared before a single ratio is measured, and every pair below runs
 *   twice, once per scope, with the scope named in its failure.
 *
 * Sizes are declared per pair so "large text" can never be used as a quiet
 * downgrade to 3.0:1. Every data pair is 12 px at 400–600 weight, which is
 * normal text, so every threshold below is 4.5.
 */

// Vitest's jsdom environment gives modules an http:// URL, so `import.meta.url`
// is not a file path here; the test root is the package root by contract.
const TOKENS_CSS = resolve(process.cwd(), "src/styles/tokens.css");

/** The two scopes, in the order the stylesheet declares them. */
const DARK_SCOPE = ":root";
const LIGHT_SCOPE = '[data-theme="light"]';

const CSS = readFileSync(TOKENS_CSS, "utf8");

/** WCAG 2.1 §1.4.3 thresholds. */
const AA_NORMAL = 4.5;
const AA_LARGE = 3.0;

/**
 * WCAG 2.1 §1.4.11 (non-text contrast): the visual information needed to
 * identify a control — its boundary, its state — must reach 3:1 against the
 * colour next to it. Text thresholds do not apply to a border: a 1 px edge is
 * not text, and measuring it at 4.5 would be a different (stricter) rule than
 * the one the spec states.
 */
const AA_NON_TEXT = 3.0;

/** §1.4.3 definition of large: ≥18.66 px bold, or ≥24 px regular. */
export function isLargeText(sizePx: number, weight: number): boolean {
  return sizePx >= 24 || (sizePx >= 18.66 && weight >= 700);
}

/** Parse `--name: value;` declarations, ignoring `var(...)` aliases. */
export function parseTokens(css: string): Record<string, string> {
  const tokens: Record<string, string> = {};
  const declaration = /(--[a-z0-9-]+)\s*:\s*([^;]+);/gi;
  let match: RegExpExecArray | null;
  while ((match = declaration.exec(css)) !== null) {
    const name = match[1];
    const raw = match[2];
    if (name === undefined || raw === undefined) continue;
    const value = raw.replace(/\/\*[\s\S]*?\*\//g, "").trim();
    if (/^#[0-9a-f]{6}$/i.test(value)) tokens[name] = value.toLowerCase();
  }
  return tokens;
}

/** Comments are prose, not paint: a documented hex must not become a token. */
export function stripComments(css: string): string {
  return css.replace(/\/\*[\s\S]*?\*\//g, "");
}

/**
 * The declarations inside the block opened by `selector`, matched by brace
 * counting so a nested at-rule (none today) cannot truncate the block early.
 */
export function blockBody(css: string, selector: string): string {
  const clean = stripComments(css);
  const open = clean.indexOf(selector);
  if (open === -1) throw new Error(`no ${selector} block in tokens.css`);
  const start = clean.indexOf("{", open + selector.length);
  if (start === -1) throw new Error(`no opening brace after ${selector}`);
  let depth = 0;
  for (let index = start; index < clean.length; index += 1) {
    const character = clean[index];
    if (character === "{") depth += 1;
    else if (character === "}") {
      depth -= 1;
      if (depth === 0) return clean.slice(start + 1, index);
    }
  }
  throw new Error(`unterminated ${selector} block in tokens.css`);
}

/** Every `--name: value;` in a block, values trimmed and unresolved. */
export function parseDeclarations(body: string): Record<string, string> {
  const declared: Record<string, string> = {};
  const declaration = /(--[a-z0-9-]+)\s*:\s*([^;]+);/gi;
  let match: RegExpExecArray | null;
  while ((match = declaration.exec(body)) !== null) {
    const name = match[1];
    const raw = match[2];
    if (name === undefined || raw === undefined) continue;
    declared[name] = raw.trim();
  }
  return declared;
}

const HEX = /^#[0-9a-f]{6}$/i;
const ALIAS = /^var\((--[a-z0-9-]+)\)$/i;

/**
 * Resolve a declaration to its literal value **using only names declared in the
 * same block**. `var(--x)` that is not restated in this scope throws: that is
 * the cross-scope alias the light block is not allowed to use.
 */
export function resolveInScope(
  declared: Record<string, string>,
  name: string,
  seen: ReadonlySet<string> = new Set<string>(),
): string {
  const value = declared[name];
  if (value === undefined) throw new Error(`${name} is not declared in this scope`);
  const alias = ALIAS.exec(value);
  if (alias === null) return value;
  const target = alias[1];
  if (target === undefined) throw new Error(`${name} has an unreadable var() reference`);
  if (seen.has(name)) throw new Error(`${name} resolves through itself`);
  const next = new Set(seen);
  next.add(name);
  return resolveInScope(declared, target, next);
}

export interface Scope {
  selector: string;
  /** Declarations as written, `var()` chains included. */
  declared: Record<string, string>;
  /** Names that resolve, inside this block, to an opaque #rrggbb. */
  colours: Record<string, string>;
  /** `name: reason` for a declaration that does not resolve to a hex here. */
  unresolved: string[];
}

/**
 * One block of `tokens.css`, resolved in isolation. Density tokens (`28px`)
 * are declared once in `:root` and are not part of the colour set, so they are
 * skipped rather than reported; anything else that fails to resolve is kept in
 * `unresolved` so the test can name it instead of dying inside the parser.
 */
export function parseScope(css: string, selector: string): Scope {
  const declared = parseDeclarations(blockBody(css, selector));
  const colours: Record<string, string> = {};
  const unresolved: string[] = [];
  for (const name of Object.keys(declared)) {
    let value: string;
    try {
      value = resolveInScope(declared, name);
    } catch (error) {
      unresolved.push(`${name}: ${(error as Error).message}`);
      continue;
    }
    if (HEX.test(value)) colours[name] = value.toLowerCase();
    else if (ALIAS.test(value)) unresolved.push(`${name}: alias left unresolved`);
  }
  return { selector, declared, colours, unresolved };
}

/**
 * The scope-parity gate: the light block must declare exactly the names the
 * dark block declares as colours — no missing token (a light surface that
 * silently keeps `#181215`) and no extra one (a typo like `--accent-txt` that
 * would otherwise sit there, unused and unmeasured).
 */
export function assertScopeParity(css: string): { dark: string[]; light: string[] } {
  const dark = parseScope(css, DARK_SCOPE);
  const light = parseScope(css, LIGHT_SCOPE);
  const darkColours = Object.keys(dark.colours).sort();
  const lightNames = Object.keys(light.declared).sort();
  const missing = darkColours.filter((name) => !lightNames.includes(name));
  const extra = lightNames.filter((name) => !darkColours.includes(name));
  if (missing.length > 0 || extra.length > 0) {
    throw new Error(
      `light scope is not a full restatement of dark: missing [${missing.join(", ")}]; ` +
        `declared only in light [${extra.join(", ")}]`,
    );
  }
  return { dark: darkColours, light: lightNames };
}

function channel(value: number): number {
  const srgb = value / 255;
  return srgb <= 0.04045 ? srgb / 12.92 : ((srgb + 0.055) / 1.055) ** 2.4;
}

/** WCAG relative luminance of an opaque #rrggbb colour. */
export function luminance(hex: string): number {
  const value = hex.replace("#", "");
  const r = Number.parseInt(value.slice(0, 2), 16);
  const g = Number.parseInt(value.slice(2, 4), 16);
  const b = Number.parseInt(value.slice(4, 6), 16);
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}

/** WCAG 2.1 contrast ratio: (L1 + 0.05) / (L2 + 0.05), lighter first. */
export function contrastRatio(a: string, b: string): number {
  const first = luminance(a);
  const second = luminance(b);
  const lighter = Math.max(first, second);
  const darker = Math.min(first, second);
  return (lighter + 0.05) / (darker + 0.05);
}

/**
 * Hue in the HSL/HSV sense — the number CSS `hsl()`, every design tool and
 * `hotpink` itself report (330°). OKLCH would put the same rose at ~353°: the
 * two spaces disagree by a fifth of a turn in this region, so the contract
 * below pins one and says which.
 */
export function hueOf(hex: string): number {
  const value = hex.replace("#", "");
  const r = Number.parseInt(value.slice(0, 2), 16) / 255;
  const g = Number.parseInt(value.slice(2, 4), 16) / 255;
  const b = Number.parseInt(value.slice(4, 6), 16) / 255;
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const span = max - min;
  if (span === 0) return 0;
  const raw = max === r ? ((g - b) / span) % 6 : max === g ? (b - r) / span + 2 : (r - g) / span + 4;
  return (raw * 60 + 360) % 360;
}

/** Shortest arc between two hues, so 350° and 10° are 20° apart, not 340°. */
export function hueDistance(a: number, b: number): number {
  const delta = Math.abs(a - b) % 360;
  return Math.min(delta, 360 - delta);
}

/** Largest channel spread in 0–255. A plane above ~12 is a colour, not a tint. */
export function channelSpread(hex: string): number {
  const value = hex.replace("#", "");
  const channels = [0, 2, 4].map((offset) => Number.parseInt(value.slice(offset, offset + 2), 16));
  return Math.max(...channels) - Math.min(...channels);
}

interface Pair {
  /** Token the text is painted with. */
  fg: string;
  /** Token of the surface it sits on. */
  bg: string;
  sizePx: number;
  weight: number;
}

/**
 * Every pair the shell or the grid actually paints. Row backgrounds only:
 * `--row-bg` (even), `--row-bg-alt` (zebra), `--row-hover` and
 * `--row-selected` are the four surfaces a data cell can land on, and a token
 * that passes on one and fails on the zebra is a bug that only appears on
 * every second row. The accent pairs are chrome, not data: a link on every
 * surface it can land on, the ink on a filled control, and the ink on a
 * destructive one.
 */
const PAIRS: readonly Pair[] = [
  { fg: "--up", bg: "--row-bg", sizePx: 12, weight: 500 },
  { fg: "--up", bg: "--row-bg-alt", sizePx: 12, weight: 500 },
  { fg: "--up", bg: "--row-hover", sizePx: 12, weight: 500 },
  { fg: "--down", bg: "--row-bg", sizePx: 12, weight: 500 },
  { fg: "--down", bg: "--row-bg-alt", sizePx: 12, weight: 500 },
  { fg: "--down", bg: "--row-hover", sizePx: 12, weight: 500 },
  { fg: "--flat", bg: "--row-bg", sizePx: 12, weight: 400 },
  { fg: "--flat", bg: "--row-bg-alt", sizePx: 12, weight: 400 },
  { fg: "--flat", bg: "--row-hover", sizePx: 12, weight: 400 },
  { fg: "--alert", bg: "--row-bg", sizePx: 12, weight: 500 },
  { fg: "--alert", bg: "--row-bg-alt", sizePx: 12, weight: 500 },
  { fg: "--alert", bg: "--row-hover", sizePx: 12, weight: 500 },
  { fg: "--fg", bg: "--row-bg", sizePx: 12, weight: 400 },
  { fg: "--fg", bg: "--row-bg-alt", sizePx: 12, weight: 400 },
  { fg: "--fg-muted", bg: "--row-bg", sizePx: 12, weight: 400 },
  { fg: "--fg-muted", bg: "--row-bg-alt", sizePx: 12, weight: 400 },
  { fg: "--fg-subtle", bg: "--row-bg", sizePx: 10, weight: 400 },
  { fg: "--fg-subtle", bg: "--row-bg-alt", sizePx: 10, weight: 400 },

  /* A selected row still prints its numbers, so every tone it can carry is
   * measured on --row-selected too — the plane furthest from the page, and so
   * the worst case for a text colour in either direction. */
  { fg: "--up", bg: "--row-selected", sizePx: 12, weight: 500 },
  { fg: "--down", bg: "--row-selected", sizePx: 12, weight: 500 },
  { fg: "--flat", bg: "--row-selected", sizePx: 12, weight: 400 },
  { fg: "--alert", bg: "--row-selected", sizePx: 12, weight: 500 },
  { fg: "--fg", bg: "--row-selected", sizePx: 12, weight: 400 },
  { fg: "--fg-muted", bg: "--row-selected", sizePx: 12, weight: 400 },
  { fg: "--fg-subtle", bg: "--row-selected", sizePx: 10, weight: 400 },

  /* Chrome: a link is text, so 4.5:1 on every plane it can be printed on —
   * including --surface-raised, the hovered-row and menu plane, not only the
   * panel it was designed on. */
  { fg: "--accent-text", bg: "--bg", sizePx: 13, weight: 400 },
  { fg: "--accent-text", bg: "--surface", sizePx: 13, weight: 400 },
  { fg: "--accent-text", bg: "--surface-raised", sizePx: 13, weight: 400 },
  { fg: "--accent-text", bg: "--surface-sunken", sizePx: 13, weight: 400 },
  { fg: "--accent-text", bg: "--row-bg", sizePx: 13, weight: 400 },
  { fg: "--accent-text", bg: "--row-bg-alt", sizePx: 13, weight: 400 },
  { fg: "--accent-text", bg: "--row-hover", sizePx: 13, weight: 400 },
  { fg: "--accent", bg: "--surface-sunken", sizePx: 13, weight: 400 },
  { fg: "--brand", bg: "--surface-sunken", sizePx: 10, weight: 400 },

  /* Ink on a fill. 12–13 px, so the button label is normal text too. */
  { fg: "--accent-fg", bg: "--accent-strong", sizePx: 13, weight: 500 },
  { fg: "--destructive-foreground", bg: "--destructive", sizePx: 13, weight: 500 },
];

/**
 * Non-text boundaries: the one part of the palette that carries information
 * without text. Every pair is a control's own edge or focus indicator measured
 * against the surface it is drawn on, so 3:1 (§1.4.11) is the floor — a 1 px
 * border is not text, and measuring it at 4.5:1 would be a different rule.
 *
 * The list is the surfaces where a control actually sits in this shell:
 *   --surface-sunken  the search box (`SymbolSearch`), inset wells
 *   --surface         panels and the top bar
 *   --surface-raised  menus and popovers
 *   --row-bg / -alt   a control inside a grid row (filters land here in Ph. 3)
 *   --bg              the page plane
 *   --row-hover       the plane furthest from the page, so the worst case for a
 *                     control edge in either direction
 *
 * `--row-selected` is deliberately absent: it is a data-row fill and the active
 * tab's underline, never a background a control edge is drawn on. If that ever
 * changes, add the pair — `--border-control` measures 2.74:1 there in dark, so
 * the token would have to move rather than the table.
 */
interface Boundary {
  /** Token of the edge. */
  fg: string;
  /** Token of the surface behind it. */
  bg: string;
}

const BOUNDARIES: readonly Boundary[] = [
  { fg: "--border-control", bg: "--surface-sunken" },
  { fg: "--border-control", bg: "--surface" },
  { fg: "--border-control", bg: "--surface-raised" },
  { fg: "--border-control", bg: "--row-bg" },
  { fg: "--border-control", bg: "--row-bg-alt" },
  { fg: "--border-control", bg: "--row-hover" },
  { fg: "--border-control", bg: "--bg" },
  { fg: "--focus", bg: "--surface-sunken" },
  { fg: "--focus", bg: "--surface" },
  { fg: "--focus", bg: "--surface-raised" },
  { fg: "--focus", bg: "--row-bg" },
  { fg: "--focus", bg: "--row-bg-alt" },
  { fg: "--focus", bg: "--row-hover" },
  /* The active tab's 2 px edge and a chip's border are the accent carrying
   * state without text, so they are boundaries at 3:1, on every plane the
   * strip or a chip can sit on. */
  { fg: "--accent", bg: "--bg" },
  { fg: "--accent", bg: "--surface" },
  { fg: "--accent", bg: "--surface-raised" },
  { fg: "--accent", bg: "--surface-sunken" },
  { fg: "--accent", bg: "--row-bg" },
  { fg: "--accent", bg: "--row-bg-alt" },
  { fg: "--accent", bg: "--row-hover" },
  /* A filled control's own area is what identifies it: no border, so the fill
   * must clear 3:1 against the planes it is dropped on. */
  { fg: "--accent-strong", bg: "--surface" },
  { fg: "--accent-strong", bg: "--row-hover" },
];

export interface Measurement {
  fg: string;
  bg: string;
  ratio: number;
  threshold: number;
  large: boolean;
}

export function measure(tokens: Record<string, string>, pairs: readonly Pair[]): Measurement[] {
  return pairs.map((pair) => {
    const fg = tokens[pair.fg];
    const bg = tokens[pair.bg];
    if (fg === undefined) throw new Error(`token ${pair.fg} is missing or not a hex colour`);
    if (bg === undefined) throw new Error(`token ${pair.bg} is missing or not a hex colour`);
    const large = isLargeText(pair.sizePx, pair.weight);
    return {
      fg: pair.fg,
      bg: pair.bg,
      ratio: contrastRatio(fg, bg),
      threshold: large ? AA_LARGE : AA_NORMAL,
      large,
    };
  });
}

/** Throw with every offending pair named, not just the first. */
export function assertContrast(tokens: Record<string, string>): Measurement[] {
  return assertEvery(measure(tokens, PAIRS), "WCAG AA contrast failure");
}

export interface BoundaryMeasurement {
  fg: string;
  bg: string;
  ratio: number;
  threshold: number;
}

export function measureBoundaries(
  tokens: Record<string, string>,
  boundaries: readonly Boundary[] = BOUNDARIES,
): BoundaryMeasurement[] {
  return boundaries.map((boundary) => {
    const fg = tokens[boundary.fg];
    const bg = tokens[boundary.bg];
    if (fg === undefined) throw new Error(`token ${boundary.fg} is missing or not a hex colour`);
    if (bg === undefined) throw new Error(`token ${boundary.bg} is missing or not a hex colour`);
    return {
      fg: boundary.fg,
      bg: boundary.bg,
      ratio: contrastRatio(fg, bg),
      threshold: AA_NON_TEXT,
    };
  });
}

/** Throw naming every boundary that fails, not just the first. */
export function assertBoundaries(tokens: Record<string, string>): BoundaryMeasurement[] {
  return assertEvery(measureBoundaries(tokens), "WCAG 1.4.11 non-text contrast failure");
}

/**
 * Shared failure formatting for both tables: a gate that stops at the first
 * bad pair hides the second one, and the operator fixes one colour per run.
 */
function assertEvery<T extends { fg: string; bg: string; ratio: number; threshold: number }>(
  measurements: readonly T[],
  label: string,
): T[] {
  const failures = measurements.filter((entry) => entry.ratio < entry.threshold);
  if (failures.length > 0) {
    const detail = failures
      .map(
        (entry) =>
          `${entry.fg} on ${entry.bg} = ${entry.ratio.toFixed(2)}:1 < ${entry.threshold.toFixed(1)}:1`,
      )
      .join("; ");
    throw new Error(`${label}: ${detail}`);
  }
  return [...measurements];
}

/**
 * Printed on success too: a gate that only speaks when it fails gives the
 * operator no evidence that it ran, and the report is the receipt.
 */
function printTable(label: string, rows: readonly string[]): void {
  console.log(`\n${label} — ${rows.length} pairs\n${rows.join("\n")}\n`);
}

function tableRows(
  measurements: readonly { fg: string; bg: string; ratio: number; threshold: number }[],
): string[] {
  return measurements.map(
    (entry) =>
      `${entry.fg.padEnd(24)} on ${entry.bg.padEnd(18)} ${entry.ratio.toFixed(2)}:1 (min ${entry.threshold})`,
  );
}

/** Parsed once, lazily: a missing light block fails one named test, not the import. */
let parsed: { dark: Scope; light: Scope } | null = null;

function scopes(): { dark: Scope; light: Scope } {
  if (parsed === null) {
    parsed = { dark: parseScope(CSS, DARK_SCOPE), light: parseScope(CSS, LIGHT_SCOPE) };
  }
  return parsed;
}

/** The two scopes as test subjects, dark first. */
const SUBJECTS = [
  { name: "dark", selector: DARK_SCOPE },
  { name: "light", selector: LIGHT_SCOPE },
] as const;

function coloursOf(selector: string): Record<string, string> {
  return selector === DARK_SCOPE ? scopes().dark.colours : scopes().light.colours;
}

/**
 * Minimum |ΔL| between the chrome rose and the datum red, per scope.
 *
 * On dark both are light inks on a near-black plane, so the rose must also be
 * the *lighter* of the two: the 0.10 gap is what keeps the pair apart after a
 * deuteranope collapses the hue axis.
 *
 * On light both are dark inks on a near-white plane, and the 4.5:1 text floor
 * caps them: on the darkest row plane (`--row-selected`, L≈0.80) a text colour
 * may not exceed L≈0.136, and on `--row-hover` L≈0.149. Two inks that still
 * read as a rose and a red land within ~0.02 of each other, so the 0.10 gap the
 * dark scope can afford is arithmetically impossible here. The light scope
 * therefore pins a 0.015 floor — enough to catch a collapse to one value — and
 * leans on the 25° hue separation, which is the channel that survives at 12 px
 * on both planes.
 */
const MIN_LIGHTNESS_GAP: Readonly<Record<"dark" | "light", number>> = { dark: 0.1, light: 0.015 };

describe("the token scopes", () => {
  it("declares a light scope alongside the dark one", () => {
    const { dark, light } = scopes();
    expect(light.selector).toBe(LIGHT_SCOPE);
    // A palette this size is not a guess: if the block were empty the parity
    // test below would pass vacuously, so the floor is asserted here.
    expect(Object.keys(dark.colours).length).toBeGreaterThanOrEqual(40);
    expect(Object.keys(light.colours).length).toBeGreaterThanOrEqual(40);
  });

  it("restates every dark colour token in the light block, and nothing else", () => {
    const { dark, light } = assertScopeParity(CSS);
    expect(light).toEqual(dark);
  });

  it("resolves every light value inside the light block", () => {
    const { light } = scopes();
    // An unresolved `var(--x)` is a value borrowed from the dark block: the
    // light theme would then be half-dark the day a dark value moves.
    expect(light.unresolved).toEqual([]);
    expect(Object.keys(light.colours).sort()).toEqual(Object.keys(light.declared).sort());
  });

  it("declares every light colour as an opaque hex", () => {
    const { light } = scopes();
    for (const [name, value] of Object.entries(light.colours)) {
      expect(value, `${name} must resolve to #rrggbb, not ${value}`).toMatch(/^#[0-9a-f]{6}$/);
    }
  });

  it("keeps the shadcn aliases pointing at the same values in both scopes", () => {
    const { dark, light } = scopes();
    for (const [name, raw] of Object.entries(dark.declared)) {
      const alias = /^var\((--[a-z0-9-]+)\)$/.exec(raw);
      if (alias === null) continue;
      const target = alias[1];
      if (target === undefined) continue;
      // Dark says `--background: var(--bg)`; light restates it as a hex, so the
      // invariant has to be asserted rather than inherited from the cascade.
      expect(light.colours[name], `${name} must equal ${target} in light`).toBe(
        light.colours[target],
      );
    }
  });
});

for (const subject of SUBJECTS) {
  const tokens = (): Record<string, string> => coloursOf(subject.selector);

  describe(`${subject.name} scope`, () => {
    it("declares the four finance tokens as opaque hex values", () => {
      for (const name of ["--up", "--down", "--flat", "--alert"]) {
        expect(tokens()[name], `${name} must be a #rrggbb hex in the ${subject.name} scope`).toMatch(
          /^#[0-9a-f]{6}$/,
        );
      }
    });

    it("declares the row surfaces the pairs are measured against", () => {
      for (const name of ["--row-bg", "--row-bg-alt", "--row-hover"]) {
        expect(tokens()[name], `${name} is required in the ${subject.name} scope`).toMatch(
          /^#[0-9a-f]{6}$/,
        );
      }
    });

    it("meets AA on every pair the shell and the grid paint", () => {
      const measurements = assertContrast(tokens());
      printTable(`WCAG AA (${subject.name})`, tableRows(measurements));
    });

    it("meets 3:1 on every control boundary the shell draws", () => {
      const measurements = assertBoundaries(tokens());
      printTable(`WCAG 1.4.11 (${subject.name})`, tableRows(measurements));
    });

    it("classifies none of the data pairs as large text", () => {
      const large = measure(tokens(), PAIRS).filter((entry) => entry.large);
      // If one of these ever becomes "large", someone changed a font size to
      // escape 4.5:1 and the exception must be reviewed, not assumed.
      expect(large).toEqual([]);
    });

    it("keeps the four semantics distinguishable from each other", () => {
      const values = ["--up", "--down", "--flat", "--alert"].map((name) => tokens()[name]);
      expect(new Set(values).size).toBe(4);
    });
  });
}

/**
 * The palette's *shape*, not its ratios — asserted per scope, because a light
 * theme that collapses the chrome rose into the datum red is the failure the
 * ramp exists to prevent, and it cannot be caught by contrast alone: both
 * colours would still be legible.
 */
for (const subject of SUBJECTS) {
  const tokens = (): Record<string, string> => coloursOf(subject.selector);

  describe(`${subject.name} rose contract`, () => {
    const token = (name: string): string => {
      const value = tokens()[name];
      if (value === undefined) throw new Error(`token ${name} is missing or not a hex colour`);
      return value;
    };

    it("keeps every weight of the accent in the pink band", () => {
      for (const name of ["--accent", "--accent-strong", "--accent-text", "--brand", "--focus"]) {
        const hue = hueOf(token(name));
        expect(
          hue,
          `${name} is ${hue.toFixed(1)}° — outside the 320–340° rose band`,
        ).toBeGreaterThanOrEqual(320);
        expect(
          hue,
          `${name} is ${hue.toFixed(1)}° — outside the 320–340° rose band`,
        ).toBeLessThanOrEqual(340);
      }
    });

    it("keeps the negative a red, not a second rose", () => {
      const hue = hueOf(token("--down"));
      expect(hue, `--down is ${hue.toFixed(1)}°, outside the 0–8° red band`).toBeGreaterThanOrEqual(
        0,
      );
      expect(hue, `--down is ${hue.toFixed(1)}°, outside the 0–8° red band`).toBeLessThanOrEqual(8);
    });

    it("separates the accent from the negative by hue and by lightness", () => {
      const accent = token("--accent");
      const down = token("--down");
      expect(accent).not.toBe(down);
      expect(hueDistance(hueOf(accent), hueOf(down))).toBeGreaterThanOrEqual(25);
      const gap = Math.abs(luminance(accent) - luminance(down));
      expect(
        gap,
        `${subject.name}: accent L=${luminance(accent).toFixed(3)} and down ` +
          `L=${luminance(down).toFixed(3)} are ${gap.toFixed(3)} apart`,
      ).toBeGreaterThanOrEqual(MIN_LIGHTNESS_GAP[subject.name]);
      if (subject.name === "dark") {
        // Hue is the channel a deuteranope loses, so on dark the rose must also
        // be the lighter of the two: the pair has to survive as a value
        // difference. On light both are dark inks and the ordering carries no
        // information — the hue arc and the sign/arrow do the work.
        expect(luminance(accent)).toBeGreaterThan(luminance(down));
      }
    });

    it("keeps every plane the table paints a tint, not a colour", () => {
      // A 100-row table is read at 12 px; a plane whose channels differ by more
      // than 12/255 starts competing with the data ink printed on it.
      for (const name of [
        "--bg",
        "--surface",
        "--surface-raised",
        "--surface-sunken",
        "--row-bg",
        "--row-bg-alt",
        "--row-hover",
      ]) {
        expect(
          channelSpread(token(name)),
          `${name} is too chromatic to be a plane`,
        ).toBeLessThanOrEqual(12);
      }
    });

    it("lets the selected row carry state, but not a poster", () => {
      expect(channelSpread(token("--row-selected"))).toBeGreaterThan(
        channelSpread(token("--row-bg")),
      );
      expect(channelSpread(token("--row-selected"))).toBeLessThanOrEqual(24);
    });

    it("leaves --flat achromatic so 'unchanged' owns no hue", () => {
      expect(channelSpread(token("--flat"))).toBeLessThanOrEqual(8);
    });
  });
}

describe("the gate itself", () => {
  /** The dark set, which is what the synthetic overrides below are applied to. */
  const darkTokens = (): Record<string, string> => scopes().dark.colours;

  it("treats 18.66 px bold as large and 18 px bold as normal", () => {
    expect(isLargeText(18.66, 700)).toBe(true);
    expect(isLargeText(24, 400)).toBe(true);
    expect(isLargeText(18, 700)).toBe(false);
    expect(isLargeText(23, 400)).toBe(false);
  });

  it("computes known WCAG ratios", () => {
    // Reference values: identical colours are 1:1, black on white is 21:1.
    expect(contrastRatio("#181215", "#181215")).toBeCloseTo(1, 5);
    expect(contrastRatio("#ffffff", "#000000")).toBeCloseTo(21, 5);
    // A mid grey on white, cross-checked by hand: 0.46 luminance-ish pair.
    expect(contrastRatio("#767676", "#ffffff")).toBeCloseTo(4.54, 1);
  });

  it("fails when --up is darkened below 4.5:1", () => {
    const broken = { ...darkTokens(), "--up": "#0b5c3a" };
    expect(() => assertContrast(broken)).toThrow(/--up on --row-bg/);
  });

  it("fails at 3.89:1 even though 3.0:1 would pass for large text", () => {
    // #66748a measures 3.89:1 on --row-bg; normal text must reject it.
    const broken = { ...darkTokens(), "--down": "#66748a" };
    expect(() => assertContrast(broken)).toThrow(/--down on --row-bg/);
  });

  it("fails when a pair is measured against a background that was lightened", () => {
    const broken = { ...darkTokens(), "--row-bg": "#e8e8e8" };
    expect(() => assertContrast(broken)).toThrow(/contrast failure/);
  });

  it("rejects the #4a5b75 control edge the file shipped before F3", () => {
    // The exact regression F3 found: the old token passed every text test it
    // was never in, because only text pairs were measured. It is still below
    // 3:1 against today's --surface-sunken, and the message names the pair.
    const broken = { ...darkTokens(), "--border-control": "#4a5b75" };
    expect(() => assertBoundaries(broken)).toThrow(/--border-control on --surface-sunken/);
    expect(() => assertBoundaries(broken)).toThrow(/2\.\d{2}:1 < 3\.0:1/);
  });

  it("applies the non-text threshold, not the text one, to boundaries", () => {
    const [first] = measureBoundaries(darkTokens());
    expect(first?.threshold).toBe(3.0);
    // The shipped boundary clears 3:1 but would not clear 4.5:1 — proof that
    // the two tables are not measuring the same rule by accident.
    const ratio = first?.ratio ?? 0;
    expect(ratio).toBeGreaterThanOrEqual(3);
    expect(ratio).toBeLessThan(4.5);
  });

  it("names every failing boundary in one message", () => {
    const broken = { ...darkTokens(), "--border-control": "#2a3446", "--focus": "#2a3446" };
    let message = "";
    try {
      assertBoundaries(broken);
    } catch (error) {
      message = (error as Error).message;
    }
    expect(message).toMatch(/--border-control on --surface-sunken/);
    expect(message).toMatch(/--focus on --surface-sunken/);
  });

  it("names a light token that was typo'd, in both directions", () => {
    // The adversarial case: `--accent-txt` is a real hex, so every ratio it is
    // not part of still passes; only the key-set comparison sees it. The two
    // blocks here are the smallest shape that exercises the parser.
    const broken = [
      ":root { --bg: #100c0e; --accent-text: #ffa8cf; }",
      '[data-theme="light"] { --bg: #fbf7f8; --accent-txt: #a3144c; }',
    ].join("\n");
    expect(() => assertScopeParity(broken)).toThrow(/missing \[--accent-text\]/);
    expect(() => assertScopeParity(broken)).toThrow(/declared only in light \[--accent-txt\]/);
  });

  it("rejects a light token that resolves through the dark block", () => {
    // `var(--bg)` is legal inside `:root`; in the light block it would silently
    // borrow the dark plane, which is exactly the gap this test exists for.
    const broken = [
      ":root { --bg: #100c0e; --fg: #efe9ec; }",
      '[data-theme="light"] { --bg: var(--surface); --fg: #1a1215; }',
    ].join("\n");
    const light = parseScope(broken, '[data-theme="light"]');
    expect(light.unresolved).toEqual(["--bg: --surface is not declared in this scope"]);
  });
});
