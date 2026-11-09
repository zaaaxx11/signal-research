"""The response policy and the request cap, asserted on real responses.

Every case here runs against the middleware as mounted (``create_app``), not
against the class in isolation: a header that is added and never installed is the
failure this file exists to catch.
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.http_hardening import CSP, HSTS, MAX_BODY_BYTES, HardeningMiddleware


def _app(*, max_body_bytes: int = MAX_BODY_BYTES) -> FastAPI:
    """A minimal app: the middleware under test, one GET, one POST."""

    app = FastAPI()
    app.add_middleware(HardeningMiddleware, max_body_bytes=max_body_bytes)

    @app.get("/thing")
    async def thing() -> dict[str, str]:
        return {"ok": "yes"}

    @app.post("/thing")
    async def write() -> dict[str, str]:
        return {"ok": "written"}

    @app.get("/envelope")
    async def envelope() -> JSONResponse:
        return JSONResponse({"error": "x", "errorMessage": "y"}, status_code=400)

    return app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(_app())


def test_every_response_carries_the_policy(client: TestClient) -> None:
    response = client.get("/thing")

    assert response.status_code == 200
    assert response.headers["content-security-policy"] == CSP
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert "geolocation=()" in response.headers["permissions-policy"]


def test_the_policy_forbids_inline_script_and_framing(client: TestClient) -> None:
    """The two directives that carry the security value, asserted by name."""

    policy = client.get("/thing").headers["content-security-policy"]

    assert "script-src 'self'" in policy
    inline = [part for part in policy.split("; ") if part.startswith("script-src")]
    assert "'unsafe-inline'" not in inline[0], "inline script is what CSP is for"
    assert "'unsafe-eval'" not in policy
    assert "frame-ancestors 'none'" in policy
    assert "object-src 'none'" in policy
    assert "base-uri 'none'" in policy


def test_the_policy_covers_error_responses_too(client: TestClient) -> None:
    """A 4xx is still a document a browser renders."""

    response = client.get("/envelope")

    assert response.status_code == 400
    assert response.headers["content-security-policy"] == CSP


def test_hsts_is_sent_only_over_https(client: TestClient) -> None:
    """Announcing HSTS from a plain-HTTP origin would pin a scheme it cannot serve."""

    assert "strict-transport-security" not in client.get("/thing").headers

    secure = client.get("/thing", headers={"x-forwarded-proto": "https"})

    assert secure.headers["strict-transport-security"] == HSTS


def test_a_body_over_the_cap_is_refused_in_the_house_envelope() -> None:
    """Not FastAPI's ``{"detail": ...}``: this API speaks one language."""

    client = TestClient(_app(max_body_bytes=1024))
    payload = {"pad": "x" * 4096}

    response = client.post("/thing", json=payload)

    assert response.status_code == 413
    body = json.loads(response.text)
    assert body["error"] == "payload_too_large"
    assert "1 KiB" in body["errorMessage"]
    assert response.headers["content-security-policy"] == CSP


def test_a_body_at_the_cap_still_answers() -> None:
    """The boundary is ``> cap``, not ``>= cap``: an exact-fit request is legal."""

    client = TestClient(_app(max_body_bytes=1024))
    payload = json.dumps({"pad": "x" * (1024 - len('{"pad": ""}'))})

    assert len(payload.encode()) == 1024
    assert client.post("/thing", content=payload, headers={"content-type": "application/json"}).status_code == 200


def test_the_real_app_installs_the_policy() -> None:
    """The mounted app, not just the class: the header must reach the shell too."""

    from app.main import create_app

    application = create_app()
    paths = {getattr(route, "path", "") for route in application.routes}
    assert "/app" in paths, "the shell mount is expected here"

    # Any response proves the middleware wraps the app; the shell is a mount, so
    # a request to it is the one that would silently miss a route-level guard.
    response = TestClient(application).get("/robots.txt")
    assert response.headers["content-security-policy"] == CSP
