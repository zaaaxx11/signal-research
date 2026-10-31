# Phase 2 adversarial review — the grid before it is committed

**Repo** `/home/ubuntu/ztockz` @ `997b37c` + the uncommitted Phase 2 tree (`web/src/grid/**`,
`web/src/data/**`, `app/api/screens.py` +13, `tests/test_screen_schema.py` +60) ·
**Review date** 2026-10-31 ·
**Harness** throwaway copy of `web/` at `/tmp/adv-web` (node_modules symlinked) for the Vitest probes;
a live `vite` dev server on **:5178** and the **production `dist/`** served by a throwaway proxy on
**:5179** that forwards `/screens` to the live API; a real headless Chromium driven over CDP for
layout, zoom, sticky and network facts. `:8099` was only ever *called*; it was never restarted, and
nothing was bound to it. No file in the repository was modified except this one.

Gates measured on this tree, before any probe:

| gate | result |
|---|---|
| `cd web && npm run check` | **green** — 122 Vitest tests, `tsc --noEmit`, `vite build`, token audit, budget **346,166 B raw / 107,234 B gzip = 104.7 KB** (58.2 % of 180 KB) |
| `.venv/bin/python -m pytest -q` | **RED — 1 failed, 4,336 passed** (`test_the_schema_route_serves_the_repo_artifact`) |
| `.venv/bin/python -m pytest -q tests/test_screen_schema.py` | 7 passed |

The grid is the best-built thing in this repo. The geometry is honest — I could not break the width
invariant anywhere the builder tested, and I could not make one row show another symbol's numbers.
What I could break is the cache contract (a real 304 under the wrong sort), the solver's behaviour
outside the widths it was designed for (a blank page), and the Python suite (red at HEAD+Phase 2,
which `npm run check` cannot see).

---

## 0. Findings

