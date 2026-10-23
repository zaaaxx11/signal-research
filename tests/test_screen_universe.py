"""Screen S0 RED — the universe: filter, cross-listing dedupe, tabs, stable rank.

Gate (PLAN-screen.md §2, §8):

* ``type=stock`` + ``subtype=common`` only — an ETF, a REIT, a warrant and a
  preferred are not part of a default equity screen;
* market cap at least USD 100 million, boundary included;
* OTC/pink-sheet listings never enter the universe;
* one issuer is one row: the NVDA listings on NASDAQ, SIX, BX, MUN and
  TRADEGATE collapse into the primary/most-liquid one;
* ``us`` holds US listings only, ``global`` holds ex-US issuers only, and the
  two tabs share no symbol;
* ranking is transparent (market cap, turnover, change) and stable: the same
  input yields the same order, and ties keep the input order.
"""

from __future__ import annotations

from typing import Any

from tests import _screen as S


def _universe(region: str) -> list[dict[str, Any]]:
    return S.universe().build_universe(S.scan_rows(region), region=region)


def _clone(region: str, source: str, **overrides: Any) -> dict[str, Any]:
    row = dict(S.fixture_row(region, source))
    row.update(overrides)
    return row


# ---------------------------------------------------------------------------
# Filter
# ---------------------------------------------------------------------------


def test_us_universe_keeps_only_common_stock() -> None:
    rows = _universe("us")
    symbols = {row["symbol"] for row in rows}

    for excluded in ("SPY", "VNQ", "O", "WAR", "PFD"):
        assert excluded not in symbols, (
            f"{excluded} is not type=stock/subtype=common and must not be screened; "
            f"the universe is {sorted(symbols)}"
        )
    assert "XYZQ" not in symbols, "an OTC listing must never reach the universe"
    assert "TINY" not in symbols, "a USD 99,999,999 company is below the floor"


def test_market_cap_floor_is_inclusive_at_one_hundred_million() -> None:
    build = S.universe().build_universe
    floor = S.universe().MIN_MARKET_CAP_USD
    assert floor == S.MIN_MARKET_CAP_USD == 100_000_000

    at_floor = build(
        [_clone("us", "AAPL", symbol="ATFLOOR", market_cap_basic=100_000_000)],
        region="us",
    )
    assert [row["symbol"] for row in at_floor] == ["ATFLOOR"], (
        "a company worth exactly USD 100,000,000 is inside the floor; the filter "
        "must use >= not >"
    )

    below = build(
        [_clone("us", "AAPL", symbol="BELOW", market_cap_basic=99_999_999)],
        region="us",
    )
    assert below == [], f"a company one dollar below the floor must be dropped: {below!r}"


def test_otc_listing_is_excluded_even_when_it_is_large() -> None:
    rows = S.universe().build_universe(
        [_clone("us", "AAPL", symbol="PINKY", exchange="OTC", market_cap_basic=2_000_000_000)],
        region="us",
    )
    assert rows == [], "OTC/pink sheet is out of scope (PLAN-screen.md §2)"


# ---------------------------------------------------------------------------
# Dedupe
# ---------------------------------------------------------------------------


def test_cross_listed_issuer_collapses_to_one_primary_row() -> None:
    rows = _universe("us")
    nvda = [row for row in rows if row["symbol"] == "NVDA"]

    assert len(nvda) == 1, (
        "NVDA is listed on NASDAQ, SIX, BX, MUN and TRADEGATE; the universe must "
        f"carry one issuer row, got {[r['exchange'] for r in nvda]}"
    )
    assert nvda[0]["exchange"] == "XNAS", (
        "the US listing is the primary/most-liquid one for a US issuer, got "
        f"{nvda[0]['exchange']!r}"
    )
    assert nvda[0]["is_primary"] is True
    assert nvda[0]["market_cap_usd"] == 4_500_000_000_000


def test_universe_is_smaller_than_the_raw_provider_answer() -> None:
    rows = _universe("us")
    assert len(rows) == S.US_TOTAL, (
        f"expected {S.US_TOTAL} issuers after filter+dedupe, got {len(rows)}"
    )
    assert len(rows) < len(S.scan_rows("us")) == S.US_RAW, (
        "the raw scanner body contains duplicates and non-common instruments; a "
        "universe equal to the raw count means neither filter ran"
    )


def test_no_issuer_appears_twice_under_different_names() -> None:
    rows = _universe("us")
    names = [str(row["name"]).casefold() for row in rows]
    assert len(set(names)) == len(names), (
        "two rows share an issuer description after dedupe: "
        f"{sorted({name for name in names if names.count(name) > 1})}"
    )


# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------


