# Exchange in the data layer — ingestion → row, sources of truth, TXSE, coverage

**Repo** `/home/ubuntu/ztockz` @ `a71fd93` · **Live** `127.0.0.1:8099` (read-only) · **Date** 2026-10-31,
measurements taken 15:30–16:05 UTC · **Code changed:** none. This document is the only file written.

Scope: the **data layer** — which module writes `exchange`, which one maps it, which one normalises
it, what value actually ships on a `/screens/select` row, and where the sources disagree. The filter
grammar of `exch` and the UI label are owned by two sibling documents and are only cross-referenced
here, never re-litigated:

* `research/exchange-contract.md` — `exch` grammar, value aliases, `GET /market/exchanges`;
* `research/exchange-ui.md` — the toolbar control and the human label (it decides the label lives in
  `web/src/lib/exchanges.ts`, **client-side**, so the wire column stays a code).

Every count below names its source and its field. Every code claim carries `file:line`. Every defect
carries a reproducer that was run.

---

## 0. The six answers, up front

| Question | Answer | Section |
|---|---|---|
| What sets `exchange` on a screen row? | `build_universe` → `_normalize` → `mic_for(exchange)`, one assignment at `app/screen/universe.py:446,451`. Nothing downstream rewrites it. | §1 |
| What is the value? | A **MIC for four venues** (`XNAS/XNYS/XASE/XCBO`) and the **raw provider code for everything else** (`SIX`, `HKEX`, `BCS` …). | §1.1 |
| Which source wins? | On the screen path, TradingView's scan column, mapped by `MIC_BY_EXCHANGE`. In the depth store, **SEC EDGAR wins on 4,209 of 4,217 rows, byte-for-byte**; Yahoo fills the 8 SEC does not know. | §2 |
| Why do 9 rows say TXSE in depth and XASE in the screener? | EDGAR prints `TXSE` for them; TradingView has **no TXSE venue at all** and files the same tickers under `AMEX`. The two never meet because the overlay is fill-missing-only. | §3 |
| What does the OTC exclusion cost? | **6,008 stock rows + 340 ETF rows** of the provider's answer, today. | §5 |
| Does anything test this? | 26 tests pin exchange behaviour; **none** pins `mic_for`, the pass-through rule, or the overlay's non-overwrite of `exchange`. | §6 |

---

## 1. The trace: exchange from ingestion to the API row

`POST /screens/select` never reads the `screen_rows` table. It rebuilds the universe from the scan
cache on every request (`app/api/screens.py:1203-1218`), so there are **two materialisations** of the
same column (response and DB) and both come from the same function.

### 1.1 The hops (screen path, criteria and symbols modes)

| # | Hop | File:line | What happens to `exchange` |
|---|---|---|---|
| 1 | Scan request built | `app/screen/scanner.py:99-121` (`"exchange"` at :102) | `exchange` is requested as a provider column |
| 2 | OTC gate at the provider | `app/screen/scanner.py:283` | `{"left":"exchange","operation":"not_in_range","right":["OTC","PNK"]}` — rows never leave the provider |
| 3 | Response parsed | `app/screen/scanner.py:313-336` | `d[]` is zipped against `columns` by name; `exchange` lands verbatim (e.g. `"AMEX"`). A **second** venue cell `venue` is derived from the ticker prefix at :333 and never read again (§7 D9) |
| 4 | Universe filter | `app/screen/universe.py:422-424` | `code = str(...).strip().upper()`; empty or `OTC_EXCHANGES` → row dropped |
| 5 | MIC mapping | `app/screen/universe.py:446` → `mic_for` at `:302-306` | `MIC_BY_EXCHANGE.get(code, code)` — **unknown codes pass through unchanged** |
| 6 | MIC table | `app/screen/universe.py:130-138` | `NASDAQ→XNAS`, `NYSE→XNYS`, `AMEX→XASE`, `CBOE→XCBO` — four entries, no TXSE |
| 7 | Row cell written | `app/screen/universe.py:451` | `"exchange": mic` — the only assignment to this key on the screen path |
| 8 | US-tab gate | `app/screen/universe.py:619,626-633` | `is_us = exchange in US_EXCHANGES`; a non-US value is dropped from `us`/`us_etf`, kept only by `global` |
| 9 | Depth overlay | `app/api/screens.py:1228` → `app/screen/depth_overlay.py:186-223` | **fill-missing-only** (`:181-183`). `exchange` is present and non-`None`, so the store can never overwrite it |
| 10 | Harmonise | `app/screen/columns.py:578-599` | synonym fill for `pe`/`pc`/`ipo_date`; `exchange` has no synonym, untouched |
| 11 | Column map | `app/screen/columns.py:183-185` | `"exchange": "exchange"` — one column, documented as "A MIC on the row (`XNAS`)" |
| 12 | Row rendering | `app/screen/snapshot.py:68-90` (`:79`) | `display_row` copies `row["exchange"]` |
| 13 | Response | `app/screen/selection.py:947-1022` | `select_rows` projects the row; no transformation |

**Live value that ships** (`columns:["exchange"]`, `region=us`, `universe_total 5576`):

```
XNAS 3308   XNYS 2009   XASE 258   XCBO 1        sum = 5576
```

Four distinct values, no `null`, and the four `eq` counts partition the universe exactly — that is
the proof there is no fifth code (reproduced in §8(a)).

### 1.2 The other three surfaces of the same column

| Surface | Path | Value for `DDS` | Citation |
|---|---|---|---|
| `POST /screens/select` | rebuilt per request from the scan | `XASE` | §1.1 |
| `GET /screens` (browse) | `refresh_screen` → `build_universe` → `overlay` → `instrument_id = f"{exchange}:{symbol}"` → `repository.save` | `XASE`, `instrument_id "XASE:DDS"` | `app/screen/snapshot.py:141,148,153,155` |
| `screen_rows` table | written by the browse path only | `XASE` (17 distinct codes across 95 snapshots, 1,900 rows) | `app/screen/repository.py:44-57,212-216,310` |
| `POST /screens/{id}/export` | `build_universe` → `overlay` → `provider_symbol(symbol, exchange)` | `XASE` | `app/screen/export.py:66,313,455,462` |
| `GET /market/depth` | depth store document | **`TXSE`** | `app/market/depth.py:1206-1207` |
| `GET /engine/security/{sym}`, `POST /engine/screen` | `SecurityRecord.exchange` | **`TXSE`**, `mic: null` | `app/engine/service.py:507`, `app/engine/store.py:111` |

