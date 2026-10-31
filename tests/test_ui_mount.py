"""The front door: ``/`` is the new cockpit, ``/legacy`` is the old one.

Phase 7 flipped the entry point (plan §9, D9). Before it, ``/`` was a redirect
to the old cockpit at ``/ui/`` and the React shell lived only under ``/app``.
After it, three things have to be true at once, and each of them is a way the
cutover can fail:

* **The new document is at the origin root**, served directly rather than
  bounced through ``/app/``, without the *old* document disappearing: the old
  cockpit stays reachable at ``/legacy`` for one release while ``/ui/`` — the
  base its relative asset links were written against — keeps answering.
* **Nothing else moves.** ``/health``, ``/health/deep``, ``/logos/{symbol}``,
  ``/news/{symbol}``, ``/market/*`` and ``/screens*`` keep their paths,
  methods and documents; the route table is asserted, not assumed, because
  ``/`` was the one route whose handler changed and the failure mode is a
  prefix that quietly starts matching something else.
* **The fallback still refuses what it refused.** A traversal-shaped request is
  a JSON 404 — never a 200 shell that hides the attempt — and ``/legacy`` and
  ``/ui`` are answered by their own handlers rather than by the SPA fallback.

The build artefact is not committed (``npm run build`` produces it), so every
test that needs a shell writes a synthetic one into ``tmp_path`` and points
``MEE_WEB_DIST`` at it. That is also what lets the suite assert the *cutover*
on a checkout that has never been built, and the *fallback* (no artefact) on
the same machine.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

ROOT = Path(__file__).resolve().parent.parent
UI_DIR = ROOT / "ui"

#: The React mount point, and the one string that separates the two documents:
#: the new shell is a bare ``<div id="root">`` that the bundle fills, the old
#: cockpit is 18 KB of server-rendered markup with a "Market cockpit" brand.
SHELL_MARKER = '<div id="root"></div>'
LEGACY_MARKER = "Market cockpit"


# ---------------------------------------------------------------------------
# harness
# ---------------------------------------------------------------------------


@pytest.fixture
def shell(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A built shell in ``tmp_path``, selected through ``MEE_WEB_DIST``.

    The assets are what make this a *build* rather than a directory: the
    document's script URL is absolute (``/app/assets/…``), exactly as
    ``web/vite.config.ts`` emits it with ``base: "/app/"``, because that detail
    is why serving the same bytes at ``/`` is safe.
    """

    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "assets" / "index-test.js").write_text("export {};\n", encoding="utf-8")
    (dist / "index.html").write_text(
        "<!doctype html><html><head><title>Ztockz</title>"
        '<script type="module" crossorigin src="/app/assets/index-test.js"></script>'
        f"</head><body>{SHELL_MARKER}</body></html>",
        encoding="utf-8",
    )
    monkeypatch.setenv("MEE_WEB_DIST", str(dist))
    return dist


@pytest.fixture
def client(shell: Path) -> TestClient:
    return TestClient(create_app())


def _asgi_get(app: Any, path: str) -> tuple[int, dict[str, str], bytes]:
    """One GET through the ASGI app with ``path`` exactly as given.

    ``httpx`` normalises ``..`` before a request leaves the client, so a
    ``TestClient`` call cannot prove what the *server* does with a traversal
    path. This builds the scope by hand — the bytes uvicorn would hand the app
    after one percent-decode — which is the only honest way to test it.
    """

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("latin-1"),
        "root_path": "",
        "query_string": b"",
        "headers": [(b"host", b"testserver")],
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 12345),
    }
    status = 0
    headers: dict[str, str] = {}
    body = bytearray()
    calls = 0

    async def receive() -> dict[str, Any]:
        # The request, then a client that keeps the connection open. FileResponse
        # runs a task group watching for a disconnect and truncates the body if
        # it sees one — answering immediately would turn a 200 into zero bytes,
        # and never answering leaves the body pending.
        nonlocal calls
        calls += 1
        if calls > 1:
            await asyncio.Event().wait()
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        nonlocal status
        if message["type"] == "http.response.start":
            status = int(message["status"])
            headers.update(
                {
                    key.decode("latin-1").lower(): value.decode("latin-1")
                    for key, value in message.get("headers", [])
                }
            )
        elif message["type"] == "http.response.body":
            body.extend(message.get("body", b""))

    asyncio.run(app(scope, receive, send))
    return status, headers, bytes(body)


