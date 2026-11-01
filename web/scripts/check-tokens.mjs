#!/usr/bin/env node
/**
 * Token-colour gate: the palette is a closed set, and this proves it.
 *
 * `src/styles/index.css` deletes Tailwind's default colour namespace
 * (`--color-*: initial`), so `text-lime-700` no longer compiles to
 * oklch(53.2% .157 131.589). That alone is not a gate: Tailwind drops an
 * unknown utility *silently*, so a component could reference a colour the
 * token system never vetted and the build would stay green. This script makes
 * that reference loud in two places:
 *
 *   1. source — any class in `src/` that names one of Tailwind's default
 *      palette colours (or an arbitrary `[#rrggbb]` in a colour slot) fails;
 *   2. artefact — every colour literal in the emitted `dist/assets/*.css`
 *      must resolve to a value declared in `tokens.css`, or to a structural
 *      neutral, or to an explicit allowlist entry (chart ramps, later).
 *
 * The artefact half is what catches a colour that arrives by another route:
 * a vendored file, an inline style, a future `@theme` addition. The source
 * half is what catches the silent drop.
 *
 * Usage:  node scripts/check-tokens.mjs [--dist <dir>] [--self-test]
 * Exit:   0 clean, 1 a colour escaped the tokens, 2 inputs unreadable.
 */
import { readFileSync, readdirSync, statSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, "..");
const TOKENS_CSS = path.join(ROOT, "src/styles/tokens.css");
const THEME_CSS = path.join(ROOT, "src/styles/index.css");
const SRC = path.join(ROOT, "src");

const distFlag = process.argv.indexOf("--dist");
const DIST =
  distFlag > -1 && process.argv[distFlag + 1]
    ? path.resolve(process.argv[distFlag + 1])
    : path.join(ROOT, "dist");
const SELF_TEST = process.argv.includes("--self-test");

/** Tailwind v4's default palette. Any of these in a colour slot is a leak. */
const PALETTE = [
  "slate", "gray", "zinc", "neutral", "stone",
  "red", "orange", "amber", "yellow", "lime", "green", "emerald", "teal",
  "cyan", "sky", "blue", "indigo", "violet", "purple", "fuchsia", "pink", "rose",
  "black", "white",
];

/** Utility prefixes that take a colour as their last semantic segment. */
const PREFIXES = [
  "bg", "text", "border", "border-t", "border-r", "border-b", "border-l",
  "border-x", "border-y", "ring", "ring-offset", "outline", "fill", "stroke",
  "from", "via", "to", "divide", "placeholder", "decoration", "accent",
  "caret", "shadow", "inset-shadow", "inset-ring",
];

/**
 * Structural neutrals: `--scrim` (#000) backs the dialog/sheet overlay and
 * every Tailwind shadow default, `--on-destructive` (#fff) is the ink on a
 * destructive button and Tailwind's ring-offset default. They are tokens in
 * `tokens.css`, listed here by value so the report can label the alphabet soup
 * Tailwind emits for them.
 */
const STRUCTURAL = new Map([
  ["#000000", "structural: --scrim (overlay, shadow base)"],
  ["#ffffff", "structural: --on-destructive (ink on destructive)"],
]);

/**
 * Explicit allowlist for colours that must exist outside the UI ramp — chart
 * series ramps in Phase 2/6, where a categorical scale is data, not chrome.
 * Every entry needs a label so the report says why it is there.
 */
const ALLOWLIST = new Map([
  // ["#ff00aa", "chart ramp: series 1 (Phase 2)"],
]);

const NON_COLOUR_KEYWORDS = new Set([
  "transparent", "currentcolor", "inherit", "initial", "unset", "none",
  "revert", "revert-layer", "auto",
]);

