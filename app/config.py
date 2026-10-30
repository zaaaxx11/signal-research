"""Settings for MEE. No provider key is ever stored here (02 §3.11)."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-backed runtime settings.

    Only non-secret, operational values live here. Provider credentials are
    read inside their adapters from env/files and never placed in a spec,
    the DB, a log line or an API response.
    """

    model_config = SettingsConfigDict(env_prefix="MEE_", extra="forbid")

    db_path: Path = Path("ztockz.db")
    export_dir: Path = Path("out")
    #: Reference master (02 §3.3). ``None`` → the repo seed at
    #: ``reference/venue_master.json``; ``MEE_REFERENCE_PATH`` overrides it.
    #: Not defaulted to a path here so a missing file is an explicit error at
    #: load time, not a surprising import of a module that happens to exist.
    reference_path: Path | None = None
    max_attempts: int = 3
    job_lease_s: int = 300
    quote_ttl_s: int = 900
    api_key_pepper: str | None = None
    #: ``fake`` keeps the deterministic in-process provider (Phase 0/1 behaviour,
    #: and the default so a bare checkout and the frozen suites stay offline);
    #: ``live`` walks the registry ladder through ``app.providers.http``.
    #: 02 §3.4 assumed one provider layer; this knob is what lets the two
    #: execution paths coexist without a test-only backdoor.
    provider_mode: Literal["fake", "live"] = "fake"
    #: ``GET /market/quote`` (P8). ``max_symbols`` is the request ceiling the
    #: route enforces, ``batch_size`` the Yahoo per-request ceiling (never > 20);
    #: ``stale_after_s`` is the engine's staleness threshold and ``timeout_s``
    #: the per-provider HTTP read timeout. No credential belongs to either
    #: endpoint, so nothing secret lives here either.
    quote_max_symbols: int = Field(default=100, ge=1, le=100)
    quote_batch_size: int = Field(default=20, ge=1, le=20)
    quote_stale_after_s: float = Field(default=5.0, gt=0)
    quote_timeout_s: float = Field(default=10.0, gt=0)
    #: QuoteHub (hybrid free/premium). Disabled by default so a bare checkout
    #: never starts a background crawl. ``quote_free_refresh_s`` is the free
    #: cadence; premium is drawn uniformly from the min/max range per cycle so
    #: the process does not publish a fixed polling fingerprint. The universe
    #: comes from ``MEE_QUOTE_UNIVERSE`` (JSON) or ``MEE_QUOTE_SYMBOLS``; the
    #: JSON store is a snapshot, not a per-tick log.
    quote_hub: bool = False
    quote_free_refresh_s: float = Field(default=900.0, gt=0)
    quote_premium_refresh_min_s: float = Field(default=60.0, gt=0)
    quote_premium_refresh_max_s: float = Field(default=120.0, gt=0)
    quote_hub_batch_size: int = Field(default=400, ge=1, le=400)
    quote_premium_batch_size: int = Field(default=500, ge=1, le=500)
    quote_universe_path: Path | None = None
    quote_store_path: Path = Path("data/quotes.json")
    quote_history_dir: Path | None = None
    #: Depth collector: Finviz + StockAnalysis + Yahoo, one field-level JSON
    #: store. It is daily, opt-in, and deliberately separate from the quote
    #: hub so a slow page cannot delay a price.
    depth: bool = False
    depth_finviz: bool = True
    depth_chart: bool = False
    depth_finra: bool = True
    depth_official: bool = True
    depth_official_concurrency: int = Field(default=2, ge=1, le=4)
    depth_news: bool = False
    depth_news_concurrency: int = Field(default=8, ge=1, le=16)
    depth_refresh_s: float = Field(default=86_400.0, gt=0)
    depth_jitter_s: float = Field(default=1_800.0, ge=0)
    depth_concurrency: int = Field(default=3, ge=1, le=10)
    depth_delay_s: float = Field(default=0.1, ge=0)
    depth_yahoo_concurrency: int = Field(default=2, ge=1, le=5)
    depth_yahoo_delay_s: float = Field(default=0.25, ge=0)
    depth_yahoo_symbol_timeout_s: float = Field(default=30.0, gt=0)
    #: Keyless fast path (plan 2026-10-28_224107): SEC bulk fundamentals +
    #: CNBC batch quote + Yahoo chart OHLC + FINRA + official index. When
    #: ``price_keyless`` is on, no API-key provider is read or called on the
    #: default path; Alpaca/Finnhub/Twelve/Polygon stay optional adapters.
    price_keyless: bool = True
    #: SEC quarterly bulk financial statement data set (``2026q3.zip``) is
    #: the fundamental backbone instead of per-symbol ``companyfacts``.
    depth_sec_bulk: bool = True
    #: Yahoo full depth remains available as a fallback rung, never the sole
    #: producer of a field group; set False to run strictly keyless-direct.
    depth_yahoo_fallback: bool = True
    #: The two per-symbol web rungs of the default path: Yahoo quoteSummary +
    #: timeseries (one handshake plus two requests per symbol) and the
    #: StockAnalysis statistics page. A keyless-fast run sets both ``False``
    #: and keeps SEC bulk + CNBC + chart + FINRA + official, which answer the
    #: same catalog without a per-symbol crawl.
    depth_yahoo_full: bool = True
    depth_stockanalysis: bool = True
    #: The per-symbol enrichment pass appended after the keyless rungs: Yahoo
    #: ``quoteSummary``/``fundamentals-timeseries`` plus one RSS headline feed
    #: per symbol. It is the crawl the keyless path structurally cannot do, so
    #: it costs ~13 minutes at the 4,217-symbol scale and is what upgrades field
    #: coverage to 109/116. Off by default: a daily cycle publishes the keyless
    #: row first and this pass is the extra rung, never a prerequisite.
    depth_enrich: bool = True
    #: Symbols per enrichment cycle. A single 4,217-symbol Yahoo pass stalls
    #: the egress (measured 0 bytes for 45 s on both direct and a residential
    #: proxy), so production rotates: this many symbols per cycle, cursor
    #: persisted, the depth store keeps the rest. 0 keeps the crawl-everything
    #: behaviour for small runs and tests.
    depth_enrich_batch: int = Field(default=500, ge=0)
    #: How often the **Yahoo** half advances one slice. Analyst targets,
    #: ownership and float are daily facts, so one pass per day is honest and
    #: costs 136 MB; running it every five minutes would re-buy the same
    #: number 288 times.
    depth_enrich_interval_s: float = Field(default=86_400.0, gt=0)
    #: How often the **news** half advances. A headline is stale in an hour
    #: and costs 2 KB per symbol, so this stays fast.
    depth_enrich_news_interval_s: float = Field(default=300.0, gt=0)
    #: Ride the paid residential endpoint for the enrichment rotation. Direct
    #: Yahoo 429s after a heavy pass and the rotation then never advances its
    #: cursor; without the auth file this rung is simply skipped.
    depth_enrich_proxy: bool = True
    #: On-demand enrichment (`POST /market/enrich`) — the premium hook. When
    #: set, the caller must present the same value in ``X-Enrich-Key``; unset
    #: leaves the route open, which is what a dev box wants.
    enrich_api_key: str | None = None
    #: Per-request ceiling for the on-demand path. A paid trigger buys a
    #: handful of symbols, not the universe.
    enrich_max_symbols: int = Field(default=50, ge=1)
    #: Per-caller budget for the on-demand path: ``enrich_rate_limit`` requests
    #: per ``enrich_rate_window_s`` seconds, bucketed by ``X-Enrich-Key`` when
    #: ``enrich_api_key`` is set and by client host otherwise. Over budget is a
    #: 429 carrying ``Retry-After``. ``0`` disables the gate explicitly (a
    #: single-tenant box behind a trusted proxy); an unset value is the default.
    enrich_rate_limit: float = Field(default=10.0, ge=0.0)
    enrich_rate_window_s: float = Field(default=60.0, gt=0.0)
    depth_sec_snapshot: Path | None = None
    depth_universe_path: Path | None = None
    depth_store_path: Path = Path("data/depth.json")
    #: A full run is 4,000+ symbols per source. One monolithic ``fetch_many``
    #: makes the whole daily cycle a hostage of the slowest provider, so the
    #: collector asks each source chunk by chunk and bounds every chunk with
    #: ``source_timeout_s``. A chunk that overruns is recorded as a visible
    #: error and skipped; the remaining chunks, the other sources and the
    #: persist of whatever arrived still happen. ``source_chunk_size`` is a
    #: request batching bound, not a universe limit.
    depth_source_timeout_s: float = Field(default=180.0, gt=0)
    depth_source_chunk_size: int = Field(default=250, ge=1)
    #: Coverage floor for the depth store: when a cycle leaves fewer distinct
    #: catalog fields filled than this, ``GET /market/depth/stats`` logs one
    #: WARNING per degraded cycle so a bad night is visible in journalctl
    #: instead of waiting for someone to open the store by hand.
    depth_coverage_floor: int = Field(default=60, ge=0)
    #: The screener's view of the depth store (2026-10-29 depth-screener plan,
    #: Phase 2). ``screen_depth`` is the one switch: ``false`` means the
    #: selection path reads no depth file at all — every row keeps exactly the
    #: columns the engine already carries. ``screen_depth_store_path`` is the
    #: read-side override; ``None`` means "the store the collector writes"
    #: (``depth_store_path``, ``data/depth.json`` when nothing is set), so a
    #: deployment that already exports ``MEE_DEPTH_STORE_PATH`` needs no second
    #: variable and cannot point the reader at a different file than the writer.
    #: Declared ``str`` because it is a read-side handle only, and it carries an
    #: explicit alias: the ``MEE_`` prefix would otherwise name it
    #: ``MEE_SCREEN_DEPTH_STORE_PATH`` while the documented variable is
    #: ``MEE_SCREEN_DEPTH_STORE`` (both spellings are accepted; a typo'd variable
    #: would otherwise be ignored in silence).
    screen_depth: bool = Field(
        default=True,
        description="Overlay depth-store columns onto /screens rows.",
    )
    screen_depth_store_path: str | None = Field(
        default=None,
        description=(
            "Depth store the screener reads; null means the collector's store "
            "(depth_store_path)."
        ),
        validation_alias=AliasChoices(
            "MEE_SCREEN_DEPTH_STORE",
            "MEE_SCREEN_DEPTH_STORE_PATH",
            "screen_depth_store_path",
        ),
    )
    #: ``GET /screens`` (P10) is a multi-source consensus by default: the
    #: TradingView snapshot supplies the twenty candidates and one observation,
    #: Yahoo/Nasdaq/Finviz/Google re-verify their cells (P10.1 added the last
    #: two). ``false`` turns the default display back into the raw snapshot,
    #: honestly labelled ``source=tradingview`` and ``degraded=true`` — it never
    #: fabricates a consensus. ``timeout_s`` is the per-verifier HTTP read
    #: timeout (Yahoo batch, Nasdaq per symbol, one page per Finviz/Google
    #: symbol).
    screen_multi_source: bool = True
    screen_verify_timeout_s: float = Field(default=10.0, gt=0)
    #: The two page-scraping verifiers (Finviz, Google Finance) run by default.
    #: ``false`` keeps the JSON pair only, for a deployment that will not depend
    #: on HTML markup; the consensus is still measured against the same five
    #: documented sources, so confidence stays honest instead of being
    #: normalised up. ``concurrency`` is capped at three on purpose: the display
    #: is twenty symbols and the pages are slow, so a wider fan-out would look
    #: like a crawl without buying latency the display can use.
    screen_html_verify: bool = True
    screen_html_verify_concurrency: int = Field(default=3, ge=1, le=3)
    #: ``GET /news/{symbol}`` (P12) consults Google News RSS and GDELT. A
    #: headline set is a slow picture, so the document — not the request — is
    #: cached for fifteen minutes: two reads inside the window send zero
    #: requests, which is also what keeps the fleet inside GDELT's documented
    #: one-request-per-five-seconds budget. ``limit`` is the default panel size
    #: (Finviz parity: eight per ticker) and ``timeout_s`` the per-provider HTTP
    #: read timeout. ``gdelt_enabled=false`` keeps the RSS leg only; the
    #: consolidated document never grows a per-source column either way.
    news_cache_ttl_s: int = Field(default=900, gt=0)
    news_limit: int = Field(default=8, ge=1, le=50)
    news_timeout_s: float = Field(default=10.0, gt=0)
    news_gdelt_enabled: bool = True
    #: Egress ladder for Yahoo fundamentals (P11E). A path to zcrapers'
    #: ``pool_ranked.json`` (or a plain text proxy list); empty = direct only.
    #: ``proxy_attempts`` is the free-rung fan-out per symbol, not a retry count
    #: for a healthy request; a refused direct wire cools for the whole bulk run.
    proxy_pool: Path | None = None
    proxy_attempts: int = Field(default=3, ge=1, le=10)
    proxy_direct_cooldown_s: float = Field(default=600.0, ge=0.0)
    #: ``GET /health`` (plan 2026-10-30_error-resilience §3). The route answers
    #: for the **deployment**, not for the uvicorn process, so every path it
    #: reads and every budget it compares against is declared here instead of
    #: being hardcoded: a box that moves its data directory must not have to
    #: patch the probe.
    #:
    #: ``health_depth_store_path`` defaults to ``None``, meaning "the store the
    #: collector writes" (``depth_store_path``). The two cursor files are derived
    #: from that same path with the collector's own ``with_suffix`` spelling, so
    #: a reader cannot be pointed at a different store than the writer's.
    health_depth_store_path: Path | None = Field(default=None)
    #: A depth store is daily, so ``/health`` allows ``depth_refresh_s`` plus
    #: this much slack before calling it stale. Two hours covers a slow cycle
    #: (measured 40-90 min at the 4,217-symbol scale) while still catching a
    #: missed night on the next poll instead of the next audit.
    health_depth_budget_slack_s: float = Field(default=7_200.0, ge=0.0)
    #: The engine snapshot. ``None`` reads ``MEE_ENGINE_SNAPSHOT`` — the very
    #: variable ``create_app``'s lifespan loads — so a deployment that already
    #: exports it needs no second variable and the probe cannot end up verifying
    #: a different file than the process serves. The field name is listed too so
    #: ``Settings(health_snapshot_path=...)`` keeps working (tests rely on it).
    health_snapshot_path: Path | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "MEE_HEALTH_SNAPSHOT_PATH",
            "MEE_ENGINE_SNAPSHOT",
            "health_snapshot_path",
        ),
    )
    #: Snapshot age budget: two daily cycles. Rebuilding a snapshot is expensive
    #: (a full EDGAR pass) and not every deployment needs a nightly one, but
    #: three days of silence is an engine that has stopped refreshing.
    health_snapshot_budget_s: float = Field(default=172_800.0, gt=0.0)
    #: Filesystem the disk check measures. ``None`` means the depth store's
    #: directory — the volume the crawl grows, and the one an ENOSPC stops first.
    health_data_dir: Path | None = Field(default=None)
    #: Free space below this is ``degraded``: the process still serves, but the
    #: next snapshot (24 MB) or depth write (11 MB) is the one that fails. 2 GiB
    #: leaves room for both plus the engine cache.
    health_min_free_gb: float = Field(default=2.0, ge=0.0)
    #: The screen database (``screens``/``screen_rows``). ``None`` means the
    #: process database (``db_path``).
    health_screen_db_path: Path | None = Field(default=None)
    #: A locked database must not hold the probe: SQLite waits this long for the
    #: writer and then the check reports ``unreadable``; the route still answers.
    #: Kept short because ``/health`` is polled by a load balancer.
    health_screen_db_timeout_s: float = Field(default=0.5, gt=0.0)
    #: ``GET /health/deep`` (never called by a load balancer) probes three
    #: upstreams. ``health_probe_timeout_s`` bounds each probe on its own and
    #: ``health_deep_budget_s`` bounds the whole route, so one upstream that
    #: accepts the connection and then says nothing cannot make an operator wait
    #: past ten seconds. The symbol and filer are the smallest request each
    #: source accepts, not a market sample.
    health_probe_timeout_s: float = Field(default=3.0, gt=0.0)
    health_deep_budget_s: float = Field(default=10.0, gt=0.0)
    health_deep_symbol: str = Field(default="AAPL", min_length=1)
    health_deep_cik: str = Field(default="0000320193", min_length=10, max_length=10)
    #: Alerting (``python -m app.ops.alerts``). The engine turns the facts above
    #: into events and a sink delivers them; nothing here names a platform.
    #: ``alert_sink_spec`` is a scheme-prefixed handle (``file:/var/log/...``),
    #: so wiring Telegram or Slack later is a new scheme in ``app.ops.sinks``
    #: and a new value here — not a new field, and not an edit to a rule.
    #: ``alert_state_path`` is the memory that stops a restart from paging
    #: twice: it holds the last thing said per key, and it is the only file the
    #: engine writes.
    alert_state_path: Path = Path("data/alerts.state.json")
    alert_sink_spec: str = "file:data/alerts.jsonl"
    #: A depth source is sick after this many consecutive failed cycles. Two,
    #: not one: a single flaky chunk is noise, two nights running is a rung that
    #: stopped answering. The ledger already restores the streak across a
    #: restart, so a deploy cannot reset the count to zero.
    alert_ledger_error_threshold: int = Field(default=2, ge=1)
    #: How many catalog fields the depth store may lose before coverage is
    #: called degraded. Measured against the high-water mark, not the previous
    #: run, so a slow slide of one field a night still fires on the fifth night
    #: instead of never. Five is the large-cap universe's noise budget: a whole
    #: source dropping out costs dozens, a single unavailable cell costs one.
    alert_field_drop_threshold: int = Field(default=5, ge=1)
    #: The daily canary (``python -m app.ops.canary``). It is installed beside
    #: the alert engine but owns its own memory: the drift baseline
    #: (``canary_state_path``) is a projection of yesterday's measurements, and
    #: the alert memory (``canary_alert_state_path``) is the engine's usual
    #: key -> last-said document. Two files, not one, because a drift baseline
    #: is not an alert record and merging the shapes would make either one
    #: unreadable to its own reader.
    #:
    #: ``canary_min_securities`` proves the snapshot is a universe, not a stub:
    #: production carries thousands (SEC company_tickers), so a thousand is a
    #: floor no healthy build can fall under and no empty file can claim.
    #: ``canary_min_median_fields`` is the per-symbol coverage floor: a keyless
    #: cycle fills roughly sixty catalog columns, and forty still passes with a
    #: whole source missing while failing on a store that parsed to nothing.
    #: ``canary_field_drop_threshold`` mirrors ``alert_field_drop_threshold``:
    #: five fields is the large-cap universe's night-to-night noise budget.
    #: ``canary_timeout_s`` bounds one upstream call and ``canary_budget_s``
    #: bounds the network time of the whole run, so two hanging providers cannot
    #: hold the timer past the next one.
    canary_report_path: Path = Path("data/canary.report.json")
    canary_state_path: Path = Path("data/canary.state.json")
    canary_alert_state_path: Path = Path("data/canary.alerts.state.json")
    canary_sink_spec: str = "file:data/canary.alerts.jsonl"
    canary_symbols: int = Field(default=20, ge=1)
    canary_min_securities: int = Field(default=1_000, ge=1)
    canary_min_median_fields: int = Field(default=40, ge=0)
    canary_field_drop_threshold: int = Field(default=5, ge=1)
    canary_timeout_s: float = Field(default=10.0, gt=0)
    canary_budget_s: float = Field(default=60.0, gt=0)
    #: The five symbols the quote check asks CNBC for. It is a *probe*, not a
    #: universe: five liquid names that every US quote backend answers, so a
    #: zero-price answer is an upstream failure rather than a delisting. A
    #: deployment that trades elsewhere can point it at its own list; the check
    #: needs five and pads from this default, which is why an empty value still
    #: cannot make the check pass by asking nothing.
    canary_quote_symbols: str = "AAPL,MSFT,NVDA,AMZN,GOOGL"


def get_settings() -> Settings:
    """Return process settings (env prefix ``MEE_``)."""

    return Settings()
