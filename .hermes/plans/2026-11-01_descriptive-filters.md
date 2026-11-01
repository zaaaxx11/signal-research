# Descriptive filter group (Finviz parity) — implementation plan

Deliver Finviz's **Descriptive** group on `/app/screener`: a server-published schema
(`GET /screens/filters`), server-side bucket predicates, index membership, signals, order-by,
and a UI rail driven entirely by that one document. TDD, RED first, no commits, never 8099,
never the `ztockz.service` unit, catalog frozen at 116.

## 0. Reality check — five things the brief gets wrong

1. **No server-side `f=` parser.** `f=<key>:<op>:<value>` is the *browser's* query string
   (`web/src/filters/urlState.ts:60`). The server takes JSON: `{mode, region,
   criteria:{filters:[{key|field,op,value}], sort, limit, offset}}` — `app/screen/selection.py:190,480,556`,
   served by `POST /screens/select` (`app/api/screens.py:1040`). Keep `f=` by emitting bucket
   tokens through the same encoder (they are plain `eq` values).
2. **22 of the 23 filter controls already exist.** `FILTER_CATALOG` (`app/screen/filters.py:1798`)
   holds all 82 Finviz keys, `tab="descriptive"` for 22. Six are refused today: `idx`, `sh_opt`,
   `sh_trades`, `ipodate`, `theme`, `subtheme`. `sh_dilution` is not among the 82 at all.
3. **Finviz's option lists are already vendored** at `research/finviz-ui-raw/views/v111-filters.json`
   (83 keys, with `isEliteOnly` flags). Do not retype them.
4. **The brief's counts are off.** Probe counts (named options, no `Any`/`Custom`/`frange`):
   theme **40** (not 39), sub-theme **268**, exchange 5 (incl. `txse`), index 4, cap 14, dividend
   14, short 14, recom 9, earnings **15** (the brief's 11 are a subset: BMO/AMC is per day —
   `todaybefore`, `todayafter`, `tomorrowbefore`, …), avg vol 18, rel vol 16, current vol 0
   (custom only), price 39, target 14, ipo 15, outstanding 16, float 34, option/short 8, dilution 5.
5. **Sorting is toolbar-only today**: three sorts (`app/screen/rank.py:26`), every header
   `enableSorting: false` and `aria-sort` deliberately absent (`web/src/grid/DataTable.tsx:433`). D5 keeps it.

`SELECTION_REQUEST_SCHEMA` is compared **byte-for-byte** to `schemas/stock_selection_request_v1.json`
(`tests/test_screen_schema.py:242`) — any schema edit re-dumps it in the same task.

## 1. The 25 controls and where each is computed

Where ∈ **scan row** / **join** (depth·fundamentals·facts) / **derived** (identity over cells) /
**new** (added by this plan) / **unavailable** (served, disabled, reason — never a filter that
returns the unfiltered set).

| Control | id | Key | Where | Status |
|---|---|---|---|---|
| Order by | `order` | — (`sort`) | rank.py | available, any numeric row field |
| Signal | `signal` | — (`signal`) | new `descriptive.SIGNALS` | 10/27 available |
| Exchange | `exch` | `exch` | scan row | 4 available, TXSE disabled |
| Index | `idx` | `idx` | **new** membership overlay | 4 available |
| Sector | `sec` | `sec` | scan row | available, options from `/market/sectors` |
| Industry | `industry` | `industry` | scan row | available, text `contains` |
| Country | `geo` | `geo` | scan row | available, text `contains` |
| Market Cap | `cap` | `cap` | scan row `market_cap_usd` | 14 buckets |
| Dividend Yield | `fa_div` | `fa_div` | scan row `dividend_yield` | 14 buckets |
| Short Float | `sh_short` | `sh_short` | facts join | 14 buckets |
| Analyst Recom. | `an_recom` | `an_recom` | derived `recom_finviz` | 9 buckets |
| Option/Short | `sh_opt` | `sh_opt` | — | **unavailable**: no optionable/shortable flag |
| Earnings Date | `earn` | `earningsdate` | scan row + parse | 9/15; 6 session options disabled |
| Average Volume | `sh_avgvol` | `sh_avgvol` | scan row `avg_volume_30d` | 18 buckets |
| Relative Volume | `sh_relvol` | `sh_relvol` | scan row `rel_volume_10d` | 16 buckets |
| Current Volume | `sh_curvol` | `sh_curvol` | scan row `volume` | number input |
| **Trades** | `sh_trades` | `sh_trades` | — | **out of scope** (below) |
| Price $ | `price` | `sh_price` | scan row `price` | 39 buckets |
| Target Price | `targetprice` | `targetprice` | scan row, two columns | 14 buckets |
| IPO Date | `ipodate` | `ipodate` | — | **unavailable**: `ipo_date` null on every row |
| Shares Outstanding | `sh_outstanding` | `sh_outstanding` | scan row | 16 buckets |
| Float | `sh_float` | `sh_float` | scan row, absolute + ratio | 34 buckets |
| Theme | `theme` | `theme` | — | **unavailable**: no classification source (40 options) |
| Sub-theme | `subtheme` | `subtheme` | — | **unavailable**: same (268) |
| Dilution Risk | `sh_dilution` | — | — | **unavailable**: no dilution input (5) |