| # | Sev | Where | What |
|---|---|---|---|
| F1 | **critical** | `web/src/data/screens.ts:272-275,283-295` | The ETag is stored per query but the "may I revalidate?" guard compares only the **view name**. Sort→other→back or page 2→page 1 sends a tag that is valid for the *request* while the rows on screen belong to the *previous* request. The server answers a correct **304** and the grid keeps the other query's rows, permanently, under the new toolbar label. Reproduced against the live API. |
| F2 | **high** | `web/src/grid/solver.ts:144-149,178-181`; `DataTable.tsx:120-123` | For a container 102–113 px wide the shrink branch builds `1 + allocate(negative)` and the invariant check **throws inside render**. There is no error boundary anywhere in `web/src`, so the whole SPA unmounts: `#root` 0 children, 0 characters of body text. |
| F3 | **high** | `app/api/screens.py:526`; `tests/test_screen_selection_api.py:480-492` | The route now returns `{**SELECTION_REQUEST_SCHEMA, "views": …}` while `schemas/stock_selection_request_v1.json` was not touched, so the contract test that asserts they are one document fails. 4,336 passed, **1 failed**; `npm run check` cannot see it. |
| F4 | medium | `web/src/grid/solver.ts:134-138`; `DataTable.tsx:262` | A container narrower than the 101 px fixed lane returns a 101 px table for a 1 px container (`sum === 101`), contradicting the module's stated invariant `sum(widths) === floor(container)`. In the browser: container 93 px, table 101 px, 8 px of horizontal overflow — the one width class the design refuses to scroll at is the one where it silently scrolls. |
| F5 | medium | `web/src/grid/format.ts:43-50` | `formatCell(0.004, money)` → **`$0.00`** with `absent: false`. Any sub-cent price (`price`, `eps`) is printed as exactly zero — a real number rendering as a different number, and the one case the "absent is not zero" doctrine does not cover. |
| F6 | medium | `web/src/grid/cellRenderers.tsx:91`; `solver.ts:144-149` | Below the min-width sum the solver compresses instead of scrolling, and numeric cells `truncate`. At 1024 px the 13-column Value panel clips 10 of 1,300 numbers: `$823,210.89` needs 76 px, gets 56 px, renders `$823,21…` ≈ $823.21 — a 1000× misread. Zero clipped cells at 1280/1440. |
| F7 | medium | `web/src/grid/DataTable.tsx:259` | `aria-rowcount={rows.length + 1}` = **101** on a 5,600-row screen. Screen readers announce a 101-row table and "row 5 of 101"; the true count is 5,601 including the header. It must be `rowCount + 1`. |
| F8 | medium | `web/src/grid/DataTable.tsx:333-357`; `screens.ts:260-265` | A view switch paints one committed frame of the **old** rows under the **new** headers before the effect clears them. Proven at hook level: a render exists with `propView=value`, `stateView=today`, `rows=6`, `status=ready`. Harmless only because no column key is shared between any two panels (§4), so the cells read `—`. |
| F9 | low | `web/src/grid/format.ts:79-85` | `formatCell(0, timestamp)` → **`1970-01-01`**. A `0` sentinel is not treated as absent, so an unknown earnings date becomes a plausible-looking date. The live API does not emit `0` today (100 profile rows: 0 zeros, min 1,793,620,800) — latent. |
| F10 | low | `web/src/grid/format.ts:61-66` | Sign and magnitude disagree at the rounding boundary: `-0.001` → `-0.00%`, `0.004` → `+0.00%` (and `▲ +0.00%` in the cell). A delta of zero shown as rising. |
| F11 | low | `web/src/grid/columnMeta.ts:184-204` | `headerNeed` guarantees the label fits only *at the declared minimum*; once the solver goes below it (any container < min-sum), headers ellipsise. At 1024 measured: `Fwd P/E` needs 57 px / has 53, `EV/EBITDA` needs 74 / has 71. Full label is in `title`, so hover recovers it. |
| F12 | low | `web/src/grid/DataTable.tsx:96-98` | `rowId` is the symbol and nothing dedupes. Two rows with the same symbol both render, with a React duplicate-key error (`Encountered two children with the same key`) — values stay correct, but DOM identity across a reorder is no longer guaranteed. |
| F13 | low | `web/src/grid/format.ts:31-35` | `plain(precision)` **constructs** an `Intl.NumberFormat` per call, twice per money cell (text + title). One ArrowDown re-renders all 1,300 cells → ~2,600 constructions. Measured: **1.0 ms** per key in Chromium (V8 caches formatters) but **36.6 µs/op** in Node → 143 ms for the same work where the cache misses. |
| F14 | low | `web/src/grid/ViewTabs.tsx:60-90` | `role="tablist"` + `role="tab"` with **no `role="tabpanel"`, no `aria-controls`, 0 tabpanels in the DOM** — carried over from Phase 1 F7 and still true of the grid that the tabs control. |

Counts: **1 critical, 2 high, 5 medium, 6 low.** (F14 is a carry-over and is counted once.)

---

## 1. The three that matter

### F1 — a correct 304 for the wrong answer (critical)

`useScreens` keeps a `Map<queryKey, etag>` (`screens.ts:244`) — a good instinct, and it defeats the
naive version of this bug (a filter change gets a new key and therefore no tag). But the decision to
*use* a tag asks the wrong question:

```ts
// web/src/data/screens.ts:272
const etag =
  latest.current.view === view && latest.current.rows.length > 0
    ? etags.current.get(key) ?? null
    : null;
```

`view` is the view **name**. `key` is the whole query — view, columns, limit, offset, sort. So
"same view, some rows, tag for this key" is satisfied while the rows on screen are another key's, and
the `304` branch then keeps whatever `previous` happens to hold (`screens.ts:283-295`), because
`previous.view === view` is also only a view-name test.

Reproduction against the **production build and the live API** (`:5179` → `:8099`), three clicks,
no instrumentation:

```
select sort = Turnover   -> POST {sort:"turnover"}  if-none-match: null  200  rows NVDA,INTC,MU
select sort = Market cap -> POST {sort:"market_cap"} if-none-match: W/"select-ad90…-a1bb…"  304
```

Observed, after the 304:

