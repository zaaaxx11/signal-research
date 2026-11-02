"""Shared helpers and fixtures for the Screen S0 RED suite (PLAN-screen.md).

Not a test module (leading underscore), so pytest never collects it. Every
contract this module pins is expanded in ``briefs/market-engine/22-screen-red.md``.

Contracts pinned here
---------------------

* **``GET /screens`` is a display, not a search.** It returns exactly
  ``DISPLAY_ROWS = 20`` rows (the top of one snapshot), never a cursor and never
  a second page. ``total`` is the post-filter/post-dedupe universe size, so
  ``total`` may be larger than the 20 rows on screen.
* **A snapshot lives ``SNAPSHOT_TTL_S = 300`` seconds.** Two reads inside that
  window share one snapshot and send zero provider requests; a read after it
  refreshes from the scanner.
* **``screen_rows`` stores the 20 displayed rows only.** The export bundle's
  ``all`` scope is the full universe, fetched on demand at export time — an
  export that recycles the 20 display rows would silently sell a 20-row table
  as "all".
* **One HTTP seam.** The scanner must call ``app.providers.http.build_client``
  as a module attribute; :func:`install_web` patches that attribute with an
  ``httpx2.MockTransport``. A scanner that builds its own client is not
  patchable and, under this suite, raises the conftest egress guard.
* **Scanner rows are provider-shaped.** ``parse_scan(body, columns)`` maps the
  positional ``d`` arrays through ``columns`` **by name**, and the parsed row
  keeps the TradingView field names (``close``, ``Value.Traded``,
  ``market_cap_basic``, ...). Normalisation into screen columns happens in the
  universe layer, not in the scanner.
* **Universe rows are screen-shaped.** ``build_universe(rows, region=...)``
  filters, dedupes and normalises into ``symbol``, ``name``, ``exchange``,
  ``country``, ``sector``, ``price``, ``change_pct``, ``volume``, ``turnover``,
  ``turnover_usd``, ``market_cap_usd``, ``currency``, ``is_primary``.
  ``rank_rows(rows, sort)`` adds ``rank`` (1..n, stable for ties).
* **Two scopes, one bundle.** ``export_bundle(screen_id, *, repository,
  directory, formats, deep_engine, job_id=None)`` writes ``all`` (light
  columns, full universe) and ``top20`` (92 canonical columns, 20 rows) and
  runs ``deep_engine(symbols)`` exactly once with the 20 display symbols.
  Artifacts are served by kind: ``all_csv``, ``top20_csv``, ...
"""

from __future__ import annotations

import hashlib
import importlib
import json
import re
import sqlite3
from contextlib import asynccontextmanager, closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Sequence

from tests._phase2 import MockWeb, install_web  # noqa: F401 - re-exported seam
from tests._support import COLUMNS as _CLAUSE_COLUMNS
from tests._support import wait_for_terminal  # noqa: F401 - re-exported

#: ``tests._support.COLUMNS`` is a ``list`` (the spec is loaded from JSON) and
#: the Phase-5 readers compare ``list(raw) == COLUMNS``, so it must stay one
#: there. The Screen suite, however, asserts ``tuple(scope.columns) == COLUMNS``
#: — a tuple never equals a list in Python, so the re-export is the same 92
#: names **in the same order** as a tuple. Only the container type differs; no
#: assertion and no spec file was touched.
COLUMNS: tuple[str, ...] = tuple(_CLAUSE_COLUMNS)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
US_SCAN_PATH = FIXTURES / "screen_scanner_us.json"
GLOBAL_SCAN_PATH = FIXTURES / "screen_scanner_global.json"

REGIONS: tuple[str, ...] = ("us", "global")
SORTS: tuple[str, ...] = ("market_cap", "turnover", "gainers")
SCOPES: tuple[str, ...] = ("all", "top20")
DATA_FORMATS: tuple[str, ...] = ("csv", "xlsx", "jsonl", "parquet")

#: The shipped screener's default columns, read from the client's own two lists
#: rather than retyped here: the bug these tests pin is precisely that the UI
#: sent keys the ranker did not know, so the list under test has to be the one
#: the UI renders. A copy would have agreed with the backend by construction.
WEB_SRC = ROOT / "web" / "src"
DEFAULT_COLUMNS_TS = WEB_SRC / "screener" / "defaultColumns.ts"
COLUMN_META_TS = WEB_SRC / "grid" / "columnMeta.ts"


