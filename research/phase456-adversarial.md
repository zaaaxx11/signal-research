# Phases 4–6 composition — adversarial review

- **Repo** `/home/ubuntu/ztockz` @ `1ffea10` + uncommitted Phase 4/5/6 (`web/src/grid/**`, `web/src/detail/**`,
  `web/src/breadth/**`, `app.tsx`).
- **Stance** every claim below came out of a real Chromium (`chromium-1248`, Playwright-Core driving the
  **production build** served read-only by uvicorn on `:8099`), not from unit tests or reading. Three builders
  built three components separately; this report is only about **seams** — who fetches what, on which
  re-render, and what happens when one of the three is slow or fails.
- **Nothing in the repository was modified.** Probes live in `/tmp/pw/`. A *copy* of `web/` was built in
  `/tmp/offbuild` (features removed) and served on `:5199` to measure the features-on/off cost. `:8099` was
  never bound, stopped or restarted. Scratch servers are stopped.

| | |
|---|---|
| Findings | **8** — 1 High · 2 Medium · 5 Low |
| Named attacks | **(a) yes** (F2) · **(b) no** · **(c) no** · **(d) no** · **(e) no** · **(f) partial** (F6, F7 inert-but-not-trapped; F1 leaves nothing to press) |
| Builder claims reproduced | **6 of 6** (116 keys as 40+40+36, 45/5 675 new highs, 420 px panel / 4 tabs / 43 fields, 40-column cap with a named refusal, `cols=` round-trip, 21 sectors) |
| Builder claims not reproducible | **1 partial** (`app.tsx:18` "the initial bundle did not grow" — true for the panel, false for the batch) |
| Measured, not asserted | 6 interactions × request logs with POST bodies · 68 sampled frames of a symbol storm · 88 columns × 3 rows of a permuted merge · 5 scroll positions · 2 viewports · 6 cold loads |

---

## 1. Findings

Severity: **High** = the reader loses the page or gets a materially wrong answer. **Medium** = a lost round
trip, or state the UI asserts but assistive tech cannot see. **Low** = precision, latency, latent edge.