`/engine/screen` filters on the **raw EDGAR venue**, not the MIC: `{"field":"exchange","op":"eq","value":"TXSE"}`
returns `total 12`; `"XASE"` and `"XNAS"` return `total 0`. Reproducer in §7 D1.

### 1.3 What explicitly does not touch it

* `depth_overlay.overlay` (`:204-222`) — runs, sees the key present, skips it.
* `harmonise` (`columns.py:578-599`) — no synonym entry for `exchange`.
* `rank_rows`, `top_rows`, `repository`, `export` — copies only.
* The web client — `web/src/grid/columnMeta.ts:183` is `exchange: { label: "Exchange", ...TEXT }`
  with no `display` hook, so it prints the raw code. There is no MIC→label table anywhere in
  `web/src` (`grep -rn "XNAS\|XNYS\|XASE\|XCBO" web/src` → no hits).

---

## 2. Every source of exchange truth in the repo, and who wins

### 2.1 The sources

| # | Source (field) | Module | Vocabulary it emits | Written into | Live evidence |
|---|---|---|---|---|---|
| S1 | TradingView scan `exchange` column | `app/screen/scanner.py:102`, parsed at `:313-336` | `NASDAQ/NYSE/AMEX/CBOE/OTC` (nothing else — measured over 11,723 stock rows and 6,116 fund rows) | screen rows, via S2 | §8(a) |
| S2 | `MIC_BY_EXCHANGE` + `mic_for` | `app/screen/universe.py:130-138`, `:302-306` | MIC for 4 codes, **provider code otherwise** | `row["exchange"]` at `:451` | 4 values in `us`, 4 in `us_etf`, 13 provider codes in `global` |
| S3 | SEC `company_tickers_exchange.json` → `SecurityRecord.exchange` | `app/engine/sources/sec.py:393,221`; bulk is the identity authority at `app/engine/ingest.py:610-627` | EDGAR venue names; in this dataset exactly `Nasdaq/NYSE/OTC/CBOE/TXSE` | engine snapshot (`store.py:111`), depth store (`depth.py:1206-1207`) | 10,433 records: Nasdaq 4,357 · NYSE 3,270 · OTC 2,579 · CBOE 44 · TXSE 12 · null 171 |
| S4 | SEC `exchange_mic` → `SecurityRecord.mic` | `app/engine/sources/sec.py:99-117,222` | ISO MIC, or `None` for TXSE and any unknown | snapshot `mic` (`store.py:217`), engine API | XNAS 4,357 · XNYS 3,270 · OTCM 2,579 · XCBO 44 · null 183 |
| S5 | Yahoo `fullExchangeName` | `app/market/yahoo_batch.py:132` (v7 batch, wired through the `enrich` rung at `depth.py:1347-1350`) | Yahoo product codes: `NasdaqGM`, `NasdaqCM`, `NasdaqGS`, `NYSE` | depth store, only where S3 is silent | 8 rows |
| S6 | Yahoo `price.exchangeName` (fallback `quoteType.exchange`) | `app/market/yahoo_depth.py:292-293` | `NasdaqGS`, `NMS`, … | depth store (source disabled here: `MEE_DEPTH_YAHOO_FULL=0`) | — |
| S7 | Depth store `exchange` cell | `app/market/depth.py:189-215` (merge), `:525-529` (priority loop) | S3 ∪ S5 ∪ S6 | `depth.json` → `GET /market/depth` | Nasdaq 2,550 · NYSE 1,652 · TXSE 9 · NasdaqGM 4 · NasdaqCM 1 · NasdaqGS 1 |
| S8 | Declared policy | `reference/source_graph.json:2118-2143`; served at `GET /sources/fields/exchange` | — | nothing (documentation) | "primary: yahoo · fallbacks: nasdaq, sec_edgar · crosscheck: tradingview · methodology: *exchange short code from the venue master*" |

### 2.2 Who wins, per surface

| Surface | Winner | Mechanism | Proof |
|---|---|---|---|
| `us` / `us_etf` screen | S1 → S2 | single assignment, no later writer | `universe.py:451`; 4 values live |
| `global` screen | S1 → S2 pass-through | `mic_for` returns the code | e.g. `SIX`, `HKEX`, `BCS` in the persisted `screen_rows` |
| Depth store | **S3**, then S5/S6 | source index = priority, lower index is unbeatable (`depth.py:204`), SEC is index 0 (`depth.py:1246-1250`), `enrich` is appended last (`:1347-1350`) | **4,209 of 4,217 depth rows are byte-identical to the SEC snapshot's `exchange`; 0 disagreements.** The 8 remaining rows have no SEC record: 6 Yahoo codes + 2 `NYSE` |
| Engine / `engine-snapshot.json` | S3 (raw) and S4 (mic) side by side | `_record()` sets both from one string (`sec.py:221-222`) | `TXSE` + `mic: null` for 12 records |
| Declared policy (S8) | — | says Yahoo is primary | **contradicted** by the code and by the data (§7 D3) |

The "which source wins" question therefore has **two different answers**, and that is the root of the
whole problem: on a screen the provider wins; in the depth store EDGAR wins; and the two answers are
never reconciled or labelled.

---

## 3. The nine TXSE rows, exactly

### 3.1 Accounting: 12 EDGAR records → 11 screen rows → 9 depth rows → 9 Finviz tiles

| Artifact | Count | Symbols |
|---|---|---|
| SEC snapshot, `exchange == "TXSE"` | **12** | CECO, DDS, **DDT**, ET, **ET-PI**, IBOC, OBK, SUN, SUNC, TCBI, **TCBI-PB**, USAC |
| `/screens/select`, rows whose symbol is one of those | **11** | the 9 commons + `ET/PI` + `TCBI/PB` — all under **`XASE`** |
| `depth.json`, `exchange == "TXSE"` | **9** | the 9 commons |
| Finviz, `f=exch_txse` (measured live) | **9** | — |

