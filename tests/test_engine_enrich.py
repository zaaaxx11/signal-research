"""RED contract for R3.5 bulk submissions enrichment (``app.engine.enrich``).

One test group per clause of ``briefs/market-engine/R3.5-CONTRACT.md``:

* §1 — the submissions disk cache: filename, a hit that makes **zero**
  requests, and a corrupt/foreign body that is a miss and is replaced;
* §2 — selection and reporting: gating before ``limit``, skipping complete and
  CIK-less records, ``SecurityConflict`` isolation, one failed issuer not
  ending the run, the time budget, and the arithmetic an operator reconciles;
* §3 — the CLI: a saved snapshot whose coverage shows the new industry,
  ``--report-only`` leaving the file byte-identical, and a second run over the
  same cache that attempts only the still-missing symbols for **0** requests.

Every request goes through ``httpx2.MockTransport`` keyed by exact URL, so the
suite-wide socket guard in ``tests/conftest.py`` never has to fire. No sleeps,
no wall clock: the budget test supplies its own stepping ``clock`` and every
snapshot uses a fixed ``fetched_at``.

CLI seam note
-------------
The CLI test replaces the ``fetch_submissions`` **module attribute** rather than
the transport. It patches ``app.engine.enrich.fetch_submissions`` when that
module exposes one (a top-level ``from app.engine.ingest import
fetch_submissions``) and always patches ``app.engine.ingest.fetch_submissions``
(a call through ``ingest.fetch_submissions`` or a function-local import). The
test asserts the fake was actually called, so an implementation that reads
submissions through some third, unpatched path fails loudly instead of silently
reaching the network.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import httpx2
import pytest

from app.engine import enrich, ingest
from app.engine.enrich import enrich_profiles, main
from app.engine.ingest import (
    EngineDataset,
    SecIngestError,
    fetch_submissions,
    submissions_cache_path,
)
from app.engine.model import Provenance, SecurityRecord
from app.engine.security import SecurityMaster
from app.engine.sources.sec import SUBMISSIONS_URL, exchange_mic, normalize_cik
from app.engine.store import EngineStore

AAPL_CIK = "0000320193"
MSFT_CIK = "0000789019"

#: A fixed observation instant: the dataset's date is evidence, not "now".
FETCHED_AT = "2026-10-26T12:00:00+00:00"

#: What EDGAR's submissions document says about Apple.
AAPL_INDUSTRY = "Electronic Computers"
AAPL_COUNTRY = "United States"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class Web:
    """A recording ``httpx2.MockTransport`` keyed by exact URL.

    An unrouted request is answered ``404`` and still recorded, so a test that
    expects a cache hit can never pass because the code quietly asked EDGAR
    (or the wrong host) and swallowed the failure.
    """

    def __init__(self) -> None:
        self.requests: list[httpx2.Request] = []
        self._routes: dict[str, Callable[[httpx2.Request], httpx2.Response]] = {}
        self._transport = httpx2.MockTransport(self._handle)

    def route(
        self, url: str, responder: Callable[[httpx2.Request], httpx2.Response]
    ) -> None:
        """Serve ``url`` with ``responder``."""

        self._routes[url] = responder

    def json(self, url: str, payload: Any, *, status: int = 200) -> None:
        """Serve ``payload`` as JSON at ``url``."""

        self.route(url, lambda _request: httpx2.Response(status, json=payload))

    def status(self, url: str, status: int) -> None:
        """Serve an error status at ``url``; the body is never the point."""

        self.route(
            url, lambda _request: httpx2.Response(status, json={"error": status})
        )

    def _handle(self, request: httpx2.Request) -> httpx2.Response:
        """Record every request, routed or not."""

        self.requests.append(request)
        responder = self._routes.get(str(request.url))
        if responder is None:
            return httpx2.Response(
                404, json={"error": "unrouted", "url": str(request.url)}
            )
        return responder(request)

    def client(self) -> httpx2.AsyncClient:
        """An async client over this transport, no redirects (as EDGAR)."""

        return httpx2.AsyncClient(transport=self._transport, follow_redirects=False)

    def urls(self) -> list[str]:
        """The URLs requested so far, in order."""

        return [str(request.url) for request in self.requests]


def submissions_body(
    *,
    cik: str,
    name: str,
    tickers: Sequence[str],
    exchanges: Sequence[str],
    industry: str | None = None,
    state: str | None = None,
) -> dict[str, Any]:
    """A submissions document shaped the way ``parse_submissions`` reads it.

    ``industry`` is SEC's ``sicDescription`` and ``state`` is the business
    address ``stateOrCountry`` — the two fields the parser maps to
    ``industry``/``country``. Omitting both yields a document that states no
    profile, which is a legitimate answer, not an error.
    """

    body: dict[str, Any] = {
        "cik": cik,
        "name": name,
        "tickers": list(tickers),
        "exchanges": list(exchanges),
    }
    if industry is not None:
        body["sicDescription"] = industry
    if state is not None:
        body["addresses"] = {
            "business": {
                "stateOrCountry": state,
                "stateOrCountryDescription": "CALIFORNIA",
            }
        }
    return body


def record(
    *,
    symbol: str,
    name: str,
    cik: str | None,
    exchange: str | None,
    industry: str | None = None,
    country: str | None = None,
) -> SecurityRecord:
    """One identity row as the bulk index would have loaded it.

    ``industry``/``country`` default to ``None``: only a submissions profile
    states them, which is exactly why the enrichment pass exists.
    """

    return SecurityRecord(
        symbol=symbol,
        name=name,
        cik=cik,
        exchange=exchange,
        mic=exchange_mic(exchange),
        asset_class="equity",
        currency="USD",
        country=country,
        sector=None,
        industry=industry,
        active=True,
    )


def master_of(*records: SecurityRecord) -> SecurityMaster:
    """A master holding ``records`` under the bulk document's provenance."""

    master = SecurityMaster()
    master.load(
        records,
        Provenance(
            source="sec",
            as_of="2026-10-26",
            url="https://www.sec.gov/files/company_tickers_exchange.json",
        ),
    )
    return master


