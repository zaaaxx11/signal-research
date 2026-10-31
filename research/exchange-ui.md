# Exchange control — the UI half

Scope: the toolbar control, the human label wherever an exchange is displayed, and the
tests that keep both honest. No code is changed by this document; every proposal below
names the file and the seam it touches.

Baseline measured on `a71fd93` with `cd web && npm run check`: **green**. 49 test files,
508 tests (500 passed, 7 skipped), `tsc` + `vite build` clean, token audit 0 off-token,
initial JS **133,841 B gzip = 130.7 KB** against the 180 KB gate (**49.3 KB headroom**),
lazy detail chunk 16,203 B gzip. One separate `npx vitest run` under load hit a 5 s
timeout in `a11y.test.tsx:361`; the gate run before it exited 0, so that is a flake, not
a regression.

---

## 1. What is actually on screen today

| Surface | File:line | What it prints | Vocabulary |
|---|---|---|---|
| grid cell | `web/src/grid/columnMeta.ts:183` (`exchange: { label: "Exchange", ...TEXT, min: 84 }`) | the raw cell | `XNAS`, `XNYS`, `XASE`, `XCBO`, `SSE`, `SIX`, … |
| detail panel | `web/src/detail/metrics.ts:121` (profile group) | the payload string | `Nasdaq` (the fixture, `detail/__fixtures__/detail.ts:20`) |
| filter chip | `web/src/filters/state.ts:142-163` (`formatValue`) | the raw value | `XNAS` |
| drawer input | `web/src/filters/ConditionBuilder.tsx:317-327` | a free-text `<input>` | whatever is typed |
| symbol search | `web/src/shell/SymbolSearch.tsx:348` | `hit.exchange` | `NASDAQ` |

So the same company's venue is spelled **three ways in one screen** (`XNAS` in the grid,
`Nasdaq` in the detail, `NASDAQ` in search). That inconsistency, not the raw code itself,
is the defect.

The wire value is not always a MIC. `app/screen/universe.py:130-139` maps only
`NASDAQ/NYSE/AMEX/CBOE → XNAS/XNYS/XASE/XCBO` and passes everything else through
uppercased (`mic_for`, line 302). The 1,900 rows in `ztockz.db` carry **17 distinct codes**:

```
XNAS 887  XNYS 515  XASE 128  SSE 87  SIX 45  LSE 45  KRX 36  HKEX 33  TWSE 18
TSE 18  XETR 15  TSX 15  TADAWUL 15  SZSE 15  EURONEXT 15  XCBO 10  BCS 3
```

