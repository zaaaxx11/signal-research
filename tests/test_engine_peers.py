"""Contract for engine-computed peers (R5B §1-§3).

``GET /engine/peers/{symbol}/industry`` answers "who else is in this group?" from the
identity the engine already owns — the SEC SIC industry on
:class:`~app.engine.model.SecurityRecord` — rather than from a scraped peer
list. The tests below pin the document, the deterministic order (same MIC
first, then symbol), the ``limit`` bound, the two honest empty answers
(different industry and a null industry), the two house error envelopes and
the service-level contract.

The master is handwritten so every date is fixed and every grouping decision is
visible in one screen: no network, no sleeps and no wall clock. The dataset is
loaded ``FETCHED_AT``, later than every identity date, so ``as_of`` can only be
the subject's own newest provenance — never the load clock and never a peer's
newer document.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest

from app.engine.ingest import EngineDataset
from app.engine.model import Provenance, SecurityRecord
from app.engine.security import SecurityMaster
from app.engine.service import EngineService, EngineUnavailable

#: The identity date every fixture row is first asserted with.
BASE_AS_OF = "2026-10-20"
#: A newer document for the subject only; the document must report this date.
AAPL_AS_OF = "2026-10-26"
#: A newer document for a *peer*; it must never date the subject's document.
NVDA_AS_OF = "2026-10-27"
#: The load clock, deliberately the newest date in the fixture.
FETCHED_AT = "2026-11-02T09:30:00+00:00"

#: The six identity fields every peer may carry — no provider label, no score.
_PEER_KEYS = frozenset(
    {"symbol", "name", "exchange", "mic", "industry", "country"}
)
#: The five top-level keys of the peer document.
_DOCUMENT_KEYS = frozenset({"symbol", "industry", "as_of", "count", "peers"})

#: ``symbol, name, industry, exchange, mic`` for the contract's fixture master.
_BASE_ROWS: tuple[tuple[str, str, str | None, str, str], ...] = (
    ("AAPL", "APPLE INC.", "Electronic Computers", "Nasdaq", "XNAS"),
    ("MSFT", "MICROSOFT CORP", "Electronic Computers", "Nasdaq", "XNAS"),
    ("NVDA", "NVIDIA CORP", "Electronic Computers", "Nasdaq", "XNAS"),
    (
        "BRK-B",
        "BERKSHIRE HATHAWAY INC.",
        "Fire, Marine & Casualty Insurance",
        "NYSE",
        "XNYS",
    ),
    ("JPM", "JPMORGAN CHASE & CO", "National Commercial Banks", "NYSE", "XNYS"),
    ("ZZZZ", "ZZZZ CORP", None, "Nasdaq", "XNAS"),
)


def _record(
    symbol: str,
    name: str,
    industry: str | None,
    *,
    exchange: str = "Nasdaq",
    mic: str = "XNAS",
) -> SecurityRecord:
    """One identity row with the fixture's fixed country, class and currency."""

    return SecurityRecord(
        symbol=symbol,
        name=name,
        cik=None,
        exchange=exchange,
        mic=mic,
        asset_class="equity",
        currency="USD",
        country="United States",
        sector=None,
        industry=industry,
    )


def _master(
    *,
    industries: Mapping[str, str | None] | None = None,
    venues: Mapping[str, tuple[str, str]] | None = None,
    aapl_as_of: str | None = AAPL_AS_OF,
    nvda_as_of: str | None = NVDA_AS_OF,
) -> SecurityMaster:
    """The contract's six-row master, plus subject/peer provenance documents.

    ``industries`` and ``venues`` override one row without rebuilding the rest,
    which is how the ordering and case-folding cases change exactly one fact.
    AAPL and NVDA each get a second document: AAPL's newer date must date the
    document, and NVDA's even newer one must not (``as_of`` belongs to the
    subject, not to the peer set).
    """

    master = SecurityMaster()
    rows: list[SecurityRecord] = []
    for symbol, name, industry, exchange, mic in _BASE_ROWS:
        if industries is not None and symbol in industries:
            industry = industries[symbol]
        if venues is not None and symbol in venues:
            exchange, mic = venues[symbol]
        rows.append(_record(symbol, name, industry, exchange=exchange, mic=mic))
    master.load(rows, Provenance(source="sec", as_of=BASE_AS_OF))

    for symbol, as_of in (("AAPL", aapl_as_of), ("NVDA", nvda_as_of)):
        if as_of is not None:
            master.add(master.require(symbol), Provenance(source="sec", as_of=as_of))
    return master


def _dataset(master: SecurityMaster) -> EngineDataset:
    """A dataset with identity only: peers need no facts and no network."""

    return EngineDataset(
        fetched_at=FETCHED_AT,
        security_master=master,
        facts={},
        errors={},
    )


