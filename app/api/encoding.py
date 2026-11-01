"""The one compression decision the JSON routes make, in one place.

There is no gzip middleware in this app, and this module is deliberately not one:
a middleware wraps every route at once, which changes the header surface — and
therefore the contract — of answers that never needed the bytes. Two routes do:
``GET /screens/filters`` (one document, 389,688 B on the live catalogue) and the
treemap. They build their own ``Response`` from bytes they already hold, so they
can negotiate the codec at the point where the bytes exist, and every other route
keeps the headers it always shipped.

Three rules, each one a way a compression layer usually goes wrong:

* **Only when the client asked.** A body encoded for a client that never offered
  the codec is a body it cannot read. ``gzip;q=0`` is a refusal, not an absence,
  and ``*`` grants it only when ``gzip`` was not named at all.
* **Only when it pays.** Below :data:`GZIP_MINIMUM` the frame costs more than the
  saving, and a 60-byte error envelope is not worth a 20-byte header.
* **Deterministic bytes.** ``mtime=0`` removes the timestamp gzip normally
  embeds, so the same document always compresses to the same bytes. The route
  derives its ``ETag`` from that payload; a compressor that leaks the clock would
  issue a new tag — and a cache miss — every second.

``Vary: Accept-Encoding`` is the caller's job to publish (it is a cache fact, not
an encoding one), and it must ride on the identity answer too: a cache that only
sees the header on the compressed variant will still hand gzip bytes to a client
that did not ask.
"""

from __future__ import annotations

import gzip
from typing import Any

__all__ = ["GZIP_MINIMUM", "compress", "wants_gzip"]

#: Bodies smaller than this are sent as they are.
GZIP_MINIMUM = 1024

#: The level. 6 is the usual size/speed knee: on the filters document it lands
#: within a few percent of level 9 for roughly a third of the CPU.
COMPRESS_LEVEL = 6


def _qvalue(parameter: str) -> float:
    """The ``q=`` weight of one ``Accept-Encoding`` item, or 1.0."""

    for part in parameter.split(";"):
        name, _, value = part.strip().partition("=")
        if name.strip().casefold() != "q":
            continue
        try:
            weight = float(value.strip())
        except ValueError:
            return 0.0
        return min(max(weight, 0.0), 1.0)
    return 1.0


def wants_gzip(header: Any) -> bool:
    """Does this ``Accept-Encoding`` header offer gzip with a non-zero weight?

    An explicit ``gzip`` wins over ``*``; ``gzip;q=0`` is a refusal even when the
    wildcard is present, because naming a codec to reject it is the one thing a
    client can say that a wildcard must not overrule.
    """

    if not isinstance(header, str) or not header.strip():
        return False
    wildcard: float | None = None
    gzip_weight: float | None = None
    for item in header.split(","):
        name, _, parameters = item.strip().partition(";")
        token = name.strip().casefold()
        weight = _qvalue(parameters)
        if token == "gzip" or token == "x-gzip":
            gzip_weight = weight if gzip_weight is None else max(gzip_weight, weight)
        elif token == "*":
            wildcard = weight
    if gzip_weight is not None:
        return gzip_weight > 0
    return bool(wildcard and wildcard > 0)


def compress(body: bytes) -> bytes:
    """``body`` as one deterministic gzip frame."""

    return gzip.compress(body, compresslevel=COMPRESS_LEVEL, mtime=0)