Where the three missing ones went:

* **DDT** — present in the provider (`AMEX:DDT` resolves) but `market_cap_basic` is `null`, so
  `build_universe` drops it even at `min_market_cap=0` ("unknown is not a cap", `universe.py:432-438`).
  It is *not* a dedupe merge: `issuer_key("Dillards Capital Trust I 7 1/2 % …", None)` ≠
  `issuer_key("Dillard's, Inc. Class A", 10015816954)` (computed with the real function).
* **ET-PI / TCBI-PB** — in the screen universe as `ET/PI` and `TCBI/PB` (TradingView's slash
  spelling), but the depth store's universe is a frozen file with no preferreds at all
  (`depth.json` contains **zero** keys with `/`).

### 3.2 Why the depth store says TXSE — the writer, found

`app/market/depth.py:1206-1207`:

```python
if record.exchange:
    fields["exchange"] = record.exchange
```

That is the only line in the depth layer that writes a venue, and it copies the **raw** `SecurityRecord.exchange`
— no `mic_for`, no upper-casing, no mapping. The record comes from the engine snapshot, whose value is
what EDGAR's `company_tickers_exchange.json` printed (`sec.py:393` → `_record` at `:200-222` →
`store.py:111`).

The proof that this line, and not Yahoo, is the writer:

* the collector's own input for `DDS` says **`AMEX`** (`/home/ubuntu/briefs/market-engine/research/probes-1000/universe.json`,
  the file named by `MEE_DEPTH_UNIVERSE_PATH`), and the store still says `TXSE`;
* across the **whole store**, 4,209 of 4,217 rows have an SEC record and **all 4,209 are byte-identical**
  to it — including the 164 where EDGAR and TradingView disagree. Zero Yahoo values survive where SEC speaks;
* the 8 rows with no SEC record are exactly the 8 that carry Yahoo codes
  (`NasdaqGM`×4, `NasdaqCM`, `NasdaqGS`, `NYSE`×2) — `app/market/yahoo_batch.py:132` maps
  `fullExchangeName → exchange`, and `depth.py:1347-1350` wires that source in last.

`exchange_mic("TXSE")` returns `None` (`sec.py:99-101` — "the venue is too new to have a verified MIC"),
so the snapshot keeps `exchange="TXSE", mic=None`. That behaviour is pinned by
`tests/test_engine_identity.py:210-225`.

### 3.3 Why the screener says XASE — TradingView has no TXSE venue

Measured against `scanner.tradingview.com/america/scan` today:

| Probe | Result |
|---|---|
| `{"left":"exchange","operation":"equal","right":"TXSE"}` over the whole US scan | **`totalCount: 0`** |
| `AMEX:DDS` | resolves (`"AMEX:DDS"`) |
| `NYSE:DDS`, `NASDAQ:CECO`, `NYSE:ET`, `NASDAQ:IBOC` | **do not exist** |
| `TXSE:DDS` | **does not exist** |

The entire provider venue vocabulary for the US market is `{NASDAQ, NYSE, AMEX, CBOE, OTC}`
(`BATS`, `IEX`, `PNK`, `TXSE` all return 0). The nine TXSE listings are inside the **AMEX** bucket, and
`mic_for("AMEX") = "XASE"`. So `XASE` is not a wrong mapping of `TXSE` — it is a correct mapping of a
provider code that is itself mislabelled for these nine tickers.

Corroboration that TXSE is the real venue, not an EDGAR artefact: Finviz's own screener, queried live
with `f=exch_txse`, returns exactly **9** rows, and its five venue buckets sum to its whole stock
universe (3,438 + 1,927 + 245 + 1 + 9 = 5,620 = the unfiltered total).

### 3.4 Why the two never meet

The overlay is **fill-missing-only**: `_fillable` returns `True` only when the key is absent, or
present-but-`None` for the eight `SCAN_NULL_CELLS` (`depth_overlay.py:171-183`). `exchange` is not in
`SCAN_NULL_CELLS` and is always present after `build_universe`, so the store's `TXSE` can never reach
a screen row. That is a deliberate design ("presence is the test, not truthiness") and it is exactly
why the disagreement is invisible rather than resolved.

### 3.5 Verdict and the residual risk

* Both values are **honest about their own source**: `XASE` = "TradingView's venue code, MIC-mapped";
  `TXSE` = "EDGAR's venue string, unmapped".
* Neither is **correct as a statement about the listing**. Two independent sources place these nine on
  TXSE — EDGAR (`company_tickers_exchange.json`) and Finviz (`f=exch_txse` → 9) — while TradingView
  has no TXSE venue at all. The inference that TradingView mislabels the venue (rather than that
  EDGAR and Finviz are both wrong) rests on those two agreeing and on the provider's vocabulary being
  the odd one out; it is an inference, labelled as one. It also makes `instrument_id` read
  `XASE:ET` for a $71.99bn issuer.
* The failure mode is structural, not accidental: a venue TradingView does not model is absorbed into
  another venue's code and then **survives `is_us` only by that accident**. Had TradingView emitted
  `TXSE` itself, `mic_for` would pass it through and `is_us` would drop all nine rows from the US tab
  entirely (§8(a), reproducer in §7 D3).

---

## 4. Disagreements, counted on the full sets (no sampling)

Sets compared: the full live universe (5,576 rows, walked page by page with `columns:["exchange","symbol","name"]`),
the full depth store (4,217 entries), the full engine snapshot (10,433 securities). Symbols were
normalised `[./] → -` before joining (TradingView writes `BRK.B`/`ET/PI`, the store writes `BRK-B`).

### 4.1 Depth store vs screener