def _routes(app: Any) -> set[tuple[str, tuple[str, ...]]]:
    """The flat route table as ``(path, methods)`` pairs."""

    return {
        (str(getattr(route, "path", "")), tuple(sorted(getattr(route, "methods", ()) or ())))
        for route in app.routes
        if getattr(route, "path", None)
    }


# ---------------------------------------------------------------------------
# the front door
# ---------------------------------------------------------------------------


def test_root_serves_the_new_shell_directly(client: TestClient) -> None:
    """``/`` is the new app — 200 with the shell, not a redirect to ``/app/``.

    Served directly because the build's base is absolute: the document asks for
    ``/app/assets/…`` whichever URL served it, so a hop would add a round trip
    before the first byte of the page that starts the paint clock and buy
    nothing. ``location`` is asserted absent, which is the difference between
    "the cutover works" and "the cutover works after one more round trip".
    """

    response = client.get("/", follow_redirects=False)

    assert response.status_code == 200
    assert "location" not in response.headers
    assert "text/html" in response.headers["content-type"]
    assert SHELL_MARKER in response.text
    # The asset URL the document hands out is under the build's base, not
    # relative to whatever path served the document.
    assert "/app/assets/index-test.js" in response.text


def test_root_and_app_prefix_are_one_document(client: TestClient) -> None:
    """Two URLs, one artefact, one validator — they must not drift."""

    root = client.get("/")
    prefix = client.get("/app")

    assert root.content == prefix.content
    assert root.headers["etag"] == prefix.headers["etag"]


def test_head_on_the_front_door_is_allowed_without_a_body(client: TestClient) -> None:
    """A cache or an uptime check sends HEAD; a 405 would be a false alarm."""

    response = client.head("/")

    assert response.status_code == 200
    assert response.content == b""
    assert "text/html" in response.headers["content-type"]


def test_the_shell_revalidates_with_a_304(client: TestClient) -> None:
    """The warm-visit half of the first-paint budget: ask, get ``304``, paint.

    Starlette 1.9's ``FileResponse`` sets an ``ETag`` and then ignores
    ``If-None-Match``, so this is the contract the route has to hold itself: a
    matching validator answers 304 with no body and the same tag, and the
    document is revalidated rather than cached blind (``no-cache``).
    """

    first = client.get("/")
    etag = first.headers["etag"]
    assert etag
    assert first.headers["cache-control"] == "no-cache"

    again = client.get("/", headers={"if-none-match": etag})

    assert again.status_code == 304
    assert again.content == b""
    assert again.headers["etag"] == etag

    # A weak or list form is the same question, answered the same way.
    weak = client.get("/", headers={"if-none-match": f'W/{etag}, "other"'})
    assert weak.status_code == 304

    # A different tag is a miss: 200 and the bytes.
    assert client.get("/", headers={"if-none-match": '"stale"'}).status_code == 200


# ---------------------------------------------------------------------------
# the old cockpit, for one release
# ---------------------------------------------------------------------------


def test_legacy_alias_serves_the_old_cockpit(client: TestClient) -> None:
    """``/legacy`` is the old UI, one hop away, at its own base.

    A 302 to ``/ui/`` and not a second mount of the same directory: the legacy
    document links ``./styles.css`` and ``./app.js`` relatively, so the bytes
    have to be served at the URL they were written against. The hop carries the
    base; the alias is ``no-store`` so next release's removal is not cached.
    """

    hop = client.get("/legacy", follow_redirects=False)

    assert hop.status_code == 302
    assert hop.headers["location"] == "/ui/"
    assert hop.headers["cache-control"] == "no-store"

    # The bookmarked spelling is the same one hop, not a 307 to ``/legacy``
    # followed by this redirect.
    slashed = client.get("/legacy/", follow_redirects=False)
    assert slashed.status_code == 302
    assert slashed.headers["location"] == "/ui/"

    page = client.get("/legacy", follow_redirects=True)
    assert page.status_code == 200
    assert LEGACY_MARKER in page.text
    assert SHELL_MARKER not in page.text


