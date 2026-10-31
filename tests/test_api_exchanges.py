"""Contract for ``GET /market/exchanges`` — the venue vocabulary and its counts.

The route exists so the interface can render a list of exchanges with row
counts without walking 5,576 rows in the browser, and without asking the
screener four times. Everything it promises is pinned here:

* **the population is the selector's**, not breadth's. The same
  ``build_universe`` call with the same floor (``0`` for stocks, ``None`` for
  the ETF tab), the same ``allow_any_subtype`` and the same scan key, so
  ``sum(counts) == universe_total`` and every ``count`` equals
  ``POST /screens/select {"key":"exch","op":"eq","value":code}.total``. A
  counter modelled on ``/market/breadth`` would report ``XNYS 2,085`` where the
  filter answers ``2,009`` — the equality test is the proof, not an eyeball;
* **one tally per scan generation**, memoised per
  ``(region, family, common_only, generation)``. A warm read costs a dict lookup
  and a JSON encode: no scan, no universe build, no count. The memo keyed
  *with* the generation is what stops a data refresh from serving yesterday's
  counts;
* **revalidatable**: ``W/"exch-<sha256(rev)>"`` over
  ``region|family|common_only|generation|universe_total|vocabulary`` and
  ``Cache-Control: no-cache`` on both the ``200`` and the ``304``. An
  ``If-None-Match`` that matches is answered ``304`` before any tally work —
  the spy asserts the tally count did not move;
* **honest refusals**: an unknown region is a ``422 invalid_region`` naming the
  accepted list and costing no scan; a region whose scan yields no venue is a
  ``200`` with ``exchanges: []`` and ``code_total: 0``, never a 500 and never a
  ``ZeroDivisionError``;
* **one label map**: the four human names are the inverted
  ``MIC_BY_EXCHANGE`` (the code that already owns "the provider calls XNAS
  NASDAQ"), and the aliases are read from ``VALUE_ALIASES`` — the same table
  ``exch eq NYSE`` compiles through, so the two can never drift.

The suite is hermetic: the scanner is a ``MockWeb`` over the recorded fixtures
(``tests/_screen.py``) or a handwritten scan body, and ``tests/conftest.py``
fails the test if anything reaches the network.
"""

from __future__ import annotations

import json
import statistics
import time
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest

from tests import _screen as S

#: The live US distribution measured on 2026-10-31 (provider name → rows).
#: ``sum`` is the live ``universe_total``; the endpoint must reproduce it exactly.
_LIVE_COUNTS: dict[str, int] = {
    "NASDAQ": 3308,
    "NYSE": 2009,
    "AMEX": 258,
    "CBOE": 1,
}

#: Every key the document carries — nothing more, nothing less.
_DOCUMENT_KEYS = frozenset(
    {
        "as_of",
        "region",
        "asset_class",
        "common_only",
        "universe_total",
        "code_total",
        "exchanges",
        "method",
    }
)
#: Every key one venue carries.
_ENTRY_KEYS = frozenset({"code", "label", "count", "share", "aliases"})


# ---------------------------------------------------------------------------
# A scanner that answers with exactly the rows a case needs
# ---------------------------------------------------------------------------


#: The country a non-US venue implies, so a ``global`` case is not silently
#: filtered out as a US issuer. ``build_universe`` keeps foreign *listings* of
#: US issuers out of the global tab, so the fixture has to name a foreign issuer.
_VENUE_COUNTRY: dict[str, str] = {"TSE": "Japan", "LSE": "United Kingdom"}