def _ts_string_array(source: str, name: str) -> list[str]:
    """The entries of an ``export const NAME = [...] as const`` list.

    Entries are quoted strings except the one identifier reference
    (``ROW_NUMBER_KEY``), which is returned verbatim and resolved by
    :func:`default_screener_columns`.
    """

    match = re.search(rf"export const {name} = \[(.*?)\] as const", source, re.S)
    assert match is not None, f"{name} is no longer a literal list in the client"
    return [token.strip().strip('"') for token in match.group(1).split(",") if token.strip()]


def default_screener_columns() -> list[str]:
    """Every column the screener opens with, in the order the client lists them."""

    source = DEFAULT_COLUMNS_TS.read_text(encoding="utf-8")
    keys = _ts_string_array(source, "DEFAULT_COLUMN_ORDER") + _ts_string_array(
        source, "DEFAULT_EXTRA_COLUMNS"
    )
    meta = COLUMN_META_TS.read_text(encoding="utf-8")
    row_number = re.search(r'export const ROW_NUMBER_KEY = "([^"]+)"', meta)
    assert row_number is not None, "ROW_NUMBER_KEY is the client's row-number spelling"
    return [row_number.group(1) if key == "ROW_NUMBER_KEY" else key for key in keys]


#: The display contract: one screen page is the top 20 of a snapshot.
DISPLAY_ROWS = 20
DEFAULT_LIMIT = 20
#: A snapshot is good for five minutes ("screen top 20, per 5 menit").
SNAPSHOT_TTL_S = 300
#: Rows the deep engine is allowed to enrich — never the whole universe.
DEEP_ROWS = 20
MIN_MARKET_CAP_USD = 100_000_000

#: The four US venue codes the app serves. All four, not three: ``XCBO`` is the
#: single stock row and 1,677 ETF rows on the live tape, and a fixture that
#: omits it fails an assertion over a *correct* universe (research D10).
US_EXCHANGES = frozenset({"XNYS", "XNAS", "XASE", "XCBO"})
OTC_EXCHANGES = frozenset({"OTC", "PNK", "OTCQX", "OTCQB", "GREY"})

#: The live population per venue, measured 2026-10-31 by walking
#: ``POST /screens/select`` (``mode=criteria``, no filters, columns
#: ``["exchange","symbol"]``, 56 pages for ``us`` and 46 for ``us_etf``). The
#: totals below are **sums of these four integers**, never a fifth hand-typed
#: number: the only way a total can be wrong is if a venue count is wrong, and
#: a venue that is not in the vocabulary makes the invariant test fail loudly.
#:
#: ``us``      3,308 + 2,009 + 258 + 1 = 5,576  (``universe_total``)
#: ``us_etf``  2,739 + 1,677 + 1,282 + 78 = 5,776
LIVE_US_BY_EXCHANGE: dict[str, int] = {
    "XNAS": 3_308,
    "XNYS": 2_009,
    "XASE": 258,
    "XCBO": 1,
}
LIVE_ETF_BY_EXCHANGE: dict[str, int] = {
    "XASE": 2_739,
    "XCBO": 1_677,
    "XNAS": 1_282,
    "XNYS": 78,
}
LIVE_US_UNIVERSE_TOTAL: int = sum(LIVE_US_BY_EXCHANGE.values())
LIVE_ETF_UNIVERSE_TOTAL: int = sum(LIVE_ETF_BY_EXCHANGE.values())

#: Expected universe size after filtering + dedupe (hand-counted from the
#: fixtures, not derived by the implementation under test).
US_TOTAL = 23
GLOBAL_TOTAL = 23
#: Raw provider rows, before any filter. ``total`` must come from the filter.
US_RAW = 34
GLOBAL_RAW = 36

#: The light table of the ``all`` scope (PLAN-screen.md §3, minimum columns).
LIGHT_COLUMNS: tuple[str, ...] = (
    "rank",
    "symbol",
    "name",
    "exchange",
    "country",
    "sector",
    "price",
    "change_pct",
    "volume",
    "turnover",
    "market_cap_usd",
    "as_of",
    "source",
)

#: TradingView fields the scanner must map (PLAN-screen.md §2).
SCANNER_FIELDS: tuple[str, ...] = (
    "name",
    "description",
    "exchange",
    "country",
    "sector",
    "close",
    "change",
    "volume",
    "Value.Traded",
    "market_cap_basic",
    "RSI",
    "beta_1_year",
    "price_52_week_high",
    "price_52_week_low",
)