| # | Sev | Area | File:line | Reproduction | Observed | Expected | One-line fix |
|---|-----|------|----------|--------------|----------|----------|--------------|
| F1 | **High** | failure isolation | `web/src/app.tsx:19` (`React.lazy`), `:264-276` (`<Suspense>` with no boundary) | Intercept `GET /app/assets/index-Bs1Pty2P.js` (the detail chunk) → `404`; load the app; press `Enter` on a row | `#root` is **emptied**: `children.length = 0`, `innerHTML.length = 0`, body text `""`. Grid, breadth, tabs, status line and footer are all gone; a subsequent click on a view tab does nothing. Console: `TypeError: Failed to fetch dynamically imported module`. There is no boundary above it — `main.tsx` mounts `<StrictMode><App/></StrictMode>` with no root boundary | One row click costs the docked column, not the session — the grid keeps working and the slot says "details unavailable · Retry" | Wrap `<DetailPanel>` in the `<ErrorBoundary resetKey={symbol} label="Details">` already imported at `app.tsx:10`, and give the import a one-shot retry |
| F2 | **Medium** | duplicate request | `web/src/detail/useDetail.ts:273` | Open AAPL, NVDA, MSFT in sequence | **3 × `GET /screens?source=tradingview`** — one per symbol, identical URL, no conditional tag, never cached (`snapshotGetCount: 3`, `identical: 1`). It also *precedes* the ownership POST, so the Ownership tab is a two-hop chain (snapshot → select) for every symbol | The snapshot id is a session constant: resolved once, reused | Memoise the snapshot id (module-level promise or a prop from the shell) and drop it from the per-symbol path — one fewer request per row open and one fewer serial round trip |
| F3 | **Medium** | a11y / selection | `web/src/grid/DataTable.tsx:570` | Select a row, dump `Accessibility.getFullAXTree` | `aria-selected="true"` is on the `<tr>`, but the table's role is `table`, not `grid`; the row nodes come back with **no `selected` property at all**. The selected row is visually tinted and programmatically invisible | Selection state announced on the row | Use `aria-current="true"` on the selected `<tr>` (valid inside a `table`), or promote the table to `role="grid"` + `aria-selected` if the row is meant to behave like a grid cursor |
| F4 | Low | dev-harness seam | `web/vite.config.ts:22` vs `web/src/detail/useDetail.ts:265` | `npm run dev`, open a row | `GET /news/AAPL?limit=20` → **404 `text/plain`** ("did you mean to visit /app/news/AAPL?"), while the control `/engine/fundamentals/AAPL` → `200 application/json`. `API_PREFIXES` lists `/screens /market /symbols /engine /health` and omits `/news` | The News tab is readable in dev | Add `"/news"` to `API_PREFIXES` |
| F5 | Low | a11y / landmarks | `web/src/app.tsx:278` | Cold load with no symbol selected; dump the AX tree | An **unnamed `complementary`** landmark is exposed (the empty `<aside id="detail-slot">`: 0 children, 0 px wide). With a symbol open the same landmark is correctly named "Details for AAPL" | No landmark when there is nothing in it | `{symbol ? … : null}` for the placeholder (keep the geometry on the Suspense fallback only) |
| F6 | Low | a11y / keyboard | `web/src/grid/DataTable.tsx:308-313` + `web/src/detail/DetailPanel.tsx:120` | Focus a row, `Enter` (focus stays on the row), `Escape` | The panel closes — but `document.activeElement` is **`BODY`**. The row's own Escape handler calls `event.currentTarget.blur()`, and the panel's document listener stands down on `event.defaultPrevented`, so nothing puts focus back | Focus stays on the row that was selected | Do not `blur()` when a panel is open, or let the panel own Escape and return focus as it already does from inside the panel |
| F7 | Low | a11y / keyboard | `web/src/detail/DetailPanel.tsx:126-130` | Open a row, `Tab` to the top-bar symbol search, press `Escape` | Panel stays open, focus stays in the input. The guard defers to "a focused control outside the panel that owns its own Escape handling" — the search input has no Escape handler | Escape closes the panel (focus is not trapped, so this is not a trap, just an inert key) | Treat the search input as a closeable origin, or blur-and-close |
| F8 | Low | layout stability | `web/src/breadth/BreadthStrip.tsx:103-135` | Delay `/market/breadth` by 3.5 s and watch the grid | Skeleton is **196 px**; resolved strip is **223 px**. The grid row jumps **+28 px** and loses 28 px of height; layout-shift value **0.0125** (total CLS 0.015 — "good", but visible while the strip is slow) | No shift | Reserve the resolved height on the skeleton (`min-h-[223px]`) |

### 1.1 The three most dangerous

**F1 — a 404 on the detail chunk takes the whole application down.**
This is the composition defect the brief was looking for: the shell was carefully built to survive a *render*
fault (`ErrorBoundary` around `ScreenPanel:220` and `AllColumnsPanel:206`) and a *data* fault (every hook
renders its own error state), but the one failure mode that belongs to neither — a lazy chunk that never
arrives — has no boundary at all. Measured: `rootChildren: 0`, `rootHTML: 0`, `bodyText: ""`, and the page
stays dead (clicking `Value` afterwards changes nothing). The trigger is ordinary: a deploy rotates the chunk
hash while a tab is open, and the first row the reader clicks ends the session. The fix is four lines, because
the boundary is already imported in the same file.

**F2 — the snapshot GET is re-sent, identically, on every row open.**
The brief's attack (a) has exactly one hit. Opening three symbols produced three byte-identical
`GET /screens?source=tradingview` calls. It is not a correctness bug — `parseOwnership` filters by symbol — but
it is a per-open round trip that also sits *in front of* the ownership POST, so the tab is a chain of two
requests where one would do, on the one tab a reader opens to compare floats across the symbols they are
clicking through. The snapshot id is a property of the session, not of the symbol.

**F3 — the selected row is invisible to assistive technology.**
`aria-selected` is set on a `<tr>` inside a `table`. Chrome drops it: the AX row carries no `selected`
property. For a keyboard or screen-reader user the grid therefore has no notion of "the row whose details are
open", even though the visual tint and the docked panel both assert one. Every other a11y contract in this
batch held up (see §5); this is the one that silently does nothing.