def test_ui_keeps_working_for_one_release(client: TestClient) -> None:
    """``/ui/`` is where the old cockpit's assets resolve; it stays mounted."""

    page = client.get("/ui/")
    assert page.status_code == 200
    assert LEGACY_MARKER in page.text

    css = client.get("/ui/styles.css")
    assert css.status_code == 200
    assert "text/css" in css.headers["content-type"]

    js = client.get("/ui/app.js")
    assert js.status_code == 200
    assert "javascript" in js.headers["content-type"]


# ---------------------------------------------------------------------------
# the fallback does not eat the aliases
# ---------------------------------------------------------------------------


def test_spa_fallback_does_not_swallow_the_legacy_names(client: TestClient) -> None:
    """``/legacy`` and ``/ui`` are answered by their own handlers, not the SPA.

    The fallback renders the *new* shell for any client route it does not
    recognise. If it ever claimed these two, the old cockpit's bookmark would
    silently load the new app — the cutover would look successful while the
    escape hatch was gone. Under ``/app`` the same words are refused outright:
    a client route may not be spelled like a root the product already owns.
    """

    assert client.get("/legacy", follow_redirects=False).status_code == 302
    assert LEGACY_MARKER in client.get("/ui/").text

    for path in ("/app/legacy", "/app/ui"):
        refused = client.get(path, follow_redirects=False)
        assert refused.status_code == 404, path
        assert "application/json" in refused.headers["content-type"], path
        assert SHELL_MARKER not in refused.text, path


@pytest.mark.parametrize(
    "path",
    (
        "/app/../etc/passwd",
        "/app/..%2f..%2fetc%2fpasswd",
        "/app/%2e%2e/etc/passwd",
        "/app//etc/passwd",
        "/app/assets/..%2f..%2fetc%2fpasswd",
        "/app/.%2e/%2e%2e/etc/passwd",
        "/../etc/passwd",
        "/%2e%2e/etc/passwd",
        "/app/etc/passwd\x00.js",
    ),
)
def test_traversal_is_still_a_json_404(path: str) -> None:
    """A blocked escape must not become a 200 shell.

    The fallback turns "no such file" into the shell, which is right for a
    client route and wrong for an attack: an operator should see the refusal in
    the log, and a scanner should see a 404, not a rendered page. The body is
    JSON because every 404 this app produces is.
    """

    status, headers, body = _asgi_get(create_app(), path)

    assert status == 404, f"{path!r} -> {status} {body[:120]!r}"
    assert "application/json" in headers.get("content-type", ""), path
    assert json.loads(body)["detail"] == "Not Found"
    assert SHELL_MARKER.encode() not in body, path


def test_a_missing_asset_is_still_a_404_not_the_shell(client: TestClient) -> None:
    """The classic subpath bug: a chunk must never come back as HTML."""

    missing = client.get("/app/assets/nope-abc123.js")

    assert missing.status_code == 404
    assert "application/json" in missing.headers["content-type"]
    assert SHELL_MARKER not in missing.text


# ---------------------------------------------------------------------------
# no build on disk: the cutover degrades, it does not 404 the front door
# ---------------------------------------------------------------------------


