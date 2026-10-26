"""RED contract for R4.5 bulk ``companyfacts`` enrichment and its CLI.

One test group per clause of ``briefs/market-engine/R4.5-CONTRACT.md``:

* §1 — the facts disk cache: filename, a hit that makes **zero** requests, a
  corrupt/foreign/empty body that is a miss and is replaced, and a live error
  body that is refused instead of cached;
* §2 — the bulk pass: one CIK filling every share class, gated symbols that are
  never fetched, one failed CIK not ending the run, the document bound
  (``limit``), the time budget, and the report arithmetic an operator
  reconciles;
* §2 — the returned dataset is a new object and the caller's facts mapping is
  untouched;
* §3 — the CLI: a saved snapshot whose facts coverage grows, ``--report-only``
  leaving the file byte-identical, a second run that attempts only the
  still-missing CIK and spends **zero** requests on a warm cache, and the
  ``SnapshotError`` → stderr/exit 2 contract.

Every request goes through ``httpx2.MockTransport`` keyed by exact URL, so the
suite-wide socket guard in ``tests/conftest.py`` never has to fire. No sleeps,
no wall clock: the budget test supplies its own stepping ``clock``, every
snapshot uses a fixed ``fetched_at``, and the CLI runs at ``--rate 1000`` so
pacing never becomes a timed test.

CLI seam note
-------------
The CLI tests replace the ``fetch_companyfacts`` **module attribute** rather
than the transport, exactly as the R3.5 submissions tests do. Both candidate
seams are patched — ``app.engine.facts_bulk.fetch_companyfacts`` when that
module exposes one (a top-level ``from app.engine.ingest import
fetch_companyfacts``) and ``app.engine.ingest.fetch_companyfacts`` (a call
through ``ingest.fetch_companyfacts`` or a function-local import). Each test
asserts the fake was actually called, so an implementation that reads
companyfacts through some third, unpatched path fails loudly instead of
silently reaching the network.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import httpx2
import pytest

from app.engine import facts_bulk, ingest
from app.engine.facts import fact_ttm_summary
from app.engine.facts_bulk import FactsReport, enrich_facts, main
from app.engine.ingest import (
    EngineDataset,
    SecIngestError,
    companyfacts_url,
    facts_cache_path,
    fetch_companyfacts,
)
from app.engine.model import Cell, Provenance, SecurityRecord, Unit
from app.engine.security import SecurityMaster
from app.engine.sources.sec import exchange_mic, normalize_cik
from app.engine.store import EngineStore

AAPL_CIK = "0000320193"
MSFT_CIK = "0000789019"

#: A fixed observation instant: a dataset's date is evidence, not "now".
FETCHED_AT = "2026-10-26T12:00:00+00:00"

#: The annual revenue both staged companyfacts documents report.
ANNUAL_REVENUE = 100.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class Web:
    """A recording ``httpx2.MockTransport`` keyed by exact URL.

    An unrouted request is answered ``404`` and still recorded, so a test that
    expects a cache hit can never pass because the code quietly asked EDGAR (or
    the wrong host) and swallowed the failure.
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


def companyfacts_body(
    cik: str, *, revenue: float = ANNUAL_REVENUE
) -> dict[str, Any]:
    """A tiny but structurally real ``companyfacts`` document for one issuer.

    ``Revenues`` carries a full-year row (364 days, so it is annual by the
    parser's own rule), which makes ``fact_ttm_summary`` return the annual
    figure itself: the TTM of a 10-K is the 10-K. The CIK is an integer, the
    way EDGAR publishes it, so the cache's "normalises to the requested CIK"
    test has something to normalise.
    """

    return {
        "cik": int(cik),
        "entityName": f"ISSUER {cik}",
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "label": "Revenues",
                    "units": {
                        "USD": [
                            {
                                "start": "2025-01-01",
                                "end": "2025-12-31",
                                "val": revenue,
                                "accn": f"{cik}-26-000001",
                                "fy": 2026,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2026-02-01",
                            }
                        ]
                    },
                }
            }
        },
    }


