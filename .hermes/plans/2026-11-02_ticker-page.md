# Ticker page — plan from the harvested route inventory

Source of truth: /home/ubuntu/finviz-research/{routes.md,inventory.md} (42 captures).
Owner: Zaaaxx. Builders: subagents. UI last.

## What the reference surface actually contains

Overview (one page, 12 tables):
- header: ticker, company, sector, industry, index membership, price, change, volume, cap, prev close
- snapshot, six parallel columns, 14 rows each: profile+dividends, valuation, EPS/sales growth, ownership+quality, shares/short/technicals, performance+consensus
- peers list, "held by" ETF list
- chart island (annual/quarterly series as JSON)
- insider rows (links to filings), news links

Separate tabs: short interest (si), earnings (ea), options chain (oc), filings (lf),
dividends (dv), forecast (fc), dilution (di). `ty=is|bs|cf` all 302 to `ty=c`.

## The gap that is our advantage

Balance sheet and cash flow are **not published** on that surface at all.
Our depth store carries SEC XBRL facts (assets, liabilities, equity, cash, debt,
revenue, net income, shares). So our ticker page can show three real statements
with 10+ years where the reference shows two annual periods of one statement.

## Page we will build (route: /app/stock/{symbol})

Tabs: Overview · Statements · Ownership · Insider · Filings · News · Chart
(Options only when an option-chain source exists; today it does not.)

Overview
- header band (price, change, cap, volume, prev close, index, sector)
- chart: bar series (already shipped), period chips D/W/M
- snapshot: 6 columns x 14 rows, values from the 116-column catalogue + depth
- peers: same sector, nearest market cap, 10 rows, click to open
- held by: ETFs we actually hold snapshots for (SPY, DIA, IWM)
- insider: last 10 Form 4 rows
- news: latest 10 for the symbol

Statements (new, differentiated)
- three panels: income, balance, cash flow
- periods: 10 annual + 8 quarterly where SEC facts allow
- each row: label, periods, YoY %, TTM column
- honest empty cell when a tag is absent; never a zero

## Backend

T1 `GET /engine/statements/{symbol}` — read SEC companyfacts from the depth store:
  income: revenue, gross profit, operating income, net income, EPS diluted
  balance: cash, current assets, total assets, current liabilities, total
           liabilities, equity, long-term debt, shares outstanding
  cash flow: operating, investing, financing, capex, free cash flow (derived)
  Shape: {symbol, source:"sec-xbrl", as_of, periods:[...], statements:{income:[{key,label,unit,values:{2025:..}}]}}
  TDD: known fixture with two years; a missing tag yields null, not 0; a symbol
  with no SEC facts is 404 `no_statements`.
T2 `GET /engine/peers/{symbol}` — same sector from the universe, rank by |cap - cap|,
  10 rows; fallback to industry when sector is too small.
T3 `GET /engine/profile/{symbol}` — one call for the header: price, change, cap,
  volume, prev close, sector, industry, index membership, 52w, beta, RSI.
  Reuses the scan row; no second computation path.

## Frontend

U1 route /app/stock/{symbol} with tabs; deep-linkable (?tab=statements).
U2 SnapshotGrid component: 6 columns, label left (muted), value right (mono),
  up/down classes, dense 20 px rows; responsive 6/3/2.
U3 StatementsTable: rows x periods, sticky first column, right-aligned numbers,
  YoY column, period selector annual/quarterly.
U4 Peers + HeldBy lists, Insider table, News list — all reuse existing components.
U5 Accessibility: real table semantics, aria-sort off (static), sr-only captions.

## Out of scope, stated
Options chain (no feed), analyst rating history (no feed), dilution tab (no source),
intraday chart (daily bars only).

## Done means
/app/stock/AAPL shows Overview with the six-column snapshot and peers/held-by, and
Statements shows three statements with >=10 annual periods for AAPL, NVDA and A.
Backend tests green, web check green, no fabricated cell.