| Comparison | Rows compared | Agree | Disagree | What it is |
|---|---|---|---|---|
| raw string equality | 4,187 | **0** | 4,187 | different vocabularies by construction; not a conflict |
| `sec.exchange_mic(depth.exchange)` vs `screen.exchange` | 4,187 | 4,008 | **164 + 15 unmappable** | the real disagreement set |
| `snapshot.mic` vs `screen.exchange` (4,179 of the 4,187 have a SEC record) | 4,179 | 4,006 | **164**, plus **9** with `mic=None` | same rows, independent column |
| `depth.exchange` vs `snapshot.exchange` | 4,209 | **4,209** | **0** | the store is a verbatim copy of EDGAR |

The 164 are **all one direction**: depth/SEC says `NYSE` (`mic XNYS`), the screener says `XASE`.
Examples (live rows): `ACCS, ACU, AEON, AGIG, AIB, AIM …` — the full list is reproducible with the
script in §7 D2.

Cause, measured rather than assumed: across **10,433** SEC records the venue vocabulary is exactly
`{Nasdaq, NYSE, OTC, CBOE, TXSE, null}` — **there is no `AMEX` / `NYSE American` / `NYSE MKT` value at
all**, even though `sec.py:110-112` maps all three. So EDGAR's `NYSE` cannot distinguish XNYS from
XASE, and the 258 AMEX rows in the screener can never be confirmed from the identity source. This is a
**hard limit of S3**, not a bug in our mapping — but it means `exchange` has one venue (AMEX, 258 rows)
whose identity is unverifiable from the official spine.

### 4.2 Per-venue counts, four sources (each with its field named)

| Venue | TradingView scan `exchange`, stock **all subtypes**, OTC excluded | `POST /screens/select` row `exchange`, `region=us` (post-dedupe) | SEC `company_tickers_exchange.exchange` (10,433 records) | Finviz `f=exch_*` (measured) |
|---|---|---|---|---|
| NASDAQ | 3,342 | **3,308** (`XNAS`) | 4,357 (`Nasdaq`) | 3,438 |
| NYSE | 2,106 | **2,009** (`XNYS`) | 3,270 (`NYSE`) | 1,927 |
| AMEX | 266 | **258** (`XASE`) | **0** | 245 |
| CBOE | 1 | **1** (`XCBO`) | 44 (`CBOE`) | 1 |
| TXSE | **0** | **0** | **12** | 9 |
| none/null | 0 | 0 | 171 | 0 |
| **total** | **5,715** | **5,576** | **10,433** | **5,620** |

How to read the deltas (each measured, not inferred):

* 5,715 → 5,576 = **−139**: issuer dedupe inside `build_universe` (`issuer_key` + `_preferred`,
  `universe.py:309-338,521-533`). Per venue: NASDAQ −34, NYSE −97, AMEX −8, CBOE 0.
* 5,620 (Finviz) vs 5,715 (TradingView) = **+95 for TradingView**, a different universe; not
  decomposed in this pass. Finviz's five buckets partition its own total exactly, so its `exch` field
  is total for its universe, as ours is for ours.
* SEC's 10,433 is a **registrant** universe, not a listing universe: 2,579 OTC + 171 null + one record
  per class (BRK-A/BRK-B are two) — hence larger than either screener.

### 4.3 Symbol-join integrity (why 1,389 rows never meet the depth store)

| Measure | Count |
|---|---|
| screener symbols (normalised) | 5,576 |
| depth symbols | 4,217 |
| intersection | **4,187** |
| depth-only | **30** |
| screener-only | **1,389** |

* The 30 depth-only rows: **23 are provably the losing line of a dual-class issuer** — the other
  class is in the live universe (`BRK-A`/`BRK.B`, `GOOG`/`GOOGL`, `FOX`/`FOXA`, `NWS`/`NWSA`,
  `UA`/`UAA`, `ZG`/`Z`, `LEN-B`/`LEN`, `HEI-A`/`HEI`, `GTN-A`/`GTN`, `LILA`/`LILAK`, `MKC-V`/`MKC`,
  `TAP-A`/`TAP`, `BF-A`/`BF.B`, `CRD-B`/`CRD.A`, `MOG-B`/`MOG.A`, `BH`/`BH.A`, `BELFA`/`BELFB`,
  `DGICB`/`DGICA`, `RUSHB`/`RUSHA`, `UONE`/`UONEK`, `WLYB`/`WLY`, `METCB`/`METC`, `CENT`/`CENTA` —
  each sibling verified present). The remaining **7** (`ADIL`, `DYNC`, `GCTK`, `LNAI`, `NCO`, `RDI`,
  `WHLR`) have no counterpart in the live universe at all: symbols the provider has since dropped.
* The 1,389 screener-only rows are the direct consequence of §5.3: the depth collector's universe is a
  **frozen file**, not the live universe.
* 289 screener symbols use `/` for preferreds (`ET/PI`, `JPM/PC` …). The overlay's spelling table
  (`depth_overlay.py:115-128`) only swaps `.` ↔ `-`, so those can never join. Today the loss is **0**
  because the store holds no `/` keys at all — a latent gap, not a current miss.

---

## 5. Coverage: what is in scope by policy, what is excluded, what it costs

### 5.1 The policy, as implemented (two gates, both unconditional)

| Gate | Citation | Effect |
|---|---|---|
| provider-side | `app/screen/scanner.py:283` | `exchange not_in_range ["OTC","PNK"]` on every scan, every region, every asset class |
| universe-side | `app/screen/universe.py:141-143,422-424` | `OTC_EXCHANGES = {OTC, OTCBB, OTCQX, OTCQB, PNK, PINK, GREY, OTCMKTS}`; empty code also dropped |

In scope by construction: the four codes the provider emits for the US market (`NASDAQ/NYSE/AMEX/CBOE`),
because `US_EXCHANGES` is derived from `MIC_BY_EXCHANGE.values()` (`universe.py:139`).
Not in scope by decision: OTC/pink (PLAN-screen.md §2, quoted in the module docstring at
`universe.py:21`). In scope by *intent* but unreachable in fact: **TXSE** — Finviz has the bucket, EDGAR
has the venue, TradingView has no code for it.

Only `OTC` ever occurs: over 11,723 stock rows and 6,116 fund rows the provider emitted no `PNK`,
`OTCQX`, `OTCQB` or `GREY`. Five of the eight spellings in `OTC_EXCHANGES` are defensive.

