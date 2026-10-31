# Finviz front end — synthesis, and what it means for us

Three measured reports sit next to this one:

| report | what it covers |
|---|---|
| `finviz-frontend-html.md` | pages, raw HTML, nav markup, URL grammar, DOM, SSR verdict |
| `finviz-frontend-assets.md` | every request, bytes, JS/CSS, images, timing, caching |
| `finviz-frontend-interaction.md` | filter/sort/page/nav on the wire, maps, charts, watchlist |

All numbers below were measured on 2026-10-31 between 14:45 and 15:15 UTC, from this box,
against public pages, with a real Chromium (Cloudflare challenged headless three times; only
sessions that cleared are counted). Our own numbers were measured on the same box.

## The one-sentence answer

Finviz is **not** smooth because it is small. Its screener page downloads **1.01 MB of
first-party JS and 596 KB of CSS**, and 55% of all bytes are ads. It is smooth because the
**rows are already in the HTML** when the document arrives: the first data row sits at 37% of
the document's byte offset and paints *before* `DOMContentLoaded` in 9 of 9 runs. Everything
heavy — bundles, ads, filter dictionary — lands **after** the user already sees data.

## What they actually built

| layer | mechanism | evidence |
|---|---|---|
| shell / nav | server-rendered MPA, 13 of 14 items are real `<a href>`; Charts is a `javascript:` upsell | clicking Maps = new document, `navigation.type = "navigate"`, 16.8 KB br, TTFB 110 ms |
| screener | SSR 20 rows (220 cells) + a 78.8 KB JSON island (28% of the page) | raw curl body contains the rows; JS disabled still yields 20 rows |
| grid | `div` ARIA grid, not `<table>`; lane-virtualised, absolutely positioned | `role=grid aria-rowcount=5621 aria-colcount=11`, 1,083 elements total |
| interactions | `fetch` 2.6 KB JSON → `history.pushState` → swap 20 rows in place | filter: 2,575 B wire, 255–804 ms server, scroll and panel preserved |
| pagination | real `<a href="?v=111&r=21" aria-current=page>`, JS intercepts it | progressive enhancement |
| maps | two 1025×569 canvases, 503 rects, first-party squarified renderer | `toDataURL` proves pixels; zero d3/echarts |
| charts | two 1201×400 canvases fed by a 35 KB inline data block | zero XHR for chart data |
| caching | document `no-cache` **without** a validator; assets `max-age=31536000` + ETag | every nav re-downloads the 41.5 KB document |

## The costs they pay, that we should not copy

| cost | number |
|---|---|
| third-party bytes | 55.2% of 3.12 MB wire (18 analytics, 34 ad-stack requests) |
| first-party JS | 1.01 MB br across 48 requests / 27 bundles |
| CSS | 5,693 rules, 596 KB decoded, render-blocking, not inlined |
| document revalidation | impossible — `no-cache` with no ETag, so 41.5 KB on every navigation |
| DOM semantics | 0 `<main>`, 0 `<header>`, 0 `<h1>`, no `<nav>`, no `aria-current` on nav |
| timing spread | FCP 532 → 7,448 ms across 9 runs on this 2-vCPU box (contention, reported as spread) |

## Ours, measured the same day

| measure | Finviz | ztockz | verdict |
|---|---|---|---|
| initial JS | 1.01 MB br | **130.7 KB gzip** | we are 7.7× lighter |
| CSS | 596 KB decoded | 11 KB gzip | we are far lighter |
| third-party share | 55.2% | **0%** | we win |
| rows in HTML | **20 (SSR)** | 0 — rendered after JS + fetch | **they win** |
| first row painted | 1,224–1,734 ms (quiet runs) | **2,185 ms**, after DCL (1,733 ms) | they win |
| interaction payload | 2.6 KB wire for 20 rows | ~38 KB decoded for 100 rows (≈381 B/row vs their 392 B/row) | parity per row |
| revalidation | none possible (no validator) | **304 in 33 ms** | we win |
| semantics | grid roles, real `<a>` pager, no landmarks | caption, `scope=col`, rowcount/colcount, skip link, one live region | we win |

The interesting part: **our per-row payload is the same size as theirs.** Our disadvantage is not
data, it is ordering — we ship 130 KB of JS, then fetch 38 KB JSON, then paint a row. They ship
the row inside the document and load the JS behind it.

## Four moves, ranked by perceived gain per unit of work

1. **Render the first screen on the server.** Inject the first 20–25 rows (the same JSON the
   panel returns) into the shell as an island, and let React adopt it. First row would arrive
   with the document (~0.3–0.5 s) instead of after the bundle and the fetch (~2.2 s).
   *Effort: 1–2 days. Risk: the island and the live data must never disagree — the island is a
   snapshot, so it needs `as_of` + an immediate revalidate on mount.*
2. **Inline the first page's data, not the whole panel.** The island alone removes the first
   round trip even without full SSR; 20 rows of our shape is ~7.6 KB raw, ~1.5 KB brotli.
   *Effort: half a day on top of (1), or standalone.*
3. **Make paging real links.** `<a href="?page=2" aria-current="page">` with JS intercepting is
   cheaper than it sounds and buys: works without JS, middle-click, prefetch, and a URL a user
   can copy. *Effort: half a day.*
4. **Copy nothing else.** Their cache policy, their ad stack, their missing landmarks and their
   1 MB of JS are all things we already do better; the lesson is ordering, not weight.

## What this does not answer

- Cold-CDN behaviour on their side (edge was warm, `age` up to 21.6 h).
- Whether their SSR island ever disagrees with the JSON API (we could not force a race).
- Their authenticated watchlist write path (no account, not tested).
