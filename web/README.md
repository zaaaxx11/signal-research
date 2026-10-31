# Ztockz web — Phase 1 shell

The new screener interface: Vite 7 + React 19 + TypeScript (strict) + Tailwind v4
+ vendored shadcn/ui. Built to static assets and served by FastAPI at `/app`
while `/` keeps the old cockpit (decision D9).

This phase ships **the shell only**: identity, symbol search, the view tab
strip, the health status line, and the box the grid will occupy. No rows are
rendered — the placeholder says so in words rather than pretending.

```
web/
├── index.html                  entry document (no external origins)
├── vite.config.ts              base "/app/", dev proxy, version define
├── src/
│   ├── app.tsx                 the shell: four bands, one screen
│   ├── main.tsx                React root
│   ├── lib/api.ts              the only module allowed to call fetch()
│   ├── components/ui/          vendored shadcn primitives (MIT, pinned)
│   ├── shell/                  TopBar, SymbolSearch, ViewTabs, StatusLine, GridPlaceholder
│   └── styles/
│       ├── tokens.css          the dark palette, single source of truth
│       ├── tokens.test.ts      WCAG contrast gate
│       └── index.css           Tailwind v4 theme mapping + base layer
├── .npmrc                      ignore-scripts=true (one redundant hook: esbuild)
└── scripts/
    ├── vendor-shadcn.mjs       pinned re-vendor of the primitives
    ├── check-budget.mjs        initial-JS gzip gate (180 KB)
    └── check-tokens.mjs        closed-palette gate: no colour outside tokens.css
```

## Commands

```bash
npm ci
npm run dev      # http://127.0.0.1:5173/app/ , proxies the API to :8099
npm run build    # tsc --noEmit && vite build  -> dist/
npm run budget   # measures dist/index.html's initial JS (gzip -9)
npm run audit:tokens  # fails on any colour in src/ or dist/ that is not a token
npm run check    # test + build + token audit + budget, in that order
```

The API must be running on `127.0.0.1:8099` for dev. `npm run build` must run
before the Python process starts if you want `/app` served: `app/main.py` mounts
`web/dist` only when `web/dist/index.html` exists, so an unbuilt checkout still
imports and serves the API instead of dying on a missing directory.

## URL contract

| Request | Answer |
|---|---|
| `GET /app` | 200, `dist/index.html` (no redirect) |
| `GET /app/` | 200, same document |
| `GET /app/value/AAPL` | 200, same document — client route |
| `GET /app/assets/*.js`, `*.css` | the file, `text/javascript` / `text/css` |
| `GET /app/assets/missing.js` | **404**, never HTML (a wrong chunk must fail loudly) |
| `GET /app/api/nope` | **404 JSON** — no API lives under the app prefix |
| `GET /app/../app/main.py` | **404** — `StaticFiles` refuses to leave `dist/` |
| `GET /` | 307 → `/ui/` (unchanged) |
| `GET /ui/` | the old cockpit (unchanged) |

The SPA fallback deliberately does not apply to paths whose last segment has an
extension, or to anything under `api/`. Both choices exist to keep a broken
asset URL visible instead of turning it into a 200 that parses HTML as
JavaScript.

## Tokens

`src/styles/tokens.css` is the only place a colour is defined; Tailwind's
`@theme inline` maps every token to a utility so no component invents one.
Dark ships alone (D11); a light theme is a second block for the same names.

The palette is rose. The interesting decision is not the pink — it is that the
negative colour had to stop being pink at the same moment. The previous ramp
paired a blue accent (`#7aa2ff`) with a pink-red loss (`#ff6b81`), so moving the
accent into pink without moving `--down` would have made a falling number and a
clickable control the same colour at 12 px. The accent family therefore ships at
three weights, and the loss moved to red:

