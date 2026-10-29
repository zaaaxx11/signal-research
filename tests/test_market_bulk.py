

# ---------------------------------------------------------------------------
# The stamp shapes CNBC actually prints
# ---------------------------------------------------------------------------
#
# CNBC moved from a full ISO-8601 instant to a bare market date with the zone
# in a companion field; a parser that only knew the first shape silently
# dropped every quote.


def test_stamp_reads_the_full_iso_form() -> None:
    from app.market.bulk import _stamp

    moment = _stamp("2026-10-27T14:49:51.640-0400")
    assert moment is not None
    assert moment.isoformat() == "2026-10-27T18:49:51.640000+00:00"


def test_stamp_reads_a_bare_market_date_with_the_zone_companion() -> None:
    from app.market.bulk import _stamp

    moment = _stamp("2026-10-28", "10/28/26 EDT")
    assert moment is not None
    assert moment.isoformat() == "2026-10-28T04:00:00+00:00", "midnight EDT"


def test_stamp_reads_a_bare_market_date_without_a_zone_as_utc_midnight() -> None:
    from app.market.bulk import _stamp

    moment = _stamp("2026-10-28")
    assert moment is not None
    assert moment.isoformat() == "2026-10-28T00:00:00+00:00"


def test_stamp_falls_back_to_the_timedate_companion_alone() -> None:
    from app.market.bulk import _stamp

    moment = _stamp(None, "10/28/26 EST")
    assert moment is not None
    assert moment.isoformat() == "2026-10-28T05:00:00+00:00"


def test_stamp_rejects_a_shape_it_does_not_know() -> None:
    from app.market.bulk import _stamp

    assert _stamp("not-a-date") is None
    assert _stamp("") is None
    assert _stamp(None) is None
    assert _stamp("2026-10-28", "garbage") is not None, "the bare date still parses"


def test_parse_cnbc_quote_survives_the_bare_date_shape() -> None:
    from app.market.bulk import parse_cnbc_quote

    row = {
        "symbol": "AAPL",
        "last": "343.83",
        "last_time": "2026-10-28",
        "last_timedate": "10/28/26 EDT",
        "previous_day_closing": "343.83",
        "volume": "29,025,462",
        "realTime": "true",
    }
    quote = parse_cnbc_quote(row)
    assert quote is not None
    assert quote.last == 343.83
    assert quote.volume == 29_025_462
    assert quote.ts_exchange.isoformat() == "2026-10-28T04:00:00+00:00"
