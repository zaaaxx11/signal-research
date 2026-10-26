"""RED contract for R3 §1: the bulk SEC identity document and its precedence.

Engine v2 resolves one registrant at a time from ``company_tickers.json``, which
carries no venue. R3 moves the **universe** onto one official document,
``company_tickers_exchange.json``: ``fields`` + ``data``, one row per registrant,
with the venue SEC itself publishes. These tests pin four surfaces:

* the parser maps **by field name**, so a reordered ``fields`` array still
  parses, and it drops a row or a ticker it cannot identify instead of repairing
  it;
* the venue → MIC additions (§1.1) are exact, and a venue without a verified MIC
  keeps its raw name with ``mic=None`` rather than a guess;
* ``load_sec_dataset`` reads the bulk document first, falls back to the legacy
  index only on a failure or an empty parse, and raises when both are unusable;
* a snapshot round-trip keeps the ``industry``/``country`` a submissions body
  fills, under the unchanged schema.

At the R3 baseline the new helpers are stubs and the load still reads only the
legacy index, so every test here fails for behaviour. Nothing opens a socket:
``httpx2.MockTransport`` answers each URL and records every request, which is
how a test can prove *which* identity documents the load asked for.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any

import httpx2
import pytest

from app.engine.ingest import (
    EngineDataset,
    SecIngestError,
    fetch_company_tickers_exchange,
    load_sec_dataset,
)
from app.engine.model import CellStatus, Provenance, SecurityRecord
from app.engine.security import SecurityMaster
from app.engine.sources.sec import (
    TICKERS_EXCHANGE_URL,
    TICKERS_URL,
    exchange_mic,
    parse_company_tickers_exchange,
)
from app.engine.store import SCHEMA_VERSION, EngineStore

AS_OF = "2026-10-26"
FETCHED_AT = "2026-10-26T12:00:00+00:00"

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

AAPL_CIK = "0000320193"
MSFT_CIK = "0000789019"

#: EDGAR's own column order in ``company_tickers_exchange.json``.
CANONICAL_FIELDS: tuple[str, ...] = ("cik", "name", "ticker", "exchange")

#: Rows deliberately out of symbol order: the parser must sort its output.
BULK_ROWS: tuple[tuple[object, ...], ...] = (
    (789019, "Microsoft Corp", "MSFT", "NYSE"),
    (320193, "Apple Inc.", "AAPL", "Nasdaq"),
    (1067983, "Berkshire Hathaway Inc", "BRK-B", "NYSE"),
)

#: The legacy document, used only by the fallback tests.
INDEX: dict[str, dict[str, object]] = {
    "0": {"cik_str": 320193, "ticker": "AAPL", "title": "APPLE INC."},
    "1": {"cik_str": 789019, "ticker": "MSFT", "title": "MICROSOFT CORP"},
}


def exchange_document(
    rows: Iterable[Sequence[object]] = (),
    *,
    fields: Sequence[str] = CANONICAL_FIELDS,
) -> dict[str, object]:
    """A ``company_tickers_exchange.json`` body: named columns, positional rows.

    The live document is exactly this shape, so a helper that keeps the header
    explicit lets a test reorder the columns without rewriting the rows.
    """

    return {"fields": list(fields), "data": [list(row) for row in rows]}


def minimal_facts(value: float = 7.0) -> dict[str, Any]:
    """A tiny but structurally real ``companyfacts`` body for Apple."""

    return {
        "cik": 320193,
        "entityName": "APPLE INC.",
        "facts": {
            "us-gaap": {
                "Assets": {
                    "label": "Assets",
                    "units": {
                        "USD": [
                            {
                                "end": "2026-06-30",
                                "val": value,
                                "accn": "0000320193-26-000001",
                                "fy": 2026,
                                "fp": "Q3",
                                "form": "10-Q",
                                "filed": "2026-07-31",
                            }
                        ]
                    },
                }
            }
        },
    }


class Web:
    """A recording ``httpx2.MockTransport`` keyed by exact URL.

    An unrouted request is answered ``404`` and still recorded, so a test can
    never pass because the load silently asked the wrong host.
    """

    def __init__(self) -> None:
        self.requests: list[httpx2.Request] = []
        self._routes: dict[str, Callable[[httpx2.Request], httpx2.Response]] = {}
        self._transport = httpx2.MockTransport(self._handle)

    def json(self, url: str, payload: Any, *, status: int = 200) -> None:
        """Answer ``url`` with ``payload`` as JSON."""

        self._routes[url] = lambda _request: httpx2.Response(status, json=payload)

    def status(self, url: str, status: int) -> None:
        """Answer ``url`` with an error status and a small JSON body."""

        self._routes[url] = lambda _request: httpx2.Response(
            status, json={"error": status}
        )

    def _handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        responder = self._routes.get(str(request.url))
        if responder is None:
            return httpx2.Response(
                404, json={"error": "unrouted", "url": str(request.url)}
            )
        return responder(request)

    def client(self) -> httpx2.AsyncClient:
        """A client whose every request passes through this transport."""

        return httpx2.AsyncClient(transport=self._transport, follow_redirects=False)

    def urls(self) -> list[str]:
        """Every requested URL, in request order."""

        return [str(request.url) for request in self.requests]


# ---------------------------------------------------------------------------
# Parsing company_tickers_exchange.json
# ---------------------------------------------------------------------------


def test_parse_company_tickers_exchange_reads_the_named_columns_in_symbol_order() -> None:
    """One row is one registrant: ticker, CIK, name and the raw venue."""

    records = parse_company_tickers_exchange(exchange_document(BULK_ROWS))

    assert [record.symbol for record in records] == ["AAPL", "BRK-B", "MSFT"]

    apple = records[0]
    assert apple.cik == AAPL_CIK
    assert apple.name == "Apple Inc."
    assert apple.exchange == "Nasdaq"
    assert apple.mic == "XNAS"
    assert apple.asset_class == "equity"
    assert apple.currency == "USD"
    assert apple.active is True
    # The document states identity, not classification: no field is invented.
    assert apple.country is None
    assert apple.sector is None
    assert apple.industry is None


def test_parse_company_tickers_exchange_maps_a_reordered_fields_array_by_name() -> None:
    """Position is presentation; only the ``fields`` header binds a value."""

    canonical = exchange_document(((320193, "Apple Inc.", "AAPL", "Nasdaq"),))
    reordered = {
        "fields": ["exchange", "ticker", "name", "cik"],
        "data": [["Nasdaq", "AAPL", "Apple Inc.", 320193]],
    }

    assert parse_company_tickers_exchange(reordered) == (
        parse_company_tickers_exchange(canonical)
    )

    record = parse_company_tickers_exchange(reordered)[0]
    assert (record.symbol, record.cik, record.name, record.exchange) == (
        "AAPL",
        AAPL_CIK,
        "Apple Inc.",
        "Nasdaq",
    )


def test_parse_company_tickers_exchange_keeps_a_venue_without_a_verified_mic() -> None:
    """The document's venue string survives even when no MIC can be proven."""

    records = parse_company_tickers_exchange(
        exchange_document(
            (
                (41, "Texas Listing Co", "TXL", "TXSE"),
                (42, "Unlisted Venue Co", "NOVN", None),
            )
        )
    )

    by_symbol = {record.symbol: record for record in records}
    assert by_symbol["TXL"].exchange == "TXSE"
    assert by_symbol["TXL"].mic is None
    assert by_symbol["NOVN"].exchange is None
    assert by_symbol["NOVN"].mic is None


