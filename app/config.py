"""Settings for MEE. No provider key is ever stored here (02 §3.11)."""

from __future__ import annotations

from pathlib import Path

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


def get_settings() -> Settings:
    """Return process settings (env prefix ``MEE_``)."""

    return Settings()