`SSE`, `SIX`, `LSE`, `TSE`, `TSX`, `KRX`, `HKEX`, `TWSE`, `SZSE`, `EURONEXT`, `TADAWUL`
and `BCS` are exchange short codes, not MICs. Any UI copy or comment that calls this
column "the MIC" is wrong; the honest name is **exchange code**, and the filter must
send it byte-for-byte. (`app/screen/columns.py:183-185` already says "A MIC on the row
(`XNAS`)" — true for the US venues, not for the global tab.)

---

## 2. The reuse map — what a copy of `SectorSelect` needs

`SectorSelect` is four pieces, not one component. All paths are relative to `web/src`.

| # | Piece | File | What to copy | Changes for exchange |
|---|---|---|---|---|
| 1 | option read | `grid/useSectors.ts:39-83` | `useSectorOptions(region, assetClass)` → `{names, status, message, reload}`; one `getJson(breadthPath(...))`, `AbortController`, 503 → `unavailable` | **do not copy as a second hook.** Extend the same hook with `venues` (see §4.1) so the app keeps one `GET /market/breadth` per mount |
| 2 | vocabulary + predicate | `grid/sectors.ts` | `SECTOR_KEY`, `clean`, `uniqueSorted`, `isSectorChoice` (84-91), `activeSector` (93-100), `withSector` (113-121) | new `grid/exchanges.ts` with `EXCHANGE_KEY = "exch"`, `isExchangeChoice` (eq **and** in), `activeExchanges`, `withExchange` |
| 3 | menu | `grid/SectorSelect.tsx:56-96` | props `{names, source, value, onChange}`; the chosen-but-absent option is prepended (line 60); source sentence printed beside the control | new `grid/ExchangeSelect.tsx`; multi-select, see §4 |
| 4 | chip | `grid/SectorSelect.tsx:98-142` | `{name, onClear}`, testids `sector-chip` / `sector-chip-name`, `aria-label="Clear sector filter: …"` | `grid/ExchangeChip.tsx`, one chip for the set: `Exchange: NASDAQ, NYSE` |
| 5 | panel wiring | `grid/ScreenPanel.tsx:295-322` (derive population + value), `320-322` (`barFilters`), `417-424` (render control), `470-472` (render chip) | `sectorCapable`, `offeredSectors`, `derivedSectors`, `sectorPopulation: SectorSource`, `sectorValue` | same five expressions for venues; `barFilters` also excludes `isExchangeChoice` |
| 6 | shell wiring | `app.tsx:164` (`useSectorOptions`), `364-378` (props, `onSectorChange`) | `screen.setFilters(withSector(...))` | `onExchangeChange={(codes) => screen.setFilters(withExchange(screen.state.filters, codes))}` |
| 7 | page reset | `filters/useScreenState.ts:112-114` | `setFilters` sets `page: 0` — that is the whole reset | none; reuse |
| 8 | URL | `filters/urlState.ts:60-68` (encode), `94-102` (`in`/`between` parse as JSON), `140-190` (`resolveKey` canonicalises aliases) | `f:exch:eq:XNAS`, `f:exch:in:["XNAS","XNYS"]` already round-trip | none |
| 9 | chip text | `filters/state.ts:142-163` | `formatValue` prints the raw string | one line: label when `key === "exch"` |
| 10 | tests | `grid/sectorSelect.test.tsx:89-124` (`Harness`), `186-291`, `405-451` (shell/URL) | the harness shape: one filter list, one page, `onSectorChange` replaces + resets | `grid/exchangeSelect.test.tsx` |

Exact prop surface a copy needs:

```ts
// grid/exchanges.ts
export const EXCHANGE_KEY = "exch";
export interface VenueBucket { readonly code: string; readonly count: number }

export function isExchangeChoice(filter: ActiveFilter): boolean;
export function activeExchanges(filters: readonly ActiveFilter[]): string[];   // [] == any
export function exchangeVenuesFromRows(rows: readonly ScreenRow[]): VenueBucket[];
export function withExchange(filters: readonly ActiveFilter[], codes: readonly string[]): ActiveFilter[];

// grid/ExchangeSelect.tsx
export type ExchangeSource = "breadth" | "rows" | "loading" | "none";
export interface ExchangeSelectProps {
  readonly venues: readonly VenueBucket[];
  readonly source: ExchangeSource;
  readonly value: readonly string[];                 // codes, never labels
  readonly onChange: (codes: string[]) => void;
}

// grid/ExchangeChip.tsx
export interface ExchangeChipProps { readonly codes: readonly string[]; readonly onClear: () => void }
```

`ScreenPanel` gains `exchangeOptions?: readonly VenueBucket[]` and
`onExchangeChange?: (codes: string[]) => void`; the control mounts only when
`onExchangeChange !== undefined`, exactly like `sectorCapable` (line 302). That gate is
what keeps every existing panel test that does not pass the callback byte-identical.

`useSectorOptions` gains one field rather than a second hook:

```ts
export interface SectorOptions {
  names: string[];          // unchanged
  venues: VenueBucket[];    // new — [] when the document has no `venues`
  status: SectorOptionsStatus;
  message: string | null;
  reload: () => void;
}
```

One `GET /market/breadth` per mount stays one. A second hook would be a second request
for the same document and would break every `fetch` stub in the suite that throws on an
unexpected URL (`grid/sectorSelect.test.tsx:399`, `a11y.test.tsx`, `app.chunk.test.tsx`,
`filters/app.filters.test.tsx`).

---

## 3. Where the human label lives

**Decision: one leaf module, `web/src/lib/exchanges.ts`, imported by both the grid and
the detail panel; the column spec gets a `display` hook in `columnMeta`, the cell
renderer stays generic, and the server adds no per-row label.**

```ts
// lib/exchanges.ts — display only, ~20 lines of data + 3 functions
export function exchangeLabel(code: string): string;   // "XNAS" -> "NASDAQ"; unknown -> the code
export function exchangeTitle(code: string): string;   // "NASDAQ (XNAS)"
export function sortExchanges(codes: readonly string[]): string[];
```

`columnMeta.ts` gains one optional field on `ColumnSpec` (line 26-40):

```ts
/** Display-only text for a raw value. Never changes the value that is sent. */
display?: (value: string) => string;
```

and `exchange` (line 183) becomes:

```ts
exchange: { label: "Exchange", ...TEXT, min: 104, display: exchangeLabel },
```

`format.ts:215-219` (the `text` case of `formatCell`) applies it and owns the title rule
— when a display changes the value, the raw value is appended in parentheses, so the code
is never lost:

```ts
case "text":
default: {
  const raw = String(value);
  const text = spec.display ? spec.display(raw) : raw;
  return { text, sign: null, absent: false, title: text === raw ? raw : `${text} (${raw})` };
}
```

`cellRenderers.tsx:99-107` adds `data-value={raw}` **only when `spec.display` exists**, so
the wire value is recoverable from the DOM without a second visible column.

The detail panel cannot import `grid/columnMeta` (it is a lazy chunk; pulling the 12 KB
column table into it to read one map is wrong). It imports the same leaf:

```ts
// detail/metrics.ts, in groupMetricRows (line 156-165)
value: definition.key === "exchange" && typeof entry?.value === "string"
  ? exchangeLabel(entry.value)
  : entry?.value,
```

`exchangeLabel` is **idempotent over both vocabularies**: `"XNAS" → "NASDAQ"`,
`"Nasdaq" → "Nasdaq"`, `"NASDAQ" → "NASDAQ"`, `"NYSE American" → "NYSE American"`,
`"BCS" → "BCS"`. That is what lets one function serve the grid (codes), the detail
(names) and the chip without a second table.

### Why not the alternatives

**A formatter in the cell renderer.** Functionally identical to the `display` hook, but
it puts a column-specific branch inside `CellValue`, which is the one component every
column shares. `CellValue` knows `spec` and `value`, not the catalogue; a
`spec.key === "exchange"` test there is the beginning of a renderer that knows column
names. The `display` hook is the same cost with the branch in data. Rejected on shape,
not on performance.

**A map inside `columnMeta`.** Right hook, wrong home for the *data*. `columnMeta` is
imported by `DataTable`, `solver`, `ScreenPanel` and every grid test; the detail chunk
must not reach into it. Keeping the table in `lib/exchanges.ts` and the *reference* in
`columnMeta` gives the grid a declarative spec and the detail a 722 B-gzip leaf instead
of a 12 KB-raw (`columnMeta.ts` is 12,101 B) metadata module.

**A server-supplied `label` on the row.** Rejected, and the reasons are measurable:

1. **Bytes are not free.** Each row would carry `"exchange_label":"NASDAQ"` ≈ 22-26 B.
   100 rows ≈ 2.3 KB against the API's 60 KB panel budget (`budget.truncated` is already
   a visible state). The breadth facet, by contrast, costs **462 B for 17 buckets** and
   **0.31 ms** to tally over 1,900 rows (measured; ~0.9 ms over the 5,678-row universe).
   A per-row field is the one shape that multiplies by page size.