| element | value |
|---|---|
| sort selector | **Market cap** |
| badge | **not refreshed · 304** |
| first three rows | **NVDA, INTC, MU** (turnover order; market-cap order is AAPL, NVDA, MSFT) |
| panel header | Today |

The same desync on paging, which is worse because it also lies about position:

```
Next  -> POST {offset:100} if-none-match: null  200  rows ADI,APH   range 101–200
Prev  -> POST {offset:0}   if-none-match: W/"select-ad90…-a1bb…"  304
```

Observed: pager **1/56** with **Prev disabled**, rows **ADI, APH**, range **101–200 of 5,600**. The
footer can never be made consistent again without a reload: `Check again` re-sends the same poisoned
tag and gets another 304.

There is no filter control yet (`filters: []` is hardcoded at `screens.ts:141`), so sort and page are
the two reachable instances today. Any filter that later reaches the request body without reaching
`queryKey` would be a third, and the same guard would wave it through.

Why the tag itself is not wrong: `app/api/screens.py:169-184` hashes `(data revision, canonical
request)`, and I verified the server behaviour directly — same body revalidates (`304`), changed
`sort` does not (`200`, 31,685 B). The server is doing exactly what it promised. The client is
holding the wrong body under a valid tag.

Expected: a `304` may only be honoured when the rows on screen were the answer to **this** request.
Fix (one line of state, not a redesign): keep the key alongside the rows —

```ts
// 304 branch + etag guard: replace `latest.current.view === view` with
latest.current.queryKey === key && latest.current.rows.length > 0
```

— and set `queryKey: key` in the `setState` that stores a 200. If the key moved, the answer is `200`:
the grid is asking about rows it is not showing.

### F2 — the solver throws, and the whole app goes blank (high)

`solveWidths` has three regimes, and only the middle one is finished:

| container (clientWidth) | branch | result |
|---|---|---|
| ≤ 101 | `available <= 0`, `solver.ts:134-138` | returns the 101 px lane, flexible columns 0 → `sum = 101 ≠ total`. No error, no fallback. |
| 102 … 101+n | `solver.ts:144-149` | `1 + allocate(max(0, available - flexible.length))` → `sum = 101 + n ≠ total` → **throws** at `solver.ts:178-181`. |
| ≥ 102+n | proportional shrink, then normal allocation | exact. |

Reproduced in the production build, one style write, no dev tools:

```js
const s = document.querySelector('[data-testid=grid-scroll]');
s.style.flex = 'none'; s.style.width = '125px';   // clientWidth 110
```

```
Uncaught Error: widths sum to 112, container is 110
#root children: 0     document.body.innerText.length: 0
```

The entire shell is gone — top bar, tabs, status line, footer — not just the table. `grep -rn
'ErrorBoundary|componentDidCatch|getDerivedStateFromError' web/src` returns nothing, so a throw in
`DataTable`'s `useMemo` (`DataTable.tsx:120-123`) reaches the React root and unmounts it. The tab is
blank white-on-black with no message and no way back except a reload.

Reachability is narrow but not contrived: `clientWidth 102-113` is a CSS width of 117–128 px (the
15 px gutter), which a 400 % zoom on a 500 px window, a future split pane, or an embedded iframe
produces. jsdom reproduction, no browser:

```ts
expect(() => solveView(TODAY_COLUMNS, 105)).toThrow('widths sum to 112, container is 105');
expect(solveView(TODAY_COLUMNS, 1).reduce((a, b) => a + b, 0)).toBe(101); // container is 1
```

Expected: `solveWidths` is arithmetic and must never throw on a positive container; the table should
either clamp (every column 1 px) or refuse to render *the table* with a stated fallback.

Fix: replace the throw with a clamp — when `available < flexible.length`, give every flexible column
1 px and accept `sum = 101 + n` as the documented "below the floor" state, and give it the same
horizontal scroll the `all` view is planned to have. Independently: put an error boundary around
`<ScreenPanel>` so a render bug can never take the shell with it.

### F3 — the Python suite is red and the JS gate cannot see it (high)

`selection_schema` now returns a superset:

```python
# app/api/screens.py:526
return {**SELECTION_REQUEST_SCHEMA, "views": view_catalogue()}
```

