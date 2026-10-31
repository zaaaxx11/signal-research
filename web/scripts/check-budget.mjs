#!/usr/bin/env node
/**
 * Initial-JS budget gate.
 *
 * "Initial" means what the browser must download before first paint: the
 * `<script type="module">` entry and every `<link rel="modulepreload">` in
 * dist/index.html. A lazily imported chunk (Phase 5's detail panel, say) is not
 * counted here and must not be, or the gate would punish code splitting.
 *
 * Size is gzip -9, the pessimistic number: a server that compresses with a
 * lower level will ship less, never more.
 *
 * Usage:  node scripts/check-budget.mjs [--budget-kb=180]
 * Exit:   0 within budget, 1 over, 2 no build to measure.
 */
import { readFileSync, readdirSync, statSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { gzipSync } from "node:zlib";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const DIST = path.resolve(HERE, "../dist");
const INDEX = path.join(DIST, "index.html");

const override = process.argv
  .find((argument) => argument.startsWith("--budget-kb="))
  ?.split("=")[1];
const BUDGET_KB = Number(override ?? 180);
if (!Number.isFinite(BUDGET_KB) || BUDGET_KB <= 0) {
  console.error(`invalid --budget-kb: ${override}`);
  process.exit(2);
}
const BUDGET = BUDGET_KB * 1024;

if (!statSync(INDEX, { throwIfNoEntry: false })) {
  console.error(
    `no build at ${INDEX} — run \`npm run build\` first (the gate measures the artefact, not the source)`,
  );
  process.exit(2);
}

const html = readFileSync(INDEX, "utf8");

/** Map a URL from index.html ("/app/assets/index-abc.js") onto dist/. */
function toFile(url) {
  const marker = "/assets/";
  const at = url.indexOf(marker);
  if (at === -1) return null;
  return path.join(DIST, url.slice(at + 1));
}

const initial = new Set();
for (const match of html.matchAll(/<script[^>]+src="([^"]+\.js)"/g)) {
  if (match[1]) initial.add(match[1]);
}
for (const match of html.matchAll(
  /<link[^>]+rel="modulepreload"[^>]+href="([^"]+\.js)"/g,
)) {
  if (match[1]) initial.add(match[1]);
}

if (initial.size === 0) {
  console.error("index.html declares no initial script — refusing to report 0 B as a pass");
  process.exit(1);
}

const rows = [];
let totalRaw = 0;
let totalGzip = 0;

for (const url of [...initial].sort()) {
  const file = toFile(url);
  if (file === null) {
    console.error(`cannot map ${url} onto dist/`);
    process.exit(2);
  }
  const bytes = readFileSync(file);
  const gzipped = gzipSync(bytes, { level: 9 });
  totalRaw += bytes.length;
  totalGzip += gzipped.length;
  rows.push({
    kind: "initial",
    name: path.basename(file),
    raw: bytes.length,
    gzip: gzipped.length,
  });
}

// Chunks that are not referenced by index.html are lazy by definition: report
// them, never gate them.
let otherGzip = 0;
for (const name of readdirSync(path.join(DIST, "assets"))) {
  if (!name.endsWith(".js")) continue;
  if (rows.some((row) => row.name === name)) continue;
  const bytes = readFileSync(path.join(DIST, "assets", name));
  const gzipped = gzipSync(bytes, { level: 9 }).length;
  otherGzip += gzipped;
  rows.push({ kind: "lazy", name, raw: bytes.length, gzip: gzipped });
}

const css = readdirSync(path.join(DIST, "assets"))
  .filter((name) => name.endsWith(".css"))
  .map((name) => {
    const bytes = readFileSync(path.join(DIST, "assets", name));
    return { name, raw: bytes.length, gzip: gzipSync(bytes, { level: 9 }).length };
  });

const pct = ((totalGzip / BUDGET) * 100).toFixed(1);
console.log("\ninitial JS (gzip -9)");
for (const row of rows) {
  const flag = row.kind === "lazy" ? "  (lazy, not counted)" : "";
  console.log(
    `  ${row.name.padEnd(28)} ${String(row.raw).padStart(8)} B raw  ${String(row.gzip).padStart(7)} B gz${flag}`,
  );
}
for (const row of css) {
  console.log(
    `  ${row.name.padEnd(28)} ${String(row.raw).padStart(8)} B raw  ${String(row.gzip).padStart(7)} B gz  (css)`,
  );
}
console.log(
  `\n  initial total   ${totalRaw} B raw · ${totalGzip} B gzip · ${(totalGzip / 1024).toFixed(1)} KB`,
);
console.log(`  budget          ${BUDGET} B (${BUDGET_KB} KB) — ${pct}% used`);
if (otherGzip > 0) {
  console.log(`  lazy JS         ${otherGzip} B gzip`);
}

if (totalGzip > BUDGET) {
  console.error(
    `\nFAIL initial JS ${(totalGzip / 1024).toFixed(1)} KB exceeds the ${BUDGET_KB} KB budget by ${( (totalGzip - BUDGET) / 1024).toFixed(1)} KB`,
  );
  process.exit(1);
}

console.log(`\nOK initial JS ${(totalGzip / 1024).toFixed(1)} KB ≤ ${BUDGET_KB} KB\n`);