**Trades is out of scope, stated rather than faked.** The brief calls `sh_trades` Elite-only;
whatever its tier, its value is a *trade count* (prints), not volume or turnover, and nothing we
collect produces it: the bulk scan carries `volume` (shares) and `Value.Traded` (currency); the
depth store has no print count; Finra publishes short-sale *volume* per venue and symbol, daily,
which is not a trade count. A
`volume / 100` column would be a fabricated metric wearing Finviz's label, so the control ships
disabled with that reason and `Trades` never joins the 82. It is the one control with
`status: "out_of_scope"`, which the UI renders exactly like `unavailable`.

## 2. Decisions

### D1 — `GET /screens/filters` is the single source for the group

One document, one module, one route beside `/screens/select`: groups → controls → options →
availability → the predicate each option compiles to. The UI renders controls; it never hardcodes
a label, an option or a Finviz token again (the F1 bug class).

```json
{"schema_version": "descriptive-1",
 "groups": [{"id": "descriptive", "label": "Descriptive", "controls": [/* 25 */]}],
 "membership": {"as_of": "2026-11-01", "counts": {"sp500": 503, "ndx": 101, "dji": 30, "rut": 1947}},
 "options_sources": {"/market/sectors": "sector labels with counts (live)",
                     "/market/exchanges": "venue codes with counts (live)"}}
```

One control:

```json
{"id":"cap","label":"Market Cap.","key":"cap","control":"buckets","form":"single","op":"eq",
 "available":true,"reason":null,"unit":"usd","hint":null,"note":null,
 "options":[{"id":"mega","label":"Mega ($200bln and more)","finviz":"mega",
             "predicate":"market_cap_usd >= 200000000000"}]}
```

`control` ∈ `buckets|select|text|number|order|signal`; `form` ∈ `single|multi` (only `exch` and
`sec`, whose `op` is `in`). An unavailable control keeps its options (greyed, as Finviz draws
them) and adds `"available": false, "reason": "<metric it needs>"` — never an empty list, never a
filter that runs. **Population rule:** a *static* Finviz vocabulary (index, price bands, earnings
dates, themes, signals, order-by) ships inline from `descriptive.py`; a *live* population (sector
labels, venue codes) is referenced by `options_sources` instead of copied — two copies of a live
list drift the first time the universe changes. Industry and country are free text (see §5.6).

Caching: the doc is built from memory (~2 ms, no scan, no join), so unlike `/screens/select` it
may hash its own body: `cache_headers(etag)` with `W/"flt-<sha256(body)[:32]>"`, `304` on
`If-None-Match`. Membership availability is in the body, so installing the file moves the tag.

### D2 — Keep `{key, op, value}`; a bucket is a token in `value`, not a new object

The wire shape stays what `parse_selection` validates (`FILTER_KEYS = {key,field,op,value}`), what
`urlState` encodes and what the published schema declares:

```json
{"key": "sh_price", "op": "eq", "value": "price:u5"}
```

A structured `{"bucket": "price:u5"}` would add a second branch to `_reject_extra`, the 422
envelope, `canonical_request`, the ETag digest, the `f=` encoder and `validateDraft` — a third
spelling of "one predicate" for no answer it cannot already give. A column per bucket contradicts
the 116 freeze and makes "50% above price" a column nobody can compute.

