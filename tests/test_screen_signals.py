"""Signals as a screener filter (plan T7).

A Finviz signal is a **population plus its own order**: ``top-gainers`` is every
row whose ``change_pct`` is positive, highest first. Today ``/market/signals``
publishes thirteen such tables for the home page; a screen panel had no way to
ask for one. This suite pins the screener half:

* the vocabulary is **27** ids — Finviz's fifteen market signals and twelve
  chart patterns — frozen in one tuple, with exactly ten available;
* an available signal's ``criterion``, ``metric`` and direction are the **home
  rule's**, compared against :data:`app.api.signals.SIGNALS` here (in the test
  only: production may not import the HTTP layer), so the two tables cannot
  describe different populations;
* an unavailable signal is a ``422 unavailable_signal`` that names itself —
  never an empty page, which a caller reads as "no stock is overbought";
* a signal sets the population, and when the request names no ``sort`` its own
  measure becomes the order; an explicit ``sort`` always wins;
* the signal is part of the canonical request, so two signals are two questions
  and therefore two ETags.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.screen.descriptive import SIGNALS
from app.screen.selection import (
    SelectionError,
    canonical_request,
    parse_selection,
    request_digest,
    select_rows,
)

AS_OF = "2026-11-02T14:30:00.000000+00:00"

#: Finviz's fifteen market signals, in panel order.
MARKET_IDS: tuple[str, ...] = (
    "top-gainers",
    "top-losers",
    "most-active",
    "most-volatile",
    "most-increased-volume",
    "unusual-volume",
    "overbought",
    "oversold",
    "new-high",
    "new-low",
    "upgrades",
    "downgrades",
    "earnbefore",
    "earnafter",
    "majornews",
)

#: Finviz's twelve chart patterns, in panel order.
PATTERN_IDS: tuple[str, ...] = (
    "wedgeup",
    "wedge",
    "wedgedown",
    "tri-ascending",
    "tri-descending",
    "channelup",
    "channel",
    "channeldown",
    "doubletop",
    "doublebottom",
    "head-shoulders",
    "head-shoulders-inverse",
)

#: The 22 signals this build can compute; the other five say why not.
AVAILABLE: frozenset[str] = frozenset(
    {
        "top-gainers",
        "top-losers",
        "most-active",
        "most-volatile",
        "most-increased-volume",
        "unusual-volume",
        "overbought",
        "oversold",
        "new-high",
        "new-low",
        "wedgeup",
        "wedge",
        "wedgedown",
        "tri-ascending",
        "tri-descending",
        "channelup",
        "channel",
        "channeldown",
        "doubletop",
        "doublebottom",
        "head-shoulders",
        "head-shoulders-inverse",
    }
)


def row(symbol: str, **cells: Any) -> dict[str, Any]:
    """One universe row: the symbol plus whatever the signal reads."""

    base: dict[str, Any] = {"symbol": symbol, "name": f"{symbol} Inc"}
    base.update(cells)
    return base


def criteria(**extra: Any) -> dict[str, Any]:
    block: dict[str, Any] = {"limit": 50}
    block.update(extra)
    return {"mode": "criteria", "region": "us", "criteria": block}


def run(signal: str, rows: list[dict[str, Any]], **extra: Any) -> Any:
    return select_rows(
        rows,
        parse_selection({**criteria(**extra), "signal": signal}),
        region="us",
        as_of=AS_OF,
    )


def symbols_of(selection: Any) -> list[str]:
    return [entry["symbol"] for entry in selection.rows]


# ---------------------------------------------------------------------------
# The vocabulary
# ---------------------------------------------------------------------------


def test_the_vocabulary_is_27() -> None:
    ids = tuple(item.id for item in SIGNALS)
    assert len(ids) == 27
    assert ids[:15] == MARKET_IDS, "the fifteen market signals, in panel order"
    assert ids[15:] == PATTERN_IDS, "the twelve patterns, in panel order"
    assert len(set(ids)) == 27, "an id may name one signal"
    assert {item.id for item in SIGNALS if item.available} == AVAILABLE
    assert len([item for item in SIGNALS if item.available]) == 22
    for item in SIGNALS:
        assert item.label.strip(), f"{item.id} has no label"
        if item.available:
            assert item.reason is None, f"{item.id} is available and still says why not"
            assert item.sort, f"{item.id} names no order"
        else:
            assert item.reason and len(item.reason) > 20, (
                f"{item.id} is unavailable without a usable reason"
            )


def test_available_signals_agree_with_the_home_rule() -> None:
    """One rule, two surfaces: the home table and the screener cannot drift."""

    from app.api.signals import SIGNALS as HOME

    home = {rule.id: rule for rule in HOME}
    mapped = [item for item in SIGNALS if item.home]
    assert len(mapped) == 9, (
        "nine of the ten available signals are home-page rules; "
        "most-increased-volume has no home table"
    )
    for item in mapped:
        rule = home[item.home]
        assert item.criterion == rule.criterion, (
            f"{item.id} and /market/signals/{rule.id} describe different predicates"
        )
        assert item.metric == rule.metric, f"{item.id} measures a different cell"
        direction = "asc" if item.sort.startswith("-") else "desc"
        assert direction == rule.order, f"{item.id} orders the other way round"
        assert not rule.available or item.available, (
            f"{item.id} is available while the home rule it mirrors is not"
        )
    assert {item.home for item in mapped} <= set(home)


@pytest.mark.parametrize(
    "signal_id", [item.id for item in SIGNALS if not item.available]
)
def test_unavailable_signal_is_422_unavailable_signal(signal_id: str) -> None:
    with pytest.raises(SelectionError) as caught:
        parse_selection({**criteria(), "signal": signal_id})

    assert caught.value.code == "unavailable_signal", caught.value.code
    message = str(caught.value)
    assert signal_id in message, "the refusal must name the signal"
    assert "422" not in message  # the code carries the status, not the prose


def test_an_unknown_signal_is_422_unknown_signal() -> None:
    with pytest.raises(SelectionError) as caught:
        parse_selection({**criteria(), "signal": "top-gainerz"})

    assert caught.value.code == "unknown_signal"
    assert "top-gainerz" in str(caught.value)
    assert "top-gainers" in str(caught.value), "the message lists the vocabulary"


def test_a_symbols_selection_does_not_take_a_signal() -> None:
    with pytest.raises(SelectionError) as caught:
        parse_selection(
            {
                "mode": "symbols",
                "symbols": ["AAPL"],
                "source": "tradingview",
                "snapshot_id": "snap-1",
                "signal": "top-gainers",
            }
        )
    assert "signal" in str(caught.value)


# ---------------------------------------------------------------------------
# The population and the order
# ---------------------------------------------------------------------------


def test_signal_orders_by_its_own_measure() -> None:
    rows = [
        row("SLOW", change_pct=1.0, market_cap_usd=9e12),
        row("FAST", change_pct=12.0, market_cap_usd=1e9),
        row("MID", change_pct=5.0, market_cap_usd=5e11),
        row("RED", change_pct=-3.0, market_cap_usd=1e12),
    ]

    selection = run("top-gainers", rows)

    assert symbols_of(selection) == ["FAST", "MID", "SLOW"], (
        "the gainers population, highest change first — not by market cap"
    )
    assert selection.total == 3
    assert selection.sort == "change_pct", "the envelope echoes the applied order"


def test_losers_ascend() -> None:
    rows = [
        row("MILD", change_pct=-2.0),
        row("DEEP", change_pct=-12.0),
        row("UP", change_pct=3.0),
    ]

    selection = run("top-losers", rows)

    assert symbols_of(selection) == ["DEEP", "MILD"]
    assert selection.sort == "-change_pct", "a leading '-' is ascending"


def test_an_explicit_sort_wins() -> None:
    rows = [
        row("SLOW", change_pct=1.0, market_cap_usd=9e12),
        row("FAST", change_pct=12.0, market_cap_usd=1e9),
    ]

    selection = run("top-gainers", rows, sort="market_cap")

    assert symbols_of(selection) == ["SLOW", "FAST"]
    assert selection.sort == "market_cap"


def test_a_signal_and_a_filter_intersect() -> None:
    rows = [
        row("BIGUP", change_pct=4.0, market_cap_usd=9e12),
        row("SMALLUP", change_pct=4.0, market_cap_usd=1e8),
        row("BIGDOWN", change_pct=-4.0, market_cap_usd=9e12),
    ]
    selection = run(
        "top-gainers", rows, filters=[{"field": "market_cap_usd", "op": "gte", "value": 1e9}]
    )

    assert symbols_of(selection) == ["BIGUP"]
    assert selection.total == 1


def test_a_signal_whose_measure_is_missing_keeps_no_row() -> None:
    rows = [row("NOCELL"), row("ZERO", change_pct=0.0)]

    selection = run("top-gainers", rows)

    assert selection.total == 0, "unknown is not a gainer, and 0% is not a gain"


def test_overbought_reads_rsi_and_refuses_unknown() -> None:
    rows = [
        row("HOT", rsi_14=78.0),
        row("WARM", rsi_14=55.0),
        row("UNKNOWN"),
    ]

    selection = run("overbought", rows)

    assert symbols_of(selection) == ["HOT"]


def test_new_high_compares_the_price_with_the_52_week_high() -> None:
    rows = [
        row("ATH", price=100.0, high_52w=100.0),
        row("NEAR", price=99.0, high_52w=100.0),
        row("FAR", price=80.0, high_52w=100.0),
    ]

    selection = run("new-high", rows)

    assert symbols_of(selection) == ["ATH"]


def test_unusual_volume_is_a_ratio_not_a_threshold_on_shares() -> None:
    rows = [
        row("LOUD", volume=3_000_000.0, avg_volume_30d=1_000_000.0, rel_volume_10d=3.2),
        row("QUIET", volume=3_000_000.0, avg_volume_30d=3_000_000.0, rel_volume_10d=1.0),
    ]

    selection = run("unusual-volume", rows)

    assert symbols_of(selection) == ["LOUD"]


# ---------------------------------------------------------------------------
# The contract
# ---------------------------------------------------------------------------


def test_the_signal_rides_in_the_canonical_request() -> None:
    gainers = parse_selection({**criteria(), "signal": "top-gainers"})
    losers = parse_selection({**criteria(), "signal": "top-losers"})
    plain = parse_selection(criteria())

    assert canonical_request(gainers)["signal"] == "top-gainers"
    assert canonical_request(plain)["signal"] is None
    assert request_digest(gainers) != request_digest(losers)
    assert request_digest(gainers) != request_digest(plain)


def test_the_signal_is_top_level_and_not_a_criteria_knob() -> None:
    """``signal`` names the population, so it sits beside ``region``."""

    with pytest.raises(SelectionError) as caught:
        parse_selection(
            {
                "mode": "criteria",
                "region": "us",
                "criteria": {"signal": "top-gainers", "limit": 5},
            }
        )
    assert "signal" in str(caught.value)
