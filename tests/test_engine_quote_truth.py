"""RED contract tests for quote freshness truth on ``POST /engine/screen``.

Every test here encodes one clause of ``briefs/market-engine/R1-CONTRACT.md``
section 2: a ``200`` screen document must state whether quotes were refreshed,
which set the refresh targeted, how many symbols were asked and answered, how
fresh the newest print is, and whether a quote-based filter or sort ran against
the pre-refresh snapshot instead of a fresh print.

The suite is written before the implementation on purpose: each test fails with
an ``AssertionError`` on the missing ``quote_snapshot`` key, never a
``KeyError``-only crash, and every pre-existing screen test keeps passing.

No test opens a socket, sleeps or reads the wall clock. The quote engine is
faked at ``app.api.engine.quote_engine_of`` — the same seam the existing screen
tests use — and every tick carries a fixed exchange and receive timestamp.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from app.engine.ingest import EngineDataset
from app.engine.model import Cell, CellStatus, Provenance, Unit
from app.engine.security import SecurityMaster
from app.engine.service import EngineService
from app.market.quote import QuoteTick

#: The four symbols the shared SEC fixture loads (``tests/fixtures``).
FIXTURE_SYMBOLS: frozenset[str] = frozenset({"AAPL", "BRK-B", "MSFT", "NVDA"})

#: The frozen sidecar vocabulary. ``source``/``provider`` are deliberately absent.
SNAPSHOT_KEYS: frozenset[str] = frozenset(
    {
        "refreshed",
        "scope",
        "requested",
        "returned",
        "as_of",
        "stale",
        "reason",
        "filter_used_snapshot",
    }
)

#: Fixed exchange print times; nothing below depends on the wall clock.
EARLY_TS = datetime(2026, 10, 26, 14, 0, tzinfo=UTC)
LATE_TS = datetime(2026, 10, 26, 15, 0, tzinfo=UTC)
#: Receive time, deliberately far later than every print: ``as_of`` must not use it.
RECEIVED_TS = datetime(2026, 10, 26, 16, 5, tzinfo=UTC)


def _tick(
    symbol: str,
    last: float,
    *,
    ts_exchange: datetime = EARLY_TS,
    stale: bool = False,
    prev_close: float | None = None,
    volume: float = 1_000.0,
) -> QuoteTick:
    """One judged tick with fixed, valid quote evidence."""

    return QuoteTick(
        symbol=symbol,
        last=last,
        prev_close=last - 10.0 if prev_close is None else prev_close,
        volume=volume,
        ts_exchange=ts_exchange,
        ts_received=RECEIVED_TS,
        source="fake",
        stale=stale,
    )


def _cell(value: Any, unit: Unit = Unit.USD) -> Cell:
    """A supplied cell, matching the shared screen-test helper style."""

    return Cell(
        value=value,
        unit=unit,
        provenance=Provenance(
            source="test", as_of="2026-10-26", status=CellStatus.OK
        ),
    )


def _dataset() -> EngineDataset:
    """The full four-symbol dataset, built from the shared SEC fixture."""

    master = SecurityMaster.from_company_tickers(
        json.loads(
            (
                Path(__file__).with_name("fixtures")
                / "engine_sec_company_tickers_sample.json"
            ).read_text()
        ),
        as_of="2026-10-26",
    )
    return EngineDataset(
        fetched_at="2026-10-26T12:00:00+00:00",
        security_master=master,
        facts={},
        errors={},
    )


def _service() -> EngineService:
    """A dataset-backed service with no quotes attached at startup."""

    return EngineService.from_dataset(_dataset())


class RecordingQuoteEngine:
    """A quote engine that records every batch it is asked for.

    A symbol with no configured tick is answered with ``None`` — the engine's
    documented "nobody had it" answer, never a zero price.
    """

    def __init__(self, ticks: Mapping[str, QuoteTick]) -> None:
        self._ticks = dict(ticks)
        self.calls: list[tuple[str, ...]] = []

    async def poll_many(
        self, symbols: Sequence[str]
    ) -> dict[str, QuoteTick | None]:
        """Record ``symbols`` and answer each from the configured ticks."""

        self.calls.append(tuple(symbols))
        return {symbol: self._ticks.get(symbol) for symbol in symbols}

    @property
    def requested(self) -> set[str]:
        """Every symbol asked for, across every batch."""

        return {symbol for call in self.calls for symbol in call}


class FailingQuoteEngine:
    """A quote engine whose batch poll always fails, like an outage."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    async def poll_many(
        self, symbols: Sequence[str]
    ) -> dict[str, QuoteTick | None]:
        """Record the batch, then fail it the way a provider outage does."""

        self.calls.append(tuple(symbols))
        raise RuntimeError("quote provider unavailable")