def _scan_body(
    rows: Sequence[tuple[str, str, float | None]],
    columns: Sequence[str],
) -> dict[str, Any]:
    """A scanner response whose ``d`` arrays are aligned with ``columns``.

    The scanner parses ``d`` **positionally** through the column list it asked
    for, so the responder must answer in the request's own order. Every cell the
    case does not care about is ``None``, which ``build_universe`` reads as "the
    provider sent no value".
    """

    data: list[dict[str, Any]] = []
    for symbol, venue, cap in rows:
        cells = {
            "name": symbol,
            "description": f"{symbol} Corporation",
            "exchange": venue,
            "country": _VENUE_COUNTRY.get(venue, "United States"),
            "sector": "Technology",
            "type": "stock",
            "subtype": "common",
            "currency": "USD",
            "close": 10.0,
            "change": 1.0,
            "volume": 1_000,
            "Value.Traded": 10_000,
            "market_cap_basic": cap,
        }
        data.append({"s": f"{venue}:{symbol}", "d": [cells.get(name) for name in columns]})
    return {"totalCount": len(data), "data": data}


def _responding(
    rows_for: Any,
) -> Any:
    """A ``MockWeb`` responder that maps the request's columns onto rows.

    ``rows_for`` is either a fixed row tuple or a callable answering the rows for
    the *n*-th scan (zero-based), which is how a data refresh is simulated
    without touching the scanner.
    """

    calls = {"n": 0}

    def respond(request: Any) -> dict[str, Any]:
        body = json.loads(request.content)
        rows = rows_for(calls["n"]) if callable(rows_for) else rows_for
        calls["n"] += 1
        return _scan_body(rows, tuple(body["columns"]))

    respond.calls = calls  # type: ignore[attr-defined]
    return respond


def _web(rows_for: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    """A scanner mock over rows, installed as the one HTTP seam."""

    web = S.MockWeb()
    web.json("tradingview", _responding(rows_for))
    S.install_web(monkeypatch, web)
    return web


# ---------------------------------------------------------------------------
# Harness: the real app, an injected scan cache and a frozen clock
# ---------------------------------------------------------------------------


class _Clock:
    """A monotonic clock a test can advance, shared with the ``ScanCache``."""

    def __init__(self, start: float = 1_000.0) -> None:
        self.now = float(start)

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += float(seconds)


@asynccontextmanager
async def _client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    rows_for: Any,
    scan_ttl: float = 5.0,
    clock: _Clock | None = None,
) -> AsyncIterator[tuple[Any, Any, Any, _Clock]]:
    """An ASGI client over the real app, the app, the mock web and the clock.

    The scan cache is replaced by one with the test's own clock so a five-second
    generation can be moved without sleeping: the generation is the input the
    ETag and the tally memo both key on, so it is the one thing a test must be
    able to drive.
    """

    monkeypatch.setenv("MEE_DB_PATH", str(tmp_path / "mee.db"))
    monkeypatch.setenv("MEE_EXPORT_DIR", str(tmp_path / "exports"))
    monkeypatch.setenv("MEE_PROVIDER_MODE", "live")
    web = _web(rows_for, monkeypatch)

    from httpx2 import ASGITransport, AsyncClient

    from app.api.screens import ScanCache
    from app.db import ensure_schema_async
    from app.main import create_app

    application = create_app()
    await ensure_schema_async(Path(tmp_path / "mee.db"))
    clock = clock or _Clock()
    application.state.scan_cache = ScanCache(ttl_s=scan_ttl, clock=clock)

    transport = ASGITransport(app=application, raise_app_exceptions=False)
    async with AsyncClient(
        transport=transport,
        base_url="http://mee.test",
        headers={"x-api-key": "exchanges-key"},
    ) as client:
        yield client, application, web, clock


def _criteria(
    code: str | None = None,
    *,
    region: str = "us",
    common_only: bool = False,
) -> dict[str, Any]:
    """One ``/screens/select`` body: the same population the route must count."""

    filters = [] if code is None else [{"key": "exch", "op": "eq", "value": code}]
    return {
        "mode": "criteria",
        "region": region,
        "criteria": {
            "filters": filters,
            "sort": "market_cap",
            "limit": 1,
            "common_only": common_only,
        },
    }


