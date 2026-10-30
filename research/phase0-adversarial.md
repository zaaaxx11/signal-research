# Phase 0 adversarial review — `POST /screens/select`, `/market/breadth`, `/symbols/search`

**Repo** `/home/ubuntu/ztockz` @ `952fd8b` + the uncommitted Phase 0 working tree (26 modified, 4 new;
+1787/−445) · **Review date** 2026-10-30 · **Harness** `tests._screen.screen_client` (in-process ASGI,
fixture scanner) · **Live** `127.0.0.1:8099` read-only.

Every finding below was reproduced against the code under review. The harness imports the working
tree, so it is the object of review; the live service is used only where the behaviour it serves is
the same (it answers catalogue-spelled `columns` from the depth overlay, which the harness has no
store for — see §4). Full suite on this tree: **4249 passed in 162 s** — every finding below is
invisible to it.

---

## 0. Findings

| # | Sev | Where | What |
|---|---|---|---|
| F1 | **critical** | `app/screen/views.py:139-350`, `app/screen/selection.py:193,969,276` | The nine new views are keyed by catalogue names but `VIEW_FIELDS` is still keyed by the legacy names: **62 of the 100 canonical view columns map to nothing and render `null` forever** — `view=income` 8/8 null, `view=profile` 12/12 null in production, with the data sitting in the row. |
| F2 | **high** | `app/screen/selection.py:989` (`_column_row`) | `columns` has no catalogue→row-column resolver: it reads the name verbatim. Catalogue spellings work only when the depth overlay happened to copy that key. `view=value` and `columns=[the same 12 keys]` return **different numbers** for `pe`, `market_cap`, `pc`, `ev_sales`; `price_to_cash` and `ev_to_sales` are 100 % null while their catalogue twins are 85 %/56 % filled. |
| F3 | **high** | `app/api/screens.py:129-142,349-357` | The ETag's "data revision" is the depth store's clock only. The live scan, the fundamentals store and the facts store all change the body without moving the tag → **304 with a changed body**, constructed below. |
| F4 | **high** | `app/api/screens.py:464`, `app/screen/wide_fill.py:80-90` | `fill_wide` rewrites the projected row after projection: +70 cells, 414 B → **118,787 B** for 100 rows × 1 column. The "exactly its cells" contract and the 60 KB panel budget both die. |
| F5 | medium | `app/screen/depth_overlay.py:70-94`, `app/screen/selection.py:193` | Three alias tables, not one (`SCREEN_ALIASES` 18, `VIEW_FIELDS` 57, `LEGACY_COLUMN_ALIASES` 23; only 11 keys shared) and they have already drifted: filters accept `perf_1w` but 422 `perf_4w`; views fill `pe` but not `income`; columns fill neither. |
| F6 | medium | `app/screen/depth_overlay.py:70-87` | `average_volume` → `avg_volume_10d`, contradicting the comment directly above it ("not aliased to `avg_volume_30d` because the windows differ"). `{"key":"average_volume","op":"gt","value":5e6}` returns the row the 10-day window picks, `sh_avgvol` returns nothing. |
| F7 | medium | `app/screen/selection.py:1378-1383`, `schemas/stock_selection_request_v1.json:239,362` | The published contract still says "One of the 82 Finviz filter keys" and `x-filter-key-count: 82`, while the validator now accepts 44 catalogue names. A client that builds its filter UI from the schema can never offer `pe`/`roic`/`market_cap`. |
| F8 | medium | `app/api/screens.py:449-464` | `fill_wide` at the new `limit=100` issues 100 stock-page fetches and the response blocks on them: measured **6.86 s** at 0.4 s/page (20 rows: 1.63 s). |
| F9 | medium | `app/api/screens.py:169-186,462` | The panel budget is enforced only for `columns`. The other way to ask for the same panel, `view`, returns **87,918 B** (`value`) / **273,923 B** (`all`) per 100 rows, including 100 logos. |
| F10 | medium | `app/api/discovery.py:409-418` | `/market/breadth` has no negative cache on a *cold* failure: 5 requests during a scanner outage = 5 scans of ~5,700 rows. |
| F11 | low | `app/screen/selection.py:398-404` | `limit: 1e9` (a JSON float) is a 422, not the documented clamp; `limit: 4.0` too. |
| F12 | low | `app/api/discovery.py:562,606` | Query validation errors use FastAPI's `{"detail": …}` while every other error on these routers uses `{error, errorMessage}`. |
| F13 | low | `app/api/discovery.py:390-394,507-528` | `UniverseCache.loader` is dead: the loader is bound when the cache is created, so swapping `app.state.universe_loader` afterwards is ignored (the docstring says it is resolved per request). |

