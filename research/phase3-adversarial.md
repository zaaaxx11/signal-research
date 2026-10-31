# Phase 3 filter surface — adversarial review

- **Repo** `/home/ubuntu/ztockz` @ `7855b5d` + uncommitted Phase 3 (`web/src/filters/**`, `app.tsx`, `grid/ScreenPanel.tsx`)
- **Stance** every claim below was produced by running something: live `POST /screens/select` against the
  read-only `:8099`, Vitest against the client modules, or both. A `200` was never accepted as proof of meaning.
- **Nothing in the repository was modified.** Probe files under `web/src/filters/__probe__/` were deleted after
  the run; `git status` is back to the three entries it started with (plus this report).

| | |
|---|---|
| Findings | **12** — 2 High · 5 Medium · 5 Low |
| Builder claims reproduced | **7 of 7** (240 tests, 116 KB, 50/0, 35 buckets 200, AND, OR refusal, dead-key notice) |
| Builder claims not reproducible | **0** |
| Named attacks | **(a) yes** (F1, F2) · **(b) yes** (F6, F8) · (c) no · (d) partial (F3: stuck, not looping) · (e) no · (f) no |
| Live proof set | 35 buckets × ~200 sampled rows (6 899 row-checks), 315 symbol/bucket membership checks, 30 boundary probes, 11 hostile bodies |

---

## 1. Findings

Severity: **High** = the reader gets a materially wrong answer set with no signal. **Medium** = the surface
misdescribes itself, traps the user, or blocks a supported interaction. **Low** = precision, wording, latent edge.