def _exchange_calls(web: Any) -> int:
    """How many scanner requests the app has made so far."""

    return web.count("tradingview")


# ---------------------------------------------------------------------------
# Shape and counts
# ---------------------------------------------------------------------------


async def test_the_route_publishes_the_four_us_venues_and_their_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The live US distribution, exactly: 3,308 / 2,009 / 258 / 1 over 5,576."""

    rows = [
        (f"{venue[:2]}{index:05d}", venue, 1_000_000_000.0)
        for venue, count in _LIVE_COUNTS.items()
        for index in range(count)
    ]
    async with _client(tmp_path, monkeypatch, rows_for=rows) as (client, _app, _web, _clock):
        response = await client.get(
            "/market/exchanges",
            params={"region": "us", "asset_class": "stock", "common_only": "0"},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == _DOCUMENT_KEYS, f"the document changed shape: {sorted(body)}"
    assert body["region"] == "us"
    assert body["asset_class"] == "stock"
    assert body["common_only"] is False
    assert body["method"] == "scan"
    assert body["as_of"]
    assert body["universe_total"] == 5576
    assert body["code_total"] == 4

    entries = body["exchanges"]
    assert [entry["code"] for entry in entries] == ["XNAS", "XNYS", "XASE", "XCBO"]
    assert [entry["count"] for entry in entries] == [3308, 2009, 258, 1]
    assert all(set(entry) == _ENTRY_KEYS for entry in entries), entries
    assert sum(entry["count"] for entry in entries) == body["universe_total"]
    assert abs(sum(entry["share"] for entry in entries) - 1.0) < 1e-12
    for entry in entries:
        assert abs(entry["share"] - entry["count"] / 5576) < 1e-15


async def test_the_order_is_count_descending_then_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stable order, so two calls diff cleanly and a bar chart is monotone."""

    rows = [
        ("AAA", "NASDAQ", 1e9),
        ("BBB", "NASDAQ", 1e9),
        ("CCC", "NYSE", 1e9),
        ("DDD", "NYSE", 1e9),
        ("EEE", "AMEX", 1e9),
    ]
    async with _client(tmp_path, monkeypatch, rows_for=rows) as (client, _app, _web, _clock):
        body = (await client.get("/market/exchanges?region=us")).json()

    keys = [(-entry["count"], entry["code"]) for entry in body["exchanges"]]
    assert keys == sorted(keys), keys
    assert [entry["code"] for entry in body["exchanges"]] == ["XNAS", "XNYS", "XASE"]


# ---------------------------------------------------------------------------
# The equality proof against /screens/select (adversarial (a))
# ---------------------------------------------------------------------------


async def test_every_count_equals_the_total_the_selector_answers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """For each venue, ``count == POST /screens/select exch eq code .total``.

    This is the breadth trap stated as an assertion: a counter sharing breadth's
    universe would answer ``XNYS 2,085`` while the filter answers ``2,009``.
    """

    async with S.screen_client(tmp_path, monkeypatch) as harness:
        body = (
            await harness.client.get(
                "/market/exchanges", params={"region": "us", "asset_class": "stock"}
            )
        ).json()
        assert body["exchanges"], "the fixture universe has no venues"

        for entry in body["exchanges"]:
            selected = await harness.client.post(
                "/screens/select", json=_criteria(entry["code"])
            )
            assert selected.status_code == 200, selected.text
            answer = selected.json()
            assert answer["total"] == entry["count"], (
                f"{entry['code']}: the route counted {entry['count']}, "
                f"exch eq {entry['code']} answers {answer['total']}"
            )
            assert answer["universe_total"] == body["universe_total"]

        unbounded = await harness.client.post("/screens/select", json=_criteria())
        assert unbounded.status_code == 200, unbounded.text
        assert unbounded.json()["universe_total"] == body["universe_total"]
        assert sum(entry["count"] for entry in body["exchanges"]) == body["universe_total"]


