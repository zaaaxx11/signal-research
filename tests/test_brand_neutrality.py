"""The product's own vocabulary, not a vendor's: nothing a client reads says it.

The screener is *inspired by* a well-known panel; it is not a copy of it, and a
client must never have to know which panel that was. This test walks the four
surfaces a client actually reads and greps them for the vendor's name:

* ``GET /screens/filters`` — the panel document (tabs, labels, hints, notes,
  option labels, predicates, reasons);
* ``POST /screens/select/schema`` — the request schema, descriptions included;
* ``view_catalogue()`` — the tab strip and its column descriptions;
* every refusal sentence in :data:`app.screen.filter_fields.UNAVAILABLE` — the
  text a ``422`` repeats verbatim.

The check is a substring scan over the serialised documents, not a review of the
source: a name that reaches a client through a label, a note or a description is
exactly as visible as one in a key, and only the serialised form proves it is
gone. ``schema_version`` is pinned beside it because it is the one field a client
caches the whole document by.
"""

from __future__ import annotations

import json
from typing import Any

from app.screen import descriptive, filter_fields, selection, views
from tests import _screen as S

#: The word the product may not publish, in any spelling a reader would see.
VENDOR = "finviz"


def _vendor_hits(document: Any) -> list[str]:
    """Short windows around every occurrence of :data:`VENDOR`, keys included.

    The scan returns context rather than the string it scanned: a failing
    ``assert VENDOR not in <500 KB of JSON>`` makes pytest run a diff over the
    whole document, which takes minutes and says less than the few words that
    actually matched.
    """

    text = json.dumps(document, sort_keys=True, default=str)
    folded = text.casefold()
    hits: list[str] = []
    start = folded.find(VENDOR)
    while start != -1:
        hits.append(text[max(0, start - 60) : start + 80].replace("\n", " "))
        start = folded.find(VENDOR, start + len(VENDOR))
        if len(hits) >= 5:
            break
    return hits


def test_the_filter_document_names_no_vendor() -> None:
    assert _vendor_hits(descriptive.document()) == []


def test_the_request_schema_names_no_vendor() -> None:
    assert _vendor_hits(selection.SELECTION_REQUEST_SCHEMA) == []


def test_the_view_catalogue_names_no_vendor() -> None:
    assert _vendor_hits(views.view_catalogue()) == []


def test_every_refusal_sentence_is_neutral() -> None:
    noisy = {
        key: reason
        for key, reason in filter_fields.UNAVAILABLE.items()
        if VENDOR in reason.casefold()
    }
    assert noisy == {}


def test_the_document_publishes_a_neutral_schema_version() -> None:
    """A client keys its cache on this string: it is the most visible name."""

    version = descriptive.document()["schema_version"]
    assert version == descriptive.SCHEMA_VERSION
    assert VENDOR not in version.casefold(), version
    assert version == "screener-2"


async def test_the_served_document_names_no_vendor(
    tmp_path: Any, monkeypatch: Any
) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        response = await harness.client.get("/screens/filters")

    assert response.status_code == 200, response.text
    folded = response.text.casefold()
    assert folded.count(VENDOR) == 0, folded[folded.find(VENDOR) - 60 :][:140]
