"""The market cockpit is served by the same application as the API.

Presentation is part of the product surface, but it must not become a second
data path: the page is static, and every number it renders is fetched from the
existing contracts. These assertions only cover delivery, not values.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app


def test_cockpit_is_served_at_ui_and_root_redirects_there() -> None:
    client = TestClient(create_app())

    page = client.get("/ui/")
    assert page.status_code == 200
    assert "Ztockz" in page.text
    assert "/app.js" in page.text

    root = client.get("/", follow_redirects=False)
    assert root.status_code in {307, 308}
    assert root.headers["location"] == "/ui/"


def test_cockpit_static_assets_are_not_api_envelopes() -> None:
    client = TestClient(create_app())

    css = client.get("/ui/styles.css")
    assert css.status_code == 200
    assert "text/css" in css.headers["content-type"]
    assert ".market-table" in css.text