Counts: **1 critical, 3 high, 6 medium, 3 low.**

---

## 1. Detail

### F1 — the nine views render `null` for 62 % of their columns (critical)

`app/screen/views.py` was rewritten to name columns in the catalogue's vocabulary (`income` names
`dividend`, `dividend_yield`, `payout_ratio`; `profile` names `company`, `sector`, `exchange`), but
the API's view renderer still maps them through `VIEW_FIELDS` (`app/screen/selection.py:193`), a
57-entry table written for the *old* six Finviz tabs (`eps_ttm`, `perf_week`, `week52_high`,
`oper_margin`, …). `view_row` (`:969`) does `row.get(VIEW_FIELDS.get(column))` and answers `None`
when the column is not in that table.

Repro (live, 2026-10-30T22:33Z):

```
POST /screens/select {"mode":"criteria","region":"us","criteria":{"limit":100},"view":"profile"}
→ 200, 12 columns, all 12 null in all 100 rows:
  company, sector, industry, security_type, country, exchange, index_membership,
  optionable, shortable, employees, earnings_date, ipo_date
POST … "view":"income"  → 8/8 null
POST … "view":"today"   → 8/11 null (change, change_from_open, gap, open, high, low,
                           average_volume, relative_volume)
POST … "view":"momentum"→ 6/10 null (perf_1w/4w/13w/26w/52w, rsi)
```

The data is present: the same process's depth entry for `ARL` carries
`"company":"AMERICAN REALTY INVESTORS INC"`, `"country":"United States"`, `"dividend_ttm":0.0`, and
the scan row carries `pct_from_52w_high`, `ret_1w`, `ret_1m`. They are simply not addressed.

Structural measure, independent of data — feed `view_row` a row containing *every* `FIELDS` and
*every* `CATALOG_BY_KEY` column, populated:

```
today 8/11 null · value 2/13 · quality 3/12 · growth 10/12 · momentum 6/10
trend 8/12 · ownership 6/10 · income 8/8 · profile 12/12   → 142 of 216 (view,column) pairs null
```

`app/screen/selection.py:276 _validate_view_fields()` is the boot check that should have caught this
and is **one-directional**: it proves every `VIEW_FIELDS` *target* is a real column and that no
`VIEW_FIELDS` key is undeclared, but never that every declared view column has a `VIEW_FIELDS` entry.
`tests/test_screen_views.py` checks the *partition* (keys ∈ catalogue) and renders through
`V.build_view`, which takes catalogue-keyed cells directly — neither path touches `view_row`.

**Fix:** add the 62 missing catalogue→row-column mappings to `VIEW_FIELDS` (many are one-liners that
already exist under the legacy key: `perf_1w`→`ret_1w`, `rsi`→`rsi_14`, `atr`→`atr_14`,
`high_52w`→`high_52w`, `dividend_yield`→`dividend_yield`), and make `_validate_view_fields` assert
`set().union(*(v.columns for v in screen_views.VIEWS)) - set(VIEW_FIELDS) == set()`.

---

### F2 — `columns` does not resolve the catalogue; two spellings, two numbers (high)

`_column_row` (`app/screen/selection.py:989`) is `document[column] = row.get(column)`. `_KNOWN_COLUMNS`
accepts both vocabularies (`FIELDS | CATALOG_BY_KEY`), so the caller may ask for `pe` **or** `pe_ttm`
and gets whichever key the row happens to carry. Filters *do* translate (`_alias_column`, `:641`);
columns do not; `view` translates through yet another table. Three readers, three behaviours.

Repro A — a deployment without a warm depth store (harness; also a cold start, or depth disabled):

```
POST /screens/select {"mode":"criteria","region":"us","criteria":{"limit":100},
  "columns":["market_cap","enterprise_value","pe","forward_pe","peg","ps","pb","pc","pfcf",
             "ev_ebitda","ev_sales","target_price"]}
→ 200, 100 rows, 24,094 B, every one of the 12 columns null in every row.
```

Repro B — the same 12 keys on the live (warm) service, same process, same universe:

```
"columns":["pe","pe_ttm","market_cap","market_cap_usd","pc","price_to_cash"]
→ {"pe":39.8211, "pe_ttm":39.5068, "market_cap":5072816312120.0,
   "market_cap_usd":5029592198492.9, "pc":81.60, "price_to_cash":null}
```