Its docstring argues, correctly, that `views` is presentation metadata and must stay **out** of
`schemas/stock_selection_request_v1.json`. But the existing contract test asserts the opposite of
that decision:

```
$ .venv/bin/python -m pytest -q
FAILED tests/test_screen_selection_api.py::test_the_schema_route_serves_the_repo_artifact
E  AssertionError: the served schema and schemas/stock_selection_request_v1.json must be one document
E    Left contains 1 more item: {'views': [...]}
1 failed, 4336 passed in 148.33s
```

`npm run check` runs only `web/`, so the Phase 2 gate is green while the repository is not. Either
the decision or the test has to move; a served document and a published artifact cannot both be "the
one document" and differ.

Fix: `assert {**served, "views": None} == {**artifact, "views": None}` (or compare after
`served.pop("views")`), and add a separate test that `served["views"] == view_catalogue()` so the
superset is intentional rather than tolerated.

---

## 2. The numbers — every hostile input, and what it printed

`formatCell(value, spec)` over the kinds a view actually uses (measured, not inferred):

| input | kind | output | verdict |
|---|---|---|---|
| `0` | money | `$0.00` | correct — a real zero is not the placeholder |
| `-0` | money | `$0.00` | correct (no `-$0.00`) |
| `null` / `undefined` / `""` / `"   "` | money | `—`, `absent: true` | correct |
| `NaN` / `±Infinity` | money | `—`, `absent: true` | defensible (documented as absent) |
| `"0"` | money | `$0.00` | correct |
| `"1234.5"` | money | `$1,234.50` | coerced, contradicting `format.ts:164-168` ("a value whose type does not match its column is shown as text") |
| `"1,234.5"`, `"N/A"` | money | `1,234.5`, `N/A` (as text) | correct |
| `true` | money | `$1.00` | `Number(true)` — absurd but not reachable from JSON |
| `[]` | money | `$0.00` | **an absent-shaped value printed as zero** — the one thing the module swears it will not do |
| `{}` | money | `[object Object]` | ugly, not a lie |
| `1e-7`, `0.004` | money | `$0.00` | **F5** — a non-zero price printed as zero |
| `1e15` | money | `$1,000,000,000,000,000.00` | fits nowhere; ellipsised at any real width (title recovers it) |
| `1e15`, `1e-7` | compactMoney | `$1000T`, `$0` | `$0` for a non-zero cap — same class as F5 |
| `0`, `-0` | delta | `0.00%`, sign 0 | correct |
| `-0.001` | delta | `-0.00%`, sign −1 | **F10** |
| `0.004` | delta | `+0.00%`, sign +1 | **F10** (renders `▲ +0.00%`) |
| `999999` | integer | `1M` | Intl rounding; acceptable for volume, would be wrong for a count |
| `0` | timestamp | `1970-01-01`, `absent: false` | **F9** |
| `1704067200` / `1704067200000` | timestamp | `2024-01-01` both | correct — unit ambiguity handled |
| `"1704067200"` | timestamp | `2024-01-01` | coerced by `Number()` |
| `1e15` | timestamp | `+033658-09` | junk string, not a crash |

The two invariants the brief asked about hold, in jsdom and in the browser: **a market cap of `0`
renders `$0`/`$0.00`, and `null` renders `—`, for every numeric kind.** The failures are at the
edges of *precision* (sub-cent), not at the absent/zero boundary.

---

## 3. The solver — the widths it was never asked about

`sum(widths) === floor(container)` holds exactly wherever the builder tested, and in more places than
they claimed. Measured `solveView(view.columns, container)`:

| container | today (11 cols) |
|---|---|
| 1 | `sum = 101` — **invariant broken** (F4) |
| 50 / 100 / 101 | `101, 0, 0 …` — **invariant broken**, table is the lane |
| 102 / 105 / 110 / 112 | **throws** `widths sum to 112, container is …` (F2) |
| 113 | `101,1,1,1,1,2,1,1,1,1,1,1` — exact: ten 1 px columns and one 2 px |
| 114 … 926 | exact; 128 → `101,2,2,3,2,3,2,2,2,3,3,3` |
| 927 (= min-sum) | exact, every column at its `min` |
| 928+ | exact, proportional |
| 1279.6 | `sum = 1279` — floors, never exceeds |
| 1280.4 | `sum = 1280` |
| 1000.6 | `sum = 1000` |