### 5.2 The measured cost

| Scan | Rows without the gate | Rows with the gate | Dropped | Share |
|---|---|---|---|---|
| `type=stock` (all subtypes) | 11,723 | 5,715 | **6,008** | 51.2 % |
| `type=stock, subtype=common` | 11,038 | 5,193 | **5,845** | 52.9 % |
| `type=fund, subtype=etf` | 6,116 | 5,776 | **340** | 5.6 % |

Product-side totals after the gate and dedupe: `us` = 5,576, `us_etf` = 5,776. For comparison, the
identity spine still *holds* 2,579 OTC registrants (`engine-snapshot.json`), so the exclusion is a
product-scope decision, not a data-availability one — `POST /engine/screen` can still filter them.

### 5.3 The cost nobody priced: depth coverage is pinned to a 4-day-old file

`MEE_DEPTH_UNIVERSE_PATH=/home/ubuntu/briefs/market-engine/research/probes-1000/universe.json`
(`/home/ubuntu/ztockz-data/engine.env:14`, read at `app/main.py:536`). That file holds **4,217 rows**
fetched `2026-10-27T14:53:50Z`; the live universe is **5,576**. Consequences, all measured:

* **1,389 live symbols (24.9 %) have no depth row at all** — no fundamentals overlay, no technicals,
  no `exchange`, nothing from `GET /market/depth`.
* the store's venue distribution is that file's distribution (Nasdaq 2,550 / NYSE 1,652 / TXSE 9 /
  Yahoo codes 6), not today's.
* the 9 TXSE rows exist in depth only because those nine were in the 27 October file.

---

## 6. Every test that pins exchange behaviour

26 test functions assert venue semantics (16 on the screen path, 10 on the engine/SEC/depth path).
"Label" = would the test fail if `row["exchange"]` became a friendly name (`"NYSE American"`)?

### 6.1 Screen path (the ones that pin the MIC contract)

| Test | File:line | Pins | On a label |
|---|---|---|---|
| `test_cross_listed_issuer_collapses_to_one_primary_row` | `tests/test_screen_universe.py:86` (assert :94) | built row `== "XNAS"` | **breaks** |
| `test_us_tab_contains_us_listings_only` | `tests/test_screen_universe.py:127` (assert :129) | `exchange in S.US_EXCHANGES` | **breaks** (`foreign != []`) |
| `test_global_tab_is_ex_us_and_drops_issuers_the_us_tab_owns` | `tests/test_screen_universe.py:146` (:158-159) | global ∩ US_EXCHANGES = ∅ | **passes vacuously** — stops testing anything |
| `test_global_keeps_the_most_liquid_listing_of_a_non_us_issuer` | `tests/test_screen_universe.py:162` (:166) | unmapped code kept verbatim (`"SIX"`) | passes |
| `test_otc_listing_is_excluded_even_when_it_is_large` | `tests/test_screen_universe.py:73` | OTC gate on the provider code | passes |
| `test_cboe_listed_etf_is_us_not_dropped` | `tests/test_screen_etf.py:117` (:135) | ETF on CBOE → `"XCBO"` and kept | **breaks** |
| `test_etf_universe_total_matches_the_live_probe` | `tests/test_screen_etf.py:232` (:243) | `{exchange} <= S.US_EXCHANGES` | **breaks** (and see D10: the fixture set has 3 codes, the app has 4) |
| `test_global_tab_serves_ex_us_issuers_only` | `tests/test_screen_api.py:182` (:195) | global ∩ US_EXCHANGES = ∅ | **passes vacuously** |
| `test_text_operators_compare_identifiers_exactly_and_search_substrings` | `tests/test_screen_filters.py:284` (:285-286) | `eq XNAS`, `in (XNYS,)` on literal rows | passes (pins the filter, not the mapping) |
| `test_string_dsl_supports_in_between_and_contains` | `tests/test_screen_filters.py:328` (:329) | DSL parses `exchange in (XNAS, XNYS)` | passes (parse only) |
| `test_only_sourced_metrics_carry_a_row_field` | `tests/test_screen_filters.py:164` (:170) | `"exch" → "exchange"` | passes (key mapping) |
| `test_the_mapped_keys_are_the_columns_a_row_really_carries` | `tests/test_screen_selection.py:148` (:150) | `"exch": "exchange"` | passes |
| `test_filters_parse_into_in_and_between_tuples` | `tests/test_screen_selection.py:573` (:580) | `field == "exchange"` | passes |
| `test_the_default_row_is_unchanged_without_columns` | `tests/test_screen_select_columns.py:59` (:67) | `exchange` is a projected column | passes (fixture value) |
| `test_yahoo_symbols_are_bare_for_us_and_suffixed_for_known_venues` | `tests/test_screen_multi_source.py:660` (:661-662) | `yahoo_symbol` accepts `XNAS` **and** `NASDAQ` | passes on literals; a label would silently return `None` |
| `test_google_symbols_carry_the_us_venue_suffix` | `tests/test_screen_multi_source_verifiers.py:405` (:406-415) | `XASE → AMEX` in Google path | passes on literals; same blind spot |

### 6.2 Engine / SEC / depth path

