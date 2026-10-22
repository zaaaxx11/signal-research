"""Shared helpers and fixtures for the Phase-2 RED suite (02 §3.4-§3.9, §4.9, §5).

Not a test module (leading underscore), so pytest never collects it. The
fixtures at the bottom are imported explicitly by the test modules
(``from tests._phase2 import mock_web``), because adding them to the Phase-0
``conftest.py`` would modify a frozen Phase-0 file.

Contracts this module pins — expanded in ``briefs/market-engine/12-phase2-red.md``:

* **``MEE_PROVIDER_MODE``** selects ``fake`` (default) or ``live``. The default
  keeps the 68 Phase-0 and 22 Phase-1 tests on the fake provider; every test in
  this suite that exercises the registry ladder sets ``live``.
* **One HTTP seam.** All provider traffic goes through
  ``app.providers.http.build_client``. :class:`MockWeb` monkeypatches that
  *module attribute* with an ``httpx2.MockTransport``; therefore adapters must
  call ``http.build_client(...)`` (module attribute) instead of a from-import,
  or the seam is not patchable.
* **Ladder result shape.** ``Ladder.fetch(capability, instrument, fields)``
  returns an object with ``observations`` (list of observation mappings),
  ``rung`` (provider name or ``None``), ``verdict`` (a :class:`Verdict`),
  ``attempts`` (list of mappings, keys pinned in :data:`ATTEMPT_KEYS`) and
  ``skipped_by_budget`` (int).
* **Runtime.ladder(budget=...)** is the single construction path: the worker
  uses it too, so a ladder that only tests can build fails the job-level gates.
* **Instrument** is duck-typed: any object with ``instrument_id``, ``symbol``,
  ``mic``, ``asset_class`` and ``currency`` attributes. The production
  ``InstrumentRef`` may be its own dataclass.
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import asynccontextmanager, closing, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Mapping, Sequence
from urllib.parse import unquote

import httpx2
import pytest

from app.contracts import Capability
from app.providers.base import ProviderSpec, Verdict

ROOT = Path(__file__).resolve().parents[1]
REFERENCE_PATH = ROOT / "tests" / "fixtures" / "reference_venue.json"

PROVIDER_MODE_ENV = "MEE_PROVIDER_MODE"
CLIENT_KEY = "phase2-client-key-3f9c1d7a"

#: ``as_of`` of the first job in the cache tests: previous trading session.
AS_OF = "2026-10-21T14:00:00+00:00"
#: ``as_of`` of the second job: the next session. Different session -> the quote
#: cache must miss even though the TTL (measured from ``fetched_at``) is fresh.
NEXT_SESSION_AS_OF = "2026-10-22T14:00:00+00:00"

#: Provider payload time. Fixed so two runs parse byte-identical rows.
QUOTE_TS = int(datetime(2026, 10, 22, 13, 58, tzinfo=UTC).timestamp())

#: Pinned attempt-record keys. A ladder that reports the walk in another shape
#: is not observable, and an unobservable skip is indistinguishable from a call.
ATTEMPT_KEYS = frozenset(
    {"rung", "provider", "capability", "verdict", "attempts", "requests", "bytes"}
)

#: Fields a live quote job asks for: identity + two quote cells.
QUOTE_FIELDS = [
    "instrument_id",
    "symbol",
    "mic",
    "currency",
    "last_price",
    "prev_close",
    "quote_as_of",
]

#: Provider credentials that must never be present unless a test sets them.
KEY_ENV_VARS = (
    "FINNHUB_API_KEY",
    "POLYGON_API_KEY",
    "EODHD_API_KEY",
    "TIINGO_API_KEY",
    "TWELVE_DATA_API_KEY",
    "OPENFIGI_API_KEY",
)


# ---------------------------------------------------------------------------
# Instrument + provider specs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Instrument:
    """Duck-typed instrument reference accepted by ``Ladder.fetch``."""

    instrument_id: str
    symbol: str
    mic: str = "XNAS"
    asset_class: str = "equity"
    currency: str = "USD"


def instrument(symbol: str, *, mic: str = "XNAS", asset_class: str = "equity") -> Instrument:
    return Instrument(
        instrument_id=f"{mic}:{symbol}",
        symbol=symbol,
        mic=mic,
        asset_class=asset_class,
    )


def spec(
    name: str,
    tier: str = "T0",
    *,
    auth: str | None = None,
    capabilities: Sequence[Capability] = (Capability.QUOTE,),
    trust: int = 3,
    delay: str = "realtime",
    supports: Callable[[str, str], bool] | None = None,
) -> ProviderSpec:
    """Build a :class:`ProviderSpec` for the injected-registry ladder tests."""

    return ProviderSpec(
        name=name,
        capabilities=frozenset(capabilities),
        cost_tier=tier,
        trust=trust,
        delay=delay,
        auth=auth,
        rate_limit=(0, 0.0),
        supports=supports or (lambda _asset_class, _mic: True),
        schema_version=f"{name}/v1",
    )


def registry() -> tuple[ProviderSpec, ...]:
    from app.providers.registry import REGISTRY

    return REGISTRY


def quote_specs() -> list[ProviderSpec]:
    return [entry for entry in registry() if Capability.QUOTE in entry.capabilities]


def first_quote_spec() -> ProviderSpec:
    from app.providers.registry import ladder_for

    rungs = ladder_for(Capability.QUOTE)
    if not rungs:
        pytest.fail("registry has no QUOTE rung (02 §5 Fase 2 gate 1)")
    return rungs[0]


def keyed_quote_specs() -> list[ProviderSpec]:
    """QUOTE rungs that need a credential (the ``skipped`` case of gate 2)."""

    return [entry for entry in quote_specs() if entry.auth is not None]


def provider_host_matches(host: str, provider: str) -> bool:
    """Heuristic host→provider match (``yahoo`` → ``query1.finance.yahoo.com``)."""

    token = provider.split("_", 1)[0].lower()
    return bool(token) and token in host.lower()


def spec_for_host(host: str) -> ProviderSpec | None:
    for entry in registry():
        if provider_host_matches(host, entry.name):
            return entry
    return None


# ---------------------------------------------------------------------------
# MockTransport fixture
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Served:
    """One request the fake transport answered."""

    host: str
    url: str
    method: str
    headers: Mapping[str, str]
    bytes_in: int
    bytes_out: int


class MockWeb:
    """``httpx2.MockTransport`` with host routing, request recording and counters.

    A route matches on a substring of the request host (``"yahoo"`` matches
    ``query1.finance.yahoo.com``). Unrouted requests are answered ``404`` and
    recorded in :attr:`unrouted`, so a test never silently succeeds because an
    adapter hit an unexpected host.
    """

    def __init__(self) -> None:
        self.requests: list[Served] = []
        self.unrouted: list[str] = []
        self._routes: list[tuple[str, Callable[[httpx2.Request], httpx2.Response]]] = []
        self._transport = httpx2.MockTransport(self._handle)

    # -- routing -----------------------------------------------------------

    def route(
        self, host_contains: str, responder: Callable[[httpx2.Request], httpx2.Response]
    ) -> None:
        self._routes.append((host_contains.lower(), responder))

    def json(
        self,
        host_contains: str,
        payload: Mapping[str, Any] | Callable[[httpx2.Request], Mapping[str, Any]],
        *,
        status: int = 200,
    ) -> None:
        def responder(request: httpx2.Request) -> httpx2.Response:
            body = payload(request) if callable(payload) else payload
            return httpx2.Response(status, json=dict(body))

        self.route(host_contains, responder)

    def html(self, host_contains: str, *, status: int = 200) -> None:
        self.route(
            host_contains,
            lambda _request: httpx2.Response(
                status,
                html="<!DOCTYPE html><html><body>Please verify you are a human</body></html>",
            ),
        )

    def status(self, host_contains: str, status: int) -> None:
        self.route(
            host_contains,
            lambda _request: httpx2.Response(status, json={"error": f"status {status}"}),
        )

    # -- transport ---------------------------------------------------------

    def _handle(self, request: httpx2.Request) -> httpx2.Response:
        response: httpx2.Response | None = None
        for needle, responder in self._routes:
            if needle in request.url.host.lower():
                response = responder(request)
                break
        if response is None:
            self.unrouted.append(str(request.url))
            response = httpx2.Response(
                404, json={"error": "unrouted", "url": str(request.url)}
            )
        self.requests.append(
            Served(
                host=request.url.host,
                url=str(request.url),
                method=request.method,
                headers=dict(request.headers),
                bytes_in=len(request.content or b""),
                bytes_out=len(response.content),
            )
        )
        return response

    def build_client(self, **_kwargs: Any) -> httpx2.AsyncClient:
        """Stand-in for ``app.providers.http.build_client``."""

        return httpx2.AsyncClient(transport=self._transport)

    # -- counters ----------------------------------------------------------

    def count(self, provider: str) -> int:
        """Requests whose host belongs to ``provider`` (``twelve_data`` → twelvedata)."""

        return sum(1 for record in self.requests if provider_host_matches(record.host, provider))

    def requests_for(self, provider: str) -> list[Served]:
        return [
            record for record in self.requests if provider_host_matches(record.host, provider)
        ]

    @property
    def bytes_out_total(self) -> int:
        return sum(record.bytes_out for record in self.requests)

    def reset(self) -> None:
        self.requests.clear()
        self.unrouted.clear()


@pytest.fixture()
def mock_web() -> MockWeb:
    """A recording fake transport, installed by ``live_client``."""

    return MockWeb()


# ---------------------------------------------------------------------------
# App harness
# ---------------------------------------------------------------------------


@dataclass
class Harness:
    client: Any
    runtime: Any
    db_path: Path
    web: MockWeb

    # -- ladder ------------------------------------------------------------

    def ladder(self, *, budget: Any | None = None) -> Any:
        factory = getattr(self.runtime, "ladder", None)
        if factory is None:
            raise NotImplementedError(
                "Runtime.ladder(budget=...) is missing: Phase-2 GREEN must expose one "
                "construction path for the registry ladder so the worker and the "
                "tests exercise the same rungs (02 §5 Fase 2, gate 2)"
            )
        try:
            return factory(budget=budget)
        except TypeError as exc:  # pragma: no cover - protects a pinned signature
            pytest.fail(f"Runtime.ladder(budget=...) has an unexpected signature: {exc}")

    # -- jobs --------------------------------------------------------------

    async def run_job(self, symbols: Sequence[str], **overrides: Any) -> dict[str, Any]:
        body = enrich_body(symbols, **overrides)
        response = await self.client.post("/markets/enrich", json=body)
        assert response.status_code == 200, (
            f"POST /markets/enrich (async=false) -> {response.status_code}: {response.text}"
        )
        return response.json()

    # -- database ----------------------------------------------------------

    def rows(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        return db_rows(self.db_path, sql, params)

    def quote(self, quote_id: str) -> dict[str, Any]:
        rows = self.rows("SELECT * FROM quotes WHERE id = ?", (quote_id,))
        assert rows, f"no quotes row for {quote_id}"
        return rows[0]

    def health(self, provider: str, capability: str = "quote") -> dict[str, Any] | None:
        return provider_health_row(self.db_path, provider, capability)

    @property
    def jobs_count(self) -> int:
        return count_jobs(self.db_path)


def install_web(monkeypatch: Any, web: MockWeb) -> None:
    """Point the one HTTP seam at the fake transport."""

    from app.providers import http as provider_http

    monkeypatch.setattr(provider_http, "build_client", web.build_client)


def clear_provider_keys(monkeypatch: Any) -> None:
    for name in KEY_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


@asynccontextmanager
async def live_client(
    tmp_path: Path,
    monkeypatch: Any,
    web: MockWeb,
    *,
    api_key: str = CLIENT_KEY,
    mode: str = "live",
    keys: Mapping[str, str] | None = None,
) -> AsyncIterator[Harness]:
    """App wired to the registry ladder and to ``web`` (no sockets)."""

    monkeypatch.setenv("MEE_DB_PATH", str(tmp_path / "mee.db"))
    monkeypatch.setenv("MEE_EXPORT_DIR", str(tmp_path / "exports"))
    monkeypatch.setenv(PROVIDER_MODE_ENV, mode)
    clear_provider_keys(monkeypatch)
    for name, value in (keys or {}).items():
        monkeypatch.setenv(name, value)
    install_web(monkeypatch, web)

    from httpx2 import ASGITransport, AsyncClient

    from app.db import ensure_schema_async
    from app.main import create_app

    application = create_app()
    db_path = Path(tmp_path / "mee.db")
    await ensure_schema_async(db_path)
    transport = ASGITransport(app=application, raise_app_exceptions=False)
    async with AsyncClient(
        transport=transport,
        base_url="http://mee.test",
        headers={"x-api-key": api_key},
    ) as client:
        yield Harness(
            client=client,
            runtime=application.state.runtime,
            db_path=db_path,
            web=web,
        )


@asynccontextmanager
async def quote_client(tmp_path: Path, monkeypatch: Any) -> AsyncIterator[tuple[Any, Any]]:
    """App wired to the reference master, surfacing 5xx instead of raising.

    The atomicity tests inject a real database failure; ``raise_app_exceptions``
    is ``False`` so the status code is observable and the rollback can be
    asserted instead of the traceback becoming the test result.
    """

    monkeypatch.setenv("MEE_DB_PATH", str(tmp_path / "mee.db"))
    monkeypatch.setenv("MEE_EXPORT_DIR", str(tmp_path / "exports"))
    monkeypatch.setenv("MEE_REFERENCE_PATH", str(REFERENCE_PATH))

    from httpx2 import ASGITransport, AsyncClient

    from app.main import create_app

    application = create_app()
    transport = ASGITransport(app=application, raise_app_exceptions=False)
    async with AsyncClient(
        transport=transport,
        base_url="http://mee.test",
        headers={"x-api-key": CLIENT_KEY},
    ) as client:
        yield client, application


# ---------------------------------------------------------------------------
# Ladder construction for the injected-registry tests
# ---------------------------------------------------------------------------


def ladder_class() -> type:
    try:
        from app.providers.ladder import Ladder
    except ImportError as exc:  # pragma: no cover - RED path
        raise NotImplementedError(
            f"app.providers.ladder.Ladder is not implemented yet (Phase 2): {exc}"
        ) from exc
    if not hasattr(Ladder, "fetch") or not hasattr(Ladder, "rungs"):
        raise NotImplementedError(
            "Ladder must expose rungs(capability) and async "
            "fetch(capability, instrument, fields)"
        )
    return Ladder


def make_ladder(
    *,
    specs: Sequence[ProviderSpec],
    adapters: Mapping[str, Any],
    budget: Any | None = None,
    clock: Callable[[], datetime] | Any | None = None,
    cache: Any | None = None,
    health: Any | None = None,
) -> Any:
    """Build a ladder over an injected registry (no global state, no DB)."""

    factory = ladder_class()
    try:
        return factory(
            registry=tuple(specs),
            adapters=dict(adapters),
            budget=budget,
            cache=cache,
            health=health,
            clock=clock,
        )
    except TypeError as exc:  # pragma: no cover - protects a pinned signature
        pytest.fail(
            "Ladder(registry=..., adapters=..., budget=..., cache=..., health=..., "
            f"clock=...) is the pinned Phase-2 constructor: {exc}"
        )


def make_budget(requests: int, credits: int = 0, seconds: int = 60) -> Any:
    """Construct the pinned :class:`Budget` (clear failure while it is a stub).

    ``Budget()`` currently accepts no arguments, so a bare constructor call
    would surface as ``TypeError`` — an infrastructure-shaped failure. This
    helper turns the same fact into an explicit Phase-2 message.
    """

    from app.providers.budget import Budget

    try:
        return Budget(requests=requests, credits=credits, seconds=seconds)
    except TypeError as exc:  # pragma: no cover - RED path
        raise NotImplementedError(
            "Budget(requests=..., credits=..., seconds=...) is the pinned Phase-2 "
            f"constructor: {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# Adapter double
# ---------------------------------------------------------------------------


class FixtureAdapter:
    """In-process adapter with programmed verdicts per symbol.

    ``verdicts[symbol]`` is a queue consumed one entry per call (the last entry
    repeats). ``calls`` records every ``fetch`` invocation, which is how the
    suite proves a skipped rung sent **zero** requests.
    """

    def __init__(
        self,
        name: str,
        *,
        verdicts: Mapping[str, Sequence[Verdict]] | None = None,
        default: Verdict | None = None,
        observations: Callable[[Any], Sequence[Mapping[str, Any]]] | None = None,
    ) -> None:
        self.name = name
        self.calls: list[str] = []
        self._queues = {symbol: list(queue) for symbol, queue in (verdicts or {}).items()}
        self._default = default or Verdict(ok=True, kind="ok")
        self._observations = observations or (
            lambda target: [observation(target, source=name, trust=3)]
        )

    async def fetch(
        self, capability: Capability, target: Instrument, fields: set[str]
    ) -> Mapping[str, Any]:
        self.calls.append(target.symbol)
        return {
            "status": 200,
            "headers": {"content-type": "application/json"},
            "body": b'{"ok":true}',
            "url": f"https://{self.name}.test/{capability.value}/{target.symbol}",
            "capability": capability.value,
        }

    def verdict(self, raw: Mapping[str, Any], target: Instrument) -> Verdict:
        queue = self._queues.get(target.symbol)
        if queue:
            return queue.pop(0) if len(queue) > 1 else queue[0]
        return self._default

    def parse(
        self, raw: Mapping[str, Any], target: Instrument
    ) -> Sequence[Mapping[str, Any]]:
        return list(self._observations(target))


def observation(
    target: Instrument,
    field: str = "last_price",
    value: float = 100.0,
    *,
    source: str = "alpha",
    trust: int = 3,
    as_of: str = "2026-10-21T13:59:00+00:00",
    unit: str = "price:USD",
) -> dict[str, Any]:
    """One observation document (shape of :class:`app.contracts.Observation`)."""

    return {
        "instrument_id": target.instrument_id,
        "field": field,
        "value_num": value,
        "value_text": None,
        "unit": unit,
        "source": source,
        "source_id": None,
        "as_of": as_of,
        "fetched_at": as_of,
        "confidence": 1.0,
        "verdict": "ok",
        "trust": trust,
        "raw_sha256": None,
        "job_id": None,
    }


# ---------------------------------------------------------------------------
# Request bodies + provider payload fixtures
# ---------------------------------------------------------------------------


def enrich_body(symbols: Sequence[str], **overrides: Any) -> dict[str, Any]:
    """A live quote job: explicit symbols, canonical fields, inline execution."""

    body: dict[str, Any] = {
        "market": {"kind": "watchlist", "symbols": list(symbols)},
        "fields": list(QUOTE_FIELDS),
        "enrichment": ["quote"],
        "as_of": AS_OF,
        "max_staleness_s": 86400,
        "allow_delayed": True,
        "budget": {"requests": 50, "credits": 0, "seconds": 120},
        "format": ["csv"],
        "retention_days": 7,
        "async": False,
    }
    body.update(overrides)
    return body


def yahoo_chart(
    symbol: str,
    *,
    price: float | None = 231.5,
    prev_close: float = 229.0,
    currency: str = "USD",
    ts: int = QUOTE_TS,
    empty: bool = False,
) -> dict[str, Any]:
    """A ``/v8/finance/chart`` body: documented shape (01-providers §Yahoo chart).

    ``empty=True`` is the ``result: null`` envelope; ``price=None`` is the same
    envelope with the promised ``regularMarketPrice`` path absent (schema drift).
    """

    if empty:
        return {"chart": {"result": None, "error": None}}
    meta: dict[str, Any] = {
        "currency": currency,
        "symbol": symbol,
        "exchangeName": "NMS",
        "fullExchangeName": "NasdaqGS",
        "instrumentType": "EQUITY",
        "chartPreviousClose": prev_close,
        "regularMarketTime": ts,
        "regularMarketDayHigh": (price or 0.0) + 2.0,
        "regularMarketDayLow": (price or 0.0) - 3.0,
        "regularMarketVolume": 1234567,
        "regularMarketOpen": prev_close + 0.5,
    }
    if price is not None:
        meta["regularMarketPrice"] = price
    return {
        "chart": {
            "result": [{"meta": meta, "timestamp": [ts], "indicators": {"quote": [{}]}}],
            "error": None,
        }
    }


def finnhub_quote(price: float = 231.5, prev_close: float = 229.0) -> dict[str, Any]:
    """Finnhub ``/quote`` body (01-providers §Finnhub)."""

    return {
        "c": price,
        "d": price - prev_close,
        "dp": round((price / prev_close - 1) * 100, 4),
        "h": price + 1.5,
        "l": price - 2.0,
        "o": prev_close + 0.5,
        "pc": prev_close,
        "t": QUOTE_TS,
    }


def symbol_from(request: httpx2.Request) -> str:
    """Best-effort symbol from a provider URL (chart path, query or header)."""

    path = unquote(request.url.path)
    for marker in ("/chart/", "/quote/", "/api/quote/"):
        if marker in path:
            tail = path.split(marker, 1)[1]
            return tail.split("/", 1)[0].split(",")[0].upper()
    for name in ("symbols", "symbol", "ticker"):
        value = request.url.params.get(name)
        if value:
            return value.split(",")[0].upper()
    return ""


def yahoo_route(web: MockWeb, *, prices: Mapping[str, float] | None = None) -> None:
    """Serve a valid chart body per requested symbol; unknown symbols 404."""

    table = dict(prices or {"AAPL": 231.5, "MSFT": 412.25})

    def responder(request: httpx2.Request) -> httpx2.Response:
        symbol = symbol_from(request)
        if symbol not in table:
            return httpx2.Response(404, json={"chart": {"result": None, "error": "Not Found"}})
        return httpx2.Response(200, json=yahoo_chart(symbol, price=table[symbol]))

    web.route("yahoo", responder)


# ---------------------------------------------------------------------------
# Log capture that survives Alembic's ``fileConfig``
# ---------------------------------------------------------------------------


class MeeLogCapture(logging.Handler):
    """Capture ``mee.*`` records on a logger Alembic does not reconfigure.

    ``app.db.ensure_schema`` runs ``alembic/env.py:fileConfig``, which clears the
    **root** handlers pytest's ``caplog`` installs. A handler attached to the
    ``mee`` logger survives, so gate 7 is not vacuously green because the
    capture was detached.
    """

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    @property
    def text(self) -> str:
        return "\n".join(
            f"{record.name}: {record.getMessage()} {record.__dict__}"
            for record in self.records
        )


@contextmanager
def capture_mee_logs() -> Any:
    logger = logging.getLogger("mee")
    handler = MeeLogCapture()
    previous = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        yield handler
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)


# ---------------------------------------------------------------------------
# SQLite reads (frozen schema of 02 §4)
# ---------------------------------------------------------------------------


def db_rows(db_path: Path, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with closing(sqlite3.connect(db_path, timeout=10.0)) as connection:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute(sql, params).fetchall()]


def quote_state(db_path: Path, quote_id: str) -> dict[str, Any]:
    rows = db_rows(db_path, "SELECT * FROM quotes WHERE id = ?", (quote_id,))
    assert rows, f"no quotes row for {quote_id}"
    return rows[0]


def count_jobs(db_path: Path) -> int:
    return int(db_rows(db_path, "SELECT COUNT(*) AS n FROM jobs")[0]["n"])


def provider_health_row(
    db_path: Path, provider: str, capability: str = "quote"
) -> dict[str, Any] | None:
    rows = db_rows(
        db_path,
        "SELECT * FROM provider_health WHERE provider = ? AND capability = ?",
        (provider, capability),
    )
    return rows[0] if rows else None


def fail_job_inserts(db_path: Path) -> None:
    """Make every ``INSERT INTO jobs`` abort inside its own transaction.

    This is the atomicity fault injector: it sits *below* the service and the
    repository, so any implementation (one transaction, or consume + compensate)
    is exercised as written. A mocked ``repository.create`` would only prove
    that the mock was called.
    """

    with closing(sqlite3.connect(db_path, timeout=10.0)) as connection:
        connection.execute(
            """
            CREATE TRIGGER phase2_fail_job_insert
            BEFORE INSERT ON jobs
            BEGIN
                SELECT RAISE(ABORT, 'injected job insert failure');
            END
            """
        )
        connection.commit()


def allow_job_inserts(db_path: Path) -> None:
    with closing(sqlite3.connect(db_path, timeout=10.0)) as connection:
        connection.execute("DROP TRIGGER IF EXISTS phase2_fail_job_insert")
        connection.commit()


__all__ = [
    "AS_OF",
    "ATTEMPT_KEYS",
    "CLIENT_KEY",
    "FixtureAdapter",
    "Harness",
    "Instrument",
    "KEY_ENV_VARS",
    "MockWeb",
    "NEXT_SESSION_AS_OF",
    "PROVIDER_MODE_ENV",
    "QUOTE_FIELDS",
    "QUOTE_TS",
    "allow_job_inserts",
    "MeeLogCapture",
    "capture_mee_logs",
    "clear_provider_keys",
    "count_jobs",
    "db_rows",
    "enrich_body",
    "fail_job_inserts",
    "finnhub_quote",
    "first_quote_spec",
    "instrument",
    "install_web",
    "keyed_quote_specs",
    "ladder_class",
    "live_client",
    "make_budget",
    "make_ladder",
    "mock_web",
    "observation",
    "provider_health_row",
    "quote_client",
    "quote_specs",
    "quote_state",
    "registry",
    "spec",
    "spec_for_host",
    "symbol_from",
    "yahoo_chart",
    "yahoo_route",
]