2. **The server cannot fill it today.** `reference/venue_master.json` ships **2 venues**,
   `MIC_BY_EXCHANGE` knows **4**, the DB holds **17**. A server label would be absent for
   13 of 17 codes, so the client would still need a fallback map — two sources of truth
   for the same string.
3. **Two producers, two vocabularies.** The grid's `exchange` comes from
   `mic_for(record.exchange)`; the detail's comes from `SecurityRecord.exchange`. A
   server label must be added to *both* documents and kept consistent, while one client
   normalizer reconciles them for free.
4. **It buys nothing for sorting or filtering.** The grid sorts server-side on
   `market_cap | turnover | gainers` (`DataTable.tsx:288-297`, every column
   `enableSorting: false`), and the filter value travels in `ActiveFilter`, never in the
   cell. A second field on the row serves only display — the one thing the client can do.

The migration path if the server ever ships `venues[].label`: `exchangeLabel(code, supplied?)`
prefers a non-empty `supplied` that differs from the code, else the map, else the code.
One line, and the client map becomes the fallback instead of the source.

### Adversarial (a) — 100 rows, alignment, and how you would know

The transform is one `Map.get` per exchange cell. Measured on this machine (Node 20,
`esbuild --minify` output):

* `exchangeLabel` **0.033 µs/call** (1,000,000 calls in 33 ms).
* A full 14-column × 100-row format pass costs **~0.98 ms** today; with the label it was
  **~0.94 ms** — the delta is inside run-to-run noise, i.e. 100 extra map lookups are
  **~3.3 µs per page**.
* For scale: the exchange column is 1 of 14 cells. The repo already measures why cell
  work matters — the wide view's 2,340 cells re-rendering cost ~110 ms, which is why rows
  are memoised (`DataTable.tsx:614-621`). One map lookup per exchange cell is not that
  class of cost.

Alignment is structural, not textual: `DataTable` is `table-layout: fixed` with a
`<colgroup>` (`DataTable.tsx:453-484`), and the text branch returns
`<span class="block truncate text-left">` — one line, one box, same height for every
row. Nothing about a label changes the column geometry.

What would catch a regression:

1. **A cost guard** — a unit test that calls `exchangeLabel` 100,000 times and fails above
   50 ms. Today that is ~3 ms. The realistic regression is someone replacing the table
   with `new Intl.DisplayNames(...)` per call (~50-100 µs each → 5-10 s), which this
   catches immediately.
2. **A structure test** — render one row; assert the exchange cell is one
   `<span class="block truncate text-left" data-kind="text" data-value="XNAS">` with
   `title="NASDAQ (XNAS)"`, and that the row still has exactly one `<td>` per column.
   jsdom cannot measure pixels; it can prove there is no wrapper that could wrap to two
   lines.
