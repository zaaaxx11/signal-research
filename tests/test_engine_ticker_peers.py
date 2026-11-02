"""T2 — peers by sector, nearest market cap, industry fallback.

The group is defined over the *screener's* universe rows (``build_universe``
output), so these tests build those rows as plain mappings. Two decisions are
the whole contract: the subject is never its own peer, and a peer is "nearest"
only when both market caps are known.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx2
import pytest

from app.screen.ticker import peer_document


def row(
    symbol: str,
    *,
    sector: str | None = "Electronic Technology",
    industry: str | None = "Semiconductors",
    market_cap: float | None = 1_000_000_000_000.0,
    price: float | None = 100.0,
    change_pct: float | None = 1.0,
    name: str | None = None,
) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "name": name or f"{symbol} Corp",
        "exchange": "XNAS",
        "country": "United States",
        "sector": sector,
        "industry": industry,
        "price": price,
        "change_pct": change_pct,
        "volume": 1_000_000.0,
        "market_cap_usd": market_cap,
    }


def cohort(
    count: int,
    *,
    sector: str = "Electronic Technology",
    industry: str = "Semiconductors",
) -> list[dict[str, Any]]:
    """``count`` peers whose caps straddle the subject's USD 1T."""

    rows: list[dict[str, Any]] = []
    for index in range(count):
        cap = 1_000_000_000_000.0 + (index + 1) * 10_000_000_000.0
        rows.append(row(f"P{index:02d}", sector=sector, industry=industry, market_cap=cap))
    return rows


# ---------------------------------------------------------------------------
# The group
# ---------------------------------------------------------------------------


def test_ten_nearest_by_market_cap_excluding_the_subject() -> None:
    subject = row("SUBJ", market_cap=1_000_000_000_000.0)
    document = peer_document([subject, *cohort(12)], "SUBJ")

    assert document is not None
    assert document["symbol"] == "SUBJ"
    assert document["basis"] == "sector"
    assert document["count"] == 10
    assert len(document["peers"]) == 10
    symbols = [peer["symbol"] for peer in document["peers"]]
    assert "SUBJ" not in symbols
    assert symbols == [f"P{index:02d}" for index in range(10)]


def test_nearest_is_absolute_distance_not_size_rank() -> None:
    subject = row("SUBJ", market_cap=1_000_000_000_000.0)
    rows = [
        subject,
        row("FAR", market_cap=2_000_000_000_000.0),  # +1.0T
        row("NEAR", market_cap=1_010_000_000_000.0),  # +10B
        row("BELOW", market_cap=970_000_000_000.0),  # -30B
    ]
    document = peer_document(rows, "SUBJ")

    assert document is not None
    assert [peer["symbol"] for peer in document["peers"]] == ["NEAR", "BELOW", "FAR"]


def test_a_short_sector_falls_back_to_industry() -> None:
    subject = row("SUBJ", sector="Electronic Technology")
    thin_sector = cohort(3, industry="Other Industry")
    wide_industry = [
        row(
            f"I{index:02d}",
            sector="Other Sector",
            industry="Semiconductors",
            market_cap=900_000_000_000.0 + index * 1e9,
        )
        for index in range(9)
    ]
    document = peer_document([subject, *thin_sector, *wide_industry], "SUBJ")

    assert document is not None
    assert document["basis"] == "industry"
    assert document["count"] == 9
    assert {peer["symbol"] for peer in document["peers"]} == {
        f"I{index:02d}" for index in range(9)
    }


def test_a_sector_with_exactly_eight_peers_is_kept() -> None:
    subject = row("SUBJ")
    document = peer_document([subject, *cohort(8)], "SUBJ")

    assert document is not None
    assert document["basis"] == "sector"
    assert document["count"] == 8


def test_a_peer_without_a_cap_cannot_be_nearest() -> None:
    subject = row("SUBJ", market_cap=1_000_000_000_000.0)
    rows = [subject, row("NOCAP", market_cap=None), row("CAP", market_cap=1_010_000_000_000.0)]
    document = peer_document(rows, "SUBJ")

    assert document is not None
    assert [peer["symbol"] for peer in document["peers"]] == ["CAP"]


def test_a_subject_without_a_cap_has_no_distance_to_rank() -> None:
    subject = row("SUBJ", market_cap=None)
    document = peer_document([subject, *cohort(3)], "SUBJ")

    assert document is not None
    assert document["count"] == 0
    assert document["peers"] == []


def test_null_sectors_and_industries_never_group_together() -> None:
    subject = row("SUBJ", sector=None, industry=None)
    document = peer_document([subject, *cohort(3, sector="Electronic Technology")], "SUBJ")

    assert document is not None
    assert document["basis"] is None
    assert document["count"] == 0


def test_an_unknown_symbol_is_none() -> None:
    assert peer_document(cohort(3), "NOPE") is None


def test_a_peer_carries_the_seven_display_fields() -> None:
    subject = row("SUBJ")
    document = peer_document([subject, *cohort(1)], "SUBJ")

    assert document is not None
    peer = document["peers"][0]
    assert set(peer) == {
        "symbol",
        "name",
        "exchange",
        "country",
        "sector",
        "industry",
        "market_cap",
        "price",
        "change_pct",
    }


# ---------------------------------------------------------------------------
# The route
# ---------------------------------------------------------------------------


@asynccontextmanager
async def route_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[httpx2.AsyncClient]:
    monkeypatch.setenv("MEE_DB_PATH", str(tmp_path / "mee.db"))
    monkeypatch.setenv("MEE_EXPORT_DIR", str(tmp_path / "exports"))

    from app.main import create_app

    application = create_app()
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=application), base_url="http://mee.test"
    ) as client:
        yield client


def install_universe(
    monkeypatch: pytest.MonkeyPatch, rows: Sequence[dict[str, Any]]
) -> None:
    async def universe(_request: Any) -> Sequence[dict[str, Any]]:
        return rows

    monkeypatch.setattr("app.api.engine.ticker_universe", universe)


async def test_peers_route_returns_the_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_universe(monkeypatch, [row("SUBJ"), *cohort(12)])

    async with route_client(tmp_path, monkeypatch) as client:
        response = await client.get("/engine/peers/SUBJ")

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"symbol", "sector", "industry", "basis", "count", "peers"}
    assert body["count"] == 10


async def test_peers_route_unknown_symbol_is_404(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_universe(monkeypatch, cohort(3))

    async with route_client(tmp_path, monkeypatch) as client:
        response = await client.get("/engine/peers/NOPE")

    assert response.status_code == 404, response.text
    assert response.json()["error"] == "security_not_found"


async def test_peers_route_scan_failure_is_503(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.screen.scanner import ScanError

    async def failing(_request: Any) -> Sequence[dict[str, Any]]:
        raise ScanError("scanner blocked")

    monkeypatch.setattr("app.api.engine.ticker_universe", failing)

    async with route_client(tmp_path, monkeypatch) as client:
        response = await client.get("/engine/peers/SUBJ")

    assert response.status_code == 503, response.text
    assert response.json()["error"] == "scanner_unavailable"
