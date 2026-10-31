# Phase 1 adversarial review — the `/app` shell

**Repo** `/home/ubuntu/ztockz` @ `ce5cb54` + the uncommitted Phase 1 tree (`app/main.py` +113,
`tests/test_web_app_mount.py`, `web/` untracked) · **Review date** 2026-10-31 · **Harness**
hand-built ASGI scopes (bypasses httpx/`TestClient` normalisation), a live `uvicorn` on
`127.0.0.1:8123`, curl `--path-as-is`, a throwaway copy of `web/` at `/tmp/webprobe` for DOM probes,
and a throwaway `dist/` copy at `/tmp/p1/dist` for symlink probes. Nothing in the repository was
modified except this file.

Gates on this tree: `cd web && npm run check` → 29 Vitest tests, `tsc --noEmit`, `vite build`,
budget **87,574 B gzip = 85.5 KB** (47.5 % of 180 KB). `.venv/bin/python -m pytest -q` → **4,314
tests, exit 0** (every dot, no F/E). `tests/test_web_app_mount.py` → 16 passed.

No file outside `web/dist` was served. No API string reached the DOM as markup. The findings below
are therefore about a broken deployment path, a fallback that answers 200 where it promised 404, a
contrast claim that is arithmetically wrong, and a search box that can commit the wrong ticker.

---

## 0. Findings

| # | Sev | Where | What |
|---|---|---|---|
| F1 | **high** | `Dockerfile:23-24`; `.github/workflows/container.yml:31-82`; `app/main.py:607-608` | The published container artefact contains no `web/dist`. `/app` 404s in the image while the container reports healthy; CI never runs `npm` or `pytest`, so nothing fails loudly. |
| F2 | medium | `app/main.py:146-158` | The SPA fallback answers **200 HTML** for traversal-shaped paths (`//etc/passwd`, `%252e%252e%252f…`, `x%00`, `/app/assets`, `.git/config`, `/app/HEALTH`), contradicting the class docstring's stated invariant. No file leaks; the probe is hidden from status-code monitoring. |
| F3 | medium | `web/src/styles/tokens.css:54`; `web/README.md:87`; `web/src/styles/tokens.test.ts:85-104` | `--border-control` measures **2.67:1** on `--row-bg` (not the claimed 3.77) and **2.76:1** where it is actually used. The search input's only visual boundary fails WCAG 1.4.11 (3:1) and the gate cannot see it. |
| F4 | medium | `web/src/shell/SymbolSearch.tsx:147-153` | `Enter` commits a hit from the **previous** query while the new one is still debouncing. Type `app`, edit to `zzzz`, press Enter → `onSelect(APP)`. Wrong ticker, silent. |
| F5 | low | `web/src/shell/SymbolSearch.tsx:72-107,111` | After commit, setting the query to the symbol re-runs the debounced effect: 220 ms later the dropdown reopens by itself and a second request is sent. |
| F6 | low | `web/src/shell/SymbolSearch.tsx:61-70` | The listbox stays open when focus leaves the component (Tab to the view tabs leaves `aria-expanded="true"` and a popover over the grid). Only Escape or an outside pointerdown closes it. |
| F7 | low | `web/src/shell/ViewTabs.tsx:80-103,112-118` | `role="tab"` with no `role="tabpanel"` and no `aria-controls` on any of the nine tabs — a tablist that controls nothing. The "All 116" button has no handler and is not disabled. |
| F8 | low | `app/main.py:617-626` | `/app` (the canonical entry) never answers `304`: `If-None-Match` returns 200/801 B, while `/app/`, `/app/index.html` and every deep link answer 304. |
| F9 | low | `web/scripts/check-budget.mjs:52-60,101-119`; `tests/test_web_app_mount.py:322-347` | The gate counts only `<script src>` + `rel=modulepreload` files. A **200 KB inline `<script>`** in `index.html` still reports 85.5 KB and exits 0; all CSS (52 KB raw) is printed but never gated. |
| F10 | low | `web/src/styles/tokens.test.ts:85-104`; `web/src/styles/tokens.css:82-83` | The contrast gate is a whitelist of 18 hardcoded pairs over `tokens.css`. Tailwind's default palette still compiles, so a component can paint `text-lime-700` (verified emitted) and no gate sees it — "a component cannot introduce a colour the token test does not know" is false. |
| F11 | low | `tests/test_web_app_mount.py:42-45,133-142` | No check ties `dist/` to `src/`. In a copy, changing the TopBar wordmark left the old `dist/` in place and 29/29 Vitest + the budget gate still passed; the mount tests assert bytes from the stale artefact. `pytest` alone never rebuilds. |
| F12 | low | `app/main.py:631-633` | The first public HTML surface ships with no `Content-Security-Policy`, `X-Content-Type-Options`, `Referrer-Policy` or frame-ancestors (verified: only `date/server/content-type/accept-ranges/content-length/last-modified/etag`). |
| F13 | low | `web/index.html:2`; `web/src/styles/index.css:10-70` | `class="dark"` is inert: no `@custom-variant dark` is declared, so vendored `dark:` utilities compile to `@media (prefers-color-scheme: dark)` (1 occurrence in the built CSS) and follow the OS, not the forced dark theme. Latent (used badge variant has no active `dark:` class). |
| F14 | low | `web/src/styles/tokens.css:28-31`; `web/README.md:83-96,149-154`; `web/src/shell/StatusLine.tsx:65,103` | Four documentation statements are not true: `--fg-disabled` is used for decorative non-disabled glyphs; "every pair is 12 px" (fg-subtle is 10 px); "verbatim except two module specifiers" (7 files also lost `"use client"`); "npm 11 blocked" the esbuild postinstall (it ran). |
| F15 | info | `app/main.py:635-663` | 404s answer FastAPI's `{"detail":"Not Found"}`, not the documented `{error,errorMessage}` envelope. Pre-existing; `web/src/lib/api.ts:48-55` reads `detail`, so the shell is unaffected. |
| F16 | info | `web/src/components/ui/dialog.tsx`, `sheet.tsx` | Focus trap works (8 Tabs stay inside, Escape closes, initial focus inside) but `aria-modal` is absent from the rendered content (Radix 1.8 behaviour). Nothing renders a Dialog/Sheet yet. |
| F17 | info | `web/src/app.tsx:58-85` | No `<h1>`; the document's first heading is the grid's `<h2>` (`GridPlaceholder.tsx:69`). |

