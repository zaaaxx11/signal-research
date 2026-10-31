"""The shell's asset URLs must resolve through the mount that serves it.

The failure this guards against is silent by construction: ``index.html`` is a
200, the document looks healthy, and every ``<script>`` and ``<link>`` it hands
out is a 404. The page then renders nothing at all — no error boundary, no
message, just an empty body — while ``/health`` stays green.

It happened once: ``vite.config.ts`` set ``base: "/app/"`` but the committed
``dist`` had been built before that change and still asked for ``/assets/…`` at
the origin root, where nothing is mounted.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

DIST = Path(__file__).resolve().parent.parent / "web" / "dist"

pytestmark = pytest.mark.skipif(
    not (DIST / "index.html").is_file(),
    reason="the web build is not present in this checkout",
)


@pytest.fixture()
def client() -> TestClient:
    """One application per test: the mount table is built at import time."""

    return TestClient(create_app())


def _asset_urls() -> list[str]:
    """Every asset the shell asks for, exactly as the browser would read it."""

    html = (DIST / "index.html").read_text(encoding="utf-8")
    return sorted(
        {
            match
            for match in re.findall(r'(?:src|href)="([^"]*assets/[^"]+)"', html)
        }
    )


def test_the_shell_references_at_least_one_asset() -> None:
    """A build that references nothing is broken, not minimal."""

    assert _asset_urls(), "index.html hands the browser no script or stylesheet"


def test_every_asset_url_the_shell_hands_out_resolves(
    client: TestClient,
) -> None:
    """Each URL is fetched as written, against the app that mounts it."""

    for url in _asset_urls():
        response = client.get(url)
        assert response.status_code == 200, (
            f"{url} is referenced by index.html but answers "
            f"{response.status_code} — the build's base and the mount disagree"
        )
        assert response.content, f"{url} answered 200 with an empty body"


def test_the_entry_point_and_its_assets_share_one_prefix(
    client: TestClient,
) -> None:
    """The prefix in the document must be a prefix the server actually has.

    ``/app`` is where ``web/dist`` is mounted; a document may live at ``/`` too,
    but its assets must still point at the mount that exists.
    """

    assert client.get("/app").status_code == 200
    for url in _asset_urls():
        assert url.startswith("/app/assets/"), (
            f"{url} does not live under the mounted prefix; a cold browser will "
            "404 it and the shell will never boot"
        )
        assert client.get(url).status_code == 200