def dataset_of(master: SecurityMaster) -> EngineDataset:
    """A dataset wrapping ``master`` with a fixed observation instant."""

    return EngineDataset(
        fetched_at=FETCHED_AT,
        security_master=master,
        facts={},
        errors={},
    )


def cache_entry(cache_dir: Path, cik: str = AAPL_CIK) -> Path:
    """The cache entry this slice promises for one issuer's submissions body."""

    return cache_dir / f"CIK{cik}-submissions.json"


def stepping_clock(step: float = 0.5) -> Callable[[], float]:
    """A monotonic-looking clock that advances ``step`` on every read.

    The budget test must not depend on wall-clock resolution: with a fixed
    ``time_budget_s`` the elapsed time has to be deterministically past it.
    """

    state = {"now": 0.0}

    def read() -> float:
        state["now"] += step
        return state["now"]

    return read


def install_fake_fetch(
    monkeypatch: pytest.MonkeyPatch,
    bodies: Mapping[str, dict[str, Any]],
    calls: list[str],
) -> list[str]:
    """Replace the submissions fetch seam(s) with a recorded lookup.

    Returns the module paths that were patched so a test can prove at least one
    seam exists. Both candidate seams are covered — see the module docstring —
    and the fake raises rather than falling through to the real, network-going
    function for a CIK this test did not stage.
    """

    async def fake_fetch(
        cik: object,
        *,
        client: Any = None,
        user_agent: str | None = None,
        cache_dir: Path | None = None,
    ) -> Any:
        """Return the staged body for ``cik``; never touch the transport."""

        key = normalize_cik(cik) or ""
        calls.append(key)
        body = bodies.get(key)
        if body is None:
            raise SecIngestError(f"no staged submissions body for cik {cik!r}")
        return body

    patched: list[str] = []
    for module in (enrich, ingest):
        if hasattr(module, "fetch_submissions"):
            monkeypatch.setattr(module, "fetch_submissions", fake_fetch)
            patched.append(module.__name__)
    return patched