Counts: **1 high, 3 medium, 10 low, 3 info.**

---

## 1. The three that matter

### F1 — the image and CI never contain the UI (high)

`Dockerfile` copies two things into `/app`: `pyproject.toml` (line 23) and `app/` (line 24). There is
no Node stage and no `COPY web`, so `web/dist` does not exist in the image, and
`app/main.py:607-608` — `web_index.is_file()` — is false. The mount is never registered.

```
$ grep -n "COPY\|CMD\|HEALTHCHECK" Dockerfile
23:COPY pyproject.toml ./
24:COPY app ./app
35:HEALTHCHECK ... urlopen('http://127.0.0.1:8080/engine/health' ...)
38:CMD ["uvicorn", "app.main:app", ...]
```

Reproduced on a tree without `dist` (copy of `app/` + `ui/` in `/tmp/nodist`):

```
routes with /app: []
GET /app              404 application/json {"detail":"Not Found"}
GET /app/screener/value 404 application/json {"detail":"Not Found"}
GET /health           200
```

Observed: a healthy API, a green container healthcheck, and a 404 for every `/app` URL.
Expected: the deployed artefact serves the phase, or the process refuses to start / reports
unhealthy when it cannot.

The same hole has three more mouths: `pyproject.toml:38-39` packages only `app*`, so a
`pip install`ed tree resolves `Path(__file__).parent.parent/web/dist` inside `site-packages` where no
build can ever be; the only workflow (`.github/workflows/container.yml`) builds that Dockerfile and
runs `/engine/health`, never `npm run check` and never `pytest` (4,314 tests); and the mount is
decided at import (`app/main.py:631`), so a `git pull && npm run build` on a running box changes
nothing until a restart — exactly the state of the live `127.0.0.1:8099` process, which answers
`GET /app → 404` while its API is up.

Fix: add a `node:22` build stage and `COPY --from=web /src/dist ./web/dist`; add `npm run check` and
`pytest` as CI steps; log one line at startup when the mount is absent.

### F2 — the fallback answers 200 for paths it promises to refuse (medium)

