"""Fase 6 — the Yahoo adapter must serve all four capabilities it declares.

Read this suite as the specification the adapter is measured against, not as a
description of what it currently does. Four groups:

* **Capabilities.** ``QUOTE``, ``EOD_BARS``, ``FUNDAMENTALS`` and ``OPTIONS`` are
  declared, because ``REGISTRY`` lists yahoo as an ``L1`` rung for each of them
  and a rung whose adapter raises "does not implement" is a rung that never
  answers.
* **EOD.** One 5y daily chart body yields the 12 EOD/technical and 9
  performance cells, matching :func:`tests._wiring.eod_oracle` — an independent
  recomputation from the same bars (MA/EMA/RSI/ATR/volatility/returns), not a
  second call into the parser.
* **Fundamentals.** One ``quoteSummary`` body fills 22 fundamentals/statements
  cells; ``dividend_yield``/``payout_ratio``/``roe`` are percent points (V6) and
  ``market_cap_usd`` is never invented from a missing FX rate.
* **OPTIONS.** The chain resolves the expiry nearest 30 DTE from the chain's own
  ``quote.regularMarketTime`` (never the wall clock), then the ATM call: IV as a
  fraction (V7) and finite Greeks with the V8 signs. Yahoo publishes no Greeks,
  so the adapter computes them.

The last test runs ``_fetch_live`` — the production live path — with a request
that asks for all four capabilities. Phase 2 wired the QUOTE ladder only and
raised ``JobError("... implements the QUOTE capability only ...")``; that refusal
is what the wiring removes.
"""

from __future__ import annotations

import math
from typing import Any

import pytest

from app.contracts import CANONICAL_COLUMNS_V1, Capability
from app.providers.base import InstrumentRef
from tests import _wiring as W

INSTRUMENT = InstrumentRef.from_symbol("AAPL", venue="XNAS")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def adapter() -> Any:
    """A fresh adapter per test: no shared state, no sockets until ``fetch``."""

    return W.yahoo_module().YahooAdapter()


def _parsed(capability: Capability, fixture: dict[str, Any]) -> dict[str, Any]:
    """Parse one fixture through the adapter and return ``{field: value}``."""

    instance = adapter()
    raw = W.raw_body(fixture, capability.value)
    return W.observations(instance.parse(raw, INSTRUMENT))


def _missing(values: dict[str, Any], columns: tuple[str, ...]) -> list[str]:
    return [column for column in columns if column not in values]


def _close(actual: Any, expected: float, column: str) -> None:
    assert isinstance(actual, (int, float)) and not isinstance(actual, bool), (
        f"{column}: expected a number, got {actual!r}"
    )
    assert actual == pytest.approx(expected, rel=1e-9, abs=1e-9), (
        f"{column}: adapter produced {actual!r}, the independent oracle says {expected!r}"
    )


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------


def test_adapter_declares_quote_eod_fundamentals_and_options() -> None:
    declared = set(adapter().capabilities)
    expected = {
        Capability.QUOTE,
        Capability.EOD_BARS,
        Capability.FUNDAMENTALS,
        Capability.OPTIONS,
    }
    assert expected <= declared, (
        "REGISTRY lists yahoo as a rung for quote/eod_bars/fundamentals/options, but the "
        f"adapter declares only {sorted(c.value for c in declared)}; an undeclared "
        "capability makes HttpAdapter.fetch raise 'does not implement' for a rung that "
        "is supposed to answer"
    )


# ---------------------------------------------------------------------------
# EOD bars and technicals
# ---------------------------------------------------------------------------