Two P/Es and two market caps in one row, ~0.8 % apart, with nothing saying which is which. Over 100
small caps: `pe` null 30/100 vs `pe_ttm` 52/100; `pc` null 15/100 vs `price_to_cash` **100/100**;
`ev_sales` 44/100 vs `ev_to_sales` **100/100**.

Repro C — `view` and `columns` are supposed to be two spellings of one panel:

```
view=value                     → pe 39.50683800855181, market_cap 5029592198492.897
columns=[the same 12 names]    → pe 39.821100917431195, market_cap 5072816312120.0
```

Why: `VIEW_FIELDS` maps `pe`→`pe_ttm`; `_column_row` reads `pe`, which only exists because the depth
overlay (`overlay(..., aliases=SCREEN_ALIASES)`) copied the 116 catalogue keys onto the row.

**Fix:** resolve every requested column through the same table the views use
(`VIEW_FIELDS.get(column, column)`) before reading the row, or accept only row columns and say so in
the schema. Either way, one metric must have one number per row.

---

### F3 — the ETag certifies data it does not cover: 304 for a changed body (high)

`_data_revision` (`app/api/screens.py:129-142`) hashes `snapshot_as_of` and `depth.as_of` only.
The body is built from four sources: the live TradingView scan, the depth store, the fundamentals
store (`enrich_rows`) and the facts store (`enrich_facts`). Three of the four can change without the
tag moving. `selection_etag` then promises a **strong** validator.

Constructed collision (harness, `app.api.screens.enrich_rows` wrapped to change one cell):

```
POST {"mode":"criteria","region":"us","criteria":{"limit":3},"view":"value"}   → forward_pe [1.0,1.0,1.0]
POST the same body                                                             → forward_pe [2.0,2.0,2.0]
  etag1 == etag2 == "select-a7239b61-ce855b79ae82999fdb9505c721b83098"
POST the same body with If-None-Match: etag1                                    → 304
```

A client that revalidates — the documented client — keeps the old numbers forever. The same hole
covers the live scan: on the production process `depth.as_of` has been `16:13:15Z` for six hours
while every request re-runs the 5,600-row scan whose `price`/`change_pct`/`volume` cells are in the
body.

Secondary, same root: the 200 body carries `as_of = now()` (microseconds), so **two 200s with the
same strong ETag never have the same bytes** — `tests/test_api_screens_etag.py:143` asserts exactly
that behaviour and calls it correct.

**Fix:** add the fundamentals/facts store revisions and a scan revision (or a coarse bucket of the
scan time) to `_data_revision`; if that is not available, make the tag weak and put a
`Cache-Control: max-age` (e.g. 5 s) in front of it instead of claiming byte-identity.

---

### F4 — `fill_wide` rewrites the projection and blows the budget (high)

`select_rows` projects first, `screens.py:464` then hands the projected rows to `WideFiller.fill`,
which adds every wide field whose cell is `None` — i.e. all 70 of them on a projected row
(`wide_fill.py:86`).

```
POST {"…":{"limit":100},"columns":["pe_ttm"],"fill_wide":true}   (warm wide cache)
→ 414 B → 118,787 B ; 3 keys/row → 73 keys/row
```

Observed row: `as_of, pe_ttm, symbol` + `atr_14, avg_volume, beta, book_per_share, …, week52_low`.
Expected: `{"symbol","pe_ttm","as_of"}` — the contract `test_a_projected_row_carries_its_cells_and_
nothing_else` asserts, and which this request violates. 118,787 B is 2× the 60 KB panel budget for a
one-column request.

**Fix:** project after the fill (or skip `fill_wide` when `columns` is set), and re-apply the
`columns` filter to any row the filler touched.

---

### F5 — three alias tables, already drifted (medium)

The module docstring claims "the alias table is the depth overlay's own … one table, two readers".
Measured: `SCREEN_ALIASES` 18 entries, `VIEW_FIELDS` 57, `LEGACY_COLUMN_ALIASES` 23; 11 keys shared,
no shared key currently disagrees, but the coverage is wildly uneven:

* filters accept `perf_1w` and refuse `perf_4w`/`13w`/`26w`/`52w` with *"no screen row carries a
  value a filter can compare"* — false: `ret_1m/3m/6m/1y` are in `FIELDS` and on every row;
* `columns` accepts all of them and resolves none (F2);
* `views` resolve `pe`, `roic`, `sma20` but not `income`, `perf_1w`, `dividend_yield` (F1).