| # | Sev | Area | File:line | Reproduction | Observed | Expected | One-line fix |
|---|-----|------|----------|--------------|----------|----------|--------------|
| F1 | **High** | units/meaning | `web/src/filters/presets.ts:107` (`u10`, and `:110` `10–20`, `:120` `20–40`) | `POST /screens/select` `{"criteria":{"filters":[{"key":"fa_pe","op":"lt","value":10}]}}` → 2 082 rows. Of those, `fa_pe lt 0` = **1 367** (INTC −31.46, GILD −64.41, SNOW −96.56, NET −483.97 at the top of the page); `fa_pe gte 0 & lt 10` = **715**. F (`pe` −6.55) is a member of `Under 10` and not a member of the 0–10 band | 1 367 of the 2 082 rows (**66 %**) are loss-makers with a *negative* P/E; the bucket is labelled `Under 10` and the chip reads `P/E < 10` | A "cheap earnings" bucket contains companies that have no earnings; the honest population for "P/E under 10" is 715 | Add `{key:"fa_pe", op:"gte", value:0}` to every `Under X` P/E bucket (the backend already models this as the `fa_pe` option `profitable`) |
| F2 | **High** | units/meaning | `web/src/filters/presets.ts:133` (`u1`) + `:127-152` (group) | `fa_div lt 1` = **410**; `fa_div gte 0` (any reported yield) = **2 203**; `fa_div eq 0` = **78**. A null-yield lookup: TSLA and SOFI carry `dividend_yield: null` and are **not** members of `Under 1%`; the 78 rows that report exactly `0.0` **are** | Two companies that both pay nothing land on opposite sides of `Under 1%`, decided by whether the provider wrote `0.0` or omitted the cell. 3 373 of 5 576 US rows (60 %) are in none of the four dividend buckets, so `Under 1%` = 410 rows while the label implies 3 783 | Non-payers are "under 1 %" by definition; at minimum the bucket must say it only counts *reported* yields | Label the group `Dividend yield (reported)` and the bucket `Under 1 % (reported)`, and add a `Pays nothing (0.0)` bucket on `fa_div eq 0` so the inconsistency is visible |
| F3 | **Medium** | URL state | `web/src/filters/urlState.ts:115-117`; rendered at `web/src/grid/ScreenPanel.tsx:285` | Open `/app/?page=999999999999999` | `page` is accepted verbatim (`999 999 999 999 998`); the POST carries `offset: 99999999999999800`; the API answers `200` with 0 rows (`total 5576`); the footer reads `999999999999999/56`, the range is `—`, `Next` is disabled and `Prev` decrements by one. The URL effect writes the poison back, so a reload restores it. Only switching view or sort escapes | Page clamped to `pageCount` (or refused with a notice), pager inside `1…56` | Clamp in `parseScreenState` (page ≤ 10 000) and reconcile `page` against `meta.total` once the first answer lands |
| F4 | **Medium** | a11y | `web/src/filters/FilterDrawer.tsx:151-163` | Render the drawer with one sibling button before and one after it, then `user.tab()` 140× | Focus leaves the dialog on tab **43** (`BUTTON outside after` → `BODY` → `BUTTON outside before`) and keeps cycling through the page behind. `aria-modal` is absent, so AT users are not told the rest is inert | `dialog.contains(document.activeElement)` on every tab step; `aria-modal="true"` | Wrap the panel in a focus trap (or Radix `Dialog`) and set `aria-modal` |
| F5 | **Medium** | a11y | `web/src/filters/FilterDrawer.tsx:157-162` + `web/src/app.tsx:84-87` (`closeDrawer`) | Focus the toolbar `Filters` button, press it, press Escape | Drawer closes correctly and applies nothing (good), but `document.activeElement` is `BODY`; the keyboard user loses their place in a 60-control panel | Focus returns to the opener | Capture `document.activeElement` on open, restore it in `closeDrawer` |
| F6 | **Medium** | state truth | `web/src/filters/state.ts:133` (`if (typeof item === "string") return item`) | `<ChipBar filters={[{key:"ta_perf",op:"eq",value:"1w10u"}]} />` | Chip reads **`Performance = 1w10u`**; the drawer's `<option>` for the same token reads `1W down 10 %+`. The two never agree, and the chip is unreadable without reopening the drawer | The chip names the option the way the control does | `formatValue` should consult `registry.byKey.get(key).options.find(o => o.token === value)?.label ?? value` before falling through to the raw string |
| F7 | **Medium** | units | `web/src/filters/registry.ts:120-122` + `presets.ts:246-248` (`UNITS` is built from `PRESETS` only) | `<ChipBar filters={[{key:"sh_short",op:"gte",value:5}]} />`; same for `targetprice` | `Short Float ≥ 5` (a percentage column) and `Target Price ≥ 200` (dollars) render as bare numbers. 40 of the 50 usable keys have no unit at all, so the chip is ambiguous exactly where the request is not | `5%` / `$200` | Derive a unit per key from a small `column → unit` table (percent for the margin/ownership/yield columns, usd for price-like columns) instead of from the preset list |
| F8 | **Low** | state truth | `web/src/filters/state.ts:112-115` (`trimNumber` → 4 dp) | Two chips: `cap lt 1000000000` and `cap lt 1000040000` (also `ta_beta gte 0.8` vs `gte 0.80004`) | Both render **`< $1B`** (`< $1B` twice) / **`≥ 0.8`** twice: two different requests, one identical chip. A reader comparing chips cannot tell them apart | Distinct requests must render distinctly | Increase precision for compact/ratio units (`toPrecision(6)` under 10) or append the exact value in the accessible name |
| F9 | **Low** | units/meaning | `web/src/filters/presets.ts:46,72,98,124,150,168,186,204,222,240` (every `Over …` bucket) | `cap gte 1999003845` = 2 105 vs `cap gt 1999003845` = 2 104 (SAH's exact cap) — `gte` includes its bound; every `Over X` bucket sends `gte` | `Over $200B`, `Over 10M`, `Over 100`, `Over 40`, `Over 6 %`, `Over 1.2`, `Over 2×`, `Over 15 %` all include a row sitting exactly on X. Not observable today (no row sits on any bound: `cap eq 200e9`, `price eq 100`, `rsi eq 70`, … all return 0), so it is latent | "Over" is strict: `gt` | Use `gt` for `Over …`, or relabel to `$200B and up` |
| F10 | **Low** | state | `web/src/filters/FilterDrawer.tsx:11` ("closing the drawer cannot lose an edit") vs `:99` (`if (!open) return null`) | Select `P/E`, type `12` in the row, press Escape, reopen | The half-built rule is gone. `ConditionBuilder`'s rows are component state and the whole subtree unmounts with the drawer | A pending row survives a close, or the drawer says it will not | Lift `rows` into the drawer/shell, or correct the docstring and warn on close with a non-empty builder |
| F11 | **Low** | a11y | `web/src/filters/FilterDrawer.tsx:236-249` | Add `cap gte 2e9` and `cap lt 10e9`, open the drawer | The "On screen" list shows two rows both reading `Market Cap.` and two buttons both named `Remove Market Cap.` — no value, no distinguishing name, while the chip bar above distinguishes them | Each row names its predicate | Render `describeFilter(...)` text in the row and reuse it in the `aria-label` |
| F12 | **Low** | a11y | `web/src/app.tsx:144` and `:179` (same `notice` passed to `ChipBar` and `FilterDrawer`) | Open a link with `f=idx:eq:sp500`, then open the drawer | The same sentence exists in two `role="status"` regions simultaneously (announced twice), and it survives every later filter change until dismissed | One live region; cleared when the state it describes changes | Pass `notice` to the chip bar only, and clear `dropped` on the first state mutation |

### 1.1 The three most dangerous

**F1 — "P/E under 10" is two-thirds loss-makers (1 367 / 2 082).**
The preset's own docstring is about not inventing Finviz tokens; the token it did invent is the missing
`profitable` guard. A reader who clicks `Under 10` to find cheap stocks gets a list dominated by companies with
negative earnings — and every row is a legitimate `pe < 10`, so nothing in the UI can look wrong. The number that
makes it undeniable: `fa_pe gte 0 AND lt 10` = **715**, i.e. 66 % of the answer is the opposite of the label's
intent. Fix is one predicate, and the backend already publishes `profitable` as a first-class `fa_pe` option.

**F2 — `Under 1 %` hides 90 % of the stocks it names, and splits identical companies across the boundary.**
410 rows are shown; 3 373 more rows have no reported yield. Two non-payers — one whose cell is `0.0` (78 such
rows, included) and one whose cell is null (TSLA, excluded) — are treated as different economic facts. Because
the four buckets are a radio group with no "pays nothing" option, the panel silently asserts that 3 373 stocks
are not under 1 %. This is the brief's failure mode in its purest form: accepted, returns rows, means something
else.

**F3 — a shared link can pin the panel to page 999 999 999 999 999.**
`page` is a free integer, the offset is multiplied and sent, the API answers `200` with an empty page, and the
client renders `999999999999999/56` with `Next` disabled. The URL is re-written with the poison, so a reload
restores it, and the only escape is to notice that switching view resets the page. No 422 loop — but an
unrecoverable-looking state from a link, which is the outcome the "never loop 422" rule was meant to prevent.

---

## 2. Builder claims — verification

| Claim | Verdict | Evidence |
|---|---|---|
| Registry offers exactly 50 keys, zero refused | **Reproduced** | `buildRegistry(parseFilterSchema(GET /screens/select/schema))` → `entries.length 50`, `usableKeys 50`, `refused 32`; 47 `{field,kind}` + 3 `{choice}` |
| 35 preset buckets across 10 groups, all 200 live | **Reproduced** | 35/35 `200`; `cap lt 2e9` → **3 472**, matching the claim exactly |
| Same-key filters are ANDed | **Reproduced** | `cap gte 2e9` + `cap lt 10e9` → **1 126** (identical to the `$2–10B` bucket); contradictory pair → **0** |
| Cross-field OR refused with a message, not a 422 | **Reproduced** | `compileGroup` throws `or_across_fields: Can't OR Market Cap. and Sector: …`; numeric OR throws `or_operator`; neither reaches `fetch` |
| A shared URL with a dead key degrades with a notice | **Reproduced** | `?f=idx:eq:sp500` → `dropped ["idx"]`, `Ignored 1 filter from this link: idx — this build cannot apply it.`, URL canonicalized to drop it |
| 240 tests | **Reproduced** | `npx vitest run` → `240 passed | 2 skipped (242)` |
| Bundle 116 KB | **Reproduced** | `npm run build` (tsc + vite green) → `118 764 B gzip`; `npm run budget` → `116.0 KB ≤ 180 KB` |

No claim failed. One caveat the builder should own: the live preset test
(`presets.live.test.ts:73-77`) asserts only `status === 200` and `typeof total === "number"`. It is exactly the
test the brief warns about — it would pass for a bucket that sent millions where the API wanted dollars, and it
passes today for F1 and F2.

---

## 3. Attacks that failed (and why they failed)

These are the ones I could not make succeed; each is a proof, not an impression.

1. **Wrong unit on any preset key.** Every one of the 35 buckets was re-run with its own column projected
   (`columns: ["symbol", <column>]`), up to 200 rows sampled per bucket (first page + last page), and every row checked
   against the bucket's predicate: **0 violations in 6 899 row-checks**. Independently, a 9-symbol panel
   (AAPL, NVDA, F, KO, TSLA, SAH, XOM, SOFI, GOOGM) was checked for membership per bucket against its own row
   value: **315/315 agreements**. Units confirmed from the data: `market_cap_usd` in dollars (AAPL 5.03e12),
   `volume`/`avg_volume_30d` in shares (NVDA 168 384 547), `dividend_yield` in percent (F 4.90, not 0.049),
   `roe` in percent (AAPL 148.75), `price` in dollars. `cap < 2e9` is *not* a millions/dollars error.
2. **A bucket that returns rows outside its label.** Partitions where the buckets claim to be exhaustive are
   exact: cap 3 472+1 126+879+99 = **5 576** = universe; price 1 563+1 634+1 669+710 = 5 576; volume
   3 028+1 370+933+245 = 5 576. For those three keys no row is in two buckets and none is in none. (`between` is inclusive on both
   ends — `[5,20]` = 1 637 vs `gte 5 & lt 20` = 1 634 — which is exactly why the presets use two predicates.)
   The gaps that do exist are null cells (F2, and 1 055 null P/E, 894 null ROE), not boundary errors.
3. **Reaching a refused key (`idx`, `theme`, `sh_opt`, …).** Not through the drawer's `<select>` (no `<option
   value="idx">`), not through ⌘K (`matchFilters(registry,"idx"|"Index"|"index membership")` → `[]`), not through
   the URL (`dropped`), not through `validateDraft` (refused with the schema's own reason). Aliases pointing at a
   refused field are dropped at registry build time (`alias.targetKey` requires a usable `byField`).
4. **A 422 from a poisoned URL.** Eleven hostile bodies were tried against the live API (object value, array on a
   scalar op, `NaN`/`Infinity` strings, hex, a 10 000-digit number, a 10 000-char string, an inverted range, a
   boolean, 5 000-element `in`, 5 000 filters). The API 422s on five of them (10 k-digit number, object
   value, boolean, `NaN`, `Infinity`) — and the client drops all of those *before* the request, because
   `toFiniteNumber` rejects anything that is not a finite JS number, so what leaves the client is always a JSON
   number the API accepts. 28 hostile query strings parse without throwing, produce only filters that
   `validateDraft` accepts, and are a **fixpoint** under `parse → encode → parse` (no URL oscillation, no
   request loop). A 2 000-`f` URL also survives.
5. **Two distinct same-key filters collapsing into one.** `cap gte 2e9` + `cap lt 10e9` travel as two predicates,
   render as two chips, and one `×` removes only its own (verified at App level from the URL and in the live API).
   Only *identical* signatures are deduped, and those are semantically identical.
6. **Losing filters when the schema is slow or fails.** With the schema gated for 300 ms, the shell renders and
   the link's filter is applied the moment it lands. With a 500 on the first attempt, the error screen shows,
   `Retry` re-fetches, and the link's filter is applied and sent (`[{key:"cap",op:"gte",value:2000000000}]`) —
   `useScreenState`'s `initialised` guard is correctly still false. The only state lost is a half-built builder
   row (F10).
7. **A request per keystroke, or a lost race.** Typing `12345` into a numeric value fires **0** requests;
   `Add to screen` fires exactly one. A second preset click aborts the in-flight request (`signal.aborted ===
   true`) and the last body wins (`[gte 2e9, lt 10e9]`); `seq` + `AbortController` make the earlier response
   unable to paint.
8. **Chip text describing a different request.** Holds for every numeric/date/text filter I tried, except the two
   documented cases (F6, F8). Escape closes the drawer without applying anything; a chip's `×` is keyboard
   operable and named `Remove filter: Market Cap. ≥ $2B`; ⌘K is fully keyboard-driven; a zero-row answer keeps
   the chip bar and its `Clear all` reachable.

---

## 4. Method / reproducibility

- **Live**: `POST http://127.0.0.1:8099/screens/select` (`mode:criteria`, `region:us`), read-only, ~450 requests.
  `GET /screens/select/schema` for the registry. No port binding, no restart, nothing written.
- **Client**: Vitest 5.0.5 + jsdom, project config, six throwaway suites
  (`__probe__/probe-registry`, `probe-url`, `probe-ui`, `probe-boot`, `probe-compiler`, `probe-chip-round`),
  deleted after the run. Each failing assertion in those suites *is* a finding above; the passing ones are the
  evidence in §3.
- **Existing suite**: `npx vitest run` 240 passed / 2 skipped; `npm run build` green; `npm run budget` 116.0 KB.
- Point-in-time note: the store's `as_of` is 2026-10-31T04:1xZ. Every count above is from that snapshot and will
  move with the data; the *relations* (715 + 1 367 = 2 082; 410 + 738 + 442 + 613 + 3 373 = 5 576) are the part
  that matters.
