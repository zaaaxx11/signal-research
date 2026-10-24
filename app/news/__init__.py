"""News ingestion and event extraction (parity P1, enrichment P12).

Two layers, deliberately separate:

* :mod:`app.news.rss` — a **pure** Google News RSS parser. The document is an
  argument, never a fetch, so the frozen suite exercises the real feed format
  with the socket guard on.
* :mod:`app.news.service` — the pipeline: injectable providers (Google News
  RSS, GDELT), one concurrent pass per symbol, dedupe by URL *and* headline,
  newest-first ordering, a fifteen-minute cache and per-provider errors. Its
  output is **one consolidated row per symbol** — never per-source columns.

The package ``__init__`` deliberately re-exports nothing: ``app.news.service``
imports ``app.news.rss``, and an ``__init__`` that imported the service would
make that a cycle at import time.
"""