def test_eod_parse_reports_bars_and_technicals() -> None:
    fixture = W.chart_5y()
    oracle = W.eod_oracle(fixture)
    values = _parsed(Capability.EOD_BARS, fixture)

    missing = _missing(values, W.EOD_COLUMNS)
    assert not missing, (
        "the EOD parse left these cells empty: "
        f"{missing}; it produced {sorted(values)} — a chart body with 5y of daily bars "
        "is enough for every one of them"
    )
    assert set(values) <= set(CANONICAL_COLUMNS_V1), (
        "the parser emitted a non-canonical field: "
        f"{sorted(set(values) - set(CANONICAL_COLUMNS_V1))}"
    )

    assert values["bars_count"] == oracle["bars_count"], (
        f"bars_count: adapter {values['bars_count']!r} vs oracle {oracle['bars_count']!r}"
    )
    assert str(values["first_bar_date"])[:10] == oracle["first_bar_date"]
    assert str(values["last_bar_date"])[:10] == oracle["last_bar_date"]

    for column in (
        "ma_20",
        "ma_50",
        "ma_200",
        "ema_12",
        "ema_26",
        "rsi_14",
        "atr_14",
        "volatility_20d",
        "avg_volume_20d",
    ):
        _close(values[column], oracle[column], column)

    assert 0.0 <= float(values["rsi_14"]) <= 100.0, values["rsi_14"]
    assert float(values["ma_20"]) != float(values["ma_200"]), (
        "ma_20 and ma_200 are identical: the parser is not using a window"
    )


def test_eod_parse_computes_every_performance_return() -> None:
    """Returns are computed from the bars, never quoted (02 §3.7, V-returns)."""

    fixture = W.chart_5y()
    oracle = W.eod_oracle(fixture)
    values = _parsed(Capability.EOD_BARS, fixture)

    columns = (
        "ret_1d",
        "ret_1w",
        "ret_1m",
        "ret_3m",
        "ret_6m",
        "ret_ytd",
        "ret_1y",
        "ret_3y",
        "ret_5y",
    )
    missing = _missing(values, columns)
    assert not missing, f"performance cells missing from the EOD parse: {missing}"

    for column in columns:
        _close(values[column], oracle[column], column)

    assert float(values["ret_5y"]) > float(values["ret_1y"]) > float(values["ret_1d"]), (
        "the return windows are not ordered by their window: "
        f"1d={values['ret_1d']!r} 1y={values['ret_1y']!r} 5y={values['ret_5y']!r}"
    )


def test_eod_parse_drops_a_bar_that_violates_the_ohlc_envelope() -> None:
    """V2: a bar with ``high < close`` is dropped and ``bars_count`` follows."""

    good = W.chart_5y()
    bad = W.corrupt_bar(good, index=5)
    expected = W.eod_oracle(bad)["bars_count"]

    values = _parsed(Capability.EOD_BARS, bad)
    assert "bars_count" in values, "the EOD parse produced no bars_count"
    assert values["bars_count"] == expected, (
        "a malformed OHLC bar must be dropped, not averaged in: "
        f"adapter {values['bars_count']!r}, oracle {expected!r}"
    )
    assert values["bars_count"] == len(W.bars(good)) - 1


def test_eod_fetch_requests_five_years_of_daily_bars(monkeypatch: Any) -> None:
    """A 1-day range cannot fill ``ma_200`` or ``ret_5y``; the URL must say 5y."""

    import asyncio

    import httpx2

    from tests._phase2 import MockWeb, install_web

    web = MockWeb()
    web.route("yahoo", lambda _request: httpx2.Response(200, json=W.chart_5y()))
    install_web(monkeypatch, web)

    instance = adapter()
    assert Capability.EOD_BARS in instance.capabilities, (
        "the adapter does not declare eod_bars, so it cannot be asked for bars"
    )
    raw = asyncio.run(
        instance.fetch(Capability.EOD_BARS, INSTRUMENT, set(CANONICAL_COLUMNS_V1))
    )

    assert raw.get("capability") == "eod_bars"
    chart_requests = [record for record in web.requests if "/v8/finance/chart/" in record.url]
    assert len(chart_requests) == 1, (
        "one instrument is one request (a warm-up for another host is allowed, a second "
        f"chart call is not); chart calls: {[r.url for r in chart_requests]}"
    )
    url = chart_requests[0].url
    assert "/v8/finance/chart/AAPL" in url, url
    assert "interval=1d" in url, f"EOD needs daily bars: {url}"
    assert "range=5y" in url, (
        "the EOD rung must ask for enough history to compute ma_200 and ret_5y: " f"{url}"
    )

    values = W.observations(instance.parse(raw, INSTRUMENT))
    assert values.get("bars_count") == W.eod_oracle(W.chart_5y())["bars_count"]