A container below the min-sum does not overflow and does not warn: it compresses every column
proportionally, below the floor the table declares. Measured in the browser — the Value panel's
smallest column is 56 px at 1024 against declared mins of 58–100 px, and 20 px at 431. That is a
fallback nobody wrote down, and it is where the numbers start to clip (F6).

Fractional containers in a real browser (DPR 1, CSS widths set directly, `clientWidth` is what the
component reads): 1123.5 → 1109, 1000.5 → 986, 999.5 → 985, 1279.5/1279.6 → 1265, 777.7 → 763,
431.25 → 416 — **`scrollWidth === clientWidth` at every one, overflow 0**. Chrome rounds
`clientWidth` *down*, so the `floor()` is on the safe side of the rounding. I could not produce a
1 px overflow at any fractional width, at any of 110/125/150/200 % zoom.

Browser zoom (CSS `zoom` on the root, production build, Value panel):

| zoom | clientWidth | sum(widths) | overflowX |
|---|---|---|---|
| 1.00 | 1239 | 1239 | 0 |
| 1.10 | 1111 | 1111 | 0 |
| 1.25 | 974 | 974 | 0 |
| 1.50 | 808 | 808 | 0 |
| 2.00 | 599 | 599 | 0 |

Nine views × four widths, production build, every combination: `sum === clientWidth`, `overflowX ===
0`. The builder's numbers reproduce exactly — **1239 / 1399 / 1879 at 1280 / 1440 / 1920**, and 983 at
1024.

But "sums exactly" is not the same as "reads correctly", and that is where the solver's honesty ends:

- below the min-sum it **compresses instead of scrolling**, which the design never states as a
  fallback and which clips numbers (F6);
- between 102–113 it **throws** (F2);
- below 101 it **overflows silently** (F4).

---

## 4. Symbols and identity

Everything the brief asked to break here held, with one opening:

| attack | result |
|---|---|
| 100 rows shuffled, each row must show its own symbol's price | **passed** — every one of 100 rows matched its own price; order rendered is order given |
| two rows with the same symbol, different prices | both render, values correct, **React duplicate-key error** (F12) |
| a symbol the client already shows, returned again | **replaced, not merged** — `setState({ rows })` swaps the array; 1 row → 2 rows → no duplicate |
| a symbol with a 200-character company name | **passed** — lane stays 101 px, the name is only in `title` (207 chars) and an `.sr-only` span; jsdom cannot prove the *visual* fit, the browser column width can |
| rows identified by position anywhere | **none found** — `getRowId` is the symbol, `selectedSymbol === symbol`, `data-symbol` per row |
| pinned lane reordering cells under the wrong header | **did not happen** — browser spot check on AAPL/Value: `$5.03T, $5.07T, 39.51, 36.51, 1.23, 10.91, 46.83, 81.60` against API `market_cap 5.0296e12, ev 5.0665e12, pe 39.507, forward_pe 36.51, peg 1.2266, ps 10.908, pb 46.826` — same order as the headers |

The one opening is F8: after a view switch there is a committed render where the rows still belong to
the previous view. It cannot show another company's *numbers* — and the reason is worth recording:
**no column key is shared between any two of the nine panels** (checked programmatically over
`schema["views"]`), so every data cell in that frame is `undefined` → `—`. The user sees the old
symbols with a wall of em-dashes under the new headers for one frame. Cheap to remove: render
`rows={screens.view === view.name ? screens.rows : []}`.

---

## 5. The 304 path, in full