/** CSS named colours, so a bare `red` in a value is caught too. */
const NAMED_COLOURS = [
  "aliceblue", "antiquewhite", "aqua", "aquamarine", "azure", "beige", "bisque",
  "black", "blanchedalmond", "blue", "blueviolet", "brown", "burlywood",
  "cadetblue", "chartreuse", "chocolate", "coral", "cornflowerblue", "cornsilk",
  "crimson", "cyan", "darkblue", "darkcyan", "darkgoldenrod", "darkgray",
  "darkgreen", "darkgrey", "darkkhaki", "darkmagenta", "darkolivegreen",
  "darkorange", "darkorchid", "darkred", "darksalmon", "darkseagreen",
  "darkslateblue", "darkslategray", "darkslategrey", "darkturquoise",
  "darkviolet", "deeppink", "deepskyblue", "dimgray", "dimgrey", "dodgerblue",
  "firebrick", "floralwhite", "forestgreen", "fuchsia", "gainsboro",
  "ghostwhite", "gold", "goldenrod", "gray", "green", "greenyellow", "grey",
  "honeydew", "hotpink", "indianred", "indigo", "ivory", "khaki", "lavender",
  "lavenderblush", "lawngreen", "lemonchiffon", "lightblue", "lightcoral",
  "lightcyan", "lightgoldenrodyellow", "lightgray", "lightgreen", "lightgrey",
  "lightpink", "lightsalmon", "lightseagreen", "lightskyblue", "lightslategray",
  "lightslategrey", "lightsteelblue", "lightyellow", "lime", "limegreen",
  "linen", "magenta", "maroon", "mediumaquamarine", "mediumblue",
  "mediumorchid", "mediumpurple", "mediumseagreen", "mediumslateblue",
  "mediumspringgreen", "mediumturquoise", "mediumvioletred", "midnightblue",
  "mintcream", "mistyrose", "moccasin", "navajowhite", "navy", "oldlace",
  "olive", "olivedrab", "orange", "orangered", "orchid", "palegoldenrod",
  "palegreen", "paleturquoise", "palevioletred", "papayawhip", "peachpuff",
  "peru", "pink", "plum", "powderblue", "purple", "rebeccapurple", "red",
  "rosybrown", "royalblue", "saddlebrown", "salmon", "sandybrown", "seagreen",
  "seashell", "sienna", "silver", "skyblue", "slateblue", "slategray",
  "slategrey", "snow", "springgreen", "steelblue", "tan", "teal", "thistle",
  "tomato", "turquoise", "violet", "wheat", "white", "whitesmoke", "yellow",
  "yellowgreen",
];

/**
 * Parse `--name: #rrggbb` declarations; `var()` aliases are resolved later.
 *
 * A name is declared once per theme *scope* — `:root` (dark) and
 * `[data-theme="light"]` (day) — so the file holds two values per name. The
 * map is keyed by name for the report, so the second declaration is filed under
 * a scoped key: `provenance` matches by value, and a light value that simply
 * overwrote its dark twin would make every dark literal in the artefact look
 * off-token.
 */