#: Rows the API contract exposes on every browse hit.
SCREEN_ROW_KEYS: frozenset[str] = frozenset(
    {
        "rank",
        "symbol",
        "logo",
        "name",
        "exchange",
        "country",
        "sector",
        "price",
        "change_pct",
        "volume",
        "turnover",
        "market_cap_usd",
        "as_of",
    }
)


# ---------------------------------------------------------------------------
# Module loading with RED-shaped failures
# ---------------------------------------------------------------------------


def _load(module_name: str) -> Any:
    """Import a Screen module, turning a missing module into a RED failure."""

    try:
        return importlib.import_module(module_name)
    except ModuleNotFoundError as exc:  # pragma: no cover - RED path
        raise NotImplementedError(
            f"{module_name} is not implemented yet (Screen S0 RED): {exc}"
        ) from exc


def _require(module: Any, *names: str) -> Any:
    missing = [name for name in names if not hasattr(module, name)]
    if missing:  # pragma: no cover - RED path
        raise NotImplementedError(
            f"{module.__name__} is missing " + ", ".join(missing) + " (Screen S0 RED)"
        )
    if len(names) == 1:
        return getattr(module, names[0])
    return tuple(getattr(module, name) for name in names)


def scanner() -> Any:
    module = _load("app.screen.scanner")
    _require(module, "SCAN_COLUMNS", "parse_scan", "fetch_scan", "ScanBlocked")
    return module


def universe() -> Any:
    module = _load("app.screen.universe")
    _require(module, "build_universe", "MIN_MARKET_CAP_USD")
    return module


def rank() -> Any:
    module = _load("app.screen.rank")
    _require(module, "rank_rows", "SORTS")
    return module


def snapshot() -> Any:
    module = _load("app.screen.snapshot")
    _require(module, "SNAPSHOT_TTL_S", "DISPLAY_ROWS")
    return module


def repository() -> Any:
    module = _load("app.screen.repository")
    _require(module, "SqliteScreenRepository")
    return module


def exporter() -> Any:
    module = _load("app.screen.export")
    _require(module, "export_bundle", "DEEP_ROWS")
    return module


def screens_api() -> Any:
    module = _load("app.api.screens")
    _require(module, "router")
    return module


# ---------------------------------------------------------------------------
# Scanner fixtures
# ---------------------------------------------------------------------------


def scan_body(region: str) -> dict[str, Any]:
    """The recorded ``scanner.tradingview.com/<region>/scan`` body."""

    path = GLOBAL_SCAN_PATH if region == "global" else US_SCAN_PATH
    return json.loads(path.read_text(encoding="utf-8"))


def scan_columns(region: str = "us") -> tuple[str, ...]:
    return tuple(scan_body(region)["columns"])


def scan_rows(region: str) -> list[dict[str, Any]]:
    """Fixture rows keyed by TradingView field name, with ``symbol`` split out."""

    body = scan_body(region)
    columns = body["columns"]
    rows: list[dict[str, Any]] = []
    for entry in body["data"]:
        row = dict(zip(columns, entry["d"], strict=True))
        row["symbol"] = str(entry["s"]).split(":", 1)[1]
        row["venue"] = str(entry["s"]).split(":", 1)[0]
        rows.append(row)
    return rows


def fixture_row(region: str, symbol: str, exchange: str | None = None) -> dict[str, Any]:
    for row in scan_rows(region):
        if row["symbol"] == symbol and (exchange is None or row["exchange"] == exchange):
            return row
    raise AssertionError(f"fixture {region} has no {symbol} on {exchange}")


def shuffled_body(region: str) -> tuple[dict[str, Any], tuple[str, ...]]:
    """The same rows with the ``d`` arrays permuted, plus the new column order.

    A parser that indexes ``d`` by hardcoded positions reads the wrong cell from
    this body; only mapping through the declared column names survives.
    """

    body = scan_body(region)
    columns = tuple(body["columns"])
    order = columns[::2] + columns[1::2]
    assert order != columns, "the permutation must actually reorder the columns"
    index = {name: position for position, name in enumerate(columns)}
    data = [
        {"s": entry["s"], "d": [entry["d"][index[name]] for name in order]}
        for entry in body["data"]
    ]
    return {"totalCount": body["totalCount"], "data": data}, order