Internally a bucket criterion is `Criterion(field=None, op="eq", value=<token>, key=<key>)` —
**no dataclass change**. `field is None` is impossible for a compiled DSL criterion, so
`_predicates()` splits on it and `apply_bucket_filters()` owns the rest. `as_dict()` already
publishes `{key, field:null, op, value}`, so `canonical_request` — and the ETag — distinguishes
two buckets for free. `idx` is one of the 82 keys whose binding is now a bucket, so
`x-filter-key-count == 82` still holds.

### D3 — Buckets are named server-side predicates; the UI never sends a range

`app/screen/buckets.py` owns `id → predicate`. Ids are `"<control>:<finviz-token>"`
(`price:u5`, `cap:mega`, `earn:todaybefore`, `idx:sp500`), traceable to the probe and stable in a
URL; lookup case-folds the token, so `SP500` works. Four predicate shapes, three of which **no**
`field:op:value` triple can express — that is the justification, not convenience:

| shape | example | why the DSL cannot |
|---|---|---|
| column vs constant | `price < 5` | it can; kept uniform so the UI has one path |
| two columns related | `target_price >= 1.5 * price`, `float/shares < 0.1` | needs two cells per row |
| date vs a market day | `earn:today` = New York's today | needs the clock + a timezone |
| set membership | `idx:sp500` = `"sp500" in index_members` | no list/set operator |

```python
# app/screen/buckets.py
NEW_YORK = ZoneInfo("America/New_York")

@dataclass(frozen=True, slots=True)
class Context:
    """The only clock a bucket may read: the answer's own read moment."""
    today: date
    @classmethod
    def at(cls, as_of: str) -> "Context":
        moment = datetime.fromisoformat(as_of)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        return cls(today=moment.astimezone(NEW_YORK).date())

@dataclass(frozen=True, slots=True)
class Bucket:
    id: str; key: str; label: str; finviz: str | None
    predicate: Callable[[Mapping[str, Any], Context], bool]
    describe: str                      # "price < 5" — the audit line the schema publishes
    available: bool = True; reason: str | None = None

def _band(token: str, scale: float = 1.0) -> tuple[str, Any] | None:
    """`u5` -> ("lt", 5*scale); `o1.5` -> ("gt", 1.5); `1to5` -> ("between", (1, 5))."""
    hit = re.fullmatch(r"(u|o)(\d+(?:\.\d+)?)", token)
    if hit:
        return ("lt" if hit.group(1) == "u" else "gt"), float(hit.group(2)) * scale
    span = re.fullmatch(r"(\d+(?:\.\d+)?)to(\d+(?:\.\d+)?)", token)
    if span:
        return "between", (float(span.group(1)) * scale, float(span.group(2)) * scale)
    return None
```

`_numeric(field, tokens, labels, scale)` builds the mechanical families from `_band`; hand-written
ones are `cap` (mega…microunder), `fa_div` (none/pos/high/veryhigh), `sh_short` (low/high),
`sh_relvol` (decimals), `sh_float` (`*p` percent tokens), `targetprice` (`a50`/`b20`), `earn`
(dates), `an_recom`, `idx`. Unknown id → `UnknownBucket` (422 `unknown_bucket`); id of an
unavailable option → `UnavailableBucket` (422 `unavailable_bucket`). Never `0 rows`.

### D4 — Where each filter is computed, and what "unavailable" means

scan row → `market_cap_usd, dividend_yield, price, volume, avg_volume_30d, rel_volume_10d,
target_price, shares_outstanding, float_shares, earnings_date, sector, industry, country, exchange`.
join → `short_float` (facts). derived → `recom_finviz` (`3 - 2*recommendation`).
new → `index_members: list[str] | None` — **internal only**: never a catalog column, never
projectable via `columns`, read only by `idx`. `len(json.load(...116.json)) == 116` still holds and
a test asserts the name is absent from it.
unavailable → the control is served disabled with its options and reason; the *key* stays refused
by `binding()`, so a hand-written filter is a 422 naming the metric, never a no-op.

### D5 — Order by, and what sorting is not