def test_us_tab_contains_us_listings_only() -> None:
    rows = _universe("us")
    foreign = [row for row in rows if row["exchange"] not in S.US_EXCHANGES]
    assert foreign == [], (
        "the US tab may only carry XNYS/XNAS/XASE listings, got "
        f"{[(r['symbol'], r['exchange']) for r in foreign]}"
    )


def test_global_tab_is_ex_us_and_drops_issuers_the_us_tab_owns() -> None:
    rows = _universe("global")
    symbols = {row["symbol"] for row in rows}

    assert len(rows) == S.GLOBAL_TOTAL, (
        f"expected {S.GLOBAL_TOTAL} ex-US issuers, got {len(rows)}"
    )
    for us_issuer in ("NVDA", "ASML", "SAP"):
        assert us_issuer not in symbols, (
            f"{us_issuer} has a US listing, so it belongs to the US tab; a Global "
            "row here would duplicate the same issuer across the two tabs"
        )
    assert not {row["exchange"] for row in rows} & S.US_EXCHANGES
    assert not {row["exchange"] for row in rows} & S.OTC_EXCHANGES


def test_global_keeps_the_most_liquid_listing_of_a_non_us_issuer() -> None:
    rows = _universe("global")
    nesn = [row for row in rows if row["symbol"] == "NESN"]
    assert len(nesn) == 1, f"Nestle appears on SIX and BX; got {len(nesn)} rows"
    assert nesn[0]["exchange"] == "SIX", (
        "the SIX listing trades more than the Berlin one; dedupe must keep the "
        f"most liquid, got {nesn[0]['exchange']!r}"
    )


def test_the_two_tabs_do_not_share_a_symbol() -> None:
    us = {row["symbol"] for row in _universe("us")}
    glob = {row["symbol"] for row in _universe("global")}
    assert us & glob == set(), (
        "US and Global must not serve the same listing: " f"{sorted(us & glob)}"
    )


# ---------------------------------------------------------------------------
# Rank
# ---------------------------------------------------------------------------


def test_ranking_is_stable_across_runs() -> None:
    first = _universe("us")
    second = _universe("us")
    assert [(row["symbol"], row["exchange"]) for row in first] == [
        (row["symbol"], row["exchange"]) for row in second
    ], "the same fixture produced two different universes; rank would drift between requests"


def test_rank_is_contiguous_and_keeps_input_order_for_ties() -> None:
    rows = [
        {"symbol": "TIE-A", "market_cap_usd": 200.0, "turnover": 10.0, "change_pct": 0.5},
        {"symbol": "TIE-B", "market_cap_usd": 200.0, "turnover": 10.0, "change_pct": 0.5},
        {"symbol": "BIG", "market_cap_usd": 300.0, "turnover": 5.0, "change_pct": 0.1},
    ]
    ranked = S.rank().rank_rows(rows, "market_cap")

    assert [row["symbol"] for row in ranked] == ["BIG", "TIE-A", "TIE-B"], (
        "equal market caps must keep the input order (stable sort), got "
        f"{[row['symbol'] for row in ranked]}"
    )
    assert [row["rank"] for row in ranked] == [1, 2, 3]
    assert S.rank().rank_rows(rows, "market_cap") == ranked, "rank_rows is not deterministic"


def test_each_sort_orders_by_its_own_transparent_metric() -> None:
    ranked = {sort: S.rank().rank_rows(_universe("us"), sort) for sort in S.SORTS}

    assert [row["symbol"] for row in ranked["market_cap"][:3]] == ["AAPL", "NVDA", "MSFT"]
    assert [row["symbol"] for row in ranked["turnover"][:3]] == ["NVDA", "AAPL", "TSLA"]
    assert [row["symbol"] for row in ranked["gainers"][:3]] == ["NVDA", "ASML", "AAPL"]

    for sort, key in (
        ("market_cap", "market_cap_usd"),
        ("turnover", "turnover"),
        ("gainers", "change_pct"),
    ):
        values = [row[key] for row in ranked[sort]]
        assert values == sorted(values, reverse=True), (
            f"sort={sort} is not descending on {key}: {values[:5]}..."
        )
        assert [row["rank"] for row in ranked[sort]] == list(range(1, len(values) + 1))


def test_turnover_usd_is_never_invented_without_fx() -> None:
    rows = _universe("global")
    foreign = [row for row in rows if row["currency"] != "USD"]
    assert foreign, "the global fixture must contain non-USD listings"

    for row in foreign:
        assert row["turnover_usd"] is None, (
            f"{row['symbol']} trades in {row['currency']}; turnover_usd must stay null "
            "until an FX rate exists, never 0 and never the local value relabelled"
        )
        assert isinstance(row["turnover"], (int, float)) and row["turnover"] > 0