def test_without_a_build_the_root_falls_back_to_the_old_cockpit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A source-only checkout still has a product at ``/``.

    ``web/dist`` is generated and uncommitted; a process that was never built
    must not answer its front door with a 404. The old redirect is the honest
    fallback — and ``/legacy`` is still the old cockpit either way.
    """

    monkeypatch.setenv("MEE_WEB_DIST", str(tmp_path / "never-built"))

    app = create_app()
    client = TestClient(app)

    root = client.get("/", follow_redirects=False)
    assert root.status_code in {307, 308}
    assert root.headers["location"] == "/ui/"

    assert client.get("/app").status_code == 404
    assert client.get("/legacy", follow_redirects=False).status_code == 302
    assert LEGACY_MARKER in client.get("/ui/").text


# ---------------------------------------------------------------------------
# the compatibility surface did not move
# ---------------------------------------------------------------------------


def test_the_route_table_is_unchanged(shell: Path) -> None:
    """Every path the cutover promised not to touch, by path *and* method.

    Two sources, because they answer different halves of the question. The API
    routers are included lazily (``include_router`` leaves an opaque router
    whose operations only surface in the OpenAPI schema), so that is where the
    API's paths are read. The front door is registered with
    ``include_in_schema=False`` and therefore appears only in the route table.

    Structural on purpose: the expensive routes (``/screens`` scans, ``/news``
    fetches, ``/health/deep`` probes) reach providers, and the suite runs with
    egress blocked. What has to be provable here is that registering ``/`` did
    not displace, re-path or re-method anything — the live behaviour of those
    routes is measured by ``scripts/ui_perf_probe.py`` against a running app.
    """

    app = create_app()
    api = {
        (path, tuple(sorted(method.upper() for method in operations)))
        for path, operations in app.openapi()["paths"].items()
    }

    expected = {
        "/health": ("GET",),
        "/health/deep": ("GET",),
        "/logos/{symbol}": ("GET",),
        "/news/{symbol}": ("GET",),
        "/market/quote": ("GET",),
        "/market/breadth": ("GET",),
        "/market/depth": ("GET",),
        "/market/hub": ("GET",),
        "/screens": ("GET",),
        "/screens/schema": ("GET",),
        "/screens/select/schema": ("GET",),
        "/screens/select": ("POST",),
        "/screens/{screen_id}/export": ("POST",),
    }
    for path, methods in expected.items():
        assert (path, tuple(sorted(methods))) in api, path

    front_door = _routes(app)
    for path, methods in {
        "/": ("GET", "HEAD"),
        "/app": ("GET", "HEAD"),
        "/legacy": ("GET", "HEAD"),
    }.items():
        assert (path, tuple(sorted(methods))) in front_door, path


def test_compatibility_paths_answer_exactly_as_before(client: TestClient) -> None:
    """The cheap half of the matrix, answered for real (no egress needed).

    ``/health`` is network-free by contract, ``/logos`` is a filesystem
    lookup, the schema routes serve a committed file and the two ``/market``
    reads answer from process state. Each is asserted for status, content type
    and the shape of its document, so a 200 shell masquerading as JSON — the
    exact failure mode of a root route that over-matches — cannot pass.
    """

    health = client.get("/health")
    assert health.status_code in {200, 503}
    document = health.json()
    assert "checks" in document and "ui" in document
    assert document["ui"]["mounted"] is True

    logo = client.get("/logos/AAPL")
    assert logo.status_code == 200
    assert logo.headers["content-type"].startswith("image/")

    # ``/market`` answers from the collector, which this process did not opt
    # into: the 503 is the route's own envelope, and reaching it at all is the
    # assertion — the SPA fallback would have answered 200 text/html.
    depth = client.get("/market/depth/stats")
    assert depth.status_code == 503
    assert depth.json()["error"] == "depth_unavailable"

    # A validation refusal is still proof of routing: the market router parsed
    # the query and rejected it in the house envelope.
    quote = client.get("/market/quote?symbol=AAPL")
    assert quote.status_code == 422
    assert quote.json()["error"] == "invalid_symbols"

    schema = client.get("/screens/select/schema")
    assert schema.status_code == 200
    assert "application/json" in schema.headers["content-type"]
    assert schema.json()["$id"].endswith("stock-selection-request-v1.json")

    screens = client.get("/screens/schema")
    assert screens.status_code == 200
    assert "application/json" in screens.headers["content-type"]


def test_news_keeps_its_own_prefix(shell: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``/news/{symbol}`` is the API's, not a client route, and not the shell.

    The service is replaced on ``app.state`` — the seam ``create_app`` already
    documents — so the assertion is about routing and the document it returns,
    with no provider on the path.
    """

    class _Stub:
        async def news(self, symbol: str, *, limit: int | None = None) -> Any:
            return {
                "symbol": symbol,
                "as_of": "2026-10-31T00:00:00+00:00",
                "count": 0,
                "items": [],
            }

    app = create_app()
    app.state.news = _Stub()
    client = TestClient(app)

    response = client.get("/news/AAPL")

    assert response.status_code == 200
    assert "application/json" in response.headers["content-type"]
    assert response.json()["symbol"] == "AAPL"
    assert SHELL_MARKER not in response.text
