# Pinned upstream fixtures

Each file here is a **recorded shape** — the third-party payload that broke a
parser, or the minimal reproduction of it. A contract test asserts on these
bytes; no test in this directory ever calls the provider. Regenerate with:

```sh
.venv/bin/python tests/fixtures/upstream/build_fixtures.py
```

`build_fixtures.py` repeats every row in source, so a binary file can still be
reviewed in a diff.

## ssga_spy_baseline.xlsx / ssga_spy_shifted.xlsx

An SSGA daily-holdings workbook in the real layout: a four-row fund preamble
(`Fund Name:`, `Ticker Symbol:`, `Holdings:`, blank), then a header row that
names each column, then the rows.

* `ssga_spy_baseline.xlsx` — aligned. Parses to `{NVDA, AAPL, MSFT}`.
* `ssga_spy_shifted.xlsx` — the data block carries one extra leading cell (a
  position column inserted without a matching header cell), so every data cell
  sits one column right of the header cell that names it. The header is
  untouched, which is what a "new first column" looks like when only the rows
  were rewritten.

**What the parser did before the contract test existed** (measured on this
fixture, `app/market/official.py` at 9b3212a):

```text
baseline -> {'AAPL', 'MSFT', 'NVDA'}
shifted  -> set()          # plus one INFO line: "ssga holdings for SPY: 0 tickers"
```

So the old code did not return *wrong numbers* — it returned **no members** for
the S&P 500, which is the same kind of silent zero the plan's §7 row calls out.
Two things make that dangerous rather than harmless:

1. `parse_ssga_holdings` documents "an unreadable file yields an empty set
   rather than an exception", so `set()` is a legal answer for both "the index
   is empty" and "the format moved". Only `OfficialDepthSource`'s independent
   `if not members:` check turns the empty result into a WARNING plus
   `complete=False`; a direct caller (the ops probe, a CLI) has no such guard.
2. The landing spot of the drift decides the damage. `_clean_ticker` validates
   every cell against `^[A-Z]{1,6}([.\-][A-Z]{1,2})?$`, so with this layout the
   misread column holds names or CUSIPs and everything is rejected. If the
   misread column held short upper-case codes, the parse would return a
   non-empty set of plausible-looking "tickers" and the source-layer guard
   (`if not members`) would let it through as a complete index. Measured on a
   synthetic international-fund variant of the same drift (same column names,
   names ending in a 1–2 letter suffix, currencies in the weight column):

   ```text
   drifted parse -> ['NESTLE-SA', 'SAP-SE']      # real tickers: NESN, SAP, AZN
   ```

   `SAP-SE` looks exactly like a class-suffixed symbol (`BRK.B` → `BRK-B`), so
   nothing downstream can tell it from a real one. That is the
   wrong-but-plausible case the contract is written against.

## sec_bulk/2026q1, sec_bulk/2026q3

Two quarters of the SEC financial statement data set in the 2026q3 column
layout (`sub.txt` 36 columns, `num.txt` 10 columns, tab separated), holding one
Apple filing each: the 10-K in q1 and the fiscal-Q3 10-Q in q3. `pre.txt` is
omitted because the loader never reads it.

**`2026q2` is deliberately absent.** That hole is the fixture: the TTM
arithmetic silently degrades when a quarter is missing, and the difference
between "no growth" and "flat company" is invisible without a diagnostic.

`build_quarters([q1, missing_q2, q3])` already raises `FileNotFoundError`
naming `2026q2.zip` — the loader refuses to build a smaller index when it is
*told* about a quarter. The gap is discovery: `SecFundamentalsDepthSource`
walks the recent-quarter labels, keeps the ones with a ZIP, and says nothing
about the rest.

## yahoo_getcrumb_too_many_requests.txt

The literal body Yahoo's `/v1/test/getcrumb` answered during the 2026-10
incident — HTTP **200**, prose, no token. The body was stored as the crumb and
pasted into every quote URL, so every batch answered 401.

All three crumb parsers in `app/` reject it today (`_CRUMB_RE` /
`_looks_like_crumb`), so it can no longer reach a query string. None of them
says *why*: the refusal is `""`, which is also what "the cookie jar is empty"
returns, so a caller cannot branch on "this IP is throttled" — the state that
made the incident last hours instead of one retry.
