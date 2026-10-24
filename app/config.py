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
    #: ``GET /screens`` (P10) is a multi-source consensus by default: the
    #: TradingView snapshot supplies the twenty candidates and one observation,
    #: Yahoo/Nasdaq re-verify their cells. ``false`` turns the default display
    #: back into the raw snapshot, honestly labelled ``source=tradingview`` and
    #: ``degraded=true`` — it never fabricates a consensus. ``timeout_s`` is the
    #: per-verifier HTTP read timeout (Yahoo batch, Nasdaq per symbol).
    screen_multi_source: bool = True
    screen_verify_timeout_s: float = Field(default=10.0, gt=0)


def get_settings() -> Settings:
    """Return process settings (env prefix ``MEE_``)."""

    return Settings()