@pytest.mark.parametrize(
    ("exchange", "expected"),
    [
        ("Nasdaq", "XNAS"),
        ("NYSE", "XNYS"),
        ("OTC", "OTCM"),
        ("CBOE", "XCBO"),
        ("TXSE", None),
        (None, None),
    ],
)
def test_exchange_mic_adds_the_r3_venues_without_touching_the_known_ones(
    exchange: object, expected: str | None
) -> None:
    """R3 adds OTC/CBOE and refuses to guess a MIC for TXSE."""

    assert exchange_mic(exchange) == expected


def test_parse_company_tickers_exchange_skips_rows_without_an_identity() -> None:
    """A blank ticker, a bad CIK or a blank name is a parse error, not a row."""

    records = parse_company_tickers_exchange(
        exchange_document(
            (
                (320193, "Apple Inc.", "   ", "Nasdaq"),
                (0, "Zero Cik", "ZERO", "NYSE"),
                ("not-a-cik", "Bad Cik", "BADCIK", "NYSE"),
                (None, "No Cik", "NOCIK", "NYSE"),
                (4, "   ", "NONAME", "NYSE"),
                (5, "Space Ticker", "HAS SPACE", "NYSE"),
                (6, "Good Co", "GOOD", "NYSE"),
            )
        )
    )

    assert [(record.symbol, record.cik) for record in records] == [
        ("GOOD", "0000000006")
    ]


