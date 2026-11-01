"""Descriptive buckets: named server-side predicates, one id per Finviz option.

The contract (plan ``2026-11-01_descriptive-filters`` D3):

* a bucket is a **name**, not a range: ``price:u5``, ``cap:mega``,
  ``earn:todaybefore``, ``idx:sp500``. The id is ``"<control>:<finviz-token>"``
  and the token is the probe's, so a URL and a 422 can both name one bucket;
* four predicate *shapes* justify the module, and three of them no
  ``field:op:value`` triple can express: two columns related
  (``target_price >= 1.5 * price``, ``float / shares < 10%``), a date against
  the market's own day (``earn:today`` is New York's today, not UTC's), and set
  membership (``idx:sp500``);
* **unknown is not zero and not false**: a null cell matches no bucket, and a
  dividend yield that nobody reported matches neither ``none`` nor ``pos``;
* units come from Finviz's **label**, not from the token's digits: ``sh_avgvol``
  is in thousands (``u1000`` is "Under 1M" = ``< 1_000_000``), shares
  outstanding and float are in millions;
* a key with no option vocabulary (``sh_curvol``) has no buckets at all, so a
  token on it is refused instead of applied as a no-op.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

import pytest

from app.screen import buckets
from app.screen.buckets import (
    Bucket,
    Context,
    UnknownBucket,
    UnavailableBucket,
    apply_bucket_filters,
    bucket,
    for_key,
)

#: The moment a bucket's clock reads. Late enough in UTC that New York is still
#: on the previous day — the one case a UTC-only implementation gets wrong.
AS_OF = "2026-11-02T02:30:00.000000+00:00"


@dataclass(frozen=True, slots=True)
class Token:
    """The criterion shape a bucket compiles to: no field, a key, a token."""

    field: str | None
    op: str
    value: Any
    key: str | None = None


def _control(key: str) -> str:
    return buckets.control_for(key)


def _token(key: str, token: str) -> str:
    return bucket(f"{_control(key)}:{token}").id


def matches(key: str, token: str, rows: list[dict[str, Any]], *, as_of: str = AS_OF) -> list[dict[str, Any]]:
    """Run one bucket over ``rows`` exactly as ``apply_bucket_filters`` will."""

    criterion = Token(field=None, op="eq", value=_token(key, token), key=key)
    return apply_bucket_filters(rows, [criterion], as_of=as_of)


def one(**cells: Any) -> dict[str, Any]:
    row: dict[str, Any] = {"symbol": "TEST"}
    row.update(cells)
    return row


# ---------------------------------------------------------------------------
# T1 — the framework, price, market cap
# ---------------------------------------------------------------------------


def test_a_bucket_id_is_namespaced_by_its_control() -> None:
    for key in ("sh_price", "cap", "fa_div", "sh_short", "an_recom", "earningsdate",
                "sh_avgvol", "sh_relvol", "sh_outstanding", "sh_float",
                "targetprice"):
        family = for_key(key)
        assert family, f"{key} should have a bucket family"
        control = buckets.control_for(key)
        assert control, f"{key} should name the control its ids are namespaced by"
        for item in family:
            assert item.id.startswith(f"{control}:"), item.id
            assert item.key == key
            assert item.label.strip(), f"{item.id} needs a label"
            assert item.describe.strip(), f"{item.id} needs an audit line"


def test_market_cap_bands_partition_a_universe() -> None:
    """mega/large/mid/small/micro/nano: 5,000 rows, exactly one band each."""

    rows = [
        one(symbol=f"R{index}", market_cap_usd=10 ** (5 + 8 * index / 4999))
        for index in range(5000)
    ]
    bands = ("mega", "large", "mid", "small", "micro", "nano")
    counts = {band: len(matches("cap", band, rows)) for band in bands}
    assert sum(counts.values()) == len(rows), counts
    assert all(counts[band] > 0 for band in bands), counts

    # Every row lands in exactly one of the six, re-derived from the counts.
    for row in rows:
        hits = [band for band in bands if matches("cap", band, [row])]
        assert len(hits) == 1, (row["market_cap_usd"], hits)

    # The overlap bands are deliberately *not* part of the partition: "+Large"
    # is the union of mega and large, and it excludes the nano tail.
    assert len(matches("cap", "largeover", rows)) == counts["mega"] + counts["large"]
    assert len(matches("cap", "microunder", rows)) == counts["micro"] + counts["nano"]


def test_price_u5_is_below_five() -> None:
    rows = [one(symbol="CHEAP", price=4.99), one(symbol="EDGE", price=5.0)]
    kept = matches("sh_price", "u5", rows)
    assert [row["symbol"] for row in kept] == ["CHEAP"]


def test_a_bucket_threshold_is_read_from_the_label_not_the_digits() -> None:
    """``sh_price:o1.5`` does not exist; ``sh_relvol:o1.5`` does — one decimal."""

    assert [row["symbol"] for row in matches("sh_relvol", "o1.5", [one(symbol="A", rel_volume_10d=1.49)])] == []


def test_an_unknown_bucket_names_the_ones_that_exist() -> None:
    with pytest.raises(UnknownBucket) as caught:
        bucket("price:u99")
    assert caught.value.code == "unknown_bucket"
    assert "u99" in str(caught.value)
    assert "price:u5" in str(caught.value)


def test_a_bucket_without_a_namespace_is_unknown() -> None:
    with pytest.raises(UnknownBucket):
        bucket("u5")


def test_apply_bucket_filters_is_the_identity_without_bucket_criteria() -> None:
    rows = [one(symbol="A", price=1.0)]
    compiled = Token(field="price", op="lt", value=5.0, key=None)
    assert apply_bucket_filters(rows, [compiled], as_of=AS_OF) == rows


def test_apply_bucket_filters_reads_the_published_mapping_shape() -> None:
    """The executor accepts ``{key, field, op, value}`` as well as a Criterion.

    ``select_rows`` hands it :class:`app.screen.selection.Criterion` objects, but
    publishing the same predicate as a plain dict must not need a second
    adapter: the mapping is what ``Criterion.as_dict`` writes.
    """

    rows = [one(symbol="CHEAP", price=4.99), one(symbol="EDGE", price=5.0)]
    published = {"key": "sh_price", "field": None, "op": "eq", "value": "price:u5"}
    kept = apply_bucket_filters(rows, [published], as_of=AS_OF)
    assert [row["symbol"] for row in kept] == ["CHEAP"]


def test_the_clock_reads_a_naive_stamp_as_utc() -> None:
    """Every ``as_of`` the API emits carries an offset; a naked one is UTC."""

    assert Context.at("2026-11-02T02:30:00").today == date(2026, 11, 1)
    assert Context.at("2026-11-02T02:30:00Z").today == date(2026, 11, 1)
    assert Context.at("2026-11-02T12:00:00-05:00").today == date(2026, 11, 2)


# ---------------------------------------------------------------------------
# T2 — short float, dividend, analyst recommendation, relative float
# ---------------------------------------------------------------------------


def test_dividend_none_matches_no_bucket() -> None:
    """A missing yield is unknown, not 0% — it is neither ``none`` nor ``pos``."""

    unknown = one(symbol="UNKNOWN", dividend_yield=None)
    zero = one(symbol="ZERO", dividend_yield=0.0)
    payer = one(symbol="PAYER", dividend_yield=0.47)

    assert matches("fa_div", "none", [unknown, zero, payer]) == [zero]
    assert [row["symbol"] for row in matches("fa_div", "pos", [unknown, zero, payer])] == ["PAYER"]


def test_float_percent_band_divides_by_shares_outstanding() -> None:
    small = one(symbol="SMALL", float_shares=5_000_000, shares_outstanding=100_000_000)
    large = one(symbol="LARGE", float_shares=15_000_000, shares_outstanding=100_000_000)
    kept = matches("sh_float", "u10p", [small, large])
    assert [row["symbol"] for row in kept] == ["SMALL"]


def test_a_float_percent_bucket_matches_nothing_without_both_cells() -> None:
    no_shares = one(symbol="NOSHARES", float_shares=5_000_000, shares_outstanding=None)
    no_float = one(symbol="NOFLOAT", float_shares=None, shares_outstanding=100_000_000)
    zero_shares = one(symbol="ZERO", float_shares=5_000_000, shares_outstanding=0)
    assert matches("sh_float", "u10p", [no_shares, no_float, zero_shares]) == []


def test_recom_strongbuy_is_lt_2() -> None:
    strong = one(symbol="STRONG", recom_finviz=1.9)
    edge = one(symbol="EDGE", recom_finviz=2.0)
    kept = matches("an_recom", "strongbuy", [strong, edge])
    assert [row["symbol"] for row in kept] == ["STRONG"]


def test_short_float_bands_are_percentage_points() -> None:
    rows = [
        one(symbol="LOW", short_float=4.9),
        one(symbol="MID", short_float=12.0),
        one(symbol="HIGH", short_float=20.1),
        one(symbol="UNKNOWN", short_float=None),
    ]
    assert [row["symbol"] for row in matches("sh_short", "low", rows)] == ["LOW"]
    assert [row["symbol"] for row in matches("sh_short", "high", rows)] == ["HIGH"]
    assert [row["symbol"] for row in matches("sh_short", "u15", rows)] == ["LOW", "MID"]


# ---------------------------------------------------------------------------
# T3 — the volume family and shares outstanding
# ---------------------------------------------------------------------------


def test_average_volume_tokens_are_shares_not_thousands() -> None:
    """``sh_avgvol:u1000`` is Finviz's "Under 1M", so the boundary is 1e6."""

    under = one(symbol="UNDER", avg_volume_30d=999_999)
    edge = one(symbol="EDGE", avg_volume_30d=1_000_000)
    kept = matches("sh_avgvol", "u1000", [under, edge])
    assert [row["symbol"] for row in kept] == ["UNDER"]
    assert bucket(_token("sh_avgvol", "u1000")).label == "Under 1M"


