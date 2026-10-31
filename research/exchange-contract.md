# The exchange contract — `exch` grammar, value aliases, and `GET /market/exchanges`

**Repo** `/home/ubuntu/ztockz` @ `a71fd93` · **Live** `127.0.0.1:8099` (uvicorn, read-only, no writes, no new
process) · **Date** 2026-10-31 · **Scope** the API/contract half only. No code was changed; every
number below was produced by calling the running service or by importing the module under its own
venv (`.venv/bin/python`) and timing the function the route calls.

The question this document answers: *what must the contract layer change so that `exch` accepts the
words a human knows, publishes the venues as data, and refuses to lie when it cannot answer.*

---

## 0. Decisions

| # | Decision | Why |
|---|---|---|
| D1 | `exch` keeps `eq`/`ne`/`in`/`contains` and its `text` kind; what changes is the **value**, which gains a closed alias table resolved at compile time. | `exchange` is an identifier column (`filters.py:99-102`), and the DSL already splits identifier ops from `contains`. |
| D2 | The alias table lives in `app/screen/filters.py`, keyed by **row column**, resolved inside `_value`. | It is the one place a raw value becomes a `Filter`; every caller (`apply_filters`, `/screens/select`, export) then sees the canonical code for free. |
| D3 | Not `SCREEN_ALIASES`, not `LEGACY_COLUMN_ALIASES`, not `x-filter-aliases`: all three map **names to fields**, and none of them can hold a value without changing a row cell or failing its own boot check. | §2. |
| D4 | `GET /market/exchanges?region=&asset_class=&common_only=` publishes `{code, label, count, share, aliases}` over **the selection's own universe**, sharing its scan. | Counts must equal filtered totals; breadth's universe is 102 rows larger (§3.4, measured). |
| D5 | An exchange value that names no row in the universe is a **422**, not a 200 with `total: 0`. A *combination* that matches nothing stays a 200/0. | §1.4 and §6(a). |
| D6 | The `/screens/select` ETag already covers an exchange filter — **provided the alias is resolved before the `Criterion` is built**. No `v2` bump, no new revision input. | §4, proof at `selection.py:1045` + `screens.py:415-426`. |

---

## 1. The grammar of `exch`, as it is at `a71fd93`

### 1.1 The key

| Property | Value | Citation |
|---|---|---|
| key | `exch` | `app/screen/filters.py:641` |
| label / tab / UI control | `Exchange` / `descriptive` / `modalSelect` | `filters.py:642-644` |
| row column | `exchange` | `filters.py:645` |
| Finviz option tokens (stored, **not published**) | `("", "amex", "cboe", "nasd", "nyse", "modal")` | `filters.py:646` |
| kind of the column | `"text"` | `filters.py:137` |
| operators accepted | `eq`, `ne`, `in`, `contains` | `filters.py:254-261` |
| operators refused | `gt`, `gte`, `lt`, `lte`, `between` (`NUMERIC_OPS`) | `filters.py:257`, `514-517` |
| value type | `str` for `eq`/`ne`; tuple of ≥1 `str` for `in`; non-empty `str` for `contains` | `filters.py:439-450` |
| string DSL form | `exch eq XNYS`, `exchange in (XNAS, XNYS)` | `filters.py:266-275`, `361-375` |
| wire form | `{"key": "exch", "op": "eq", "value": "XNYS"}` or `{"field": "exchange", ...}` (exactly one of `key`/`field`, `selection.py:481-490`) | `selection.py:466-536` |

**Where values are normalised today — and where they are not.** The operator token is case-folded and
`=`/`==` collapse to `eq` (`filters.py:409-415`); one pair of surrounding quotes is stripped
(`filters.py:350-358`); `in` members are stripped and unquoted (`filters.py:361-375`, `444-447`).
The scalar value of `eq`/`ne` is **not** trimmed and **not** case-folded — `filters.py:448-450`
returns `raw` verbatim, and `_matches` compares with `text == expected` (`filters.py:603-604`).
Consequences measured live:

| Request | Result |
|---|---|
| `{"key":"exch","op":"eq","value":"XNYS "}` (trailing space) | `200`, `total 0` |
| `{"key":"exch","op":"eq","value":"nyse"}` | `200`, `total 0` |
| `{"key":"exch","op":"eq","value":"XNYS"}` | `200`, `total 2009` |

### 1.2 Every error a bad value produces (reproduced, verbatim)

`POST /screens/select` with `{"mode":"criteria","region":"us","criteria":{"filters":[<FILTER>],"limit":1}}`:

| `<FILTER>` | HTTP | `error` | `errorMessage` |
|---|---|---|---|
| `{"key":"exch","op":"gt","value":1}` | 422 | `invalid_filter` | `criteria.filters[0] (key 'exch'): operator 'gt' needs a numeric field; 'exchange' is text` |
| `{"key":"exch","op":"between","value":"1..2"}` | 422 | `invalid_filter` | `... operator 'between' needs a numeric field; 'exchange' is text` |
| `{"key":"exch","op":"eq","value":123}` | 422 | `invalid_filter` | `... exchange expects a string, got 123` |
| `{"key":"exch","op":"eq","value":null}` | 422 | `invalid_filter` | `... exchange expects a string, got None` |
| `{"key":"exch","op":"in","value":[1]}` | 422 | `invalid_filter` | `... exchange 'in' expects strings, got [1]` |
| `{"key":"exch","op":"in","value":[]}` | 422 | `invalid_filter` | `... exchange 'in' expects at least one value` |
| `{"key":"exch","op":"contains","value":"  "}` | 422 | `invalid_filter` | `... exchange 'contains' expects a non-empty string, got '  '` |

All seven are decided in `_criterion` before a scan runs — measured wall time **0.0 s** against
2.8 s for a cold `200` — because `parse_selection` runs at `screens.py:1097-1103`, ahead of
`_scan_plan`/`_selection_scan` (`screens.py:1175-1188`). That "a refusal costs no scan" property is
worth keeping for the new alias errors (§6a).