function parseTokens(css) {
  const tokens = new Map();
  for (const match of css.matchAll(/(--[a-z0-9-]+)\s*:\s*([^;]+);/gi)) {
    const name = match[1];
    const raw = match[2].replace(/\/\*[\s\S]*?\*\//g, "").trim();
    if (!name || !/^#[0-9a-f]{6}$/i.test(raw)) continue;
    const value = raw.toLowerCase();
    const existing = tokens.get(name);
    if (existing === undefined) tokens.set(name, value);
    else if (existing !== value) tokens.set(`${name} (other scope)`, value);
  }
  return tokens;
}

function expandHex(hex) {
  let value = hex.toLowerCase().replace("#", "");
  if (value.length === 3 || value.length === 4) {
    value = value
      .split("")
      .map((character) => character + character)
      .join("");
  }
  const base = `#${value.slice(0, 6)}`;
  const alpha = value.length === 8 ? value.slice(6, 8) : "ff";
  return { base, transparent: alpha === "00" };
}

function rgbToHex(r, g, b) {
  return `#${[r, g, b]
    .map((channel) => Math.max(0, Math.min(255, Number(channel))).toString(16).padStart(2, "0"))
    .join("")}`;
}

/**
 * Comments are prose, not paint: a doc block that *names* the colour it is
 * warning about (`text-lime-700`) must not fail the gate it documents.
 */
function stripComments(text) {
  return text
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/(^|[^:])\/\/[^\n]*/g, "$1");
}

/**
 * Class tokens in a source file that name a Tailwind palette colour.
 * Deliberately narrow: only the default palette and only in a colour slot, so
 * `text-sm`, `border-b` or `from-top` cannot be reported as colour leaks.
 */
function scanSource(text, file) {
  const findings = [];
  const prefix = PREFIXES.map((value) => value.replace(/-/g, "\\-")).join("|");
  const palette = PALETTE.join("|");
  const colourSlot = new RegExp(
    `(?<![\\w-])(?:${prefix})-(?:(${palette})(?:-\\d{2,3})?)(?:\\/\\d{1,3})?(?![\\w-])`,
    "g",
  );
  for (const match of text.matchAll(colourSlot)) {
    const name = match[1];
    const token = match[0];
    if (name === "black" || name === "white") {
      findings.push({
        file, token, allowed: true, reason: "structural neutral (declared in index.css)",
      });
      continue;
    }
    const line = text.slice(0, match.index).split("\n").length;
    findings.push({
      file, token, allowed: false, line, reason: `Tailwind palette colour "${name}"`,
    });
  }
  const arbitrary = new RegExp(
    `(?<![\\w-])(?:${PREFIXES.filter((value) => !value.startsWith("shadow")).join("|")})` +
      `-\\[(#[0-9a-f]{3,8}|(?:rgb|hsl|oklch|oklab|lab|lch|color)\\([^\\]]*\\))\\]`,
    "gi",
  );
  for (const match of text.matchAll(arbitrary)) {
    const line = text.slice(0, match.index).split("\n").length;
    findings.push({
      file, token: match[0], allowed: false, line,
      reason: "arbitrary colour value bypasses the token set",
    });
  }
  return findings;
}

/** Every colour literal in a CSS chunk, with the declaration it sits in. */
function scanCss(css, file) {
  // `@supports (color:color-mix(in lab,red,red))` is a feature probe compiled
  // by Tailwind into nearly every file; it paints nothing. Dropping preludes
  // keeps the blocks they guard and removes the probe colours.
  const clean = css.replace(/@supports[^{]*\{/g, "{");
  const findings = [];
  const literals = [];
  const hexRe = /#([0-9a-f]{3,4}|[0-9a-f]{6}|[0-9a-f]{8})\b/gi;
  const rgbRe = /\brgba?\(\s*(\d{1,3})[\s,]+(\d{1,3})[\s,]+(\d{1,3})/gi;
  const functionRe = /\b(?:oklch|oklab|lab|lch|hwb|hsla?)\([^)]*\)/gi;

  const record = (raw, index, base, transparent) => {
    const start = Math.max(clean.lastIndexOf("{", index), clean.lastIndexOf(";", index));
    const declaration = clean
      .slice(start + 1, Math.min(clean.length, index + 60))
      .replace(/\s+/g, " ")
      .trim()
      .slice(0, 80);
    findings.push({
      file,
      raw,
      base,
      transparent,
      declaration,
      token: raw,
      reason: base === null ? "colour function outside the token set" : "not in tokens.css",
    });
  };

  for (const match of clean.matchAll(hexRe)) {
    const { base, transparent } = expandHex(match[0]);
    record(match[0], match.index ?? 0, base, transparent);
    literals.push([match.index ?? 0, match[0].length]);
  }
  for (const match of clean.matchAll(rgbRe)) {
    record(match[0], match.index ?? 0, rgbToHex(match[1], match[2], match[3]), false);
    literals.push([match.index ?? 0, match[0].length]);
  }
  for (const match of clean.matchAll(functionRe)) {
    record(match[0], match.index ?? 0, null, false);
    literals.push([match.index ?? 0, match[0].length]);
  }

  // Bare named colours, outside the literals already recorded.
  const named = new RegExp(`(?<![\\w-])(${NAMED_COLOURS.join("|")})(?![\\w-])`, "gi");
  for (const match of clean.matchAll(named)) {
    const index = match.index ?? 0;
    const inside = literals.some(([start, length]) => index >= start && index < start + length);
    if (inside) continue;
    const keyword = match[1].toLowerCase();
    if (NON_COLOUR_KEYWORDS.has(keyword)) continue;
    record(match[1], index, null, false);
  }
  return findings;
}

function walk(dir, extensions) {
  const files = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) files.push(...walk(full, extensions));
    else if (extensions.some((extension) => entry.name.endsWith(extension))) files.push(full);
  }
  return files;
}

function provenance(base, tokens) {
  if (base === null) return null;
  if (STRUCTURAL.has(base)) return STRUCTURAL.get(base);
  if (ALLOWLIST.has(base)) return ALLOWLIST.get(base);
  const names = [];
  for (const [name, value] of tokens) if (value === base) names.push(name);
  if (names.length > 0) return names.join(" / ");
  return null;
}

function formatFindings(findings, root) {
  const lines = [];
  for (const finding of findings) {
    const location = finding.line ? `${path.relative(root, finding.file)}:${finding.line}` : finding.file;
    lines.push(`  ${location}  ${finding.token}`.padEnd(72) + `  ${finding.reason}`);
    if (finding.declaration) lines.push(`      in: ${finding.declaration}`);
  }
  return lines.join("\n");
}

function selfTest() {
  const leaks = [
    '<div className="text-lime-700" />',
    '<div className="bg-red-500/40" />',
    '<div className="hover:border-slate-700" />',
    '<div className="text-[#ff00aa]" />',
    '<div className="from-emerald-400 to-sky-600" />',
  ];
  const clean = [
    '<div className="text-fg border-line-control bg-surface-sunken" />',
    '<div className="text-sm border-b from-top shadow-lg" />',
    '<div className="bg-black/50 text-white" />',
  ];
  let ok = true;
  for (const sample of leaks) {
    const found = scanSource(sample, "<self-test>").filter((entry) => !entry.allowed);
    if (found.length === 0) {
      console.error(`self-test: scanner missed an off-token class: ${sample}`);
      ok = false;
    }
  }
  for (const sample of clean) {
    const found = scanSource(sample, "<self-test>").filter((entry) => !entry.allowed);
    if (found.length > 0) {
      console.error(`self-test: scanner flagged a legitimate class: ${sample} (${found[0].token})`);
      ok = false;
    }
  }
  const cssLeak = scanCss(".x{color:oklch(53.2% .157 131.589)}", "<self-test>");
  if (cssLeak.length === 0) {
    console.error("self-test: CSS scanner missed an oklch literal");
    ok = false;
  }
  const cssClean = scanCss(
    "@supports (color:color-mix(in lab,red,red)){.x{color:#5f7189;box-shadow:0 0 #0000}}",
    "<self-test>",
  ).filter((finding) => !finding.transparent);
  if (cssClean.length !== 1 || cssClean[0]?.base !== "#5f7189") {
    console.error(`self-test: CSS scanner expected only #5f7189, saw ${cssClean.length}`);
    ok = false;
  }
  // Two scopes, one name: the parser must keep both values or every dark
  // literal in the artefact resolves to "not in tokens.css".
  const twoScopes = parseTokens(':root{--bg:#100c0e;}[data-theme="light"]{--bg:#fbf7f8;}');
  if (twoScopes.size !== 2) {
    console.error(`self-test: parser kept ${twoScopes.size} of 2 scoped values for --bg`);
    ok = false;
  }
  if (!ok) process.exit(1);
  console.log("self-test: scanner catches every off-token sample and flags no legitimate class\n");
}

if (SELF_TEST) selfTest();

const tokensCss = readFileSync(TOKENS_CSS, "utf8");
const themeCss = readFileSync(THEME_CSS, "utf8");
const tokens = parseTokens(tokensCss);

// The @theme block is the only place a colour utility can get a name.
const allowedNames = new Set(
  [...themeCss.matchAll(/--color-([a-z0-9-]+)\s*:/gi)].map((match) => match[1]),
);

/**
 * The two mechanisms this gate assumes. Removing either would leave the check
 * green while the palette quietly reopened, so they are asserted here rather
 * than trusted to survive a refactor.
 */
const mechanismFailures = [];
if (!/--color-\*\s*:\s*initial/.test(themeCss)) {
  mechanismFailures.push("src/styles/index.css no longer resets the palette (`--color-*: initial`)");
}
if (!/@import\s+"tailwindcss"\s+source\(none\)/.test(themeCss)) {
  mechanismFailures.push(
    'src/styles/index.css no longer pins content detection (`@import "tailwindcss" source(none)`)',
  );
}

const sourceFiles = walk(SRC, [".tsx", ".ts", ".css"]);
const sourceFindings = [];
for (const file of sourceFiles) {
  sourceFindings.push(...scanSource(stripComments(readFileSync(file, "utf8")), file));
}
const sourceLeaks = sourceFindings.filter((entry) => !entry.allowed);

const assets = path.join(DIST, "assets");
if (!statSync(assets, { throwIfNoEntry: false })) {
  console.error(`no build at ${assets} — run \`npm run build\` first (this gate reads the artefact)`);
  process.exit(2);
}

const cssFiles = readdirSync(assets)
  .filter((name) => name.endsWith(".css"))
  .map((name) => path.join(assets, name));
if (cssFiles.length === 0) {
  console.error(`no CSS in ${assets} — refusing to report an empty artefact as clean`);
  process.exit(2);
}

const cssFindings = [];
for (const file of cssFiles) cssFindings.push(...scanCss(readFileSync(file, "utf8"), file));

const cssLeaks = cssFindings.filter((finding) => {
  if (finding.transparent) return false;
  if (provenance(finding.base, tokens) !== null) return false;
  return true;
});

// Inventory: every colour that reached the browser, and where it came from.
const inventory = new Map();
for (const finding of cssFindings) {
  const key = finding.transparent ? "transparent" : (finding.base ?? finding.raw);
  const source = finding.transparent
    ? "keyword (paints nothing)"
    : provenance(finding.base, tokens);
  if (!inventory.has(key)) inventory.set(key, { count: 0, source });
  inventory.get(key).count += 1;
}

console.log(`\ntoken colours in ${path.relative(ROOT, assets)}/*.css — ${inventory.size} distinct`);
for (const [colour, entry] of [...inventory].sort((a, b) => a[0].localeCompare(b[0]))) {
  const label = entry.source ?? "NOT A TOKEN";
  console.log(`  ${colour.padEnd(10)} ×${String(entry.count).padStart(3)}  ${label}`);
}
console.log(`  theme colour names: ${[...allowedNames].sort().join(", ")}`);

let failed = false;

if (mechanismFailures.length > 0) {
  console.error("\nFAIL the closed-palette mechanism is missing:");
  for (const message of mechanismFailures) console.error(`  ${message}`);
  failed = true;
}

if (sourceLeaks.length > 0) {
  console.error(`\nFAIL ${sourceLeaks.length} off-token colour class(es) in src/:`);
  console.error(formatFindings(sourceLeaks, ROOT));
  failed = true;
}

if (cssLeaks.length > 0) {
  console.error(`\nFAIL ${cssLeaks.length} colour value(s) in the emitted CSS are not in tokens.css:`);
  console.error(formatFindings(cssLeaks, ROOT));
  console.error("  add the colour to src/styles/tokens.css (if it is chrome) or to ALLOWLIST (if it is a chart ramp)");
  failed = true;
}

if (failed) process.exit(1);
console.log(`\nOK every colour resolves to a token: ${tokens.size} declared, 0 off-token\n`);