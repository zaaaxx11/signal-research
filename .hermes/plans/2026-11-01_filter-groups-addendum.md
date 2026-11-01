# Filter groups addendum — Fundamental, Technical, News, ETF (T16–T25)

Extends `2026-11-01_descriptive-filters` (T1–T6 merged) so the four remaining Finviz filter tabs
answer real predicates instead of 422s, reusing the bucket framework rather than growing a second
compiler. TDD, RED first, no commits, never 8099, never `ztockz.service`, **catalogue frozen at 82**.

Companion measurement: `.hermes/research/2026-11-01_finviz-filter-groups.md` ("the audit").

## 0. Where this starts

**Landed today** (verified in the working tree): `app/screen/buckets.py` (Context, Bucket, `_band`,
`_numeric`, 12 families, **207 ids**, `apply_bucket_filters`), `app/screen/index_membership.py`,
`filter_fields.BUCKET_KEYS`, `selection._bucket_criterion`. **51 of 82 keys are live; 31 refused.**

**Landing right now, uncommitted**: T7–T11 — `app/screen/descriptive.py` (1,278 lines, one group of
**25 controls**: `order`, `signal` and 23 keys including `sh_dilution`, at `schema_version`
`descriptive-1`), `app/screen/data/descriptive_options.json`, `scripts/descriptive-options.py`,
`GET /screens/filters` in `app/api/screens.py`, and their six test files. A second builder owns those,
and owns `app/screen/*` and `app/api/*` while they run.

Rules for every task below:

1. **Do not start T16 before T11 merges.** Every task here edits files T7–T11 also edit; rebasing a
   half-written `buckets.py` against a new `descriptive.py` is how a registry loses a family.
2. **One table per concept.** A task adds rows to exactly two tables — `buckets._CONTROL_KEYS` and,
   where a key also needs a numeric column, `filters.FIELDS` — never a third list.
3. **Never a second compiler for one key.** `selection._criterion` returns at
   `if resolved.choice`, so a bucket on `ta_perf` would be unreachable code. `ta_perf`, `ta_perf2`
   and `ta_volatility` keep `choice_filters` as their only owner.
4. **A token Finviz draws either compiles or is refused by name** (`UnavailableBucket` /
   `UnknownBucket`, both 422). No option may quietly match nothing.
5. **Wiring a key means deleting it from `filter_fields._GROUPS` in the same commit.**
   `_validate()` fails the import when a key is both mapped and refused (`both=[…]`), so the tuple
   entry goes and the group's sentence shrinks to the keys still refused — and a group whose last key
   leaves is deleted, not left as an empty tuple with a reason nobody reads.

### What this plan changes, in one line each

