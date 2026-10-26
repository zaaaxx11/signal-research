"""Contract for the SEC ingestion slice (``app.engine.ingest``).

The slice exists to answer one question: **what documents did we read, and what
did they say?** Everything here is about keeping that answer honest:

* the three fetchers call the URLs EDGAR publishes, with a contactable
  ``User-Agent``, and never follow a redirect;
* a document read from the disk cache does not touch the network at all;
* one issuer's failure is that issuer's error row — the other symbols still
  load, and no number is invented for the failed one;
* ``EngineDataset`` is a value: frozen fields, read-only mappings, and a copy
  taken at construction so a later mutation of the caller's dict cannot change
  history.

The documents are real: ``tests/fixtures/sec_companyfacts_aapl.json`` is the
trimmed ``companyfacts`` body for Apple (CIK 0000320193) that the facts and
adapter suites also read. The index and the synthetic bodies are handwritten
shapes of the same public documents. Every request goes through
``httpx2.MockTransport``; the suite-wide socket guard in ``tests/conftest.py``
would fail the test if anything opened a connection.
"""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import httpx2
import pytest

from app.engine.facts import fact_ttm_summary
from app.engine.ingest import (
    EngineDataset,
    SecIngestError,
    fetch_companyfacts,
    fetch_company_tickers,
    fetch_submissions,
    load_sec_dataset,
)
from app.engine.security import SecurityMaster
from app.providers import http as provider_http

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "sec_companyfacts_aapl.json"

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

AAPL_CIK = "0000320193"
MSFT_CIK = "0000789019"
BRK_CIK = "0001067983"

#: Three registrants, shaped exactly like EDGAR's object keyed by index strings.
INDEX: dict[str, dict[str, Any]] = {
    "0": {"cik_str": 320193, "ticker": "AAPL", "title": "APPLE INC."},
    "1": {"cik_str": 789019, "ticker": "MSFT", "title": "MICROSOFT CORP"},
    "2": {"cik_str": 1067983, "ticker": "BRK-B", "title": "BERKSHIRE HATHAWAY INC"},
}

#: AAPL's TTM revenue in the real fixture: FY2025 + 9M FY2026 - 9M FY2025.
AAPL_REVENUE = 466_823_000_000.0