`_is_client_route` (`app/main.py:146-158`) is a string check on the *normalised* path Starlette
hands it. `StaticFiles.get_path` runs `os.path.normpath(os.path.join(*route_path.split("/")))`, which
removes a leading `/` and collapses literal `..`, but a percent-encoded `..` that survived one
uvicorn decode is a literal `%2e%2e` and is untouched. The fallback then serves `index.html`.

Wire-level, `curl --path-as-is`, live server:

```
/app//etc/passwd                              200 text/html  801
/app///etc/passwd                             200 text/html  801
/app/%252e%252e%252f%252e%252e%252fetc%252fpasswd  200 text/html  801
/app/x%00                                     200 text/html  801
/app/%00                                      200 text/html  801
/app/assets                                   200 text/html  801
/app/assets/                                  200 text/html  801
/app/.git/config                              200 text/html  801
/app/HEALTH                                   200 text/html  801
```

Raw-ASGI scope (no client normalisation) agrees, e.g. `path="/app/%2e%2e%2f%2e%2e%2fetc%2fpasswd"`
(decoded once by the server from `%252e…`) → 200 HTML; `path="/app/..\\..\\etc\\passwd"` → 404;
`path="/app/....//....//etc/passwd"` → 404 (the `startswith("..")` arm catches it).