def _service(master: SecurityMaster | None = None) -> EngineService:
    """The composition a deployment uses after ingestion."""

    return EngineService.from_dataset(_dataset(_master() if master is None else master))


@asynccontextmanager
async def _client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    service: EngineService,
) -> AsyncIterator[Any]:
    """An ASGI client over the real app, wired to exactly one engine service."""

    monkeypatch.setenv("MEE_DB_PATH", str(tmp_path / "mee.db"))
    monkeypatch.setenv("MEE_EXPORT_DIR", str(tmp_path / "exports"))

    from httpx2 import ASGITransport, AsyncClient

    from app.main import create_app

    application = create_app()
    application.state.engine = service
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://mee.test"
    ) as client:
        yield client


def _peer_symbols(document: Mapping[str, Any]) -> list[str]:
    """Peer tickers in document order."""

    return [peer["symbol"] for peer in document["peers"]]


# ---------------------------------------------------------------------------
# §1 The document
# ---------------------------------------------------------------------------


async def test_peers_returns_the_same_industry_group_without_the_subject(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AAPL's group is MSFT and NVDA, alphabetical, self excluded, count two."""

    async with _client(tmp_path, monkeypatch, _service()) as client:
        response = await client.get("/engine/peers/AAPL/industry")

    assert response.status_code == 200, response.text
    body = response.json()

    assert set(body) == _DOCUMENT_KEYS
    assert body["symbol"] == "AAPL"
    assert body["industry"] == "Electronic Computers"
    assert body["count"] == 2
    assert _peer_symbols(body) == ["MSFT", "NVDA"]
    assert "AAPL" not in _peer_symbols(body)


async def test_peers_dates_the_document_with_the_subject_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``as_of`` is AAPL's newest document, not NVDA's (newer) or the load."""

    async with _client(tmp_path, monkeypatch, _service()) as client:
        response = await client.get("/engine/peers/AAPL/industry")

    assert response.status_code == 200, response.text
    body = response.json()

    assert body["as_of"] == AAPL_AS_OF
    assert body["as_of"] != NVDA_AS_OF, "a peer's document must not date the subject"
    assert body["as_of"] != FETCHED_AT, "the load clock must never be published"


async def test_each_peer_carries_exactly_the_six_identity_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every peer is ``symbol/name/exchange/mic/industry/country`` and nothing else."""

    async with _client(tmp_path, monkeypatch, _service()) as client:
        response = await client.get("/engine/peers/AAPL/industry")

    assert response.status_code == 200, response.text
    peers = response.json()["peers"]

    assert len(peers) == 2
    for peer in peers:
        assert set(peer) == _PEER_KEYS, peer
        assert isinstance(peer["symbol"], str) and peer["symbol"]
        assert isinstance(peer["name"], str) and peer["name"]
        assert isinstance(peer["industry"], str) and peer["industry"]
        assert peer["country"] == "United States"

    assert peers[0] == {
        "symbol": "MSFT",
        "name": "MICROSOFT CORP",
        "exchange": "Nasdaq",
        "mic": "XNAS",
        "industry": "Electronic Computers",
        "country": "United States",
    }


# ---------------------------------------------------------------------------
# §1 Ordering and the limit
# ---------------------------------------------------------------------------


async def test_peers_orders_the_same_mic_before_symbol_ascending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MSFT on XNYS sorts behind NVDA on the subject's XNAS, despite its name."""

    service = _service(_master(venues={"MSFT": ("NYSE", "XNYS")}))
    async with _client(tmp_path, monkeypatch, service) as client:
        response = await client.get("/engine/peers/AAPL/industry")

    assert response.status_code == 200, response.text
    assert _peer_symbols(response.json()) == ["NVDA", "MSFT"]


async def test_peers_limit_one_truncates_after_ordering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A limit of one returns the first peer only, not the first row loaded."""

    async with _client(tmp_path, monkeypatch, _service()) as client:
        response = await client.get("/engine/peers/AAPL/industry?limit=1")

    assert response.status_code == 200, response.text
    body = response.json()

    assert _peer_symbols(body) == ["MSFT"]
    assert body["count"] == 1
    assert len(body["peers"]) == body["count"]


async def test_peers_accepts_the_maximum_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The upper bound is inclusive: ``limit=50`` is a normal 200 page."""

    async with _client(tmp_path, monkeypatch, _service()) as client:
        response = await client.get("/engine/peers/AAPL/industry?limit=50")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["count"] == 2
    assert _peer_symbols(body) == ["MSFT", "NVDA"]


@pytest.mark.parametrize("raw_limit", ["0", "51", "x"])
async def test_peers_rejects_a_limit_outside_one_to_fifty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    raw_limit: str,
) -> None:
    """Zero, above the cap and non-numeric are one house ``422 invalid_limit``."""

    async with _client(tmp_path, monkeypatch, _service()) as client:
        response = await client.get(f"/engine/peers/AAPL/industry?limit={raw_limit}")

    assert response.status_code == 422, response.text
    body = response.json()
    assert set(body) == {"error", "errorMessage"}
    assert body["error"] == "invalid_limit"
    assert body["errorMessage"]