### 1.3 What the endpoint answers today for a *well-formed* value

Measured 2026-10-31, `region=us`, `universe_total 5576`:

| Filter | HTTP | `total` | Note |
|---|---|---|---|
| `exch eq XNYS` | 200 | **2009** | the only correct spelling |
| `exch eq NYSE` | 200 | **0** | the human spelling of the same venue |
| `exch eq nyse` | 200 | 0 | |
| `exch eq nope` | 200 | **0** | a typo is indistinguishable from an empty market |
| `exch eq "XNYS "` | 200 | 0 | trailing space |
| `exch ne NYSE` | 200 | **5576** | the whole universe, because nothing equals `NYSE` |
| `exch in ["XNAS","XNYS"]` | 200 | 5317 | `= 3308 + 2009` |
| `exch in ["NYSE","NASDAQ"]` | 200 | **0** | |
| `exch contains "S"` | 200 | 5575 | `contains` is a substring, not a lookup |
| `{"field":"exchange","op":"eq","value":"XNYS"}` | 200 | 2009 | the `field` form is the same predicate with `key: null` |

The universe's exchange vocabulary, taken by walking all 5,576 rows (`columns:["symbol","exchange"]`,
56 pages — and note each page is a **separate scan**, because `offset` is part of the scan key,
`screens.py:601-638`; the walk cost 156.8 s):

| code | rows |
|---|---|
| `XNAS` | 3308 |
| `XNYS` | 2009 |
| `XASE` | 258 |
| `XCBO` | 1 |
| **sum** | **5576 = `universe_total`** |

For comparison, `region=us_etf` is 5,776 rows (`XASE 2739`, and a 100-row sample is `XASE 72 /
XNAS 23 / XCBO 4 / XNYS 1`), and `region=global` is 40,774 rows whose tokens are **provider codes,
not MICs** (`TSE`, `LSE`, `TSX`, `SSE`, `EURONEXT`, `XETR`, `SIX`, `HKEX` …) because `mic_for` maps
only the four US venues and returns everything else unchanged (`universe.py:302-306`, `130-138`).
Any value table is therefore *per region*, and `global`'s is open-ended by construction.

### 1.4 The honesty gap, stated as a contract

`exch eq nope` answering `200 / total 0` says "no listed company trades on `nope`", which is true and
useless: the same bytes are what a correct spelling of a real venue returns when the region has no
listing. The filter cannot tell "not a venue" from "no rows", so the caller cannot tell either.

And this is **not an exchange bug**. The same class of mismatch exists on every text key whose row
values come from the provider rather than from Finviz's panel:

| Filter | HTTP | total |
|---|---|---|
| `sec eq technology` | 200 | **0** |
| `sec eq Technology` | 200 | **0** |
| `sec contains tech` | 200 | 1744 |

`/market/breadth` shows why: the 21 live sector buckets are `Finance 1610`, `Health Technology 830`,
`Technology Services 576`, `Electronic Technology 338`, … (`/market/breadth?region=us`, measured).
The Finviz token `technology` is simply not a value any row carries. So the plan must ship a
**mechanism** (a declared value vocabulary per column), not an `if key == "exch"` special case — and
must ship it for one column first, because `exch` is the only text column whose canonical set is
closed in code (`MIC_BY_EXCHANGE`, four entries) instead of provider-owned.

### 1.5 What a client can learn today

`GET /screens/select/schema` publishes `x-filter-keys.exch = {"label","tab","field","kind"}`
(measured) — **no `options`, no value list**. `Binding.as_dict` deliberately omits `options`
(`filter_fields.py:307-318`) because Finviz's bucket tokens are UI vocabulary, not values. The
consequence is that the four human spellings exist in the code (`filters.py:646`) and in the
published contract nowhere. A schema-driven picker can only offer `XNYS`.

---

## 2. Where a value alias belongs

### 2.1 The three mechanisms already in the tree

