"""``build_depth_collector`` wiring for the optional per-symbol enrichment pass.

The default source list is a contract: the same seven rungs, in the same order,
that the keyless fast path ships today. ``depth_enrich`` is the only switch
allowed to add an eighth, and when it does the pass has to hand FINRA the two
divisor maps it owns — the Yahoo v7 batch ``average_volume`` and the stage's
``shares_float`` — otherwise ``short_ratio``/``short_float`` gain nothing from a
thirteen-minute crawl.

Everything here runs offline: the enrichment stage's own sources, the batch
quote source and FINRA are replaced with in-process fakes, so the suite's
zero-egress guard in ``tests/conftest.py`` is never tempted.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, Sequence

import pytest

from app.market.depth import DepthObservation, build_depth_collector
from app.market.enrichment import EnrichmentStage
from app.market.finra import FinraShortSource
from app.market.yahoo_batch import YahooBatchQuoteSource

T0 = datetime(2026, 10, 29, 14, 0, tzinfo=UTC)

#: The exact rungs, in order, a collector built without ``depth_enrich`` has.
DEFAULT_SOURCE_NAMES = (
    "sec",
    "sec_bulk",
    "finviz",
    "yahoo_full",
    "official",
    "stockanalysis",
    "finra",
)


def offline_settings(**overrides: Any) -> SimpleNamespace:
    """Settings whose only live source is FINRA; every flag is explicit.

    ``depth_sec_snapshot`` points at a path that does not exist, so the SEC
    identity rung answers ``{}`` without touching a file, and nothing here
    reaches a socket.
    """

    values: dict[str, Any] = {
        "depth_sec_snapshot": "/nonexistent/engine-snapshot.json",
        "depth_sec_bulk": False,
        "depth_finviz": False,
        "depth_yahoo_full": False,
        "depth_chart": False,
        "depth_official": False,
        "depth_stockanalysis": False,
        "depth_finra": True,
        "depth_news": False,
        "depth_store_path": None,
        "depth_concurrency": 1,
        "depth_delay_s": 0.0,
        "depth_yahoo_concurrency": 1,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def enrich_stage(collector: Any) -> EnrichmentStage:
    """The Yahoo half of the split cadence, or an ``AssertionError``."""

    stages = [
        source
        for source in collector.sources
        if isinstance(source, EnrichmentStage) and source.name == "enrich"
    ]
    assert len(stages) == 1, (
        "expected the Yahoo enrichment rung, got "
        f"{[s.name for s in collector.sources]}"
    )
    return stages[0]


def news_stage(collector: Any) -> EnrichmentStage:
    """The news half of the split cadence."""

    stages = [
        source
        for source in collector.sources
        if isinstance(source, EnrichmentStage) and source.name == "news_enrich"
    ]
    assert len(stages) == 1, (
        "expected the news enrichment rung, got "
        f"{[s.name for s in collector.sources]}"
    )
    return stages[0]


def test_settings_flag_defaults_on() -> None:
    """Enrichment is part of the design, not an opt-in extra."""

    from app.config import Settings

    settings = Settings()
    assert getattr(settings, "depth_enrich", None) is True
    assert getattr(settings, "depth_enrich_batch", None) == 500


def test_default_source_list_carries_neither_enricher() -> None:
    """``depth_enrich`` absent means today's list, name for name and rung for rung."""

    collector = build_depth_collector(
        SimpleNamespace(
            depth_store_path=None, depth_sec_snapshot=None, depth_enrich=False
        ),
        ["AAPL", "MSFT"],
    )

    assert tuple(source.name for source in collector.sources) == DEFAULT_SOURCE_NAMES
    assert not any(isinstance(source, EnrichmentStage) for source in collector.sources)
    assert not any(
        isinstance(source, YahooBatchQuoteSource) for source in collector.sources
    )


def test_depth_enrich_appends_the_stage_after_the_existing_sources() -> None:
    collector = build_depth_collector(
        offline_settings(depth_enrich=True), ["AAPL", "BRK.B"]
    )

    # The earlier rungs are untouched and keep their order; the pass is last,
    # so a field it shares with FINRA/SEC never outranks the cheaper answer.
    assert tuple(source.name for source in collector.sources) == (
        "sec",
        "finra",
        "enrich",
        "news_enrich",
    )
    stage = enrich_stage(collector)
    assert stage.name == "enrich"
    assert news_stage(collector).name == "news_enrich"
    # The Yahoo half costs 33 KB per symbol and is a daily fact; the news half
    # costs 2 KB and goes stale in an hour. Two rungs, two clocks.
    assert stage._refresh_s == 86_400.0
    assert news_stage(collector)._refresh_s == 300.0

    # The batch quote source rides along, held by the stage that consumes it.
    assert isinstance(stage.batch_quote_source, YahooBatchQuoteSource)


def test_finra_receives_both_enrichment_references() -> None:
    collector = build_depth_collector(offline_settings(depth_enrich=True), ["AAPL"])

    stage = enrich_stage(collector)
    finra = next(
        source for source in collector.sources if isinstance(source, FinraShortSource)
    )

    # Both divisors are live references, not a snapshot taken at build time:
    # the pass fills them after the crawl, and FINRA reads them at fetch time.
    assert callable(finra._average_volume)
    assert callable(finra._shares_float)
    assert finra._average_volume() == stage.average_volume_map()
    assert finra._shares_float() == stage.shares_float


def test_finra_keeps_its_own_reference_when_enrich_is_off() -> None:
    """Without the flag FINRA is constructed exactly as before — no new wiring."""

    collector = build_depth_collector(offline_settings(), ["AAPL"])

    finra = next(
        source for source in collector.sources if isinstance(source, FinraShortSource)
    )
    assert "enrich" not in [source.name for source in collector.sources]
    assert callable(finra._average_volume)
    assert finra._shares_float is None