| question | answer | evidence |
|---|---|---|
| is the ETag stored after a 304? | **yes** | second `Check again` sends the same `if-none-match` and gets 304 again (prod + jsdom) |
| does a subsequent *different* query send the old tag? | **no** — the map is per key | sort change: `if-none-match: null`, 200 |
| does returning to a **previous** query send the old tag? | **yes, and that is the bug** | market_cap → turnover → market_cap sends the market_cap tag (F1) |
| is that tag valid for the request? | yes — the server is right to answer 304 | `curl` with the same tag but `sort: turnover` → 200 |
| does the stale badge clear on the next 200? | **yes** | jsdom: refresh after the data moves → `revalidated: false`, new etag stored |
| does a 304 clear the grid? | no | 100 rows kept, badge `not refreshed · 304` |
| what re-enables the grid after F1? | only a reload | `Check again` re-sends the poisoned tag |

The bug is therefore not "the ETag is wrong" but "the *body* is not keyed with the tag". The `Map` is
the right data structure with the wrong accessor.

---

## 6. Abort and race

Three fast view switches, responses resolved in reverse order (last first, then the stragglers):

```
after stragglers: { view: 'value', rows: 'G1,G2', status: 'ready' }
pending[0].aborted: true   pending[1].aborted: true
```

**The last view wins, no earlier response writes state, and the superseded requests are genuinely
aborted.** This is the part of the module I could not break.

Aborting does not become an error: a request rejected with `DOMException: AbortError` leaves
`status: 'loading'` (it is the *replaced* request), and a real transport failure surfaces as
`status: 'error'`, `error: {status: 0, message: 'Failed to fetch'}` on the first attempt — both as
designed.

---

## 7. Accessibility and semantics

| property | observed | verdict |
|---|---|---|
| real `<table>` | `TABLE` implicit role, `aria-label="Today screen"` | ✓ |
| header cells carry `scope` | 12/12 `th[scope="col"]` | ✓ |
| pinned lane keeps its header | sticky `th` at `top:0; left:0`; after `scrollTop 400` + `scrollLeft 250` the header's top equals the container's top and the lane's left equals the container's left | ✓ (real browser) |
| `aria-sort` | absent on all 12, correctly — sorting is a toolbar choice, not a column action | ✓ by design |
| tab stops for 100 rows | **1** (`tabIndex` 0 on one row, −1 on 99); ArrowDown moves it; Enter activates; Escape clears and blurs | ✓ roving focus |
| null placeholder accessible name | `<span data-null aria-label="Not available">—</span>`, 306 of them in the 100-row today fixture | ✓ |
| delta spoken form | `aria-hidden` arrow + `.sr-only` "down " + `-1.20%` | ✓ sign is never colour-only |
| `aria-rowcount` | **101** for a 5,600-row screen | ✗ F7 |
| `role="tab"` → panel | 0 `tabpanel`s, 0 `aria-controls` | ✗ F14 (carry-over) |
| duplicate header labels | none at 1280 in any of the nine panels; two labels ellipsise at 1024 | F11 |

Typography as claimed: `td` 12 px / 17.4 px line-height, `th` 12 px mono, row pitch **28 px**, header
**24 px**, identity lane **101 px**, `scrollbar-gutter: stable` computed on the scroll box.

---

## 8. Bundle and render cost

- **Initial JS: 346,166 B raw / 107,234 B gzip = 104.7 KB** — the claim reproduces exactly
  (`npm run budget`, `gzip -9`, entry + modulepreload only). CSS adds 53,717 B / 9,986 B gzip and is
  printed but not gated (Phase 1 F9, unchanged).
- 100 rows × 12 columns (today) measured in Chromium: **1,200 `<td>` and 3,640 elements**; the
  13-column panel is 1,300 `<td>` (measured in jsdom). No virtualisation.
- One `ArrowDown` in the production build: **1.0 ms** (12 presses: 0.8–1.4 ms). The same key in
  jsdom over the 13-column panel: 3 React commits and **1,300 `formatCell` calls** — every cell
  re-rendered and re-formatted, and ~2,600 `Intl.NumberFormat` constructions, because `plain()` is a
  factory (`format.ts:31`). There *is* a per-cell re-render storm — nothing is memoised — but at
  100×13 Chromium absorbs it. The latent cost is real where V8's formatter cache does not help: the same
  3,900 constructions take **143 ms** in Node (36.6 µs/op), and the `all` view (116 columns × 100
  rows = 11,700 cells) is 9× the work whenever Phase 4 mounts it. Memoising `plain()` in a
  `Map<number, Intl.NumberFormat>` is two lines and removes the entire allocation axis.