3. **A width check** — the header/label test already exists (`columnMeta.test.ts:59-68`).
   The cell has no such guard, so `min: 84 → 104` is the fix: 84 px − 16 px padding =
   68 px ≈ 10 characters at 12 px, and `NYSE American` is 13. At 104 the text fits; the
   `profile` view floor moves 1,205 → 1,225 px, still under 1,280, so no view starts
   scrolling at the reference width (solver floor measured with the fixture views).

### Adversarial (b) — client formatter vs a free server field, honestly

The strongest case for the server is **one vocabulary**: a single table the API owns,
served once, with no client copy to drift. That case only holds if the table is complete
and the surface is a facet, not a row. Here it is neither: the shipped venue master has
2 entries and the server mapping 4, while the product shows 17 — a server label today
would be a 4-of-17 answer wearing the authority of the backend. And the per-row shape
pays bytes on every page for a string that never varies within a venue.

The honest split is:

* **now** — client map + code fallback, because display must work for all 17 codes today
  and the code is the contract;
* **later, if wanted** — `venues[].label` on the breadth facet (not on the row), sourced
  from a completed venue master; the client prefers it and keeps the map as fallback.

I would not add a per-row label under any of those plans.

---

## 4. The control

### 4.1 Options: shape, source, population

`GET /market/breadth` gains one optional field, computed in the loop that already walks
every row (`app/api/discovery.py:284-333`):

```json
"venues": [{"code": "XNAS", "count": 887}, {"code": "XNYS", "count": 515}]
```

* one bucket per non-blank `row["exchange"]`, reusing `_sector_label`'s trim/blank rule;
  a row with no exchange cell is not a venue and gets no null bucket (unlike sectors,
  where the null bucket keeps the counts summing to `universe_total`);
* `count` is the last scan's universe count, **before any filter** — the menu title must
  say so;
* cost measured: **0.31 ms / 1,900 rows**, **462 B JSON / 17 buckets**;
* `method` gains one sentence: "one bucket per exchange code the scan carried; a row
  with no exchange cell is not a venue."

Client parsing (`breadth/report.ts`): `venues?: readonly VenueBucket[]` is **optional and
tolerant** — absent or non-array ⇒ `[]`; a malformed bucket is skipped. Rationale: a bad
facet must degrade to "from rows on screen", not take the sector menu and the breadth
strip down with it. This asymmetry with the strict headline counts is deliberate and
should be commented as such. `LIVE_BREADTH` is annotated `BreadthReport`
(`breadth/__fixtures__/breadth.ts:18`), so a **required** field would fail `tsc` and the
whole gate; optional is the only shape that compiles today.

Populations, same four states as sectors (`ScreenPanel.tsx:310-317`):

| State | When | Menu | Source sentence |
|---|---|---|---|
| `breadth` | `venues.length > 0` | whole market, with counts | "from breadth" |
| `rows` | no breadth, ≥1 exchange cell on the page | page codes and page counts | "from rows on screen" |
| `loading` | neither yet, read in flight | empty | "exchange list loading" |
| `none` | neither, read settled | empty | "no exchange list" |

When the source is `rows`, the counts are **page counts** (≤ 100), not market counts; the
same number in the same place means two different populations, so the count's `title`
repeats the source: "12 rows on this page" vs "887 rows in the last scan, before your
filters".

Order: a fixed `VENUES` order in `lib/exchanges.ts` — US majors first (`XNAS`, `XNYS`,
`XASE`, `XCBO`), then the rest by label, unknown codes last and alphabetical. Sectors are
alphabetical because 21 long names have no natural priority; exchange does have one, and
a menu that puts `XNAS` first saves the reader the scroll on 80% of rows. The order is
static, never the tape's.

### 4.2 State, URL, chip

* **Value is a set of codes**, `readonly string[]`; `[]` means "All exchanges".
* **One predicate, replaced**: `withExchange` drops every `exch` `eq`/`in` predicate and
  appends `in` (≥2 codes) or `eq` (1 code). `exch ne …` and `exch contains …` are not its
  claim and survive (they stay in the chip bar), exactly as `isSectorChoice` only owns
  `eq` (`sectors.ts:84-91`).
* **`eq` for one, `in` for many.** One shape is tempting, but a single venue is an
  equality, the drawer writes equality, and a link that already says `exch:eq:XNAS` should
  not be rewritten into `in` just because the control was used. Both ops are already
  first-class: `filters/types.ts` `FILTER_OPS`, `registry.ts:81` `TEXT_OPS`, `urlState.ts:94`,
  `state.ts:184`, and `app/screen/filters.py:100` ("`eq`/`ne`/`in` compare exactly").
* **Codes are upper-cased on write** (`code.trim().toUpperCase()`); the API is exact-match
  on the code (`filters.py:100`), so a hand-typed `xnas` that reached the wire would match
  nothing.