# ---------------------------------------------------------------------------
# Fundamentals
# ---------------------------------------------------------------------------


def test_fundamentals_parse_fills_all_22_columns() -> None:
    fixture = W.quote_summary()
    oracle = W.fundamentals_oracle(fixture)
    values = _parsed(Capability.FUNDAMENTALS, fixture)

    missing = _missing(values, W.FUNDAMENTAL_COLUMNS)
    assert not missing, (
        f"quoteSummary left these columns empty: {missing}; it produced {sorted(values)}"
    )
    for column, expected in oracle.items():
        if expected is None:
            assert values.get(column) in (None, ""), (
                f"{column} must stay empty without an FX rate, got {values.get(column)!r}"
            )
            continue
        _close(values[column], float(expected), column)

    # The two scaling rules the verifier relies on (V6 / no silent conversion).
    assert float(values["dividend_yield"]) == pytest.approx(0.44, rel=1e-9), (
        "dividend_yield is stored in percent points: Yahoo's 0.0044 is 0.44, not 0.0044"
    )
    assert float(values["market_cap_usd"]) == pytest.approx(
        float(values["market_cap"]), rel=1e-12
    ), "an AAPL quote in USD has the same market cap in both columns"

    # V4/V5 arithmetic the row will later be verified against.
    assert float(values["market_cap"]) == pytest.approx(
        float(fixture["quoteSummary"]["result"][0]["price"]["regularMarketPrice"])
        * float(values["shares_outstanding"]),
        rel=0.02,
    ), "market_cap must agree with price x shares_outstanding within 2% (V4)"


def test_fundamentals_fetch_asks_quote_summary_for_the_four_modules(
    monkeypatch: Any,
) -> None:
    import asyncio

    import httpx2

    from tests._phase2 import MockWeb, install_web

    web = MockWeb()
    web.route("yahoo", lambda _request: httpx2.Response(200, json=W.quote_summary()))
    install_web(monkeypatch, web)

    instance = adapter()
    assert Capability.FUNDAMENTALS in instance.capabilities, (
        "the adapter does not declare fundamentals, so it cannot be asked for them"
    )
    raw = asyncio.run(
        instance.fetch(Capability.FUNDAMENTALS, INSTRUMENT, set(CANONICAL_COLUMNS_V1))
    )

    assert raw.get("capability") == "fundamentals"
    summary_requests = [
        record for record in web.requests if "/quoteSummary/" in record.url
    ]
    assert len(summary_requests) == 1, (
        "one quoteSummary body is one request (the cookie/crumb warm-up, if any, is "
        f"not a second body); quoteSummary calls: {[r.url for r in summary_requests]}"
    )
    url = summary_requests[0].url
    assert "/v10/finance/quoteSummary/AAPL" in url, url
    for module in ("price", "summaryDetail", "defaultKeyStatistics", "financialData"):
        assert module in url, f"the modules parameter is missing {module!r}: {url}"

    values = W.observations(instance.parse(raw, INSTRUMENT))
    assert values.get("pe_ttm") is not None and values.get("revenue_ttm") is not None


# ---------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------