| | before | after |
|---|---|---|
| live keys | 51 / 82 | **70 / 82** |
| refused keys | 31 | **12** (`theme`, `subtheme`, `ipodate`, `sh_trades`, `sh_opt`, `sh_insidertrans`, `ta_highlow20d`, `ta_pattern`, `ah_close`, `ah_change`, `news_keywords`, `etf_heldby`) |
| bucket ids | 207 | **1,317** (1,110 new across T17–T23: 1,037 available, 73 refused with a reason) |
| `filter_fields` disposition counts | 51 mapped / 31 refused | **70 / 12** (T11's test moves again) |
| `Context` | `today: date` | `today: date`, `moment: datetime` |

## 1. Decisions

### D6 — One band table, one builder, one family per control

Every fundamental control's vocabulary is the same shape: `low`/`high`/`positive`/`negative` are
*labels whose thresholds differ per control* ("Low (<15)" for P/E, "Low (<1)" for PEG, "Low (<3)"
for Price/Cash), and `u…`/`o…` are bands. So the unit of work is a table + a shared builder, not 37
hand-written families:

```python
# app/screen/buckets.py
_GROWTH = MappingProxyType({          # 11 EPS/sales growth keys share this verbatim
    "neg": ("lt", 0.0),
    "pos": ("gt", 0.0),
    "poslow": ("between", (0.0, 10.0)),   # Finviz's "Positive Low (0-10%)"
    "high": ("gt", 25.0),
})

_BANDS: Mapping[str, Mapping[str, tuple[str, Any]]] = MappingProxyType({
    "fa_pe": {"low": ("lt", 15.0), "profitable": ("gt", 0.0), "high": ("gt", 50.0)},
    "fa_peg": {"low": ("lt", 1.0), "high": ("gt", 2.0)},
    "fa_roa": {"pos": ("gt", 0.0), "neg": ("lt", 0.0), "verypos": ("gt", 15.0), "veryneg": ("lt", -15.0)},
    "fa_roe": {"pos": ("gt", 0.0), "neg": ("lt", 0.0), "verypos": ("gt", 30.0), "veryneg": ("lt", -15.0)},
    # …one row per control that has a special token
})

def _family(control, key, column=None, *, bands=None, tokens=None, suffix="",
            refuse=None, predicate=None, describe=None):
    """One bucket per probe option of ``key``, in the probe's order.

    ``column`` + ``_band`` is the common case (``pe:u5`` → ``pe_ttm < 5``).
    ``predicate`` replaces it where a token needs two cells (``a5h``), both cells
    of a pair (``epsrev:bp``) or a clock (``news:today``): it receives
    ``(token, op, value)`` and returns the row predicate. ``refuse`` is
    ``{token: reason}`` — the token is still a bucket (``available=False``), so
    the family covers the probe without pretending the token works.
    """
    special = bands if bands is not None else _BANDS.get(key, {})
    family = []
    for token in (_tokens(key) if tokens is None else tokens):
        if refuse and token in refuse:
            family.append(Bucket(
                id=f"{control}:{token}", key=key, label=_label(key, token), finviz=token,
                predicate=lambda row, context: False,
                describe=f"unavailable: {refuse[token]}",
                available=False, reason=refuse[token],
            ))
            continue
        if token in special:
            op, value = special[token]
        else:
            band = _band(token)
            if band is None:
                raise RuntimeError(f"{key}: token {token!r} has no band and no entry in _BANDS")
            op, value = band
        family.append(Bucket(
            id=f"{control}:{token}", key=key, label=_label(key, token), finviz=token,
            predicate=predicate(token, op, value) if predicate else _column_predicate(column, op, value),
            describe=describe(token, op, value) if describe else _describe(column, op, value),
        ))
    return tuple(family)
```

`_label(key, token)` reads the probe's own label and falls back to
`_band_label(op, value, suffix=…)`; **labels must come from the probe file** (vendored at
`research/finviz-ui-raw/views/v111-filters.json`, copied into
`app/screen/data/descriptive_options.json` by T9's script) — a hand-typed label drifts the first
time Finviz relabels a band.

### D7 — `_band` must read a negative threshold

`(u|o)(\d+(?:\.\d+)?)` cannot read `u-50`, which is 51 real tokens across `fa_roa`, `fa_roe`,
`fa_roi`, `fa_grossmargin`, `fa_opermargin`, `fa_netmargin`, `sh_insidertrans`, `sh_insttrans`.
One character, eight controls unblocked:

```python
hit = re.fullmatch(r"(u|o)(-?\d+(?:\.\d+)?)", token)
```

Nothing else in `_band` moves; `u5`, `o1.5`, `0to5` and `1to5` keep their meanings (regression test).

### D8 — `u` is "up" in `ta_gap`/`ta_change`/`ta_changeopen`, and that is a different family

`sh_price:u5` is "Under 5"; `ta_gap:u5` is "Up 5 %". `_band("u0")` would compile "Up 0 %" as
`gap < 0` — the exact opposite. Those three keys get their own builder, never `_numeric`:

```python
def _direction_family(control, key, column):
    for token in _tokens(key):
        if token in ("u", "d"):
            op, value = ("gt", 0.0) if token == "u" else ("lt", 0.0)
        else:
            side, digits = re.fullmatch(r"([ud])(\d+(?:\.\d+)?)", token).groups()
            number = float(digits)
            op, value = ("gte", number) if side == "u" else ("lte", -number)
```

`u0` → `>= 0`, `d0` → `<= 0`, `u20` → `>= 20`, `d20` → `<= -20`. The label is the proof:
`u1` = "Up 1 %", `d1` = "Down 1 %".

### D9 — A distance needs two cells, and an implausible one matches nothing

`ta_highlow50d/52w/alltime` and `ta_sma20/50/200` are *distance* controls over a row that stores a
*level*. `b5h` = "5 % or more **below High**", `a5h` = "5 % or more **above Low**" (the vendored
labels say so; `a` does not mean "above the high"). So one token reads `price` **and** one anchor:

```python
def _distance(op: str, pct: float, level: str, *, side: str):
    """`side="below"`: (1 - price/level)*100; `side="above"`: (price/level - 1)*100."""
    def predicate(row, context):
        price, anchor = _number(row.get("price")), _number(row.get(level))
        if price is None or anchor is None or price <= 0 or anchor <= 0:
            return False
        if anchor > price * 10:            # 24 rows carry high_50d > 10x price (audit §7.5)
            return False                    # an implausible level is not a measurement
        distance = (1 - price / anchor) * 100 if side == "below" else (price / anchor - 1) * 100
        return _test(distance, op, pct)
    return predicate
```

`nh` → `price >= 0.999 * level` (the same epsilon `signals.py` uses, so the bucket and the home
table agree); `nl` → `price <= 1.001 * level`. The same guard covers all three keys, which is why
`ta_alltime` can ship at all despite 22 rows whose all-time high is below the 52-week high.

### D10 — Depth-backed cells become first-class filter fields, once

`filter_fields._validate` refuses a key that names a field the row does not "carry"
(`universe.CARRIED_FIELDS`), but the overlay has been putting 30+ depth cells on the row for months
under their catalogue names. The fix is a declared set, not a wider guess:

```python
# app/screen/universe.py
DEPTH_FIELDS: frozenset[str] = frozenset({
    "eps_this_y", "eps_next_y", "eps_qoq", "eps_yoy_ttm", "eps_past_3y", "eps_past_5y",
    "eps_next_5y", "sales_qoq", "sales_yoy_ttm", "sales_past_3y", "sales_past_5y",
    "eps_surprise", "revenue_surprise", "lt_debt_to_equity",
    "dividend_growth_1y", "dividend_growth_3y", "dividend_growth_5y",
    "insider_trans", "inst_trans", "high_50d", "low_50d",
    "all_time_high", "all_time_low", "candlestick_pattern", "news_time",
})
CARRIED_FIELDS = (frozenset(UNIVERSE_COLUMNS) | frozenset(PARITY_FIELDS.values())
                  | frozenset(DERIVED_FIELDS) | DEPTH_FIELDS)
```

A test reads the real store and asserts every declared name is non-null for ≥1 symbol, so the set
cannot claim a cell the collector stopped writing.

### D11 — Elite-only and source-less controls ship disabled, naming both

`Trades`, `After-Hours Close`, `After-Hours Change`, `News Keywords`, `Held By` are
`isEliteOnly: true` on the **control** in the vendored probe (every other key has only its `Custom`
option flagged). They ship `available: false` with a reason that states the tier *and* the missing
source, so a reader can tell "we are not paying Finviz" from "we have no data". Never hidden, never
a filter that returns the unfiltered set.

## 2. Namespaces (permanent URL surface)

`buckets.CONTROL_KEYS` is the one table; ids are `<control>:<finviz-token>`, so this list is what a
saved screen will contain forever.

| tab | control → key |
|---|---|
| fundamental | `pe`→fa_pe, `fpe`→fa_fpe, `peg`→fa_peg, `ps`→fa_ps, `pb`→fa_pb, `pc`→fa_pc, `pfcf`→fa_pfcf, `evebitda`→fa_evebitda, `evsales`→fa_evsales, `divgrowth`→fa_divgrowth, `epsyoy`→fa_epsyoy, `epsyoy1`→fa_epsyoy1, `epsqoq`→fa_epsqoq, `epsytm`→fa_epsyoyttm, `eps3y`→fa_eps3years, `eps5y`→fa_eps5years, `epsnext5y`→fa_estltgrowth, `salesqoq`→fa_salesqoq, `salesttm`→fa_salesyoyttm, `sales3y`→fa_sales3years, `sales5y`→fa_sales5years, `epsrev`→fa_epsrev, `roa`→fa_roa, `roe`→fa_roe, `roi`→fa_roi, `curratio`→fa_curratio, `quickratio`→fa_quickratio, `ltdebteq`→fa_ltdebteq, `debteq`→fa_debteq, `grossmargin`→fa_grossmargin, `opermargin`→fa_opermargin, `netmargin`→fa_netmargin, `payout`→fa_payoutratio, `insiderown`→sh_insiderown, `insidertrans`→sh_insidertrans, `instown`→sh_instown, `insttrans`→sh_insttrans |
| technical | `rsi`→ta_rsi, `gap`→ta_gap, `change`→ta_change, `changeopen`→ta_changeopen, `sma20`→ta_sma20, `sma50`→ta_sma50, `sma200`→ta_sma200, `highlow50d`→ta_highlow50d, `highlow52w`→ta_highlow52w, `alltime`→ta_alltime, `candle`→ta_candlestick, `beta`→ta_beta, `atr`→ta_averagetruerange |
| news | `news`→news_date |

No collision with the twelve existing controls (`price`, `cap`, `fa_div`, `sh_short`, `an_recom`,
`earn`, `sh_avgvol`, `sh_relvol`, `sh_outstanding`, `sh_float`, `targetprice`, `idx`). `_validate`
already fails on a duplicate id; add the namespace assertion to T16.

**Synonym policy**: a bucket id may carry one readable synonym per token where Finviz's mnemonic is
unreadable (`rsi:overbought` → `rsi:ob70`, `rsi:oversold` → `rsi:os30`). Synonyms resolve through a
per-control `Mapping[str, str]` consulted by `bucket()`, and **the envelope always echoes the
canonical id**, so a synonym is a spelling, not a second bucket. No synonym for the fundamental
bands (`pe:u15` is already readable).

## 3. Tasks

Every task names its **tab** and its **disposition**, using the five words the audit uses:
`scan row`, `join`, `derived formula`, `new source named`, `unavailable`.

---

### T16 — Band parser, band table, family builder *(tab: none yet; mechanism)*

**Files** `app/screen/buckets.py`; **new** `tests/test_screen_buckets_groups.py`.
**Disposition** none — this task wires no key.

**RED**

```python
def test_band_reads_a_negative_threshold():
    assert buckets._band("u-50") == ("lt", -50.0)
    assert buckets._band("u-5") == ("lt", -5.0)
    assert buckets._band("o-10") == ("gt", -10.0)

def test_band_still_reads_every_descriptive_token():
    # the 207 ids that ship today must not move: same (op, value) after the regex change
    assert buckets._band("u5") == ("lt", 5.0)
    assert buckets._band("o1.5") == ("gt", 1.5)
    assert buckets._band("0to5") == ("between", (0.0, 5.0))

def test_a_family_covers_every_probe_option_in_order():
    family = buckets._family("pe", "fa_pe", "pe_ttm", bands=buckets._BANDS["fa_pe"])
    assert [b.finviz for b in family] == list(buckets._tokens("fa_pe"))
    assert len(family) == 23

def test_a_family_token_without_a_band_is_an_import_error():
    with pytest.raises(RuntimeError, match="has no band and no entry in _BANDS"):
        buckets._family("pe", "fa_pe", "pe_ttm", bands={})   # drops low/profitable/high

def test_every_control_id_is_unique_and_starts_with_its_namespace():
    ids = [b.id for key in buckets.KEYS for b in buckets.for_key(key)]
    assert len(ids) == len(set(ids))
    assert all(b.id.split(":")[0] == buckets.control_for(b.key) for key in buckets.KEYS for b in buckets.for_key(key))
    assert len(set(buckets._CONTROL_KEYS.values())) == len(buckets._CONTROL_KEYS)   # two controls, one key
```

**Impl** the `_band` regex (D7), `_family` (D6), `_BANDS` with the rows T17–T19 will need, and a
`_LABELS` table built once from the probe artifact and read by `_label(key, token)`. No new control
in `_CONTROL_KEYS` yet, so `for_key("fa_pe")` still answers `()` — T17 is what registers it.

**Verify** `python -m pytest tests/test_screen_buckets_groups.py tests/test_screen_buckets.py -q`

---

### T17 — Valuation family (9 controls, 204 ids) *(tab: fundamental · disposition: join)*

**Files** `app/screen/buckets.py`, `tests/test_screen_buckets_groups.py`.
**Disposition** `join` — every column is a depth cell (`pe`, `forward_pe`, `peg`, `ps`, `pb`, `pc`,
`pfcf`, `ev_ebitda`, `ev_sales`), published on the row under the screen field name by
`depth_overlay.SCREEN_ALIASES`. `price_to_cash` is `derived formula` when the fundamentals join
answers it and `join` otherwise; the bucket reads the row field either way.

**RED**

```python
def test_pe_low_is_under_15_and_high_is_over_50():
    assert _matches("pe:low", {"pe_ttm": 12.0}) and not _matches("pe:low", {"pe_ttm": 15.0})
    assert _matches("pe:high", {"pe_ttm": 51.0}) and not _matches("pe:high", {"pe_ttm": 50.0})

def test_ev_ebitda_negative_bucket_is_below_zero():
    assert _matches("evebitda:negative", {"ev_ebitda": -3.0})
    assert not _matches("evebitda:negative", {"ev_ebitda": 0.0})

def test_ev_sales_positive_is_strictly_over_zero():
    assert _matches("evsales:positive", {"ev_to_sales": 0.01})
    assert not _matches("evsales:positive", {"ev_to_sales": 0.0})

def test_price_to_cash_reads_the_screen_field_not_the_depth_spelling():
    assert _matches("pc:u10", {"price_to_cash": 8.0})
    assert not _matches("pc:u10", {"pc": 8.0})     # the store's spelling is not the row field

def test_a_null_multiple_matches_no_bucket():
    for token in ("low", "high", "u5"):
        assert not _matches(f"pe:{token}", {"pe_ttm": None})

def test_the_nine_valuation_controls_are_registered_with_204_ids():
    assert sum(len(buckets.for_key(k)) for k in
               ("fa_pe", "fa_fpe", "fa_peg", "fa_ps", "fa_pb", "fa_pc", "fa_pfcf",
                "fa_evebitda", "fa_evsales")) == 204
```

**Impl** nine `_family(...)` rows over `pe_ttm`, `pe_forward`, `peg`, `ps`, `pb`, `price_to_cash`,
`pfcf`, `ev_ebitda`, `ev_to_sales`, with `_BANDS` rows for `low`/`high`/`profitable`/`negative`/
`positive` (thresholds from the audit's label table: P/E 15/50, PEG 1/2, P/S 1/10, P/B 1/5,
P/C 3/50, P/FCF 15/50, EV/EBITDA 15/50, EV/Sales 1/10).

**Verify** `python -m pytest tests/test_screen_buckets_groups.py -q`

---

### T18 — Growth family (11 controls, 176 ids) *(tab: fundamental · disposition: join)*

**Files** `app/screen/buckets.py`, `tests/test_screen_buckets_groups.py`.
**Disposition** `join` — eleven depth growth-rate cells, 2,383–4,038 rows each.

**RED**

```python
def test_eps_growth_poslow_is_the_zero_to_ten_band():
    assert _matches("epsyoy:poslow", {"eps_this_y": 0.0})
    assert _matches("epsyoy:poslow", {"eps_this_y": 10.0})
    assert not _matches("epsyoy:poslow", {"eps_this_y": 10.01})

def test_estltgrowth_reads_the_next_five_year_rate_not_the_forward_eps_level():
    # eps_forecast_next_fy is a LEVEL (4.20 = $4.20); eps_next_5y is a percent (12.0 = 12%)
    assert _matches("epsnext5y:o10", {"eps_next_5y": 12.0})
    assert not _matches("epsnext5y:o10", {"eps_forecast_next_fy": 12.0, "eps_next_5y": None})

def test_a_growth_bucket_reads_percent_not_fraction():
    assert _matches("epsyoy:o10", {"eps_this_y": 18.52})       # AAPL
    assert not _matches("epsyoy:o10", {"eps_this_y": 0.1852})  # the same number as a fraction

def test_every_growth_key_shares_one_vocabulary():
    keys = ("fa_epsyoy", "fa_epsyoy1", "fa_epsqoq", "fa_epsyoyttm", "fa_eps3years",
            "fa_eps5years", "fa_estltgrowth", "fa_salesqoq", "fa_salesyoyttm",
            "fa_sales3years", "fa_sales5years")
    assert {tuple(b.finviz for b in buckets.for_key(k)) for k in keys} == {tuple(buckets._tokens("fa_epsyoy"))}
    assert sum(len(buckets.for_key(k)) for k in keys) == 176
```

**Impl** eleven `_family(control, key, column, bands=_GROWTH)` calls. `fa_estltgrowth` is the one to
watch: the row also carries `eps_forecast_next_fy` (a level) and the old table mapped one to the
other once already.

**Verify** as T17.

---

### T19 — Quality, leverage, ownership, dividend growth (15 controls, 380 ids) *(tab: fundamental)*

**Files** `app/screen/buckets.py`, `app/screen/filter_fields.py`, `tests/test_screen_buckets_groups.py`.
**Disposition** `join`, except `insidertrans` which is `unavailable` (data present, product withdrawn
it — audit §7.1) and `divgrowth`'s nine `cy*` tokens, which are `unavailable` (no 1…9-year series).

**RED**

```python
def test_verypos_thresholds_are_per_control_not_shared():
    assert _matches("roe:verypos", {"roe": 31.0})
    assert not _matches("roa:verypos", {"roa": 20.0})   # ROA's Very Positive is >15, ROE's >30
    assert _matches("roa:verypos", {"roa": 16.0})

def test_dividend_growth_year_token_picks_the_window():
    assert _matches("divgrowth:1yo5", {"dividend_growth_1y": 6.0})
    assert not _matches("divgrowth:1yo5", {"dividend_growth_1y": 4.0})
    assert _matches("divgrowth:3yo10", {"dividend_growth_3y": 11.0})

def test_a_control_that_has_no_column_for_a_token_refuses_it_by_name():
    with pytest.raises(buckets.UnavailableBucket, match="no series"):
        buckets.bucket("divgrowth:cy9")

def test_insider_transactions_are_refused_with_the_quality_reason():
    with pytest.raises(buckets.UnavailableBucket, match="mixture of ownership levels and junk"):
        buckets.bucket("insidertrans:pos")

def test_negative_margin_bands_compile_after_the_band_fix():
    assert _matches("grossmargin:u-50", {"gross_margin": -60.0})
    assert not _matches("grossmargin:u-50", {"gross_margin": -40.0})
```

**Impl** fifteen families; `_BANDS` rows for `roa/roe/roi` (verypos 15/30/25, veryneg −15/−15/−10),
`grossmargin` (high 50), `opermargin` (high 25, veryneg −20), `netmargin` (high 20, veryneg −20),
`payout` (none 0, low 20, high 50), `curratio` (low 1, high 3), `quickratio` (low 0.5, high 3),
`ltdebteq`/`debteq` (low 0.1, high 0.5), `insiderown` (low 5, high 30, veryhigh 50), `instown`
(low 5, high 90). `divgrowth` needs its own small parser — `(1|3|5)ypos` → `gt 0` on that year's
column, `(1|3|5)yoN` → `gt N` on that year's column — because its token carries **two** facts
(window and threshold); the nine `cy*` are `refuse={token: "no 1-to-9-year dividend series: the depth
store carries 1y/3y/5y only"}`. `insidertrans` is thirty-two `refuse=` entries whose reason quotes the
product's own withdrawal. A refused token is an unavailable **bucket**, never an omission, so
`_validate()` still sees a complete family.

**Also** replace `filter_fields._GROUPS`' growth/dividend/lt-debt/insider reasons with the ones that
are now true (see §4, item 1).

**Verify** as T17 + `python -m pytest tests/test_screen_filter_fields.py -q`

---

### T20 — Depth fields become filter fields *(tab: fundamental · disposition: join)*

**Files** `app/screen/universe.py`, `app/screen/filters.py`, `app/screen/filter_fields.py`,
`app/screen/buckets.py`, `tests/test_screen_depth_fields.py` (new).

**RED**

```python
def test_every_depth_field_is_a_catalog_column():
    from app.screen.columns import CATALOGUE_KEY_SET
    assert universe.DEPTH_FIELDS <= CATALOGUE_KEY_SET

def test_the_depth_store_fills_every_declared_field():
    store = json.loads(Path("/home/ubuntu/ztockz-data/depth.json").read_text())["symbols"]
    missing = [k for k in universe.DEPTH_FIELDS
               if not any(isinstance(row.get(k), (int, float)) for row in store.values())]
    assert missing == []          # a declared field the collector stopped writing

def test_lt_debteq_filters_the_ratio_column():
    assert filter_fields.binding("fa_ltdebteq").field == "lt_debt_to_equity"
    answer = selection.select_rows(
        [{"symbol": "X", "lt_debt_to_equity": 0.66}],
        _req("fa_ltdebteq", "eq", "ltdebteq:o0.5"),
        region="us", as_of="2026-11-01T20:00:00+00:00",
    )
    assert answer.total == 1

def test_epsrev_bp_needs_both_surprises():
    assert _matches("epsrev:bp", {"eps_surprise": 4.0, "revenue_surprise": 1.0})
    assert not _matches("epsrev:bp", {"eps_surprise": 4.0, "revenue_surprise": None})
```

**Impl** `DEPTH_FIELDS` + `CARRIED_FIELDS` (D10); `FIELDS` entries for the 23 numeric ones
(`eps_this_y` … `insider_trans`, `lt_debt_to_equity`, `high_50d`, `low_50d`, `all_time_high`,
`all_time_low`) so a caller may also send `{key:"fa_epsyoy", op:"gte", value:15}`;
`candlestick_pattern` and `news_time` stay **out** of `FIELDS` (text, bucket-only) so no numeric
operator can reach them. `fa_ltdebteq` gains `field="lt_debt_to_equity"` **and** its 22-token family;
`fa_epsrev` stays `field=None` with a 3-id family over two cells.

**Verify** `python -m pytest tests/test_screen_depth_fields.py -q`

---

### T21 — Technical price and oscillator families (6 controls, 135 ids) *(tab: technical)*

**Files** `app/screen/buckets.py`, `app/screen/choice_filters.py`,
`tests/test_screen_buckets_groups.py`.
**Disposition** `scan row` for all six — `rsi_14`, `gap_pct`, `change_pct`, `change_from_open`,
`beta_1y`, `atr_14` are parity cells at 99.7–99.95 %. The `3y` performance tokens are the one
**derived formula** here (`ret_3y`, already on 5,573 rows).

**RED**

```python
def test_gap_u_means_up_and_d_means_down():
    assert _matches("gap:u2", {"gap_pct": 2.5})
    assert not _matches("gap:u2", {"gap_pct": -2.5})
    assert _matches("gap:d2", {"gap_pct": -2.5})

def test_gap_u0_is_not_under_zero():
    # the trap: _band("u0") is ("lt", 0.0); "Up 0%" is >= 0
    assert _matches("gap:u0", {"gap_pct": 0.0})
    assert not _matches("gap:u0", {"gap_pct": -0.1})

def test_rsi_overbought_synonym_resolves_to_the_canonical_id():
    assert buckets.bucket("rsi:overbought").id == "rsi:ob70"
    assert _matches("rsi:overbought", {"rsi_14": 71.0})
    assert not _matches("rsi:ob70", {"rsi_14": 69.9})

def test_rsi_not_overbought_is_lt_not_lte():
    assert _matches("rsi:nob60", {"rsi_14": 59.9})
    assert not _matches("rsi:nob60", {"rsi_14": 60.0})

def test_atr_tokens_are_price_units():
    assert _matches("atr:o5", {"atr_14": 5.5})
    assert not _matches("atr:o5", {"atr_14": 4.9})

def test_the_three_year_performance_tokens_compile_against_ret_3y():
    assert "3yup" in choice_filters.choices_for("ta_perf")
    assert choice_filters.UNSUPPORTED["ta_perf"] == {}
    assert choice_filters.compile_option("ta_perf", "3y10u").value == -10.0
```

**Impl** `_direction_family` for gap/change/changeopen (D8), plain `_family` for `rsi` (with the
`ob/os/nob/nos` builder and two synonyms), `beta`, `atr`; and in `choice_filters`, replace
`Window("3y", "3-year", None, _NO_THREE_YEAR)` with `Window("3y", "3-year", "ret_3y")` and delete
`_NO_THREE_YEAR`. `ret_3y` is already in `PARITY_FIELDS` and in `CARRIED_FIELDS`, so the module's own
boot gate passes unchanged.

**Verify** `python -m pytest tests/test_screen_buckets_groups.py tests/test_screen_choice_filters.py -q`

---

### T22 — Technical trend and range families (6 controls, 178 ids) *(tab: technical)*

**Files** `app/screen/buckets.py`, `tests/test_screen_buckets_groups.py`.
**Disposition** `derived formula` — `(price / level − 1) × 100` over `sma_20/50/200` (scan row) and
`high_50d/low_50d`, `high_52w/low_52w`, `all_time_high/all_time_low` (join, 75.09 %). Nine cross
tokens per SMA key and `alltime:nh`/`nl` are `unavailable` with a named source.

**RED**

```python
def test_sma_distance_is_price_over_sma_minus_one():
    assert _matches("sma20:pa10", {"price": 110.0, "sma_20": 100.0})
    assert not _matches("sma20:pa10", {"price": 109.0, "sma_20": 100.0})
    assert _matches("sma20:pb10", {"price": 90.0, "sma_20": 100.0})

def test_sma_above_is_a_two_column_comparison():
    assert _matches("sma20:sa50", {"sma_20": 101.0, "sma_50": 100.0})
    assert not _matches("sma20:sa50", {"sma_20": 99.0, "sma_50": 100.0})

def test_a_cross_token_is_refused_by_name_not_by_zero_rows():
    with pytest.raises(buckets.UnavailableBucket, match="cross"):
        buckets.bucket("sma20:cross50a")

def test_a5h_is_above_the_low_not_below_the_high():
    row = {"price": 55.0, "high_50d": 60.0, "low_50d": 50.0}
    assert _matches("highlow50d:a10h", row)          # 10% above the low
    assert not _matches("highlow50d:b10h", row)      # only 8.3% below the high

def test_new_high_is_within_a_tick_of_the_level():
    assert _matches("highlow52w:nh", {"price": 99.95, "high_52w": 100.0})
    assert not _matches("highlow52w:nh", {"price": 99.0, "high_52w": 100.0})

def test_an_implausible_level_matches_nothing():
    assert not _matches("highlow50d:b10h", {"price": 1.0, "high_50d": 1250.0})

def test_all_time_new_high_is_refused_while_the_column_is_unreliable():
    with pytest.raises(buckets.UnavailableBucket, match="52-week high"):
        buckets.bucket("alltime:nh")

def test_candlestick_matches_only_the_two_classes_we_can_name():
    assert _matches("candle:h", {"candlestick_pattern": "hammer"})
    assert _matches("candle:d", {"candlestick_pattern": "doji"})
    with pytest.raises(buckets.UnavailableBucket, match="no classifier"):
        buckets.bucket("candle:lls")
```

**Impl** `_distance` (D9) for the six keys; `nh`/`nl` as `price >= 0.999*level` / `<= 1.001*level`;
the 27 cross tokens as `available=False` with the reason *"needs yesterday's price and yesterday's
sma: a cross is an event between two sessions and no row carries the previous session"*;
`alltime:nh`/`nl` refused with *"the all-time-high cell is below the 52-week high on 22 rows, so a
new-all-time-high test would admit rows that are nowhere near one"*; `candle` as an equality family
over `candlestick_pattern` for `h` and `d`, the other nine refused naming the missing classifier.

**Verify** as T17.

---

### T23 — News family and the clock (1 control, 12 ids) *(tab: news · disposition: join)*

**Files** `app/screen/buckets.py`, `app/api/screens.py`, `tests/test_screen_buckets_groups.py`.
**Disposition** `join` — `news_time` (ISO-8601 UTC, 4,112 rows, **one headline per symbol**).
Three minute-window tokens are `unavailable` (`prevminutes5`, `prevminutes30`, `prevhours1`);
`prevhours24` stays but is documented as "the newest headline is within 24 h".

**RED**

```python
def test_news_today_is_new_york_today():
    at = "2026-11-02T02:30:00+00:00"           # 2026-11-01 22:30 in New York
    assert _matches_at("news:today", {"news_time": "2026-11-01T18:00:00+00:00"}, at)
    assert not _matches_at("news:today", {"news_time": "2026-11-02T01:00:00+00:00"}, at)

def test_news_todayafter_needs_a_stamp_after_the_close():
    at = "2026-11-02T02:30:00+00:00"
    assert _matches_at("news:todayafter", {"news_time": "2026-11-01T21:30:00+00:00"}, at)  # 17:30 ET
    assert not _matches_at("news:todayafter", {"news_time": "2026-11-01T15:00:00+00:00"}, at)  # 11:00 ET

def test_a_minute_window_is_refused_with_the_collector_cadence():
    with pytest.raises(buckets.UnavailableBucket, match="13 minutes"):
        buckets.bucket("news:prevminutes5")

def test_a_clock_dependent_answer_carries_its_day():
    revision = _data_revision(...)                      # app/api/screens.py
    assert "clock=2026-11-01" in revision

def test_two_days_are_two_etags():
    assert _etag_for(as_of="2026-11-01T20:00:00Z") != _etag_for(as_of="2026-11-02T20:00:00Z")
```

**Impl** `Context` gains `moment: datetime` (defaulted from `today` so the existing constructor keeps
working) and `at()` fills it; `news` family with `today`, `todayafter`, `sinceyesterday`,
`sinceyesterdayafter`, `yesterday`, `yesterdayafter`, `prevhours24`, `prevdays7`, `prevmonth`
(30 days, said in the label); the three minute tokens refused with *"the depth store refreshes about
every 13 minutes, so a five-minute window can only contain rows the poll happened to land on"*.
`_data_revision` gains `clock=<New York date of as_of>` — the same hole already lets `earn:today`
304 across midnight, so this closes both.

**Verify** `python -m pytest tests/test_screen_buckets_groups.py tests/test_api_screens_etag.py -q`

---

### T24 — The document serves five tabs *(tab: all · disposition: n/a)*

**Files** `app/screen/descriptive.py` (from T9), `app/api/screens.py` (route from T10),
`tests/test_screen_descriptive_groups.py` (new).
**Disposition** metadata only: every control gains `tab` and `disposition` ∈
`scan_row | join | derived | new_source | unavailable`, and `unavailable` carries both the tier and
the metric.

**RED**

```python
def test_the_document_has_five_tabs():
    doc = descriptive.document()
    assert [g["id"] for g in doc["groups"]] == ["descriptive", "fundamental", "technical", "news", "etf"]
    # descriptive keeps its 25 controls (order, signal, 23 keys incl. sh_dilution)
    assert {g["id"]: len(g["controls"]) for g in doc["groups"]} == {
        "descriptive": 25, "fundamental": 37, "technical": 20, "news": 2, "etf": 1}

def test_every_control_states_its_tab_and_disposition():
    for group in descriptive.document()["groups"]:
        for control in group["controls"]:
            assert control["tab"] == group["id"]
            assert control["disposition"] in {"scan_row", "join", "derived", "new_source", "unavailable"}
            if control["disposition"] == "unavailable":
                assert control["available"] is False and control["reason"]

def test_every_option_of_a_live_bucket_control_has_a_predicate():
    for group in descriptive.document()["groups"][1:]:
        for control in group["controls"]:
            if control["control"] != "buckets":
                continue
            for option in control["options"]:
                if option["available"]:
                    assert option["predicate"], (control["id"], option["id"])
                else:
                    assert option["reason"], (control["id"], option["id"])

def test_the_elite_only_controls_name_the_tier_and_the_source():
    by_id = {c["id"]: c for g in descriptive.document()["groups"] for c in g["controls"]}
    for control_id in ("sh_trades", "ah_close", "ah_change", "news_keywords", "etf_heldby"):
        control = by_id[control_id]
        assert control["available"] is False
        assert "Elite" in control["reason"] and ("no source" in control["reason"] or "not ingested" in control["reason"])

def test_the_etf_tab_has_one_control_and_no_options():
    etf = descriptive.document()["groups"][4]
    assert len(etf["controls"]) == 1 and etf["controls"][0]["key"] == "etf_heldby"
```

**Impl** a `_GROUPS_BY_TAB` table in `descriptive.py` (tab → ordered keys, read from
`FILTER_CATALOG`) and one `DISPOSITIONS: Mapping[str, str]` keyed by catalogue key — the single place
a control's provenance is stated. The document's `schema_version` moves to `"finviz-2"`; T10's ETag
already hashes the body.

**Verify** `python -m pytest tests/test_screen_descriptive_groups.py tests/test_api_screen_filters.py -q`

---

### T25 — Adversarial sweep and live DoD *(tab: all)*

**Files** `tests/test_screen_groups_adversarial.py` (new). No production edit expected.

**RED**

```python
def test_a_token_from_another_control_is_refused():
    with pytest.raises(SelectionError, match="belongs to control"):
        selection.parse_selection({"mode": "criteria", "region": "us",
                                   "criteria": {"filters": [{"key": "ta_rsi", "op": "eq", "value": "pe:u15"}]}})

def test_an_unavailable_token_is_422_and_never_an_unfiltered_page(client):
    body = post(client, {"key": "ta_pattern", "op": "eq", "value": "wedge"})
    assert body["error"] == "unavailable_filter_key" and "total" not in body

def test_the_twelve_refusals_are_exactly_the_twelve():
    assert set(filter_fields.unavailable_keys()) == {
        "theme", "subtheme", "ipodate", "sh_trades", "sh_opt", "sh_insidertrans",
        "ta_highlow20d", "ta_pattern", "ah_close", "ah_change", "news_keywords", "etf_heldby"}

def test_116_columns_are_still_116_and_carry_no_depth_only_names():
    from app.screen.views import ALL_COLUMNS
    assert len(ALL_COLUMNS) == 116
    assert "eps_this_y" in ALL_COLUMNS          # a catalogue column, not a new one
    assert "index_members" not in ALL_COLUMNS   # still internal-only

def test_the_column_cap_still_refuses_41():
    assert selection.COLUMN_LIMIT == 40
```

**Live DoD** (against `:8111`, never 8099):

```bash
curl -s localhost:8111/screens/filters | jq '{v:.schema_version,
  tabs:[.groups[]|{id,n:(.controls|length)}],
  live:([.groups[].controls[]|select(.available and .key)]|length),
  off:([.groups[].controls[]|select((.available|not) and .key)|.id])}'
# {"v":"finviz-2","tabs":[{"id":"descriptive","n":25},{"id":"fundamental","n":37},
#  {"id":"technical","n":20},{"id":"news","n":2},{"id":"etf","n":1}],"live":70,
#  "off":["theme","subtheme","ipodate","sh_trades","sh_opt","sh_dilution",
#          "sh_insidertrans","ta_highlow20d","ta_pattern","ah_close","ah_change",
#          "news_keywords","etf_heldby"]}
# 70 live *keys*; 72 live controls with `order` and `signal`. `sh_dilution` is a
# control with no catalogue key, so it is in `off` but not in the 12.
```

- `{"key":"fa_epsyoy","op":"eq","value":"epsyoy:o25"}` → 200, `total` ≈ 1,260, every row's
  `eps_this_y > 25`.
- `{"key":"ta_rsi","op":"eq","value":"rsi:ob70"}` → 200, `total == 271` (the same count
  `signals.py` reports for `overbought`).
- `{"key":"ta_highlow52w","op":"eq","value":"highlow52w:nl"}` → 200, `total == 56`.
- `{"key":"ta_perf","op":"eq","value":"3y10u"}` → 200 (was 422); `ta_perf2` same.
- `{"key":"fa_ltdebteq","op":"eq","value":"ltdebteq:o0.5"}` → 200, `total` ≈ 1,700.
- `{"key":"news","op":"eq","value":"news:today"}` → 200 with a small `total` (a Sunday: 506 rows are
  within 24 h, `news:today` is fewer).
- `{"key":"ta_gap","op":"eq","value":"gap:u0"}` → 200, every row's `gap_pct >= 0`.
- `{"key":"fa_epsyoy","op":"eq","value":"epsyoy:u-10"}` → 200 (the negative-threshold family).
- `{"key":"insidertrans","op":"eq","value":"insidertrans:pos"}` → 422 `unavailable_bucket`.
- `{"key":"sh_trades","op":"eq","value":"sh_trades:u100"}` → 422 `unavailable_filter_key` naming the
  tier and the source.
- `python -m pytest tests/test_screen_buckets_groups.py tests/test_screen_depth_fields.py \
   tests/test_screen_descriptive_groups.py tests/test_screen_groups_adversarial.py \
   tests/test_screen_buckets.py tests/test_screen_selection_buckets.py \
   tests/test_screen_choice_filters.py tests/test_screen_filter_fields.py -q` → 0 failed
- `cd web && npm run check` → clean.

## 4. What could break

1. **`filter_fields` disposition counts.** `tests/test_screen_filter_fields.py` pins 50/32 today and
   T11 moves it to 51/31; this plan moves it again to **70/12** (the twelve are listed in T25), and
   the module docstring's "Forty-seven keys… thirty-one are refused" needs the same edit. Three tasks
   in a row editing one constant is a merge conflict waiting to happen — land the counts in **one**
   commit after T22, not per task.
2. **`buckets`' boot gate is stricter than it looks.** `_validate()` compares a family's tokens to
   `_tokens(key)` *in order*; a family built with `tokens=` (e.g. `divgrowth` without `cy*`) fails
   the gate unless the refused tokens are `Bucket(available=False)` entries inside the family, not
   omissions. Every refusal in this plan is an unavailable **bucket**, never a missing row.
3. **The 207 existing ids must not move.** `price:u5`, `cap:mega`, `idx:sp500` are in saved URLs.
   T16's regression test pins `_band`'s output for the descriptive tokens; nothing in T17–T23 edits
   an existing family.
4. **`fa_pe` already answers a numeric value.** Adding a bucket family to a mapped key is legal
   (`sh_price` does it), but `_bucket_criterion` returns `None` for a value that is a plain number,
   so `{op:"lt", value:15}` keeps going to the DSL and `{op:"eq", value:"pe:u15"}` to the bucket.
   A test for both on one key belongs in T17.
5. **The `pc` alias trap.** `depth_overlay` puts the store's `pc` cell on the row *and* the alias
   `price_to_cash`; a family that reads `pc` would answer 4,163 rows that no other layer agrees
   with. T17's `test_price_to_cash_reads_the_screen_field_not_the_depth_spelling` is the guard.
6. **`all_time_high` is wrong on ~361 rows** (audit §7.3). Any distance bucket over it is fine — a
   wrong level gives a wrong distance, but the `nh`/`nl` tokens are a false-positive machine, which
   is why T22 refuses exactly those two and not the family.
7. **`Context` is frozen and slots.** Adding `moment` with a default keeps `Context.at(...)` and every
   existing test valid; a required field would break `Context(today=…)` in `tests/` and any future
   caller. Do not add a third clock field — `moment` is the answer's own read instant, `today` is
   that instant in New York.
8. **The clock and the ETag.** `_data_revision` has no clock today, so `earn:today` can 304 across
   midnight. T23 adds `clock=<NY date>`; without it a news answer is cacheable past its own window,
   and with a *minute* window it would be wrong within the 5-minute scan cache — which is the second
   reason the minute tokens are refused rather than built.
9. **1,273 buckets is 1,273 closures.** Memory is a few hundred KB, but a typo in `_CONTROL_KEYS`
   produces two families under one namespace and `_validate` only catches duplicate *ids*, not two
   controls naming the same key: add `assert len(set(_CONTROL_KEYS.values())) == len(_CONTROL_KEYS)`
   in T16.
10. **Packaging.** `app/screen/data/descriptive_options.json` is already T9's problem
    (`[tool.setuptools.package-data]`); this plan adds no new data file.
11. **T24 rewrites five published reasons.** `sh_trades`, `ah_close`, `ah_change`, `news_keywords`
    and `etf_heldby` currently inherit `filter_fields.UNAVAILABLE`'s sentence, which says nothing
    about the Elite tier. D11 wants both facts in one string, so the descriptive group's `sh_trades`
    reason changes too — check `tests/test_screen_descriptive.py` for a pinned substring before
    editing, and update the test in the same commit if it pins one.

## 5. Out of scope, stated rather than faked

| control | tab | why | what would unblock it |
|---|---|---|---|
| **Trades** (`sh_trades`) | descriptive | Elite-only in the probe (`isEliteOnly: true` on the control). The value is a **print count**; the scan carries `volume` (shares) and `Value.Traded` (currency), depth has no count, FINRA gives short-sale volume. `volume/100` would be a fabricated metric wearing Finviz's label | Polygon.io aggregates, IEX HIST, Databento |
| **After-Hours Close** (`ah_close`) | technical | Elite-only **and** no source: the scan is regular-session. The provider returns `premarket_close`/`postmarket_close` (90.7 % / 100 % on a 400-row probe) but no collector stores either | ingest `postmarket_close` + `postmarket_change` into depth, then a price family |
| **After-Hours Change** (`ah_change`) | technical | same | same |
| **News Keywords** (`news_keywords`) | news | Elite-only, and it needs an article corpus. We hold **one headline per symbol**, so a keyword filter would search 4,112 titles, one per company | a news collector (N articles/symbol/day) + a text index |
| **ETF Held By** (`etf_heldby`) | etf | Elite-only, and needs a fund→constituent table; none is ingested. The six index member lists are not holdings | a fund-holdings feed |
| **Pattern** (`ta_pattern`) | technical | 28 tokens, no bars store, no detector; `yahoo_chart` computes indicators and discards the series | persist daily bars + a pattern engine |
| **20-Day High/Low** (`ta_highlow20d`) | technical | no 20-day window anywhere; a 50-day high cannot be narrowed | bars store, or a provider 20-day column (unprobed) |
| **Insider Transactions** (`sh_insidertrans`) | fundamental | the data exists (4,118 rows) and is junk — `signals.py` withdrew it by name (TSLA 76.0, META 45.9, GME 8.7) | SEC EDGAR Form 4 rollup per symbol |
| **Dilution Risk** (`sh_dilution`) | descriptive | not in the 82-key catalog at all (the probe has 83); needs share-count history | SEC `dei:EntityCommonStockSharesOutstanding` across filings |
| **Theme / Sub-theme** | descriptive | no classification column in scan, depth, snapshot or SQLite | licensed taxonomy or an editorial GICS→theme map |

Each of these ships in the document as `available: false` with `disposition: "unavailable"` and a
reason that names **both** the tier and the missing source — the shape D11 fixes.

## 6. Order and size

New live keys per task — the count only moves when a key goes from refused to available; building a
token family for an already-live key (all nine of T17, five of T21) adds ids, not keys.

| task | tab | new bucket ids | new live keys | live after | risk |
|---|---|---|---|---|---|
| T16 | — | 0 | 0 | 51 | low (regex + builder) |
| T17 | fundamental | 204 | 0 (nine keys already answer a number) | 51 | low |
| T18 | fundamental | 176 | **+11** (`fa_epsyoy` … `fa_sales5years`) | 62 | low |
| T19 | fundamental | 380 (41 refused) | **+2** (`fa_divgrowth`, `sh_insttrans`) | 64 | medium (`_GROUPS` rewrite) |
| T20 | fundamental | 25 (22 `ltdebteq` + 3 `epsrev`) | **+2** (`fa_ltdebteq`, `fa_epsrev`) | 66 | medium (`CARRIED_FIELDS`) |
| T21 | technical | 135 | 0 (six keys already answer a number) | 66 | medium (choice-window fix) |
| T22 | technical | 178 (29 refused) | **+3** (`ta_highlow50d`, `ta_alltime`, `ta_candlestick`) | 69 | high (three anchors, guards) |
| T23 | news | 12 (3 refused) | **+1** (`news_date`) | 70 | high (clock + ETag) |
| T24 | all | 0 | 0 | 70 | medium (document, UI) |
| T25 | all | 0 | 0 | 70 | — |

19 keys leave the refused list; `sh_insidertrans` deliberately does not (T19), which is the one
difference between "every refused key with data" (20) and what this plan ships (19). T25's
`test_the_twelve_refusals_are_exactly_the_twelve` is the assertion that ends the argument.

Size: `buckets.py` grows by roughly 150 lines (a 40-row `_BANDS` table, four builders, one synonym
map); `universe.py` by 25 (`DEPTH_FIELDS`); `filters.py` by 23 `FIELDS` rows plus one `field=` on
`fa_ltdebteq`; `choice_filters.py` by −6 (deleting the 3-year refusal); `descriptive.py` by ~40 (tab
+ disposition tables). No new module except the tests.