| | `SCREEN_ALIASES` | `LEGACY_COLUMN_ALIASES` | filter aliases (`x-filter-aliases`) |
|---|---|---|---|
| file | `app/screen/depth_overlay.py:82-88` | `app/screen/views.py:614-616` | `app/screen/selection.py:1102-1140` |
| size at HEAD (measured by import) | **32** (the "18" in `columns.py:5`'s docstring is stale) | **23** | **105** |
| direction | catalogue key → row column | legacy column → tuple of catalogue keys | catalogue key → `{field, kind, label}` |
| derived from | `CATALOGUE_COLUMNS`, filtered to `field != key` (`depth_overlay.py:85-87`) | `catalogue_keys_for(name)` over the frozen tabs (`views.py:601-616`) | `binding()` + `_alias_column` (`selection.py:1119-1134`) |
| readers | `overlay()` row merge (`depth_overlay.py:191-217`), export/snapshot aliasing | the frozen vanilla views only | the published schema (`selection.py:1397`) |
| its own validator | projection test against the one table | `views._validate`: every target must be a catalogue key (`views.py:697-710`) | none needed: built from the validator's own tables |
| lifecycle | permanent | **scheduled for deletion** with the vanilla client (`views.py:450-454`) | permanent |

### 2.2 Why none of the three is the right home

- **`SCREEN_ALIASES` is a row-cell map.** Its only consumer renames keys *on the row*
  (`depth_overlay.overlay`, `depth_overlay.py:191-217`, called from `screens.py:1228`,
  `snapshot.py:148`, `export.py:455`). An entry `"nyse": "XNYS"` there would be read as "the row
  column `nyse` is spelled `XNYS`" and, worse, any table that *did* apply it would be rewriting the
  cell the response returns — exactly the failure §6(c) forbids. Its key space is metric names, and
  it is a projection: `depth_overlay.py:71-76` says a second literal in it fails
  `test_one_table_is_the_only_mapping`.
- **`LEGACY_COLUMN_ALIASES` is frozen compatibility.** It exists so `ui/app.js` keeps working, it is
  built only from the names the frozen tabs render (`views.py:601-611`), and adding `"nyse"` would
  trip its own import-time check — "alias targets that are not catalogue keys" (`views.py:706-710`),
  because `nyse` is not a catalogue key and `XNYS` is not one either. It also dies with the client
  (`views.py:452-454`), which is the opposite of a contract that should outlive a UI.
- **The filter aliases are key-level by definition.** `_FILTER_ALIASES` answers "which *column* does
  this *key* name" (`selection.py:1102-1116`); a value has no key position, and the table is
  derived, not written, so it cannot host a literal without becoming a second source of truth.

### 2.3 Recommendation: a value table on the column, resolved in `compile_filter`

**None of the three.** If forced to pick the nearest, it is the filter-alias table — the only one of
the three already published to clients, and the only one built from the validator's own tables — but
its key space is *keys*, and putting a value in it would make one table answer two questions with
two different key spaces, which is the drift `columns.py:5-18` exists to end.

**Shape** — one literal in `app/screen/filters.py`, typed
`Mapping[str, Mapping[str, tuple[str, ...]]]` and keyed by **row column** (not by filter key, so
`{"field":"exchange"}` and `{"key":"exch"}` cannot disagree):

```python
#: Row column → canonical code → the spellings that name it. Only a column whose
#: values are a closed vocabulary in code (MIC_BY_EXCHANGE) may appear here.
VALUE_ALIASES: Mapping[str, Mapping[str, tuple[str, ...]]] = MappingProxyType({
    "exchange": MappingProxyType({
        "XNYS": ("nyse",),
        "XNAS": ("nasdaq", "nasd"),
        "XASE": ("nyse american", "amex"),
        "XCBO": ("cboe",),
    }),
})
```

**Why this file.** `_value(field, kind, op, raw)` (`filters.py:418-450`) is the only place a raw value
becomes a `Filter`; `compile_filter` is called by `apply_filters` (`filters.py:624`), by `_criterion`
(`selection.py:526`) and therefore by every route and by `export`/`snapshot`. One resolution point
means the `Criterion` the ETag hashes and the row the matcher compares cannot disagree. `filters.py`
imports nothing from `app.*` (`filters.py:105-112`), so the table cannot create the cycle that
`universe.py` ↔ `filters.py` would (`universe.py` already imports `filters`).

**No invented vocabulary.** The alias side is the probe's own tokens (`filters.py:646`) plus two long
spellings whose Finviz token is an abbreviation of a different name; the canonical side is
`MIC_BY_EXCHANGE.values()` (`universe.py:130-138`). A unit test pins both directions
(§5, T1), so the table cannot grow a word Finviz never showed and cannot name a code the product
never produces.

**Resolution semantics** (this is also §6(a)'s answer):

```
resolve(column, raw):
    table = VALUE_ALIASES.get(column)
    if table is None: return raw                      # untouched, as today
    token = raw.strip()                               # a trailing space is a typo, not a venue
    for code, spellings in table.items():
        if token == code or token.casefold() in spellings: return code
    raise UnknownValue(column, raw, accepted=table)    # 422, never a pass-through
```

- Case-insensitive in, canonical out (`nyse`→`XNYS`, `NYSE`→`XNYS`, `NySe`→`XNYS`).
- `strip()` only: no prefix, no fuzzy, no `startswith` — that is how `nasdax` would silently become
  `XNAS`.
- `contains` is **not** aliased: it is the operator a human types (`filters.py:99-102`), and
  `contains "S"` (5,575 rows) must keep meaning a substring.
- For `in`, each member resolves through the same call, duplicates collapse preserving first-seen
  order, and the empty-list refusal (`filters.py:373-374`) is unchanged.

**Publishing it.** `Binding.as_dict` (`filter_fields.py:296-318`) gains a `values` block for a key
whose field has a table — the same extension shape as `choice` (`filter_fields.py:314-315`):

```json
"exch": {"label": "Exchange", "tab": "descriptive", "field": "exchange", "kind": "text",
         "values": {"codes": ["XNAS","XNYS","XASE","XCBO"],
                    "aliases": {"nyse":"XNYS","nasdaq":"XNAS","nasd":"XNAS",
                                "nyse american":"XASE","amex":"XASE","cboe":"XCBO"}}}
```

This rides into `/screens/select/schema` for free (`selection.py:1397`), and
`schemas/stock_selection_request_v1.json` must be regenerated because the served document and the
artifact are compared byte-for-byte (`screens.py:1020-1023`). `_filter_value_schema`'s description
(`selection.py:1142-1159`) must gain one sentence: for `exch` the value is a MIC **or** a published
spelling, and anything else is a 422 — today it ends by telling the caller that Finviz's option
tokens are "display buckets, not values", which is precisely the rule `exch` now needs an exception
to.

**Why not a value `enum` in the request schema.** `value` is shared by all 187 keys
(`selection.py:1392`); an `enum` there would forbid every numeric filter. The vocabulary
belongs on the key (`x-filter-keys.exch.values`), where a client can find it without a second
document.

---

## 3. `GET /market/exchanges`

### 3.1 Route and parameters

```
GET /market/exchanges?region=us&asset_class=stock&common_only=false
```

| Param | Type | Default | Validation |
|---|---|---|---|
| `region` | text | `us` | one of `BREADTH_REGIONS` (`discovery.py:123`) → else `422 invalid_region` |
| `asset_class` | text, optional | family of `region` | `stock`/`etf`; `global`+`etf` refused (`discovery.py:690-704`) |
| `common_only` | bool | `false` | parsed as text like `limit` (`discovery.py:721-747` is the precedent) → else `422 invalid_flag` |

Validation must reuse `_universe_family` (`discovery.py:673-704`) verbatim, not a new parser: the
refusal *is* the contract, and two parsers drift. `common_only` earns a parameter because it changes
the population by 412 rows and 297 `XNYS` rows (measured: `universe_total 5576 → 5164`,
`XNYS 2009 → 1712`); an endpoint without it would publish a number no screen can reproduce.

### 3.2 Shape

```json
{
  "as_of": "2026-10-31T15:43:49+00:00",
  "region": "us",
  "asset_class": "stock",
  "common_only": false,
  "universe_total": 5576,
  "code_total": 4,
  "exchanges": [
    {"code": "XNAS", "label": "NASDAQ",        "count": 3308, "share": 0.5933, "aliases": ["nasdaq","nasd"]},
    {"code": "XNYS", "label": "NYSE",          "count": 2009, "share": 0.3603, "aliases": ["nyse"]},
    {"code": "XASE", "label": "NYSE American", "count": 258,  "share": 0.0463, "aliases": ["nyse american","amex"]},
    {"code": "XCBO", "label": "Cboe BZX",      "count": 1,    "share": 0.0002, "aliases": ["cboe"]}
  ],
  "method": {
    "universe": "the population /screens/select filters over: build_universe(floor 0 for stocks, common_only as sent)",
    "count":    "one row per issuer per code; the counts partition universe_total",
    "share":    "count / universe_total, null when universe_total is 0",
    "label":    "a vendored human name; null means none is known and the code is the only name",
    "aliases":  "the spellings `exch eq` accepts for this code, from the same table the filter reads"
  }
}
```

`as_of` is **when these counts were computed** (the tally's build moment), not when the request
arrived — a warm read must not read as a fresh measurement. No `stale` field: this route shares the
selection's `ScanCache`, whose contract is that "a failure is never an answer"
(`screens.py:659-662`), so there is no stale snapshot to label. A boolean that can only ever be
`false` is a lie waiting for its first `true`.

Invariants (each one is a test in §5):

- `sum(e.count) == universe_total` — breadth makes the same promise for sectors
  (`discovery.py:187-190`); this is the property that lets a client draw a 100 % bar.
- `count == POST /screens/select {"key":"exch","op":"eq","value":code}.total` for every entry.
- Ordering: `count` descending, then `code` ascending — the same rule as breadth's sector sort
  (`discovery.py:331-333`), so the order is stable across calls and diffable.
- `label` is `null`, never the code echoed back: a label that repeats the code cannot be told from a
  real name that happens to be a code, and the null says which it is.
- `code_total` is stated so a reader never has to count a JSON array to know whether the list was
  truncated. It is never truncated: the list **is** the vocabulary.
- `share` is `null` when `universe_total == 0` (a 0.0 share would be a fabricated ratio — the rule
  breadth applies to `avg_change_pct`, `discovery.py:242-244`).

Sizes measured: `us` = 4 entries, 528 B encoded; `global` = 40,774 rows and **90 distinct provider
codes**, 7,059 B — still under one panel, and it must not be paginated: a vocabulary with a page
size is a vocabulary that lies about what exists.

### 3.3 Cache policy

| Layer | Policy | Reason |
|---|---|---|
| the scan | the app's existing `ScanCache`, TTL `SCAN_CACHE_TTL_S = 5 s` (`screens.py:598`), keyed by `_scan_key` **exactly as a criteria query keys it**: `columns=PARITY_COLUMNS`, `limit=FULL_SCAN_ROWS`, `min_market_cap=None`, `subtype=None` (`screens.py:847-864`) | a panel that opens both `/screens/select` and `/market/exchanges` inside 5 s pays **one** provider scan, and the two routes cannot describe different pages |
| the tally | memoised per `(region, asset_class, common_only, generation)` — one small dict per entry, last two generations kept | the pass is 0.687 ms (§3.5); per request it is noise, per *generation* it is free |
| the build | one `build_universe` per `(generation, flags)`, shared by every request in that window | 237 ms measured on `us`, **3.65 s on `global`**: one build per window, never per request |
| freshness signal | `as_of` in the body, `X-Scan-Cache` in the header (the same observable `/screens/select` emits, `screens.py:1293`) | a client can tell "scanned" from "replayed" without a second call |
| failure | `503 scanner_unavailable`, one attempt per request and one per concurrent burst (single-flight, `screens.py:745-763`) | the same behaviour `/screens/select` has today; if a negative cache is wanted it belongs in `ScanCache` for both routes, not in one route's private cache |

**Do not add a second cache with a different floor.** That is precisely how `/market/breadth`
(5,678) and `/screens/select` (5,576) came to disagree today (§3.4). One population, one builder,
one key.

### 3.4 The trap: breadth's universe is **not** the filtered universe (measured)

One provider page (5,715 raw rows), the same `build_universe` call, one argument different:

| build | floor | total | XNAS | XNYS | XASE | XCBO |
|---|---|---|---|---|---|---|
| breadth (`discovery.py:388-395`) | `min_market_cap=None` | **5678** | 3326 | 2085 | 266 | 1 |
| select (`screens.py:1199-1203`) | `0` for stocks | **5576** | 3308 | 2009 | 258 | 1 |
| difference | | −102 | −18 | **−76** | −8 | 0 |

The 102 rows are stocks the provider sends with no `market_cap_basic`; a floor of `0` drops them
because "a row with no cap cannot prove it fits" (`universe.py:432-438`), while `None` keeps them.
A counter modelled on breadth would therefore publish `XNYS: 2085` while `exch eq XNYS` answers
`2009` — a **3.8 % over-count on the largest human-visible venue**, and the one number a reader is
most likely to check. The endpoint must use the select call, not the breadth one; that is the whole
reason §3.3 shares the criteria scan key.

### 3.5 What this costs (measured on this box)

| Operation | `us` | `global` |
|---|---|---|
| scan (provider) | ~1.2 s warm-process / 2.0 s cold (`screens.py:587-597`) | **16.2 s** (96,933 raw rows) |
| `build_universe` | **237 ms** (5,715 → 5,576) | **3.65 s** (96,933 → 40,774) |
| tally (`Counter(r["exchange"] …)`) | **0.687 ms** (5,576 rows) | not measured separately |
| `breadth_of` pass (for reference) | **11.25 ms** (21 sector buckets + 2×52w) | — |
| document encode | **0.018 ms** (528 B) | 7,059 B |
| `/market/breadth` end-to-end | **14 ms** warm, **1.2 s** cold (3,267 B, no ETag) | — |
| `/screens/select` | 2.8 s cold at 100 rows; warm revalidation `304` **18 ms** | 22.4 s cold |

So an endpoint that recomputed the tally per request would add 0.7 ms to a 14 ms-shaped route (5 %) —
not a crisis, but pointless; one that *scanned* per request would add 1.2 s (`us`) or 16 s
(`global`), which is the mistake `/market/breadth` already avoids. The memoised shape costs
**0.02 ms steady state** and **237 ms / 3.65 s once per 5 s window**, with **zero** new provider
calls whenever a selection already fetched the page.

### 3.6 ETag

The body is 528 B, so hashing it would be cheap — but a body hash can only be taken *after* building,
so it cannot save the build, and `as_of` (the tally's build moment) would make two tallies of one
unchanged universe different entities. So the tag is **weak** over the answer's *inputs*, the shape
`/screens/select` already uses and for the same reason (`screens.py:215-230`, `895-903`,
`selection.py:1082-1084`):

```
W/"exch-<sha256(rev)>"
rev = f"v1;region={region};family={family};common_only={int};scan={generation};total={universe_total};vocab={vocab_hash}"
```

| input | source | why it must be in the tag |
|---|---|---|
| `region`, `family`, `common_only` | the validated request | a different tab is a different vocabulary and a different population |
| `scan={generation}` | `ScanCache.page()` (`screens.py:721-763`) | a re-scan always moves it, so a changed universe cannot answer `304` (`screens.py:397-404`) |
| `total={universe_total}` | the built universe | insurance: a build-flag change that does not move the generation still moves the tag |
| `vocab={vocab_hash}` | sha256 over the sorted `(code, label, aliases)` triples | labels and aliases are code, not data: relabelling `XASE` changes the body while no store moves, and a tag that ignored it would `304` a stale label |

- `304` path: `not_modified(request, etag)` **before** the build (`app/api/cache.py:82-85`), so a
  revalidation costs a generation lookup, not 237 ms.
- Headers: `cache_headers(etag)` → `ETag` + `Cache-Control: private, max-age=0, must-revalidate`
  (`app/api/cache.py:68-79`), plus `X-Scan-Cache` on the `200` (`screens.py:1293`).
- `/market/breadth` today sends **neither** an `ETag` nor `Cache-Control` (measured: only `date`,
  `server`, `content-length`, `content-type`), so no client can revalidate it at all. The new route
  must not copy that; its 304 path is the reason it is cheap to poll.

### 3.7 When the universe changes

| Event | The endpoint | A filter |
|---|---|---|
| a venue appears (a new code in the scan) | appears with `count`, `label: null` unless vendored, `aliases: []` | `exch eq <code>` is accepted — gate 2 (§6a) reads the live set |
| a venue disappears | leaves `exchanges`; `code_total` drops; `share`s renormalise | `exch eq <code>` → 422 `unknown_value` naming the live codes |
| counts move (listings added or removed) | new `count`s, new `universe_total`, new `scan=` → new `ETag` | `total` moves with it; both read the same generation |
| the scan fails | `503 scanner_unavailable` (single attempt per burst) | `503` today too (`screens.py:1189-1196`) |
| the alias or label table is edited | `vocab=` moves → new `ETag`; the JSON changes with no data change | resolution changes with it, and an affected filter's tag moves because the **compiled value** moves (§4.3) |
| a code exists in the table but no row carries it in this region | absent from `exchanges` | 422, not `0` (§6a) |

The one thing this design cannot promise is a count that survives an outage — deliberately. A count
that outlives its scan is a number no `total` can reproduce, which is the failure mode §3.4 already
demonstrates between two routes.

### 3.8 Failure modes

| Condition | Status | Body | Scan spent |
|---|---|---|---|
| `region` not in `BREADTH_REGIONS` | 422 | `{"error":"invalid_region", …}` | none (`discovery.py:682-689`) |
| `asset_class` outside `stock`/`etf` | 422 | `invalid_asset_class` | none |
| `global` + `etf` | 422 | `asset_class_mismatch` | none |
| `common_only` not a boolean token | 422 | `invalid_flag` (new code) | none |
| scanner failed | 503 | `scanner_unavailable` + the reason | one attempt per burst, shared by single-flight |
| scan OK, zero rows | 200 | `exchanges: []`, `universe_total: 0`, `share: null` | one |
| a code with no vendored label | 200 | `label: null`, `count`, `aliases: []` | none |
| an alias in the table that no row carries | 200 | the code is simply absent; `exch eq <alias>` is then a 422 (§6a) | none |

An empty `exchanges` array is only ever published with `universe_total: 0`, and that body is a
statement about the scan, not about the market.

---

## 4. The ETag verdict for `/screens/select` with an exchange filter

### 4.1 What the tag is made of

```
W/"select-<sha256(revision line)>-<sha256(canonical_request)[:32]>"
     ^ screens.py:230        ^ selection.py:1059-1071
```

- revision line: `v2;snapshot=…;depth=…;scan=…;fund=…;facts=…;logo=…` — `screens.py:415-423`.
- canonical request: mode, region, asset_class, **filters**, sort, limit, offset, symbols, source,
  snapshot_id, view, columns, common_only, fill_wide — `selection.py:1041-1056`.
- the filter half is `[criterion.as_dict() for criterion in request.filters]` (`selection.py:1045`),
  and `as_dict` is exactly `{"key","field","op","value"}` (`selection.py:290-294`).

### 4.2 Does it already cover an exchange filter?

**Yes — the filter value is an ETag input, and it is the *compiled* value.** Proof by line:
`selection.py:1045` (filters enter the canonical request) → `selection.py:1068-1071`
(`json.dumps(sort_keys=True)` + `sha256(...)[:32]`) → `screens.py:230` (the tag) →
`screens.py:1173-1180` (the warm fast path recomputes the tag from the *parsed* request before any
build). Measured end-to-end: `exch eq XNYS` at `limit 100` returns
`W/"select-e70f16…-30def9a93a28e097e4bd526b1448b0fc"`; replaying it yields `304` in 18 ms with no
body, against 2,813 ms for the `200` that built the panel.

Two properties this buys, both verified in-process by calling `request_digest` at HEAD:

| criterion | digest |
|---|---|
| `value="XNYS"`, `key="exch"` | `76daff803ad259702a4e0fab761e2603` |
| `value="NYSE"`, `key="exch"` (today) | `b627f5217926a1f0d473b2103fe934d3` |
| `value="XNYS"`, `key=null` (`field` form) | `71ab4d19e396319ba338199ab9e07d30` |

So **the moment `NYSE` compiles to `Criterion.value == "XNYS"` the two spellings collapse onto one
tag** — the same tag the correct spelling already has, with no change to `_data_revision`. And
because a re-scan always moves `scan=` (`screens.py:397-404`), a change in the universe that alters
which rows match still moves the tag.

### 4.3 The one requirement this places on the implementation

The alias must be resolved **before** the `Criterion` is constructed, i.e. inside
`compile_filter`/`_value` (`filters.py:418-450`), not at match time in `_matches`
(`filters.py:559-611`) and not in `apply_filters`. If it were applied later:

- `{"value":"NYSE"}` would keep hashing `NYSE`, so a **later edit to the alias table** (or the
  deploy that introduces it) would change the answer while every warm client's tag still matched →
  a `304` for a body the client never read. That is the F3 failure mode the `v2` revision was rewritten
  to remove (`screens.py:381-388`).
- The response would also echo `NYSE` in `filters` while the predicate compared `XNYS`
  (`selection.py:834` echoes `criterion.as_dict()`), so the envelope would show a value that is not
  the value.

Resolve first, and **no `v2`→`v3` bump is required**: the revision *composition* does not change,
and every aliased spelling simply acquires the tag of its canonical value on the next request (a
`200`, the safe direction — `screens.py:386-388`).

### 4.4 Two asymmetries to leave alone

- `{"field":"exchange"}` and `{"key":"exch"}` are one predicate with two digests (`key` is hashed).
  Normalising `key`→`field` would move every existing client's tag; it is unrelated to this change
  and must not ride along.
- The echoed `filters` value stays canonical (`XNYS`). Adding `as_written` to `Criterion.as_dict()`
  would put the caller's spelling back into `canonical_request` and re-split the tags — if the
  product wants to show the spelling, it must be added to the *response* projection, not to
  `as_dict()`.

---

## 5. Tests the plan must add

Each case names the assertion, not the scenario. Existing conventions: `tests/test_screen_filters.py`
(DSL, no API), `tests/test_screen_selection_api.py` (the route over a fixture scanner),
`tests/test_api_discovery.py` (breadth/route + cache, `_client`/`_loader` fixtures at
`tests/test_api_discovery.py:95-176`), `tests/test_screen_schema.py` (the artifact).

**A. Alias mechanics — `tests/test_screen_filters.py`**

1. `test_the_exchange_value_table_is_derived_from_the_catalogue_and_the_mic_map` — asserts
   `set(VALUE_ALIASES["exchange"]) == US_EXCHANGES` and every alias is in
   `FILTER_CATALOG["exch"].options` or one of the two documented long forms. *Fails if the table
   invents a venue or forgets one the product emits.*
2. `test_exch_nyse_compiles_to_xnys_at_parse_time` — `compile_filter({"key":"exch","op":"eq","value":"NYSE"})`
   → `Filter(field="exchange", op="eq", value="XNYS")`. *The tag depends on this.*
3. `test_alias_lookup_is_case_insensitive_and_strips_surrounding_space` — `nyse`, `NYSE`, `NySe`,
   `" XNYS "` all compile to `"XNYS"`.
4. `test_an_unknown_exchange_value_raises_instead_of_passing_through` — `compile_filter(...
   "value":"nope")` raises, and `str(exc)` contains `XNYS`. *A rewrite-before-validation design
   fails here.*
5. `test_no_prefix_or_fuzzy_matching_of_exchange_values` — `"nasdax"`, `"nas"`, `"xn"` all raise.
6. `test_in_resolves_each_member_and_dedupes_in_first_seen_order` — `["nyse","XNYS","nasdaq"]` →
   `("XNYS","XNAS")`; `apply_filters` over a 3-row fixture returns exactly the `XNYS` and `XNAS`
   rows.
7. `test_contains_is_not_aliased` — `exch contains "nys"` still matches a `XNYS` row (substring),
   and `exch contains "nyse"` matches nothing. *Pins the identifier/substring split at
   `filters.py:99-102`.*
8. `test_the_alias_does_not_touch_a_rows_exchange_cell` — after `apply_filters`, the returned row
   objects are the same objects and `row["exchange"] == "XNYS"`; `column_map.field_for("exchange")
   == "exchange"` and `column_map.value_for(row, "exchange") == "XNYS"`. *This is §6(c).*

**B. Route behaviour — `tests/test_screen_selection_api.py`**

9. `test_exch_alias_equals_the_mic_count` — a fixture with mixed venues: `exch eq NYSE` and
   `exch eq XNYS` return the same `total`, the same `universe_total` and the same `rows[*].symbol`.
10. `test_an_unknown_exchange_value_is_a_422_in_the_house_envelope` — `exch eq NOPE` → 422,
    `error == "unknown_value"`, `errorMessage` names `XNYS`, and the fake scanner recorded **zero**
    calls. *A refusal must still cost no scan.*
11. `test_a_code_absent_from_the_universe_is_a_422_not_an_empty_screen` — with a fixture where no
    row is `XCBO`, `exch eq XCBO` → 422 (second gate), while the same request in a fixture holding
    one `XCBO` row → 200 `total 1`.
12. `test_a_combination_that_matches_nothing_is_still_a_200_with_zero` — `exch eq XNYS` AND
    `exch eq XNAS` (two valid values, one column: a row has one exchange) → 200, `total 0`. *Pins the
    422-vs-empty line.*
13. `test_the_exchange_filter_etag_is_spelling_insensitive` — the two requests of T9 carry the same
    `ETag`, and replaying it returns 304 with an empty body.
14. `test_the_exchange_filter_etag_moves_when_the_scan_generation_moves` — same request after
    `SCAN_CACHE_TTL_S` expiry → the `ETag` differs, and the new one replays as 304.
15. `test_exch_on_a_global_selection_accepts_a_provider_code` — `region=global`, `exch eq TSE` → 200
    and a non-zero total on a fixture with a `TSE` row. *Proves the canonical side stayed open where
    the vocabulary is provider-owned.*

**C. The endpoint — `tests/test_api_discovery.py`**

16. `test_exchanges_counts_match_the_filtered_totals` — for every entry from
    `GET /market/exchanges?region=us`, `POST /screens/select {"key":"exch","op":"eq","value":code}`
    returns `total == entry["count"]`, and `sum(count) == universe_total`.
17. `test_exchanges_uses_the_select_universe_not_the_breadth_one` — a fixture row with
    `market_cap_basic=None` must be **absent** from `universe_total` and from every count, while
    `/market/breadth` on the same fixture reports it. *This is the 5,678-vs-5,576 regression.*
18. `test_exchanges_ignores_common_only_by_default_and_honours_it_when_asked` — the same fixture
    with a non-common row: default count includes it, `common_only=true` excludes it, and the two
    `ETag`s differ.
19. `test_exchanges_scans_once_for_many_requests` — 20 sequential calls inside the TTL → the loader
    called once (the shape of `tests/test_api_discovery.py:421-437`), and the tally helper called
    once per generation.
20. `test_exchanges_etag_is_stable_within_a_generation_and_moves_after_it` — identical `ETag` on
    repeats, `304` on replay with no body, different `ETag` after the TTL, and `Cache-Control:
    private, max-age=0, must-revalidate` on both 200 and 304 (`app/api/cache.py:68-79`). Ordinary
    revalidation must also **not** call the scanner: the `304` costs a generation lookup.
21. `test_exchanges_publishes_a_label_or_null_and_never_echoes_the_code` — `XNYS` → `"NYSE"`; a
    fixture-only code → `label: null`, `aliases: []`, `count > 0`.
22. `test_exchanges_refuses_a_bad_region_before_it_scans` — `region=xx` → 422 `invalid_region` in
    `{error, errorMessage}` and the loader recorded zero calls (mirrors
    `tests/test_api_discovery.py:564-581`).
23. `test_exchanges_answers_503_when_the_scan_fails_and_has_no_stale_field` — blocked scanner →
    503 `scanner_unavailable` in `{error, errorMessage}`, the document never carries `stale`, and 20
    concurrent requests during the outage share **one** scan attempt (single-flight,
    `screens.py:745-763`; mirrors `tests/test_api_discovery.py:639-677`).

**D. The published contract — `tests/test_screen_schema.py`**

24. `test_the_schema_publishes_the_exchange_value_vocabulary` — `x-filter-keys.exch.values.codes`
    equals `sorted(VALUE_ALIASES["exchange"])`, `values.aliases` equals the flattened table, and
    `schemas/stock_selection_request_v1.json` still equals `SELECTION_REQUEST_SCHEMA` byte for byte
    (`screens.py:1020-1023`).

Two of these (T17, T18) are not hypothetical: they are the two measured divergences in §3.4 and
§3.1, and a plan that does not pin them will re-introduce a number that contradicts its own filter.

---

## 6. Adversarial answers

### (a) An alias layer that rewrites values before validation can turn a typo into a valid filter

It can, and the fix is structural, not a cleaner regex:

1. **The table is consulted, never applied.** Resolution is `dict` membership on a `strip()`-ed,
   case-folded token; there is no `re.sub`, no `startswith`, no edit distance. `nasdax` is not
   `nasd` with a suffix — it is a miss, and a miss raises.
2. **Canonical codes are matched before aliases**, and both sides are closed. On `us`/`us_etf` the
   canonical set is exactly the four MICs the product emits (`MIC_BY_EXCHANGE`), pinned by T1, so
   the accepted vocabulary cannot silently grow with the table's author's imagination.
3. **A miss raises a distinct error**: `UnknownValue` → `SelectionError(code="unknown_value", 422)`
   with the accepted codes in the message. It is not `invalid_filter` (that says "the grammar is
   wrong"); it says "the word is wrong", which is the client's actual bug.
4. **The second gate closes the remaining hole.** A spelling can be well-formed, in the table, and
   still name nothing in the region (`OTC`, `XNYS` on a region with no NYSE rows). Before the
   filters run, each text value with a table is checked against the universe's own code set — the
   same set §3 publishes. Not present → 422, with the live codes. This is what makes `nope`, `OTC`
   and `nse`-on-`us` all refuse instead of answering "the market is empty".
5. **The gate must not swallow true emptiness.** It applies to a *value*, never to a *combination*:
   `exch eq XNYS` AND `exch eq XNAS` (both valid, empty intersection — a row has one exchange) stays
   `200 / 0`, and T12 pins it. `ne` on a valid code returns the complement (measured
   `5,576 − 2,009 = 3,567`), not `5,576`.
6. **Order matters for cost.** Gate 1 is in `parse_selection` and costs no scan (measured 0.0 s);
   gate 2 runs after the universe exists and reads the memoised tally (~0.02 ms). Neither gate
   needs a provider call of its own.

**Where gate 2 goes.** In `select()`, between the universe build (`screens.py:1199-1207`, after the
two joins so the population is final) and `select_rows` (`screens.py:1240`). Not in
`compile_filter`: it has no universe and must stay pure, or the same call would answer differently
depending on when it ran. Not in `apply_filters` either: it is also the export and snapshot path,
where the universe is not the selection's. It raises `SelectionError(code="unknown_value")`, so the
422 wears the house envelope (`screens.py:1100-1103`). Criteria mode only — a `symbols` selection
carries no filters.

The one residual: a *canonical* code that is a real venue but has no row in the region is a 422 whose
message must say which region — otherwise the caller cannot tell "wrong word" from "wrong tab".

### (b) A count endpoint that scans 5,576 rows per request is a new hot path

Measured, this box: `breadth_of` over 5,576 rows = **11.25 ms**, a bare `Counter` = **0.687 ms**,
`build_universe` = **237 ms** (`us`) / **3.65 s** (`global`, 96,933 rows), the scan = **1.2 s**
(`us`) / **16.2 s** (`global`), the encoded 4-entry document = **0.018 ms**.
`/market/breadth` warm today costs **14 ms** end to end — so yes, the *existing* precedent already
pays a full pass per request, and copying it would put ~0.7 ms (tally only) or ~11 ms (if the shape
were copied wholesale) on a route a panel may call on every mount.

The cheaper shape, in the order it matters:

1. **Never scan in the route.** Reuse `ScanCache` with the criteria key (§3.3): a panel that also
   calls `/screens/select` pays **zero** extra provider requests, and a standalone caller pays at
   most one scan per 5 s window.
2. **Memoise the tally per generation**, not per request: `{(region, family, common_only,
   generation): {code: count}}`, last two generations (≈ 8 small dicts, a few hundred bytes).
   Steady state becomes a dict lookup plus 0.018 ms of JSON: the 0.687 ms pass runs once per
   generation, ≈ 34× less work per request, and ≈ 560× less than breadth's 11.25 ms pass.
3. **Pay the 237 ms build once per generation**, not per request. It is the same build
   `/screens/select` runs, and it is 4.7 % of a 5 s window in the worst case (a client polling the
   endpoint with no other traffic).
4. **Do not "optimise" by counting the scan page directly.** `page.rows` is 5,715 rows: 139 of them
   are OTC, foreign, cap-less or duplicate-issuer rows that `build_universe` removes
   (`universe.py:422-424`, `432-438`, `600-616`) and `universe_total` is 5,576. A counter over raw
   rows would be 2.5 % high, per-code wrong, and would drift the day `build_universe` changes.
   Exactness is worth 237 ms once per window.
5. **Keep the response small.** 4 entries on `us`, 90 on `global` (7,059 B measured) — under one
   panel in both cases. Do not paginate a vocabulary.

### (c) If `exch` is also used by the view/column projection, an alias must not change the column's value

It is, in three places, and all three read the row cell rather than the filter value:

- `CATALOGUE_COLUMNS["exchange"] = "exchange"` (`columns.py:183-185`) with the comment that a MIC on
  the row *is* the exchange column;
- `_EXTRA_ROW_COLUMNS` includes `exchange` (`columns.py:124-142`), so `"columns": ["exchange"]`
  projects it verbatim via `value_for` (`columns.py:557-571`);
- the browse row, the export CSV and the snapshot all carry it (`schema.py:211`,
  `export.py:66`, `snapshot.py:79`).

The design keeps them apart by construction: `Criterion.value` is a **predicate constant**, produced
in `compile_filter` (`filters.py:495-520`) and never written back to a row — `apply_filters`
documents that rows pass through untouched (`filters.py:619-621`) and returns the same objects. The
alias table is therefore not reachable from any row-building path, and specifically **must not be
added to `harmonise`** (`columns.py:578-600`), which is the one row-level rewrite point: its contract
is "fill a null canonical cell from a synonym", so a value entry there would rewrite
`row["exchange"]` and every consumer above would start printing `NYSE` where the MIC belongs — in
the response body, in the CSV, and in the depth-overlay key (`depth_overlay.py:115-130` keys on the
symbol, but `export` and `browse` would both ship the wrong cell).

T8 pins exactly this: same row object, `row["exchange"] == "XNYS"`, `value_for(row,"exchange") ==
"XNYS"`, while `Criterion.value == "XNYS"` came from a request that said `NYSE`.

---

## 7. Out of scope, with reasons

- **Sector/country/industry values.** Same disease (§1.4, measured), different cure: their canonical
  vocabulary is provider-owned and grows without a deploy (21 sector buckets today), so an alias
  table on a closed set is not available. The mechanism in §2.3 is the right shape; the *data* needs
  a source-of-truth answer first (the `/market/breadth` buckets are the closest thing to one).
- **Non-MIC codes on `global`.** They are legitimate row tokens — 90 distinct codes over 40,774 rows
  (measured); the endpoint publishes them and the filter accepts them, but no label or alias can be
  vendored for 86 provider spellings. `label: null` is the honest answer, not a transliteration
  table.
- **`key` vs `field` ETag asymmetry** (§4.4). Real, pre-existing, and a separate change with its own
  tag migration.
- **A `values` block for every text key.** Only `exchange` has a closed canonical set today; adding
  an empty block for the others would publish a promise the product cannot keep.
