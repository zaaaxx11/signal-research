"""The Fundamental, Technical and News bucket families (plan addendum T16–T23).

The Descriptive groups (``tests/test_screen_buckets.py``) pinned one shape —
``token`` → one column comparison. These groups are harder, and every hard case
has a test here:

* ``u``/``o`` mean *under*/*over* for a multiple and **up**/**down** for a
  change (``gap:u0`` is ``gap_pct >= 0``, not ``< 0``);
* a control may carry a **negative** threshold (``grossmargin:u-50``);
* a *distance* control stores a **level** (``price`` vs ``sma_20``), so one
  token reads two cells and an implausible level matches nothing;
* a token may need **two** cells (``epsrev:bp``), a **window** and a threshold
  (``divgrowth:3yo10``), or a **clock** (``news:today`` is New York's today);
* a token Finviz draws may be refused **by name** — an unavailable bucket that
  is a 422, never a filter that quietly matches nothing.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.screen import buckets, choice_filters, filter_fields

ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "research" / "finviz-ui-raw" / "views" / "v111-filters.json"

#: Late enough in UTC that New York is still on the previous day — the one case
#: a UTC-only clock gets wrong. 2026-11-01 is also the day US DST ends, so the
#: offset here is EST (-05:00), not EDT: the answer must not depend on which.
AS_OF = "2026-11-02T02:30:00+00:00"


def _matches(token: str, row: dict[str, Any], *, as_of: str = AS_OF) -> bool:
    """Run one bucket id over one row exactly as ``apply_bucket_filters`` will."""

    found = buckets.bucket(token)
    return found.predicate(row, buckets.Context.at(as_of))


def _matches_at(token: str, row: dict[str, Any], moment: str) -> bool:
    return _matches(token, row, as_of=moment)


def _ids(*controls: str) -> int:
    """The id count over a list of controls, read through their keys."""

    return sum(len(buckets.for_key(buckets.CONTROL_KEYS[control])) for control in controls)


# ---------------------------------------------------------------------------
# T16 — the band parser, the band table, the family builder
# ---------------------------------------------------------------------------


def test_band_reads_a_negative_threshold() -> None:
    assert buckets._band("u-50") == ("lt", -50.0)
    assert buckets._band("u-5") == ("lt", -5.0)
    assert buckets._band("o-10") == ("gt", -10.0)


def test_band_still_reads_every_descriptive_token() -> None:
    # The 207 ids that shipped before these groups must not move.
    assert buckets._band("u5") == ("lt", 5.0)
    assert buckets._band("o1.5") == ("gt", 1.5)
    assert buckets._band("0to5") == ("between", (0.0, 5.0))
    assert buckets._band("1to5") == ("between", (1.0, 5.0))


def test_a_family_covers_every_probe_option_in_order() -> None:
    family = buckets._family("pe", "fa_pe", "pe_ttm", bands=buckets._BANDS["fa_pe"])
    assert [item.finviz for item in family] == list(buckets._tokens("fa_pe"))
    assert len(family) == 23
    assert [item.id for item in family][:3] == ["pe:low", "pe:profitable", "pe:high"]


def test_a_family_token_without_a_band_is_an_import_error() -> None:
    with pytest.raises(RuntimeError, match="has no band and no entry in _BANDS"):
        buckets._family("pe", "fa_pe", "pe_ttm", bands={})  # drops low/profitable/high


def test_every_control_id_is_unique_and_starts_with_its_namespace() -> None:
    ids = [item.id for key in buckets.KEYS for item in buckets.for_key(key)]
    assert len(ids) == len(set(ids))
    assert all(
        item.id.split(":")[0] == buckets.control_for(item.key)
        for key in buckets.KEYS
        for item in buckets.for_key(key)
    )
    assert len(set(buckets._CONTROL_KEYS.values())) == len(buckets._CONTROL_KEYS), (
        "two controls naming one key would publish two families under one namespace"
    )


def test_every_published_label_comes_from_the_probe_not_a_hand_typed_string() -> None:
    """The vendored labels are Finviz's own; a retyped one drifts on relabel."""

    probe = json.loads(PROBE.read_text(encoding="utf-8"))
    for key in buckets.KEYS:
        options = {
            option["value"]: option["label"]
            for option in probe[key]["options"]
            if option["value"] not in ("", "frange", "modal")
        }
        for token in buckets._tokens(key):
            assert buckets._label(key, token) == options[token], (key, token)