def test_relative_volume_decimal_token() -> None:
    above = one(symbol="ABOVE", rel_volume_10d=1.51)
    edge = one(symbol="EDGE", rel_volume_10d=1.5)
    assert [row["symbol"] for row in matches("sh_relvol", "o1.5", [above, edge])] == ["ABOVE"]


def test_shares_outstanding_tokens_are_millions() -> None:
    under = one(symbol="UNDER", shares_outstanding=999_999)
    edge = one(symbol="EDGE", shares_outstanding=1_000_000)
    assert [row["symbol"] for row in matches("sh_outstanding", "u1", [under, edge])] == ["UNDER"]


def test_current_volume_has_no_buckets() -> None:
    """The probe's only named option is "Any"; a range needs a number, not a token."""

    assert for_key("sh_curvol") == ()
    with pytest.raises(UnknownBucket):
        bucket("sh_curvol:u50")


# ---------------------------------------------------------------------------
# T4 — target price and earnings dates
# ---------------------------------------------------------------------------


def test_target_price_is_relative_to_the_rows_own_price() -> None:
    hit = one(symbol="HIT", price=100.0, target_price=150.0)
    miss = one(symbol="MISS", price=100.0, target_price=140.0)
    below = one(symbol="BELOW", price=100.0, target_price=50.0)

    assert [row["symbol"] for row in matches("targetprice", "a50", [hit, miss, below])] == ["HIT"]
    assert [row["symbol"] for row in matches("targetprice", "b50", [hit, miss, below])] == ["BELOW"]
    assert [row["symbol"] for row in matches("targetprice", "above", [hit, miss, below])] == [
        "HIT",
        "MISS",
    ]
    assert [row["symbol"] for row in matches("targetprice", "below", [hit, miss, below])] == ["BELOW"]