Observed: a 200 that is indistinguishable from a real client route for nine traversal-shaped or
malformed requests. Expected (the class's own docstring, `app/main.py:110-115`): "refuses to fall
back for any path containing `..` or starting with `/` — the fallback would otherwise turn a blocked
traversal into a 200 shell, which hides an attack the operator should see in the log."

Two details make the claim worse than a wording slip. The `path.startswith("/")` arm at
`app/main.py:151` is **dead code** for HTTP requests, because `get_path` already stripped the leading
slash; and the reserved-segment check at `:154` is case-sensitive, so `/app/HEALTH` is a client route
while `/app/health` is a 404. The test that is supposed to cover this
(`tests/test_web_app_mount.py:255-262`) lists only literal and single-encoded forms, all of which
take the 404 branch.

No data escaped: every one of these 200s is the 801-byte `index.html`. See §3 for the containment
attacks that failed.

Fix: reject any path containing `%`, `\`, NUL or a `.`-only segment before falling back, and
lowercase-compare the first segment; or replace the heuristic with an explicit client-route
allowlist.

### F3 — the control boundary is 2.67:1 and the comment says 3.77:1 (medium)

`tokens.css:54` declares `--border-control: #4a5b75; /* control boundary: 3.77:1 on --row-bg */`, and
`README.md:87` repeats "(3.77:1)". Recomputed from the same file with the WCAG 2.1 formula:

```
--border-control #4a5b75 on --row-bg     #10141c  = 2.67:1   (claimed 3.77)
--border-control #4a5b75 on --surface    #10141c  = 2.67:1
--border-control #4a5b75 on --surface-sunken #0c1017 = 2.76:1
--border-control #4a5b75 on --bg         #0a0d13  = 2.82:1
```

Every one of the 23 other claimed pairs is accurate to ±0.01 (the documented `--up` 10.43, `--down`
6.74, `--flat` 8.36, `--alert` 10.46, `--fg` 15.41, `--fg-muted` 8.72, `--fg-subtle` 6.23 and their
alt/hover columns all reproduce exactly; `--focus` 7.65 and `--fg-disabled` 3.26 do too). This one
value is simply wrong, and it is the one that matters: `SymbolSearch.tsx:189` draws the search box
with `border border-line-control` on `bg-surface-sunken`, and the fill differs from the header
surface by 1.03:1, so the 1 px border **is** the control's boundary. WCAG 1.4.11 requires 3:1 for
"visual information required to identify user interface components"; 2.76 fails.

It is invisible to the gate because `tokens.test.ts` measures 18 *text* pairs on the three row
surfaces only. `--border-control` is mentioned in the comment at `tokens.css:54` but never enters
`PAIRS`.

Fix: `--border-control: #5b6d8a` (3.63 on `--surface-sunken`, 3.51 on `--row-bg`) and add the
control-boundary and focus pairs to the test.

---

## 2. Detail — the rest

### F4 — Enter commits a hit from the previous query

`SymbolSearch.tsx:147-153` reads `state.hits[active]` with no test that those hits belong to the
current text. The debounce effect (`:72-107`) only schedules; between keystroke and response the old
list is still rendered and `open` is still true.

Probe (`/tmp/webprobe/src/probe-adversarial.test.tsx`):

```
type "app" → wait 220 ms → 2 hits, active=0
fireEvent.change "zzzz"          ← list still shows APP
fireEvent.keyDown Enter
onSelect calls: [ { symbol: 'APP', name: 'AppLovin Corp' } ]
```

Observed: the UI selects AppLovin while the input reads `zzzz`. Expected: no commit unless
`state.phase === "ready"` and the hits were produced by `trimmed`.

Fix: store the query with the hits (`State.query`) and gate `Enter` on `state.query === trimmed`.

### F5 — selection re-opens the list and re-queries

`commit` (`:109-117`) does `setQuery(hit.symbol)`; the effect depends on `trimmed` (`:107`), so it
schedules `searchSymbols("APP")` again. Probe: after Enter + 230 ms, `aria-expanded=true`,
`listbox=true`, `fetch` calls = 2.

Observed: a dropdown the user just dismissed reopens itself under the cursor. Expected: closed until
the user types again.

Fix: keep a `committedRef`/`skipNext` flag and skip the effect when `trimmed === selected.symbol`.

### F6 — focus leaving does not close the listbox

The only close paths are Escape (`:155-159`) and `pointerdown` outside (`:61-70`); there is no
`onBlur`/`focusout`. Probe: type, wait, then `user.tab()` three times — focus lands on the "Today"
tab button while `aria-expanded` is still `true` and the listbox is still in the DOM, floating over
the grid.

Fix: `onBlur` on the root that closes when `relatedTarget` is outside `rootRef`.

### F7 — tabs without panels

`ViewTabs.tsx:80-103` renders nine `role="tab"` buttons with correct roving tabindex
(`0,-1,-1,…`, verified) and Arrow/Home/End handling, but no tab has `aria-controls` and
`document.querySelectorAll('[role="tabpanel"]')` is 0. WAI-ARIA requires each tab to reference the
panel it controls; the grid it switches (`app.tsx:72-76`) is a plain `<section>`. The "All 116"
button (`:112-118`) has a `title` but no `onClick` and no `disabled` — a dead control that looks live.

Fix: give each tab `id` + `aria-controls`, wrap the grid in `role="tabpanel"` with
`aria-labelledby`, and either implement or `disabled` the All-116 button.

### F8 — the canonical entry point ignores conditional requests

`_app_root` returns a bare `FileResponse` (`app/main.py:617-626`). Starlette's `FileResponse` handles
ranges but not `If-None-Match`; `StaticFiles.file_response` does. Measured with the ETag the server
itself returned:

```
ETag "9287483dd8db72ea4c8da4a28c2424b2"
GET /app                 If-None-Match → 200  801 B
GET /app/                If-None-Match → 304    0 B
GET /app/index.html      If-None-Match → 304    0 B
GET /app/screener/value  If-None-Match → 304    0 B
```

Observed: the entry URL re-sends the document on every revalidation while every other URL 304s.
Expected: one caching behaviour for one document. (The docstring at `:618-625` justifies the route by
cache behaviour, which makes the inconsistency pointed.)

Fix: serve `/app` through the mount's own `file_response` path, or add the two conditional headers
to the route.

### F9 — the budget gate does not measure the document

`check-budget.mjs:52-60` builds `initial` from `<script src>` and `<link rel=modulepreload>`; nothing
else is counted, and CSS (`:101-119`) is printed but not compared to the budget. In a copy of
`web/` with the built `dist/`, appending a 200,004-byte inline script to `dist/index.html`:

```
initial total   278361 B raw · 87574 B gzip · 85.5 KB
budget          184320 B (180 KB) — 47.5% used
OK initial JS 85.5 KB ≤ 180 KB        exit=0
```

Observed: 200 KB of initial JavaScript in the document, gate green. Expected: the number the browser
downloads before first paint. `tests/test_web_app_mount.py:322-347` re-implements the same two
selectors and shares the blind spot; a `<link rel=preload as=script>` (not modulepreload) is likewise
uncounted.

Fix: add the text of every inline `<script>` (and, if the budget is meant to be transfer size, the
CSS) to the total.

### F10 — the contrast gate protects a file, not the shipped CSS

`tokens.css:82-83` claims the shadcn aliases mean "a component cannot introduce a colour the token
test does not know". The aliases only add names; Tailwind v4's default palette is still present. In
the copy, adding `text-lime-700` to `GridPlaceholder.tsx` and building emits:

```
.text-lime-700{color:var(--color-lime-700)}
--color-lime-700:oklch(53.2% .157 131.589)
```

and in an isolated copy (`/tmp/paletteprobe`) all 29 Vitest tests still pass. Likewise `--destructive`, `--primary`, `--secondary` and the
`--popover`/`--row-selected` surfaces are defined but never measured.

On the positive side, the pairs that *are* listed are the right worst cases: for light-on-dark text,
`--row-hover` (#1b2230) is lighter than `--surface`, `--surface-sunken`, `--surface-raised` and
`--popover`, so a token that clears 4.5 there clears the other surfaces too. I recomputed 27 pairs
the shell actually paints (top bar, status line, footer, grid header, empty state, selected option,
search error, focus ring): every text pair passes, and among the boundaries only the control border
fails (the two decorative hairlines are not information, so 1.4.3 does not apply to them). The size
classification is honest — every declared size is 10–12 px, far below 24 px / 18.66 px bold, and the
guard test at `tokens.test.ts:177-182` does catch a size edit (I tried: changing `--fg-subtle`'s
declared size to 24 px fails "classifies none of the data pairs as large text").

Fix: set `--color-*: initial` in `@theme` (or lint for colour utilities outside the token set) and
add the control/focus pairs.

### F11 — `dist/` is never tied to `src/`

In a copy with `src/` and the existing `dist/`, I changed the TopBar wordmark to `STALEBUILD`.
`npx vitest run` → 29 passed; `node scripts/check-budget.mjs` → OK 85.5 KB; `dist/assets/*.js`
contained 0 occurrences of the new string. `tests/test_web_app_mount.py` reads `INDEX` and the built
assets directly (`:133-142`, `:322-347`), so it certifies the old bytes too. `app/main.py` likewise
serves whatever is on disk.

Observed: green gates over an artefact that does not contain the current source. Expected: either the
gate rebuilds, or it proves the artefact came from this source.

Fix: write a source hash into `index.html` at build time and assert it in the Python test (or make
`pytest` invoke `npm run build` when `web/src` is newer than `web/dist`).

### F12 — no security headers

`curl -sI http://127.0.0.1:8123/app` returns exactly `date`, `server`, `content-type`,
`accept-ranges`, `content-length`, `last-modified`, `etag`. For the first HTML surface in the product
that is a thin default: no CSP (so any future injection executes), no `nosniff`, no
`frame-ancestors`/`X-Frame-Options`, no `Referrer-Policy`. The document makes no third-party request
today, which is exactly what a `default-src 'self'` CSP would enforce.

Fix: one middleware adding `Content-Security-Policy: default-src 'self'; frame-ancestors 'none'`,
`X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`.

### F13 — `class="dark"` does nothing

`index.html:2` sets `class="dark"`, but `index.css` declares no `@custom-variant dark`, so Tailwind
compiles `dark:` to a media query — the built CSS contains exactly one `prefers-color-scheme` occurrence.
The tokens are unconditionally dark on `:root`, so the page stays dark; only the vendored `dark:`
utilities (button outline, select, destructive badge) switch on the *OS* preference. Today the only
used component with a `dark:` class is the badge's destructive variant, which the shell does not
render, so the impact is latent — but the next component that uses one will be half-styled on a
light-OS machine.

Fix: `@custom-variant dark (&:where(.dark, .dark *));` or delete the class.

### F14 — four statements that are not true

1. `tokens.css:30-31` and `README.md:85`: "`--fg-disabled` … is used only for disabled labels". It is
   used for the breadcrumb separator and the status dot (`StatusLine.tsx:65,103`), neither disabled
   nor a label. Both uses are `aria-hidden`/decorative, so this is documentation, not a violation.
2. `README.md:83`: "Every pair is 12 px at 400–600 weight". `--fg-subtle` is declared at 10 px in the
   test (`tokens.test.ts:102-103`) and is used at 10 px by every status-line and footer label.
3. `README.md:94-96` / `NOTICE.md`: "verbatim except two module specifiers". Diffing all eight
   components I fetched from the recorded commit against the local files (after the header and the
   two rewrites) leaves only one difference: upstream's `"use client"` directive was also removed
   from `sheet`, `command`, `select`, `dropdown-menu`, `dialog`, `tabs`, `tooltip` (button has none).
   The component logic is otherwise byte-identical, so this is provenance wording, not tampering.
4. `README.md:149-154`: the esbuild postinstall was "blocked". `npm config get ignore-scripts` is
   `false`, there is no `.npmrc`, and `node_modules/esbuild/bin/esbuild` is a 10 MB executable with
   link count 2 — the hardlink `install.js` creates. The hook exists (one package in the installed
   tree, as claimed) and it ran.

### F15–F17 — observations

- 404 bodies are `{"detail":"Not Found"}` (FastAPI default), not the house envelope the module
  docstring promises. `api.ts:48-55` reads both, so the shell copes; worth aligning before clients
  are written against it.
- Radix `Dialog`/`Sheet` focus behaviour is correct: 8 Tabs stay within the panel, Escape closes,
  initial focus is inside. `aria-modal` is not emitted on the content element in this version.
- No `<h1>`; heading order starts at `h2`.

---

## 3. Attacks that failed, and why

**Containment (goal b): no file outside `web/dist` was served.** Every attempt below returned 404 or
the 801-byte shell — no canary (`from __future__`, `create_app`, `root:x:`, `MEE_DB_PATH`) appeared in
any body.

| Attempt | Result |
|---|---|
| `/app/../app/main.py`, `/app/../../etc/passwd` | 404 JSON |
| `/app/%2e%2e/%2e%2e/etc/passwd`, `/app/..%2f..%2fetc%2fpasswd`, mixed `.%2e` | 404 JSON |
| `/app/assets/../../../../etc/passwd` (literal and encoded) | 404 JSON |
| `/app/..%5c..%5cetc%5cpasswd` | 404 JSON |
| `/app/....//....//etc/passwd`, `/app/..;/..;/etc/passwd` | 404 JSON |
| `/app/index.html%00.png`, `/app/index.html%00/../../app/main.py` | 404 JSON |
| `/app/.env`, `/app/.DS_Store`, `/app/package.json`, `/app/../web/package.json` | 404 JSON |
| symlink `link.py → app/main.py` inside a dist copy | 404, `lookup_path` → `("", None)` |
| symlink `etc → /etc` (directory) | 404; path `/app/etc/passwd` → 200 shell, no file |
| symlink `passwd → /etc/passwd`, `rel.py → ../../app/main.py`, `.hidden → /etc/passwd` | 404, `("", None)` |
| symlink `assets/evil.js → app/main.py` | 404 (extension rule) |
| `/app/index.html`, `/app/favicon.svg` | 200, the real files |

Why it holds: Starlette 1.9's `StaticFiles.lookup_path` refuses a path starting with `/` or `\`,
resolves with `realpath`, and compares `commonpath` against the real directory, so symlinks out and
`..` both fall through; `get_response` maps `ValueError` (NUL) and `ENAMETOOLONG` to 404; and the
fallback can only ever return `index.html`, never an arbitrary path. The containment claim is true.
What is not true is that a blocked attempt is *visible* — see F2.

**XSS (goal: none reached the DOM as markup).** `grep -rnE "dangerouslySetInnerHTML|innerHTML|outerHTML|document.write|insertAdjacentHTML|eval\(|new Function|javascript:" src/` finds nothing outside comments and the test payloads. Hostile values rendered through a stubbed `fetch` and through `StatusLine` props:

```
symbol  <img src=x onerror="window.__xss=1">
name    <script>window.__xss=2</script>
exchange javascript:window.__xss=3
health check status  <img src=x onerror="alert(6)">
health version       <b onmouseover="window.__xss=7">1</b>
```

Result: `window.__xss` undefined; 0 `[onerror]`, 0 `[onload]`, 0 `<img>`, 0 `<script>`; all payloads
present in `textContent`; 0 `[href]` elements anywhere, so no URL is built from API data; the only
SVGs are lucide's two `aria-hidden` icons. `StatusLine`'s `title` attribute receives the escaped
string (`depth_store: <img src=x onerror="alert(6)">` as text).

**API client.** `searchSymbols` trims, refuses an empty query without a request, and builds the URL
with `URLSearchParams`: `q=a b&limit=999#frag?x="><script>` becomes
`/symbols/search?q=a+b%26limit%3D999%23frag%3Fx%3D%22%3E%3Cscript%3E&limit=8`. The path is a literal;
no absolute URL is accepted anywhere in `api.ts`.

**Failure modes.** A 500 carrying an HTML body (the SPA-fallback shape) produces `Response was not
JSON` in the panel — `readJson` refuses to parse HTML as data, which is the right defence. A 500 with
the house envelope surfaces `errorMessage`; a `TypeError` from `fetch` surfaces "Failed to fetch"; a
50,000-character query sends one request (no client cap); whitespace sends none. With the API
unreachable the shell renders with `data-health-status="unknown"` and `OFFLINE`, not a blank page.

**HEAD / methods / deep links.** `HEAD /app` → 200, 0 B, `content-length: 801`; `HEAD
/app/screener/value` → 200, 0 B; `HEAD /app/assets/missing.js` → 404 JSON, no body. `POST`, `PUT`,
`OPTIONS` → 405 JSON with an `allow: HEAD, GET` header. `/app/screener/value?tab=value&x=%3Cscript%3E` → 200,
byte-identical shell (query string preserved for the client). `/app` and `/app/` both 200, no
redirect. `/app/assets/index-DOES-NOT-EXIST.js` and `/app/assets/nope.css` → 404 JSON, never HTML —
the central claim of the fallback design holds. Reserved names (`/app/api`, `/app/screens/select`,
`/app/health`, `/app/symbols/search`, `/app/requests`, `/app/export`) → 404 JSON. The old UI is
untouched: `/` → 307 `/ui/`, `/ui/` 200, `/ui/app.js` 200, `/ui/styles.css` 200.

**Bundle contents.** No source maps, no `sourceMappingURL`, no `http(s)://` beyond W3C namespace URIs
and React's error URL, no `MEE_` strings, and no credential-shaped strings (the only `password` occurrences are React DOM's input-type table). `npm audit --omit=dev`: 0
vulnerabilities. 23 direct dependencies, all exact-pinned, all permissive licences (Apache-2.0 / MIT /
ISC), installed versions equal the specs.

---

## 4. Builder claims, checked

| Claim | Verdict | Evidence |
|---|---|---|
| Directory traversal blocked; `lookup_path` refuses | **true** | §3: the full traversal and symlink matrix, no leak |
| SPA fallback never swallows API 404s | **true for API names, false as written** | 9 traversal-shaped paths get 200 HTML (F2) |
| Deep link loads with the `/app` base | **true** | `/app/screener/value` = exact `index.html`; its `/app/assets/*` URLs all 200 |
| Contrast test bites; `--up` broken to 2.29:1 | **true** | `#0b5c3a` on `#10141c` = 2.29, reproduced independently |
| 23 deps, all pinned and licensed | **true** | `package.json` 23 entries; installed == spec; permissive licences |
| One postinstall hook (esbuild), blocked | **hook count true, "blocked" false** | `ignore-scripts=false`; `bin/esbuild` is the hardlinked 10 MB binary install.js writes |
| 85.5 KB initial JS | **true** | 87,574 B gzip = 85.5 KB, 47.5 % of 180 KB |

Claims I could not reproduce: only "blocked" above. Everything else reproduced on this tree.

---

## 5. Gates run

```
cd web && npm run check
  vitest        3 files, 29 tests passed
  tsc --noEmit  clean
  vite build    index-BXmaiSho.js 278.36 kB (87.72 kB gzip), CSS 51.98 kB (9.61 kB gzip)
  budget        87,574 B gzip = 85.5 KB, 47.5 % of 180 KB — OK

.venv/bin/python -m pytest -q
  4,314 tests, exit 0 (pytest.log: 4,314 dots, no F/E)

.venv/bin/python -m pytest tests/test_web_app_mount.py -q
  16 passed
```

Probes (throwaway, not committed): `/tmp/p1/asgi_probe.py` (raw ASGI, the whole request matrix),
`/tmp/p1/symlink_probe.py` (dist copy + 6 symlinks), `/tmp/p1/contrast.py` (24 claimed + 27 real
pairs), `/tmp/webprobe/src/probe-{adversarial,shell,dialog,blur}.test.tsx` (XSS, keyboard path,
focus trap, blur), `/tmp/staleprobe` (stale dist + 200 KB inline script), `/tmp/paletteprobe` (a Tailwind-palette colour no gate sees),
`/tmp/nodist` (a tree with no `dist`).