* **Page resets** because `screen.setFilters` sets `page: 0` (`useScreenState.ts:112-114`);
  the panel's derived page key (`ScreenPanel.tsx:150-153`) also collapses to page 0 in the
  same commit, so exactly one request goes out.
* **Chip**: one chip for the whole predicate, in the shared chip row above the table:
  `Exchange: NASDAQ, NYSE`, `title="NASDAQ (XNAS), NYSE (XNYS)"`,
  `aria-label="Clear exchange filter: NASDAQ, NYSE"`, clearing sends `[]`.
* **Chip bar untouched**: `barFilters` excludes exchange choices when the control is
  mounted (`ScreenPanel.tsx:320-322`), so the predicate is not printed twice. When the
  control is *not* mounted, an `exch` predicate stays in `ChipBar` — unchanged behaviour.
* **Chip row**: `SectorChip` and `ExchangeChip` share one bar. `SectorChip` gains
  `bare?: boolean` (default `false`, so existing behaviour is byte-identical) and
  `ScreenPanel` renders `<div data-testid="control-chip-bar" role="group"
  aria-label="Toolbar filters">` when either is present. No test queries
  `sector-chip-bar`, so the wrapper can absorb it without an edit.

### 4.3 Empty, loading, and the 0-row exchange

| Situation | What the reader sees |
|---|---|
| nothing chosen | trigger reads `All exchanges`, `title="Any exchange"` |
| chosen code is in the list | its row is checked; trigger reads the label, or `2 exchanges` |
| chosen code **not** in the list (stale link, or 0 rows on this page) | the code is prepended, checked, `data-extra="true"`, `title="Not in the current exchange list; the link still sends XWAR."` — it is never silently dropped (the `SectorSelect.tsx:60` rule) |
| menu opened with no options | one inert line with the source sentence; the trigger stays **enabled** so a link's code can still be unchecked |
| the chosen exchange matches **0 rows** | nothing special in the menu: the chip stays, the grid says "No rows in this view" (`DataTable.tsx:558-569`), and the filter is one click from being cleared. The control **cannot** know per-filter counts and must not print a fake one |
| breadth read failed, page has rows | `rows` population, source sentence says so; counts are page counts |
| breadth read failed, page has no exchange cell | `none`, empty menu, trigger still enabled |

### 4.4 Keyboard

One tab stop (the trigger). The menu is a `role="listbox"` with
`aria-multiselectable="true"`, `tabIndex={-1}`, focused on open, with
`aria-activedescendant` pointing at the active option (the CommandPalette pattern,
`filters/CommandPalette.tsx:117-183`, but with a button trigger instead of an input).

| Key | On the trigger | In the menu |
|---|---|---|
| `Enter` | open | toggle the active option, close, focus back to the trigger |
| `Space` | open | toggle the active option, **stay open** |
| `ArrowDown` / `ArrowUp` | open at the first checked option, else the first | move with wrap |
| `Home` / `End` | — | first / last |
| letter keys | — | type-ahead over labels, 500 ms buffer, cycles |
| `Escape` | — | close, focus back to the trigger |
| `Tab` | — | close, let the browser move on |

Space toggles and stays open (you are building a set); Enter commits and closes. Every
toggle applies immediately, like every other control in the app — there is no Apply.
Two quick toggles send two requests; the hook aborts the superseded one
(`data/screens.ts`, same as the sector control).

Positioning: the menu is `position: fixed`, anchored to the trigger's
`getBoundingClientRect()` on open, clamped to the viewport, closed on scroll/resize and
on an outside `pointerdown`. `absolute` inside the toolbar would be clipped by the
panel's `overflow-hidden` (`ScreenPanel.tsx:373`); a fixed box escapes it because no
ancestor has a transform.

### 4.5 Single or multi — the decision

**Multi-select, one `in`/`eq` predicate.** The defence:

1. **Single cannot express the common screen.** With replacement semantics, two
   single-select venues can only be two `exch eq` rows, which the API **ANDs** into zero
   rows. "NASDAQ or NYSE" — the canonical US screen — is unrepresentable. The alternative
   is to send readers to the drawer to type `exch in (XNAS, XNYS)` by hand, which is a
   split brain for one facet.
2. **`in` is already supported end to end** — op vocabulary, URL codec, chip text, API
   compile — so multi is a UI cost, not a contract change.
3. **It is nearly free**: measured below, multi costs **+0.65 KB gzip** over single
   because the label table and the chip dominate both.
4. **Parity with Finviz**, whose control is a `modalSelect` popover
   (`research/finviz-ui.md:330`); the operator's brief says that modal is multi. (The
   captured HTML only shows the closed trigger — `aria-haspopup="listbox"`, text "Any" —
   so the multi claim is the brief's, not something this repo measured.)