# ---------------------------------------------------------------------------
# T17 — valuation: nine multiples, 204 ids
# ---------------------------------------------------------------------------


def test_pe_low_is_under_15_and_high_is_over_50() -> None:
    assert _matches("pe:low", {"pe_ttm": 12.0})
    assert not _matches("pe:low", {"pe_ttm": 15.0})
    assert _matches("pe:high", {"pe_ttm": 51.0})
    assert not _matches("pe:high", {"pe_ttm": 50.0})
    assert _matches("pe:u15", {"pe_ttm": 14.9})


def test_ev_ebitda_negative_bucket_is_below_zero() -> None:
    assert _matches("evebitda:negative", {"ev_ebitda": -3.0})
    assert not _matches("evebitda:negative", {"ev_ebitda": 0.0})


def test_ev_sales_positive_is_strictly_over_zero() -> None:
    assert _matches("evsales:positive", {"ev_to_sales": 0.01})
    assert not _matches("evsales:positive", {"ev_to_sales": 0.0})
    assert _matches("evsales:negative", {"ev_to_sales": -3.0})


def test_price_to_cash_reads_the_screen_field_not_the_depth_spelling() -> None:
    assert _matches("pc:u10", {"price_to_cash": 8.0})
    assert not _matches("pc:u10", {"pc": 8.0}), "the store's spelling is not the row field"


def test_a_null_multiple_matches_no_bucket() -> None:
    for token in ("low", "high", "u5"):
        assert not _matches(f"pe:{token}", {"pe_ttm": None})


def test_the_nine_valuation_controls_are_registered_with_204_ids() -> None:
    assert _ids("pe", "fpe", "peg", "ps", "pb", "pc", "pfcf", "evebitda", "evsales") == 204


# ---------------------------------------------------------------------------
# T18 — growth: eleven EPS/sales keys, one shared vocabulary, 176 ids
# ---------------------------------------------------------------------------


def test_eps_growth_poslow_is_the_zero_to_ten_band() -> None:
    assert _matches("epsyoy:poslow", {"eps_this_y": 0.0})
    assert _matches("epsyoy:poslow", {"eps_this_y": 10.0})
    assert not _matches("epsyoy:poslow", {"eps_this_y": 10.01})
    assert not _matches("epsyoy:poslow", {"eps_this_y": -1.0})


def test_estltgrowth_reads_the_next_five_year_rate_not_the_forward_eps_level() -> None:
    # eps_forecast_next_fy is a LEVEL (4.20 = $4.20); eps_next_5y is a percent.
    assert _matches("epsnext5y:o10", {"eps_next_5y": 12.0})
    assert not _matches(
        "epsnext5y:o10", {"eps_forecast_next_fy": 12.0, "eps_next_5y": None}
    )


def test_a_growth_bucket_reads_percent_not_fraction() -> None:
    assert _matches("epsyoy:o10", {"eps_this_y": 18.52})  # AAPL
    assert not _matches("epsyoy:o10", {"eps_this_y": 0.1852})  # the same as a fraction


def test_every_growth_key_shares_one_vocabulary() -> None:
    keys = (
        "fa_epsyoy",
        "fa_epsyoy1",
        "fa_epsqoq",
        "fa_epsyoyttm",
        "fa_eps3years",
        "fa_eps5years",
        "fa_estltgrowth",
        "fa_salesqoq",
        "fa_salesyoyttm",
        "fa_sales3years",
        "fa_sales5years",
    )
    assert {tuple(item.finviz for item in buckets.for_key(key)) for key in keys} == {
        tuple(buckets._tokens("fa_epsyoy"))
    }
    assert sum(len(buckets.for_key(key)) for key in keys) == 176


# ---------------------------------------------------------------------------
# T19 — quality, leverage, ownership, dividend growth: 380 ids, 41 refused
# ---------------------------------------------------------------------------


def test_verypos_thresholds_are_per_control_not_shared() -> None:
    # ROE's Very Positive is >30; ROA's is >15; ROIC's is >25.
    assert _matches("roe:verypos", {"roe": 31.0})
    assert not _matches("roe:verypos", {"roe": 25.0})
    assert _matches("roa:verypos", {"roa": 16.0})
    assert not _matches("roa:verypos", {"roa": 14.0})
    assert _matches("roi:verypos", {"roic": 26.0})
    assert not _matches("roi:verypos", {"roic": 20.0})