async def test_peers_validates_the_limit_before_looking_up_the_symbol(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unusable limit is a 422 even for an unknown symbol — not a 404."""

    async with _client(tmp_path, monkeypatch, _service()) as client:
        response = await client.get("/engine/peers/NOPE/industry?limit=0")

    assert response.status_code == 422, response.text
    assert response.json()["error"] == "invalid_limit"


# ---------------------------------------------------------------------------
# §1 Honest empty groups
# ---------------------------------------------------------------------------


async def test_peers_of_a_different_industry_is_an_empty_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BRK-B is alone in casualty insurance: JPM is a bank, so count is zero."""

    async with _client(tmp_path, monkeypatch, _service()) as client:
        response = await client.get("/engine/peers/BRK-B/industry")

    assert response.status_code == 200, response.text
    body = response.json()

    assert body["symbol"] == "BRK-B"
    assert body["industry"] == "Fire, Marine & Casualty Insurance"
    assert body["as_of"] == BASE_AS_OF
    assert body["count"] == 0
    assert body["peers"] == []


async def test_peers_of_a_null_industry_is_an_empty_group_not_a_503(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No SIC industry is a truthful empty group, and nulls never group together."""

    master = _master()
    master.add(
        _record("AAAA", "ANOTHER NULL INDUSTRY CO", None),
        Provenance(source="sec", as_of=BASE_AS_OF),
    )
    async with _client(tmp_path, monkeypatch, _service(master)) as client:
        response = await client.get("/engine/peers/ZZZZ/industry")

    assert response.status_code == 200, response.text
    body = response.json()

    assert body["symbol"] == "ZZZZ"
    assert body["industry"] is None
    assert body["as_of"] == BASE_AS_OF
    assert body["count"] == 0
    assert body["peers"] == []


# ---------------------------------------------------------------------------
# §1 Determinism
# ---------------------------------------------------------------------------


async def test_peers_is_deterministic_across_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two reads of the same group produce byte-identical documents."""

    async with _client(tmp_path, monkeypatch, _service()) as client:
        first = await client.get("/engine/peers/AAPL/industry?limit=10")
        second = await client.get("/engine/peers/AAPL/industry?limit=10")

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json() == second.json()
    assert _peer_symbols(first.json()) == ["MSFT", "NVDA"]


# ---------------------------------------------------------------------------
# §2 House envelopes and §1 service behaviour
# ---------------------------------------------------------------------------


async def test_peers_unknown_symbol_is_a_404_security_not_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A symbol outside the master is a 404, never an empty 200."""

    async with _client(tmp_path, monkeypatch, _service()) as client:
        response = await client.get("/engine/peers/NOPE/industry")

    assert response.status_code == 404, response.text
    body = response.json()
    assert set(body) == {"error", "errorMessage"}
    assert body["error"] == "security_not_found"
    assert body["errorMessage"]


async def test_peers_without_a_dataset_is_a_503_screen_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing ingested is a 503, not an empty group that looks like a market."""

    async with _client(tmp_path, monkeypatch, EngineService()) as client:
        response = await client.get("/engine/peers/AAPL/industry")

    assert response.status_code == 503, response.text
    body = response.json()
    assert set(body) == {"error", "errorMessage"}
    assert body["error"] == "screen_unavailable"
    assert body["errorMessage"]


def test_service_peers_without_a_master_raises_engine_unavailable() -> None:
    """The service refuses before a lookup when no identity master is loaded."""

    with pytest.raises(EngineUnavailable) as caught:
        EngineService().peers("AAPL")

    assert caught.value.code == "screen_unavailable"


def test_service_peers_unknown_symbol_is_none() -> None:
    """A loaded master answers ``None`` for a symbol it does not hold."""

    assert _service().peers("NOPE") is None


def test_service_peers_matches_industry_case_insensitively() -> None:
    """Case and surrounding whitespace are presentation, not a different group."""

    service = _service(
        _master(
            industries={
                "MSFT": " electronic computers ",
                "NVDA": "ELECTRONIC COMPUTERS",
            }
        )
    )
    document = service.peers("AAPL")

    assert document is not None
    assert _peer_symbols(document) == ["MSFT", "NVDA"]
