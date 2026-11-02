# coinids.json — manifest-driven fetch and the signal table

Status: plan (2026-11-02). Owner: Zaaaxx. Builder: subagent.

## Goal
Read a server-side manifest of instruments and answer one table containing every
signal we can actually compute, so "what to fetch" is configuration, not code.

## Why
Today fetching is universe-driven: the scan returns ~5.6k US stocks and everything
else is derived from that. A manifest answers the opposite question — "fetch
exactly these, including symbols outside the scanned universe" — and gives batch
jobs, exports and (later) option contracts one place to live.

`CoinIDs { id1: "AAPL", id2: "AMZN" }` from the owner's document is this manifest.

## Schema (versioned, forward-compatible)
```json
{
  "version": 1,
  "updated_at": "2026-11-02T00:00:00Z",
  "instruments": [
    {"id": "id1", "symbol": "AAPL", "asset": "stock"},
    {"id": "id2", "symbol": "AMZN", "asset": "stock"},
    {"id": "id3", "symbol": "AAPL", "asset": "option",
     "expiry": "2026-01-16", "strike": 200, "right": "C"}
  ]
}
```
Rules: `symbol` is the identity (not `id`); `id` is an optional human label and
must be unique when present; duplicate (symbol, asset, expiry, strike, right) is
a 422; `asset` defaults to `stock`; option rows require expiry+strike+right.
Reading the legacy flat form `{"id1":"AAPL","id2":"AMZN"}` is allowed and
normalised into this shape, because the owner's document uses it.

## Location and ownership
`/home/ubuntu/ztockz-data/coinid.json` (override with `MEE_COINIDS_PATH`).
Missing file = empty manifest, not an error. Owner edits it directly; no write
endpoint in this phase.

## API
- `GET /market/instruments` — the table, one row per instrument:
  id, symbol, asset, and every available signal:
  price, change_pct, volume, beta_1y, market_cap_usd, dividend_yield,
  recommendation, pe_ttm, pe_forward, rsi_14, sma_50, sma_200, ret_ytd,
  ret_1w, ret_1m, plus `null` + a block-level `unavailable[]` for what we cannot
  compute (greeks, expiry, days-to-maturity — no option-chain source yet).
- `GET /market/instruments/meta` — manifest status: path, version, updated_at,
  count, ids that are not in the master, ids with no price.
- Query `?ids=id1,id2` narrows the answer; unknown id is 422 `unknown_instrument`.

## Distribution
One row per instrument, ordered by manifest order. Rows the scan cannot price
still appear with nulls and a reason — a manifest entry is a promise to show it,
not to invent it.

## TDD tasks
1. `app/market/coinids.py` — loader + normaliser + validator.
   RED: `test_coinids_reads_legacy_flat_form`, `test_coinids_rejects_duplicate`,
   `test_coinids_option_requires_contract_fields`, `test_missing_file_is_empty`.
2. `app/api/instruments.py` — the two routes, registered in `app/main.py`.
   RED: `test_instruments_table_answers_one_row_per_manifest_entry`,
   `test_unknown_id_is_422`, `test_unpriceable_symbol_is_null_with_reason`.
3. Signal assembly reuses existing readers (scan row, depth overlay, derived)
   — no second computation path.
   RED: `test_instruments_reuses_the_scan_row_for_price_and_beta`.
4. Contract sweep: schema artifact + response shape pinned.

## Out of scope (stated, not faked)
Options greeks, expiry, days-to-maturity, IBKR. Each appears as `null` with the
reason `no option-chain source yet` / `broker integration not configured`.

## Done means
`GET /market/instruments` returns one row per manifest entry with real values for
the 16 signals above, nulls carrying reasons for the rest, and the whole suite
green.