def test_options_parse_selects_the_thirty_dte_atm_call() -> None:
    fixture = W.options_chain()
    oracle = W.options_oracle(fixture)
    values = _parsed(Capability.OPTIONS, fixture)

    missing = _missing(values, W.OPTION_COLUMNS)
    assert not missing, (
        f"the options parse left these cells empty: {missing}; it produced {sorted(values)}"
    )

    assert str(values["opt_expiry"])[:10] == oracle["opt_expiry"], (
        "the ATM block must name the expiry it was read from: "
        f"{values['opt_expiry']!r} vs {oracle['opt_expiry']!r}"
    )
    assert int(values["opt_dte"]) == oracle["opt_dte"], (
        f"opt_dte: adapter {values['opt_dte']!r} vs oracle {oracle['opt_dte']!r} "
        "(DTE is measured from the chain's own regularMarketTime)"
    )
    assert float(values["opt_strike_atm"]) == pytest.approx(oracle["opt_strike_atm"]), (
        "the ATM strike is the call closest to the chain's spot: "
        f"{values['opt_strike_atm']!r} vs {oracle['opt_strike_atm']!r}"
    )

    iv = float(values["opt_iv_atm"])
    assert iv == pytest.approx(oracle["opt_iv_atm"], rel=1e-9), values["opt_iv_atm"]
    assert 0.0 < iv <= 5.0, (
        f"V7: opt_iv_atm is a fraction in (0, 5]; got {iv!r} — 26.43 (%) is 0.2643"
    )

    assert int(values["opt_open_interest_atm"]) == oracle["opt_open_interest_atm"]
    assert int(values["opt_volume_atm"]) == oracle["opt_volume_atm"]

    greeks = {
        "opt_delta_atm": values["opt_delta_atm"],
        "opt_gamma_atm": values["opt_gamma_atm"],
        "opt_theta_atm": values["opt_theta_atm"],
        "opt_vega_atm": values["opt_vega_atm"],
    }
    for column, value in greeks.items():
        assert isinstance(value, (int, float)) and not isinstance(value, bool), (
            f"{column}: Yahoo publishes no Greeks, so the adapter must compute one; "
            f"got {value!r}"
        )
        assert math.isfinite(float(value)), f"{column} is not finite: {value!r}"

    # V8 signs for a long ATM call.
    assert 0.0 < float(values["opt_delta_atm"]) < 1.0, values["opt_delta_atm"]
    assert float(values["opt_gamma_atm"]) >= 0.0, values["opt_gamma_atm"]
    assert float(values["opt_vega_atm"]) >= 0.0, values["opt_vega_atm"]
    assert float(values["opt_theta_atm"]) < 0.0, (
        "a long ATM call loses value with time: theta must be negative"
    )


def test_options_fetch_resolves_the_expiry_nearest_thirty_dte(monkeypatch: Any) -> None:
    """``/v7/finance/options`` returns the *nearest* chain; ~30 DTE needs ``date``."""

    import asyncio

    import httpx2

    from tests._phase2 import MockWeb, install_web

    fixture = W.options_chain()
    oracle = W.options_oracle(fixture)
    # The first response is what Yahoo answers without a ``date``: every expiry
    # plus the nearest one's chain.
    nearest = {
        "optionChain": {
            "result": [
                {
                    **fixture["optionChain"]["result"][0],
                    "options": [
                        {
                            **fixture["optionChain"]["result"][0]["options"][0],
                            "expirationDate": fixture["optionChain"]["result"][0][
                                "expirationDates"
                            ][0],
                        }
                    ],
                }
            ],
            "error": None,
        }
    }

    def responder(request: httpx2.Request) -> httpx2.Response:
        date_param = request.url.params.get("date")
        if date_param is None:
            return httpx2.Response(200, json=nearest)
        assert int(date_param) == oracle["expiration"], (
            "the dated request must ask for the expiry nearest 30 DTE "
            f"({oracle['expiration']}), got {date_param}"
        )
        return httpx2.Response(200, json=fixture)

    web = MockWeb()
    web.route("yahoo", responder)
    install_web(monkeypatch, web)

    instance = adapter()
    assert Capability.OPTIONS in instance.capabilities, (
        "the adapter does not declare options, so it cannot be asked for a chain"
    )
    raw = asyncio.run(
        instance.fetch(Capability.OPTIONS, INSTRUMENT, set(CANONICAL_COLUMNS_V1))
    )

    assert raw.get("capability") == "options"
    chain_requests = [
        record for record in web.requests if "/v7/finance/options/" in record.url
    ]
    assert len(chain_requests) == 2, (
        "resolving ~30 DTE is two requests (expiry list, then the dated chain); "
        f"the adapter sent {len(chain_requests)}: {[r.url for r in chain_requests]}"
    )
    assert "/v7/finance/options/AAPL" in chain_requests[0].url
    assert "date=" not in chain_requests[0].url, (
        f"the first request must not pin a date: {chain_requests[0].url}"
    )
    assert "date=" in chain_requests[1].url, chain_requests[1].url

    values = W.observations(instance.parse(raw, INSTRUMENT))
    assert int(values.get("opt_dte", -1)) == oracle["opt_dte"], (
        f"opt_dte: {values.get('opt_dte')!r} vs oracle {oracle['opt_dte']!r}"
    )