def test_dividend_growth_year_token_picks_the_window() -> None:
    assert _matches("divgrowth:1yo5", {"dividend_growth_1y": 6.0})
    assert not _matches("divgrowth:1yo5", {"dividend_growth_1y": 4.0})
    assert _matches("divgrowth:3yo10", {"dividend_growth_3y": 11.0})
    assert not _matches("divgrowth:3yo10", {"dividend_growth_5y": 11.0})
    assert _matches("divgrowth:5ypos", {"dividend_growth_5y": 0.5})


def test_a_control_that_has_no_column_for_a_token_refuses_it_by_name() -> None:
    with pytest.raises(buckets.UnavailableBucket, match="no series"):
        buckets.bucket("divgrowth:cy9")
    assert len(buckets.for_key("fa_divgrowth")) == 30, "a refusal is a bucket, not an omission"


def test_insider_transactions_are_refused_with_the_quality_reason() -> None:
    with pytest.raises(
        buckets.UnavailableBucket, match="mixture of ownership levels and junk"
    ):
        buckets.bucket("insidertrans:pos")
    assert "sh_insidertrans" in filter_fields.unavailable_keys()


def test_negative_margin_bands_compile_after_the_band_fix() -> None:
    assert _matches("grossmargin:u-50", {"gross_margin": -60.0})
    assert not _matches("grossmargin:u-50", {"gross_margin": -40.0})
    assert _matches("opermargin:u-20", {"oper_margin": -25.0})
    assert _matches("netmargin:veryneg", {"profit_margin": -21.0})
    assert not _matches("netmargin:veryneg", {"profit_margin": -19.0})


def test_the_quality_and_ownership_controls_are_registered_with_380_ids() -> None:
    """Fifteen controls (T20 adds ``ltdebteq``'s 22 ids beside its field)."""

    assert (
        _ids(
            "divgrowth",
            "roa",
            "roe",
            "roi",
            "curratio",
            "quickratio",
            "debteq",
            "grossmargin",
            "opermargin",
            "netmargin",
            "payout",
            "insiderown",
            "insidertrans",
            "instown",
            "insttrans",
        )
        == 380
    )
    refused = [
        item
        for control in ("divgrowth", "insidertrans")
        for item in buckets.for_key(buckets.CONTROL_KEYS[control])
        if not item.available
    ]
    assert len(refused) == 41, "9 dividend windows + 32 insider-transaction tokens"


# ---------------------------------------------------------------------------
# T21 — technical price and oscillator families: 6 controls, 135 ids
# ---------------------------------------------------------------------------


def test_gap_u_means_up_and_d_means_down() -> None:
    assert _matches("gap:u2", {"gap_pct": 2.5})
    assert not _matches("gap:u2", {"gap_pct": -2.5})
    assert _matches("gap:d2", {"gap_pct": -2.5})
    assert not _matches("gap:d2", {"gap_pct": 2.5})
    assert _matches("change:d20", {"change_pct": -25.0})


def test_gap_u0_is_not_under_zero() -> None:
    # The trap: _band("u0") is ("lt", 0.0); "Up 0%" is >= 0.
    assert _matches("gap:u0", {"gap_pct": 0.0})
    assert not _matches("gap:u0", {"gap_pct": -0.1})
    assert _matches("gap:d0", {"gap_pct": 0.0})
    assert not _matches("gap:d0", {"gap_pct": 0.1})


def test_rsi_overbought_synonym_resolves_to_the_canonical_id() -> None:
    assert buckets.bucket("rsi:overbought").id == "rsi:ob70"
    assert buckets.bucket("rsi:oversold").id == "rsi:os30"
    assert _matches("rsi:overbought", {"rsi_14": 71.0})
    assert not _matches("rsi:ob70", {"rsi_14": 69.9})
    assert _matches("rsi:os30", {"rsi_14": 29.0})


def test_rsi_not_overbought_is_lt_not_lte() -> None:
    assert _matches("rsi:nob60", {"rsi_14": 59.9})
    assert not _matches("rsi:nob60", {"rsi_14": 60.0})
    assert _matches("rsi:nos50", {"rsi_14": 50.1})
    assert not _matches("rsi:nos50", {"rsi_14": 50.0})


