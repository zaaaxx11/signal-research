"""Licence descriptions for the five quote rungs — every one of them internal.

A ``200`` from a quote adapter is not a licence. Yahoo, Stooq, Nasdaq's site
API, Finnhub and Polygon all answer a private research workload, and none of
them grants redistribution of the print to a third party: their terms either
forbid it outright or require a paid agreement this deployment does not have.
So the licence verdict lives here, next to no parser at all, because it must be
readable without an HTTP client: an adapter that works perfectly still may not
answer a public request.

The registry is a *description*, not a gate. :mod:`app.engine.license` decides
admissibility from these specs; :func:`~app.engine.license.public_sources`
filters them without reordering, and this module's
:func:`quote_source_available` is the single question the screen route asks
before it polls anything.

Features are always ``{"quote"}``: a rung may be a fine identity or EOD source,
but this module only speaks for the observed quote. Widening a feature set here
would silently widen what a public screen may read.
"""

from __future__ import annotations

from app.engine.license import public_sources
from app.engine.model import LicenseClass, SourceSpec

#: The one feature every spec below declares. Nothing else may be inferred from
#: the presence of a rung: "this adapter can also serve EOD bars" is a different
#: licence question with a different answer.
QUOTE_FEATURE = "quote"

#: The five observed-quote rungs, in ladder order (L1 → L5 of 02 §3.4).
#:
#: Every entry is ``INTERNAL`` with ``redistribution=False`` on purpose. The
#: rate limits are the ceilings this deployment already applies to each adapter,
#: not a vendor promise: Yahoo publishes none, so one request per second is our
#: own conservative choice.
QUOTE_SOURCE_SPECS: tuple[SourceSpec, ...] = (
    SourceSpec(
        name="yahoo",
        license_class=LicenseClass.INTERNAL,
        redistribution=False,
        attribution="Yahoo Finance",
        terms_url="https://legal.yahoo.com/us/en/yahoo/terms/otos/index.html",
        features=frozenset({QUOTE_FEATURE}),
        rate_limit=(1, 1.0),
    ),
    SourceSpec(
        name="stooq",
        license_class=LicenseClass.INTERNAL,
        redistribution=False,
        attribution="Stooq",
        terms_url="https://stooq.com/terms/",
        features=frozenset({QUOTE_FEATURE}),
        rate_limit=(5, 1.0),
    ),
    SourceSpec(
        name="nasdaq",
        license_class=LicenseClass.INTERNAL,
        redistribution=False,
        attribution="Nasdaq",
        terms_url="https://www.nasdaq.com/legal/terms-and-conditions",
        features=frozenset({QUOTE_FEATURE}),
        rate_limit=(10, 1.0),
    ),
    SourceSpec(
        name="finnhub",
        license_class=LicenseClass.INTERNAL,
        redistribution=False,
        attribution="Finnhub",
        terms_url="https://finnhub.io/terms-of-service",
        features=frozenset({QUOTE_FEATURE}),
        rate_limit=(60, 60.0),
    ),
    SourceSpec(
        name="polygon",
        license_class=LicenseClass.INTERNAL,
        redistribution=False,
        attribution="Polygon.io",
        terms_url="https://polygon.io/legal/terms",
        features=frozenset({QUOTE_FEATURE}),
        rate_limit=(100, 60.0),
    ),
)


def quote_sources(*, public_mode: bool) -> tuple[SourceSpec, ...]:
    """The quote rungs that may answer under ``public_mode``, order preserved.

    Private research admits all five; a public deployment admits none of them,
    and that empty tuple is the honest answer rather than a filtered ladder with
    a silent fallback hidden inside it.
    """

    return public_sources(QUOTE_SOURCE_SPECS, public_mode=public_mode)


def quote_source_available(*, public_mode: bool) -> bool:
    """May *any* quote rung answer a request this process serves?

    This is the screen route's one licence question. ``False`` means no fetch
    may be attempted and no quote cell may be attached — not even when the
    caller asked for a refresh, because a caller cannot license a source.
    """

    return bool(quote_sources(public_mode=public_mode))


__all__ = [
    "QUOTE_FEATURE",
    "QUOTE_SOURCE_SPECS",
    "quote_source_available",
    "quote_sources",
]
