"""Typed lifecycle errors. HTTP code lives with the error, not the route."""

from __future__ import annotations


class JobError(Exception):
    """Base class for job lifecycle errors."""

    status_code: int = 400


class JobNotFound(JobError):
    status_code = 404


class JobTerminal(JobError):
    """Cancel/retry on a job that already reached a terminal state."""

    status_code = 409


class RequestNotServable(JobError):
    """The request is valid but this deployment cannot honour it (501).

    Raised *before* a job row exists: a job that cannot produce what was asked
    for is a failed job with a misleading ``Success`` waiting to happen.
    """

    status_code = 501


class QuoteError(Exception):
    """Base class for quote errors."""

    status_code: int = 400


class QuoteAlreadyUsed(QuoteError):
    """Second use, or use by a different owner: both are 409 (no oracle)."""

    status_code = 409


class QuoteExpired(QuoteError):
    status_code = 410