---

## 2. Task 1 — request behaviour under composition

Real Chrome, `POST` bodies captured, every request between two marks attributed to the interaction that
caused it. Counts are requests, not responses (aborted predecessors counted separately).

| Interaction | `POST /screens/select` | `GET /market/breadth` | Other | Duplicate? | Request for a view already left? |
|---|---|---|---|---|---|
| Cold load | **1** (`limit:100`, 13 cols) | **1** | `/screens/select/schema`, `/health` | no | n/a |
| View switch (Today→Value) | **1** | 0 | — | no | previous POST aborted (cleanup) |
| Sort change | **1** | 0 | — | no | previous POST aborted |
| Filter apply / clear | **1** each | 0 | — | no | previous POST aborted |
| Page turn | **1** | 0 | — | no | no |
| Row open (grid) | **1** (ownership) | 0 | fundamentals, insider, news, `GET /screens?source=tradingview` = **5 total** | **yes — the snapshot GET, per open (F2)** | no |
| Row open (inside all-columns) | **1** | 0 | same four | **yes (F2)** — but **no chunk refetch**: the 3 wide requests stayed at 3, the 20 wide rows and `as-of` untouched | no |
| Breadth retry (after 503) | 0 | **1** | — | no | no |
| All-columns ON | **3** (40 + 40 + 36 columns) | 0 | — | no — three *distinct* bodies | no |
| All-columns OFF | **1** (the view's) | 0 | — | no | the 3 chunk requests are aborted |
| All-columns "Check again" | **3** | 0 | — | no | no |
| Re-`Enter` on the already-selected row | **0** | 0 | — | — | — |
| Two view clicks 120 ms apart | **2** | 0 | — | no; first aborted, final state = last click (`Quality`, rows not stale) | second request is for the view the user is on |

**No fetch loop anywhere.** Breadth is one read per mount plus one per explicit retry (2 total across a
failure and its retry). `useAllColumns` re-runs only on `[signature, attempt, enabled, chunks, view, limit,
sort]` — none of which depend on the selected symbol, which is why opening a row inside wide mode sends zero
chunk requests.

**Aborts are the whole duplicate defence**, and they work: every superseded POST ends `ERR_ABORTED` rather
than racing. The one request that is *identical on purpose and repeated* is F2.

---

## 3. Task 2 — detail panel seams

Probe: fundamentals/insider/news intercepted, delayed 1.1 s, and answered with a symbol-stamped marker
(AAPL `pe = 1001`, NVDA `2002`, MSFT `3003`); a sampler captured `(header, pe)` every 40 ms while the probe
fired `AAPL → NVDA → AAPL` 160 ms apart.

| Check | Result |
|---|---|
| Symbol A's numbers under symbol B's heading | **Not reproducible.** 68 sampled frames across the storm, **0 mismatches**. The `key={symbol}` on `DetailBody` (`DetailPanel.tsx:274`) is doing the work: the old tree is discarded, not patched |
| Unhandled rejection | **None.** `window.__rej` empty, no `pageerror`, no console error |
| Close mid-load | Panel closes, in-flight requests abort, and 1.4 s later **no late answer paints** (`strayText: false` — symbol 3003 never appears after closing MSFT) |
| Re-open after close | Clean: header AAPL, `pe = 1001` — the correct symbol's number, not a cached previous one |
| Focus after close | **Lost to `body`** when Escape is pressed with focus on the row (F6); *correct* — returns to `tr[data-symbol="AAPL"]` — when focus is inside the panel |
| **Lazy chunk 404** | **Blanks the page (F1)** |

Escape, measured from three different focus origins (all with the panel open):

| Focus before Escape | Panel closed | Focus after |
|---|---|---|
| `Fundamentals` tab | yes | `TR[data-symbol="AAPL"]` |
| the resize separator | yes | `TR[data-symbol="AAPL"]` |
| the tab panel body | yes | `TR[data-symbol="AAPL"]` |
| the grid row itself | yes | **`BODY`** (F6) |
| the top-bar search input | **no** (F7) | the input |

---

## 4. Task 3 — all-columns seam

The three chunk POSTs were intercepted and answered by hand, so the merge could be attacked directly.

**(a) Merge by symbol under different completion orders.** Chunk order was permuted *and* chunk 1 was made
the slowest (1 600 ms vs 100 ms / 700 ms):

| Chunk | Rows returned (in this order) | Delay |
|---|---|---|
| 1 (40 keys) | AAPL, NVDA, MSFT | 1 600 ms (**last to answer**) |
| 2 (40 keys) | MSFT, AAPL, NVDA | 100 ms |
| 3 (36 keys) | NVDA, MSFT, AAPL | 700 ms |

Result: row order **AAPL, NVDA, MSFT** (first appearance, not completion order), **88 columns** compared by
parsed magnitude across the three rows (a one-symbol shift would reverse or collapse the ordering), **88
`ordered-ok`, 0 shifted**. Attack (c) fails.

**(b) A failed chunk marks exactly its own columns.** Chunk 2 → `500`:

- header line: `3 of 3 symbols · as of 10:00:00Z · **120 cells absent**` = 3 rows × 40 columns — exactly chunk
  2's width, nothing else;
- notice: `Chunk 2 of 3 failed (HTTP 500: …): its 40 columns are shown as "—" on every row.`;
- the rendered window (`0:17`) contains only chunk-1 columns, and it shows **0** absent cells there — the
  blindness is localised to the chunk that failed.

**(c) A chunk that answers without a symbol** (chunk 2 omits MSFT): `40 cells absent` = 1 row × 40 columns,
notice names the symbol: `Chunk 2 of 3 answered without 1 symbol (MSFT): that row's columns from this chunk
are shown as "—".`

**(d) Pinned lane at both ends** (1440 px, 116 columns, `scrollWidth 8 248`):

| scrollLeft | window | identity cell left | container left | header lane left | aligned? |
|---|---|---|---|---|---|
| 0 | `0:17` | 13 | 13 | 13 | yes |
| 1 650 | `16:39` | 13 | 13 | 13 | yes |
| 3 712 | `42:63` | 13 | 13 | 13 | yes |
| 5 774 | `67:89` | 13 | 13 | 13 | yes |
| 8 233 (max) | `98:115` | 13 | 13 | 13 | yes |

The lane never drifts, the header lane tracks the body lane exactly, and the **last** column (index 115) is
reachable at max scroll — no dead strip at the right edge. Note `scrollWidth` shifts by 15 px between the
first and last measurement (windowing re-lays-out), so "scroll to the computed max" lands 15 px short of the
true end; harmless, and it resolves on the next scroll event.

---

## 5. Task 4 — breadth seam

| Attack | Result |
|---|---|
| **503 from `/market/breadth`** | Strip → `data-state="unavailable"`, "Scanner unavailable … these counts stay blank rather than dropping to zero". The grid is **unaffected**: still mounts, still fetches, 100 rows once the select lands. Attack (d) fails |
| Retry after 503 | **Exactly 1** `GET /market/breadth`, `data-state` back to `ready`, 1 557 shown, no skeleton residue, grid untouched |
| Stale snapshot (`stale: true`) | Badge `[data-testid="breadth-stale"]` renders ("Stale snapshot"), numbers still printed, `data-stale="true"` on the strip |
| Sector with `avg_change_pct: null` | Prints `n/a`, `data-sign="absent"`, neutral ink, and sorts **last** (index 3 of 4). Never `0.00%` |
| Numbers read as text | Yes — AX tree carries `StaticText "1,557"`, `"3,932"`, `"5,678"` as ordinary text, plus an `image` node labelled "Advancing 1,557 versus declining 3,932 over 5,678 scanned; 189 unchanged." |

Live figures reproduced exactly as the parent reported: **Advancing 1 557 · Declining 3 932 · Unchanged 189 ·
Universe 5 678**, new highs **45 / 5 675 reported**, new lows **54 / 5 675**, 21 sector cells, no sum warning.

---

## 6. Task 5 — layout

| Viewport | Document | Panel | Grid | Verdict |
|---|---|---|---|---|
| 1 280 × 900, panel closed | `scrollWidth 1 280 = clientWidth` | — | 13 → 1 259 | fits |
| 1 280 × 900, panel open | **`1 280 = 1 280`, no h-scroll** | 420 px, 848 → 1 268, inside the viewport | 826 px, 13 → 839, **scrolls internally** (`data-overflow="true"`) | all three fit; strip 12 → 1 268 |
| 1 024 × 768, panel open | **`1 024 = 1 024`, no h-scroll** | 420 px, 592 → 1 012 — it does **not** shrink | 570 px, scrolls internally | contract met: the grid scrolls, the document never does |
| 1 440 × 900, all-columns open | `1 440 = 1 440` | — | 8 233 px of columns in a scroll box | fits |

A sweep for elements protruding past `innerWidth` returned **none** at either width. Attack (e) fails.

The one caveat is that the panel is `flex-none` at 420 px with `minWidth: 360` (`DetailPanel.tsx:216-218`), so
it never yields between 360 and 560 px of its own width — at 1 024 px the *grid* absorbs the whole loss (570 px
for 11 columns). That is the allowed branch of the requirement, but it means the panel is the most expensive
thing on a small screen and there is no breakpoint that auto-narrows it.

---

## 7. Task 6 — accessibility

**Landmark order (AX tree, verified against DOM):** `banner` (top bar) → `navigation` "Breadcrumb" (status
line) → `main` → `region` "Market breadth us · stock" → `region` "Sectors" (inside breadth) → `complementary`
"Details for AAPL" → `contentinfo` (footer). One banner, one main, one contentinfo, no nesting violations.
Breadth is a *named* region, so it appears in a landmark menu — good. The closed-state artefact is F5.

**Tab order** (22 presses from `body`, steady state): grid pager `Next` → **panel** (resizer → Close →
Fundamentals tab → tab panel) → `body` → **top bar** search → view tabs → `All columns` → `Filters` →
`Columns` → `Sort by` → `Check again` → **the one tabbable grid row** → `Next` (wrap). So the reading order the
brief asks for holds: top bar → tabs → toolbar → grid (roving `tabIndex`, exactly one row) → panel. The
breadth strip contributes **zero** tab stops, which is right for static text but means a keyboard-only reader
cannot reach the strip's `Retry` after a 503 without going through the whole grid first — the strip has no
focusable element in its happy state and only the failure state adds one, *after* the grid in DOM order.

**Escape:** returns focus to `tr[data-symbol]` from every focus origin inside the panel (§3); fails from the
row itself (F6) and is inert from the search input (F7). No state was found where Escape closes nothing while
focus is inside the panel, and no state where focus is trapped (the panel is `role="complementary"`, not a
dialog, and never traps). Attack (f) is a partial: F1 leaves a page where nothing exists to press, and F6/F7
leave Escape inert in two specific places.

**Breadth numbers as text:** yes. `<dl>/<dt>/<dd>`, plain `StaticText` nodes, values like "1,557" reachable by
Ctrl-F and by a screen reader; the bar is a single `img` with a full sentence, not a canvas.

---

## 8. Task 7 — fresh-load cost

Same machine, same Chromium, 3 cold loads each, medians. "Features off" is a `/tmp` copy of `web/` built with
`BreadthStrip`, `AllColumnsPanel` and the lazy detail removed from `app.tsx` (no repository edit), served from
`:5199` behind a proxy to the same API.

| | shipped | features off | delta |
|---|---|---|---|
| First contentful paint | **408 ms** | 344 ms | **+64 ms** |
| First grid row | **3 448 ms** | 3 183 ms | **+265 ms** |
| DCL | 247 ms | 199 ms | +48 ms |
| API requests before the grid has a row | **4** (schema, health, breadth, select) | 3 | **+1** |
| JS + CSS transferred | 485 748 B | 458 461 B | **+27 287 B** |
| Main chunk on disk | 426 511 B | 399 249 B | **+27 262 B** |
| Extra chunk (detail, lazy) | 49 434 B | none | **+0 on first paint** |

What each feature actually costs before the grid has rows:

- **Breadth** — 1 request and part of the 27 KB; it never blocks the grid (its 503 run still produced 100 rows).
- **All columns** — **0 requests, 0 first-paint bytes beyond the code**: it is opt-in. Turning it on costs 3
  POSTs and measured **5 677 ms to 20 rows** versus **3 448 ms** for the ordinary view — 1.6× the latency for
  a 20-row page, because three 20-row requests run in parallel against the same engine.
- **Detail** — **0 bytes and 0 requests on the first paint** (the 49 KB chunk is separate and only downloaded
  when a row opens). The claim in `app.tsx:18` holds for the panel.

The dominant term in "first row" is not any of the three features: it is `POST /screens/select` (~3.1 s of
server time), which is unchanged by the batch.

---

## 9. Attacks that failed (the list, for the record)

Every one of these was attempted and did not work:

1. **Make the app send the same request twice** — failed for `POST /screens/select` in all six interactions,
   for the three chunk POSTs (distinct bodies, 40/40/36), for the wide "Check again" (3, not 6), for a
   re-`Enter` on the selected row (0), and for a rapid double view-switch (2, first aborted). **Only F2
   (snapshot GET) succeeds.**
2. **Detail panel showing A's numbers under B's heading** — 0 mismatches in 68 sampled frames under a
   160 ms symbol storm with 1.1 s latency.
3. **All-columns merge shifting a row by one symbol** — 88 columns × 3 rows, permuted order, slowest chunk
   answering last: 0 shifted.
4. **A breadth failure taking the grid down** — 503 leaves the strip blank and the grid at 100 rows.
5. **A horizontal scrollbar on the document at 1 280** — none at 1 024, 1 280 or 1 440, panel open or closed;
   no protruding element.
6. **Escape closing nothing / focus trapped** — no trap found; Escape is inert in two specific origins
   (F6, F7) but always closes from inside the panel and never traps focus.

---

## 10. Builder claims: reproduced vs not

| Claim | Verdict |
|---|---|
| "116 keys in three chunks of 40/40/36, merged by symbol" | **Reproduced** — panel reads `3 requests · 40 + 40 + 36 columns`; bodies confirm 40/40/36 distinct column lists, all `limit: 20, offset: 0, sort: market_cap` |
| "breadth ADVANCING 1 557 · DECLINING 3 932 · UNCHANGED 189 · UNIVERSE 5 678, new highs 45/5 675" | **Reproduced exactly**, plus new lows 54/5 675 |
| "row Enter opens 'Details for AAPL' at 420 px, four tabs, 43 FIELDS · 43 REPORTED" | **Reproduced** — `aria-label="Details for AAPL"`, width 420, 4 tabs, "43 fields · 43 reported · as of 2026-06-30" |
| "column picker capped at 40" | **Reproduced** — 116 checkboxes, header `Columns 11 / 40`; clicking past the cap yields `A panel holds at most 40 columns; remove one before adding "row_type".` and the table settles at `40 columns · custom · 100 rows` |
| "a `cols=` URL param" | **Reproduced** — `?cols=value:price,change,pe,beta&view=value` renders exactly `identity, price, change, pe, beta` and `4 columns · custom`; the picker writes the same format back |
| "21 sectors" | **Reproduced** — 21 `sector-cell` nodes, sorted by average, strongest first |
| `app.tsx:18` "the initial bundle did not grow when it was built" | **Partially not reproducible** — true for the detail panel (49 KB stays lazy, 0 bytes on first paint), but the batch as a whole added **27 262 B** to the main chunk, all of it before first paint |

---

## 11. What I could not prove

- **Real network failure of the chunk 404** — I simulated it by intercepting the chunk request and answering
  `404`. That is faithful to what a rotated hash or a dropped CDN response does, but it is not a real deploy.
- **CLS under a slow network** — measured locally with an artificial 3.5 s breadth delay; on a real link the
  shift would be larger, not smaller.
- **The `#detail-slot` width at 1 024 with the panel at 560 px** (its maximum) — I measured the 420 px default
  only; at 560 px the grid would be 430 px wide at 1 024, still scrolling internally, still no document scroll.
- **jsdom-based unit coverage of these seams** — not run here; the 420-test suite belongs to `npm run check`,
  and this report is deliberately real-browser evidence only.