# ---------------------------------------------------------------------------
# The production live path
# ---------------------------------------------------------------------------


async def test_live_fetch_accepts_four_capabilities_and_fills_their_cells() -> None:
    """``_fetch_live`` must walk every requested capability, not refuse three."""

    from app.contracts import MarketRequest
    from app.jobs.errors import JobError

    request = MarketRequest.model_validate(
        W.submit_payload(
            market={"kind": "watchlist", "symbols": ["AAPL"]},
            enrichment=["quote", "eod_bars", "fundamentals", "options"],
            fields=None,
        )
    )
    ladder = W.CapabilityLadder()

    try:
        outcome = await W.runner_module()._fetch_live(
            request,
            ["AAPL"],
            set(CANONICAL_COLUMNS_V1),
            W.ladder_factory(ladder),
        )
    except JobError as exc:
        raise AssertionError(
            "the live path still refuses a four-capability request: " f"{exc}"
        ) from None

    assert len(ladder.calls) == 4, (
        "each requested capability is walked once per symbol; "
        f"the ladder saw {ladder.calls}"
    )
    assert sorted(ladder.calls) == ["eod_bars", "fundamentals", "options", "quote"]

    assert len(outcome.rows) == 1, outcome.rows
    row = outcome.rows[0]
    assert row["instrument_id"] == "XNAS:AAPL"
    assert row["as_of_achieved"] == "2026-10-21T20:00:00+00:00", (
        "the quote's as_of is the row's time of data"
    )

    for column in ("bars_count", "ma_20", "ma_200", "rsi_14"):
        assert row.get(column) is not None, (
            f"the EOD observations never reached the row: {column} is empty"
        )
    for column in ("market_cap", "pe_ttm", "roe"):
        assert row.get(column) is not None, (
            f"the fundamentals observations never reached the row: {column} is empty"
        )
    for column in ("opt_expiry", "opt_dte", "opt_strike_atm", "opt_iv_atm"):
        assert row.get(column) is not None, (
            f"the options observations never reached the row: {column} is empty"
        )

    assert row["ma_20"] == pytest.approx(341.75)
    assert row["market_cap"] == pytest.approx(5.1675848e12)
    assert row["opt_iv_atm"] == pytest.approx(0.2643)
    assert row["opt_expiry"] == "2026-11-20"

    # A quote cell is owned by the quote phase (P1 conflation), never pre-filled
    # by the fetch path — but the observation must be there for P1 to conflate.
    assert row.get("last_price") is None, (
        "the fetch path must not write quote cells; the quote phase conflates them"
    )
    documents = outcome.observations.get("XNAS:AAPL") or []
    assert documents, "the quote observations were not handed to the orchestrator"
    fields = {str(item.get("field")) for item in documents}
    assert {"last_price", "quote_as_of"} <= fields, (
        f"P1 needs the quote observations, got {sorted(fields)}"
    )


def test_fundamentals_fetch_retries_one_authwall(monkeypatch: Any) -> None:
    """A stale crumb is retried once; a second 401 remains the answer."""

    import asyncio

    import httpx2

    from tests._phase2 import MockWeb, install_web

    web = MockWeb()
    calls: list[str] = []

    def responder(request: httpx2.Request) -> httpx2.Response:
        path = request.url.path
        if "quoteSummary" in path:
            calls.append(str(request.url))
            if len(calls) == 1:
                return httpx2.Response(401, json={"error": "Invalid Crumb"})
            return httpx2.Response(200, json=W.quote_summary())
        if path.endswith("/v1/test/getcrumb"):
            return httpx2.Response(200, text="crumb-token")
        return httpx2.Response(200, text="")

    web.route("yahoo", responder)
    install_web(monkeypatch, web)

    instance = adapter()
    raw = asyncio.run(
        instance.fetch(Capability.FUNDAMENTALS, INSTRUMENT, set(CANONICAL_COLUMNS_V1))
    )

    assert int(raw.get("status") or 0) == 200
    assert len(calls) == 2, f"expected one retry after the 401, got {len(calls)} quoteSummary calls"
    assert "/quoteSummary/AAPL" in calls[-1]
