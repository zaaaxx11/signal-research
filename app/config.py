"""Settings for MEE. No provider key is ever stored here (02 §3.11)."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
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
    depth_refresh_s: float = Field(default=86_400.0, gt=0)
    depth_jitter_s: float = Field(default=1_800.0, ge=0)
    depth_concurrency: int = Field(default=3, ge=1, le=10)
    depth_delay_s: float = Field(default=0.1, ge=0)
    depth_yahoo_concurrency: int = Field(default=2, ge=1, le=5)
    depth_sec_snapshot: Path | None = None
    depth_universe_path: Path | None = None
    depth_store_path: Path = Path("data/depth.json")
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


def get_settings() -> Settings:
    """Return process settings (env prefix ``MEE_``)."""

    return Settings()
