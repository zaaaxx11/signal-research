"""Screen: one TradingView bulk scan → filtered universe → 20-row display → bundle.

The package is the cheap half of the enrichment engine: ranking the whole
universe costs one scan, and the expensive per-symbol enrichment is spent only
on the twenty rows a user actually sees. Nothing here builds a second scraper —
the scan is one POST to the scanner endpoint, the deep half is injected by the
composition root (``Runtime.screen_deep_engine``).

Modules
-------
``scanner``     one HTTP request, positional ``d`` arrays mapped **by name**.
``universe``    filter (common stock, USD 100 m floor, no OTC), cross-listing
                dedupe, MIC mapping, US vs ex-US tab ownership.
``rank``        three transparent sorts; stable, so a tie keeps input order.
``snapshot``    a 300 s display window and the always-20-rows browse document.
``repository``  ``screens`` + ``screen_rows`` (the 20 displayed rows only).
``export``      one bundle, two scopes: ``all`` light, ``top20`` deep.
``deep``        the default production deep engine (quote rung, canonical row).
"""
