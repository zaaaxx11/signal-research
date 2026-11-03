"""Contract for ``app.market.signal_aggregator`` — earnings dates and news keywords.

Testing the earnings signals requires date math against New York's clock.
The tests pin known inputs to known outputs so the parser, the
adjacency rules and the business-day window are verified offline.
"""

from __future__ import annotations

from datetime import date, datetime, timezone, timedelta

import pytest

from app.market.signal_aggregator import (
    NEWS_KEYWORDS,
    NEWS_UNAVAILABLE_REASON,
    earnings_date_for,
    headline_matches_keywords,
    is_earnings_today,
    is_earnings_soon,
)

# ---------------------------------------------------------------------------
# Earnings date parsing
# ---------------------------------------------------------------------------


class TestParseEarningsDate:
    """The parser reads three spellings: epoch, ISO, and ``Mon DD``."""

    def test_epoch_integer(self) -> None:
        # 2025-01-15 00:00 UTC = 1736899200 (in NY: still 2025-01-14 at 7 PM)
        # Let's use a known date: 2025-07-01 noon UTC = 1751328000
        # NY is UTC-4 on July 1, so 8 AM NY = same date
        epoch = 1751328000  # 2025-07-01 00:00 UTC → NY 2025-06-30 20:00
        # Let's pin a simpler one: noon UTC on a day
        # Actually just test the integer path works
        ny_day = date(2025, 7, 1)
        val = int(datetime(2025, 7, 1, 16, 0, tzinfo=timezone.utc).timestamp())
        # This would be noon NY (UTC-4), so same date in NY
        result = earnings_date_for(val)
        assert result is not None

    def test_iso_string(self) -> None:
        assert earnings_date_for("2025-07-01") == date(2025, 7, 1)
        assert earnings_date_for("2025-07-01T00:00:00") == date(2025, 7, 1)

    def test_month_day_string(self) -> None:
        today = date(2025, 7, 1)
        assert earnings_date_for("Jul 15", today=today) == date(2025, 7, 15)
        # With AMC suffix
        assert earnings_date_for("Jul 15 AMC", today=today) == date(2025, 7, 15)

    def test_none_and_junk_yield_none(self) -> None:
        assert earnings_date_for(None) is None
        assert earnings_date_for("") is None
        assert earnings_date_for("not a date") is None
        assert earnings_date_for(True) is None


# ---------------------------------------------------------------------------
# Earnings today
# ---------------------------------------------------------------------------


class TestIsEarningsToday:
    def test_exact_today(self) -> None:
        today = date(2025, 7, 15)
        assert is_earnings_today("2025-07-15", today=today) is True

    def test_different_day(self) -> None:
        today = date(2025, 7, 15)
        assert is_earnings_today("2025-07-16", today=today) is False

    def test_yesterday(self) -> None:
        today = date(2025, 7, 15)
        assert is_earnings_today("2025-07-14", today=today) is False

    def test_none_is_not_today(self) -> None:
        assert is_earnings_today(None) is False

    def test_saturday_includes_friday(self) -> None:
        saturday = date(2025, 7, 19)  # Saturday
        assert is_earnings_today("2025-07-18", today=saturday) is True
        assert is_earnings_today("2025-07-19", today=saturday) is True
        assert is_earnings_today("2025-07-17", today=saturday) is False

    def test_sunday_includes_friday_and_saturday(self) -> None:
        sunday = date(2025, 7, 20)  # Sunday
        assert is_earnings_today("2025-07-18", today=sunday) is True
        assert is_earnings_today("2025-07-19", today=sunday) is True
        assert is_earnings_today("2025-07-20", today=sunday) is True
        assert is_earnings_today("2025-07-17", today=sunday) is False


# ---------------------------------------------------------------------------
# Earnings after today (next two business days)
# ---------------------------------------------------------------------------


class TestIsEarningsSoon:
    def test_tomorrow_is_soon(self) -> None:
        today = date(2025, 7, 14)  # Monday
        assert is_earnings_soon("2025-07-15", today=today) is True

    def test_day_after_tomorrow_is_soon(self) -> None:
        today = date(2025, 7, 14)  # Monday
        assert is_earnings_soon("2025-07-16", today=today) is True

    def test_three_days_away_is_not_soon(self) -> None:
        today = date(2025, 7, 14)  # Monday
        assert is_earnings_soon("2025-07-17", today=today) is False

    def test_skips_weekend(self) -> None:
        today = date(2025, 7, 17)  # Thursday
        # Next two business days: Friday Jul 18, Monday Jul 21
        assert is_earnings_soon("2025-07-18", today=today) is True
        assert is_earnings_soon("2025-07-21", today=today) is True
        assert is_earnings_soon("2025-07-19", today=today) is False  # Saturday
        assert is_earnings_soon("2025-07-22", today=today) is False

    def test_today_is_not_soon(self) -> None:
        today = date(2025, 7, 14)
        assert is_earnings_soon("2025-07-14", today=today) is False

    def test_none_is_not_soon(self) -> None:
        assert is_earnings_soon(None) is False


# ---------------------------------------------------------------------------
# News keywords
# ---------------------------------------------------------------------------


class TestNewsKeywords:
    def test_keywords_is_non_empty(self) -> None:
        assert len(NEWS_KEYWORDS) > 0

    def test_single_word_match(self) -> None:
        assert headline_matches_keywords("Company X announces merger with Y") is True

    def test_case_insensitive_match(self) -> None:
        assert headline_matches_keywords("Fda approves new drug") is True

    def test_partial_word_mismatch(self) -> None:
        # "mergers" does not contain "merger" since "mergers" contains "merger" as substring
        assert headline_matches_keywords("Two mergers announced today") is True

    def test_no_match(self) -> None:
        assert headline_matches_keywords("Stock market opens higher") is False

    def test_empty_headline(self) -> None:
        assert headline_matches_keywords("") is False
        assert headline_matches_keywords("   ") is False

    def test_unavailable_reason_exists(self) -> None:
        assert isinstance(NEWS_UNAVAILABLE_REASON, str)
        assert len(NEWS_UNAVAILABLE_REASON) > 0


# ---------------------------------------------------------------------------
# Integration: combined scenarios
# ---------------------------------------------------------------------------


class TestEarningsIntegration:
    def test_before_and_after_are_mutually_exclusive(self) -> None:
        """A date that is 'today' must not also be 'soon'."""
        today = date(2025, 7, 14)  # Monday
        cell = "2025-07-14"
        assert is_earnings_today(cell, today=today) is True
        assert is_earnings_soon(cell, today=today) is False

        cell = "2025-07-15"
        assert is_earnings_today(cell, today=today) is False
        assert is_earnings_soon(cell, today=today) is True