`sort` stays the one knob and gains the catalog: `rank.ORDER_BY` is *derived* from
`filters.FIELDS` (`kind == "number"`) so it cannot drift; `"<field>"` is descending, `"-<field>"`
ascending (Finviz's own `o=-marketcap`). The three legacy names stay aliases (`gainers`→`change_pct`,
`turnover`→`turnover`, `market_cap`→`market_cap_usd`), so existing links and the browse route are
untouched (`SORTS`/`SORT_METRICS` keep serving `GET /screens`). Missing values sort **last in both
directions** — a null is not a small number. **Clicking a header does not sort, and this plan keeps
it that way**: the panel is server-paged by `offset`, so a client-side sort would reorder one page
while `rank` and the pager describe another; a header sort has to be a request, and the toolbar
already is one. `signal` sets the population and, when `sort` is absent, its own measure becomes
the default order; an explicit `sort` always wins.

## 3. Files

**New** `app/screen/{buckets,index_membership,descriptive}.py`,
`app/screen/data/{descriptive_options,index_membership}.json`,
`scripts/{index-membership,descriptive-options}.py`, `web/src/filters/{descriptive.ts,DescriptiveRail.tsx}`,
`web/src/grid/{OrderBySelect,SignalSelect}.tsx`.
**Edited** `app/screen/{filters,filter_fields,selection,rank}.py`, `app/api/screens.py`,
`schemas/stock_selection_request_v1.json`,
`web/src/filters/{types,registry,validation,state,urlState,useScreenState,FilterDrawer}`,
`web/src/grid/ScreenPanel.tsx`, `web/src/data/screens.ts`, `web/src/filters/__fixtures__/schema.ts`.

## 4. Tasks (RED test → minimal impl → verify)

### T1 — Bucket framework, price, market cap
**RED** `tests/test_screen_buckets.py::test_market_cap_bands_partition_a_universe` (5,000 synthetic
rows: `mega/large/mid/small/micro/nano` place every row in exactly one band; `largeover` overlaps
by design and is excluded). `::test_price_u5_is_below_five` (`price:u5` accepts 4.99, refuses 5.00 — `lt`).
**Impl** `buckets.py` as D3: `Context`, `Bucket`, `_band`, `_numeric`, `REGISTRY`, `bucket(id)`,
`for_key(key)`, `apply_bucket_filters(rows, criteria, *, as_of)`.
**Verify** `python -m pytest tests/test_screen_buckets.py -q`

### T2 — Short float, dividend, analyst, relative float
**RED** `::test_dividend_none_matches_no_bucket` (a `None` yield is unknown, not 0% — it matches
neither `none` nor `pos`); `::test_float_percent_band_divides_by_shares_outstanding` (5M/100M
matches `float:u10p`, 15M/100M does not); `::test_recom_strongbuy_is_lt_2`.
**Impl** those four families; `*p` tokens read two cells and return `None` if either is missing.
**Verify** same command, green.

### T3 — Volume family and shares outstanding
**RED** `::test_average_volume_tokens_are_shares_not_thousands` (`sh_avgvol:u1000` = `< 1e6` —
probe label "Under 1M"); `::test_relative_volume_decimal_token` (`sh_relvol:o1.5` = `> 1.5`);
`::test_current_volume_has_no_buckets` (`for_key("sh_curvol") == ()`).
**Impl** `_numeric` with `scale=1e3|1e6` per Finviz's *label*, not the token digits.
**Verify** as above.

### T4 — Target price and earnings dates
**RED** `::test_target_price_is_relative_to_the_rows_own_price` (price 100 / target 150 matches
`targetprice:a50`, target 140 does not); `::test_earnings_today_is_new_york_today` (as_of
`2026-11-02T02:30:00+00:00` is Nov 1 in NY, so a Nov-2 date does **not** match `earn:today`);
`::test_earnings_parser_agrees_with_calendar` (epoch int, `2026-11-17`, `Nov 17` agree with
`app.api.calendar.parse_earnings_date` — a drift test; production must not import `app.api`, hence
the duplicate parser); `::test_the_six_session_options_are_unavailable_with_a_reason`.
**Impl** `_day(value) -> date | None` plus window predicates over `ctx.today` (`nextdays5`,
`prevdays5`, `thisweek`, `nextweek`, `prevweek`, `thismonth`; ISO weeks, Monday-first).
**Verify** as above.

