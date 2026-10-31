#!/usr/bin/env node
/**
 * Vendor the shadcn/ui primitives Phase 1 needs, pinned to one commit.
 *
 * Why a script and not `npx shadcn add`: the CLI resolves `latest`, which is an
 * unpinned executable path (plan §10, risk 1). This script fetches each file
 * from GitHub raw at a fixed commit SHA and records that SHA in the file
 * header, so every vendored byte is traceable to a reviewable revision.
 *
 * Transformations, and nothing else:
 *   from "cn"                              -> from "@/lib/utils"
 *   @/registry/new-york-v4/ui/<name>       -> @/components/ui/<name>
 * Both are path rewrites the registry itself performs when it installs into a
 * project; the component logic is copied verbatim.
 *
 * Usage: npm run vendor:shadcn [-- --force]
 */
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const OUT_DIR = path.resolve(HERE, "../src/components/ui");

/** shadcn-ui/ui, default branch, resolved 2026-10-30T08:35:43Z. */
const REPO = "shadcn-ui/ui";
const COMMIT = "b8d7baa15b1dca5716f13153e63795fc3d741f6b";
const REGISTRY_DIR = "apps/v4/registry/new-york-v4/ui";

/**
 * Phase 1 needs exactly these. `dialog` is not used directly: `command`
 * imports it for CommandDialog, so it is a required transitive file.
 */
const COMPONENTS = [
  "button",
  "input",
  "select",
  "badge",
  "tooltip",
  "sheet",
  "tabs",
  "resizable",
  "command",
  "dropdown-menu",
  "separator",
  "skeleton",
  "table",
  "dialog",
];

const FORCE = process.argv.includes("--force");

function header(name) {
  return `/*
 * shadcn/ui — ${name}
 *
 * Source:  https://github.com/${REPO}/blob/${COMMIT}/${REGISTRY_DIR}/${name}.tsx
 * Commit:  ${COMMIT}
 * Licence: MIT (shadcn-ui/ui, LICENSE.md at the same commit)
 * Fetched: 2026-10-30 by scripts/vendor-shadcn.mjs
 *
 * Copied verbatim except two module specifiers (the registry's own install
 * transform): "cn" -> "@/lib/utils" and "@/registry/new-york-v4/ui/*" ->
 * "@/components/ui/*". Do not edit by hand: re-vendor instead, so the commit
 * in this header keeps meaning something.
 */
`;
}

async function main() {
  await mkdir(OUT_DIR, { recursive: true });
  let written = 0;
  let skipped = 0;

  for (const name of COMPONENTS) {
    const target = path.join(OUT_DIR, `${name}.tsx`);
    if (existsSync(target) && !FORCE) {
      const current = await readFile(target, "utf8");
      if (current.includes(COMMIT)) {
        skipped += 1;
        continue;
      }
      throw new Error(
        `${target} exists at a different commit; re-run with --force to replace it`,
      );
    }

    const url = `https://raw.githubusercontent.com/${REPO}/${COMMIT}/${REGISTRY_DIR}/${name}.tsx`;
    const response = await fetch(url);
    if (!response.ok) {
      throw new Error(`${url} -> HTTP ${response.status}`);
    }
    let source = await response.text();
    if (source.length < 200 || source.includes("404: Not Found")) {
      throw new Error(`${url} did not return a component (${source.length} B)`);
    }

    // Strip the upstream "use client" directive: it is a Next.js marker with no
    // meaning in a Vite bundle, and keeping it would imply this is an RSC app.
    source = source.replace(/^"use client";?\n+/, "").replace(/^'use client';?\n+/, "");
    source = source.replaceAll('from "cn"', 'from "@/lib/utils"');
    source = source.replaceAll(
      '"@/registry/new-york-v4/ui/',
      '"@/components/ui/',
    );

    if (/\bany\b/.test(source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*$/gm, ""))) {
      console.warn(`warn: ${name}.tsx contains the token "any" — review it`);
    }

    await writeFile(target, header(name) + source, "utf8");
    written += 1;
    console.log(`vendored ${name}.tsx  ${source.length} B`);
  }

  console.log(`\n${written} written, ${skipped} already at ${COMMIT.slice(0, 12)}`);
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : error);
  process.exitCode = 1;
});