| Token | Value | Role | Hue (HSL) | Hue (OKLCH) |
|---|---|---|---:|---:|
| `--accent` | `#ff8ec4` | chrome: active-tab edge, focus ring, chip border | 331.3° | 351.0° |
| `--accent-text` | `#ffa8cf` | links and secondary chrome on dark | 333.1° | 351.4° |
| `--accent-strong` | `#e8528f` | fill: primary button / active tab, ink `#1f0a14` | 335.6° | 358.8° |
| `--down` | `#ff5f4d` | a loss, always with its sign and arrow | 6.1° | 29.4° |

**34.7° of hue** (38.4° in OKLCH) and **0.146 of relative luminance** separate
the brand rose from the loss red: `--accent` is the lighter of the two, so the
pair survives as a value difference even when hue is gone. Both numbers are
asserted — `--accent` must land in 320–340°, `--down` in 0–8°, the arc between
them ≥25°, and `luminance(accent) − luminance(down) ≥ 0.1`. Hue is quoted in HSL
because that is the number `hsl()`, every design tool and CSS `hotpink` report;
OKLCH puts the same rose ~20° higher, which is the standard sRGB→OKLab warp, and
the contract test pins one space instead of letting the two drift apart.

The fill weight is not decoration: `--accent-strong` measures 5.31:1 against
`--surface` and 4.60:1 against `--row-hover`, so a filled control clears
WCAG 1.4.11 without a border, and its ink clears 4.5:1. `--accent-soft`
(`#31262c`) is the *surface* the vendored primitives mean by `accent`
(`hover:bg-accent`, the skeleton pulse); `index.css` maps `--color-accent` to it
while `--accent` itself stays the rose the focus ring and the native checkbox
use. A pink skeleton would have been the first casualty of not separating them.

Measured by `src/styles/tokens.test.ts` (WCAG 2.1 relative luminance), never by
eye:

| Token | Value | `--row-bg` | `--row-bg-alt` | `--row-hover` | `--row-selected` |
|---|---|---:|---:|---:|---:|
| `--up` | `#3ddc97` | 10.46 | 9.96 | 9.06 | 8.56 |
| `--down` | `#ff5f4d` | 6.16 | 5.86 | 5.33 | 5.04 |
| `--flat` | `#a6a2a4` | 7.33 | 6.98 | 6.35 | 6.00 |
| `--alert` | `#ffb454` | 10.48 | 9.98 | 9.08 | 8.58 |
| `--fg` | `#efe9ec` | 15.43 | 14.69 | 13.37 | 12.64 |
| `--fg-muted` | `#b9afb4` | 8.67 | 8.26 | 7.51 | 7.10 |
| `--fg-subtle` | `#9d9398` | 6.21 | 5.92 | 5.38 | 5.09 |
| `--accent-text` | `#ffa8cf` | 10.35 | 9.85 | 8.96 | 8.47 |

Every data pair is 10–12 px at 400–600 weight — normal text, so the 4.5:1
threshold applies and none of them is excused as "large". `--row-selected` is in
the table because a selected row still prints its numbers: the tones are measured
on the lightest plane a datum can land on, not just the two zebra stripes.
`--fg-disabled` (`#6d6569`, 3.27 on `--row-bg`, 3.38 on `--surface-sunken`) is
exempt by WCAG 1.4.3 (inactive controls) and is used only for disabled labels.
It is a grey with no rose in it on purpose: a disabled control must not read as a
half-strength active pink one.

`--flat` (`#a6a2a4`) is the only achromatic semantic in the file — channel spread
4/255 — so "unchanged" owns no hue and cannot be mistaken for chrome.

Borders are decorative hairlines; the boundaries that carry meaning — a control's
edge, the focus ring, the active tab's 2 px edge — are measured separately, at
the 3:1 of WCAG 1.4.11:

| Token | Value | sunken | surface | raised | row-bg | row-alt | hover | page |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| `--border-control` | `#7b6e75` | 3.94 | 3.81 | 3.59 | 3.81 | 3.63 | 3.30 | 4.01 |
| `--focus` | `#ff8ec4` | 9.03 | 8.73 | 8.23 | 8.73 | 8.31 | 7.56 | 9.18 |
| `--accent` | `#ff8ec4` | 9.03 | 8.73 | 8.23 | 8.73 | 8.31 | 7.56 | 9.18 |
| `--accent-strong` | `#e8528f` | 5.49 | 5.31 | 5.01 | 5.31 | 5.06 | 4.60 | 5.58 |

The focus row is the adversarial one: a rose ring drawn on a rose-tinted zebra
row. It measures 8.31:1 there against a 3:1 floor — the tint costs 0.42 of ratio,
not the margin. `--border-control` keeps 0.30 of headroom at its worst case
(`--row-hover`), which is why it moved up to `#7b6e75` rather than staying at the
old value's 3.19.

The surfaces are a tint, not a colour: page `#100c0e`, surface/row `#181215`,
zebra `#1e171b`, hover `#281f24` — hue 324–330°, OKLCH chroma 0.008–0.016, at
most 9/255 of channel spread, and **1.14 ΔE from neutral** for `--row-bg`. The
accent is 15 ΔE away. That is the whole "calm at 100 rows" argument: the rose
lives in the chrome, and the table is warm charcoal. `--row-selected`
(`#34202b`, spread 20/255) and `--selection` (`#49293c`) are states, and they are
the only fills allowed more chroma than a plane — both still print `--fg` at
12.64:1 and 10.53:1.

Colour is never the only channel. The delta renderer prints `▲`/`▼` and a signed
percent, and carries an `sr-only` word ("up"/"down"/"unchanged"); a cell's tone is
redundant with its text. Under deuteranopia (`#ff8ec4` simulates to `#b7bac1`,
`#ff5f4d` to `#b3a248`) the two land 14.6 ΔE apart with 8 L* of separation — a
grey-blue versus an olive — and the accent's role (a 2 px edge, a ring, a link)
differs from the number's (a right-aligned tabular figure with an arrow) even
where the hues converge.

The search box is why the boundary table exists: `SymbolSearch` paints a 1 px
`border-line-control` on `bg-surface-sunken`, and that fill differs from the
header surface by 1.03:1 — the border *is* the control, so it is information,
not decoration. The value before F3, `#4a5b75`, measured **2.67:1** on
`--row-bg` and **2.76:1** where it is used while this file claimed 3.77:1. The
gate never saw it because `PAIRS` only held text pairs; `BOUNDARIES` now
computes the table above from `tokens.css` on every run and fails on the old
value with the pair and ratio named.

## Palette (closed by construction)

Two mechanisms, because either alone leaves a hole:

1. **The namespace is deleted.** `src/styles/index.css` opens with
   `@theme { --color-*: initial; … }`, so Tailwind's ~250 default colours stop
   existing. A colour utility can only name something that file maps;
   `text-lime-700` no longer compiles. `black` and `white` are re-declared
   because two vendored primitives use them as a dialog scrim and as ink on
   `--destructive`; they resolve to `--scrim` and `--on-destructive`, so
   `tokens.css` is still the only file that names a colour.
2. **The gate fails instead of shrugging.** Tailwind drops an unknown utility
   *silently*, so a component could still reference a colour nobody vetted and
   the build would stay green. `scripts/check-tokens.mjs` (in `npm run check`)
   scans `src/` for default-palette classes and arbitrary `[#rrggbb]` values,
   then parses every colour literal in `dist/assets/*.css` and requires it to
   resolve to a `tokens.css` value, a structural neutral, or an explicit
   `ALLOWLIST` entry (reserved for chart ramps). It prints the whole inventory:
   every colour in the shipped CSS, with the token it came from.

Negative proof, run while adding this: `text-lime-700` in
`src/shell/GridPlaceholder.tsx` → `npm run check` exits 1 with

```
FAIL 1 off-token colour class(es) in src/:
  src/shell/GridPlaceholder.tsx:54  text-lime-700   Tailwind palette colour "lime"
```