| Test | File:line | Pins |
|---|---|---|
| `test_parse_company_tickers_exchange_keeps_a_venue_without_a_verified_mic` | `tests/test_engine_identity.py:210` (:223,225) | `exchange == "TXSE"`, `mic is None` |
| `test_exchange_mic_adds_the_r3_venues_without_touching_the_known_ones` | `tests/test_engine_identity.py:240` (:232-236) | `TXSE → None`, `CBOE → XCBO`, `OTC → OTCM` |
| `test_load_takes_the_universe_from_one_exchange_document` | `tests/test_engine_identity.py:332` (:360-361) | bulk doc is the identity source (`Nasdaq → XNAS`) |
| `test_exchange_mic_maps_only_venues_it_can_name` | `tests/test_engine_security.py:151` (:136-143) | 14-row venue→MIC table incl. `NYSE American → XASE` |
| `test_parse_submissions_pairs_each_ticker_with_its_exchange` | `tests/test_engine_security.py:245` (:255-257) | parallel arrays paired by index |
| `test_parse_submissions_leaves_a_missing_exchange_unknown` | `tests/test_engine_security.py:263` | missing element → `None`, never a sibling's |
| `test_master_refuses_to_overwrite_a_conflicting_venue` | `tests/test_engine_security.py:502` (:510-522) | venue conflict is refused |
| `test_a_venue_disagreement_is_a_conflict_that_leaves_the_row_intact` | `tests/test_engine_enrich.py:465` (:471-493) | disagreement recorded, value not replaced |
| `test_sec_depth_source_reads_identity_and_classifies_sector` | `tests/test_market_depth.py:279` (:307) | `SecDepthSource` publishes `exchange` verbatim (`"Nasdaq"`) |
| `test_full_exchange_name_becomes_exchange` | `tests/test_market_yahoo_batch.py:273` (:286-287) | Yahoo `fullExchangeName` → `exchange` (`"NasdaqGS"`) |

### 6.3 What is **not** tested (the gaps that matter)

| Gap | Why it matters |
|---|---|
| `mic_for` / `MIC_BY_EXCHANGE` — **zero direct references** in `tests/` (`grep -rn "MIC_BY_EXCHANGE\|mic_for" tests/*.py` → empty) | the four-entry table and the pass-through branch have no unit test |
| the pass-through + `is_us` interaction | only exercised by `"SIX"` in the Global tab; no test covers an unmapped code in the US tab (`build_universe([... exchange="TXSE"], region="us")` → `[]`, untested) |
| the live venue vocabulary | no test asserts that the four values are the *only* values in the live universe; a live count is not a test |
| `depth_overlay` × `exchange` | `tests/test_screen_depth_overlay.py` never mentions `exchange`; nothing pins "the store cannot overwrite the row's venue" |
| the 6 Yahoo-coded rows | no test covers a symbol with no SEC record whose only venue is Yahoo's product code |
| the `exch` option list vs the captured Finviz panel | no test compares `filters.py:646` to `research/finviz-ui-raw/views/v111-filters.json` |

**Answer to "what breaks if the column became a friendly label":** the three hard breaks are
`test_screen_universe.py:94`, `test_screen_etf.py:135` and `test_screen_universe.py:129`, plus
`test_screen_etf.py:243`; two more (`test_screen_universe.py:158`, `test_screen_api.py:195`) would
**pass for the wrong reason** and quietly stop asserting anything. Everything on the engine side
(`§6.2`) is unaffected, because the engine column is a different field. The UI document already
decided the label belongs client-side (`research/exchange-ui.md` §3), which breaks none of these —
that is the cheaper path and the one this document does not contradict.

---

## 7. Defects

Severity: **S1** wrong answer a user can see; **S2** wrong answer an operator can see; **S3** latent /
integrity; **S4** documentation.

### D1 — One ticker, two venues, no arbitration and no marker (S1)

`/screens/select` says `XASE`; `/market/depth` and `/engine/security` say `TXSE`; nothing in either
payload says which source the value came from.

```bash
curl -s -X POST localhost:8099/screens/select -H 'content-type: application/json' \
  -d '{"mode":"criteria","region":"us","criteria":{"limit":5,"filters":[
       {"field":"exchange","op":"eq","value":"XASE"}],"sort":"market_cap"},
       "columns":["exchange","symbol","name","market_cap"]}'
# -> ET  XASE  Energy Transfer LP  71999393286   (first row)
curl -s 'localhost:8099/market/depth?symbols=ET'   # -> "exchange":"TXSE"
curl -s 'localhost:8099/engine/security/ET'        # -> "exchange":"TXSE","mic":null
```

Evidence: `universe.py:446,451` vs `depth.py:1206-1207` vs `service.py:507`.

### D2 — The depth store's `exchange` mixes three vocabularies, and its declared owner is wrong (S2)

`depth.json` today: `Nasdaq 2,550 · NYSE 1,652 · TXSE 9 · NasdaqGM 4 · NasdaqCM 1 · NasdaqGS 1`.
`NasdaqGM` is not a venue name and not a MIC; it is Yahoo's product code, written by
`yahoo_batch.py:132`. The catalog nevertheless declares `"exchange": (OWNER_YAHOO, "exact")`
(`depth_fields.py:101`) — i.e. one owner, one vocabulary, for a column that has two writers and three
spellings.

```python
# .venv/bin/python
import json; from collections import Counter
d=json.load(open('/home/ubuntu/ztockz-data/depth.json'))['symbols']
print(Counter(e.get('exchange') for e in d.values()).most_common())
```

Full disagreement list (164 rows), self-contained — dumps the universe itself, then joins:

```python
import json, re, urllib.request
from app.engine.sources.sec import exchange_mic
URL="http://127.0.0.1:8099/screens/select"; rows=[]; off=0
while True:
    body={"mode":"criteria","region":"us","criteria":{"limit":100,"offset":off,"filters":[],
          "sort":"market_cap"},"columns":["exchange","symbol"]}
    req=urllib.request.Request(URL,data=json.dumps(body).encode(),
                               headers={"content-type":"application/json"})
    d=json.load(urllib.request.urlopen(req,timeout=180)); rows+=d["rows"]
    if not d["rows"]: break
    off+=len(d["rows"])
norm=lambda s: re.sub(r"[./]","-",str(s or "").strip().upper())
scr={norm(r["symbol"]):r["exchange"] for r in rows}
dep={norm(k):v for k,v in json.load(open("/home/ubuntu/ztockz-data/depth.json"))["symbols"].items()}
dis=[(k,dep[k].get("exchange"),scr[k]) for k in set(scr)&set(dep)
     if exchange_mic(dep[k].get("exchange")) not in (None,scr[k])]
print(len(dis)); print(sorted(dis)[:10])
# -> 164  [('ACCS','NYSE','XASE'), ('ACU','NYSE','XASE'), ...]
```

### D3 — The declared precedence is the opposite of the implemented one (S2)

