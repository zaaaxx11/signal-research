"""P11K — ``scripts/screen-facts.py`` grows a pool and a retry mode.

The P11J full fill left 578 of ~3,900 symbols blocked by Cloudflare, 43 empty
and 12 unserved: those symbols have **no row**, and the 3,269 that answered must
not be scraped again. This suite locks the two CLI promises:

* ``--proxy-pool`` hands the ranked pool to :class:`FinvizAdapter`, so a run
  climbs ``direct → r.jina.ai → free → paid`` and reports the exit that answered;
* ``--retry-missing`` reads the facts store first and spends requests only on
  the symbols without a fresh row — a fresh row is evidence, not a candidate;
* a refused symbol writes nothing: no fake value, no null row, no parse of a
  challenge or a markdown reader body.

Everything here is offline: the single HTTP seam is a fake that answers by exit
and host, and the database is a throwaway SQLite file.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, Callable

import pytest
from alembic import command

from app.db import alembic_config
from app.providers import http as provider_http
from app.screen import facts as F

ROOT = Path(__file__).resolve().parents[1]
CLI_PATH = ROOT / "scripts" / "screen-facts.py"
PAGE = (
    (ROOT / "tests" / "fixtures" / "finviz_stock_aapl.html").read_text(encoding="utf-8")
    + (ROOT / "tests" / "fixtures" / "finviz_stock_facts_aapl.html").read_text(encoding="utf-8")
)
CHALLENGE = (
    "<!DOCTYPE html><html><head><title>Just a moment...</title></head>"
    "<body>cf-chl challenge-platform</body></html>"
)
MARKDOWN = (
    "Title: AAPL - Finviz\n\nURL Source: http://finviz.com/stock?t=AAPL\n\n"
    "Markdown Content:\nShort Float 0.83%\n"
)
EXPECTED = {"short_float": 0.83, "insider_own": 0.12, "inst_own": 68.93}


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


def load_cli(name: str = "screen_facts_cli_p11k") -> Any:
    """Import ``scripts/screen-facts.py`` without making it a package."""

    spec = importlib.util.spec_from_file_location(name, CLI_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Settings:
    """The slice of ``app.config.Settings`` the CLI reads, pinned for a temp DB."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = str(db_path)
        self.proxy_pool = None
        self.proxy_attempts = 3
        self.proxy_direct_cooldown_s = 600.0


def prepare_db(path: Path) -> None:
    command.upgrade(alembic_config(path), "head")


class _Response:
    def __init__(self, status: int, *, text: str = "") -> None:
        self.status_code = status
        self.content = text.encode("utf-8")
        self.headers = {"content-type": "text/html"}


class _Client:
    def __init__(
        self,
        proxy: str | None,
        responder: Callable[[str | None, str], _Response],
        calls: list[dict[str, Any]],
    ) -> None:
        self.proxy = proxy
        self._responder = responder
        self._calls = calls

    async def __aenter__(self) -> "_Client":
        return self

    async def __aexit__(self, *_args: Any) -> None:
        return None

    async def get(self, url: str) -> _Response:
        self._calls.append({"proxy": self.proxy, "url": url})
        return self._responder(self.proxy, url)


def install(
    monkeypatch: pytest.MonkeyPatch,
    responder: Callable[[str | None, str], _Response],
    calls: list[dict[str, Any]],
) -> None:
    def build_client(**kwargs: Any) -> _Client:
        return _Client(kwargs.get("proxy"), responder, calls)

    monkeypatch.setattr(provider_http, "build_client", build_client)


def server(monkeypatch: pytest.MonkeyPatch, db_path: Path, calls: list[dict[str, Any]]):
    cli = load_cli()
    monkeypatch.setattr(cli, "get_settings", lambda: Settings(db_path))
    return cli