async def test_fake_run_lands_an_enrichment_field_in_the_document(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End to end: the pass runs as a source and its fields reach the document."""

    import app.market.enrichment as enrichment_module
    import app.market.finra as finra_module

    class FakeYahoo:
        name = "yahoo_full"

        def __init__(self, **_: Any) -> None:
            self.calls: list[tuple[str, ...]] = []

        async def fetch_many(
            self, symbols: Sequence[str]
        ) -> dict[str, DepthObservation]:
            self.calls.append(tuple(symbols))
            return {
                "AAA": DepthObservation(
                    symbol="AAA",
                    fields={"pe": 12.5, "shares_float": 4_000_000.0},
                    as_of=T0,
                    source=self.name,
                )
            }

    class FakeNews:
        name = "news"

        def __init__(self, **_: Any) -> None:
            pass

        async def fetch_many(self, symbols: Sequence[str]) -> dict[str, DepthObservation]:
            return {}

    class OfflineBatchQuote(YahooBatchQuoteSource):
        async def fetch_many(self, symbols: Sequence[str]) -> dict[str, dict]:
            return {"AAA": {"average_volume": 1_000_000.0}}

    class OfflineFinra(FinraShortSource):
        async def fetch_many(self, symbols: Sequence[str]) -> dict[str, DepthObservation]:
            return {}

    monkeypatch.setattr(enrichment_module, "YahooDepthSource", FakeYahoo)
    monkeypatch.setattr(enrichment_module, "RssNewsSource", FakeNews)
    monkeypatch.setattr("app.market.yahoo_batch.YahooBatchQuoteSource", OfflineBatchQuote)
    monkeypatch.setattr(finra_module, "FinraShortSource", OfflineFinra)

    collector = build_depth_collector(
        offline_settings(depth_enrich=True, depth_refresh_s=86_400.0),
        ["AAA"],
        clock=lambda: T0,
    )
    stage = enrich_stage(collector)
    finra = next(
        source for source in collector.sources if isinstance(source, OfflineFinra)
    )

    result = await collector.refresh()
    assert result.errors == 0, result.error

    document = collector.document()
    row = document["symbols"]["AAA"]
    # ``pe`` exists nowhere else in this run: it can only come from the pass.
    assert row["pe"] == 12.5
    assert row["shares_float"] == 4_000_000.0

    # The pass pushed both maps into FINRA as concrete values, not just the
    # build-time references: that is what upgrades short_ratio/short_float.
    assert finra._average_volume == {"AAA": 1_000_000.0}
    assert finra._shares_float == {"AAA": 4_000_000.0}
    assert stage.average_volume_map() == {"AAA": 1_000_000.0}


async def test_one_pass_serves_every_chunk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 250-symbol chunking must not turn one 13-minute crawl into seventeen."""

    import app.market.enrichment as enrichment_module

    class FakeYahoo:
        name = "yahoo_full"

        def __init__(self, **_: Any) -> None:
            self.calls: list[tuple[str, ...]] = []

        async def fetch_many(
            self, symbols: Sequence[str]
        ) -> dict[str, DepthObservation]:
            self.calls.append(tuple(symbols))
            return {
                symbol: DepthObservation(
                    symbol=symbol, fields={"pe": 1.0}, as_of=T0, source=self.name
                )
                for symbol in symbols
            }

    class FakeNews:
        name = "news"

        def __init__(self, **_: Any) -> None:
            pass

        async def fetch_many(self, symbols: Sequence[str]) -> dict[str, DepthObservation]:
            return {}

    class OfflineFinra(FinraShortSource):
        async def fetch_many(self, symbols: Sequence[str]) -> dict[str, DepthObservation]:
            return {}

    class OfflineBatchQuote(YahooBatchQuoteSource):
        async def fetch_many(self, symbols: Sequence[str]) -> dict[str, dict]:
            return {}

    monkeypatch.setattr(enrichment_module, "YahooDepthSource", FakeYahoo)
    monkeypatch.setattr(enrichment_module, "RssNewsSource", FakeNews)
    monkeypatch.setattr("app.market.yahoo_batch.YahooBatchQuoteSource", OfflineBatchQuote)
    monkeypatch.setattr("app.market.finra.FinraShortSource", OfflineFinra)

    collector = build_depth_collector(
        offline_settings(depth_enrich=True, depth_source_chunk_size=2, depth_finra=False),
        ["AAA", "BBB", "CCC", "DDD"],
        clock=lambda: T0,
    )
    stage = enrich_stage(collector)

    await collector.refresh()

    # Four symbols at a chunk size of two is two collector calls, but the crawl
    # itself was asked for the whole universe exactly once.
    assert stage.yahoo_source.calls == [("AAA", "BBB", "CCC", "DDD")]
    assert collector.document()["symbols"]["DDD"]["pe"] == 1.0


def test_the_rotation_has_its_own_cadence() -> None:
    """500 symbols per 24 h would take nine days; the rung uses its own timer."""

    settings = SimpleNamespace(
        depth_store_path=None,
        depth_sec_snapshot=None,
        depth_enrich=True,
        depth_enrich_batch=500,
        depth_enrich_interval_s=300.0,
        depth_refresh_s=86_400.0,
    )
    collector = build_depth_collector(settings, ["AAPL", "MSFT"])
    enrich = next(s for s in collector.sources if s.name == "enrich")
    assert enrich._refresh_s == 300.0, "the enrichment cadence, not the 24 h refresh"