def aapl_facts() -> dict[str, Any]:
    """A fresh copy of the real Apple ``companyfacts`` document."""

    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def minimal_facts(cik: int, *, value: float = 7.0) -> dict[str, Any]:
    """A tiny but structurally real ``companyfacts`` body for one issuer."""

    return {
        "cik": cik,
        "entityName": f"ISSUER {cik}",
        "facts": {
            "us-gaap": {
                "Assets": {
                    "label": "Assets",
                    "units": {
                        "USD": [
                            {
                                "end": "2026-06-30",
                                "val": value,
                                "accn": f"{cik:010d}-26-000001",
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


def submissions_body(cik: int, *, tickers: list[str], exchanges: list[str]) -> dict[str, Any]:
    """A submissions document with the fields the parser reads."""

    return {
        "cik": f"{cik:010d}",
        "name": "ISSUER",
        "tickers": tickers,
        "exchanges": exchanges,
    }


class Web:
    """A recording ``httpx2.MockTransport`` keyed by exact URL.

    An unrouted request is answered ``404`` and still recorded, so a test can
    never pass because the code silently asked the wrong host.
    """

    def __init__(self) -> None:
        self.requests: list[httpx2.Request] = []
        self._routes: dict[str, Callable[[httpx2.Request], httpx2.Response]] = {}
        self._transport = httpx2.MockTransport(self._handle)

    # -- routing -----------------------------------------------------------

    def route(
        self, url: str, responder: Callable[[httpx2.Request], httpx2.Response]
    ) -> None:
        self._routes[url] = responder

    def json(self, url: str, payload: Any, *, status: int = 200) -> None:
        self.route(url, lambda _request: httpx2.Response(status, json=payload))

    def status(self, url: str, status: int) -> None:
        self.route(url, lambda _request: httpx2.Response(status, json={"error": status}))

    # -- transport ---------------------------------------------------------

    def _handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        responder = self._routes.get(str(request.url))
        if responder is None:
            return httpx2.Response(
                404, json={"error": "unrouted", "url": str(request.url)}
            )
        return responder(request)

    def client(self) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(transport=self._transport, follow_redirects=False)

    # -- reads -------------------------------------------------------------

    def urls(self) -> list[str]:
        return [str(request.url) for request in self.requests]


def cache_path(cache_dir: Path, cik: str = AAPL_CIK) -> Path:
    """The cache entry this slice promises for one issuer's facts document."""

    return cache_dir / f"CIK{cik}-companyfacts.json"


# ---------------------------------------------------------------------------
# EngineDataset: an immutable value
# ---------------------------------------------------------------------------


def test_engine_dataset_freezes_its_fields_and_its_mappings() -> None:
    source = {"AAPL": {}}
    dataset = EngineDataset(
        fetched_at="2026-10-26T12:00:00+00:00",
        security_master=SecurityMaster(),
        facts=source,
        errors={"ZZZZ": "unknown"},
    )

    with pytest.raises(FrozenInstanceError):
        dataset.fetched_at = "2026-10-27T00:00:00+00:00"  # type: ignore[misc]
    with pytest.raises(TypeError):
        dataset.facts["AAPL"] = {}  # type: ignore[index]
    with pytest.raises(TypeError):
        dataset.errors["AAPL"] = "later"  # type: ignore[index]

    source["MSFT"] = {}
    assert "MSFT" not in dataset.facts, (
        "the dataset must copy its mappings: a caller's later write cannot "
        "retroactively change what a completed load reported"
    )


def test_engine_dataset_rejects_a_blank_timestamp_or_foreign_master() -> None:
    with pytest.raises(ValueError):
        EngineDataset(
            fetched_at="   ",
            security_master=SecurityMaster(),
            facts={},
            errors={},
        )
    with pytest.raises(TypeError):
        EngineDataset(
            fetched_at="2026-10-26T12:00:00+00:00",
            security_master={},  # type: ignore[arg-type]
            facts={},
            errors={},
        )
    with pytest.raises(TypeError):
        EngineDataset(
            fetched_at="2026-10-26T12:00:00+00:00",
            security_master=SecurityMaster(),
            facts={"AAPL": "not cells"},  # type: ignore[dict-item]
            errors={},
        )


# ---------------------------------------------------------------------------
# The three fetchers
# ---------------------------------------------------------------------------


async def test_fetch_company_tickers_calls_the_index_with_a_contact_user_agent() -> None:
    web = Web()
    web.json(TICKERS_URL, INDEX)

    payload = await fetch_company_tickers(client=web.client())

    assert payload == INDEX
    assert web.urls() == [TICKERS_URL]
    user_agent = web.requests[0].headers["user-agent"]
    assert user_agent.strip()
    assert "@" in user_agent, (
        "SEC answers 403 to an anonymous client; the User-Agent must carry a "
        f"contactable identity, got {user_agent!r}"
    )
    assert web.requests[0].headers["accept"] == "application/json"


async def test_fetch_submissions_pads_the_cik_in_the_path() -> None:
    web = Web()
    web.json(
        SUBMISSIONS_URL.format(cik=AAPL_CIK),
        submissions_body(320193, tickers=["AAPL"], exchanges=["Nasdaq"]),
    )

    payload = await fetch_submissions(320193, client=web.client())

    assert payload["cik"] == AAPL_CIK
    assert web.urls() == [SUBMISSIONS_URL.format(cik=AAPL_CIK)]


async def test_fetch_companyfacts_pads_the_cik_in_the_xbrl_path() -> None:
    web = Web()
    web.json(FACTS_URL.format(cik=AAPL_CIK), aapl_facts())

    payload = await fetch_companyfacts("0000320193", client=web.client())

    assert payload["cik"] == 320193
    assert web.urls() == [FACTS_URL.format(cik=AAPL_CIK)]


@pytest.mark.parametrize("cik", ["AAPL", "", 0, -1, True, None])
async def test_the_cik_fetchers_refuse_a_value_that_is_not_a_cik(cik: Any) -> None:
    web = Web()

    with pytest.raises(ValueError):
        await fetch_companyfacts(cik, client=web.client())
    with pytest.raises(ValueError):
        await fetch_submissions(cik, client=web.client())

    assert web.urls() == [], "an invalid identifier must fail before any request"


async def test_an_injected_client_is_used_and_left_open() -> None:
    web = Web()
    web.json(TICKERS_URL, INDEX)
    client = web.client()

    await fetch_company_tickers(client=client)

    assert not client.is_closed, (
        "the fetcher must not close a client it did not build: the injected "
        "client belongs to the caller"
    )
    await client.aclose()


async def test_a_fetch_without_a_client_builds_one_through_the_module_seam(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    web = Web()
    web.json(TICKERS_URL, INDEX)
    built: list[httpx2.AsyncClient] = []
    captured: dict[str, Any] = {}

    def build_client(**kwargs: Any) -> httpx2.AsyncClient:
        captured.update(kwargs)
        client = web.client()
        built.append(client)
        return client

    monkeypatch.setattr(provider_http, "build_client", build_client)

    payload = await fetch_company_tickers()

    assert payload == INDEX
    assert len(built) == 1
    assert built[0].is_closed, "a client the fetcher opened must be closed again"
    assert "@" in str((captured.get("headers") or {}).get("user-agent") or "")


@pytest.mark.parametrize("user_agent", ["", "   ", "ztockz-agent"])
async def test_a_user_agent_without_a_contact_is_refused(user_agent: str) -> None:
    web = Web()
    web.json(TICKERS_URL, INDEX)

    with pytest.raises(ValueError):
        await fetch_company_tickers(client=web.client(), user_agent=user_agent)

    assert web.urls() == [], "the policy check belongs before the request"


async def test_an_http_failure_is_one_domain_error_naming_the_url() -> None:
    web = Web()
    web.status(FACTS_URL.format(cik=AAPL_CIK), 503)

    with pytest.raises(SecIngestError) as excinfo:
        await fetch_companyfacts(AAPL_CIK, client=web.client())

    message = str(excinfo.value)
    assert FACTS_URL.format(cik=AAPL_CIK) in message
    assert "503" in message


async def test_a_non_json_body_is_a_domain_error_not_a_payload() -> None:
    web = Web()
    web.route(
        TICKERS_URL,
        lambda _request: httpx2.Response(
            200, text="<!DOCTYPE html><html><body>maintenance</body></html>"
        ),
    )

    with pytest.raises(SecIngestError):
        await fetch_company_tickers(client=web.client())


# ---------------------------------------------------------------------------
# load_sec_dataset: identity + facts from real documents
# ---------------------------------------------------------------------------


async def test_load_builds_identity_and_real_aapl_facts() -> None:
    web = Web()
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
    web.json(FACTS_URL.format(cik=AAPL_CIK), aapl_facts())

    dataset = await load_sec_dataset(["AAPL"], client=web.client(), as_of="2026-07-31")

    record = dataset.security_master.get("AAPL")
    assert record is not None
    assert record.cik == AAPL_CIK
    assert record.name == "APPLE INC."
    assert record.exchange == "Nasdaq"
    assert dataset.facts["AAPL"] == fact_ttm_summary(aapl_facts())
    assert dataset.errors == {}

    revenue = dataset.facts["AAPL"]["revenue"]
    assert revenue.value == pytest.approx(AAPL_REVENUE)
    assert revenue.provenance is not None
    assert revenue.provenance.source == "sec"
    assert revenue.provenance.as_of == "2026-06-27"

    assert dataset.security_master.provenance("AAPL")[0].as_of == "2026-07-31"
    assert datetime.fromisoformat(dataset.fetched_at).tzinfo is not None
    assert all("@" in request.headers["user-agent"] for request in web.requests), (
        "every EDGAR request carries the contactable User-Agent, not just the index"
    )

    # The index is the identity authority for the whole load, not for one row.
    assert dataset.security_master.symbols() == ("AAPL", "BRK-B", "MSFT")


async def test_screen_spelling_and_duplicates_resolve_to_one_instrument() -> None:
    web = Web()
    web.json(TICKERS_URL, INDEX)
    web.json(FACTS_URL.format(cik=AAPL_CIK), aapl_facts())

    dataset = await load_sec_dataset([" aapl ", "AAPL", "aapl"], client=web.client())

    assert list(dataset.facts) == ["AAPL"]
    assert dataset.errors == {}
    assert web.urls().count(FACTS_URL.format(cik=AAPL_CIK)) == 1, (
        "one instrument is one document: duplicates in the request must not "
        "spend a second request"
    )


async def test_a_dot_share_class_is_fetched_under_the_edgar_spelling() -> None:
    web = Web()
    web.json(TICKERS_URL, INDEX)
    web.json(FACTS_URL.format(cik=BRK_CIK), minimal_facts(1067983))

    dataset = await load_sec_dataset(["brk.b"], client=web.client())

    assert set(dataset.facts) == {"BRK-B"}
    assert dataset.errors == {}
    assert dataset.security_master.get("BRK-B") is not None


async def test_two_classes_of_one_issuer_share_one_cik_document() -> None:
    index = {
        "0": {"cik_str": 1067983, "ticker": "BRK-A", "title": "BERKSHIRE HATHAWAY INC"},
        "1": {"cik_str": 1067983, "ticker": "BRK-B", "title": "BERKSHIRE HATHAWAY INC"},
    }
    web = Web()
    web.json(TICKERS_URL, index)
    web.json(FACTS_URL.format(cik=BRK_CIK), minimal_facts(1067983))

    dataset = await load_sec_dataset(["BRK-A", "BRK-B"], client=web.client())

    assert set(dataset.facts) == {"BRK-A", "BRK-B"}
    assert dataset.facts["BRK-A"] == dataset.facts["BRK-B"]
    assert web.urls().count(FACTS_URL.format(cik=BRK_CIK)) == 1, (
        "the cache and the request budget are keyed by CIK because the "
        "document is per issuer, not per share class"
    )


async def test_partial_failures_are_recorded_per_symbol_without_facts() -> None:
    web = Web()
    web.json(TICKERS_URL, INDEX)
    web.json(FACTS_URL.format(cik=AAPL_CIK), aapl_facts())
    web.status(FACTS_URL.format(cik=MSFT_CIK), 500)

    dataset = await load_sec_dataset(
        ["AAPL", "MSFT", "ZZZZ"], client=web.client()
    )

    assert set(dataset.facts) == {"AAPL"}
    assert set(dataset.errors) == {"MSFT", "ZZZZ"}
    assert "MSFT" not in dataset.facts, "a failed fetch has no facts, not empty ones"
    assert "500" in dataset.errors["MSFT"]
    assert dataset.security_master.get("ZZZZ") is None, (
        "an unknown symbol does not become an instrument just because it was asked for"
    )
    assert "index" in dataset.errors["ZZZZ"]


async def test_a_non_companyfacts_body_is_an_error_not_an_empty_result() -> None:
    web = Web()
    web.json(TICKERS_URL, INDEX)
    web.json(FACTS_URL.format(cik=AAPL_CIK), {"error": "rate limited"})

    dataset = await load_sec_dataset(["AAPL"], client=web.client())

    assert "AAPL" in dataset.errors
    assert "AAPL" not in dataset.facts


async def test_a_redirect_is_a_failure_not_a_hop() -> None:
    web = Web()
    web.json(TICKERS_URL, INDEX)
    web.route(
        FACTS_URL.format(cik=AAPL_CIK),
        lambda _request: httpx2.Response(
            302, headers={"location": "https://evil.example/steal.json"}
        ),
    )

    dataset = await load_sec_dataset(["AAPL"], client=web.client())

    assert "AAPL" in dataset.errors
    assert "302" in dataset.errors["AAPL"]
    assert all("evil.example" not in url for url in web.urls())


async def test_an_unavailable_index_fails_the_load_instead_of_inventing_identities() -> None:
    web = Web()
    web.status(TICKERS_URL, 503)

    with pytest.raises(SecIngestError):
        await load_sec_dataset(["AAPL"], client=web.client())

    assert web.urls() == [
        "https://www.sec.gov/files/company_tickers_exchange.json",
        TICKERS_URL,
    ], (
        "the bulk exchange document is attempted first and then the legacy "
        "index; without an index there is no identity, so no issuer document "
        "may be requested and no symbol may be reported as merely unknown"
    )


async def test_an_empty_index_is_a_failure_not_a_universe_of_unknowns() -> None:
    web = Web()
    web.json(TICKERS_URL, {})

    with pytest.raises(SecIngestError):
        await load_sec_dataset(["AAPL"], client=web.client())


# ---------------------------------------------------------------------------
# The disk cache
# ---------------------------------------------------------------------------


async def test_load_refuses_a_bare_string_as_the_universe() -> None:
    web = Web()
    web.json(TICKERS_URL, INDEX)

    with pytest.raises(TypeError):
        await load_sec_dataset("AAPL", client=web.client())  # type: ignore[arg-type]

    assert web.urls() == []


async def test_a_cached_issuer_document_is_not_fetched_again(tmp_path: Path) -> None:
    cache_dir = tmp_path / "sec-cache"
    first_web = Web()
    first_web.json(TICKERS_URL, INDEX)
    first_web.json(FACTS_URL.format(cik=AAPL_CIK), aapl_facts())

    first = await load_sec_dataset(["AAPL"], client=first_web.client(), cache_dir=cache_dir)

    entry = cache_path(cache_dir)
    assert entry.is_file(), "the raw document is cached under a clear CIK filename"
    assert entry.suffix == ".json"
    assert AAPL_CIK in entry.name
    assert json.loads(entry.read_text(encoding="utf-8"))["cik"] == 320193
    assert first_web.urls().count(FACTS_URL.format(cik=AAPL_CIK)) == 1

    second_web = Web()
    second_web.json(TICKERS_URL, INDEX)
    second = await load_sec_dataset(["AAPL"], client=second_web.client(), cache_dir=cache_dir)

    assert second.facts == first.facts
    assert second.errors == {}
    assert FACTS_URL.format(cik=AAPL_CIK) not in second_web.urls(), (
        "a cache hit must not call the client for that document"
    )


async def test_a_corrupt_cache_entry_is_replaced_not_trusted(tmp_path: Path) -> None:
    cache_dir = tmp_path / "sec-cache"
    cache_dir.mkdir()
    entry = cache_path(cache_dir)
    entry.write_text("{ this is not json", encoding="utf-8")

    web = Web()
    web.json(TICKERS_URL, INDEX)
    web.json(FACTS_URL.format(cik=AAPL_CIK), aapl_facts())

    dataset = await load_sec_dataset(["AAPL"], client=web.client(), cache_dir=cache_dir)

    assert dataset.facts["AAPL"]["revenue"].value == pytest.approx(AAPL_REVENUE)
    assert web.urls().count(FACTS_URL.format(cik=AAPL_CIK)) == 1
    assert json.loads(entry.read_text(encoding="utf-8"))["cik"] == 320193


async def test_a_cache_entry_for_another_issuer_is_not_served(tmp_path: Path) -> None:
    cache_dir = tmp_path / "sec-cache"
    cache_dir.mkdir()
    # A valid Apple document sitting under Microsoft's filename is a stale or
    # mis-copied entry; serving it would report Apple's numbers as MSFT's.
    cache_path(cache_dir, MSFT_CIK).write_text(
        json.dumps(aapl_facts()), encoding="utf-8"
    )

    web = Web()
    web.json(TICKERS_URL, INDEX)
    web.json(FACTS_URL.format(cik=MSFT_CIK), minimal_facts(789019))

    dataset = await load_sec_dataset(["MSFT"], client=web.client(), cache_dir=cache_dir)

    assert dataset.facts["MSFT"]["assets"].value == pytest.approx(7.0)
    assert web.urls().count(FACTS_URL.format(cik=MSFT_CIK)) == 1


async def test_a_failed_fetch_writes_no_cache_entry(tmp_path: Path) -> None:
    cache_dir = tmp_path / "sec-cache"
    web = Web()
    web.json(TICKERS_URL, INDEX)
    web.status(FACTS_URL.format(cik=MSFT_CIK), 503)

    dataset = await load_sec_dataset(["MSFT"], client=web.client(), cache_dir=cache_dir)

    assert "MSFT" in dataset.errors
    assert not cache_path(cache_dir, MSFT_CIK).exists(), (
        "a cache is evidence of a document that was read; a failure is not one"
    )