This is the root cause of F1/F2/F6: the table is complete for the *depth overlay's* needs, and the
two new readers inherited it as if it were the vocabulary.

**Fix:** one `CATALOGUE_COLUMNS: Mapping[str, str]` (catalogue key → row column) with three readers;
derive `VIEW_FIELDS` from it and assert the three agree at import.

---

### F6 — `average_volume` filters on the wrong window (medium)

`app/screen/depth_overlay.py:70-87`:

```
#: Only measurements with the same definition are mapped. ``average_volume`` is
#: not aliased to ``avg_volume_30d`` because the windows differ, …
"average_volume": "avg_volume_10d",
```

The comment states the rule, the next line breaks it: the catalogue sources `average_volume` from the
provider's `averageVolume` (a 3-month average, `app/engine/data/finviz_columns_116.json`), the scan
column it is mapped to is a 10-day average, and `sh_avgvol` — the Finviz filter key for the same
metric — maps to `avg_volume_30d`.

```
rows: STEADY avg_volume_10d=0.9M avg_volume_30d=5.0M ; SURGE avg_volume_10d=6.0M avg_volume_30d=1.0M
{"key":"average_volume","op":"gt","value":5000000} → ["SURGE"]   (field avg_volume_10d)
{"key":"sh_avgvol",     "op":"gt","value":5000000} → []          (field avg_volume_30d)
```