async def test_the_route_and_the_selector_share_one_provider_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One population, one builder, one scan key — in both directions."""

    async with S.screen_client(tmp_path, monkeypatch) as harness:
        body = (await harness.client.get("/market/exchanges?region=us")).json()
        assert body["exchanges"]
        after_route = S.provider_requests(harness.web)
        assert after_route == 1, f"the route scanned {after_route} times"

        answer = await harness.client.post(
            "/screens/select", json=_criteria(body["exchanges"][0]["code"])
        )
        assert answer.status_code == 200, answer.text
        assert S.provider_requests(harness.web) == after_route, (
            "the selection paid a second scan: the two routes key the cache differently"
        )

    async with S.screen_client(tmp_path, monkeypatch) as harness:
        answer = await harness.client.post(
            "/screens/select", json=_criteria("XNYS")
        )
        assert answer.status_code == 200, answer.text
        after_select = S.provider_requests(harness.web)
        body = (await harness.client.get("/market/exchanges?region=us")).json()
        assert body["exchanges"]
        assert S.provider_requests(harness.web) == after_select == 1, (
            "the route paid a second scan after a selection had already fetched the page"
        )


async def test_the_equality_holds_when_common_only_is_asked_for(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``common_only=1`` narrows the population exactly as the selector does.

    The flag changes the population by 412 rows on the live universe; a route
    that ignored it would publish a number no screen can reproduce.
    """

    async with S.screen_client(tmp_path, monkeypatch) as harness:
        default = (await harness.client.get("/market/exchanges?region=us")).json()
        narrowed = (
            await harness.client.get("/market/exchanges?region=us&common_only=1")
        ).json()

        assert narrowed["common_only"] is True
        assert narrowed["universe_total"] < default["universe_total"]
        assert narrowed["universe_total"] == sum(
            entry["count"] for entry in narrowed["exchanges"]
        )

        for entry in narrowed["exchanges"]:
            selected = await harness.client.post(
                "/screens/select", json=_criteria(entry["code"], common_only=True)
            )
            assert selected.status_code == 200, selected.text
            assert selected.json()["total"] == entry["count"]

        unbounded = await harness.client.post(
            "/screens/select", json=_criteria(common_only=True)
        )
        assert unbounded.json()["universe_total"] == narrowed["universe_total"]


async def test_a_cap_less_row_is_not_in_the_universe_the_route_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The 5,678-vs-5,576 trap: breadth keeps a row with no cap, select does not.

    ``min_market_cap=0`` drops a row whose cap the provider never sent, because
    "a row with no cap cannot prove it fits". A counter built like breadth's
    would include it and disagree with the filter by 3.8 % on XNYS.
    """

    rows = [
        ("AAA", "NASDAQ", 1e9),
        ("BBB", "NYSE", 1e9),
        ("CCC", "NYSE", None),  # no market cap: in breadth, out of select
    ]
    async with _client(tmp_path, monkeypatch, rows_for=rows) as (client, _app, _web, _clock):
        answer = (await client.get("/market/exchanges?region=us")).json()
        breadth = (await client.get("/market/breadth?region=us")).json()

    assert answer["universe_total"] == 2, answer
    assert {entry["code"]: entry["count"] for entry in answer["exchanges"]} == {
        "XNAS": 1,
        "XNYS": 1,
    }
    assert breadth["universe_total"] == 3, (
        "breadth is expected to keep the cap-less row; if this changed, the trap "
        f"this test pins no longer exists: {breadth['universe_total']}"
    )
    assert breadth["universe_total"] != answer["universe_total"]


# ---------------------------------------------------------------------------
# Memoisation and cost (adversarial (b) and (d))
# ---------------------------------------------------------------------------


async def test_two_calls_in_one_generation_do_one_scan_and_one_tally(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The tally is per generation, not per request; the scan is shared too."""

    from app.api import exchanges as module

    tallies: list[int] = []
    real_tally = module.tally_exchanges

    def counting_tally(rows: Any, **kwargs: Any) -> Any:
        tallies.append(sum(1 for _row in rows))
        return real_tally(rows, **kwargs)

    monkeypatch.setattr(module, "tally_exchanges", counting_tally)

    rows = [("AAA", "NASDAQ", 1e9), ("BBB", "NYSE", 1e9)]
    async with _client(tmp_path, monkeypatch, rows_for=rows) as (client, _app, web, _clock):
        first = await client.get("/market/exchanges?region=us")
        second = await client.get("/market/exchanges?region=us")
        scans = _exchange_calls(web)

    assert first.status_code == 200 and second.status_code == 200
    assert first.json() == second.json()
    assert len(tallies) == 1, f"the tally ran {len(tallies)} times"
    assert tallies == [2]
    assert scans == 1, f"the scanner was called {scans} times"