def scanner_web() -> MockWeb:
    """A ``MockWeb`` that answers both scanner endpoints with the fixtures."""

    web = MockWeb()

    def responder(request: Any) -> dict[str, Any]:
        url = str(request.url)
        if "/global/" in url:
            return scan_body("global")
        if "/america/" in url:
            return scan_body("us")
        raise AssertionError(
            f"the scanner called an unexpected URL: {url}; expected "
            "scanner.tradingview.com/america/scan or /global/scan"
        )

    web.json("tradingview", responder)
    return web


def provider_requests(web: MockWeb) -> int:
    """TradingView requests recorded so far."""

    return web.count("tradingview")


# ---------------------------------------------------------------------------
# App harness
# ---------------------------------------------------------------------------


@dataclass
class Harness:
    client: Any
    runtime: Any
    db_path: Path
    web: MockWeb

    @property
    def screen_repository(self) -> Any:
        try:
            return self.runtime.screen_repository
        except AttributeError as exc:  # pragma: no cover - RED path
            raise NotImplementedError(
                "Runtime.screen_repository is missing (Screen S0 RED): the API and "
                "the export bundle must read screens through one repository"
            ) from exc

    async def create_screen(
        self, region: str = "us", sort: str = "market_cap", **params: Any
    ) -> dict[str, Any]:
        query: dict[str, Any] = {"region": region, "sort": sort}
        query.update(params)
        response = await self.client.get("/screens", params=query)
        assert response.status_code == 200, (
            f"GET /screens?{query} -> {response.status_code}: {response.text}"
        )
        return response.json()


def _assert_mounted(application: Any) -> None:
    paths = {getattr(route, "path", "") for route in application.routes}
    if not any(path.startswith("/screens") for path in paths):
        raise NotImplementedError(
            "app.main.create_app() does not include the screens router (Screen S0 RED): "
            "GET /screens must be reachable from the ASGI app"
        )


@asynccontextmanager
async def screen_client(
    tmp_path: Path, monkeypatch: Any, web: MockWeb | None = None
) -> AsyncIterator[Harness]:
    """An ASGI app wired to the fake scanner and to a temp database."""

    screens_api()  # RED: the router module must exist before the app is built
    transport_web = web or scanner_web()

    monkeypatch.setenv("MEE_DB_PATH", str(tmp_path / "mee.db"))
    monkeypatch.setenv("MEE_EXPORT_DIR", str(tmp_path / "exports"))
    monkeypatch.setenv("MEE_PROVIDER_MODE", "live")
    install_web(monkeypatch, transport_web)

    from httpx2 import ASGITransport, AsyncClient

    from app.db import ensure_schema_async
    from app.main import create_app

    application = create_app()
    _assert_mounted(application)
    db_path = Path(tmp_path / "mee.db")
    await ensure_schema_async(db_path)

    transport = ASGITransport(app=application, raise_app_exceptions=False)
    async with AsyncClient(
        transport=transport,
        base_url="http://mee.test",
        headers={"x-api-key": "screen-s0-key"},
    ) as client:
        yield Harness(
            client=client,
            runtime=application.state.runtime,
            db_path=db_path,
            web=transport_web,
        )


# ---------------------------------------------------------------------------
# Database probes (the schema of PLAN-screen.md §5, read without the app)
# ---------------------------------------------------------------------------


def connect_db(db_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path, timeout=10.0)
    connection.row_factory = sqlite3.Row
    return connection


def _table_exists(db_path: Path, name: str) -> bool:
    with closing(connect_db(db_path)) as connection:
        found = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
        ).fetchone()
    return found is not None


def _retry(operation: Callable[[], Any], what: str) -> Any:
    last: Exception | None = None
    for _attempt in range(8):
        try:
            return operation()
        except sqlite3.OperationalError as exc:  # single-writer SQLite under WAL
            last = exc
    raise AssertionError(f"could not {what}: {last}")


def stored_screen(db_path: Path, screen_id: str) -> dict[str, Any]:
    """The ``screens`` row, or a RED failure when the table does not exist yet."""

    if not _table_exists(db_path, "screens"):  # pragma: no cover - RED path
        raise NotImplementedError(
            "the screens/screen_rows tables are not implemented yet (Screen S0 RED): "
            "PLAN-screen.md §5 declares them"
        )

    def read() -> list[dict[str, Any]]:
        with closing(connect_db(db_path)) as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM screens WHERE id = ?", (screen_id,)
                ).fetchall()
            ]

    rows = _retry(read, f"read screen {screen_id}")
    assert rows, f"no screens row for {screen_id}"
    return rows[0]