def test_a_target_bucket_matches_nothing_without_a_price() -> None:
    row = one(symbol="NOPRICE", price=None, target_price=150.0)
    assert matches("targetprice", "a50", [row]) == []


def test_earnings_today_is_new_york_today() -> None:
    """02:30Z on 2 November is still 1 November in New York."""

    new_york_today = one(symbol="NY", earnings_date=date(2026, 11, 1))
    utc_today = one(symbol="UTC", earnings_date=date(2026, 11, 2))
    kept = matches("earningsdate", "today", [new_york_today, utc_today])
    assert [row["symbol"] for row in kept] == ["NY"]
    assert Context.at(AS_OF).today == date(2026, 11, 1)


def test_the_earnings_windows_are_iso_weeks() -> None:
    # 2026-11-01 is a Sunday: its ISO week is Mon 26 Oct … Sun 1 Nov.
    today = one(symbol="TODAY", earnings_date="2026-11-01")
    monday = one(symbol="MON", earnings_date="2026-10-26")
    last_week = one(symbol="PREV", earnings_date="2026-10-19")
    next_week = one(symbol="NEXT", earnings_date="2026-11-03")

    assert [r["symbol"] for r in matches("earningsdate", "thisweek", [today, monday, last_week, next_week])] == [
        "TODAY",
        "MON",
    ]
    assert [r["symbol"] for r in matches("earningsdate", "prevweek", [today, monday, last_week])] == [
        "PREV"
    ]
    assert [r["symbol"] for r in matches("earningsdate", "nextweek", [today, next_week])] == ["NEXT"]
    # "This month" is the calendar month, not the ISO week: 26 October is last
    # week *and* last month.
    november = one(symbol="NOV", earnings_date="2026-11-15")
    assert [r["symbol"] for r in matches("earningsdate", "thismonth", [today, monday, last_week, november])] == [
        "TODAY",
        "NOV",
    ]