# ---------------------------------------------------------------------------
# §1 — the cache
# ---------------------------------------------------------------------------


def test_submissions_cache_path_names_the_issuer_document(tmp_path: Path) -> None:
    """The cache file is keyed by the ten-digit CIK and openly named."""

    cache_dir = tmp_path / "sec-cache"

    path = submissions_cache_path(cache_dir, AAPL_CIK)

    assert path == cache_dir / "CIK0000320193-submissions.json"
    assert path.name == "CIK0000320193-submissions.json"
    assert path.parent == cache_dir, (
        "the entry belongs in the cache directory it was asked for, not a "
        "sibling or the process working directory"
    )


async def test_a_cached_submissions_body_is_served_without_a_request(
    tmp_path: Path,
) -> None:
    """A usable cache entry answers the read and spends zero requests."""

    cache_dir = tmp_path / "sec-cache"
    cache_dir.mkdir()
    cached = submissions_body(
        cik=AAPL_CIK,
        name="Apple Inc.",
        tickers=["AAPL"],
        exchanges=["Nasdaq"],
        industry=AAPL_INDUSTRY,
        state="CA",
    )
    cache_entry(cache_dir).write_text(json.dumps(cached), encoding="utf-8")

    web = Web()  # every route is a 404: a request here would be a failure

    payload = await fetch_submissions(
        AAPL_CIK, client=web.client(), cache_dir=cache_dir
    )

    assert payload == cached, "the document on disk is the document returned"
    assert web.requests == [], (
        "a cache hit must perform zero HTTP requests: the whole point of the "
        "cache is a resumable pass, not a re-crawl"
    )


@pytest.mark.parametrize(
    "cached_text",
    [
        pytest.param("{not json at all", id="invalid-json"),
        pytest.param(
            json.dumps(
                {
                    "cik": MSFT_CIK,
                    "name": "Microsoft Corp",
                    "tickers": ["MSFT"],
                    "exchanges": ["Nasdaq"],
                }
            ),
            id="foreign-cik",
        ),
        pytest.param(
            json.dumps({"cik": AAPL_CIK, "name": "Apple Inc."}), id="no-tickers"
        ),
    ],
)
async def test_an_unusable_cache_entry_is_a_miss_and_is_replaced(
    tmp_path: Path, cached_text: str
) -> None:
    """A body we cannot vouch for is refetched and overwritten, never served."""

    cache_dir = tmp_path / "sec-cache"
    cache_dir.mkdir()
    entry = cache_entry(cache_dir)
    entry.write_text(cached_text, encoding="utf-8")

    fresh = submissions_body(
        cik=AAPL_CIK,
        name="Apple Inc.",
        tickers=["AAPL"],
        exchanges=["Nasdaq"],
        industry=AAPL_INDUSTRY,
        state="CA",
    )
    web = Web()
    web.json(SUBMISSIONS_URL.format(cik=AAPL_CIK), fresh)

    payload = await fetch_submissions(
        AAPL_CIK, client=web.client(), cache_dir=cache_dir
    )

    assert payload == fresh, "an unusable entry is a miss, not an answer"
    assert web.urls() == [SUBMISSIONS_URL.format(cik=AAPL_CIK)], (
        "a miss is exactly one fetch of the requested issuer's document"
    )
    stored = entry.read_text(encoding="utf-8")
    try:
        stored_body: object = json.loads(stored)
    except ValueError:
        stored_body = stored  # unparsed text cannot equal a document: this is a miss
    assert stored_body == fresh, (
        "the unusable entry is replaced by the body that was actually read, so "
        "the next run is a hit"
    )


# ---------------------------------------------------------------------------
# §2 — enrichment selection and reporting
# ---------------------------------------------------------------------------