class StaticRows:
    """A row provider with no :class:`EngineDataset` behind it."""

    def __init__(self, rows: tuple[dict[str, Cell], ...]) -> None:
        self._rows = rows
        self.calls = 0

    def rows(self) -> tuple[dict[str, Cell], ...]:
        """Return the fixed rows, counting how often the screen read them."""

        self.calls += 1
        return self._rows


def _app(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    service: EngineService,
    quote_engine: object,
) -> Any:
    """Build an app whose engine state is ``service`` and quote engine is faked."""

    from app.main import create_app

    monkeypatch.setenv("MEE_DB_PATH", str(tmp_path / "mee.db"))
    monkeypatch.setenv("MEE_EXPORT_DIR", str(tmp_path / "exports"))
    application = create_app()
    application.state.engine = service
    monkeypatch.setattr("app.api.engine.quote_engine_of", lambda request: quote_engine)
    return application


async def _post(application: Any, payload: dict[str, Any]) -> Any:
    """POST one screen query through the in-process ASGI app (no socket)."""

    from httpx2 import ASGITransport, AsyncClient

    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://mee.test"
    ) as client:
        return await client.post("/engine/screen", json=payload)


async def test_page_refresh_reports_a_complete_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A page-scoped refresh names its scope, counts and exchange timestamp."""

    engine = RecordingQuoteEngine({"AAPL": _tick("AAPL", 200.0)})
    application = _app(tmp_path, monkeypatch, _service(), engine)

    response = await _post(
        application,
        {
            "filters": [{"field": "ticker", "op": "eq", "value": "AAPL"}],
            "limit": 20,
            "offset": 0,
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert "quote_snapshot" in body, body
    sidecar = body["quote_snapshot"]

    assert set(sidecar) == SNAPSHOT_KEYS
    assert "source" not in sidecar
    assert "provider" not in sidecar
    assert sidecar["refreshed"] is True
    assert sidecar["scope"] == "page"
    assert sidecar["requested"] == 1
    assert sidecar["returned"] == 1
    assert sidecar["as_of"] == EARLY_TS.isoformat()
    assert sidecar["stale"] is False
    assert sidecar["reason"] is None
    assert sidecar["filter_used_snapshot"] is False

    assert engine.requested == {"AAPL"}
    assert body["rows"][0]["price"]["value"] == 200.0


async def test_sidecar_is_stale_when_any_returned_tick_is_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One stale tick in the batch makes the whole snapshot stale."""

    engine = RecordingQuoteEngine(
        {
            "AAPL": _tick("AAPL", 200.0),
            "BRK-B": _tick("BRK-B", 400.0),
            "MSFT": _tick("MSFT", 300.0, stale=True),
            "NVDA": _tick("NVDA", 150.0),
        }
    )
    application = _app(tmp_path, monkeypatch, _service(), engine)

    response = await _post(
        application, {"filters": [], "limit": 20, "offset": 0}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert "quote_snapshot" in body, body
    sidecar = body["quote_snapshot"]

    assert sidecar["scope"] == "page"
    assert sidecar["requested"] == 4
    assert sidecar["returned"] == 4
    assert sidecar["stale"] is True


async def test_sidecar_is_not_stale_when_every_returned_tick_is_fresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A batch with no stale tick reports ``stale: false``, not ``null``."""

    engine = RecordingQuoteEngine(
        {
            "AAPL": _tick("AAPL", 200.0),
            "BRK-B": _tick("BRK-B", 400.0),
            "MSFT": _tick("MSFT", 300.0),
            "NVDA": _tick("NVDA", 150.0),
        }
    )
    application = _app(tmp_path, monkeypatch, _service(), engine)

    response = await _post(
        application, {"filters": [], "limit": 20, "offset": 0}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert "quote_snapshot" in body, body
    sidecar = body["quote_snapshot"]

    assert sidecar["requested"] == 4
    assert sidecar["returned"] == 4
    assert sidecar["stale"] is False


async def test_disabled_refresh_on_a_non_quote_query_never_calls_the_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``refresh_quotes: false`` is reported as skipped and skips the fetch."""

    engine = RecordingQuoteEngine({"AAPL": _tick("AAPL", 200.0)})
    application = _app(tmp_path, monkeypatch, _service(), engine)

    response = await _post(
        application,
        {
            "filters": [{"field": "ticker", "op": "eq", "value": "AAPL"}],
            "refresh_quotes": False,
            "limit": 20,
            "offset": 0,
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert "quote_snapshot" in body, body
    sidecar = body["quote_snapshot"]

    assert sidecar["refreshed"] is False
    assert sidecar["scope"] == "none"
    assert sidecar["reason"] == "refresh_disabled"
    assert sidecar["filter_used_snapshot"] is False
    assert sidecar["requested"] == 0
    assert sidecar["returned"] == 0
    assert sidecar["as_of"] is None
    assert sidecar["stale"] is None

    assert engine.calls == []
    assert "price" not in body["rows"][0]


async def test_a_plain_row_provider_reports_no_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A service with rows but no dataset cannot refresh and says so."""

    engine = RecordingQuoteEngine({"AAPL": _tick("AAPL", 200.0)})
    service = EngineService(
        rows=StaticRows(({"ticker": _cell("AAPL", Unit.TEXT)},))
    )
    application = _app(tmp_path, monkeypatch, service, engine)

    response = await _post(
        application, {"filters": [], "limit": 20, "offset": 0}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert "quote_snapshot" in body, body
    sidecar = body["quote_snapshot"]

    assert sidecar["refreshed"] is False
    assert sidecar["scope"] == "none"
    assert sidecar["reason"] == "no_provider"
    assert sidecar["filter_used_snapshot"] is False

    assert engine.calls == []


async def test_a_price_filter_sees_the_refreshed_candidate_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A quote filter refreshes every dataset symbol *before* the screen runs.

    AAPL prints 50 and the other three print 300; the ``price >= 200`` filter
    must therefore exclude AAPL and keep three rows. The pre-refresh service
    has no prices at all, so a filter reading the snapshot would return nothing.
    """

    engine = RecordingQuoteEngine(
        {
            "AAPL": _tick("AAPL", 50.0),
            "BRK-B": _tick("BRK-B", 300.0),
            "MSFT": _tick("MSFT", 300.0),
            "NVDA": _tick("NVDA", 300.0),
        }
    )
    application = _app(tmp_path, monkeypatch, _service(), engine)

    response = await _post(
        application,
        {
            "filters": [{"field": "price", "op": "gte", "value": 200}],
            "limit": 20,
            "offset": 0,
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert "quote_snapshot" in body, body
    sidecar = body["quote_snapshot"]

    assert engine.requested == set(FIXTURE_SYMBOLS)
    assert sidecar["scope"] == "candidates"
    assert sidecar["refreshed"] is True
    assert sidecar["filter_used_snapshot"] is False
    assert sidecar["requested"] == 4
    assert sidecar["returned"] == 4
    assert sidecar["as_of"] == EARLY_TS.isoformat()

    assert body["total"] == 3
    assert body["returned"] == 3
    tickers = {row["ticker"]["value"] for row in body["rows"]}
    assert "AAPL" not in tickers
    assert tickers == {"BRK-B", "MSFT", "NVDA"}
    assert [row.get("price", {}).get("value") for row in body["rows"]] == [
        300.0,
        300.0,
        300.0,
    ]


async def test_a_disabled_refresh_on_a_price_filter_says_it_used_the_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The snapshot verdict is explicit, and it is the un-refreshed one.

    No quote was ever attached to this dataset, so ``price >= 200`` matches
    nothing. The test asserts that emptiness rather than letting a silent
    snapshot read look like an empty market.
    """

    engine = RecordingQuoteEngine({"AAPL": _tick("AAPL", 200.0)})
    application = _app(tmp_path, monkeypatch, _service(), engine)

    response = await _post(
        application,
        {
            "filters": [{"field": "price", "op": "gte", "value": 200}],
            "refresh_quotes": False,
            "limit": 20,
            "offset": 0,
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert "quote_snapshot" in body, body
    sidecar = body["quote_snapshot"]

    assert sidecar["reason"] == "refresh_disabled"
    assert sidecar["refreshed"] is False
    assert sidecar["filter_used_snapshot"] is True

    assert engine.calls == []
    assert body["total"] == 0
    assert body["rows"] == []


async def test_a_raising_quote_engine_degrades_to_quote_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An outage answers 200 with a typed reason and no invented price."""

    engine = FailingQuoteEngine()
    application = _app(tmp_path, monkeypatch, _service(), engine)

    response = await _post(
        application,
        {
            "filters": [{"field": "price", "op": "gte", "value": 200}],
            "limit": 20,
            "offset": 0,
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert "quote_snapshot" in body, body
    sidecar = body["quote_snapshot"]

    assert sidecar["reason"] == "quote_unavailable"
    assert sidecar["refreshed"] is False
    assert sidecar["filter_used_snapshot"] is True
    assert sidecar["requested"] == 4
    assert sidecar["returned"] == 0

    assert body["rows"] == []
    assert all("price" not in row for row in body["rows"])


async def test_an_empty_quote_batch_degrades_to_quote_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Zero ticks is an outage, not a market with no prices."""

    engine = RecordingQuoteEngine({})
    application = _app(tmp_path, monkeypatch, _service(), engine)

    response = await _post(
        application,
        {
            "filters": [{"field": "price", "op": "gte", "value": 200}],
            "limit": 20,
            "offset": 0,
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert "quote_snapshot" in body, body
    sidecar = body["quote_snapshot"]

    assert engine.requested == set(FIXTURE_SYMBOLS)
    assert sidecar["reason"] == "quote_unavailable"
    assert sidecar["refreshed"] is False
    assert sidecar["filter_used_snapshot"] is True
    assert sidecar["requested"] == 4
    assert sidecar["returned"] == 0

    assert body["rows"] == []
    assert all("price" not in row for row in body["rows"])


async def test_a_failed_refresh_on_a_price_sort_keeps_rows_without_prices(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A sort that uses quotes never attaches a price the batch did not return."""

    engine = FailingQuoteEngine()
    application = _app(tmp_path, monkeypatch, _service(), engine)

    response = await _post(
        application,
        {
            "filters": [],
            "sort": {"field": "price", "descending": True},
            "limit": 20,
            "offset": 0,
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert "quote_snapshot" in body, body
    sidecar = body["quote_snapshot"]

    assert sidecar["reason"] == "quote_unavailable"
    assert sidecar["refreshed"] is False
    assert sidecar["filter_used_snapshot"] is True
    assert sidecar["requested"] == 4
    assert sidecar["returned"] == 0

    assert body["total"] == 4
    assert len(body["rows"]) == 4
    assert all("price" not in row for row in body["rows"])


async def test_page_scope_as_of_is_the_newest_exchange_print(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``as_of`` is the max ``ts_exchange``, never the receive time or a clock."""

    engine = RecordingQuoteEngine(
        {
            "AAPL": _tick("AAPL", 200.0, ts_exchange=EARLY_TS),
            "MSFT": _tick("MSFT", 300.0, ts_exchange=LATE_TS),
        }
    )
    application = _app(tmp_path, monkeypatch, _service(), engine)

    response = await _post(
        application, {"filters": [], "limit": 20, "offset": 0}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert "quote_snapshot" in body, body
    sidecar = body["quote_snapshot"]

    assert sidecar["scope"] == "page"
    assert sidecar["refreshed"] is True
    assert sidecar["requested"] == 4
    assert sidecar["returned"] == 2
    assert sidecar["as_of"] == LATE_TS.isoformat()
    assert sidecar["as_of"] != RECEIVED_TS.isoformat()
    assert sidecar["as_of"] != EARLY_TS.isoformat()

    prices = {
        row["ticker"]["value"]: row["price"]["value"]
        for row in body["rows"]
        if "price" in row
    }
    assert prices == {"AAPL": 200.0, "MSFT": 300.0}