def cache_entry(cache_dir: Path, cik: str = AAPL_CIK) -> Path:
    """The cache entry the contract promises for one issuer's facts document.

    Built from the file name the contract fixes rather than from
    :func:`facts_cache_path`, so a corrupt entry can be staged even while that
    function is still a stub — and so a wrong naming rule in the reader cannot
    hide behind the same helper that writes the file.
    """

    return cache_dir / f"CIK{cik}-companyfacts.json"


def parse_or_none(text: str) -> object:
    """Decode ``text`` as JSON, or ``None`` when the entry is not JSON.

    A cache file that cannot be decoded is self-evidently not the fresh
    document, so the assertion reports that as a mismatch instead of raising a
    decoder error the operator would have to interpret.
    """

    try:
        return json.loads(text)
    except ValueError:
        return None


def record(
    *,
    symbol: str,
    name: str,
    cik: str | None,
    exchange: str | None = "Nasdaq",
) -> SecurityRecord:
    """One identity row as the bulk index would have loaded it."""

    return SecurityRecord(
        symbol=symbol,
        name=name,
        cik=cik,
        exchange=exchange,
        mic=exchange_mic(exchange),
        asset_class="equity",
        currency="USD",
        country=None,
        sector=None,
        industry=None,
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


def dataset_of(
    master: SecurityMaster,
    *,
    facts: Mapping[str, Mapping[str, Cell]] | None = None,
    errors: Mapping[str, str] | None = None,
) -> EngineDataset:
    """A dataset wrapping ``master`` with a fixed observation instant."""

    return EngineDataset(
        fetched_at=FETCHED_AT,
        security_master=master,
        facts={} if facts is None else facts,
        errors={} if errors is None else errors,
    )


def revenue_cell(value: float = 1.0) -> Cell:
    """A fact cell an earlier pass would already have put on a symbol."""

    return Cell(
        value=value,
        unit=Unit.USD,
        provenance=Provenance(source="sec", as_of="2025-12-31"),
    )


def stepping_clock(step: float = 0.5) -> Callable[[], float]:
    """A monotonic-looking clock that advances ``step`` on every read.

    The budget test must not depend on wall-clock resolution: with a fixed
    ``time_budget_s`` the elapsed time has to be deterministically past it.
    """

    state = {"now": 0.0}

    def read() -> float:
        """Advance and return the fake monotonic reading."""

        state["now"] += step
        return state["now"]

    return read


def install_fake_fetch(
    monkeypatch: pytest.MonkeyPatch,
    bodies: Mapping[str, dict[str, Any]],
    calls: list[str],
) -> list[str]:
    """Replace the companyfacts fetch seam(s) with a recorded lookup.

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
            raise SecIngestError(f"no staged companyfacts body for cik {cik!r}")
        return body

    patched: list[str] = []
    for module in (facts_bulk, ingest):
        if hasattr(module, "fetch_companyfacts"):
            monkeypatch.setattr(module, "fetch_companyfacts", fake_fetch)
            patched.append(module.__name__)
    return patched


# ---------------------------------------------------------------------------
# §1 — the cache
# ---------------------------------------------------------------------------


def test_facts_cache_path_names_the_issuer_document(tmp_path: Path) -> None:
    """The cache file is keyed by the ten-digit CIK and openly named."""

    cache_dir = tmp_path / "sec-cache"

    path = facts_cache_path(cache_dir, AAPL_CIK)

    assert path == cache_dir / "CIK0000320193-companyfacts.json"
    assert path.name == "CIK0000320193-companyfacts.json"
    assert path.parent == cache_dir, (
        "the entry belongs in the cache directory it was asked for, not a "
        "sibling or the process working directory"
    )
    assert (
        facts_cache_path(cache_dir, MSFT_CIK).name
        == "CIK0000789019-companyfacts.json"
    ), "two issuers must never share one cache entry"


async def test_a_cached_companyfacts_body_is_served_without_a_request(
    tmp_path: Path,
) -> None:
    """A usable cache entry answers the read and spends zero requests."""

    cache_dir = tmp_path / "sec-cache"
    cache_dir.mkdir()
    body = companyfacts_body(AAPL_CIK)
    cache_entry(cache_dir).write_text(json.dumps(body), encoding="utf-8")
    web = Web()
    # Routed on purpose: the payload alone cannot prove the cache was read, so
    # the zero-request assertion is what makes this a hit test.
    web.json(companyfacts_url(AAPL_CIK), body)

    payload = await fetch_companyfacts(
        AAPL_CIK, client=web.client(), cache_dir=cache_dir
    )

    assert payload == body, "the cached document is the answer, not a refetch"
    assert web.requests == [], (
        "a cache hit makes zero requests: asking EDGAR again is exactly what "
        "the cache exists to avoid"
    )


@pytest.mark.parametrize(
    "stale",
    [
        pytest.param("", id="empty"),
        pytest.param("{ this is not json", id="truncated-json"),
        pytest.param(json.dumps({"error": "rate limited"}), id="no-facts-mapping"),
        pytest.param(
            json.dumps(companyfacts_body(MSFT_CIK)), id="another-issuers-body"
        ),
    ],
)
async def test_an_unusable_cache_entry_is_a_miss_and_is_replaced(
    tmp_path: Path, stale: str
) -> None:
    """A body the reader cannot vouch for is refetched once and overwritten."""

    cache_dir = tmp_path / "sec-cache"
    cache_dir.mkdir()
    entry = cache_entry(cache_dir)
    entry.write_text(stale, encoding="utf-8")
    fresh = companyfacts_body(AAPL_CIK)
    web = Web()
    web.json(companyfacts_url(AAPL_CIK), fresh)

    payload = await fetch_companyfacts(
        AAPL_CIK, client=web.client(), cache_dir=cache_dir
    )

    assert payload == fresh, "a miss must answer with the document EDGAR sent"
    assert web.urls() == [companyfacts_url(AAPL_CIK)], (
        "an unusable entry is a miss, so exactly one request is spent"
    )
    assert parse_or_none(entry.read_text(encoding="utf-8")) == fresh, (
        "the miss replaces the entry: a cache is not evidence of anything "
        "except a document that was once read"
    )


async def test_a_live_body_that_is_not_companyfacts_is_refused_and_not_cached(
    tmp_path: Path,
) -> None:
    """A proxy error page is an error, never an empty document on disk."""

    cache_dir = tmp_path / "sec-cache"
    web = Web()
    web.json(companyfacts_url(AAPL_CIK), {"error": "rate limited", "message": "slow"})

    with pytest.raises(SecIngestError):
        await fetch_companyfacts(AAPL_CIK, client=web.client(), cache_dir=cache_dir)

    assert not cache_entry(cache_dir).exists(), (
        "a body that is not a companyfacts document must never poison the "
        "cache: the next run would serve it as if it were evidence"
    )


# ---------------------------------------------------------------------------
# §2 — the bulk pass
# ---------------------------------------------------------------------------


async def test_one_document_fills_every_class_of_its_cik() -> None:
    """Two share classes of one CIK get the same cells from one document."""

    master = master_of(
        record(symbol="AAPL", name="Apple Inc.", cik=AAPL_CIK),
        record(symbol="APLB", name="Apple Inc.", cik=AAPL_CIK),
    )
    dataset = dataset_of(master)
    body = companyfacts_body(AAPL_CIK)
    web = Web()
    web.json(companyfacts_url(AAPL_CIK), body)

    enriched, report = await enrich_facts(dataset, client=web.client())

    assert web.urls() == [companyfacts_url(AAPL_CIK)], (
        "Berkshire's classes share one document: two symbols of one CIK must "
        "not spend two requests"
    )
    assert isinstance(report, FactsReport)
    assert set(enriched.facts) == {"AAPL", "APLB"}
    assert enriched.facts["AAPL"] == enriched.facts["APLB"] == fact_ttm_summary(body)
    assert enriched.facts["AAPL"]["revenue"].value == pytest.approx(ANNUAL_REVENUE)
    assert report.requested == 1
    assert report.attempted == 1
    assert report.documents == 1, "one CIK is one document, not one per class"
    assert report.symbols == 2, "symbols counts fact rows added, not documents"
    assert report.skipped == 0
    assert report.failed == 0
    assert report.requests == 1
    assert report.deferred == 0


async def test_symbols_with_facts_or_without_a_cik_are_skipped() -> None:
    """Neither an already-filled symbol nor a CIK-less one is ever fetched."""

    master = master_of(
        record(symbol="AAPL", name="Apple Inc.", cik=AAPL_CIK),
        record(symbol="PRIV", name="Private Co", cik=None, exchange=None),
    )
    existing = {"revenue": revenue_cell(7.0)}
    dataset = dataset_of(master, facts={"AAPL": existing})
    web = Web()

    enriched, report = await enrich_facts(dataset, client=web.client())

    assert web.requests == [], (
        "a symbol that already carries facts and a symbol with no CIK are both "
        "ineligible: neither may spend a request"
    )
    assert report.requested == 0
    assert report.attempted == 0
    assert report.documents == 0
    assert report.symbols == 0
    assert report.skipped == 2, (
        "skipped is how an operator sees that the universe was gated, not "
        "silently empty"
    )
    assert report.failed == 0
    assert report.requests == 0
    assert report.deferred == 0
    assert enriched.facts == {"AAPL": existing}
    assert "PRIV" not in enriched.facts


async def test_a_failed_cik_does_not_end_the_run() -> None:
    """One 500 is a counted failure; the next issuer still fills its symbol."""

    master = master_of(
        record(symbol="AAAA", name="Alpha Corp", cik=MSFT_CIK),
        record(symbol="BBBB", name="Beta Corp", cik=AAPL_CIK),
    )
    dataset = dataset_of(master)
    body = companyfacts_body(AAPL_CIK)
    web = Web()
    web.status(companyfacts_url(MSFT_CIK), 500)
    web.json(companyfacts_url(AAPL_CIK), body)

    enriched, report = await enrich_facts(dataset, client=web.client())

    assert sorted(web.urls()) == sorted(
        [companyfacts_url(MSFT_CIK), companyfacts_url(AAPL_CIK)]
    ), "both eligible documents are attempted; a failure is not a stop"
    assert report.requested == 2
    assert report.attempted == 2
    assert report.documents == 1
    assert report.symbols == 1, "only the readable issuer's symbol gains facts"
    assert report.failed == 1
    assert report.requests == 2
    assert report.deferred == 0
    assert set(enriched.facts) == {"BBBB"}
    assert enriched.facts["BBBB"]["revenue"].value == pytest.approx(ANNUAL_REVENUE)
    assert "AAAA" not in enriched.facts, (
        "a failed fetch has no facts, not empty ones"
    )
    assert enriched.errors == dataset.errors, (
        "a per-CIK failure is a report count; the caller's error mapping is "
        "shared unchanged"
    )


async def test_limit_bounds_documents_not_symbols() -> None:
    """``limit=1`` selects one CIK document, and an unselected one is not deferred."""

    master = master_of(
        record(symbol="AAAA", name="Alpha Corp", cik=MSFT_CIK),
        record(symbol="BBBB", name="Beta Corp", cik=AAPL_CIK),
    )
    dataset = dataset_of(master)
    web = Web()
    web.json(companyfacts_url(MSFT_CIK), companyfacts_body(MSFT_CIK))
    web.json(companyfacts_url(AAPL_CIK), companyfacts_body(AAPL_CIK))

    enriched, report = await enrich_facts(dataset, client=web.client(), limit=1)

    assert report.requested == 1, (
        "the limit bounds documents, so the eligible set after the bound is "
        "one — not the two the caller's universe holds"
    )
    assert report.attempted == 1
    assert report.deferred == 0, (
        "an unselected document is not deferred by a limit; deferral is the "
        "time budget's word"
    )
    assert report.documents == 1
    assert report.symbols == 1
    assert report.requests == 1
    assert len(web.requests) == 1
    assert len(enriched.facts) == 1


async def test_a_zero_time_budget_defers_every_selected_document() -> None:
    """No budget means no fetch started, and the remainder is reported."""

    master = master_of(
        record(symbol="AAAA", name="Alpha Corp", cik=MSFT_CIK),
        record(symbol="BBBB", name="Beta Corp", cik=AAPL_CIK),
    )
    dataset = dataset_of(master)
    web = Web()

    enriched, report = await enrich_facts(
        dataset,
        client=web.client(),
        time_budget_s=0.0,
        clock=stepping_clock(),
    )

    assert report.requested == 2, (
        "the selected set is still reported: deferral is visible, not silence"
    )
    assert report.attempted == 0
    assert report.deferred == 2
    assert report.documents == 0
    assert report.symbols == 0
    assert report.failed == 0
    assert report.requests == 0
    assert web.requests == [], "zero budget means zero transport calls"
    assert enriched.facts == {}


async def test_the_returned_dataset_is_new_and_the_input_is_untouched() -> None:
    """Enrichment returns a new dataset; the caller's facts mapping is frozen."""

    master = master_of(
        record(symbol="AAPL", name="Apple Inc.", cik=AAPL_CIK),
        record(symbol="APLB", name="Apple Inc.", cik=AAPL_CIK),
    )
    dataset = dataset_of(master)
    before = dict(dataset.facts)
    web = Web()
    web.json(companyfacts_url(AAPL_CIK), companyfacts_body(AAPL_CIK))

    enriched, _report = await enrich_facts(dataset, client=web.client())

    assert enriched is not dataset, (
        "a completed load is a value: enrichment must not edit the observation "
        "the caller already holds"
    )
    assert isinstance(enriched, EngineDataset)
    assert dict(dataset.facts) == before == {}, (
        "the input dataset's facts mapping is unchanged in place"
    )
    assert enriched.facts is not dataset.facts
    assert set(enriched.facts) == {"AAPL", "APLB"}
    assert enriched.security_master is dataset.security_master, (
        "the master is shared unchanged: facts enrichment does not touch "
        "identity"
    )
    assert enriched.errors == dataset.errors
    assert enriched.fetched_at == dataset.fetched_at


# ---------------------------------------------------------------------------
# §3 — the CLI
# ---------------------------------------------------------------------------


def cli_snapshot(
    tmp_path: Path,
    *records: SecurityRecord,
    facts: Mapping[str, Mapping[str, Cell]] | None = None,
    filename: str = "engine-snapshot.json",
) -> Path:
    """Save a snapshot through the real store and return its path."""

    path = tmp_path / filename
    EngineStore(path).save(dataset_of(master_of(*records), facts=facts))
    return path


def test_cli_saves_the_enriched_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``main`` enriches, saves, and prints the report document as JSON."""

    path = cli_snapshot(
        tmp_path,
        record(symbol="AAPL", name="Apple Inc.", cik=AAPL_CIK),
        record(symbol="MSFT", name="Microsoft Corp", cik=MSFT_CIK),
    )
    cache_dir = tmp_path / "sec-cache"
    calls: list[str] = []
    patched = install_fake_fetch(
        monkeypatch,
        {AAPL_CIK: companyfacts_body(AAPL_CIK), MSFT_CIK: companyfacts_body(MSFT_CIK)},
        calls,
    )
    assert patched, "the CLI must read companyfacts through a patchable seam"

    exit_code = main(
        [
            "--snapshot",
            str(path),
            "--cache-dir",
            str(cache_dir),
            "--rate",
            "1000",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.err == ""
    assert calls == [AAPL_CIK, MSFT_CIK], (
        "the CLI attempts every missing CIK exactly once, in symbol order"
    )
    assert json.loads(captured.out) == {
        "requested": 2,
        "attempted": 2,
        "documents": 2,
        "symbols": 2,
        "skipped": 0,
        "failed": 0,
        "requests": 2,
        "deferred": 0,
    }, "stdout is the report document, exactly the dataclass fields"

    reloaded = EngineStore(path).load()
    assert set(reloaded.facts) == {"AAPL", "MSFT"}, (
        "the saved snapshot must carry the new facts; an in-memory-only pass "
        "leaves the next startup where it started"
    )
    assert reloaded.facts["AAPL"]["revenue"].value == pytest.approx(ANNUAL_REVENUE)
    assert reloaded.facts["MSFT"]["revenue"].value == pytest.approx(ANNUAL_REVENUE)


def test_cli_report_only_leaves_the_snapshot_byte_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``--report-only`` measures the pass without rewriting the snapshot."""

    path = cli_snapshot(
        tmp_path,
        record(symbol="AAPL", name="Apple Inc.", cik=AAPL_CIK),
        filename="report-only.json",
    )
    before = path.read_bytes()
    calls: list[str] = []
    install_fake_fetch(
        monkeypatch, {AAPL_CIK: companyfacts_body(AAPL_CIK)}, calls
    )

    exit_code = main(
        [
            "--snapshot",
            str(path),
            "--cache-dir",
            str(tmp_path / "sec-cache"),
            "--report-only",
            "--rate",
            "1000",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert calls == [AAPL_CIK], "the report describes a read that actually happened"
    assert json.loads(captured.out)["symbols"] == 1, (
        "the report still describes the work that was measured"
    )
    assert path.read_bytes() == before, (
        "--report-only must not touch the snapshot: a measurement is not a write"
    )
    assert EngineStore(path).load().facts == {}, (
        "and the facts it measured stay out of the snapshot it did not write"
    )


def test_cli_second_run_attempts_only_the_missing_cik_from_a_warm_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A resumed run skips the filled symbol and reads the cached document."""

    aapl_body = companyfacts_body(AAPL_CIK)
    path = cli_snapshot(
        tmp_path,
        record(symbol="AAPL", name="Apple Inc.", cik=AAPL_CIK),
        record(symbol="MSFT", name="Microsoft Corp", cik=MSFT_CIK),
        facts={"AAPL": fact_ttm_summary(aapl_body)},
        filename="resumed.json",
    )
    cache_dir = tmp_path / "sec-cache"
    cache_dir.mkdir()
    # A previous pass already read MSFT's document, so this run must find it.
    cache_entry(cache_dir, MSFT_CIK).write_text(
        json.dumps(companyfacts_body(MSFT_CIK)), encoding="utf-8"
    )
    calls: list[str] = []
    install_fake_fetch(
        monkeypatch, {MSFT_CIK: companyfacts_body(MSFT_CIK)}, calls
    )

    exit_code = main(
        [
            "--snapshot",
            str(path),
            "--cache-dir",
            str(cache_dir),
            "--rate",
            "1000",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert calls == [MSFT_CIK], (
        "only the still-missing CIK is attempted; AAPL already carries facts"
    )
    assert json.loads(captured.out) == {
        "requested": 1,
        "attempted": 1,
        "documents": 1,
        "symbols": 1,
        "skipped": 1,
        "failed": 0,
        "requests": 0,
        "deferred": 0,
    }, "a warm cache answers the attempt with zero requests"

    reloaded = EngineStore(path).load()
    assert reloaded.facts["AAPL"] == fact_ttm_summary(aapl_body), (
        "the existing cells are carried through unchanged"
    )
    assert reloaded.facts["MSFT"]["revenue"].value == pytest.approx(ANNUAL_REVENUE), (
        "the resumed run fills the symbol it selected"
    )


def test_cli_reports_an_unreadable_snapshot_on_stderr(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A snapshot that cannot be verified is a JSON error and exit 2."""

    missing = tmp_path / "missing.json"

    exit_code = main(["--snapshot", str(missing)])

    captured = capsys.readouterr()
    assert exit_code == 2, (
        "a bad snapshot is an operator problem with a stable exit code, not a "
        "traceback"
    )
    assert captured.out == "", "the report goes to stdout only on success"
    document = json.loads(captured.err)
    assert document["error"] == "snapshot_unreadable"
    assert str(missing) in document["errorMessage"]