def test_parse_company_tickers_exchange_drops_a_ticker_bound_to_two_ciks() -> None:
    """An ambiguous ticker is no identity; the same CIK twice keeps the first."""

    records = parse_company_tickers_exchange(
        exchange_document(
            (
                (11, "Dup First", "DUP", "Nasdaq"),
                (22, "Dup Second", "DUP", "NYSE"),
                (33, "One", "ONE", "NYSE"),
                (33, "One Again", "ONE", "Nasdaq"),
            )
        )
    )

    assert [
        (record.symbol, record.cik, record.name, record.exchange)
        for record in records
    ] == [("ONE", "0000000033", "One", "NYSE")]


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        "nope",
        3,
        {},
        {"fields": list(CANONICAL_FIELDS)},
        {"data": [[320193, "Apple Inc.", "AAPL", "Nasdaq"]]},
        {"fields": "cik", "data": []},
        {"fields": list(CANONICAL_FIELDS), "data": {}},
    ],
)
def test_parse_company_tickers_exchange_returns_nothing_for_another_document(
    payload: object,
) -> None:
    """A body of the wrong JSON shape is an empty parse, not an exception."""

    assert parse_company_tickers_exchange(payload) == ()


async def test_fetch_company_tickers_exchange_reads_the_bulk_document() -> None:
    """The fetcher targets the one official URL with a contactable User-Agent."""

    web = Web()
    document = exchange_document(BULK_ROWS)
    web.json(TICKERS_EXCHANGE_URL, document)

    payload = await fetch_company_tickers_exchange(client=web.client())

    assert payload == document
    assert web.urls() == [TICKERS_EXCHANGE_URL]
    assert "@" in web.requests[0].headers["user-agent"]
    assert web.requests[0].headers["accept"] == "application/json"


# ---------------------------------------------------------------------------
# load_sec_dataset: the bulk document is the identity authority
# ---------------------------------------------------------------------------


async def test_load_takes_the_universe_from_one_exchange_document() -> None:
    """MSFT is never requested, so its venue proves the document is bulk."""

    web = Web()
    web.json(
        TICKERS_EXCHANGE_URL,
        exchange_document(
            (
                (320193, "Apple Inc.", "AAPL", "Nasdaq"),
                (789019, "Microsoft Corp", "MSFT", "NYSE"),
            )
        ),
    )
    web.json(FACTS_URL.format(cik=AAPL_CIK), minimal_facts())

    try:
        dataset = await load_sec_dataset(["AAPL"], client=web.client(), as_of=AS_OF)
    except SecIngestError as exc:
        raise AssertionError(
            "the load must build its universe from company_tickers_exchange.json "
            f"alone, but it raised {type(exc).__name__}: {exc}"
        ) from exc

    assert dataset.facts["AAPL"]["assets"].value == pytest.approx(7.0)
    assert dataset.errors == {}

    apple = dataset.security_master.require("AAPL")
    microsoft = dataset.security_master.require("MSFT")
    assert (apple.exchange, apple.mic) == ("Nasdaq", "XNAS")
    assert (microsoft.exchange, microsoft.mic) == ("NYSE", "XNYS"), (
        "MSFT was never requested: its venue can only come from the bulk document"
    )
    assert dataset.security_master.symbols() == ("AAPL", "MSFT")

    assert web.urls().count(TICKERS_EXCHANGE_URL) == 1
    assert TICKERS_URL not in web.urls(), (
        "the legacy index is a fallback, not a document the load reads anyway"
    )