`reference/source_graph.json:2118-2143` and `GET /sources/fields/exchange` say
`primary: yahoo · fallbacks: nasdaq, sec_edgar · "exchange short code from the venue master" ·
crosscheck: tradingview`. The code puts **SEC first and makes its value unbeatable**
(`depth.py:1246-1250` + `:204`), and it never compares TradingView at all — the crosscheck edge exists
only in the JSON. Measured consequence: 4,209/4,209 rows carry EDGAR's spelling, 0 carry Yahoo's, and
the TradingView disagreement (9 TXSE) is never surfaced as a crosscheck verdict.

### D4 — Pass-through + `is_us` silently drops any venue the MIC table does not know (S1, latent today)

```python
# .venv/bin/python
from app.screen.universe import build_universe, mic_for
row=lambda ex:{"symbol":"T","description":"T Co","exchange":ex,"country":"United States",
  "type":"stock","subtype":"common","currency":"USD","close":10.0,"change":1.0,
  "volume":1000,"Value.Traded":10000.0,"market_cap_basic":5e9}
for ex in ("TXSE","BATS","NASDAQ","AMEX"):
    print(ex, mic_for(ex), [r["exchange"] for r in build_universe([row(ex)], region="us")])
# TXSE TXSE []
# BATS BATS []
# NASDAQ XNAS ['XNAS']
# AMEX XASE ['XASE']
```

`mic_for` returns the code, `US_EXCHANGES` does not contain it, `is_us` is `False`, the row vanishes
(`universe.py:302-306,139,619,626`). The in-repo precedent is the CBOE comment at
`universe.py:134-137`: "leaving it unmapped made the ETF universe silently drop ~1.7k live rows".
TXSE is the same shape and is only masked because the provider calls those nine `AMEX`.

### D5 — The `exch` filter cannot express TXSE, and its stored options match no row value (S2)

`filters.py:640-647` stores `("", "amex", "cboe", "nasd", "nyse", "modal")`. The captured Finviz panel
in this repo has **seven** options, including `{"value":"txse","label":"TXSE"}`
(`research/finviz-ui-raw/views/v111-filters.json:37-39`). Two independent faults: the token list is one
short of the panel it claims to copy ("metadata verbatim", `filters.py:634`), and every token in it is
Finviz vocabulary while the row carries MICs.

```bash
curl -s -X POST localhost:8099/screens/select -H 'content-type: application/json' \
 -d '{"mode":"criteria","region":"us","criteria":{"limit":1,"filters":[{"key":"exch","op":"eq","value":"amex"}]}}'
# -> total 0
# same with "value":"XASE" -> total 258
```

This is the value-vocabulary gap `research/exchange-contract.md` §1.3 already documents for `NYSE`;
the new fact here is that its proposed alias table, if fed from `filters.py:646`, would have **no
`txse` entry even once implemented**.

### D6 — `instrument_id` claims `<MIC>:<symbol>` and is not one (S3)

`app/models.py:104`: "`instrument_id` (`<MIC>:<symbol>`) is the dedupe key". Built at
`app/screen/snapshot.py:153` as `f"{row['exchange']}:{row['symbol']}"`. The persisted table proves the
violation: 1,900 rows, 17 distinct codes, of which 13 are provider codes
(`SSE 87, SIX 45, LSE 45, KRX 36, HKEX 33, TWSE 18, TSE 18, XETR 15, TSX 15, TADAWUL 15, SZSE 15,
EURONEXT 15, BCS 3`), and `DDS` would be `XASE:DDS` while the engine's own identity for the same
ticker is `TXSE`.

```bash
sqlite3 /home/ubuntu/ztockz/ztockz.db \
 "select exchange,count(*) from screen_rows group by 1 order by 2 desc"
```

### D7 — Depth coverage is pinned to a frozen universe file (S2)

See §5.3. `engine.env:14` → 4,217 rows @ 2026-10-27; live 5,576; **1,389 symbols (24.9 %) with no
depth row**; 23 of the 30 depth-only rows are the losing class of a dual-class issuer, so the screener
serves `BRK.B` while the store holds `BRK-A` — the same instrument under two keys. Reproducer: the join
in §4.3 (write both sets to JSON, intersect in Python — not a shell variable).

### D8 — `XCBO` is missing from the verifier/export venue sets (S3)

`multi_source.py:182` (`US_VENUES`) and `app/screen/symbols.py:53` omit `XCBO`, while
`universe.py:139` includes it. The single CBOE row (`symbol: "CBOE"`, `exchange: "XCBO"`, live) is
therefore skipped by every verification leg (`multi_source.py:445,774`) and by
`GOOGLE_EXCHANGE_BY_VENUE` (`:203-215`).

```bash
curl -s -X POST localhost:8099/screens/select -H 'content-type: application/json' \
 -d '{"mode":"criteria","region":"us","criteria":{"limit":5,"filters":[{"field":"exchange","op":"eq","value":"XCBO"}]},"columns":["exchange","symbol"]}'
# -> [{"symbol":"CBOE","exchange":"XCBO", ...}]
```

### D9 — The scanner's second venue field is parsed and then dropped (S4)

`scanner.py:333` writes `row["venue"]` from the ticker prefix (`"AMEX:DDS"`), and no screen-path
reader uses it. Measured: over 5,715 stock rows the prefix and the `exchange` column agree **100 %** —
so it is a free crosscheck that is currently thrown away. Either use it as an assertion or delete it;
keeping a second, unchecked copy of the same fact invites exactly the drift seen in `D2`.

### D10 — Test fixtures drift from the code they test (S3)

* `tests/_screen.py:84` — `US_EXCHANGES = frozenset({"XNYS","XNAS","XASE"})`, three codes; the app's
  `US_EXCHANGES` (`universe.py:139`) has **four** (`XCBO`). `test_screen_universe.py:129` and
  `test_screen_api.py:195` compare against the fixture, so an ETF on CBOE would fail them even though
  the code is right.
* `tests/test_screen_etf.py:232-243` — pins `ETF_UNIVERSE_TOTAL == 6_123` "the live probe count"; the
  live ETF universe is **5,776** today (§4.2). The fixture is self-consistent, so the test passes
  while its stated justification is stale.