The honest cost of multi: a custom listbox instead of a native `<select>` — ~120 more
lines, an anchored popover, outside-click and focus-return logic that a native select
gets for free. If the product only ever wanted one venue, single would be the better
trade. It does not.

---

## 5. Tests to add

New `web/src/grid/exchanges.test.ts` (pure, no DOM):

1. **labels every code the product issues and falls back to the code** — table over the
   17 observed codes; `exchangeLabel("XNAS") === "NASDAQ"`, `("xnas") === "NASDAQ"`,
   `("Nasdaq") === "Nasdaq"`, `("BCS") === "BCS"`, `("") === ""`.
2. **no label is its own code** — for every entry, `exchangeLabel(code) !== code`
   (catches a table entry that would make the chip print a raw code).
3. **orders the menu US majors first, unknowns last** —
   `sortExchanges(["BCS","XLON","XNYS","XNAS"])` → `["XNAS","XNYS","BCS","XLON"]` (both
   unknown, so alphabetical by label); duplicates collapse, and the input is not mutated.
4. **reads `eq` and `in`, ignores every other op** — `activeExchanges` over
   `exch eq XNAS` → `["XNAS"]`; `exch in ["XNYS","XNAS"]` → `["XNAS","XNYS"]`;
   `exch contains asd` → `[]`; `exch ne XNAS` → `[]`.
5. **writes `eq` for one, `in` for many, and replaces its own predicate** —
   `withExchange([cap], ["XNAS"])` → `[cap, {key:"exch",op:"eq",value:"XNAS"}]`;
   `(["XNAS","XNYS"])` → `in` in canonical order; `([])` → `[cap]`; an existing
   `exch eq XASE` is removed, not appended; `cap` survives every time.
6. **counts the page's venues** — `exchangeVenuesFromRows` over 3 rows with
   `XNAS, XNYS, XNAS` → `[{XNAS,2},{XNYS,1}]`, null/blank skipped.

New `web/src/grid/exchangeSelect.test.tsx` (copy the `Harness` at
`grid/sectorSelect.test.tsx:89-124`, swapping `withSector` for `withExchange` and adding
`exchangeOptions`):

7. **renders every option with its label and count, and names its source** — open the
   menu; `role="option"` count equals the fixture's venues; text `NASDAQ`, count `887`;
   `exchange-source` reads "from breadth"; its `title` contains "17 exchange codes".
8. **toggling two options sends one `in`, page 1, other filters kept, chip in labels** —
   start at page 3 (`301–400 of 5,600`) with `cap ≥ 2e9`; toggle NASDAQ and NYSE;
   assert `criteria.offset === 0`; `criteria.filters === [{cap…}, {key:"exch",op:"in",
   value:["XNAS","XNYS"]}]`; chip text `Exchange: NASDAQ, NYSE`; chip `title` contains
   `XNAS`; the chip bar (`chip-bar`) does **not** contain "NASDAQ" (not printed twice).
9. **unchecking down to one switches to `eq`** — untoggle NYSE → `{key:"exch",op:"eq",
   value:"XNAS"}`.
10. **unchecking the last removes the predicate and the chip** — filters back to `[cap]`;
    `queryByTestId("exchange-chip")` is null; range returns to page 1.
11. **a link's single value arrives checked and unchanged** — initial
    `exch eq XNAS` → trigger reads `NASDAQ`, the option is `aria-selected="true"`, chip
    shows `NASDAQ`, first request carries `eq XNAS`.
12. **a link's unknown code is offered, checked and still sent** — initial
    `exch eq XWAR`, options from breadth → 18 rows, `XWAR` row `data-extra="true"`,
    checked; request carries `XWAR`; chip reads `XWAR`.
13. **URL round-trip is byte-identical** — after toggling, `getAll("f")` contains
    `exch:in:["XNAS","XNYS"]`; `parseScreenState` of that address bar yields the same
    predicate; a fresh mount shows the same chip and the same checked options.
14. **keyboard**: `ArrowDown` opens and activates the first checked option; `Space`
    toggles and leaves `aria-expanded="true"`; `Escape` closes and returns focus to the
    trigger; `Tab` closes; a letter key jumps to the first matching label.
15. **zero-row exchange does not lie** — pick a venue, answer with `rows: []`; assert the
    chip survives, the grid says "No rows in this view", and the menu still offers the
    venue.
16. **blank/duplicate options collapse** — `[{code:"XNAS"},{code:""},{code:"XNAS"}]` →
    one option.

Additions to existing files:

17. `web/src/grid/format.test.ts` — **the exchange cell prints the label, keeps the code
    in the title and in `data-value`**: `formatCell("XNAS", columnSpec("exchange"))`
    → `{text:"NASDAQ", title:"NASDAQ (XNAS)"}`; render a `DataTable` row and assert the
    `<span>` has `data-value="XNAS"`; `"XWAR"` → `{text:"XWAR", title:"XWAR"}`; `null` →
    em dash with `data-null="true"` (no label for an absent value).