def test_atr_tokens_are_price_units() -> None:
    assert _matches("atr:o5", {"atr_14": 5.5})
    assert not _matches("atr:o5", {"atr_14": 4.9})
    assert _matches("atr:u0.25", {"atr_14": 0.1})


def test_the_six_technical_price_controls_are_registered_with_135_ids() -> None:
    assert _ids("rsi", "gap", "change", "changeopen", "beta", "atr") == 135


def test_the_three_year_performance_tokens_compile_against_ret_3y() -> None:
    assert "3yup" in choice_filters.choices_for("ta_perf")
    assert choice_filters.UNSUPPORTED["ta_perf"] == {}
    assert choice_filters.compile_option("ta_perf", "3y10u").value == -10.0
    assert choice_filters.compile_option("ta_perf2", "3y1000o").value == 1000.0


# ---------------------------------------------------------------------------
# T22 — technical trend and range families: 7 controls, 189 ids, 38 refused
# ---------------------------------------------------------------------------


def test_sma_distance_is_price_over_sma_minus_one() -> None:
    assert _matches("sma20:pa10", {"price": 110.0, "sma_20": 100.0})
    assert not _matches("sma20:pa10", {"price": 109.0, "sma_20": 100.0})
    assert _matches("sma20:pb10", {"price": 90.0, "sma_20": 100.0})
    assert _matches("sma20:pa", {"price": 100.5, "sma_20": 100.0})
    assert _matches("sma20:pb", {"price": 99.5, "sma_20": 100.0})


def test_sma_above_is_a_two_column_comparison() -> None:
    assert _matches("sma20:sa50", {"sma_20": 101.0, "sma_50": 100.0})
    assert not _matches("sma20:sa50", {"sma_20": 99.0, "sma_50": 100.0})
    assert _matches("sma20:sb50", {"sma_20": 99.0, "sma_50": 100.0})
    assert _matches("sma200:sa50", {"sma_200": 101.0, "sma_50": 100.0})


def test_a_cross_token_is_refused_by_name_not_by_zero_rows() -> None:
    with pytest.raises(buckets.UnavailableBucket, match="cross"):
        buckets.bucket("sma20:cross50a")
    with pytest.raises(buckets.UnavailableBucket, match="cross"):
        buckets.bucket("sma200:pc")
    assert len(buckets.for_key("ta_sma20")) == 25, "a refusal is a bucket, not an omission"


def test_a5h_is_above_the_low_not_below_the_high() -> None:
    row = {"price": 55.0, "high_50d": 60.0, "low_50d": 50.0}
    assert _matches("highlow50d:a10h", row)  # 10% above the low
    assert not _matches("highlow50d:b10h", row)  # only 8.3% below the high
    assert _matches("highlow50d:b5h", row)
    assert _matches("highlow50d:b0to10h", row)
    assert not _matches("highlow50d:a0to5h", row)


def test_new_high_is_within_a_tick_of_the_level() -> None:
    assert _matches("highlow52w:nh", {"price": 99.95, "high_52w": 100.0})
    assert not _matches("highlow52w:nh", {"price": 99.0, "high_52w": 100.0})
    assert _matches("highlow52w:nl", {"price": 50.05, "low_52w": 50.0})
    assert not _matches("highlow52w:nl", {"price": 51.0, "low_52w": 50.0})


def test_an_implausible_level_matches_nothing() -> None:
    # 24 rows carry high_50d > 10x price; a distance over one is not a measurement.
    assert not _matches("highlow50d:b10h", {"price": 1.0, "high_50d": 1250.0})
    assert not _matches("highlow50d:nh", {"price": 1.0, "high_50d": 1250.0})


def test_all_time_new_high_is_refused_while_the_column_is_unreliable() -> None:
    with pytest.raises(buckets.UnavailableBucket, match="52-week high"):
        buckets.bucket("alltime:nh")
    with pytest.raises(buckets.UnavailableBucket, match="52-week high"):
        buckets.bucket("alltime:nl")
    assert _matches("alltime:b10h", {"price": 90.0, "all_time_high": 100.0})