### D11 — `IdentityIndex` documents a column it does not publish (S4)

`app/market/identity.py:10-11` says the engine snapshot "carries ... its own exchange and country", but
`FIELDS` (`:39-46`) publishes `ticker, company, sector, industry, country, security_type` — no
`exchange`, no `mic`. A caller of the identity join cannot get the venue, which is why surfaces ask
the snapshot directly and end up with two answer shapes.

---

## 8. Adversarial answers

### (a) Can the pass-through hide a whole venue? Not today — but only because the provider hides it first

Proved by exhaustion, not by sampling:

1. The live `us` universe has exactly **four** distinct `exchange` values, and their `eq` counts sum
   to `universe_total`: 3,308 + 2,009 + 258 + 1 = 5,576. There is no null and no fifth code.
2. The live `us_etf` universe has exactly **four**: `XASE 2,739 · XCBO 1,677 · XNAS 1,282 · XNYS 78`
   = 5,776.
3. The provider's own vocabulary, scanned unfiltered, is `{NASDAQ, NYSE, AMEX, CBOE, OTC}` for stocks
   (11,723 rows) and `{AMEX, CBOE, NASDAQ, NYSE, OTC}` for ETFs (6,116 rows). `TXSE`, `BATS`, `IEX`
   and `PNK` each return `totalCount 0` from `scanner.tradingview.com/america/scan`.

So `mic_for`'s pass-through branch never fires on either US tab today. It *is* the mechanism by which
a venue would disappear (reproducer in D4), and TXSE is the near-miss: 11 real rows, a real venue,
served under another venue's code. The pass-through did not hide TXSE — the provider did — but the
pass-through is what would turn "provider emits a new code" into "venue silently missing" with no
error, no log line and no test.

### (b) Do the depth store and the screener disagree on more than 9 symbols? Yes: 179, not 9

Full sets, not samples: 5,576 × 4,217 symbols, normalised `[./]→-`, joined on symbol → 4,187 common.

* **164** rows where EDGAR/depth says `NYSE` and the screener says `XASE` (§4.1);
* **9** rows where depth says `TXSE` and the screener says `XASE` (the question's nine);
* **6** rows where depth carries a Yahoo product code (`NasdaqGM/CM/GS`) and the screener says `XNAS`
  — unmappable rather than contradictory, but still two different strings for one instrument;
* **4,008** rows agree once the vocabulary is normalised; raw string equality agrees on **0**.

Total rows with a non-identical venue after normalisation: **179** (164 + 9 + 6). The 9 in the brief
are the visible tip; the 164 NYSE/AMEX rows are the larger, quieter half, and they have a different
cause (EDGAR cannot express AMEX, §4.1).

### (c) Never "the NYSE count"

Every count in this document names its source and field: "TradingView scan `exchange`, stock all
subtypes, OTC excluded = 2,106"; "`POST /screens/select` row `exchange`, region `us`, post-dedupe =
2,009"; "SEC `company_tickers_exchange.exchange` = 3,270"; "Finviz `f=exch_nyse` = 1,927". The four
numbers are all "NYSE" and none of them is interchangeable.

---

## 9. What I could not prove, and what would settle it

| Open question | Why it is open | How to settle it |
|---|---|---|
| Is `NYSE American` ever emitted by EDGAR in the *bulk* document? | the repo holds only the derived snapshot (0 AMEX values in 10,433 records); `data.sec.gov` returns `["NYSE"]` for ACU's submissions too | fetch `company_tickers_exchange.json` once and count `exchange` values; if it has no AMEX, the 164 are unfixable from S3 |
| Which of the 164 are genuinely AMEX vs uplisted to NYSE | needs a per-symbol official source (exchange rulebook / listing notice) | compare against `nasdaq.com` listing metadata or an exchange-published symbol directory |
| The 95-row TradingView-vs-Finviz delta | two different universes; not decomposed | per-symbol diff of both full lists, which needs an authenticated Finviz export |
| Whether the 2 Yahoo-`NYSE` rows (ASBH, MSGK) are really NYSE | no SEC record for them at all | query the venue's own symbol directory |

---

## Appendix — provenance of every number in this document

| Number | Where it came from |
|---|---|
| 5,576 · XNAS 3,308 · XNYS 2,009 · XASE 258 · XCBO 1 | `POST /screens/select`, `region=us`, full walk of `columns:["exchange","symbol"]`, 56 pages, 2026-10-31 |
| 5,776 · XASE 2,739 · XCBO 1,677 · XNAS 1,282 · XNYS 78 | same, `region=us_etf` |
| 11,723 / 5,715 / 5,193 / 6,008 / 5,845 | `scanner.tradingview.com/america/scan`, `type=stock`, with and without `exchange not_in_range ["OTC","PNK"]`, `range [0,20000]` |
| 6,116 / 5,776 / 340 | same, `type=fund` + `subtype=etf` |
| 10,433 · Nasdaq 4,357 · NYSE 3,270 · OTC 2,579 · CBOE 44 · TXSE 12 · null 171 | `/home/ubuntu/ztockz-data/engine-snapshot.json`, `dataset.securities[*].exchange` |
| XNAS 4,357 · XNYS 3,270 · OTCM 2,579 · XCBO 44 · null 183 | same file, `[*].mic` |
| 4,217 · Nasdaq 2,550 · NYSE 1,652 · TXSE 9 · NasdaqGM 4 · NasdaqCM 1 · NasdaqGS 1 | `/home/ubuntu/ztockz-data/depth.json`, `symbols[*].exchange` |
| 4,187 · 30 · 1,389 · 4,008 · 164 · 9 · 6 | symbol-normalised set operations between the two files above and the live universe dump |
| 3,438 · 1,927 · 245 · 1 · 9 · 5,620 | `finviz.com/screener.ashx?v=111&f=exch_{nasd,nyse,amex,cboe,txse}` and the unfiltered page, fetched live |
| 1,900 rows · 17 codes · 95 snapshots | `sqlite3 /home/ubuntu/ztockz/ztockz.db`, `screen_rows` |