async def test_enrichment_fills_industry_and_country_from_submissions(
    tmp_path: Path,
) -> None:
    """One indexed issuer missing its profile is filled and counted once."""

    master = master_of(
        record(symbol="AAPL", name="Apple Inc.", cik=AAPL_CIK, exchange="Nasdaq")
    )
    dataset = dataset_of(master)
    web = Web()
    web.json(
        SUBMISSIONS_URL.format(cik=AAPL_CIK),
        submissions_body(
            cik=AAPL_CIK,
            name="Apple Inc.",
            tickers=["AAPL"],
            exchanges=["Nasdaq"],
            industry=AAPL_INDUSTRY,
            state="CA",
        ),
    )

    report = await enrich_profiles(
        dataset, client=web.client(), cache_dir=tmp_path / "sec-cache"
    )

    enriched = dataset.security_master.require("AAPL")
    assert enriched.industry == AAPL_INDUSTRY, (
        "sicDescription is SEC's own industry wording; the row must carry it"
    )
    assert enriched.country == AAPL_COUNTRY, (
        "a California business address is the United States, not 'CA' and not "
        "'California'"
    )
    assert report.enriched == 1
    assert report.attempted == 1
    assert report.requested == 1
    assert report.requests == 1
    assert report.deferred == 0
    assert report.conflicts == 0
    assert report.failed == 0
    assert report.skipped == 0, "the row was eligible, so nothing was skipped"


async def test_complete_and_cikless_records_are_skipped_and_never_fetched(
    tmp_path: Path,
) -> None:
    """Gating happens before selection: neither row is an attempt."""

    master = master_of(
        record(
            symbol="AAPL",
            name="Apple Inc.",
            cik=AAPL_CIK,
            exchange="Nasdaq",
            industry=AAPL_INDUSTRY,
            country=AAPL_COUNTRY,
        ),
        record(symbol="PRIV", name="Private Co", cik=None, exchange=None),
    )
    dataset = dataset_of(master)
    web = Web()

    report = await enrich_profiles(dataset, client=web.client())

    assert report.skipped == 2
    assert report.requested == 0
    assert report.attempted == 0
    assert report.requests == 0
    assert report.enriched == 0
    assert web.requests == [], (
        "a complete row and a row without a CIK must never be fetched: there is "
        "no document to ask for"
    )


async def test_a_venue_disagreement_is_a_conflict_that_leaves_the_row_intact(
    tmp_path: Path,
) -> None:
    """Bulk venue vs submissions venue: counted, not raised, not overwritten."""

    master = master_of(
        record(symbol="AAPL", name="Apple Inc.", cik=AAPL_CIK, exchange="Nasdaq")
    )
    dataset = dataset_of(master)
    web = Web()
    web.json(
        SUBMISSIONS_URL.format(cik=AAPL_CIK),
        submissions_body(
            cik=AAPL_CIK,
            name="Apple Inc.",
            tickers=["AAPL"],
            exchanges=["NYSE"],
            industry=AAPL_INDUSTRY,
            state="CA",
        ),
    )

    report = await enrich_profiles(dataset, client=web.client())

    assert report.conflicts == 1
    assert report.failed == 0, "a documented disagreement is not a fetch failure"
    assert report.enriched == 0
    row = dataset.security_master.require("AAPL")
    assert row.exchange == "Nasdaq", (
        "load order is not evidence: a conflicting document must not replace "
        "the venue the bulk index published"
    )
    assert row.industry is None, (
        "a conflicted merge writes nothing at all, so the profile stays unknown "
        "rather than half-applied"
    )


async def test_one_failed_issuer_does_not_end_the_run(tmp_path: Path) -> None:
    """The first issuer answers 500; the second is still enriched."""

    master = master_of(
        record(symbol="AAAA", name="Alpha Corp", cik=MSFT_CIK, exchange="Nasdaq"),
        record(symbol="BBBB", name="Beta Corp", cik=AAPL_CIK, exchange="Nasdaq"),
    )
    dataset = dataset_of(master)
    web = Web()
    web.status(SUBMISSIONS_URL.format(cik=MSFT_CIK), 500)
    web.json(
        SUBMISSIONS_URL.format(cik=AAPL_CIK),
        submissions_body(
            cik=AAPL_CIK,
            name="Beta Corp",
            tickers=["BBBB"],
            exchanges=["Nasdaq"],
            industry=AAPL_INDUSTRY,
            state="CA",
        ),
    )

    report = await enrich_profiles(dataset, client=web.client())

    assert report.attempted == 2
    assert report.requested == 2
    assert report.failed == 1, "one bad issuer is one failed row, not the run"
    assert report.enriched == 1
    assert report.requests == 2
    assert dataset.security_master.require("AAAA").industry is None
    assert dataset.security_master.require("BBBB").industry == AAPL_INDUSTRY, (
        "the failure must not abort the pass before the second issuer is read"
    )