def test_candlestick_matches_only_the_two_classes_we_can_name() -> None:
    assert _matches("candle:h", {"candlestick_pattern": "hammer"})
    assert _matches("candle:d", {"candlestick_pattern": "doji"})
    assert not _matches("candle:h", {"candlestick_pattern": "doji"})
    assert not _matches("candle:h", {"candlestick_pattern": None})
    with pytest.raises(buckets.UnavailableBucket, match="no classifier"):
        buckets.bucket("candle:lls")


def test_the_trend_and_range_controls_are_registered_with_178_ids() -> None:
    assert _ids("sma20", "sma50", "sma200", "highlow50d", "highlow52w", "alltime") == 178
    assert _ids("candle") == 11
    refused = {
        control: sum(
            1
            for item in buckets.for_key(buckets.CONTROL_KEYS[control])
            if not item.available
        )
        for control in ("sma20", "sma50", "sma200", "alltime", "candle")
    }
    assert refused == {
        "sma20": 9,
        "sma50": 9,
        "sma200": 9,
        "alltime": 2,
        "candle": 9,
    }, "27 cross events, 2 all-time new highs, 9 unclassified candlestick shapes"


# ---------------------------------------------------------------------------
# T23 — the news family and the clock: 1 control, 12 ids, 3 refused
# ---------------------------------------------------------------------------


def test_news_today_is_new_york_today() -> None:
    at = "2026-11-02T02:30:00+00:00"  # 2026-11-01 in New York
    assert _matches_at("news:today", {"news_time": "2026-11-01T18:00:00+00:00"}, at)
    assert not _matches_at("news:today", {"news_time": "2026-11-02T14:00:00+00:00"}, at)


def test_news_todayafter_needs_a_stamp_after_the_close() -> None:
    at = "2026-11-02T02:30:00+00:00"
    assert _matches_at(
        "news:todayafter", {"news_time": "2026-11-02T01:30:00+00:00"}, at
    ), "20:30 New York, after the close"
    assert not _matches_at(
        "news:todayafter", {"news_time": "2026-11-01T15:00:00+00:00"}, at
    ), "10:00 New York, before the close"


def test_news_windows_are_relative_to_the_answers_own_moment() -> None:
    at = "2026-11-02T02:30:00+00:00"
    fresh = {"news_time": "2026-11-01T20:00:00+00:00"}
    stale = {"news_time": "2026-10-20T20:00:00+00:00"}
    assert _matches_at("news:prevhours24", fresh, at)
    assert not _matches_at("news:prevhours24", stale, at)
    assert _matches_at("news:prevdays7", fresh, at)
    assert not _matches_at("news:prevdays7", stale, at)
    assert _matches_at("news:prevmonth", {"news_time": "2026-10-10T00:00:00+00:00"}, at)
    assert not _matches_at("news:prevmonth", {"news_time": "2026-09-01T00:00:00+00:00"}, at)


def test_a_minute_window_is_refused_with_the_collector_cadence() -> None:
    with pytest.raises(buckets.UnavailableBucket, match="13 minutes"):
        buckets.bucket("news:prevminutes5")
    with pytest.raises(buckets.UnavailableBucket, match="13 minutes"):
        buckets.bucket("news:prevhours1")
    assert len(buckets.for_key("news_date")) == 12


def test_a_clock_dependent_answer_carries_its_day() -> None:
    from app.api.screens import _data_revision

    revision = _data_revision(
        snapshot_id=None,
        snapshot_as_of=None,
        depth="depth-1",
        scan=7,
        fundamentals="3:2026-11-01",
        facts="2:2026-11-01",
        brand="brand-1",
        members="2026-11-01:503,101,30,1881",
        clock="2026-11-01",
    )
    assert "clock=2026-11-01" in revision


def test_two_days_are_two_etags() -> None:
    from app.api.screens import _data_revision, _etag
    from app.screen.selection import parse_selection

    request = parse_selection(
        {"mode": "criteria", "region": "us", "criteria": {"limit": 5}}
    )

    def tag(clock: str) -> str:
        return _etag(
            revision=_data_revision(
                snapshot_id=None,
                snapshot_as_of=None,
                depth="depth-1",
                scan=7,
                fundamentals="3:2026-11-01",
                facts="2:2026-11-01",
                brand="brand-1",
                members="2026-11-01:503,101,30,1881",
                clock=clock,
            ),
            request=request,
        )

    assert tag("2026-11-01") != tag("2026-11-02")
