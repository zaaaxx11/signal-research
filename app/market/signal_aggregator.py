"""Signal aggregator helpers — date parsing, news keyword infrastructure.

These are self-contained helpers the signals route reads so the route
itself stays about the rules.  Every function here is pure: the clock is
the caller's, the keyword list is a frozen set, and the earnings-date
parser is the same one :func:`app.screen.buckets.day` uses (imported,
not copied).
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from app.screen.buckets import NEW_YORK, day as _parse_day

# ---------------------------------------------------------------------------
# Earnings date
# ---------------------------------------------------------------------------


def earnings_date_for(value: Any, *, today: date | None = None) -> date | None:
    """One ``earnings_date`` cell as a date, or ``None`` when it is not one.

    Delegates to :func:`app.screen.buckets.day` — the one owner of the
    three-spelling parse (epoch int, ISO string, ``Sep 28`` / ``Sep 28 BMO``).
    ``today`` defaults to New York's date right now.
    """
    if today is None:
        today = datetime.now(tz=NEW_YORK).date()
    return _parse_day(value, today=today)


def is_earnings_today(value: Any, *, today: date | None = None) -> bool:
    """True when ``value`` is an earnings date and it equals ``today``.

    ``today`` defaults to New York's Monday–Friday date.  The weekends
    fallback catches a Saturday/Sunday runtime: on a Saturday the window
    stretches to include Friday, and on a Sunday it includes Friday and
    Saturday so a Monday filing is still "today" in business-day terms.
    """
    if today is None:
        today = _ny_today()
    moment = earnings_date_for(value, today=today)
    if moment is None:
        return False
    return _is_today_or_adjacent(moment, today)


def is_earnings_soon(value: Any, *, today: date | None = None) -> bool:
    """True when ``value`` is an earnings date in the next two business days.

    Two sessions, not one, because a trader looking after the close on a
    Tuesday wants to see Wednesday *and* Thursday ahead, and the signal
    is "earnings after today", not "earnings tomorrow".
    """
    if today is None:
        today = _ny_today()
    moment = earnings_date_for(value, today=today)
    if moment is None:
        return False
    # Already today → belongs in *before*, not *after*.
    if _is_today_or_adjacent(moment, today):
        return False
    return _within_sessions(moment, today, after=2)


# ---------------------------------------------------------------------------
# Major news keywords
# ---------------------------------------------------------------------------

#: Keywords whose presence in a headline suggests a material event.  Kept
#: as a frozen set so the list is easy to audit and cheap to test.
NEWS_KEYWORDS: frozenset[str] = frozenset(
    {
        "merger",
        "acquisition",
        "acquire",
        "acquires",
        "acquired",
        "fda",
        "lawsuit",
        "lawsuit",
        "litigation",
        "bankruptcy",
        "restructuring",
        "layoff",
        "layoffs",
        "spin-off",
        "spinoff",
        "ipo",
        "going public",
        "recall",
        "scandal",
        "fraud",
        "investigation",
        "subpoena",
        "settlement",
        "antitrust",
        "monopoly",
        "breach",
        "hack",
        "cyberattack",
        "ransomware",
        "delisting",
        "suspension",
        "halts",
        "halted",
        "ceo",
        "cfo",
        "executive",
        "resign",
        "resigns",
        "resigned",
        "fired",
        "activist",
        "takeover",
        "buyout",
        "tender offer",
        "share buyback",
        "dividend",
        "split",
        "guidance",
        "warns",
        "warning",
        "profit warning",
        "earnings surprise",
        "beat",
        "miss",
        "downgrade",
        "upgrade",
        "initiate",
        "coverage",
        "target price",
        "price target",
        "upgrade",
        "downgrade",
    }
)


def headline_matches_keywords(headline: str) -> bool:
    """True when ``headline`` contains at least one of :data:`NEWS_KEYWORDS`.

    Matching is case-insensitive and scans substrings, so "Antitrust" matches
    "antitrust" and "mergers" does NOT match "merger" — the word list is
    exact on purpose, not stemmed.
    """
    if not headline.strip():
        return False
    lowered = headline.strip().lower()
    for keyword in NEWS_KEYWORDS:
        if keyword in lowered:
            return True
    return False


NEWS_UNAVAILABLE_REASON = (
    "news analysis not real-time; keyword list ready for connection"
)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

from datetime import timedelta


def _ny_today() -> date:
    """New York's date right now."""
    return datetime.now(tz=NEW_YORK).date()


def _is_today_or_adjacent(moment: date, today: date) -> bool:
    """A Saturday runtime: Friday is "today". Sunday: Friday+Saturday are "today"."""
    weekday = today.weekday()  # Monday=0, Sunday=6
    if weekday == 5:  # Saturday
        return moment == today or moment == today - timedelta(days=1)
    if weekday == 6:  # Sunday
        return moment in (
            today,
            today - timedelta(days=1),
            today - timedelta(days=2),
        )
    return moment == today


def _within_sessions(moment: date, today: date, *, after: int) -> bool:
    """``moment`` is in the next ``after`` business days from ``today``."""
    cursor = today
    for _ in range(after):
        cursor = cursor + timedelta(days=1)
        while cursor.weekday() >= 5:  # skip Saturday/Sunday
            cursor = cursor + timedelta(days=1)
        if moment == cursor:
            return True
    return False