async def test_a_warm_read_does_no_scan_no_build_and_no_tally(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Adversarial (d): the route is not a 5,576-row scan per request."""

    from app.api import exchanges as module

    counted = {"tally": 0, "build": 0}
    real_tally = module.tally_exchanges
    real_build = module.build_universe

    def tally_spy(rows: Any, **kwargs: Any) -> Any:
        counted["tally"] += 1
        return real_tally(rows, **kwargs)

    def build_spy(*args: Any, **kwargs: Any) -> Any:
        counted["build"] += 1
        return real_build(*args, **kwargs)

    monkeypatch.setattr(module, "tally_exchanges", tally_spy)
    monkeypatch.setattr(module, "build_universe", build_spy)

    rows = [("AAA", "NASDAQ", 1e9), ("BBB", "NYSE", 1e9)]
    async with _client(tmp_path, monkeypatch, rows_for=rows) as (client, _app, web, _clock):
        await client.get("/market/exchanges?region=us")
        assert counted == {"tally": 1, "build": 1}
        scans_after_first = _exchange_calls(web)
        for _ in range(5):
            assert (await client.get("/market/exchanges?region=us")).status_code == 200

    assert counted == {"tally": 1, "build": 1}, (
        "a warm read must reuse the memo; it did "
        f"{counted['tally']} tallies and {counted['build']} builds"
    )
    assert _exchange_calls(web) == scans_after_first, "a warm read scanned again"


# ---------------------------------------------------------------------------
# ETag, revalidation and the generation (adversarial (b))
# ---------------------------------------------------------------------------


async def test_a_revalidation_answers_304_before_any_tally(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``If-None-Match`` costs no tally, no build and no scan."""

    from app.api import exchanges as module

    tallies = {"n": 0}
    real_tally = module.tally_exchanges

    def tally_spy(rows: Any, **kwargs: Any) -> Any:
        tallies["n"] += 1
        return real_tally(rows, **kwargs)

    monkeypatch.setattr(module, "tally_exchanges", tally_spy)

    rows = [("AAA", "NASDAQ", 1e9), ("BBB", "NYSE", 1e9)]
    async with _client(tmp_path, monkeypatch, rows_for=rows) as (client, _app, web, _clock):
        first = await client.get("/market/exchanges?region=us")
        etag = first.headers["etag"]
        assert etag.startswith('W/"exch-'), etag
        assert first.headers["cache-control"] == "no-cache"

        second = await client.get(
            "/market/exchanges?region=us", headers={"If-None-Match": etag}
        )
        scans = _exchange_calls(web)

    assert second.status_code == 304, second.text
    assert second.content == b""
    assert second.headers["etag"] == etag
    assert second.headers["cache-control"] == "no-cache"
    assert tallies["n"] == 1, f"the 304 paid a tally: {tallies['n']}"
    assert scans == 1, f"the 304 scanned the provider: {scans}"


async def test_the_etag_is_stable_within_a_generation_and_moves_after_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Adversarial (b): a memo (or a tag) keyed without the generation goes stale."""

    first_rows = [("AAA", "NASDAQ", 1e9), ("BBB", "NYSE", 1e9), ("CCC", "NYSE", 1e9)]
    second_rows = [("AAA", "NASDAQ", 1e9), ("BBB", "NYSE", 1e9)]
    clock = _Clock()

    async with _client(
        tmp_path,
        monkeypatch,
        rows_for=lambda scan: first_rows if scan == 0 else second_rows,
        clock=clock,
    ) as (client, _app, web, _clock):
        before = await client.get("/market/exchanges?region=us")
        repeat = await client.get("/market/exchanges?region=us")

        clock.advance(6.0)  # the generation window closes
        after = await client.get("/market/exchanges?region=us")
        replay = await client.get(
            "/market/exchanges?region=us",
            headers={"If-None-Match": before.headers["etag"]},
        )

    assert before.status_code == repeat.status_code == after.status_code == 200
    assert before.headers["etag"] == repeat.headers["etag"]
    assert after.headers["etag"] != before.headers["etag"], (
        "the tag survived a new scan generation; a client would 304 a stale body"
    )
    assert _exchange_calls(web) == 2

    # The counts moved with the data: the memo was keyed by generation.
    assert before.json()["universe_total"] == 3
    assert after.json()["universe_total"] == 2
    assert after.json()["exchanges"][0]["count"] == 1

    # The old tag does not revalidate the new body.
    assert replay.status_code == 200
    assert replay.headers["etag"] == after.headers["etag"]


def test_the_revision_covers_all_six_inputs() -> None:
    """A tag that ignored one input could 304 a body it never measured."""

    from app.api.exchanges import exchange_revision

    base = exchange_revision(
        region="us",
        family="stock",
        common_only=False,
        generation=1,
        universe_total=5576,
        vocabulary="v0",
    )
    variants = {
        "region": exchange_revision(
            region="us_etf",
            family="stock",
            common_only=False,
            generation=1,
            universe_total=5576,
            vocabulary="v0",
        ),
        "family": exchange_revision(
            region="us",
            family="etf",
            common_only=False,
            generation=1,
            universe_total=5576,
            vocabulary="v0",
        ),
        "common_only": exchange_revision(
            region="us",
            family="stock",
            common_only=True,
            generation=1,
            universe_total=5576,
            vocabulary="v0",
        ),
        "generation": exchange_revision(
            region="us",
            family="stock",
            common_only=False,
            generation=2,
            universe_total=5576,
            vocabulary="v0",
        ),
        "universe_total": exchange_revision(
            region="us",
            family="stock",
            common_only=False,
            generation=1,
            universe_total=5575,
            vocabulary="v0",
        ),
        "vocabulary": exchange_revision(
            region="us",
            family="stock",
            common_only=False,
            generation=1,
            universe_total=5576,
            vocabulary="v1",
        ),
    }
    for name, revision in variants.items():
        assert revision != base, f"the revision ignores {name}"
    assert len({base, *variants.values()}) == 7


# ---------------------------------------------------------------------------
# Labels, aliases and the closed vocabulary
# ---------------------------------------------------------------------------


async def test_each_venue_carries_a_human_label_and_never_echoes_its_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """NASDAQ/NYSE/AMEX/CBOE — and a label is never the code in another case."""

    rows = [
        ("AAA", "NASDAQ", 1e9),
        ("BBB", "NYSE", 1e9),
        ("CCC", "AMEX", 1e9),
        ("DDD", "CBOE", 1e9),
    ]
    async with _client(tmp_path, monkeypatch, rows_for=rows) as (client, _app, _web, _clock):
        body = (await client.get("/market/exchanges?region=us")).json()

    labels = {entry["code"]: entry["label"] for entry in body["exchanges"]}
    assert labels == {
        "XNAS": "NASDAQ",
        "XNYS": "NYSE",
        "XASE": "AMEX",
        "XCBO": "CBOE",
    }
    for code, label in labels.items():
        assert label and label != code, f"{code} was labelled with its own code"
        assert label.upper() != code.upper()


def test_the_label_map_is_the_inverted_mic_map() -> None:
    """One owner: ``MIC_BY_EXCHANGE`` already names the four venues."""

    from app.api.exchanges import EXCHANGE_LABELS
    from app.screen.universe import MIC_BY_EXCHANGE

    assert dict(EXCHANGE_LABELS) == {
        mic: name for name, mic in MIC_BY_EXCHANGE.items()
    }


async def test_the_aliases_are_read_from_the_filter_value_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``NYSE`` means XNYS in both places, or the two have drifted."""

    from app.screen.filters import VALUE_ALIASES

    table = VALUE_ALIASES["exchange"]
    expected = {
        code: sorted(alias for alias, target in table.items() if target == code)
        for code in set(table.values())
    }

    rows = [
        ("AAA", "NASDAQ", 1e9),
        ("BBB", "NYSE", 1e9),
        ("CCC", "AMEX", 1e9),
        ("DDD", "CBOE", 1e9),
    ]
    async with _client(tmp_path, monkeypatch, rows_for=rows) as (client, _app, _web, _clock):
        body = (await client.get("/market/exchanges?region=us")).json()

    assert expected["XNAS"] == ["nasd", "nasdaq"]
    for entry in body["exchanges"]:
        assert entry["aliases"] == expected[entry["code"]], entry

    # And every published alias is accepted by the filter it describes.
    from app.screen.filters import compile_filter

    for entry in body["exchanges"]:
        for alias in entry["aliases"]:
            assert compile_filter({"field": "exchange", "op": "eq", "value": alias}).value == (
                entry["code"]
            )


async def test_a_code_with_no_vendored_label_answers_null_never_the_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``global`` carries provider codes; a name we do not know is ``null``."""

    rows = [
        ("AAA", "TSE", 1e9),
        ("BBB", "LSE", 1e9),
    ]
    async with _client(
        tmp_path,
        monkeypatch,
        rows_for=[(symbol, venue, cap) for symbol, venue, cap in rows],
    ) as (client, _app, _web, _clock):
        body = (await client.get("/market/exchanges?region=global")).json()

    assert body["code_total"] == 2
    for entry in body["exchanges"]:
        assert entry["label"] is None, entry
        assert entry["aliases"] == [], entry


# ---------------------------------------------------------------------------
# Refusals and the empty answer
# ---------------------------------------------------------------------------


async def test_an_unknown_region_is_a_422_that_names_the_accepted_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A typo is refused in the house envelope, before any scan."""

    rows = [("AAA", "NASDAQ", 1e9)]
    async with _client(tmp_path, monkeypatch, rows_for=rows) as (client, _app, web, _clock):
        response = await client.get("/market/exchanges?region=xx")

    assert response.status_code == 422, response.text
    body = response.json()
    assert set(body) == {"error", "errorMessage"}, body
    assert body["error"] == "invalid_region"
    for accepted in ("us", "global", "us_etf"):
        assert accepted in body["errorMessage"], body["errorMessage"]
    assert _exchange_calls(web) == 0, "a refused region still scanned"


async def test_a_bad_common_only_is_a_422_not_a_silent_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``common_only=yes-please`` must not be read as false."""

    rows = [("AAA", "NASDAQ", 1e9)]
    async with _client(tmp_path, monkeypatch, rows_for=rows) as (client, _app, web, _clock):
        response = await client.get("/market/exchanges?region=us&common_only=maybe")

    assert response.status_code == 422, response.text
    body = response.json()
    assert set(body) == {"error", "errorMessage"}
    assert body["error"] == "invalid_flag"
    assert _exchange_calls(web) == 0


async def test_a_region_with_no_venues_is_an_empty_200_not_a_500(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Adversarial (c): an empty universe is 0.0 share, never a division error."""

    rows = [
        ("AAA", "OTC", 1e9),  # OTC is excluded by policy
        ("BBB", "SIX", 1e9),  # a foreign venue cannot be in the US tab
    ]
    async with _client(tmp_path, monkeypatch, rows_for=rows) as (client, _app, _web, _clock):
        response = await client.get("/market/exchanges?region=us")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["exchanges"] == []
    assert body["code_total"] == 0
    assert body["universe_total"] == 0
    assert body["method"] == "scan"


def test_the_document_builder_returns_zero_share_for_an_empty_universe() -> None:
    """The guard is in the arithmetic, not only in the route."""

    from app.api.exchanges import exchanges_document

    document = exchanges_document(
        {},
        universe_total=0,
        as_of="2026-10-31T00:00:00+00:00",
        region="us",
        asset_class="stock",
        common_only=False,
    )
    assert document["exchanges"] == []
    assert document["code_total"] == 0
    assert document["universe_total"] == 0


# ---------------------------------------------------------------------------
# Cost: warm p50 < 30 ms, 304 p50 < 5 ms (measured in process, monotonic)
# ---------------------------------------------------------------------------


async def test_a_warm_read_is_cheap_and_a_304_is_cheaper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The live shape (5,576 rows, 4 venues) over one generation, timed."""

    rows = [
        (f"{venue[:2]}{index:05d}", venue, 1_000_000_000.0)
        for venue, count in _LIVE_COUNTS.items()
        for index in range(count)
    ]
    async with _client(tmp_path, monkeypatch, rows_for=rows) as (client, _app, _web, _clock):
        warm = await client.get("/market/exchanges?region=us")
        assert warm.status_code == 200, warm.text
        etag = warm.headers["etag"]

        hot: list[float] = []
        for _ in range(25):
            started = time.monotonic()
            response = await client.get("/market/exchanges?region=us")
            hot.append((time.monotonic() - started) * 1000.0)
            assert response.status_code == 200

        fresh: list[float] = []
        for _ in range(25):
            started = time.monotonic()
            response = await client.get(
                "/market/exchanges?region=us", headers={"If-None-Match": etag}
            )
            fresh.append((time.monotonic() - started) * 1000.0)
            assert response.status_code == 304

    warm_p50 = statistics.median(hot)
    fresh_p50 = statistics.median(fresh)
    assert warm_p50 < 30.0, f"warm p50 {warm_p50:.2f} ms is over the 30 ms budget"
    assert fresh_p50 < 5.0, f"304 p50 {fresh_p50:.2f} ms is over the 5 ms budget"
    print(f"\nwarm p50 {warm_p50:.2f} ms · 304 p50 {fresh_p50:.2f} ms")


async def test_head_answers_like_get_without_a_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A monitor issues HEAD; a 405 is a broken contract, not a nicety.

    ``/app`` supports HEAD for the same reason: a cache or an uptime probe must
    be able to ask "is this current?" without downloading 531 bytes. The
    revalidation over HEAD must still be a 304 with no body.
    """

    rows = [
        (f"{venue[:2]}{index:05d}", venue, 1_000_000_000.0)
        for venue, count in _LIVE_COUNTS.items()
        for index in range(count)
    ]
    async with _client(tmp_path, monkeypatch, rows_for=rows) as (
        client,
        _app,
        _web,
        _clock,
    ):
        get = await client.get("/market/exchanges", params={"region": "us"})
        head = await client.head("/market/exchanges", params={"region": "us"})
        again = await client.head(
            "/market/exchanges",
            params={"region": "us"},
            headers={"If-None-Match": head.headers["etag"]},
        )

    assert head.status_code == 200, head.text
    assert head.content == b"", "HEAD must not carry the document"
    assert head.headers["etag"] == get.headers["etag"]
    assert head.headers["cache-control"] == "no-cache"
    assert head.headers["content-length"] == get.headers["content-length"]
    assert again.status_code == 304
    assert again.content == b""
