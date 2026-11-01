# S&P 500: 503 snapshot members vs 500 live `idx:sp500` rows

**Answer: the three are GOOG, FOX and NWS — the second share class of three dual-class
issuers. They are not lost in the `idx` join; they are collapsed one step earlier by
`build_universe`'s issuer dedupe.**

## Table

| Symbol as stored | Provider spelling | Reason category |
|---|---|---|
| `GOOG` | `GOOG` (present, NASDAQ) | Dual-class / issuer dedupe — same `issuer_key` as `GOOGL` |
| `FOX` | `FOX` (present, NASDAQ) | Dual-class / issuer dedupe — same `issuer_key` as `FOXA` |
| `NWS` | `NWS` (present, NASDAQ) | Dual-class / issuer dedupe — same `issuer_key` as `NWSA` |

Evidence per pair (live scan, 2026-11-01). The provider sends **one description and one
total-company cap for both lines**, so `issuer_key(description, cap)` is identical:

| Pair | description (both) | `market_cap_basic` (both) | turnover | winner |
|---|---|---|---|---|
| GOOGL / GOOG | `Alphabet Inc.` | 3,411,530,562,225 | 18.38 B / 12.55 B | **GOOGL** |
| FOXA / FOX | `Fox Corporation` | 25,116,089,553 | 539.6 M / 53.1 M | **FOXA** |
| NWSA / NWS | `News Corporation` | 16,209,162,716 | 121.9 M / 39.6 M | **NWSA** |

`_preferred()` breaks the tie by turnover after venue priority, so the more liquid class
survives and the sibling is dropped — silently, because `total` counts rows, not members.

## Ruled out with evidence
- **Spelling (`BRK.B` vs `BRK-B`)**: not the cause. `index_membership._key` folds `-` and
  space to `.` both ways, and both dotted names survive live (`BRK.B` at rank 10, `BF.B` in the 500).
- **Absent from the provider scan**: no — all three carry `type=stock`, `subtype=common`, `exchange=NASDAQ`.
- **No cap / cap floor / subtype gate**: no — caps are 3.4 T, 25.1 B, 16.2 B, and the
  criteria route scans with `min_market_cap=None` + `subtype=None`, then builds with
  `min_market_cap=0` + `allow_any_subtype=True`.
- **Join bug**: no — `enrich_rows` labels the surviving siblings `["sp500"]`, and
  live-minus-snapshot is empty: the live 500 are a strict subset of the snapshot.

## Method

1. Read `app/screen/selection.py` (`select_rows`, `_selected`), `app/screen/index_membership.py`
   (`enrich_rows`, `_key`), and the route at `app/api/screens.py:1267-1326`.
2. Reproduced that pipeline in-process with the route's exact arguments:
   `fetch_scan("us", limit=100_000, columns=PARITY_COLUMNS, min_market_cap=None, subtype=None)`
   → 5,715 rows; `build_universe(page.rows, region="us", min_market_cap=0,
   columns=PARITY_COLUMNS, allow_any_subtype=True, asset_class="stock")` → 5,576 rows;
   `index_membership.enrich_rows`; `select_rows` over `idx:sp500` → **total 500**,
   `universe_total 5576`.
3. Enumerated all 500 through offset pages and diffed against
   `index_membership.load().indices["sp500"]` (503): `snapshot − live = {GOOG, FOX, NWS}`;
   `live − snapshot = ∅`.
4. Confirmed live once: `POST /screens/select` → `total: 500`, `universe_total: 5576`;
   `GET /screens/filters` → `membership.counts.sp500: 503` (the visible 503/500 mismatch).

## Verdict

The 500 is correct **for this product's one-row-per-issuer universe** but not for the index:
there is no join bug — the three are merged by the issuer dedupe in `build_universe`
(`app/screen/universe.py:309` `issuer_key`, applied at `:612-615`), which keys on
description + a $1M cap bucket and so cannot keep two share classes whose provider
description and total cap are identical, leaving `idx:sp500` three short of the index's
503 listed lines while `/screens/filters` still advertises 503.