…while the emitted CSS contains **zero** `lime` values. That silence is the
reason the source half exists; the artefact half catches the other route (a
colour arriving from a vendored file, an inline style, a future `@theme`).

Content detection is explicit — `@import "tailwindcss" source(none)` plus
`@source` for `index.html` and `src/`. The default heuristic scans the entire
project (anything not gitignored), and during this work it compiled the gate's
own self-test fixture, `.text-[#ff00aa]`, into the bundle.

## Vendored primitives

Copied from `shadcn-ui/ui` at commit **`b8d7baa15b1dca5716f13153e63795fc3d741f6b`**
(2026-10-30), path `apps/v4/registry/new-york-v4/ui/`. Each file carries the full
URL and commit in its header; `npm run vendor:shadcn -- --force` re-copies them.
The only transformation is two module specifiers (`"cn"` → `@/lib/utils`,
`@/registry/new-york-v4/ui/*` → `@/components/ui/*`) — the registry's own install
transform. Licence: MIT (see `NOTICE.md`).

| File | Imported by the shell? | Purpose |
|---|---|---|
| `button.tsx` | no (Phase 2) | actions, filter chips |
| `input.tsx` | no (Phase 3) | numeric filter fields |
| `select.tsx` | no (Phase 3) | preset buckets |
| `badge.tsx` | **yes** | the PREVIEW chip |
| `tooltip.tsx` | no — measured 21 KB gzip | column-header help in Phase 2 |
| `sheet.tsx` | no (Phase 3) | filter drawer |
| `tabs.tsx` | no (Phase 5) | detail-panel tabs |
| `resizable.tsx` | no (Phase 5) | docked detail panel |
| `command.tsx` | no (Phase 3) | ⌘K field jump |
| `dropdown-menu.tsx` | no (Phase 2) | column visibility |
| `separator.tsx` | no | layout separators |
| `skeleton.tsx` | **yes** | search loading rows |
| `table.tsx` | no (Phase 2) | grid markup |
| `dialog.tsx` | transitive | `command.tsx` imports it |

Unimported files cost nothing: they are tree-shaken out of the build. The tab
strip is hand-written rather than Radix `Tabs` because it owns a placeholder
selection and roving focus that Radix would fight; the vendored `Tabs` is for
Phase 5's detail panel.

## Dependencies

All direct dependencies, exact-pinned (no `^`), npm-installed, none with an
install script of their own. Licence and last-publish date read from
`registry.npmjs.org` on 2026-10-30.

| Package | Version | Licence | Published | Note |
|---|---|---|---|---|
| `react`, `react-dom` | 19.3.1 | MIT | 2026-10-14 | active |
| `radix-ui` | 1.8.1 | MIT | 2026-10-12 | active; the unified package the registry imports |
| `class-variance-authority` | 0.7.1 | **Apache-2.0** | 2024-11-26 | pre-1.0 but stable; a shadcn dependency |
| `clsx` | 2.1.1 | MIT | 2024-04-23 | stable micro-lib, no releases needed |
| `tailwind-merge` | 3.7.0 | MIT | 2026-09-12 | active |
| `lucide-react` | 1.65.0 | **ISC** | 2026-10-30 | active (published today) |
| `cmdk` | 1.1.1 | MIT | 2025-03-14 | slow cadence, no open blocker |
| `react-resizable-panels` | 4.15.3 | MIT | 2026-10-30 | active |
| `tailwindcss`, `@tailwindcss/vite` | 4.3.3 | MIT | 2026-07-16 | active |
| `tw-animate-css` | 1.4.0 | MIT | 2025-09-24 | tiny keyframe set for shadcn states |
| `vite` | 7.3.7 | MIT | 2026-10-06 | plan pins Vite 7; Vite 8 exists |
| `@vitejs/plugin-react` | 5.2.0 | MIT | 2026-03-12 | supports Vite 4–8 |
| `typescript` | 5.9.3 | **Apache-2.0** | 2025-09-30 | plan says TS 5.x; 7.0 is the native port |
| `vitest` | 5.0.5 | MIT | 2026-10-15 | active |
| `jsdom` | 30.1.3 | MIT | 2026-10-15 | test DOM |
| `@testing-library/react` | 16.3.3 | MIT | 2026-08-27 | active |
| `@testing-library/dom` | 10.4.2 | MIT | 2026-09-13 | peer of the above, declared explicitly |
| `@testing-library/user-event` | 14.6.8 | MIT | 2026-10-13 | active |
| `@types/node` | 24.10.1 | MIT | 2025-11-11 | types |
| `@types/react`, `@types/react-dom` | 19.3.0 | MIT | 2026-09-09 | types |

