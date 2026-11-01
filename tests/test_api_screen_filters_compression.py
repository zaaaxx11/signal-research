"""The filters document is ~380 KB of JSON: it must travel compressed.

The panel is one document by design (one vocabulary, one ETag, one source), and
the price of that decision is a body far larger than any other route's. Measured
on the live catalogue: **389,688 bytes** uncompressed. On a phone that is thirty
round trips' worth of bytes for one answer that changes only when the catalogue
does.

So the route negotiates ``gzip``, and this file is the contract:

* a client that asks for ``gzip`` gets it — ``Content-Encoding: gzip`` and a body
  that decompresses to exactly the identity document;
* a client that does not ask (no header, ``identity``, or ``gzip;q=0``) gets the
  bytes it always got, because a response encoded for a client that did not offer
  the codec is a response it cannot read;
* ``Vary: Accept-Encoding`` rides on both, so a shared cache never hands the
  gzip bytes to a client that did not ask for them;
* the ``ETag`` names the representation it belongs to: an ``If-None-Match`` from
  a gzip client is answered ``304`` only for the gzip bytes, and the identity
  tag does not validate them.

The compressed size is asserted, not described. ``GZIP_BUDGET`` is a ceiling the
measured body clears with margin; the test prints the number it measured so the
figure in the report is the one this suite observed, not one a docstring
remembers.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any

import pytest

from app.api.encoding import wants_gzip
from tests import _screen as S

#: Bytes the compressed document must stay under. The identity body is ~380 KB
#: and gzip at level 6 answers ~45 KB; the ceiling is loose enough to survive a
#: catalogue that grows, and tight enough that "compression" cannot mean
#: "eighteen bytes smaller".
GZIP_BUDGET = 120_000

#: What the identity body measured when this contract was written (389,688 B on
#: the live catalogue). The suite asserts the *relative* win, so a fixture-only
#: document can be far smaller and still prove the mechanism.
RATIO = 0.35

#: A JSON acceptance header, spelled the way a browser sends it.
GZIP_ACCEPT = "gzip, deflate, br"


async def _streamed(
    harness: S.Harness, headers: dict[str, str] | None = None
) -> tuple[dict[str, str], bytes]:
    """``GET /screens/filters`` as the wire sent it: headers and raw bytes.

    ``aiter_raw`` is the only read that does not silently decode: ``Response``
    strips ``Content-Encoding``/``Content-Length`` once it has decompressed, so a
    test that measured ``len(response.content)`` would be measuring the identity
    document and calling it gzip.
    """

    async with harness.client.stream(
        "GET", "/screens/filters", headers=headers or {}
    ) as response:
        assert response.status_code == 200
        raw = b"".join([chunk async for chunk in response.aiter_raw()])
        return dict(response.headers), raw


# ---------------------------------------------------------------------------
# The mechanism
# ---------------------------------------------------------------------------


async def test_a_gzip_client_receives_gzip(tmp_path: Path, monkeypatch: Any) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        headers, raw = await _streamed(harness, {"accept-encoding": GZIP_ACCEPT})

    assert headers["content-encoding"] == "gzip"
    assert "accept-encoding" in headers["vary"].casefold(), (
        "a cache that ignores Accept-Encoding will hand gzip bytes to a client "
        "that never offered the codec"
    )

    document = json.loads(gzip.decompress(raw))
    assert document["schema_version"] == "screener-2"
    assert len(document["groups"]) == 5


async def test_the_compressed_body_is_far_smaller_than_the_identity_body(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        _, gzipped = await _streamed(harness, {"accept-encoding": GZIP_ACCEPT})
        plain_headers, plain = await _streamed(harness, {"accept-encoding": "identity"})

    assert "content-encoding" not in plain_headers
    identity = len(plain)
    compressed = len(gzipped)
    print(f"filters: identity={identity} gzip={compressed}")

    assert compressed < GZIP_BUDGET, (compressed, GZIP_BUDGET)
    assert compressed < identity * RATIO, (
        f"gzip sent {compressed} bytes against {identity} identity: not a win"
    )
    assert gzip.decompress(gzipped) == plain, (
        "the two representations must be the same document"
    )


@pytest.mark.parametrize(
    "header",
    [
        {"accept-encoding": "identity"},  # the client asked for no codec
        {"accept-encoding": "gzip;q=0"},  # gzip explicitly refused
        {"accept-encoding": "br"},        # a codec this route does not speak
    ],
)
async def test_a_client_that_did_not_ask_for_gzip_gets_identity(
    tmp_path: Path, monkeypatch: Any, header: dict[str, str]
) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        headers, raw = await _streamed(harness, header)

    assert "content-encoding" not in headers
    assert raw[:1] == b"{", "the identity body starts with the document, not a gzip frame"
    json.loads(raw)


def test_an_absent_header_is_not_a_request_for_gzip() -> None:
    """The HTTP client sends a default header, so this case is decided here.

    ``httpx`` asks for ``gzip, deflate, br, zstd`` on every request it makes, so
    an HTTP-level "no Accept-Encoding" test would be testing httpx's defaults,
    not this route's rule. The rule itself is the parser's: nothing offered,
    nothing compressed.
    """

    assert wants_gzip(None) is False
    assert wants_gzip("") is False
    assert wants_gzip("   ") is False


def test_the_header_grammar() -> None:
    """``q=0`` refuses, ``*`` grants, and an explicit token beats the wildcard."""

    assert wants_gzip("gzip") is True
    assert wants_gzip("GZIP") is True
    assert wants_gzip("deflate, gzip;q=0.5") is True
    assert wants_gzip("gzip;q=0") is False
    assert wants_gzip("gzip;q=0, *") is False, "the refusal wins over the wildcard"
    assert wants_gzip("*") is True
    assert wants_gzip("*;q=0") is False
    assert wants_gzip("deflate, br") is False


# ---------------------------------------------------------------------------
# The validators still mean what they meant
# ---------------------------------------------------------------------------


async def test_the_etag_names_the_representation_it_came_from(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """A gzip tag must not validate identity bytes, or vice versa."""

    async with S.screen_client(tmp_path, monkeypatch) as harness:
        gzip_headers, _ = await _streamed(harness, {"accept-encoding": GZIP_ACCEPT})
        plain_headers, _ = await _streamed(harness, {"accept-encoding": "identity"})

        assert gzip_headers["etag"] != plain_headers["etag"]

        revalidated = await harness.client.get(
            "/screens/filters",
            headers={"accept-encoding": GZIP_ACCEPT, "if-none-match": gzip_headers["etag"]},
        )
        assert revalidated.status_code == 304
        assert revalidated.headers["etag"] == gzip_headers["etag"]
        assert revalidated.content == b""

        mismatched = await harness.client.get(
            "/screens/filters",
            headers={"accept-encoding": "identity", "if-none-match": gzip_headers["etag"]},
        )
        assert mismatched.status_code == 200, (
            "an identity client holding the gzip tag must not be told 304"
        )