async def test_limit_is_applied_after_gating() -> None:
    """``limit=1`` over two eligible symbols attempts exactly one."""

    master = master_of(
        record(symbol="AAAA", name="Alpha Corp", cik=MSFT_CIK, exchange="Nasdaq"),
        record(symbol="BBBB", name="Beta Corp", cik=AAPL_CIK, exchange="Nasdaq"),
    )
    dataset = dataset_of(master)
    web = Web()
    for cik, symbol, name in (
        (MSFT_CIK, "AAAA", "Alpha Corp"),
        (AAPL_CIK, "BBBB", "Beta Corp"),
    ):
        web.json(
            SUBMISSIONS_URL.format(cik=cik),
            submissions_body(
                cik=cik,
                name=name,
                tickers=[symbol],
                exchanges=["Nasdaq"],
                industry=AAPL_INDUSTRY,
                state="CA",
            ),
        )

    report = await enrich_profiles(dataset, client=web.client(), limit=1)

    assert report.requested == 1, (
        "the limit bounds the work after gating, so a skipped row cannot eat "
        "the one slot the caller asked for"
    )
    assert report.attempted == 1
    assert report.deferred == 0, "an unselected row is not deferred by a limit"
    assert report.requests == 1
    assert len(web.requests) == 1


async def test_a_zero_time_budget_defers_every_eligible_symbol() -> None:
    """No budget means no fetch started, and the remainder is reported."""

    master = master_of(
        record(symbol="AAAA", name="Alpha Corp", cik=MSFT_CIK, exchange="Nasdaq"),
        record(symbol="BBBB", name="Beta Corp", cik=AAPL_CIK, exchange="Nasdaq"),
    )
    dataset = dataset_of(master)
    web = Web()

    report = await enrich_profiles(
        dataset,
        client=web.client(),
        time_budget_s=0.0,
        clock=stepping_clock(),
    )

    assert report.requested == 2, (
        "the eligible set is still reported: deferral is visible, not silence"
    )
    assert report.attempted == 0
    assert report.deferred == 2
    assert report.requests == 0
    assert web.requests == []
    assert dataset.security_master.require("AAAA").industry is None
    assert dataset.security_master.require("BBBB").industry is None


# ---------------------------------------------------------------------------
# §3 — the CLI
# ---------------------------------------------------------------------------


def cli_snapshot(
    tmp_path: Path,
    *records: SecurityRecord,
    filename: str = "engine-snapshot.json",
) -> Path:
    """Save a snapshot through the real store and return its path."""

    path = tmp_path / filename
    EngineStore(path).save(dataset_of(master_of(*records)))
    return path


