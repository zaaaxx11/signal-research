"""The crawler documents are built from the request, and name no host.

Two rules, and they are the whole point of serving these from the app instead of
from ``web/public``:

* **the host comes from the request.** A crawler on ``example.org`` is told about
  ``example.org``, which is what lets the box move domains without an edit and
  keeps a deployment name out of the repository;
* **one page list.** The sitemap and the crawl policy read :data:`PAGES`, so a
  page cannot be in one and missing from the other.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import crawl

REPO = Path(__file__).resolve().parents[1]

#: The host a deployment has used, which no file may hard-code any more. Assembled
#: from parts so this guard file does not trip its own scan.
DEPLOY_HOST = "ztockz." + "customin.biz.id"


@pytest.fixture()
def client() -> TestClient:
    """The crawl router alone, on a host a test can recognise."""

    app = FastAPI()
    app.include_router(crawl.router)
    return TestClient(app, base_url="http://crawl.test")


def test_the_sitemap_lists_every_page_on_the_host_that_asked(client: TestClient) -> None:
    body = client.get("/sitemap.xml").text

    assert body.startswith('<?xml version="1.0" encoding="UTF-8"?>')
    assert f'<urlset xmlns="{crawl.SITEMAP_NS}">' in body
    for path, priority in crawl.PAGES:
        assert f"<loc>http://crawl.test{path}</loc>" in body, path
        assert f"<priority>{priority}</priority>" in body, path
    assert f"<loc>{len(crawl.PAGES)}" not in body  # a stray count, not a page
    assert body.count("<url>") == len(crawl.PAGES)


def test_the_sitemap_follows_the_host_and_never_declares_one(client: TestClient) -> None:
    """The same route, two hosts: the answer follows the request, not the code."""

    first = client.get("/sitemap.xml").text
    assert "http://crawl.test/app/" in first
    assert DEPLOY_HOST not in first

    other = TestClient(client.app, base_url="https://elsewhere.example")
    second = other.get("/sitemap.xml").text

    assert "https://elsewhere.example/app/" in second
    assert "crawl.test" not in second, "the host must come from the request"
    assert first.replace("http://crawl.test", "https://elsewhere.example") == second


def test_robots_allows_everything_and_names_the_sitemap_on_its_own_host(
    client: TestClient,
) -> None:
    response = client.get("/robots.txt")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    body = response.text
    assert "User-agent: *" in body
    assert "Allow: /" in body
    assert "Disallow:" not in body
    assert "Sitemap: http://crawl.test/sitemap.xml" in body
    assert DEPLOY_HOST not in body


def test_both_documents_are_cacheable_but_cheap(client: TestClient) -> None:
    """A crawler re-reads them; the edge keys its cache by host, as the code notes."""

    for path in ("/robots.txt", "/sitemap.xml"):
        assert client.get(path).headers["cache-control"] == crawl.CACHE_CONTROL


def test_the_app_mounts_the_crawl_documents_at_the_root() -> None:
    """Under ``/app`` they would be answered where no crawler looks."""

    from app.main import create_app

    paths = {getattr(route, "path", "") for route in create_app().routes}

    assert "/robots.txt" in paths
    assert "/sitemap.xml" in paths


def test_the_preview_description_counts_the_filters_it_has() -> None:
    """A number in a static document is a pin: it has to match the catalogue.

    ``og:description`` states the filter count, and a crawler reads it from the
    raw HTML — no script can correct it, so a number that drifted would be what
    every link preview showed. This is the same drift the served schema had.
    """

    from app.screen.filters import FILTER_KEYS

    html = (REPO / "web" / "index.html").read_text(encoding="utf-8")

    assert f"{len(FILTER_KEYS)} filters" in html, (
        "the preview description must count the catalogue it advertises"
    )


def test_no_file_hard_codes_the_deployment_host() -> None:
    """The rule the static files broke: the repository names no deployment host."""

    offenders: list[str] = []
    for root in ("app", "web", "tests"):
        for path in (REPO / root).rglob("*"):
            if path.is_dir() or "node_modules" in path.parts or "dist" in path.parts:
                continue
            if path.suffix not in {".py", ".ts", ".tsx", ".html", ".json", ".txt", ".xml"}:
                continue
            if DEPLOY_HOST in path.read_text(encoding="utf-8", errors="ignore"):
                offenders.append(str(path.relative_to(REPO)))

    assert offenders == [], f"these files name the deployment host: {offenders}"