---

## 9. Claims: reproduced, not reproduced, not provable here

**Reproduced exactly**

- 100 rows × 12–13 data columns; widths sum to the container at 1280/1440/1920 → **1239 / 1399 /
  1879 px, zero overflow**; also zero at 1024 and under 110/125/150/200 % zoom and at nine fractional
  container widths.
- Row pitch 28, header 24, identity lane 101, type 12 px.
- Sticky header verified at `scrollTop 400` (and the lane at `scrollLeft 250`).
- 304 keeps 100 rows and shows `not refreshed · 304`.
- Keyboard Enter/Escape (and arrows, Home/End, PageUp/Down) with one roving tab stop.
- Nine views, all summing exactly at four widths.
- Bundle **104.7 KB gzip**; **122 Vitest tests**; `npm run check` green.
- Decision (c): `scrollbar-gutter: stable` is load-bearing — the scroll box is 1254 px wide and
  `clientWidth` 1239 at a 1280 viewport; without the reservation the first solve would be 15 px too
  wide.
- Decision (d): `all` is 116 columns, over the 40-column cap, and is not a tab; `ScreenPanel.tsx:62`
  refuses it with an alert rather than sending a request that would 422.
- Decision (a): requesting `logo` really does blow the budget — `today + logo` returned **71 rows**
  (`rows_dropped: 29`, `bytes: 56195` of 60000) against 100 rows at 31,430 B without it. The monogram
  is a forced choice, not a preference.
- Decision (b): no column is sortable (`enableSorting: false` on all, `aria-sort` absent); the three
  API sorts are all the engine has.

**Not reproduced as stated**

- **"13–14 column views":** the widest panel is `value` at **13** data columns (14 `<col>`s with the
  identity lane); no panel has 14. `all` has 116 and is excluded.
- **"32 python tests":** `tests/test_screen_schema.py` collects **7**; the full suite is **4,337**
  with **1 failing** (F3). Whatever the 32 was, it is not a gate this tree passes.
- **"~66 rows" with logos:** it is **71** today (the number depends on logo URL length; the
  conclusion is unchanged).

**Could not be proved in jsdom (and was therefore proved in Chromium or left open)**

- Anything geometric: jsdom has no layout, so `scrollWidth`/`clientWidth`, ellipsis and sticky are
  CSS-contract assertions there. All of §3 and §7's layout rows come from the headless browser.
- Whether the F8 stale frame is *painted*: React's passive effects run after paint, so it should be,
  but I can only prove the commit exists, not the paint. The hook-level frame log is the evidence.
- Server-side latency is 12.5 s for a 100-row live scan on this box — every view switch pays it. Not a
  grid defect, but it means the stale frame is not "one frame before data arrives"; it is one frame
  before the *empty* state, which then lasts 12 s.

---

## 10. Method

- Throwaway Vitest files in a `/tmp/adv-web` copy (`src/grid/zz-adv-{probe,format,table,cost}.test.ts*`,
  `src/data/zz-adv-{etag,race}.test.tsx`), all green, none in the repository. They use the real
  `useScreens`, a `fetch` stub that reproduces the server's `(revision, request)` tagging exactly
  (verified against `curl`), and the repo's own 100-row fixture.
- Production build (`web/dist`, already built by `npm run check`) served at `/app` by a proxy that
  forwards `/screens` to `:8099` — so the cache finding is not a mock artefact. All API calls were
  reads; `:8099` was never restarted and nothing was bound to it.
- Headless Chromium over CDP for geometry, zoom, sticky, `getComputedStyle` and the network log.
- Cleanup: `/tmp/adv-web`, `/tmp/serve_dist.py`, the `:5178` dev server and the `:5179` proxy are
  throwaway; the API process was not touched.