def test_cli_saves_the_enriched_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``main`` enriches, saves, and prints the report document as JSON."""

    path = cli_snapshot(
        tmp_path,
        record(symbol="AAPL", name="Apple Inc.", cik=AAPL_CIK, exchange="Nasdaq"),
    )
    cache_dir = tmp_path / "sec-cache"
    body = submissions_body(
        cik=AAPL_CIK,
        name="Apple Inc.",
        tickers=["AAPL"],
        exchanges=["Nasdaq"],
        industry=AAPL_INDUSTRY,
        state="CA",
    )
    calls: list[str] = []
    patched = install_fake_fetch(monkeypatch, {AAPL_CIK: body}, calls)
    assert patched, "the CLI must read submissions through a patchable seam"

    exit_code = main(
        ["--snapshot", str(path), "--cache-dir", str(cache_dir)]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert calls == [AAPL_CIK], "the CLI reads the one issuer that is missing a profile"
    assert json.loads(captured.out) == {
        "requested": 1,
        "attempted": 1,
        "enriched": 1,
        "skipped": 0,
        "conflicts": 0,
        "failed": 0,
        "requests": 1,
        "deferred": 0,
    }, "stdout is the report document, exactly the dataclass fields"

    reloaded = EngineStore(path).load()
    row = reloaded.security_master.require("AAPL")
    assert row.industry == AAPL_INDUSTRY, (
        "the saved snapshot must carry the enrichment; an in-memory-only pass "
        "leaves the next startup where it started"
    )
    assert row.country == AAPL_COUNTRY


def test_cli_report_only_leaves_the_snapshot_byte_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``--report-only`` measures coverage without rewriting the snapshot."""

    path = cli_snapshot(
        tmp_path,
        record(symbol="AAPL", name="Apple Inc.", cik=AAPL_CIK, exchange="Nasdaq"),
        filename="report-only.json",
    )
    before = path.read_bytes()
    body = submissions_body(
        cik=AAPL_CIK,
        name="Apple Inc.",
        tickers=["AAPL"],
        exchanges=["Nasdaq"],
        industry=AAPL_INDUSTRY,
        state="CA",
    )
    calls: list[str] = []
    install_fake_fetch(monkeypatch, {AAPL_CIK: body}, calls)

    exit_code = main(
        [
            "--snapshot",
            str(path),
            "--cache-dir",
            str(tmp_path / "sec-cache"),
            "--report-only",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert calls == [AAPL_CIK], "the report describes a read that actually happened"
    assert json.loads(captured.out)["enriched"] == 1, (
        "the report still describes the work that was measured"
    )
    assert path.read_bytes() == before, (
        "--report-only must not touch the snapshot: a measurement is not a write"
    )


async def test_a_second_run_attempts_only_the_still_missing_symbols(
    tmp_path: Path,
) -> None:
    """Resumability: the enriched row is skipped, the rest is a cache read."""

    cache_dir = tmp_path / "sec-cache"
    master = master_of(
        record(symbol="AAPL", name="Apple Inc.", cik=AAPL_CIK, exchange="Nasdaq"),
        record(symbol="MSFT", name="Microsoft Corp", cik=MSFT_CIK, exchange="Nasdaq"),
    )
    dataset = dataset_of(master)
    first_web = Web()
    first_web.json(
        SUBMISSIONS_URL.format(cik=AAPL_CIK),
        submissions_body(
            cik=AAPL_CIK,
            name="Apple Inc.",
            tickers=["AAPL"],
            exchanges=["Nasdaq"],
            industry=AAPL_INDUSTRY,
            state="CA",
        ),
    )
    first_web.json(
        SUBMISSIONS_URL.format(cik=MSFT_CIK),
        submissions_body(
            cik=MSFT_CIK,
            name="Microsoft Corp",
            tickers=["MSFT"],
            exchanges=["Nasdaq"],
        ),
    )

    first = await enrich_profiles(
        dataset, client=first_web.client(), cache_dir=cache_dir
    )

    assert first.requested == 2
    assert first.attempted == 2
    assert first.enriched == 1
    assert first.requests == 2, "both documents were cache misses on the first run"
    assert first.failed == 0
    assert first.conflicts == 0

    second_web = Web()  # a request here would be a 404 and a recorded failure
    second = await enrich_profiles(
        dataset, client=second_web.client(), cache_dir=cache_dir
    )

    assert second.skipped == 1, (
        "AAPL now holds both profile fields, so it must not be attempted again"
    )
    assert second.requested == 1, "MSFT still states no profile, so it stays eligible"
    assert second.attempted == 1
    assert second.requests == 0, (
        "the second run reads MSFT's cached document: a resumed pass makes zero "
        "requests for documents it already has"
    )
    assert second.enriched == 0
    assert second.failed == 0
    assert second.conflicts == 0
    assert second.deferred == 0
    assert second_web.requests == []