### Install scripts

Exactly one hook exists in the tree: **`esbuild@0.28.2` (MIT)**,
`postinstall: node install.js`, pulled in by Vite. Nothing else declares a
preinstall/install/postinstall — checked by walking every package.json under
`node_modules` (250 files, 1 hook).

`.npmrc` sets `ignore-scripts=true`, so that hook does not run. It is redundant
here: npm installs the real 10,288,264-byte binary as the platform-specific
optional dependency (`@esbuild/linux-arm64` on this machine — npm picks the
matching `@esbuild/*` package), and esbuild's JS API — what Vite calls —
resolves it from there. The observable cost is that `node_modules/esbuild/bin/esbuild` stays a
9,350-byte JS shim instead of being hard-linked to the binary; `npx esbuild
--version` still answers `0.28.2`, because the shim resolves the platform
package.

Proven cold, not warm (the warm tree still had the old 10 MB hard link, which
is exactly the state that hides a broken clean install):

```
rm -rf node_modules && npm ci      # 231 packages, 0 vulnerabilities
npm run check                      # 38 tests, tsc, vite build, token audit, 85.6 KB
```

An earlier version of this section said npm 11 "blocked" the hook. It did not:
`npm config get ignore-scripts` was `false` and the 10 MB binary was sitting in
`bin/esbuild` — the build had been running with the script enabled all along.
It is blocked now, by this repository, because it buys nothing here.

## Build budget

`scripts/check-budget.mjs` sums gzip -9 of the entry script and every
`modulepreload` in `dist/index.html`. Lazy chunks are reported and not counted.

```
initial JS   85.6 KB gzip  (87,615 B)   of 180 KB  — 47.5 %
CSS           9.3 KB gzip  (51,743 B raw)
```

`tests/test_web_app_mount.py` re-measures this independently in Python and
asserts the same ceiling, then runs the node script twice: once at the real
budget (must pass) and once at `--budget-kb=1` (must fail), so the gate is proven
to bite.

## Tests

```bash
npm test                                   # the Vitest suite: contrast, tokens, grid, filters, API
.venv/bin/python -m pytest tests/test_web_app_mount.py -q
```

The contrast test parses `tokens.css` and computes WCAG ratios itself — 36
text pairs at 4.5:1 and 22 control boundaries at 3:1 — so darkening a token
fails the build with the exact pair and ratio named. The search test drives the
real component with fake timers and a mocked `fetch`: one request per typing
burst, arrow keys move `aria-activedescendant`, Enter commits **only a list
that answers the text in the box** (the debounce window cannot commit the
previous query's hit), Escape dismisses without clearing, and a zero-result
query makes Enter a no-op.

## Deliberate omissions

- No router. The shell has no second screen; a router would be 12 KB of
  dependency for state that is one `useState`.
- No state library, no `nuqs` yet — Phase 3 brings URL state with the filters.
- No polling of `/health`: one read per mount plus an explicit retry. `/health`
  touches the depth store and SQLite; a page is not a monitor.
- No charts, no virtualisation, no TanStack Table: Phase 2 and Phase 6.