def run_json(cli: Any, *argv: str, capsys: Any) -> tuple[int, dict[str, Any]]:
    code = cli.main(["--json", "--sleep", "0", *argv])
    out = capsys.readouterr().out
    return code, json.loads(out)


def stored_rows(db_path: Path) -> dict[str, dict[str, Any]]:
    with closing(sqlite3.connect(db_path)) as connection:
        connection.row_factory = sqlite3.Row
        return {
            str(row["symbol"]): {
                "fields": json.loads(row["fields"]),
                "fetched_at": row["fetched_at"],
            }
            for row in connection.execute("SELECT * FROM screen_facts")
        }


def seed(db_path: Path, symbol: str, values: dict[str, float]) -> None:
    """Write one fresh row through the store, exactly as a previous run would."""

    import asyncio

    async def write() -> None:
        store = F.SqliteFactsStore(db_path)
        try:
            await store.upsert(F.facts_from_values(symbol, values))
        finally:
            await store.aclose()

    asyncio.run(write())


def writes_page(_proxy: str | None, url: str) -> _Response:
    return _Response(200, text=PAGE) if url.startswith("https://r.jina.ai/") else _Response(403, text=CHALLENGE)


# ---------------------------------------------------------------------------
# --proxy-pool: the scrape gets an egress ladder
# ---------------------------------------------------------------------------


def test_direct_blocked_is_filled_through_the_reader(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: Any
) -> None:
    calls: list[dict[str, Any]] = []
    install(monkeypatch, writes_page, calls)
    cli = server(monkeypatch, tmp_path / "facts.db", calls)
    monkeypatch.setattr(cli, "ensure_head", lambda _path: "0005")

    code, document = run_json(cli, "--symbols", "AAPL", capsys=capsys)

    assert code == 0, document
    assert document["header"].startswith("screen_facts fill · 1 filled")
    assert document["results"][0]["egress"] == "jina"
    assert document["results"][0]["status"] == "written"
    assert document["blocked"] == 0
    assert [call["url"].startswith("https://r.jina.ai/") for call in calls] == [False, True]
    assert stored_rows(tmp_path / "facts.db")["AAPL"]["fields"] == EXPECTED


def test_every_rung_refused_writes_nothing_and_reports_blocked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: Any
) -> None:
    calls: list[dict[str, Any]] = []

    def refused(proxy: str | None, url: str) -> _Response:
        if url.startswith("https://r.jina.ai/"):
            return _Response(429)
        return _Response(403, text=CHALLENGE)

    install(monkeypatch, refused, calls)
    cli = server(monkeypatch, tmp_path / "facts.db", calls)
    monkeypatch.setattr(cli, "ensure_head", lambda _path: "0005")

    code, document = run_json(
        cli,
        "--symbols",
        "MSFT",
        "--proxy-pool",
        "http://p1:1",
        "--proxy-attempts",
        "1",
        capsys=capsys,
    )

    assert code == 1
    assert document["outcomes"] == {"written": 0, "kept": 0, "empty": 0, "failed": 1}
    assert document["blocked"] == 1, "a Cloudflare refusal must be reported as blocked"
    assert document["results"][0]["status"] == "failed"
    assert stored_rows(tmp_path / "facts.db") == {}, "a refused page writes no row"
    assert [call["proxy"] for call in calls] == [None, None, "http://p1:1"]


def test_a_markdown_reader_body_is_reported_and_not_written(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: Any
) -> None:
    calls: list[dict[str, Any]] = []

    def markdown(proxy: str | None, url: str) -> _Response:
        if url.startswith("https://r.jina.ai/"):
            return _Response(200, text=MARKDOWN)
        return _Response(403, text=CHALLENGE)

    install(monkeypatch, markdown, calls)
    cli = server(monkeypatch, tmp_path / "facts.db", calls)
    monkeypatch.setattr(cli, "ensure_head", lambda _path: "0005")

    code, document = run_json(cli, "--symbols", "AAPL", capsys=capsys)

    assert code == 1
    assert document["results"][0]["kind"] == "schema_drift"
    assert "markdown" in document["results"][0]["detail"].lower()
    assert stored_rows(tmp_path / "facts.db") == {}