async def test_a_broken_exchange_document_falls_back_to_the_legacy_index() -> None:
    """A 503 on the bulk document is a fallback, not an error row."""

    web = Web()
    web.status(TICKERS_EXCHANGE_URL, 503)
    web.json(TICKERS_URL, INDEX)
    web.json(
        SUBMISSIONS_URL.format(cik=AAPL_CIK),
        {
            "cik": AAPL_CIK,
            "name": "Apple Inc.",
            "tickers": ["AAPL"],
            "exchanges": ["Nasdaq"],
        },
    )
    web.json(FACTS_URL.format(cik=AAPL_CIK), minimal_facts())

    dataset = await load_sec_dataset(["AAPL"], client=web.client(), as_of=AS_OF)

    assert dataset.errors == {}, "a fallback is a recovery, not a failed symbol"
    assert dataset.facts["AAPL"]["assets"].value == pytest.approx(7.0)
    assert dataset.security_master.require("AAPL").exchange == "Nasdaq"
    assert dataset.security_master.require("MSFT").exchange is None, (
        "the legacy index carries no venue, and the requested symbol's "
        "submissions body cannot speak for a symbol that was never requested"
    )

    assert web.urls()[0] == TICKERS_EXCHANGE_URL
    assert web.urls().count(TICKERS_EXCHANGE_URL) == 1
    assert web.urls().count(TICKERS_URL) == 1


async def test_an_empty_exchange_document_falls_back_to_the_legacy_index() -> None:
    """A 200 with no usable row is as unusable as a failed fetch."""

    web = Web()
    web.json(TICKERS_EXCHANGE_URL, exchange_document())
    web.json(TICKERS_URL, INDEX)
    web.json(FACTS_URL.format(cik=AAPL_CIK), minimal_facts())

    dataset = await load_sec_dataset(["AAPL"], client=web.client(), as_of=AS_OF)

    assert dataset.errors == {}
    assert set(dataset.facts) == {"AAPL"}
    assert dataset.security_master.get("MSFT") is not None
    assert web.urls()[0] == TICKERS_EXCHANGE_URL
    assert web.urls().count(TICKERS_URL) == 1


async def test_two_broken_identity_documents_fail_the_load() -> None:
    """Without either identity document there is no universe to load."""

    web = Web()
    web.status(TICKERS_EXCHANGE_URL, 503)
    web.status(TICKERS_URL, 503)

    with pytest.raises(SecIngestError):
        await load_sec_dataset(["AAPL"], client=web.client())

    assert web.urls() == [TICKERS_EXCHANGE_URL, TICKERS_URL], (
        "the bulk document is attempted first, then the legacy index; with "
        "neither there is no identity, so no issuer document may be requested"
    )


# ---------------------------------------------------------------------------
# Snapshot: industry and country survive a round-trip
# ---------------------------------------------------------------------------


def test_a_snapshot_round_trip_keeps_industry_and_country(tmp_path: Path) -> None:
    """The store already has the keys; R3 must not lose what submissions fills."""

    master = SecurityMaster()
    master.add(
        SecurityRecord(
            symbol="AAPL",
            name="Apple Inc.",
            cik=AAPL_CIK,
            exchange="Nasdaq",
            mic="XNAS",
            asset_class="equity",
            currency="USD",
            country="United States",
            sector=None,
            industry="Electronic Computers",
        ),
        Provenance(
            source="sec",
            as_of=AS_OF,
            status=CellStatus.OK,
            url=TICKERS_EXCHANGE_URL,
        ),
    )
    dataset = EngineDataset(
        fetched_at=FETCHED_AT,
        security_master=master,
        facts={},
        errors={},
    )
    path = tmp_path / "snapshot.json"

    EngineStore(path).save(dataset)
    restored = EngineStore(path).load().security_master.require("AAPL")

    assert restored.industry == "Electronic Computers"
    assert restored.country == "United States"
    assert restored.exchange == "Nasdaq"
    assert restored.sector is None
    assert SCHEMA_VERSION == 1, (
        "industry and country already have keys: R3 adds no schema change"
    )