### T5 — Index membership
**RED** `tests/test_screen_index_membership.py::test_the_vendored_snapshot_is_plausible_and_stamped`
(`sp500 480..520`, `ndx 95..110`, `dji == 30`, `rut 1700..2200`, `as_of` ISO and ≤ today).
`::test_both_ticker_spellings_resolve` (`BRK.B` and `BRK-B` → `sp500`).
`::test_a_missing_snapshot_makes_idx_unavailable_not_empty` (loader `as_of=None` → document marks
`idx` unavailable with a reason → filter is a 422).
**Impl** `scripts/index-membership.py` (stdlib `html.parser` table reader, `--from <csv>` fallback,
refuses implausible counts) writes `app/screen/data/index_membership.json`
`{"as_of","source":{...},"indices":{"sp500":[...]}}`; `index_membership.py` exposes `load()`,
`members(index)`, `indices_for(symbol)`, `enrich_rows(rows)` (dual spelling; missing → `None`,
never `[]`).
**Verify** `python scripts/index-membership.py && python -m pytest tests/test_screen_index_membership.py -q`

### T6 — Selection: the bucket path
**RED** `tests/test_screen_selection_buckets.py::test_a_bucket_token_never_reaches_the_dsl`
(`Criterion.field is None`; `_predicates` drops it; `apply_filters` never sees the token);
`::test_a_token_is_case_insensitive` (`SP500` == `sp500`); `::test_unknown_token_is_422_unknown_bucket`;
`::test_a_token_on_a_wrong_key_is_refused` (`cap:eq:u5` → 422, ids are namespaced);
`::test_two_different_buckets_are_two_etags` (`price:u5` vs `price:o5` differ in `request_digest`);
`::test_the_envelope_echoes_the_token` (`filters[0].value == "price:u5"`, `field` null).
**Impl** `selection._criterion` after `binding()`: `if resolved.bucket: return _bucket_criterion(...)`
(require `op == "eq"`, validate through `buckets.bucket`, keep `key`); `_selected`:
`apply_bucket_filters(matched, request.filters, as_of=as_of)`; `Binding` gains `bucket: bool`; `idx`
leaves `_GROUPS`; `filter_fields.as_dict` adds `"bucket": true`.
**Verify** `python -m pytest tests/test_screen_selection_buckets.py -q`

### T7 — Signals
**RED** `tests/test_screen_signals.py::test_the_vocabulary_is_27` (15 market + 12 pattern ids,
frozen: `top-gainers, top-losers, most-active, most-volatile, most-increased-volume,
unusual-volume, overbought, oversold, new-high, new-low` available; `upgrades, downgrades,
earnbefore, earnafter, majornews` and `wedgeup, wedge, wedgedown, tri-ascending, tri-descending,
channelup, channel, channeldown, doubletop, doublebottom, head-shoulders, head-shoulders-inverse`
unavailable); `::test_available_signals_agree_with_the_home_rule`
(imports `app.api.signals.SIGNALS` in the test only and compares `criterion` strings);
`::test_unavailable_signal_is_422_unavailable_signal` (`upgrades`, `majornews`, `earnbefore`, the
12 patterns); `::test_signal_orders_by_its_own_measure`; `::test_an_explicit_sort_wins`.
**Impl** `descriptive.SIGNALS` (`id, label, available, reason, measure, accepts, order`);
`SELECTION_KEYS += {"signal"}`, `SelectionRequest.signal`, `canonical_request["signal"]`,
`_signal()`, `_selected` prefilters by `accepts` then ranks.
**Verify** `python -m pytest tests/test_screen_signals.py -q`