def test_the_pool_is_reported_with_the_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: Any
) -> None:
    calls: list[dict[str, Any]] = []
    install(monkeypatch, writes_page, calls)
    cli = server(monkeypatch, tmp_path / "facts.db", calls)
    monkeypatch.setattr(cli, "ensure_head", lambda _path: "0005")

    _code, document = run_json(
        cli,
        "--symbols",
        "AAPL",
        "--proxy-pool",
        "http://p1:1,http://p2:2",
        capsys=capsys,
    )

    assert document["egress"]["pool"]["free_urls"] == 2
    assert document["egress"]["rungs"] == ["direct", "jina", "free", "paid"]


# ---------------------------------------------------------------------------
# --retry-missing: only what has no fresh row
# ---------------------------------------------------------------------------


def test_retry_missing_skips_a_fresh_row_and_fetches_the_missing_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: Any
) -> None:
    db_path = tmp_path / "facts.db"
    prepare_db(db_path)
    seed(db_path, "AAPL", EXPECTED)
    before = stored_rows(db_path)["AAPL"]["fetched_at"]

    calls: list[dict[str, Any]] = []
    install(monkeypatch, writes_page, calls)
    cli = server(monkeypatch, db_path, calls)
    monkeypatch.setattr(cli, "ensure_head", lambda _path: "0005")

    code, document = run_json(
        cli, "--symbols", "AAPL,MSFT", "--retry-missing", capsys=capsys
    )

    assert code == 0, document
    assert document["retry"] == {"requested": 2, "missing": 1, "fresh": 1}
    assert [call["url"] for call in calls if call["url"].startswith("https://finviz.com/")] == [
        "https://finviz.com/stock?t=MSFT"
    ], "the fresh AAPL row must not be scraped again"
    rows = stored_rows(db_path)
    assert rows["AAPL"]["fetched_at"] == before
    assert rows["MSFT"]["fields"] == EXPECTED


def test_retry_missing_with_nothing_missing_sends_no_request(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: Any
) -> None:
    db_path = tmp_path / "facts.db"
    prepare_db(db_path)
    seed(db_path, "AAPL", EXPECTED)

    calls: list[dict[str, Any]] = []
    install(monkeypatch, writes_page, calls)
    cli = server(monkeypatch, db_path, calls)
    monkeypatch.setattr(cli, "ensure_head", lambda _path: "0005")

    code, document = run_json(cli, "--symbols", "AAPL", "--retry-missing", capsys=capsys)

    assert code == 0
    assert calls == [], "a fresh row is evidence; the retry must spend no request"
    assert document["retry"] == {"requested": 1, "missing": 0, "fresh": 1}
    assert document["results"] == []


def test_retry_missing_leaves_a_blocked_symbol_absent_never_null(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: Any
) -> None:
    db_path = tmp_path / "facts.db"
    prepare_db(db_path)
    seed(db_path, "AAPL", EXPECTED)

    calls: list[dict[str, Any]] = []

    def refused(proxy: str | None, url: str) -> _Response:
        return _Response(403, text=CHALLENGE)

    install(monkeypatch, refused, calls)
    cli = server(monkeypatch, db_path, calls)
    monkeypatch.setattr(cli, "ensure_head", lambda _path: "0005")

    code, document = run_json(
        cli, "--symbols", "AAPL,MSFT", "--retry-missing", capsys=capsys
    )

    assert code == 1
    assert document["retry"]["missing"] == 1
    assert [entry["status"] for entry in document["results"]] == ["failed"]
    rows = stored_rows(db_path)
    assert set(rows) == {"AAPL"}, "a blocked symbol must not gain a null-filled row"
    assert rows["AAPL"]["fields"] == EXPECTED