A 200 with a different row set for what the catalogue calls one column. (The same table also lands
the store's 3-month number into `avg_volume_10d` on any row whose 10-day cell is null.)

**Fix:** map `average_volume` → `avg_volume_30d` and add the four missing `perf_*` windows.

---

### F7 — the published schema still advertises 82 filter keys (medium)

`app/screen/selection.py:1378-1383` and `schemas/stock_selection_request_v1.json:239`:
*"One of the 82 Finviz filter keys … listed in x-filter-keys"*, `x-filter-key-count: 82`.

The validator now accepts 44 more (`GET /screens/select/schema` is unchanged, `{"key":"roic","op":
"gt","value":10}` is 200). The schema is the document clients generate their filter picker from; a
conforming client cannot offer `pe`, `roic`, `market_cap` — the entire point of feature 5. The
schema test (`tests/test_screen_selection.py:876`) checks `field`'s enum and `limit`'s default, never
`key`.

**Fix:** publish `x-filter-aliases` (catalogue key → column) and a count, and update the description.

---

### F8 — `fill_wide` × `limit: 100` is a blocking 100-fetch request (medium)

`WideFiller` runs `concurrency=6` with no per-request deadline; the response waits for
`gather(...)`. With a 0.4 s stock page (a fast one):

```
limit=20,  fill_wide=true → 1.63 s
limit=100, fill_wide=true → 6.86 s
```

Before this diff the page was capped at 20, so the worst case was 5× smaller. 100 rows × the real
egress ladder (direct → Jina → proxy → paid) can be minutes, on a route an anonymous caller can
drive.

**Fix:** cap `fill_wide` at `DISPLAY_LIMIT` rows, or give the fill a hard deadline and return the
document without the wide cells when it expires.

---

### F9 — the panel budget is enforced only for `columns` (medium)

`_with_logos` opts out of the brand mark only when `columns` is set (`screens.py:183`); a `view`
request gets 100 logos and the full column set:

```
view=value, limit=100 → 87,918 B      (12 columns + rank/name/logo per row)
view=all,   limit=100 → 273,923 B     (116 columns + logo per row)
columns=(12 names), limit=100 → 24,094 B (the budgeted path)
```

`view` is the documented way to ask for the same panel, and `ui/app.js` sends `view` with
`fill_wide` for the "all" tab. 274 KB per poll is 4.5× the budget the plan sets for the panel.

**Fix:** apply the same "logo only when asked" rule to `view`, and cap `all` at a smaller limit or
exclude it from the poll path.

---

### F10 — a cold breadth failure is retried by every request (medium)

`app/api/discovery.py:409-418`: on `ScanError`, a *warm* entry is kept and its retry deferred by a
TTL; a *cold* one re-raises with no record of the attempt.

```
UniverseCache(loader_that_always_raises); 5 × snapshot("us","stock") → 5 scan attempts
```

During a bot-wall (the scanner's normal failure mode) every request buys a fresh 5,700-row scan.

**Fix:** store `(now, None)` on failure and raise a cached 503 for the TTL.

---

### F11–F13 (low)

* **F11** `_limit` (`selection.py:398-404`) rejects `1e9`/`4.0` — JSON floats — with 422, while the
  schema and docstring promise "clamped, not refused". Accept int-valued floats (`float.is_integer()`).
* **F12** `?region=xx` and `?limit=abc` on `/symbols/search`, `?region=nope` on `/market/breadth`,
  return `{"detail":[{"type":"literal_error",…}]}`; every other error on these routers is
  `{error,errorMessage}`. Take `str` and validate into `ApiError`.
* **F13** `UniverseCache.loader` (`discovery.py:391-394`) is never read; `_cache_for` binds the
  loader once. Swapping `app.state.universe_loader` after the first request has no effect, contrary
  to `_universe_loader`'s docstring.

---

## 2. Attacks that failed (and why)

| Attack | Result |
|---|---|
| **ETag collision by key order** (`{"a":1,"b":2}` vs `{"b":2,"a":1}`) | No. `canonical_request` builds one dict and `json.dumps(sort_keys=True)`. |
| **ETag collision by omitted vs explicit defaults** | No. `canonical_request` hashes the parsed `SelectionRequest`, not the payload; `tests/test_api_screens_etag.py:191` covers it. |
| **ETag collision by float formatting** (`10` vs `10.0`) | No — and correctly: `Criterion` stores the compiled value, so both hash to `cae43a7b…`. |
| **ETag collision by unicode normalisation** | No (and no normalisation anywhere): `"Technology "` (trailing space) is a different tag *and* a different result — `eq` is exact by design. |
| **Two identical requests, different tag** (dict iteration, `as_of` microseconds) | No. Tag is `sha1(revision)[:8] + sha256(canonical)[:32]`, no clock, no dict order. |
| **Tag unstable across a restart** | No. Pure function of strings; no id(), no path, no process state. |
| **Tag leaks snapshot path / internal ids** | No. `select-<8 hex>-<32 hex>`; the only input is the caller's own request. |
| **304 carries a body / different validators** | No. `Response(304)` emits no body and no `Content-Length`; the 304 repeats `ETag` and `Cache-Control` (`{"etag":…,"cache-control":"private, max-age=0, must-revalidate"}`). |
| **A licensed/private body cached by a public client** | No. `Cache-Control: private` on both 200 and 304; a shared cache must not store it. (There is also no auth on these routes at all — the `x-api-key` header is only an owner fingerprint for jobs.) |
| **`If-None-Match: *`** | Returns 304 — legal for a GET-style validator, and the route is read-only. |
| **`limit`: negative / 0 / bool / string / float / null** | 422 for all; `null` → 100; `101` and `10^20` → 100 clamped, envelope reports 100. Only the float case is wrong (F11). |
| **100 rows × 12 columns is slow** | No. 2.6–3.1 s wall on the live service, and ~2.9 s of that is the TradingView scan; the projection itself is <10 ms (harness: 100 rows × 40 columns in 61 ms). |
| **Force the server to build 162 KB then discard it via 304** | No. The validator is checked before `fetch_scan`, so a 304 costs a dict hash and zero rows. `view=all` still builds 274 KB legitimately (F9). |
| **`q` with `.*`, `%`, `_`, `'; DROP TABLE--`, 10 k chars** | No. Search is a linear loop over a cached tuple, no SQL, no regex, no scan; measured 0.00 ms for 200 k-char needles. Live: `.*`, `%`, `_`, `'; DROP TABLE--`, `Ω` all return `[]`. |
| **Search scans the universe repeatedly** | No. `IdentityIndex` is cached on `(master identity, len)`; one build per master. |
| **Search leaks internal fields** | No. Hits are exactly `{symbol, name, exchange}`; `region=us` drops anything whose MIC is not XNYS/XNAS/XASE. |
| **Breadth mutates shared state between requests** | No. `breadth_of` only reads; rows unchanged after a pass, two passes byte-identical; the cached snapshot is a tuple of copies. |
| **Breadth partition breaks on NaN / inf / `True` / "  " / "1e309"** | No. Non-finite and unparseable changes land in `unchanged` + `unchanged_missing`; `adv+dec+unch == universe_total` and `Σ sector.count == universe_total` hold. |
| **Alias shadows a legacy key** (`pe` vs `pe_ttm` vs `forward_pe`) | No. `binding()` runs first, so the 82 Finviz keys win; `pe`→`pe_ttm`, `forward_pe`→`pe_forward`, `pe_ttm`→itself. |
| **A catalogue key silently dropped instead of 422** | No. `enterprise_value`, `cash_per_share`, `employees` → 422 `unavailable_filter_key` naming the key. |
| **A filter accepted but silently ignored** | None found. The only "accepted but ineffective" case is a column that matches nothing because it is null everywhere — which is the pre-existing "unknown matches no filter" rule, not a new swallow. |
| **A key assigned twice in the views partition without failing** | No. `ViewDefinition.__post_init__` catches within a view, `_validate()` catches across views, and `tests/test_screen_views.py:125` re-derives the partition from the catalogue **JSON file**, not from the module. |
| **`all` view still works** | Yes: 116 keys in catalogue order, `view("all").columns == CATALOG_KEYS`, served with values (where mapped). |
| **rename `total` → `matched` breaks a caller** | No caller found. `ui/app.js` reads `selection.total` (the select route, unchanged) and never the browse `total`; `scripts/` does not read it; `snapshot_document` and the multi-source `_build` (`**snapshot`) both carry `matched`/`universe_total`. Live: `GET /screens?region=us` → `matched 3896, universe_total 3896, returned 20`, no `total`. |
| **Import-direction rules** | Clean: `tests/test_import_direction.py` 11 passed; `discovery.py` imports only `app.*` domain modules (no requests/urllib/sqlalchemy). |

---

## 3. Tests that pass for the wrong reason

1. `tests/test_screen_selection_panel.py:248` — `test_a_hundred_rows_of_twelve_columns_stay_under_
   sixty_kilobytes` asserts only the key set and the byte size. Its fixture `projected_rows()`
   (`:130`) writes `pe_forward`, `price_to_cash`, `ev_to_sales` while the request asks for
   `forward_pe`, `pc`, `ev_sales`, so 5 of 12 cells are null *by construction*; and null is 4 bytes.
   On the harness the same request is 100 % null (24,094 B) and the assertion still passes.
2. `tests/test_api_screens_etag.py:229` (same panel, over HTTP) — `assert len(response.content) <
   60_000` passes on the real pipeline **because every cell is null**; a *correctly* filled
   12-column panel is ~24.8 KB, so the budget assertion is insensitive to the bug in both directions.
3. `tests/test_screen_views.py:712` — `test_a_complete_row_fills_every_view` feeds
   `V.build_view` a `{key: 1.0 for key in CATALOG_KEYS}` dict, i.e. the renderer's own ideal input.
   It never calls `app.screen.selection.view_row`, the function the API uses, which maps through
   `VIEW_FIELDS`. This test would pass on the pre-diff code and passes on the broken code.
4. `tests/test_api_screens_etag.py:143` — `test_the_tag_survives_a_clock_tick` passes for *any* tag
   that is not time-derived, including a constant. Nothing in the file asserts that a **data** change
   moves the tag, which is the property F3 breaks; the sibling test only varies the request.
5. `tests/test_screen_selection_panel.py:417` — `test_the_alias_layer_is_the_depth_overlay_table`
   asserts `criterion.field == SCREEN_ALIASES[alias]`, i.e. that the table is applied to itself. A
   wrong mapping (`average_volume` → `avg_volume_10d`) passes by definition.
6. Minor: `tests/test_screen_selection.py:423` asserts `limit=PANEL_LIMIT+1` clamps, using a 23-row
   fixture where `returned == 23` cannot distinguish a 100-row clamp from a 23-row clamp.

Not a wrong-reason test, but worth recording: the whole suite (**4249 passed**) is green on a tree
where two of the nine views return nothing.

---

## 4. Environment note

The process on `:8099` (pid 4008744, started 22:21:29Z) serves fill values for catalogue-spelled
`columns` — not because the working tree resolves them (it does not: `_column_row` is verbatim) but
because its runtime has a warm depth store (4,217 symbols) whose 116 catalogue keys the overlay
copies onto each row. That is why F2 needs both probes: the harness (no store) shows the hole, the
live service shows the *inconsistency* it creates. Decisive check — `GET /market/depth?symbols=ARL`
holds `pe 35.07843137254902`, `market_cap 288960049.27`, `pc 2.9`, `price_to_cash null`,
`average_volume 5801.5` (94 keys), and `POST /screens/select … "columns":["pe","market_cap","pc",
"price_to_cash"]` for ARL returns exactly `35.07843137254902 / 288960049.27 / 2.9 / null` — the
column values are the depth store's, read under its catalogue key, not the scan's.
Live measurements used here: `columns`, `view`, `/market/breadth`, `/symbols/search`, `/screens`,
`/market/depth`; no state was written and the process was not restarted.