18. `web/src/grid/exchanges.test.ts` — **the formatter cost guard**: 100,000
    `exchangeLabel` calls under 50 ms (today ~3 ms).
19. `web/src/filters/state.test.ts` — **the chip says NASDAQ, the URL does not**:
    `describeFilter(registry, {key:"exch",op:"eq",value:"XNAS"})` → text
    `Exchange = NASDAQ`; `encodeScreenState` for the same filter still contains `XNAS`.
20. `web/src/detail/metrics.test.ts` — **the detail labels a code and leaves a name
    alone**: a payload with `exchange: "XNAS"` → `groupMetricRows(...)`, then
    `metricCell(row.value, row.unit).text === "NASDAQ"`; `"Nasdaq"` → `"Nasdaq"`;
    absent → em dash, still 43 rows.
21. `web/src/a11y.test.tsx` — add `"Exchange"` to the tab-trace list (line ~293) and one
    assertion that the trigger is `aria-haspopup="listbox"`, `aria-expanded` flips, and
    the menu is `aria-multiselectable="true"` with one tab stop.

Companion, not in this slice (each is a separate decision):

22. Drawer input: `ConditionBuilder` normalizes a typed label (`NASDAQ`) to the code
    before `onAdd`, and the row's hint reads "codes: XNAS, XNYS, XASE, XCBO". Test:
    typing `NASDAQ` sends `exch eq XNAS`.
23. Future export button: assert the exported row is `screens.rows`, not cell text —
    `expect(rows[0].exchange).toBe("XNAS")` after a render, and the CSV path serializes
    `rows`, never `data-value`/`textContent`.

---

## 6. Existing tests: what breaks, what must change

Nothing breaks **if** the implementation keeps the five constraints below; each is a
place where a plausible shortcut does break the suite.

| Constraint | What it protects | If violated |
|---|---|---|
| `venues` is optional on `BreadthReport` | `breadth/__fixtures__/breadth.ts:18` is `const LIVE_BREADTH: BreadthReport = {…}` | `tsc --noEmit` fails the gate, all tests fail to compile |
| the exchange control makes **no new request** | `grid/sectorSelect.test.tsx:399` `throw new Error("unexpected fetch …")`, same in `a11y.test.tsx`, `app.chunk.test.tsx`, `filters/app.filters.test.tsx` | those suites fail at mount |
| the control mounts only when `onExchangeChange` is passed | `grid/ScreenPanel.test.tsx`, `.frame`, `.pending` render the panel with no callback | new control appears in panels whose tests assert toolbar text/counts (today none do, but the gate is what keeps it true) |
| the exchange chip lives in `control-chip-bar`, not `ChipBar` | `filters/ScreenPanel.filters.test.tsx:157,236` assert `chip-bar` is **null** when no filters | those two tests fail |
| `exchange.min` ≤ 108 | `profile` view floor 1,205 + 20 = 1,225 < 1,280, so no view overflows at the reference width | `solver`/`DataTable` overflow expectations and `data-overflow` assertions change |

Tests that must be **updated** (not fixed):

* `web/src/a11y.test.tsx:293` — the ordered tab trace should name `Exchange`; the test
  still passes without it, which is exactly why it should be added (the promise is the
  list, not the count).
* No other existing test mentions `exchange` in a rendered cell: `grep -rn "exchange"
  web/src/{grid,detail,filters}/*.test.*` returns only the fixture and the detail
  fixture's `"Nasdaq"`, and no test asserts its rendered text. The risk here is **silent
  absence**, not regression.

Tests that would break for the *wrong* reason and must not be "fixed" by weakening them:

* `detail/metrics.test.ts:44` expects 3 reported profile fields (`ticker`, `company`,
  `exchange`). A label function that returned `""` for an unknown input would drop
  `exchange` from that list. `exchangeLabel` must never return empty for a non-empty
  input.

---

## 7. Bundle cost — measured, not asserted

Method: `esbuild 0.28.2 --bundle --minify --format=esm --jsx=automatic`, React external,
`gzip -9`; the same measure the gate uses. `SectorSelect.tsx` is the yardstick: it is
already in the initial bundle and costs **1,039 B gzip** for the select *and* the chip.

| Artefact | raw | gzip |
|---|---|---|
| `grid/SectorSelect.tsx` (today, the yardstick) | 2,374 B | **1,039 B** |
| `lib/exchanges.ts` (labels, order, `eq`/`in` read-write) | 1,241 B | **722 B** |
| single-select + chip + label table, one entry | 2,816 B | **1,322 B** |
| multi-select + chip + label table, one entry | 4,494 B | **1,969 B** |

Delta for the recommended (multi) implementation, adding the ScreenPanel/App wiring
(~150-200 B gzip, the size of the `withSector` + prop path): **≈ 2.1-2.3 KB gzip**.

