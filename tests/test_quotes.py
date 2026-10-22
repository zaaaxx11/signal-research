"""Single-use quote CAS (02 §4.3; 05 Langkah 4: second use -> 409, TTL -> 410)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from app.jobs.errors import QuoteAlreadyUsed, QuoteExpired
from app.jobs.quotes import open_quote_repository
from tests._support import quote_plan

T0 = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)
OWNER = "sha256:owner-a"
OTHER = "sha256:owner-b"


async def _quote(repo: Any, **kwargs: Any) -> dict[str, Any]:
    kwargs.setdefault("ttl_s", 900)
    return await repo.create(api_key=OWNER, plan=quote_plan(), now=T0, **kwargs)


async def test_first_use_consumes_the_quote_and_returns_the_frozen_plan(tmp_path: Path) -> None:
    repo = await open_quote_repository(tmp_path / "mee.db")
    quote = await _quote(repo)
    assert quote["id"]
    assert quote["status"] == "Open"
    assert quote["used_at"] is None

    consumed = await repo.consume(quote["id"], api_key=OWNER, now=T0 + timedelta(seconds=1))
    assert consumed["plan"]["columns_version"] == "v1"
    assert consumed["plan"]["universe"]["symbols"] == ["AAPL", "BBCA.JK"]
    assert consumed["used_at"] is not None


async def test_second_use_is_a_409(tmp_path: Path) -> None:
    repo = await open_quote_repository(tmp_path / "mee.db")
    quote = await _quote(repo)
    await repo.consume(quote["id"], api_key=OWNER, now=T0 + timedelta(seconds=1))

    with pytest.raises(QuoteAlreadyUsed) as caught:
        await repo.consume(quote["id"], api_key=OWNER, now=T0 + timedelta(seconds=2))
    assert caught.value.status_code == 409


async def test_foreign_owner_gets_409_and_does_not_burn_the_quote(tmp_path: Path) -> None:
    repo = await open_quote_repository(tmp_path / "mee.db")
    quote = await _quote(repo)

    with pytest.raises(QuoteAlreadyUsed) as caught:
        await repo.consume(quote["id"], api_key=OTHER, now=T0 + timedelta(seconds=1))
    assert caught.value.status_code == 409, "foreign owner must not learn 'unused' vs 'used'"

    consumed = await repo.consume(quote["id"], api_key=OWNER, now=T0 + timedelta(seconds=2))
    assert consumed["plan"]["columns_version"] == "v1"


async def test_expired_quote_is_a_410(tmp_path: Path) -> None:
    repo = await open_quote_repository(tmp_path / "mee.db")
    quote = await _quote(repo, ttl_s=900)

    with pytest.raises(QuoteExpired) as caught:
        await repo.consume(quote["id"], api_key=OWNER, now=T0 + timedelta(seconds=901))
    assert caught.value.status_code == 410