def stored_rows(db_path: Path, screen_id: str) -> list[dict[str, Any]]:
    """``screen_rows`` ordered by rank: the 20 rows the display serves."""

    if not _table_exists(db_path, "screen_rows"):  # pragma: no cover - RED path
        raise NotImplementedError(
            "the screen_rows table is not implemented yet (Screen S0 RED): "
            "PLAN-screen.md §5 declares it"
        )

    def read() -> list[dict[str, Any]]:
        with closing(connect_db(db_path)) as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM screen_rows WHERE screen_id = ? ORDER BY rank",
                    (screen_id,),
                ).fetchall()
            ]

    return _retry(read, f"read screen rows of {screen_id}")


def age_screen(db_path: Path, screen_id: str, *, seconds: int = SNAPSHOT_TTL_S + 1) -> None:
    """Move a snapshot past its TTL without sleeping (expiry test)."""

    if not _table_exists(db_path, "screens"):  # pragma: no cover - RED path
        raise NotImplementedError(
            "the screens table is not implemented yet (Screen S0 RED)"
        )
    now = datetime.now(UTC)
    created_at = datetime.fromtimestamp(now.timestamp() - seconds, UTC).isoformat(
        timespec="microseconds"
    )
    expires_at = datetime.fromtimestamp(now.timestamp() - 1, UTC).isoformat(
        timespec="microseconds"
    )

    def write() -> None:
        with closing(connect_db(db_path)) as connection:
            connection.execute(
                "UPDATE screens SET created_at = ?, expires_at = ? WHERE id = ?",
                (created_at, expires_at, screen_id),
            )
            connection.commit()

    _retry(write, f"age screen {screen_id}")


def parse_dt(value: Any) -> datetime:
    assert isinstance(value, str) and value, f"expected an ISO-8601 timestamp, got {value!r}"
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None, f"timestamp must carry an offset: {value!r}"
    return parsed


def file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# Deep-engine stand-in
# ---------------------------------------------------------------------------


def deep_row(symbol: str, rank_number: int = 1) -> dict[str, Any]:
    """One 92-column row, shaped like the pipeline's canonical table."""

    row: dict[str, Any] = {column: None for column in COLUMNS}
    row.update(
        {
            "instrument_id": f"XNAS:{symbol}",
            "symbol": symbol,
            "name": f"{symbol} Deep Holdings Inc.",
            "asset_class": "equity",
            "instrument_type": "common",
            "exchange": "XNAS",
            "mic": "XNAS",
            "currency": "USD",
            "country": "US",
            "instrument_status": "active",
            "sector": "Technology",
            "industry": "Software",
            "last_price": 100.0 + rank_number,
            "prev_close": 99.0 + rank_number,
            "change_abs": 1.0,
            "change_pct": 1.01,
            "day_volume": 1_000_000 + rank_number,
            "turnover_value": 101_000_000.0,
            "quote_as_of": "2026-10-22T20:00:00+00:00",
            "market_cap": 1_000_000_000_000.0,
            "market_cap_usd": 1_000_000_000_000.0,
            "data_quality": "complete",
            "filled_columns_count": 92,
            "verified_columns_count": 92,
            "primary_source": "screen-deep-fake",
            "as_of_achieved": "2026-10-22T21:00:00+00:00",
        }
    )
    return row


class FakeDeepEngine:
    """Records every symbol the bundle asks to enrich, and returns 92-column rows."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def __call__(self, symbols: Sequence[str]) -> list[dict[str, Any]]:
        batch = [str(symbol) for symbol in symbols]
        self.calls.append(batch)
        return [deep_row(symbol, index + 1) for index, symbol in enumerate(batch)]

    @property
    def symbols(self) -> list[str]:
        return self.calls[0] if self.calls else []


def install_deep_engine(runtime: Any, engine: Callable[..., Any]) -> None:
    """Inject the deep engine at the composition root (never inside a route)."""

    try:
        getattr(runtime, "screen_deep_engine")
    except AttributeError as exc:  # pragma: no cover - RED path
        raise NotImplementedError(
            "Runtime.screen_deep_engine is missing (Screen S0 RED): the export route "
            "must take the deep engine from the runtime so tests can record the "
            "symbols it enriches"
        ) from exc
    runtime.screen_deep_engine = engine


def artifact_kinds(formats: Sequence[str] = DATA_FORMATS) -> set[str]:
    return {f"{scope}_{fmt}" for scope in SCOPES for fmt in formats}
