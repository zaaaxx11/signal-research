"""``EnrichmentStage``: the per-symbol crawl that follows the keyless fast path.

Every test drives the stage through injected fake sources, so the suite stays
inside the zero-egress guard in ``tests/conftest.py``: no socket, no crumb, no
feed. The fakes record the symbol list they were handed, which is how the
isolation, the wiring and the merge order are checked rather than assumed.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any, Sequence

from app.market.depth import DepthObservation
from app.market.enrichment import EnrichmentStage
from app.market.news import RssNewsSource
from app.market.yahoo_depth import YahooDepthSource

T0 = datetime(2026, 10, 29, 14, 0, tzinfo=UTC)


def obs(
    symbol: str,
    fields: dict[str, Any],
    *,
    source: str = "fixture",
    ts: datetime = T0,
) -> DepthObservation:
    return DepthObservation(symbol=symbol, fields=fields, as_of=ts, source=source)


class FakeSource:
    """One depth source that answers from a table, or fails on demand.

    ``broken`` answers with something that is not a mapping, which is how a
    source that violates the protocol is exercised without a network.
    """

    def __init__(
        self,
        name: str,
        values: dict[str, DepthObservation],
        *,
        error: BaseException | None = None,
        broken: bool = False,
    ) -> None:
        self.name = name
        self.values = values
        self.error = error
        self.broken = broken
        self.calls: list[tuple[str, ...]] = []

    async def fetch_many(
        self, symbols: Sequence[str]
    ) -> dict[str, DepthObservation]:
        self.calls.append(tuple(symbols))
        if self.error is not None:
            raise self.error
        if self.broken:
            return [self.values[symbol] for symbol in symbols if symbol in self.values]  # type: ignore[return-value]
        return {
            symbol: self.values[symbol] for symbol in symbols if symbol in self.values
        }


def stage(
    yahoo: FakeSource, news: FakeSource, **kwargs: Any
) -> EnrichmentStage:
    return EnrichmentStage(yahoo_source=yahoo, news_source=news, **kwargs)


async def test_merges_both_sources_with_yahoo_winning_a_key_collision() -> None:
    yahoo = FakeSource(
        "yahoo_full",
        {
            "AAPL": obs(
                "AAPL",
                {
                    "market_cap": 3_000_000_000_000.0,
                    "shares_float": 15_200_000_000.0,
                    "pe": 32.4,
                },
                source="yahoo_full",
            )
        },
    )
    news = FakeSource(
        "news",
        {
            "AAPL": obs(
                "AAPL",
                {
                    "news_title": "Apple lifts buyback by another $50B",
                    "news_url": "https://finance.yahoo.com/news/apple-lifts-buyback.html",
                    "news_time": "2026-10-29T12:00:00+00:00",
                    # A collision: the feed must not outvote Yahoo's own number.
                    "market_cap": 1.0,
                },
                source="news",
            )
        },
    )

    result = await stage(yahoo, news).run(["AAPL"])

    assert result == {
        "AAPL": {
            "market_cap": 3_000_000_000_000.0,
            "shares_float": 15_200_000_000.0,
            "pe": 32.4,
            "news_title": "Apple lifts buyback by another $50B",
            "news_url": "https://finance.yahoo.com/news/apple-lifts-buyback.html",
            "news_time": "2026-10-29T12:00:00+00:00",
        }
    }


async def test_one_source_raising_costs_only_its_own_fields() -> None:
    yahoo = FakeSource(
        "yahoo_full", {}, error=RuntimeError("crumb endpoint throttled")
    )
    news = FakeSource(
        "news",
        {"MSFT": obs("MSFT", {"news_title": "Microsoft signs a deal"}, source="news")},
    )
    enrichment = stage(yahoo, news)

    result = await enrichment.run(["MSFT"])

    assert result == {"MSFT": {"news_title": "Microsoft signs a deal"}}
    assert enrichment.last_errors == {
        "yahoo_full": "RuntimeError: crumb endpoint throttled"
    }
    assert enrichment.shares_float == {}


async def test_shares_float_map_is_extracted_and_skips_unusable_values() -> None:
    yahoo = FakeSource(
        "yahoo_full",
        {
            "AAPL": obs("AAPL", {"shares_float": 15_200_000_000.0}, source="yahoo_full"),
            "MSFT": obs("MSFT", {"shares_float": 7_400_000_000}, source="yahoo_full"),
            "NVDA": obs("NVDA", {"shares_float": "24300000000"}, source="yahoo_full"),
            "TSLA": obs("TSLA", {"shares_float": None}, source="yahoo_full"),
            "AMD": obs("AMD", {"shares_float": "n/a"}, source="yahoo_full"),
            "GOOG": obs("GOOG", {"shares_float": True}, source="yahoo_full"),
            "META": obs("META", {"shares_float": float("nan")}, source="yahoo_full"),
            # The cell is absent, not zero: no float is invented for it.
            "AMZN": obs("AMZN", {"pe": 40.0}, source="yahoo_full"),
        },
    )
    news = FakeSource("news", {})
    enrichment = stage(yahoo, news)

    await enrichment.run(
        ["AAPL", "MSFT", "NVDA", "TSLA", "AMD", "GOOG", "META", "AMZN"]
    )

    assert enrichment.shares_float == {
        "AAPL": 15_200_000_000.0,
        "MSFT": 7_400_000_000.0,
        "NVDA": 24_300_000_000.0,
    }


async def test_last_errors_records_a_failure_and_a_clean_run_clears_it() -> None:
    yahoo = FakeSource(
        "yahoo_full",
        {
            "AAPL": obs(
                "AAPL",
                {"shares_float": 15_200_000_000.0, "pe": 32.4},
                source="yahoo_full",
            )
        },
    )
    news = FakeSource("news", {}, error=ValueError("feed answered HTTP 500"))
    enrichment = stage(yahoo, news)

    result = await enrichment.run(["AAPL"])

    assert result == {"AAPL": {"shares_float": 15_200_000_000.0, "pe": 32.4}}
    assert enrichment.last_errors == {"news": "ValueError: feed answered HTTP 500"}
    # A failed feed must not cost the float map FINRA is waiting for.
    assert enrichment.shares_float == {"AAPL": 15_200_000_000.0}

    news.error = None
    news.values = {
        "AAPL": obs("AAPL", {"news_title": "Apple lifts buyback"}, source="news")
    }
    result = await enrichment.run(["AAPL"])

    assert result["AAPL"]["news_title"] == "Apple lifts buyback"
    assert enrichment.last_errors == {}


async def test_an_empty_symbol_list_asks_nobody() -> None:
    yahoo = FakeSource("yahoo_full", {})
    news = FakeSource("news", {})
    enrichment = stage(yahoo, news)

    assert await enrichment.run([]) == {}
    assert yahoo.calls == []
    assert news.calls == []
    assert enrichment.last_errors == {}
    assert enrichment.shares_float == {}


async def test_symbols_are_canonicalised_and_deduplicated_before_any_request() -> None:
    yahoo = FakeSource("yahoo_full", {})
    news = FakeSource("news", {})
    enrichment = stage(yahoo, news)

    await enrichment.run(["aapl", "AAPL", "brk.b", "", "BRK-B"])

    assert yahoo.calls == [("AAPL", "BRK-B")]
    assert news.calls == [("AAPL", "BRK-B")]


async def test_a_source_that_answers_without_a_mapping_is_recorded_not_raised() -> None:
    yahoo = FakeSource(
        "yahoo_full", {"AAPL": obs("AAPL", {"pe": 32.4}, source="yahoo_full")}, broken=True
    )
    news = FakeSource(
        "news",
        {"AAPL": obs("AAPL", {"news_title": "Apple lifts buyback"}, source="news")},
    )
    enrichment = stage(yahoo, news)

    result = await enrichment.run(["AAPL"])

    assert result == {"AAPL": {"news_title": "Apple lifts buyback"}}
    assert enrichment.last_errors == {
        "yahoo_full": "TypeError: fetch_many did not return a mapping"
    }
    assert enrichment.shares_float == {}


async def test_a_symbol_no_source_covered_is_absent_from_the_result() -> None:
    yahoo = FakeSource(
        "yahoo_full", {"AAPL": obs("AAPL", {"pe": 32.4}, source="yahoo_full")}
    )
    news = FakeSource("news", {})

    result = await stage(yahoo, news).run(["AAPL", "ZZZZ"])

    assert result == {"AAPL": {"pe": 32.4}}


def test_the_default_stage_wires_the_paced_yahoo_source_and_the_news_chain() -> None:
    enrichment = EnrichmentStage()

    assert isinstance(enrichment.yahoo_source, YahooDepthSource)
    assert enrichment.yahoo_source.include_timeseries is True
    assert enrichment.yahoo_source.include_options is False
    assert enrichment.yahoo_source.request_delay_s == 0.25
    assert enrichment.yahoo_source.concurrency == 4
    assert isinstance(enrichment.news_source, RssNewsSource)
    assert enrichment.news_source.fallback is True


def test_concurrency_is_configurable() -> None:
    assert EnrichmentStage(concurrency=9).yahoo_source.concurrency == 9


# ---------------------------------------------------------------------------
# Batch rotation
# ---------------------------------------------------------------------------
#
# A single 4,217-symbol Yahoo pass stalls the egress; a daily rotation over
# the universe is the shape that survives.


def test_a_batch_takes_the_next_slice_and_wraps(tmp_path: Path) -> None:
    stage = EnrichmentStage(
        yahoo_source=FakeSource("yahoo", {}),
        news_source=FakeSource("news", {}),
        batch_size=3,
        state_path=tmp_path / "cursor.json",
    )
    symbols = ["A", "B", "C", "D", "E"]
    first = stage._take_batch(symbols)
    second = stage._take_batch(symbols)
    assert first == ["A", "B", "C"]
    assert second == ["D", "E", "A"], "wraps around the end"


def test_the_cursor_survives_a_restart(tmp_path: Path) -> None:
    state = tmp_path / "cursor.json"
    one = EnrichmentStage(
        yahoo_source=FakeSource("yahoo", {}),
        news_source=FakeSource("news", {}),
        batch_size=2,
        state_path=state,
    )
    one._take_batch(["A", "B", "C", "D"])

    two = EnrichmentStage(
        yahoo_source=FakeSource("yahoo", {}),
        news_source=FakeSource("news", {}),
        batch_size=2,
        state_path=state,
    )
    assert two._take_batch(["A", "B", "C", "D"]) == ["C", "D"]


def test_a_corrupt_state_file_starts_at_zero(tmp_path: Path) -> None:
    state = tmp_path / "cursor.json"
    state.write_text("not json")
    stage = EnrichmentStage(
        yahoo_source=FakeSource("yahoo", {}),
        news_source=FakeSource("news", {}),
        batch_size=2,
        state_path=state,
    )
    assert stage._take_batch(["A", "B", "C"]) == ["A", "B"]


def test_no_batch_size_enriches_everything(tmp_path: Path) -> None:
    stage = EnrichmentStage(
        yahoo_source=FakeSource("yahoo", {}),
        news_source=FakeSource("news", {}),
    )
    assert stage.batch_size is None


# ---------------------------------------------------------------------------
# Fallback when the enrichment egress refuses
# ---------------------------------------------------------------------------


def test_a_dead_cycle_does_not_advance_the_cursor(tmp_path: Path) -> None:
    """No answer means the slice was not covered; retry it next cycle.

    Advancing on an empty cycle would walk the cursor past symbols nobody
    enriched and only come back after a full wrap.
    """

    stage = EnrichmentStage(
        yahoo_source=FakeSource("yahoo", {}),
        news_source=FakeSource("news", {}),
        batch_size=2,
        state_path=tmp_path / "cursor.json",
    )
    symbols = ["A", "B", "C", "D"]
    assert _run(stage.run(symbols)) == {}
    assert json.loads((tmp_path / "cursor.json").read_text())["cursor"] == 0

    # The next cycle starts where the dead one did.
    assert stage._take_batch(symbols) == ["A", "B"]


def test_a_cycle_with_any_answer_advances(tmp_path: Path) -> None:
    answered = {
        "A": DepthObservation(
            symbol="A",
            fields={"pe": 1.0},
            as_of=datetime.now(UTC),
            source="yahoo",
        )
    }
    stage = EnrichmentStage(
        yahoo_source=FakeSource("yahoo", answered),
        news_source=FakeSource("news", {}),
        batch_size=2,
        state_path=tmp_path / "cursor.json",
    )
    assert _run(stage.run(["A", "B", "C", "D"])) == {"A": {"pe": 1.0}}
    assert json.loads((tmp_path / "cursor.json").read_text())["cursor"] == 2


def _run(coro: Any) -> Any:
    return asyncio.run(coro)
