"""``GET /logos/{symbol}``: a brand mark on its own URL, cached for thirty days.

The route exists so a 100-row panel can stop carrying 64 KB of base64 decoration:
the projection drops ``logo`` entirely and every row keeps an ``<img src>``. The
tests below pin the four promises the route makes:

* **a ladder** — the local store, then the vendored 35 assets, then one fixed
  upstream keyed by the symbol's website domain, then the monogram;
* **a guaranteed answer** — a symbol with no mark is a 200 with a generated SVG,
  never a 404, so a row can never have a hole;
* **a disk cache with rules** — one fetch per symbol, atomic writes, a size
  ceiling, and a corrupted file replaced instead of served;
* **a hostile input that cannot escape** — a traversal-shaped symbol or a
  private-address website is refused before a filesystem or network call.

No test here touches the network: the fetcher is injected everywhere, which is
also how the "one upstream call" assertions are counted.

``ORCL`` is the symbol the upstream tests use on purpose: ``AAPL``, ``NVDA`` and
``MSFT`` are in the vendored set, so a test written against one of them would
pass without ever reaching the rung it claims to prove.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator
from urllib.parse import quote

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.api import logos
from app.auth import ApiError
from app.config import Settings

#: The symbol whose website drives the upstream rung; not vendored, not folded.
TICKER = "ORCL"
WEBSITE = "https://www.oracle.com"

#: A body that passes the magic-byte check. The route never decodes an image, so
#: a header plus a stub chunk is a PNG as far as this contract is concerned.
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"\x00" * 32
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
HTML = b"<!doctype html><html><body>502 Bad Gateway</body></html>"


@dataclass
class Fetcher:
    """The injected upstream: counts every call and answers with one body."""

    body: bytes | None = PNG
    delay: float = 0.0
    calls: list[tuple[str, float]] = field(default_factory=list)

    async def __call__(self, url: str, timeout_s: float) -> bytes | None:
        self.calls.append((url, timeout_s))
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.body


def settings_for(tmp_path: Path, **overrides: Any) -> Settings:
    """Settings pointed at a temp dir; upstream is off unless a test says so."""

    defaults: dict[str, Any] = {
        "db_path": tmp_path / "mee.db",
        "export_dir": tmp_path / "exports",
        "logo_dir": tmp_path / "logos",
        "logo_upstream": False,
        "logo_upstream_timeout_s": 0.5,
    }
    defaults.update(overrides)
    return Settings(**defaults)


def depth_file(tmp_path: Path, symbols: dict[str, dict[str, Any]]) -> Path:
    """A depth store with the given rows; the shape ``load_depth`` parses."""

    path = tmp_path / "depth.json"
    path.write_text(
        json.dumps({"as_of": "2026-10-31T00:00:00+00:00", "symbols": symbols}),
        encoding="utf-8",
    )
    return path


def build_app(tmp_path: Path, *, fetcher: Fetcher | None, **overrides: Any) -> SimpleNamespace:
    """An app with only the logo router, a temp store and an injected fetcher."""

    settings = settings_for(tmp_path, **overrides)
    store = logos.LogoStore(settings, fetcher=fetcher)
    application = FastAPI()
    application.include_router(logos.router)

    # The one handler ``create_app`` adds for this route; without it a raised
    # ``ApiError`` would surface as a traceback instead of the 404 envelope.
    @application.exception_handler(ApiError)
    async def _api_error(_request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.error, "errorMessage": exc.message},
        )

    application.state.logos = store
    return SimpleNamespace(app=application, store=store, settings=settings)


@contextmanager
def serve(
    tmp_path: Path,
    *,
    fetcher: Fetcher | None = None,
    upstream: bool = False,
    **overrides: Any,
) -> Iterator[SimpleNamespace]:
    harness = build_app(tmp_path, fetcher=fetcher, logo_upstream=upstream, **overrides)
    with TestClient(harness.app) as client:
        harness.client = client
        yield harness


def _files(directory: Path) -> list[Path]:
    return sorted(p for p in directory.rglob("*") if p.is_file()) if directory.exists() else []


def _tickers(count: int) -> list[str]:
    """``count`` distinct four-letter tickers (monogram labels cannot collide)."""

    return [
        "Q" + chr(65 + index % 26) + chr(65 + (index // 26) % 26) + chr(65 + (index // 676) % 26)
        for index in range(count)
    ]


# ---------------------------------------------------------------------------
# The ladder
# ---------------------------------------------------------------------------


def test_the_ladder_prefers_the_local_store_over_the_vendored_asset(tmp_path: Path) -> None:
    """AAPL has both a vendored PNG and a store file: the store wins."""

    local = tmp_path / "logos"
    local.mkdir(parents=True)
    (local / "AAPL.png").write_bytes(PNG + b"store")
    fetcher = Fetcher()

    with serve(tmp_path, fetcher=fetcher) as harness:
        first = harness.client.get("/logos/AAPL")
        # A vendor asset with no store counterpart is the next rung.
        second = harness.client.get("/logos/NVDA")

    assert first.status_code == second.status_code == 200
    assert first.content == PNG + b"store", "the store file is served byte for byte"
    assert first.headers["x-logo-source"] == "store"
    assert second.headers["x-logo-source"] == "vendored"
    assert second.content.startswith(b"\x89PNG"), "the vendored 35-file set still works"
    assert fetcher.calls == []


def test_the_vendored_set_answers_with_the_file_s_own_type(tmp_path: Path) -> None:
    """``META.jpg`` is served as ``image/jpeg``, read from its bytes."""

    assert len(list(logos.VENDORED_DIR.iterdir())) >= 30, "the vendored set is still there"

    with serve(tmp_path) as harness:
        response = harness.client.get("/logos/META")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/jpeg")
    assert response.content.startswith(b"\xff\xd8\xff")


def test_the_vendored_directory_is_the_one_branding_resolves() -> None:
    from app.branding import logos as branding

    assert logos.VENDORED_DIR == branding._UI_LOGO_DIR, (
        "GET /logos and the JSON `logo` field must read one directory"
    )


def test_a_symbol_with_no_asset_answers_a_monogram_svg(tmp_path: Path) -> None:
    fetcher = Fetcher()

    with serve(tmp_path, fetcher=fetcher) as harness:
        response = harness.client.get("/logos/NOTATICKER")

    assert response.status_code == 200, "a missing mark is never a 404 path"
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert response.content.startswith(b"<svg")
    assert b"xmlns=" in response.content
    assert fetcher.calls == []


# ---------------------------------------------------------------------------
# Upstream, once
# ---------------------------------------------------------------------------


def test_an_upstream_answer_is_stored_and_never_refetched(tmp_path: Path) -> None:
    fetcher = Fetcher(body=PNG)
    depth = depth_file(tmp_path, {TICKER: {"website": WEBSITE}})

    with serve(
        tmp_path,
        fetcher=fetcher,
        upstream=True,
        screen_depth=True,
        screen_depth_store_path=str(depth),
    ) as harness:
        first = harness.client.get(f"/logos/{TICKER}")
        second = harness.client.get(f"/logos/{TICKER}")
        third = harness.client.get(f"/logos/{TICKER}")

    assert first.status_code == second.status_code == third.status_code == 200
    assert first.headers["x-logo-source"] == "upstream"
    assert second.headers["x-logo-source"] == "store", "the second read is the cache"
    assert first.content == second.content == PNG
    assert len(fetcher.calls) == 1, f"one fetch, then the file: {fetcher.calls}"
    assert (tmp_path / "logos" / f"{TICKER}.png").is_file()
    assert harness.store.stats()["count"] == 1


def test_a_preplaced_file_costs_zero_upstream_calls(tmp_path: Path) -> None:
    local = tmp_path / "logos"
    local.mkdir(parents=True)
    (local / f"{TICKER}.png").write_bytes(PNG)
    fetcher = Fetcher()
    depth = depth_file(tmp_path, {TICKER: {"website": WEBSITE}})

    with serve(
        tmp_path,
        fetcher=fetcher,
        upstream=True,
        screen_depth=True,
        screen_depth_store_path=str(depth),
    ) as harness:
        response = harness.client.get(f"/logos/{TICKER}")

    assert response.status_code == 200
    assert fetcher.calls == [], "a symbol with a file is never refetched"


def test_the_website_comes_from_the_depth_store_and_keys_a_fixed_host(tmp_path: Path) -> None:
    fetcher = Fetcher()
    depth = depth_file(tmp_path, {TICKER: {"website": f"{WEBSITE}/investors"}})

    with serve(
        tmp_path,
        fetcher=fetcher,
        upstream=True,
        screen_depth=True,
        screen_depth_store_path=str(depth),
    ) as harness:
        response = harness.client.get(f"/logos/{TICKER}")

    assert response.status_code == 200
    assert len(fetcher.calls) == 1
    url, timeout_s = fetcher.calls[0]
    assert url == logos.UPSTREAM_TEMPLATE.format(domain="www.oracle.com")
    assert url.startswith(f"https://{logos.UPSTREAM_HOST}/"), "the host is fixed in code"
    assert "size=64" in url
    assert timeout_s == pytest.approx(0.5)


def test_a_symbol_with_no_website_never_calls_upstream(tmp_path: Path) -> None:
    fetcher = Fetcher()
    depth = depth_file(tmp_path, {"ZZZZ": {"pe": 12.0}})

    with serve(
        tmp_path,
        fetcher=fetcher,
        upstream=True,
        screen_depth=True,
        screen_depth_store_path=str(depth),
    ) as harness:
        response = harness.client.get("/logos/ZZZZ")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert fetcher.calls == [], "no domain, no fetch"


def test_a_failed_fetch_is_remembered_briefly_not_retried_per_request(tmp_path: Path) -> None:
    fetcher = Fetcher(body=None)
    depth = depth_file(tmp_path, {TICKER: {"website": WEBSITE}})

    with serve(
        tmp_path,
        fetcher=fetcher,
        upstream=True,
        screen_depth=True,
        screen_depth_store_path=str(depth),
    ) as harness:
        first = harness.client.get(f"/logos/{TICKER}")
        second = harness.client.get(f"/logos/{TICKER}")

    assert first.status_code == second.status_code == 200
    assert second.headers["content-type"].startswith("image/svg+xml")
    assert len(fetcher.calls) == 1, "an upstream outage must not be re-probed per row"


# ---------------------------------------------------------------------------
# The disk cache: corruption, type, size
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("corrupt", [b"", HTML, b"\x00" * 64])
def test_a_corrupted_cache_file_is_replaced_not_served(tmp_path: Path, corrupt: bytes) -> None:
    local = tmp_path / "logos"
    local.mkdir(parents=True)
    (local / f"{TICKER}.png").write_bytes(corrupt)
    fetcher = Fetcher(body=PNG)
    depth = depth_file(tmp_path, {TICKER: {"website": WEBSITE}})

    with serve(
        tmp_path,
        fetcher=fetcher,
        upstream=True,
        screen_depth=True,
        screen_depth_store_path=str(depth),
    ) as harness:
        response = harness.client.get(f"/logos/{TICKER}")

    assert response.status_code == 200
    assert response.headers["x-logo-source"] == "upstream"
    assert response.content == PNG
    assert not response.content.lstrip().startswith(b"<"), "HTML is never served as an image"
    assert (local / f"{TICKER}.png").read_bytes() == PNG, "the bad file was replaced, not kept"
    assert len(fetcher.calls) == 1


def test_a_corrupted_file_without_upstream_falls_back_to_the_monogram(tmp_path: Path) -> None:
    local = tmp_path / "logos"
    local.mkdir(parents=True)
    (local / f"{TICKER}.png").write_bytes(HTML)

    with serve(tmp_path) as harness:
        response = harness.client.get(f"/logos/{TICKER}")
        again = harness.client.get(f"/logos/{TICKER}")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert not (local / f"{TICKER}.png").exists(), "an unservable file is removed, not retried forever"
    assert response.content == again.content, "the monogram is deterministic"


def test_a_non_image_upstream_answer_is_not_cached(tmp_path: Path) -> None:
    fetcher = Fetcher(body=HTML)
    depth = depth_file(tmp_path, {TICKER: {"website": WEBSITE}})

    with serve(
        tmp_path,
        fetcher=fetcher,
        upstream=True,
        screen_depth=True,
        screen_depth_store_path=str(depth),
    ) as harness:
        response = harness.client.get(f"/logos/{TICKER}")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert _files(tmp_path / "logos") == [], "an error page must never become a cached logo"


def test_an_oversized_upstream_answer_is_refused(tmp_path: Path) -> None:
    fetcher = Fetcher(body=PNG + b"\x00" * 4096)
    depth = depth_file(tmp_path, {TICKER: {"website": WEBSITE}})

    with serve(
        tmp_path,
        fetcher=fetcher,
        upstream=True,
        logo_max_bytes=1024,
        screen_depth=True,
        screen_depth_store_path=str(depth),
    ) as harness:
        response = harness.client.get(f"/logos/{TICKER}")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert _files(tmp_path / "logos") == [], "an oversized body is refused, not written"


def test_the_store_is_bounded_and_reports_its_size(tmp_path: Path) -> None:
    fetcher = Fetcher(body=PNG)  # 56 bytes
    symbols = {f"S{index}": {"website": f"https://s{index}.example"} for index in range(10)}
    depth = depth_file(tmp_path, symbols)

    with serve(
        tmp_path,
        fetcher=fetcher,
        upstream=True,
        logo_store_max_bytes=180,
        screen_depth=True,
        screen_depth_store_path=str(depth),
    ) as harness:
        for symbol in symbols:
            assert harness.client.get(f"/logos/{symbol}").status_code == 200
        stats = harness.store.stats()

    assert stats["bytes"] <= 180, f"the store must stay under its ceiling: {stats}"
    assert 0 < stats["count"] < len(symbols), f"old entries are evicted: {stats}"
    assert stats["max_bytes"] == 180
    assert sum(p.stat().st_size for p in _files(tmp_path / "logos")) == stats["bytes"]


def test_the_write_is_atomic_so_a_reader_never_sees_a_partial_file(tmp_path: Path) -> None:
    fetcher = Fetcher(body=PNG)
    depth = depth_file(tmp_path, {TICKER: {"website": WEBSITE}})

    with serve(
        tmp_path,
        fetcher=fetcher,
        upstream=True,
        screen_depth=True,
        screen_depth_store_path=str(depth),
    ) as harness:
        assert harness.client.get(f"/logos/{TICKER}").status_code == 200

    leftovers = [p.name for p in _files(tmp_path / "logos") if p.name.startswith(".")]
    assert leftovers == [], f"a temp file must be renamed, not left behind: {leftovers}"
    assert [p.name for p in _files(tmp_path / "logos")] == [f"{TICKER}.png"]


# ---------------------------------------------------------------------------
# Adversarial: the website field
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "website",
    [
        "http://127.0.0.1/",
        "http://127.0.0.1:8099/",
        "http://169.254.169.254/latest/meta-data/",
        "http://localhost:8080",
        "http://10.0.0.5/",
        "http://192.168.1.1/",
        "http://[::1]/",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "https://user:pass@evil.example/",
        "https://metadata.google.internal/",
        "https://printer.local/",
        "https://apple.com@127.0.0.1/",
        "0.0.0.0",
    ],
)
def test_a_private_or_non_http_website_is_never_fetched(tmp_path: Path, website: str) -> None:
    fetcher = Fetcher()
    depth = depth_file(tmp_path, {TICKER: {"website": website}})

    with serve(
        tmp_path,
        fetcher=fetcher,
        upstream=True,
        screen_depth=True,
        screen_depth_store_path=str(depth),
    ) as harness:
        response = harness.client.get(f"/logos/{TICKER}")

    assert response.status_code == 200, "a refused website degrades, it does not fail"
    assert fetcher.calls == [], f"{website!r} must never reach the upstream"
    assert _files(tmp_path / "logos") == []


def test_the_upstream_url_cannot_be_retargeted_by_the_website() -> None:
    """The host half of the URL is code; only ``{domain}`` comes from data."""

    template = logos.UPSTREAM_TEMPLATE
    assert template.startswith(f"https://{logos.UPSTREAM_HOST}/")
    assert "https%3A%2F%2F{domain}" in template, "the domain rides as a percent-encoded url="
    assert template.count("{domain}") == 1
    # Whatever a website carries, only its hostname can survive into the slot:
    # no second host, no port, no query delimiter, no userinfo.
    for hostile in (
        "evil.example&size=4096",
        "evil.example#x",
        "evil.example/x",
        "a@b.example",
        "evil.example:8443",
        "evil.example?x=1",
    ):
        domain = logos.safe_domain(f"https://{hostile}")
        assert domain in (None, "evil.example"), hostile
        if domain is not None:
            assert set(domain) <= set("abcdefghijklmnopqrstuvwxyz0123456789.-")


def test_safe_domain_reads_a_bare_or_schemed_website() -> None:
    assert logos.safe_domain("https://www.apple.com") == "www.apple.com"
    assert logos.safe_domain("http://apple.com/") == "apple.com"
    assert logos.safe_domain("apple.com") == "apple.com"
    assert logos.safe_domain("HTTPS://WWW.APPLE.COM/investors") == "www.apple.com"
    assert logos.safe_domain("") is None
    assert logos.safe_domain(None) is None


# ---------------------------------------------------------------------------
# Adversarial: the symbol
# ---------------------------------------------------------------------------


HOSTILE_SYMBOLS = [
    "../../etc/passwd",
    "..",
    ".",
    "../",
    "A B",
    "A" * 200,
    "AAPL/../../etc/passwd",
    "AAPL\x00.png",
    "%2e%2e%2fetc%2fpasswd",
    "AAPL\n",
    " AAPL",
    ".hidden",
    "-AAPL",
    "AAPL;rm -rf /",
]


@pytest.mark.parametrize("symbol", HOSTILE_SYMBOLS)
def test_a_hostile_symbol_is_refused_without_touching_disk_or_network(
    tmp_path: Path, symbol: str
) -> None:
    fetcher = Fetcher()
    depth = depth_file(tmp_path, {symbol: {"website": WEBSITE}})

    with serve(
        tmp_path,
        fetcher=fetcher,
        upstream=True,
        screen_depth=True,
        screen_depth_store_path=str(depth),
    ) as harness:
        response = harness.client.get("/logos/" + quote(symbol, safe=""))

    assert response.status_code in (404, 200)
    if response.status_code == 200:
        # A 200 here is the transport's doing, not a hole: the URL parser strips
        # a trailing newline before the request is built, so "AAPL\n" arrived as
        # the ticker AAPL. What matters either way is that nothing was fetched
        # and nothing was written outside the store.
        assert response.headers["content-type"].startswith("image/")
    else:
        # Either this route's envelope (a plain segment it refused) or Starlette's
        # own 404 for a path that could not match ``/logos/{symbol}`` at all
        # because it carried a separator.
        body = response.json()
        assert body.get("error", body.get("detail")) in {"unknown_symbol", "Not Found"}
    assert fetcher.calls == [], "a hostile symbol must not reach the upstream"
    assert _files(tmp_path / "logos") == [], "and must not write anything"
    assert not (tmp_path / "etc").exists()
    assert not (tmp_path.parent / "etc").exists()


def test_normalize_symbol_is_the_one_gate() -> None:
    assert logos.normalize_symbol("aapl") == "AAPL"
    assert logos.normalize_symbol("BRK.B") == "BRK.B"
    assert logos.normalize_symbol("BTC-USD") == "BTC-USD"
    assert logos.normalize_symbol("^GSPC") == "^GSPC"
    assert logos.normalize_symbol("JPM/PC") == "JPM-PC", "a preferred share folds onto a dash"
    assert logos.normalize_symbol("jpm/pc") == "JPM-PC"
    for bad in HOSTILE_SYMBOLS:
        with pytest.raises(logos.InvalidSymbol):
            logos.normalize_symbol(bad)


def test_a_preferred_share_ticker_still_gets_an_answer(tmp_path: Path) -> None:
    """``JPM/PC`` is 34 of a 100-row US panel; a slash must not mean a broken row."""

    with serve(tmp_path) as harness:
        response = harness.client.get("/logos/JPM/PC")

    assert response.status_code == 200, "a share class is a ticker, not a path"
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert harness.store.stats()["count"] == 0, "a monogram writes nothing"


def test_a_mixed_case_symbol_is_one_cache_entry(tmp_path: Path) -> None:
    fetcher = Fetcher()
    depth = depth_file(tmp_path, {TICKER: {"website": WEBSITE}})

    with serve(
        tmp_path,
        fetcher=fetcher,
        upstream=True,
        screen_depth=True,
        screen_depth_store_path=str(depth),
    ) as harness:
        lower = harness.client.get(f"/logos/{TICKER.lower()}")
        upper = harness.client.get(f"/logos/{TICKER}")
        mixed = harness.client.get(f"/logos/{TICKER.title()}")

    assert lower.status_code == upper.status_code == mixed.status_code == 200
    assert lower.content == upper.content == mixed.content
    assert lower.headers["etag"] == upper.headers["etag"] == mixed.headers["etag"]
    assert len(fetcher.calls) == 1, f"case is normalised before the cache: {fetcher.calls}"
    assert [p.name for p in _files(tmp_path / "logos")] == [f"{TICKER}.png"]


# ---------------------------------------------------------------------------
# Adversarial: concurrency
# ---------------------------------------------------------------------------


async def test_fifty_concurrent_requests_make_one_upstream_call(tmp_path: Path) -> None:
    from httpx2 import ASGITransport, AsyncClient

    fetcher = Fetcher(body=PNG, delay=0.05)
    harness = build_app(
        tmp_path,
        fetcher=fetcher,
        logo_upstream=True,
        screen_depth=True,
        screen_depth_store_path=str(depth_file(tmp_path, {TICKER: {"website": WEBSITE}})),
    )

    async with AsyncClient(
        transport=ASGITransport(app=harness.app), base_url="http://mee.test"
    ) as client:
        responses = await asyncio.gather(
            *(client.get(f"/logos/{TICKER}") for _ in range(50))
        )

    assert all(response.status_code == 200 for response in responses)
    assert len({response.content for response in responses}) == 1, "one body for fifty callers"
    assert len(fetcher.calls) == 1, f"single-flight failed: {len(fetcher.calls)} fetches"
    assert len(_files(tmp_path / "logos")) == 1


# ---------------------------------------------------------------------------
# Headers
# ---------------------------------------------------------------------------


def test_the_response_is_cacheable_for_thirty_days_and_carries_a_strong_etag(
    tmp_path: Path,
) -> None:
    fetcher = Fetcher()

    with serve(tmp_path, fetcher=fetcher) as harness:
        response = harness.client.get("/logos/NOTATICKER")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "public, max-age=2592000, immutable"
    assert int(response.headers["cache-control"].split("max-age=")[1].split(",")[0]) == 2_592_000
    etag = response.headers["etag"]
    assert etag.startswith('"') and etag.endswith('"'), f"a strong ETag is quoted: {etag!r}"
    assert not etag.startswith("W/")
    assert len(etag) >= 16
    assert response.headers["content-length"] == str(len(response.content))


def test_if_none_match_answers_304_with_no_body(tmp_path: Path) -> None:
    with serve(tmp_path) as harness:
        first = harness.client.get("/logos/NOTATICKER")
        second = harness.client.get(
            "/logos/NOTATICKER", headers={"if-none-match": first.headers["etag"]}
        )
        moved = harness.client.get("/logos/NOTATICKER", headers={"if-none-match": '"other"'})

    assert second.status_code == 304, second.text
    assert second.content == b""
    assert second.headers["etag"] == first.headers["etag"]
    assert second.headers["cache-control"] == first.headers["cache-control"]
    assert moved.status_code == 200


def test_the_etag_moves_when_the_mark_changes(tmp_path: Path) -> None:
    local = tmp_path / "logos"
    local.mkdir(parents=True)
    (local / "AAPL.png").write_bytes(PNG)

    with serve(tmp_path) as harness:
        before = harness.client.get("/logos/AAPL")
        (local / "AAPL.png").write_bytes(PNG + b"new")
        after = harness.client.get("/logos/AAPL")

    assert before.status_code == after.status_code == 200
    assert before.headers["etag"] != after.headers["etag"]


# ---------------------------------------------------------------------------
# The payload win
# ---------------------------------------------------------------------------


def test_a_hundred_row_projection_carries_no_logo_bytes_and_every_row_has_a_url(
    tmp_path: Path,
) -> None:
    """The whole point: 100 rows, zero base64, one URL per row that answers 200."""

    from app.api.screens import _with_logos

    rows = [
        {"symbol": ticker, "name": f"Company {index}", "price": 10.0 + index}
        for index, ticker in enumerate(_tickers(100))
    ]
    document = {"rows": rows, "returned": 100}

    projected = _with_logos(document, columns=("symbol", "name", "price"))
    encoded = json.dumps(projected, separators=(",", ":"))
    assert "logo" not in encoded, "a projection that did not ask for the mark must not carry it"
    assert "data:image" not in encoded, "no base64 bytes in a numeric panel"

    decorated = _with_logos({"rows": [dict(row) for row in rows]})
    bloated = json.dumps(decorated, separators=(",", ":"))
    saved = len(bloated.encode()) - len(encoded.encode())
    assert saved > 60_000, f"the mark costs ~640 B per row; measured saving {saved} B"

    with serve(tmp_path) as harness:
        answers = [harness.client.get(f"/logos/{row['symbol']}") for row in rows]

    assert all(response.status_code == 200 for response in answers)
    assert all(response.headers["content-type"].startswith("image/") for response in answers)
    assert len({response.content for response in answers}) == 100, "one distinct mark per ticker"


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


def test_create_app_registers_the_logo_route(tmp_path: Path) -> None:
    from app.main import create_app

    application = create_app(
        Settings(db_path=tmp_path / "mee.db", export_dir=tmp_path / "exports")
    )
    paths = {getattr(route, "path", None) for route in application.routes}
    assert "/logos/{symbol:path}" in paths


def test_the_settings_default_to_upstream_on_with_a_timeout() -> None:
    settings = Settings()
    assert settings.logo_upstream is True
    assert settings.logo_upstream_timeout_s > 0
    assert settings.logo_max_bytes >= 1024
    assert settings.logo_store_max_bytes >= settings.logo_max_bytes


def test_the_cache_follows_the_depth_store_when_no_data_dir_is_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The logo store must land on the data volume, not in the checkout.

    This deployment pins each store by absolute path in engine.env rather than
    setting MEE_DATA_DIR, so a bare ``data_dir()/logos`` resolves to the working
    directory — the same class of bug that once put the canary's state inside
    the repository.
    """

    from app.api.logos import resolve_logo_dir
    from app.config import Settings

    monkeypatch.delenv("MEE_DATA_DIR", raising=False)
    store = tmp_path / "depth.json"

    assert resolve_logo_dir(Settings(depth_store_path=store)) == tmp_path / "logos"
