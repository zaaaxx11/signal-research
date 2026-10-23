"""P1 gate — 71 Finviz quote metrics, their printed units, and news from a document.

Gate (PLAN-finviz-parity.md §1, §6 #2 and #5):

* the quote page carries **71** metric cells; :data:`app.market.fields.METRICS`
  names every one of them, in fixture order, each with a unique key;
* ``B``/``M``/``K`` scale, ``%`` keeps its magnitude, ``$`` is stripped and
  ``N/A`` is ``None`` — never ``0``, never a guess;
* the one duplicated Finviz label (``EPS next Y``: a currency estimate *and* a
  growth rate) becomes two metrics, not one overwritten cell;
* a canonical column is claimed only when the *definition* matches: ``SMA20`` is
  a distance in percent, not ``ma_20``; ``Avg Volume`` declares no averaging
  window, so it is not ``avg_volume_20d``;
* Google News RSS is parsed from a **document** (no request), and an item without
  a ``pubDate`` has ``published_at = None`` rather than "now".

The RSS parser lives in ``app/news/rss.py``; its tests sit in this file because
this is the "read exactly what the provider printed" suite.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import Any

import pytest

from app.market import fields as F
from app.news import rss
from tests._support import COLUMNS, ROOT

QUOTE_FIXTURE = ROOT / "tests" / "fixtures" / "finviz_quote_metrics.json"
NEWS_FIXTURE = ROOT / "tests" / "fixtures" / "google_news_sample.xml"

FIXTURE_ROWS: list[dict[str, str]] = json.loads(
    QUOTE_FIXTURE.read_text(encoding="utf-8")
)
SNAPSHOT = F.parse_snapshot(FIXTURE_ROWS)


# ---------------------------------------------------------------------------
# The table: 71 metrics, fixture order, unique keys
# ---------------------------------------------------------------------------


def test_the_table_declares_seventy_one_metrics() -> None:
    assert len(F.METRICS) == 71, (
        "PLAN-finviz-parity.md §1 measures 71 quote metrics; the table must name "
        f"all of them, not {len(F.METRICS)}"
    )
    assert len(F.METRIC_BY_KEY) == 71


def test_metric_keys_are_unique_and_snake_case() -> None:
    keys = [metric.key for metric in F.METRICS]
    assert len(set(keys)) == len(keys), f"duplicate metric key(s) in {keys}"
    for key in keys:
        assert key.islower() and " " not in key and "-" not in key, key


def test_the_table_follows_the_finviz_print_order() -> None:
    printed = [row["name"] for row in FIXTURE_ROWS]
    assert [metric.label for metric in F.METRICS] == printed, (
        "the table must be the fixture's cell order, so a screenshot and the "
        "model can be read side by side"
    )


def test_every_fixture_cell_is_present_and_filled() -> None:
    assert len(SNAPSHOT) == 71
    unfilled = {
        key: (cell.value, cell.reason)
        for key, cell in SNAPSHOT.items()
        if cell.value is None or cell.reason is not None
    }
    assert unfilled == {}, (
        "the fixture is a complete quote page: every cell must parse to a value "
        f"with no caveat, got {unfilled}"
    )


def test_the_duplicated_eps_next_y_label_is_two_metrics() -> None:
    group = F.METRICS_BY_LABEL["EPS next Y"]
    assert len(group) == 2, "Finviz prints 'EPS next Y' twice (estimate and growth)"
    assert group[0].key == "eps_next_y" and group[0].unit is F.Unit.USD
    assert group[1].key == "eps_next_y_growth" and group[1].unit is F.Unit.PERCENT
    assert SNAPSHOT["eps_next_y"].value == pytest.approx(9.60)
    assert SNAPSHOT["eps_next_y_growth"].value == pytest.approx(8.61), (
        "the growth cell must not overwrite the estimate cell"
    )


def test_a_label_printed_more_often_than_declared_is_an_error() -> None:
    rows = [
        {"name": "EPS next Y", "raw": "9.60"},
        {"name": "EPS next Y", "raw": "8.61%"},
        {"name": "EPS next Y", "raw": "7.00%"},
    ]
    with pytest.raises(ValueError, match="appears 3 times"):
        F.parse_snapshot(rows)


def test_an_unknown_metric_key_is_a_caller_error() -> None:
    with pytest.raises(ValueError, match="unknown quote metric"):
        F.metric_for("not_a_finviz_metric")
    with pytest.raises(ValueError, match="unknown quote metric"):
        F.canonical_for("not_a_finviz_metric")


def test_a_label_the_table_does_not_know_is_schema_drift() -> None:
    rows = [{"name": "Dividend", "raw": "0.80"}, {"name": "Price", "raw": "347.79"}]
    with pytest.raises(ValueError, match="unknown Finviz label"):
        # strict mode must not drop a cell silently
        F.parse_snapshot(rows)

    lenient = F.parse_snapshot(rows, strict=False)
    assert set(lenient) == {"price"}, "non-strict mode drops only the unknown label"


# ---------------------------------------------------------------------------
# Units: B/M/K, %, $, commas
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("market_cap", 5_075_670_000_000.0),
        ("avg_volume", 44_060_000.0),
        ("shares_outstanding", 14_610_000_000.0),
        ("volume", 8_992_502.0),
        ("employees", 166_000.0),
    ],
)
def test_magnitude_suffixes_scale_to_units(key: str, expected: float) -> None:
    assert F.parse_cell(key, SNAPSHOT[key].raw).value == pytest.approx(expected)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("1.5K", 1_500.0), ("2M", 2_000_000.0), ("3.25B", 3_250_000_000.0), ("1T", 1e12)],
)
def test_every_suffix_the_printer_uses_is_read(raw: str, expected: float) -> None:
    assert F.parse_scalar(raw, F.Unit.COUNT) == pytest.approx(expected)


def test_a_percent_keeps_its_magnitude_and_is_never_rescaled() -> None:
    assert F.parse_cell("eps_this_y", "18.43%").value == pytest.approx(18.43)
    assert F.parse_cell("insider_trans", "-3.26%").value == pytest.approx(-3.26)
    assert F.parse_cell("insider_own", "0.12%").value == pytest.approx(0.12), (
        "0.12% is 0.12 percentage points; dividing by 100 twice would invent 0.0012"
    )
    assert F.parse_cell("perf_10y", "1093.10%").value == pytest.approx(1093.10)


def test_a_dollar_sign_is_stripped_without_changing_the_number() -> None:
    assert F.parse_cell("price", "$347.79").value == pytest.approx(347.79)
    assert F.parse_scalar("$1,234.50", F.Unit.USD) == pytest.approx(1234.50)
    assert F.parse_scalar("$", F.Unit.USD) is None, "a lone sign is not a price"


def test_thousands_separators_are_read() -> None:
    assert F.parse_cell("volume", "8,992,502").value == pytest.approx(8_992_502.0)
    assert F.parse_cell("volume", "1 234").value is None, (
        "a space separator is not a thousands separator we guess"
    )


# ---------------------------------------------------------------------------
# Null sentinel: absent, never zero
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw", ["N/A", "n/a", "N.A.", "-", "--", "", "nan", "None"])
def test_sentinels_are_none_never_zero(raw: str) -> None:
    cell = F.parse_cell("price", raw)
    assert cell.value is None, f"{raw!r} is an absence, not the price 0"
    assert cell.reason is F.MissingReason.ABSENT


def test_a_none_value_is_absent_not_zero() -> None:
    cell = F.parse_cell("pe", None)
    assert (cell.value, cell.reason) == (None, F.MissingReason.ABSENT)


@pytest.mark.parametrize(("raw", "expected"), [("0", 0.0), ("0.00", 0.0), (0, 0.0)])
def test_a_real_zero_is_kept_and_counts_as_filled(raw: object, expected: float) -> None:
    cell = F.parse_cell("current_ratio", raw)
    assert cell.value == pytest.approx(expected)
    assert cell.reason is None
    assert cell.filled is True


def test_free_text_is_unparsed_not_zero() -> None:
    cell = F.parse_cell("pe", "abc")
    assert cell.value is None
    assert cell.reason is F.MissingReason.UNPARSED, (
        "a cell the parser cannot read must say so; silently returning 0 would "
        "publish a fake P/E"
    )
    assert F.parse_cell("volume", "1.2.3").reason is F.MissingReason.UNPARSED


@pytest.mark.parametrize(("key", "raw"), [("pe", "39.87%"), ("price", "347.79%")])
def test_a_percent_in_a_currency_or_multiple_cell_is_a_unit_mismatch(
    key: str, raw: str
) -> None:
    cell = F.parse_cell(key, raw)
    assert cell.value is None, "a percent sign is a different unit, not a suffix"
    assert cell.reason is F.MissingReason.UNPARSED


# ---------------------------------------------------------------------------
# Dates and multi-part cells
# ---------------------------------------------------------------------------


def test_ipo_is_read_as_a_date() -> None:
    cell = F.parse_cell("ipo", "Dec 12, 1980")
    assert cell.value == date(1980, 12, 12)
    assert cell.reason is None
    assert SNAPSHOT["ipo"].value == date(1980, 12, 12)


@pytest.mark.parametrize("raw", ["soon", "1980-13-45", "Dec 32, 1980"])
def test_a_date_that_is_not_a_date_is_unparsed(raw: str) -> None:
    cell = F.parse_cell("ipo", raw)
    assert cell.value is None
    assert cell.reason is F.MissingReason.UNPARSED


def test_pair_cells_keep_both_numbers_and_their_units() -> None:
    assert SNAPSHOT["week52_high"].value == pytest.approx((348.36, -0.16))
    assert SNAPSHOT["week52_low"].value == pytest.approx((243.42, 42.88))
    assert SNAPSHOT["volatility"].value == pytest.approx((1.77, 1.80))
    assert SNAPSHOT["eps_past_3_5y"].value == pytest.approx((6.89, 17.91)), (
        "3-year and 5-year growth are two numbers, not an average"
    )


def test_a_pair_with_one_missing_half_is_partial_not_dropped() -> None:
    cell = F.parse_cell("week52_high", "348.36 N/A")
    assert cell.value == (pytest.approx(348.36), None)
    assert cell.reason is F.MissingReason.PARTIAL, (
        "keeping the price and flagging the missing distance is more honest than "
        "dropping the cell"
    )
    assert cell.present is True, "the price half exists and must stay readable"
    assert cell.filled is False


def test_an_iso_date_is_read_as_well_as_the_printed_one() -> None:
    assert F.parse_cell("ipo", "1980-12-12").value == date(1980, 12, 12)


def test_a_snapshot_row_may_be_a_pair_instead_of_a_mapping() -> None:
    cells = F.parse_snapshot([("Price", "347.79"), ("Volume", "1,000")])
    assert cells["price"].value == pytest.approx(347.79)
    assert cells["volume"].value == pytest.approx(1000.0)


def test_a_cell_with_more_parts_than_declared_is_unparsed() -> None:
    cell = F.parse_cell("week52_high", "348.36 -0.16% 243.42")
    assert cell.value is None
    assert cell.reason is F.MissingReason.UNPARSED


def test_text_cells_split_on_their_own_separator() -> None:
    assert SNAPSHOT["index"].value == ("DJIA", "NDX", "S&P 500")
    assert SNAPSHOT["option_short"].value == ("Yes", "Yes")
    assert F.parse_cell("option_short", "N/A").value is None


# ---------------------------------------------------------------------------
# Canonical columns: a lookalike is not an alias
# ---------------------------------------------------------------------------


def test_every_canonical_name_is_one_of_the_ninety_two_columns() -> None:
    canonical = {
        metric.key: metric.canonical
        for metric in F.METRICS
        if metric.canonical is not None
    }
    assert canonical, "at least the headline cells must map to a canonical column"
    unknown = {key: value for key, value in canonical.items() if value not in COLUMNS}
    assert unknown == {}, f"mapped to columns outside the v1 contract: {unknown}"


@pytest.mark.parametrize(
    ("key", "canonical"),
    [
        ("price", "last_price"),
        ("prev_close", "prev_close"),
        ("change_pct", "change_pct"),
        ("volume", "day_volume"),
        ("pe", "pe_ttm"),
        ("forward_pe", "pe_forward"),
        ("eps_ttm", "eps_ttm"),
        ("eps_next_y", "eps_forward"),
        ("sales", "revenue_ttm"),
        ("income", "net_income_ttm"),
        ("roe", "roe"),
        ("atr_14", "atr_14"),
        ("rsi_14", "rsi_14"),
        ("perf_week", "ret_1w"),
        ("perf_half_y", "ret_6m"),
        ("perf_year", "ret_1y"),
        ("shares_float", "float_shares"),
        ("ipo", "listing_date"),
        ("index", "index_memberships"),
    ],
)
def test_the_headline_cells_fill_the_canonical_column_they_mean(
    key: str, canonical: str
) -> None:
    assert F.canonical_for(key) == canonical


@pytest.mark.parametrize(
    ("key", "wrong_column", "why"),
    [
        ("sma20", "ma_20", "SMA20 is the distance in percent, not the average"),
        ("sma50", "ma_50", "SMA50 is the distance in percent, not the average"),
        ("sma200", "ma_200", "SMA200 is the distance in percent, not the average"),
        ("week52_high", "day_high", "the 52-week high is not today's high"),
        ("week52_low", "day_low", "the 52-week low is not today's low"),
        ("market_cap", "market_cap_usd", "Finviz prints no currency, so USD is an assumption"),
        ("avg_volume", "avg_volume_20d", "Finviz declares no averaging window"),
        ("lt_debt_to_equity", "debt_to_equity", "long-term debt is a different ratio"),
        ("volatility", "volatility_20d", "week/month volatility is a different measure"),
        ("perf_10y", "ret_10y", "the canonical table stops at ret_5y"),
    ],
)
def test_a_lookalike_is_not_mapped_to_a_different_definition(
    key: str, wrong_column: str, why: str
) -> None:
    assert F.canonical_for(key) != wrong_column, f"{key} → {wrong_column}: {why}"


def test_the_display_units_are_a_closed_observation_vocabulary() -> None:
    allowed = {"price:USD", "pct", "ratio", "shares", "count", "date", "text"}
    for unit in F.Unit:
        assert unit.observation_unit in allowed, unit


# ---------------------------------------------------------------------------
# Google News RSS: parsed from a document, never fetched
# ---------------------------------------------------------------------------


def _item_xml(
    title: str,
    link: str,
    *,
    pub_date: str | None = None,
    source: str | None = None,
) -> str:
    parts = [f"<title>{title}</title>", f"<link>{link}</link>"]
    if pub_date is not None:
        parts.append(f"<pubDate>{pub_date}</pubDate>")
    if source is not None:
        parts.append(f'<source url="https://example.com/{source.lower()}">{source}</source>')
    return "<item>" + "".join(parts) + "</item>"


def _feed(*items: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>'
        "<title>feed</title>" + "".join(items) + "</channel></rss>"
    )


def test_the_sample_feed_yields_at_least_three_usable_headlines() -> None:
    items = rss.parse_google_news(NEWS_FIXTURE.read_text(encoding="utf-8"))
    assert len(items) == 5, "the fixture carries five items; every one is readable"
    assert all(item.headline and item.url for item in items)


def test_headline_source_timestamp_and_url_are_separated() -> None:
    first = rss.parse_google_news(NEWS_FIXTURE.read_text(encoding="utf-8"))[0]
    assert first.headline == "Nvidia hits record high on AI demand"
    assert first.source == "Reuters"
    assert first.source_url == "https://www.reuters.com"
    assert first.published_at == datetime(2026, 10, 21, 14, 5, tzinfo=UTC), (
        "the timestamp is the article's, in UTC"
    )
    assert first.url == "https://news.google.com/rss/articles/CBMiA1RV?oc=5"


def test_a_title_entity_is_unescaped() -> None:
    second = rss.parse_google_news(NEWS_FIXTURE.read_text(encoding="utf-8"))[1]
    assert second.headline == "Nvidia's new chip roadmap leaks"
    assert second.source == "The Verge"


def test_the_publisher_suffix_is_stripped_only_when_it_matches_the_source() -> None:
    document = _feed(
        _item_xml("Chip rally continues - CNBC", "https://e.test/1", source="CNBC"),
        _item_xml("Chip rally continues - CNBC", "https://e.test/2"),
    )
    with_source, without_source = rss.parse_google_news(document)

    assert with_source.headline == "Chip rally continues"
    assert without_source.headline == "Chip rally continues - CNBC", (
        "with no <source> element the suffix is the only publisher evidence; "
        "guessing it away would lose the source entirely"
    )
    assert without_source.source == "", "no <source> means no publisher, not a guess"


def test_an_undated_item_has_no_timestamp() -> None:
    items = rss.parse_google_news(NEWS_FIXTURE.read_text(encoding="utf-8"))
    undated = [item for item in items if item.published_at is None]
    assert len(undated) == 2, "one item has no <pubDate>, one has an unreadable one"
    assert all(item.headline for item in undated), "an undated item is still a headline"


def test_the_default_limit_is_eight_and_applies_to_usable_items() -> None:
    assert rss.DEFAULT_LIMIT == 8, "Finviz shows eight headlines per ticker"
    document = _feed(
        *[
            _item_xml(f"Headline {index}", f"https://e.test/{index}")
            for index in range(12)
        ]
    )
    assert len(rss.parse_google_news(document)) == 8
    assert len(rss.parse_google_news(document, limit=3)) == 3
    assert rss.parse_google_news(document, limit=None) == rss.parse_google_news(
        document, limit=12
    )


def test_a_repeated_url_is_one_headline() -> None:
    document = _feed(
        _item_xml("Same story", "https://e.test/same"),
        _item_xml("Same story again", "https://e.test/same"),
    )
    assert len(rss.parse_google_news(document)) == 1


def test_an_item_without_a_url_is_not_a_row() -> None:
    document = _feed("<item><title>No link</title></item>")
    assert rss.parse_google_news(document) == []


def test_a_broken_document_is_not_silently_empty() -> None:
    with pytest.raises(rss.GoogleNewsError):
        rss.parse_google_news("<rss><channel>")
    with pytest.raises(rss.GoogleNewsError):
        rss.parse_google_news("https://news.google.com/rss/search?q=AAPL")


def test_an_empty_channel_is_an_empty_list() -> None:
    assert rss.parse_google_news(_feed()) == []


def test_a_negative_limit_is_a_caller_error() -> None:
    with pytest.raises(ValueError, match="limit"):
        rss.parse_google_news(_feed(), limit=-1)


def test_feed_url_encodes_the_query_without_a_request() -> None:
    url = rss.feed_url("Nvidia stock")
    assert url.startswith("https://news.google.com/rss/search?q=")
    assert url == (
        "https://news.google.com/rss/search?q=Nvidia%20stock"
        "&hl=en-US&gl=US&ceid=US:en"
    ), "the query is percent-encoded; ':' stays literal so ceid keeps its spelling"
    assert " " not in url
    with pytest.raises(ValueError, match="blank"):
        rss.feed_url("   ")


def test_a_news_item_serialises_to_json_shaped_values() -> None:
    item = rss.parse_google_news(NEWS_FIXTURE.read_text(encoding="utf-8"))[0]
    payload: dict[str, Any] = item.as_dict()
    assert payload["published_at"] == "2026-10-21T14:05:00+00:00"
    undated = rss.parse_google_news(NEWS_FIXTURE.read_text(encoding="utf-8"))[3]
    assert undated.as_dict()["published_at"] is None
