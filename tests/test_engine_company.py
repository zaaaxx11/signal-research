"""``GET /engine/company/{symbol}`` — the ticker page's Company profile block.

The block is one ``quoteSummary`` ``assetProfile`` read: the business summary
paragraph and the key people, in the order Yahoo publishes them. Every test
here is offline: the transport test drives ``MockWeb`` through the one HTTP
seam, and the route tests inject the fetcher, so the suite never opens a socket.

Two properties matter more than the shape:

* a cache hit is a **served answer**, not a licence to call the network again;
* a refusal with nothing on disk is a ``404 no_company_profile``, never a
  fabricated paragraph.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx2
import pytest

from app.market.company import (
    MAX_OFFICERS,
    CompanyProfile,
    Officer,
    collect_company_profile,
    parse_company_profile,
)

# ---------------------------------------------------------------------------
# A Yahoo-shaped fixture
# ---------------------------------------------------------------------------

APPLE_SUMMARY = (
    "Apple Inc. designs, manufactures, and markets smartphones, personal "
    "computers, tablets, and wearables.\n\nThe Company also sells a range of "
    "related services, including advertising, cloud, and payment services.\n"
)

APPLE_OFFICERS: tuple[dict[str, Any], ...] = (
    {
        "maxAge": 1,
        "name": "Mr. Timothy D. Cook",
        "age": 65,
        "title": "CEO & Director",
        "yearBorn": 1960,
        "fiscalYear": 2025,
        "totalPay": {"raw": 7461000, "fmt": "7.46M"},
        "exercisedValue": {"raw": 0, "fmt": None},
        "unexercisedValue": {"raw": 0, "fmt": None},
    },
    {
        "maxAge": 1,
        "name": "Mr. Luca Maestri",
        "age": 62,
        "title": "Chief Financial Officer & Senior Vice President",
        "yearBorn": 1963,
    },
    {
        "maxAge": 1,
        "name": "Ms. Katherine L. Adams",
        "age": 62,
        "title": "General Counsel & Senior Vice President",
        "yearBorn": 1963,
    },
    {
        "maxAge": 1,
        "name": "Mr. Deirdre O'Brien",
        "age": 59,
        "title": "Senior Vice President of Retail & People",
        "yearBorn": 1966,
    },
    {
        "maxAge": 1,
        "name": "Mr. Jeffrey E. Williams",
        "age": 62,
        "title": "Chief Operating Officer",
        "yearBorn": 1963,
    },
    {
        "maxAge": 1,
        "name": "Ms. Sabih Khan",
        "age": 59,
        "title": "Senior Vice President of Operations",
        "yearBorn": 1966,
    },
    {
        "maxAge": 1,
        "name": "Mr. John Ternus",
        "age": 51,
        "title": "Senior Vice President of Hardware Engineering",
        "yearBorn": 1975,
    },
    {
        "maxAge": 1,
        "name": "Mr. Craig Federighi",
        "age": 56,
        "title": "Senior Vice President of Software Engineering",
        "yearBorn": 1969,
    },
    {
        "maxAge": 1,
        "name": "Mr. Eddy Cue",
        "age": 61,
        "title": "Senior Vice President of Services",
        "yearBorn": 1965,
    },
    {
        "maxAge": 1,
        "name": "Ms. Mary Demby",
        "age": 58,
        "title": "Chief Information Officer",
        "yearBorn": 1968,
    },
    {
        "maxAge": 1,
        "name": "Mr. Chris Young",
        "age": 50,
        "title": "Senior Vice President of Artificial Intelligence",
        "yearBorn": 1976,
    },
    {
        "maxAge": 1,
        "name": "Ms. Nancy Paxton",
        "age": 57,
        "title": "Senior Director of Investor Relations",
        "yearBorn": 1969,
    },
)


def apple_modules() -> dict[str, Any]:
    return {
        "assetProfile": {
            "address1": "One Apple Park Way",
            "city": "Cupertino",
            "state": "CA",
            "zip": "95014",
            "country": "United States",
            "phone": "408-996-1010",
            "website": "https://www.apple.com",
            "industry": "Consumer Electronics",
            "sector": "Technology",
            "longBusinessSummary": APPLE_SUMMARY,
            "fullTimeEmployees": 164000,
            "companyOfficers": [dict(officer) for officer in APPLE_OFFICERS],
        },
        "price": {
            "symbol": "AAPL",
            "longName": "Apple Inc.",
            "shortName": "Apple",
            "exchangeName": "NMS",
        },
    }


def summary_body(modules: Mapping[str, Any] | None) -> dict[str, Any]:
    return {
        "quoteSummary": {
            "result": [dict(modules)] if modules is not None else None,
            "error": None,
        }
    }


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def test_the_parser_returns_name_description_and_officers() -> None:
    profile = parse_company_profile("AAPL", apple_modules())

    assert profile is not None
    assert profile.symbol == "AAPL"
    assert profile.name == "Apple Inc."
    assert profile.description == (
        "Apple Inc. designs, manufactures, and markets smartphones, personal "
        "computers, tablets, and wearables. The Company also sells a range of "
        "related services, including advertising, cloud, and payment services."
    )
    assert profile.officers[0] == Officer(name="Mr. Timothy D. Cook", title="CEO & Director")


def test_newlines_and_repeated_spaces_are_collapsed() -> None:
    modules = apple_modules()
    modules["assetProfile"]["longBusinessSummary"] = (
        "First line.\n\n\nSecond   line.\r\nThird\tline.  "
    )

    profile = parse_company_profile("AAPL", modules)

    assert profile is not None
    assert profile.description == "First line. Second line. Third line."


def test_officers_are_capped_at_ten_in_source_order() -> None:
    profile = parse_company_profile("AAPL", apple_modules())

    assert profile is not None
    assert len(profile.officers) == MAX_OFFICERS == 10
    assert [officer.name for officer in profile.officers] == [
        officer["name"] for officer in APPLE_OFFICERS[:10]
    ]
    assert "Mr. Chris Young" not in {officer.name for officer in profile.officers}


def test_an_officer_without_a_name_or_title_is_skipped_not_invented() -> None:
    modules = apple_modules()
    modules["assetProfile"]["companyOfficers"] = [
        {"name": "  ", "title": "Ghost"},
        {"name": "Ms. Real Person", "title": "   "},
        {"name": "Mr. Real Person", "title": "Chief Executive Officer"},
        {"name": "Mr. Also Real", "title": "Chief Financial Officer"},
    ]

    profile = parse_company_profile("AAPL", modules)

    assert profile is not None
    assert profile.officers == (
        Officer(name="Mr. Real Person", title="Chief Executive Officer"),
        Officer(name="Mr. Also Real", title="Chief Financial Officer"),
    )


def test_officer_spacing_is_collapsed_too() -> None:
    modules = apple_modules()
    modules["assetProfile"]["companyOfficers"] = [
        {"name": "Mr.  Kevan   Parekh", "title": "Senior  VP & CFO"}
    ]

    profile = parse_company_profile("AAPL", modules)

    assert profile is not None
    assert profile.officers == (Officer(name="Mr. Kevan Parekh", title="Senior VP & CFO"),)


def test_a_missing_description_is_no_profile_at_all() -> None:
    modules = apple_modules()
    modules["assetProfile"]["longBusinessSummary"] = "   \n  "

    assert parse_company_profile("AAPL", modules) is None
    assert parse_company_profile("AAPL", {"assetProfile": {}}) is None
    assert parse_company_profile("AAPL", None) is None


def test_the_name_falls_back_to_the_short_name_then_the_symbol() -> None:
    modules = apple_modules()
    modules["price"].pop("longName")

    profile = parse_company_profile("AAPL", modules)
    assert profile is not None
    assert profile.name == "Apple"

    modules["price"].pop("shortName")
    profile = parse_company_profile("AAPL", modules)
    assert profile is not None
    assert profile.name == "AAPL"


# ---------------------------------------------------------------------------
# The service: 7-day disk cache, honest fallback
# ---------------------------------------------------------------------------


def fake_profile(symbol: str = "AAPL") -> CompanyProfile:
    return CompanyProfile(
        symbol=symbol,
        name="Apple Inc.",
        description="A business summary.",
        officers=(Officer(name="Mr. Timothy D. Cook", title="CEO & Director"),),
    )


class SpyFetcher:
    """A fetcher that records calls and answers one canned profile."""

    def __init__(self, profile: CompanyProfile | None = None, error: Exception | None = None):
        self.calls: list[str] = []
        self.profile = profile
        self.error = error

    async def __call__(self, symbol: str) -> CompanyProfile | None:
        self.calls.append(symbol)
        if self.error is not None:
            raise self.error
        return self.profile


def cache_file(directory: Path, symbol: str = "AAPL") -> Path:
    """``MEE_COMPANY_CACHE_DIR`` is the company directory itself."""

    return directory / f"{symbol}.json"


def write_cache(
    directory: Path,
    *,
    symbol: str = "AAPL",
    fetched_at: datetime,
    description: str = "Cached summary.",
    source: str = "yahoo",
) -> Path:
    path = cache_file(directory, symbol)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "symbol": symbol,
                "fetched_at": fetched_at.isoformat(),
                "source": source,
                "payload": {
                    "name": "Apple Inc.",
                    "description": description,
                    "officers": [{"name": "Mr. Timothy D. Cook", "title": "CEO & Director"}],
                },
            }
        ),
        encoding="utf-8",
    )
    return path


async def test_a_live_fetch_is_written_to_disk_and_served(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEE_COMPANY_CACHE_DIR", str(tmp_path))
    fetcher = SpyFetcher(fake_profile())
    now = datetime(2026, 11, 2, 15, 0, tzinfo=UTC)

    document = await collect_company_profile("AAPL", fetcher=fetcher, now=now)

    assert document is not None
    assert document["source"] == "yahoo"
    assert document["as_of"] == now.isoformat()
    assert document["officers"] == [{"name": "Mr. Timothy D. Cook", "title": "CEO & Director"}]
    stored = json.loads(cache_file(tmp_path).read_text(encoding="utf-8"))
    assert stored["fetched_at"] == now.isoformat()
    assert stored["payload"]["description"] == "A business summary."
    assert stored["payload"]["officers"][0]["name"] == "Mr. Timothy D. Cook"


async def test_a_fresh_cache_hit_does_not_call_the_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEE_COMPANY_CACHE_DIR", str(tmp_path))
    now = datetime(2026, 11, 2, 15, 0, tzinfo=UTC)
    write_cache(tmp_path, fetched_at=now - timedelta(days=6))
    fetcher = SpyFetcher(fake_profile())

    document = await collect_company_profile("AAPL", fetcher=fetcher, now=now)

    assert document is not None
    assert document["source"] == "yahoo-cache"
    assert document["as_of"] == (now - timedelta(days=6)).isoformat()
    assert fetcher.calls == []


async def test_a_cache_older_than_seven_days_is_refetched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEE_COMPANY_CACHE_DIR", str(tmp_path))
    now = datetime(2026, 11, 2, 15, 0, tzinfo=UTC)
    write_cache(tmp_path, fetched_at=now - timedelta(days=8))
    fetcher = SpyFetcher(fake_profile())

    document = await collect_company_profile("AAPL", fetcher=fetcher, now=now)

    assert document is not None
    assert document["source"] == "yahoo"
    assert fetcher.calls == ["AAPL"]
    assert json.loads(cache_file(tmp_path).read_text(encoding="utf-8"))[
        "fetched_at"
    ] == now.isoformat()


async def test_a_refused_fetch_falls_back_to_the_old_cache_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEE_COMPANY_CACHE_DIR", str(tmp_path))
    now = datetime(2026, 11, 2, 15, 0, tzinfo=UTC)
    stamp = now - timedelta(days=30)
    write_cache(tmp_path, fetched_at=stamp)
    fetcher = SpyFetcher(error=RuntimeError("yahoo answered 429"))

    document = await collect_company_profile("AAPL", fetcher=fetcher, now=now)

    assert document is not None
    assert document["source"] == "yahoo-cache-stale"
    assert document["as_of"] == stamp.isoformat()
    assert document["description"] == "Cached summary."


async def test_a_refused_fetch_with_no_cache_is_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEE_COMPANY_CACHE_DIR", str(tmp_path))
    fetcher = SpyFetcher(error=RuntimeError("yahoo answered 429"))

    document = await collect_company_profile(
        "AAPL", fetcher=fetcher, now=datetime(2026, 11, 2, tzinfo=UTC)
    )

    assert document is None


async def test_a_payload_without_a_description_is_not_cached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEE_COMPANY_CACHE_DIR", str(tmp_path))
    fetcher = SpyFetcher(None)

    document = await collect_company_profile(
        "AAPL", fetcher=fetcher, now=datetime(2026, 11, 2, tzinfo=UTC)
    )

    assert document is None
    assert not cache_file(tmp_path).exists()


async def test_a_blank_description_is_not_a_description(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEE_COMPANY_CACHE_DIR", str(tmp_path))
    blank = CompanyProfile(
        symbol="AAPL", name="Apple Inc.", description="  \n ", officers=()
    )

    document = await collect_company_profile(
        "AAPL", fetcher=SpyFetcher(blank), now=datetime(2026, 11, 2, tzinfo=UTC)
    )

    assert document is None
    assert not cache_file(tmp_path).exists()


# ---------------------------------------------------------------------------
# The route
# ---------------------------------------------------------------------------


@asynccontextmanager
async def route_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[httpx2.AsyncClient]:
    monkeypatch.setenv("MEE_DB_PATH", str(tmp_path / "mee.db"))
    monkeypatch.setenv("MEE_EXPORT_DIR", str(tmp_path / "exports"))
    monkeypatch.setenv("MEE_COMPANY_CACHE_DIR", str(tmp_path))

    from app.main import create_app

    application = create_app()
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=application), base_url="http://mee.test"
    ) as client:
        yield client


def install_company(monkeypatch: pytest.MonkeyPatch, fetcher: Any) -> None:
    """Route the endpoint through the real service, with the network replaced.

    The route's one seam is ``app.api.engine.company_profile``; wrapping
    :func:`collect_company_profile` here keeps the cache and fallback under test
    while the provider read stays a canned answer.
    """

    from app.market.company import collect_company_profile

    async def company_profile(symbol: str) -> Mapping[str, Any] | None:
        return await collect_company_profile(symbol, fetcher=fetcher)

    monkeypatch.setattr("app.api.engine.company_profile", company_profile)


async def test_the_route_returns_the_company_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fetcher = SpyFetcher(fake_profile())
    install_company(monkeypatch, fetcher)

    async with route_client(tmp_path, monkeypatch) as client:
        response = await client.get("/engine/company/AAPL")

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"symbol", "name", "description", "officers", "source", "as_of"}
    assert body["symbol"] == "AAPL"
    assert body["name"] == "Apple Inc."
    assert body["officers"] == [{"name": "Mr. Timothy D. Cook", "title": "CEO & Director"}]
    assert body["source"] == "yahoo"
    assert fetcher.calls == ["AAPL"]


async def test_the_route_is_404_no_company_profile_without_a_description(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_company(monkeypatch, SpyFetcher(None))

    async with route_client(tmp_path, monkeypatch) as client:
        response = await client.get("/engine/company/AAPL")

    assert response.status_code == 404, response.text
    assert response.json()["error"] == "no_company_profile"


async def test_the_route_is_404_when_yahoo_refuses_and_nothing_is_cached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_company(monkeypatch, SpyFetcher(error=RuntimeError("yahoo answered 401")))

    async with route_client(tmp_path, monkeypatch) as client:
        response = await client.get("/engine/company/AAPL")

    assert response.status_code == 404, response.text
    assert response.json()["error"] == "no_company_profile"


async def test_a_second_route_call_is_served_from_the_cache_without_the_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = SpyFetcher(fake_profile())
    install_company(monkeypatch, first)

    async with route_client(tmp_path, monkeypatch) as client:
        assert (await client.get("/engine/company/AAPL")).status_code == 200

        offline = SpyFetcher(error=AssertionError("the network must not be asked"))
        install_company(monkeypatch, offline)
        response = await client.get("/engine/company/AAPL")

    assert response.status_code == 200, response.text
    assert response.json()["source"] == "yahoo-cache"
    assert offline.calls == []


# ---------------------------------------------------------------------------
# The transport: the existing Yahoo cookie/crumb seam, one assetProfile read
# ---------------------------------------------------------------------------


class FakeYahoo:
    """Routes the cookie host and the API host; counts the crumb exchanges."""

    def __init__(self) -> None:
        from tests._phase2 import MockWeb

        self.web = MockWeb()
        self.cookie_calls = 0
        self.crumb_calls = 0
        self.crumb_token = "test-crumb"
        self.quote_requests: list[httpx2.Request] = []
        self.quote_handler: Any = None
        self.web.route("fc.yahoo.com", self._cookie)
        self.web.route("query1.finance.yahoo.com", self._api)

    def _cookie(self, _request: httpx2.Request) -> httpx2.Response:
        self.cookie_calls += 1
        return httpx2.Response(404, text="")

    def _api(self, request: httpx2.Request) -> httpx2.Response:
        if request.url.path.endswith("/v1/test/getcrumb"):
            self.crumb_calls += 1
            return httpx2.Response(200, text=self.crumb_token)
        if "/quoteSummary/" in request.url.path:
            self.quote_requests.append(request)
            if self.quote_handler is not None:
                return self.quote_handler(request)
            return httpx2.Response(200, json=summary_body(apple_modules()))
        return httpx2.Response(404, json={"error": "unrouted"})


async def test_the_transport_reads_one_asset_profile_through_the_shared_seam(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.market.company import YahooCompanySource
    from tests._market_quote import install_seam

    yahoo = FakeYahoo()
    install_seam(monkeypatch, yahoo.web)

    profile = await YahooCompanySource().fetch("AAPL")

    assert profile is not None
    assert profile.name == "Apple Inc."
    assert profile.description.startswith("Apple Inc. designs")
    assert len(yahoo.quote_requests) == 1
    assert yahoo.quote_requests[0].url.path.endswith("/quoteSummary/AAPL")
    assert yahoo.quote_requests[0].url.params["modules"] == "assetProfile,price"
    assert yahoo.quote_requests[0].url.params["crumb"] == "test-crumb"


async def test_a_crumb_with_a_slash_is_a_valid_crumb(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Yahoo's real token was ``a0MnGz/haT1``; refusing it cost a live read."""

    from app.market.company import YahooCompanySource
    from tests._market_quote import install_seam

    yahoo = FakeYahoo()
    yahoo.crumb_token = "a0MnGz/haT1"
    install_seam(monkeypatch, yahoo.web)

    profile = await YahooCompanySource().fetch("AAPL")

    assert profile is not None
    assert yahoo.quote_requests[0].url.params["crumb"] == "a0MnGz/haT1"


async def test_a_401_renews_the_crumb_once_and_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.market.company import YahooCompanySource
    from tests._market_quote import install_seam

    yahoo = FakeYahoo()
    seen: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        crumb = request.url.params.get("crumb", "")
        seen.append(crumb)
        if len(seen) == 1:
            return httpx2.Response(401, json={"finance": {"error": {"code": "Invalid Crumb"}}})
        return httpx2.Response(200, json=summary_body(apple_modules()))

    yahoo.quote_handler = handler
    install_seam(monkeypatch, yahoo.web)

    profile = await YahooCompanySource().fetch("AAPL")

    assert profile is not None
    assert yahoo.crumb_calls == 2
    assert seen == ["test-crumb", "test-crumb"]