* 2.2 KB on 130.7 KB is **+1.7%**; it consumes **4.5%** of the 49.3 KB headroom.
* Single-select would have been ≈ 1.5 KB; **multi costs +0.65 KB**.
* The detail chunk grows by the same label leaf (`+0.7 KB` on 16.2 KB) and is not gated.
* No CSS: every class used already exists (`border-line`, `bg-surface-sunken`,
  `text-fg-subtle`, …); `npm run audit:tokens` should stay at 0 off-token. No new tokens.

The isolated numbers are an upper bound: in the real bundle the repeated class strings
compress against the existing ones, and the gate will report the exact figure. If the
reader wants the smallest *correct* implementation, it is single-select at ~1.5 KB — but
it is not correct for the "NASDAQ or NYSE" screen (§4.5).

---

## 8. Adversarial (c) — CSV export must not start emitting `NASDAQ`

**There is no client-side export path today.** `grep -rn "createObjectURL|new Blob|download=|text/csv"`
over `web/src` returns nothing; the only exports in this repo are server-generated:

* `POST /screens/{screen_id}/export` (202) enqueues a job, and
  `GET /requests/{request_id}/export/{fmt}` (`app/api/requests.py:75`) serves the
  **artifact the worker wrote, never a re-render** — the route's own docstring says so
  (line 9). The writer is `app/export/writer.py`; `_validate` (407-416) refuses any column
  list that is not exactly `CANONICAL_COLUMNS_V1`, and that contract carries **both**
  `exchange` and `mic` (`app/contracts.py:38-39`).

So a display-layer formatter **cannot** contaminate an export: there is no path from the
DOM back to the writer. The risk is a future client export built from cell text — which
is why the spec adds `data-value` (the raw code) to the cell and test 23 asserts that any
future exporter serializes `screens.rows`, not `textContent`.

One trap worth naming: the grid column called `exchange` actually carries the **MIC-mapped
code** (`mic_for`), while the export's `exchange` column carries the provider name and its
`mic` column carries the code. The grid's column is therefore the export's `mic`. Nobody
should "harmonise" the UI label into the export's `exchange` column — that would rewrite a
92-column contract that `_validate` pins. The display label is display only, and the test
that proves it is test 17 (`text` is `NASDAQ`, `data-value` is `XNAS`).

---

## 9. Risks, and what I would watch

1. **Toolbar width.** Two selects plus three sort buttons plus Filters/Columns/Check
   again in one `shrink-0` row (`ScreenPanel.tsx:383-467`). The sector trigger is
   `max-w-[11rem]`, the exchange trigger should be `max-w-[9rem]`, and both source
   sentences already hide below `xl`. At 1280 it fits; below ~1100 the right group can
   overflow the `overflow-hidden` section. Mitigation: put both controls in one
   `flex min-w-0 items-center gap-2` cluster and measure 1280/1100/900 during the visual
   pass. No test covers this today, which is itself worth noting.
2. **A second vocabulary drift.** `lib/exchanges.ts` is a client copy of names the
   backend half-knows. Unknown codes fall back to the code, so it can be *incomplete*
   without being *wrong*; the review that adds a venue is one line. This is the cost the
   server-label option would remove only after the venue master is real.
3. **The 0-row claim.** The control deliberately does not show per-filter counts. If
   someone later adds "0" next to a venue based on the current result, that is a new
   client-side count of a server population — the exact error `breadth/report.ts` exists
   to prevent. Counts stay universe counts until the server offers a facet count.
4. **`exch`/`mic` naming.** The doc and the code comments should say "exchange code", not
   "MIC", for the global tab. `SSE` is not a MIC.
5. **The `a11y` timeout.** One `npx vitest run` on a loaded box timed out at 5 s in
   `a11y.test.tsx:361`; the gate run before it was green. If the new toolbar control adds
   one more Tab stop, that test gets marginally slower. If it becomes flaky, raise that
   test's timeout rather than trimming the trace.

---

## 10. One-paragraph version

Copy `SectorSelect`'s four pieces (read, vocabulary, menu, chip) into
`grid/ExchangeSelect.tsx` + `grid/ExchangeChip.tsx` + `grid/exchanges.ts`, fed by a
`venues` facet added to the existing `GET /market/breadth` read so no new request appears;
make it a multi-select listbox writing `exch eq` for one code and `exch in` for several,
with the same replace-own-predicate, page-reset, URL and chip contracts as the sector
control; label codes through one leaf module (`lib/exchanges.ts`) referenced by
`columnMeta.exchange.display` and by the detail's `groupMetricRows`, so the grid, detail,
chip and drawer all print `NASDAQ` while the wire, the URL, the title and `data-value`
keep `XNAS`; and add ~20 tests, the largest of which asserts exactly that split.