### T8 — Order by
**RED** `tests/test_screen_order_by.py::test_order_by_accepts_any_numeric_catalog_field`
(`pe_ttm`, `rsi_14`, `dividend_yield` → 200; `sector` → 422); `::test_a_leading_minus_is_ascending`;
`::test_missing_values_sort_last_both_ways`; `::test_the_three_legacy_sorts_still_work`.
**Impl**
```python
ORDER_BY: tuple[str, ...] = tuple(sorted(n for n, k in FIELDS.items() if k == "number"))
ALIASES = {"market_cap": "market_cap_usd", "gainers": "change_pct", "turnover": "turnover"}

def order_metric(sort: str | None) -> tuple[str, bool]:
    """(column, ascending). `-field` is ascending — Finviz's `o=-marketcap`."""
    if not sort:
        return "market_cap_usd", False
    name = sort[1:] if sort.startswith("-") else sort
    column = ALIASES.get(name, name)
    if column not in FIELDS or FIELDS[column] != "number":
        raise ValueError(f"unknown sort {sort!r}; expected one of {', '.join(ORDER_BY)}")
    return column, sort.startswith("-")
```
`rank_rows` sorts once by `(0, -v)/(0, v)/(1, 0.0)` after the symbol pre-sort; `selection._sort(raw, *, default)`
validates through `order_metric`.
**Verify** `python -m pytest tests/test_screen_order_by.py -q`

