"""``ops refresh-identity``: fresh identity, carried facts, nothing relabelled.

The snapshot a deployment serves has two freshness problems, not one. Its
*identity* — which tickers exist, their names, venues and CIKs — is two bulk
EDGAR documents; its *facts* are one document per issuer, and re-reading 10,433 of
them is tens of gigabytes and hours. This suite pins the small, honest move: read
the identity again, carry every fact exactly as it was, and say plainly in the
report which rows came from where.

Every request goes through ``httpx2.MockTransport``; the suite-wide socket guard in
``tests/conftest.py`` fails any test that opens a connection.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

import httpx2
import pytest

from app.engine.ingest import EngineDataset, SecIngestError, refresh_identity
from app.engine.model import Cell, CellStatus, Provenance, Unit
from app.engine.security import SecurityMaster
from app.engine.sources.sec import TICKERS_EXCHANGE_URL, TICKERS_URL
from app.engine.store import EngineStore

FETCHED_AT = "2026-11-03T09:00:00+00:00"
AS_OF = "2026-11-03"
AAPL_CIK = "0000320193"
MSFT_CIK = "0000789019"
NEWCO_CIK = "0001999999"


class Web:
    """A recording ``httpx2.MockTransport``; an unrouted request is a 404."""

    def __init__(self) -> None:
        self.requests: list[httpx2.Request] = []
        self._routes: dict[str, Callable[[httpx2.Request], httpx2.Response]] = {}
        self._transport = httpx2.MockTransport(self._handle)

    def route(self, url: str, payload: Any) -> None:
        self._routes[url] = lambda _request: httpx2.Response(200, json=payload)

    def status(self, url: str, status: int) -> None:
        self._routes[url] = lambda _request: httpx2.Response(status, json={"error": status})

    def _handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        responder = self._routes.get(str(request.url))
        if responder is None:
            return httpx2.Response(404, json={"error": "unrouted", "url": str(request.url)})
        return responder(request)

    def client(self) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(transport=self._transport, follow_redirects=False)

    def urls(self) -> list[str]:
        return [str(request.url) for request in self.requests]


def exchange_document(rows: tuple[tuple[int, str, str, str], ...]) -> dict[str, Any]:
    return {
        "fields": ["cik", "name", "ticker", "exchange"],
        "data": [list(row) for row in rows],
    }


def legacy_index(rows: tuple[tuple[int, str, str], ...]) -> dict[str, Any]:
    return {
        str(index): {"cik_str": cik, "ticker": ticker, "title": title}
        for index, (cik, ticker, title) in enumerate(rows)
    }


def _cell(value: float, *, source: str = "sec") -> Cell:
    return Cell(
        value=value,
        unit=Unit.USD,
        provenance=Provenance(source=source, as_of=AS_OF, status=CellStatus.OK),
    )


def _write_snapshot(
    path: Path,
    symbols: dict[str, str],
    *,
    countries: dict[str, str] | None = None,
) -> None:
    """A snapshot whose facts carry ``symbols`` — the state a refresh starts from.

    ``countries`` stands in for the submissions enrichment a crawl adds: the field
    is not in the identity documents, which is exactly why a refresh has to carry
    it instead of rebuilding identity from scratch.
    """

    master = SecurityMaster.from_company_tickers(
        legacy_index(tuple((int(cik), symbol, f"{symbol} Inc") for symbol, cik in symbols.items())),
        as_of=AS_OF,
    )
    for symbol, country in (countries or {}).items():
        record = master.get(symbol)
        assert record is not None
        master.add(
            replace(record, country=country, industry="Software"),
            Provenance(source="sec", as_of=AS_OF, status=CellStatus.OK),
        )
    dataset = EngineDataset(
        fetched_at=FETCHED_AT,
        security_master=master,
        facts={symbol: {"pe": _cell(1.0 + index)} for index, symbol in enumerate(symbols)},
        errors={},
    )
    EngineStore(path).save(dataset)


# ---------------------------------------------------------------------------
# the happy path: identity re-read, facts carried
# ---------------------------------------------------------------------------


def _ten_symbols() -> dict[str, str]:
    """Ten known symbols: a 1-of-10 removal is a delisting, not a broken read."""

    return {f"SYM{index:02d}": f"{1_000_000 + index}" for index in range(9)} | {
        "MSFT": MSFT_CIK
    }


def _exchange_rows(symbols: dict[str, str]) -> tuple[tuple[int, str, str, str], ...]:
    return tuple(
        (int(cik), f"{symbol} Inc.", symbol, "Nasdaq") for symbol, cik in symbols.items()
    )


def test_refresh_reads_identity_and_carries_every_fact(tmp_path: Path) -> None:
    known = _ten_symbols()
    survivors = {key: value for key, value in known.items() if key != "MSFT"}
    rows = _exchange_rows(survivors) + ((int(NEWCO_CIK), "Newco Inc.", "NEWCO", "NYSE"),)
    web = Web()
    web.route(TICKERS_EXCHANGE_URL, exchange_document(rows))
    source = tmp_path / "engine-snapshot.json"
    output = tmp_path / "engine-snapshot.new.json"
    _write_snapshot(source, known)

    report = asyncio.run(
        refresh_identity(source, output=output, client=web.client(), as_of=AS_OF)
    )

    # One bulk identity document, one request: no issuer document is re-read.
    assert web.urls() == [TICKERS_EXCHANGE_URL]
    assert report["symbols_before"] == 10
    assert report["symbols_after"] == 10
    assert report["symbols_added"] == ["NEWCO"]
    assert report["symbols_added_count"] == 1
    assert report["symbols_removed"] == ["MSFT"]
    assert report["symbols_removed_count"] == 1
    assert report["facts_carried"] == 9
    assert report["facts_dropped_count"] == 1
    assert report["facts_read_from_network"] == 0
    assert report["verified"] is True
    assert "identity" in str(report["note"])

    refreshed = EngineStore(output).load()
    assert sorted(refreshed.security_master.symbols()) == sorted([*survivors, "NEWCO"])
    # NEWCO is known and honestly fact-less: the crawl is what fills it.
    assert "NEWCO" not in refreshed.facts
    # The carried cell is the same evidence, not a relabelled copy.
    carried = refreshed.facts["SYM00"]["pe"]
    assert carried.provenance is not None
    assert carried.provenance.as_of == AS_OF
    assert carried.provenance.source == "sec"
    assert refreshed.fetched_at != FETCHED_AT, "this run is the new observation"


def test_refresh_leaves_the_source_snapshot_untouched_when_asked_to(tmp_path: Path) -> None:
    known = _ten_symbols()
    web = Web()
    web.route(
        TICKERS_EXCHANGE_URL,
        exchange_document(_exchange_rows({k: v for k, v in known.items() if k != "MSFT"})),
    )
    source = tmp_path / "engine-snapshot.json"
    output = tmp_path / "engine-snapshot.new.json"
    _write_snapshot(source, known)
    before = source.read_bytes()

    asyncio.run(refresh_identity(source, output=output, client=web.client(), as_of=AS_OF))

    assert source.read_bytes() == before, "an operator verifies before swapping"
    assert output.exists()


def test_refresh_writes_in_place_when_no_output_is_named(tmp_path: Path) -> None:
    web = Web()
    web.route(
        TICKERS_EXCHANGE_URL,
        exchange_document(
            (
                (320193, "Apple Inc.", "AAPL", "Nasdaq"),
                (789019, "Microsoft Corp", "MSFT", "Nasdaq"),
            )
        ),
    )
    source = tmp_path / "engine-snapshot.json"
    _write_snapshot(source, {"AAPL": AAPL_CIK, "MSFT": MSFT_CIK})

    report = asyncio.run(refresh_identity(source, client=web.client(), as_of=AS_OF))

    assert report["target"] == str(source)
    assert sorted(EngineStore(source).load().security_master.symbols()) == ["AAPL", "MSFT"]


# ---------------------------------------------------------------------------
# fail-closed: a broken read never becomes the served file
# ---------------------------------------------------------------------------


def test_a_truncated_identity_document_is_refused_before_anything_is_written(
    tmp_path: Path,
) -> None:
    """Twenty known symbols, one survivor: that is a broken read, not a delisting."""

    web = Web()
    web.route(TICKERS_EXCHANGE_URL, exchange_document(((320193, "Apple Inc.", "AAPL", "Nasdaq"),)))
    source = tmp_path / "engine-snapshot.json"
    output = tmp_path / "engine-snapshot.new.json"
    _write_snapshot(
        source, {f"SYM{index:02d}": f"{2_000_000 + index}" for index in range(20)}
    )

    with pytest.raises(SecIngestError) as raised:
        asyncio.run(refresh_identity(source, output=output, client=web.client(), as_of=AS_OF))

    assert "identity_refresh_implausible" in str(raised.value)
    assert not output.exists(), "a refused refresh writes nothing"


def test_an_identity_read_that_answers_nothing_is_refused(tmp_path: Path) -> None:
    web = Web()
    web.status(TICKERS_EXCHANGE_URL, 500)
    web.status(TICKERS_URL, 500)
    source = tmp_path / "engine-snapshot.json"
    output = tmp_path / "engine-snapshot.new.json"
    _write_snapshot(source, {"AAPL": AAPL_CIK})

    with pytest.raises(SecIngestError):
        asyncio.run(refresh_identity(source, output=output, client=web.client(), as_of=AS_OF))

    assert not output.exists()


def test_the_saved_file_is_loaded_back_before_the_report_says_verified(tmp_path: Path) -> None:
    """The digest is the contract: a write that does not verify is not published."""

    web = Web()
    web.route(TICKERS_EXCHANGE_URL, exchange_document(((320193, "Apple Inc.", "AAPL", "Nasdaq"),)))
    source = tmp_path / "engine-snapshot.json"
    output = tmp_path / "engine-snapshot.new.json"
    _write_snapshot(source, {"AAPL": AAPL_CIK})

    report = asyncio.run(
        refresh_identity(source, output=output, client=web.client(), as_of=AS_OF)
    )

    body = json.loads(output.read_text(encoding="utf-8"))
    assert body["schema_version"] == 1
    assert report["verified"] is True
    assert len(EngineStore(output).load().security_master.symbols()) == 1


def test_refresh_carries_enrichment_the_identity_documents_do_not_state(
    tmp_path: Path,
) -> None:
    """``country``/``sector``/``industry`` come from submissions, not identity.

    Rebuilding identity from the bulk document alone dropped 5,389 countries and
    5,379 industries on the live snapshot. The refresh carries them, and the old
    provenance travels with the value so the field still names its document.
    """

    known = _ten_symbols()
    survivors = {key: value for key, value in known.items() if key != "MSFT"}
    web = Web()
    web.route(TICKERS_EXCHANGE_URL, exchange_document(_exchange_rows(survivors)))
    source = tmp_path / "engine-snapshot.json"
    output = tmp_path / "engine-snapshot.new.json"
    _write_snapshot(source, known, countries={"SYM00": "United States", "SYM01": "Canada"})

    report = asyncio.run(
        refresh_identity(source, output=output, client=web.client(), as_of=AS_OF)
    )

    assert report["enrichment_carried"] == 2
    assert report["enrichment_conflicts"] == []
    refreshed = EngineStore(output).load()
    record = refreshed.security_master.require("SYM00")
    assert record.country == "United States"
    assert record.industry == "Software"
    # The snapshot format itself records ONE provenance for the whole file
    # (``snapshot``, stamped at save/load), so the carried fields keep their
    # original document only inside the refresh run. The values are what the file
    # carries; the per-field history was never in this format.
    assert [item.source for item in refreshed.security_master.provenance("SYM00")] == [
        "snapshot"
    ]