def test_the_earnings_parser_agrees_with_calendar() -> None:
    """The bucket module owns a second parser; the two must not drift.

    Production may not import :mod:`app.api` (that is the HTTP layer), so the
    duplicate exists. It is asserted here instead: the ISO and month-day
    spellings are the calendar's, and the epoch integer the *scan* writes is a
    third spelling the calendar refuses (``text()`` rejects non-strings) while a
    screen row really carries it — the one documented difference.
    """

    from app.api.calendar import parse_earnings_date

    today = date(2026, 11, 1)
    for raw in ("2026-11-17", "Nov 17", "2026-11-01"):
        assert buckets.day(raw, today=today) == parse_earnings_date(raw, today=today), raw

    # Epoch seconds: the scan's own shape, and a date *label* — the collector
    # reads it as a UTC day (app.market.yahoo_batch._epoch_date), not as an
    # instant to be re-zoned.
    epoch = int(datetime(2026, 11, 17, tzinfo=UTC).timestamp())
    assert buckets.day(epoch, today=today) == date(2026, 11, 17)
    assert parse_earnings_date(epoch, today=today) is None, (
        "the calendar refuses non-strings; if this line ever fails the duplicate "
        "parser can be deleted and the calendar imported"
    )


def test_the_six_session_options_are_unavailable_with_a_reason() -> None:
    """No session stamp exists, so BMO/AMC cannot be told apart — say so."""

    family = {item.finviz: item for item in for_key("earningsdate")}
    assert len(family) == 15, "the probe's 15 named options"
    sessions = (
        "todaybefore",
        "todayafter",
        "tomorrowbefore",
        "tomorrowafter",
        "yesterdaybefore",
        "yesterdayafter",
    )
    for token in sessions:
        item = family[token]
        assert item.available is False, token
        assert item.reason and "session" in item.reason, token
        with pytest.raises(UnavailableBucket) as caught:
            bucket(item.id)
        assert caught.value.code == "unavailable_bucket"
    available = [token for token, item in family.items() if item.available]
    assert len(available) == 9, available
    assert set(available) == {
        "today",
        "tomorrow",
        "yesterday",
        "nextdays5",
        "prevdays5",
        "thisweek",
        "nextweek",
        "prevweek",
        "thismonth",
    }


# ---------------------------------------------------------------------------
# The families are complete against the probe's vocabulary
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "count"),
    [
        ("sh_price", 39),
        ("cap", 14),
        ("fa_div", 14),
        ("sh_short", 14),
        ("an_recom", 9),
        ("earningsdate", 15),
        ("sh_avgvol", 18),
        ("sh_relvol", 16),
        ("sh_outstanding", 16),
        ("sh_float", 34),
        ("targetprice", 14),
    ],
)
def test_the_family_is_the_probe_option_count(key: str, count: int) -> None:
    assert len(for_key(key)) == count


def test_every_bucket_is_a_callable_predicate_over_a_row() -> None:
    row = one(
        symbol="ALL",
        price=10.0,
        market_cap_usd=3_000_000_000.0,
        dividend_yield=1.0,
        short_float=5.0,
        recom_finviz=2.5,
        earnings_date=date(2026, 11, 1),
        avg_volume_30d=500_000.0,
        rel_volume_10d=1.0,
        shares_outstanding=100_000_000.0,
        float_shares=50_000_000.0,
        target_price=12.0,
    )
    context = Context.at(AS_OF)
    for key in ("sh_price", "cap", "fa_div", "sh_short", "an_recom", "earningsdate",
                "sh_avgvol", "sh_relvol", "sh_outstanding", "sh_float",
                "targetprice"):
        for item in for_key(key):
            if not item.available:
                continue
            assert item.predicate(row, context) is False or item.predicate(row, context) is True
            assert isinstance(item.describe, str)