### T9 — The descriptive document
**RED** `tests/test_screen_descriptive.py::test_one_control_per_finviz_control` (25 controls, ids
and order as §1); `::test_every_option_count_matches_the_probe` (the 18 counts in §0.4, read from
the vendored probe — tests may read `research/`, production may not);
`::test_every_unavailable_control_states_why_and_has_no_predicate`;
`::test_every_bucket_control_resolves_every_option` (per control: `len(for_key(key)) == len(options)`
— catches a missing predicate family).
**Impl** `scripts/descriptive-options.py` extracts the 23 descriptive keys into
`app/screen/data/descriptive_options.json` (drops `isEliteOnly` `Custom`/`modal`/`frange`), and
`pyproject.toml` gains `"app.screen" = ["data/*.json"]` under `[tool.setuptools.package-data]`
(today only `app.engine`'s data ships — editable installs hide this). `descriptive.py` joins the
file with `buckets.for_key`, `SIGNALS`, `rank.ORDER_BY`, and the runtime membership state;
`_validate()` fails the import if a control names an unknown key, an option has no bucket, or an
available control names no predicate.
**Verify** `python scripts/descriptive-options.py && python -m pytest tests/test_screen_descriptive.py -q`

### T10 — `GET /screens/filters`
**RED** `tests/test_api_screen_filters.py::test_get_screens_filters_serves_the_descriptive_group`
(200; `schema_version == "descriptive-1"`; 25 controls; `membership.counts.sp500 > 480`);
`::test_the_document_revalidates_on_its_own_digest` (304, empty body);
`::test_availability_is_a_runtime_fact` (loader monkeypatched to "missing" → `idx` unavailable,
reason names the snapshot, not the scanner).
**Impl**
```python
@router.get("/filters")
async def filters(request: Request) -> Response:
    document = descriptive.document()                 # memory only, ~2 ms
    etag = f'"flt-{hashlib.sha256(_encoded(document)).hexdigest()[:32]}"'
    headers = cache_headers(etag)
    if not_modified(request, etag):
        return Response(status_code=304, headers=headers)
    return Response(content=_encoded(document), media_type="application/json", headers=headers)
```
plus in `select()`: `universe = membership.enrich_rows(universe)` after `enrich_facts`, and
`_data_revision` gains `;members=<as_of>:<counts-hash>` with `"v2"` → `"v3"` (a new input is a
deliberate bump; every warm client pays one 200, the safe direction).
**Verify** `python -m pytest tests/test_api_screen_filters.py -q`

### T11 — Contract sweep (the task everyone forgets)
**RED** `tests/test_screen_filter_fields.py::test_the_catalogue_disposition_adds_up`
(`mapped == 51`, `unavailable == 31`, sum 82; line 346's `("idx", …)` moves out of the refused
tuple) and `tests/test_screen_schema.py::test_the_served_schema_and_the_artifact_are_one_document`.
**Impl** `x-filter-keys.idx` → `{label, tab, field: null, bucket: true}`; `x-filter-key-count`
stays 82; `criteria.sort.enum` widens to `ORDER_BY + aliases`; top-level `signal` added; re-dump
`schemas/stock_selection_request_v1.json` from `SELECTION_REQUEST_SCHEMA`
(`json.dumps(..., indent=2, ensure_ascii=False) + "\n"`, matching the file's current shape).
**Verify** `python -m pytest tests/test_screen_schema.py tests/test_screen_filter_fields.py -q`

### T12 — UI: the rail
**RED** `web/src/filters/descriptive.test.tsx::"renders one control per entry and disables an
unavailable one with its reason"` (disabled button: `aria-disabled="true"`, `title` = reason,
click calls nothing); `::"a bucket press emits the token, not a range"`
(`onFilter({key:"sh_price", op:"eq", value:"price:u5"})`);
`web/src/filters/DescriptiveRail.test.tsx::"a disabled control is skipped by Tab"`.
**Impl** `descriptive.ts` (`DescriptiveDocument` types, `loadDescriptive()`, `bucketOptions()`,
`claimedKeys()`), `DescriptiveRail.tsx` (one `<section>` per control, bucket buttons reusing
`FilterDrawer`'s existing classes, `<select multiple>` for `multi`, input for `text`/`number`);
`registry.ts` adds `bucketOf: Map<token,label>` and `validation.ts` short-circuits
`if (entry.bucketOf?.has(String(value))) return {ok:true, key, op:"eq", value}` — without it a
token is rejected as a non-number (§5.4); `FilterDrawer` renders the rail above the presets and
skips every `PRESETS` group in `claimedKeys`.
**Verify** `cd web && npx vitest run src/filters/descriptive.test.tsx src/filters/DescriptiveRail.test.tsx`

### T13 — UI: order by and signal
**RED** `web/src/filters/urlState.test.ts::"a signal and an order-by survive the round trip"`
(`?o=-pe_ttm&s=top-gainers` → state → same string); `web/src/grid/SignalSelect.test.tsx::"an
unavailable signal is listed and unselectable"`; `ScreenPanel.filters.test.tsx::"a signal change is
one request and the rows are the signal's"`.
**Impl** `useScreenState` gains `signal`/`order` (URL `s`/`o`, `o` omitted when default);
`SignalSelect`/`OrderBySelect` read the document, never a literal; `ScreensQuery.signal`,
`queryKey` includes it, body sends it.
**Verify** `cd web && npx vitest run src/filters/urlState.test.ts src/grid/SignalSelect.test.tsx`

### T14 — Adversarial sweep
**RED** `tests/test_screen_descriptive_adversarial.py::test_an_unavailable_key_is_422_and_never_an_unfiltered_page`
(`theme`, `sh_opt`, `ipodate`, `sh_trades` → 422, body has no `total`);
`::test_an_unknown_option_is_named` (`idx:eq:sp900` → 422 listing the 4 accepted ids);
`::test_a_bucket_on_a_key_that_has_none_is_refused` (`vol:eq:price:u5` → 422);
`::test_116_columns_are_still_116_and_carry_no_index_members`;
`::test_the_column_cap_still_refuses_41` (`COLUMN_LIMIT == 40`, 41 → 422).
**Verify** `python -m pytest tests/test_screen_descriptive_adversarial.py -q`

### T15 — Live DoD on a scratch instance (**never** 8099)
`MEE_DB_PATH=/tmp/dod.db MEE_EXPORT_DIR=/tmp/dod-export .venv/bin/uvicorn app.main:app --port 8111 &`
then §6; kill it after.

## 5. What could break

1. **Disposition counts.** `tests/test_screen_filter_fields.py:139-142` pins 50/32 and line 346
   names `idx` as refused. Both are *intended* changes (51/31) — update them with the move, and
   the `filter_fields` docstring ("Forty-seven keys… thirty-two are refused").
2. **The schema artifact.** `tests/test_screen_schema.py:242` compares served vs
   `schemas/stock_selection_request_v1.json` byte-for-byte; a widened `sort` enum or a new `signal`
   property without the re-dump fails there, not in `npm run check`.
3. **The ETag.** Two stale-row paths: (a) forgetting `members=` in `_data_revision` (new
   membership file 304s old rows) — hence the `v3` bump; (b) a "cleanup" that drops null `field`
   keys from `as_dict()` collides `price:u5` with `price:o5`, since `value` is the only thing that
   distinguishes them.
4. **`validateDraft` rejects the token.** `web/src/filters/validation.ts:208` switches on
   `entry.kind`; `sh_price` is `number`, so `"price:u5"` fails `numberValue` and the bucket
   silently drops out of the URL on reload. The `bucketOf` short-circuit is not optional.
5. **The scan cache.** Buckets filter *after* the scan. Do not push `idx` into provider filters or
   `_scan_key`: a second key is a second scan per panel (5 s TTL, single-flight) and two panels
   describing one universe differently.
6. **Industry/country as selects.** Finviz's 146 industry and 62 country labels are *their*
   taxonomy; the row carries the provider's string ("Semiconductors", "United States"). Shipping
   Finviz labels as `eq` values returns empty pages — hence `contains` text inputs. A select needs
   a label→provider-string table, not a copy of the probe.
7. **Drawer contracts.** The rail adds 25 controls to a drawer with 12 preset groups: the focus
   trap (`FOCUSABLE`), `aria-modal`, and `hidden`-not-unmounted must survive
   (`FilterDrawer.test.tsx` covers all three). One scrollable column, compact buttons.
8. **The 40-column cap.** `COLUMN_LIMIT = 40`, `MAX_COLUMNS = 40`, `AllColumnsPanel` chunks at 40.
   This plan adds **zero** columns; projecting `index_members` violates the freeze and T14.
9. **Packaging.** `[tool.setuptools.package-data]` lists `app.engine` only; a JSON under
   `app/screen/data/` loads from a checkout and 500s from a wheel. T9 adds the entry — a test that
   imports the module from an installed tree would not catch it, so treat it as a review item.

## 6. Definition of done (mechanical)

Against `:8111`:

```
curl -s localhost:8111/screens/filters | jq '{v:.schema_version,n:(.groups[0].controls|length),
  off:[.groups[0].controls[]|select(.available==false)|.id],sp:.membership.counts.sp500}'
# {"v":"descriptive-1","n":25,"off":["sh_opt","sh_trades","ipodate","theme","subtheme","sh_dilution"],"sp":>480}
```
- option counts: `price 39, cap 14, fa_div 14, sh_short 14, an_recom 9, earn 15 (9 available),
  sh_avgvol 18, sh_relvol 16, sh_float 34, targetprice 14, ipodate 15, sh_outstanding 16,
  exch 5 (4 available), idx 4, theme 40, subtheme 268, sh_dilution 5, signal 27 (10 available)`.
- `POST /screens/select {"mode":"criteria","region":"us","criteria":{"filters":[{"key":"idx","op":"eq","value":"sp500"}],"limit":1}}`
  → `total > 400`; `"rut"` → `> 1500`; `"dji"` → `>= 28`.
- `{"key":"sh_price","op":"eq","value":"price:u5"}` → 200, `total > 0`, every row `price < 5`.
- `{"key":"earn","op":"eq","value":"today"}` → 200 (`total >= 0` is honest; a 422 is not).
- `{"key":"theme","op":"eq","value":"ai"}` → 422 `unavailable_filter_key`, no `total` in body.
- `{"key":"idx","op":"eq","value":"sp900"}` → 422 `unknown_bucket`, message names `sp900`.
- `{"mode":"criteria","region":"us","signal":"top-gainers","criteria":{"limit":5}}` → 200, rows in
  `change_pct` desc order; `"upgrades"` → 422 `unavailable_signal`.
- `python -m pytest tests/test_screen_buckets.py tests/test_screen_index_membership.py tests/test_screen_selection_buckets.py tests/test_screen_signals.py tests/test_screen_order_by.py tests/test_screen_descriptive.py tests/test_api_screen_filters.py tests/test_screen_descriptive_adversarial.py -q` → `0 failed`.
- `python -m pytest tests/test_screen_schema.py tests/test_screen_filter_fields.py tests/test_engine_catalog.py -q` → `0 failed`.
- `cd web && npm run check` (vitest + `tsc --noEmit` + tokens + budget) → clean.
- `python -c "import json;print(len(json.load(open('app/engine/data/finviz_columns_116.json'))))"` → `116`.
- `git diff --stat` touches only §3's files; the schema artifact shows a semantic diff, not a reflow.

## 7. Deferred (named, so nobody re-invents them badly)

BMO/AMC earnings sessions (needs a session stamp at collection, then flip 6 options); theme/sub-theme
classification (needs a vendor taxonomy join — all 308 labels are already served disabled); dilution
risk (needs share-count history → a computed dilution rate); IPO date (fix the collector, not the
filter); optionable/shortable flags; Trades (Elite-only, no print-count source).
