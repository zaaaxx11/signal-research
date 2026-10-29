"""Contract for the keyless SEC-bulk depth source and its collector wiring.

The slice answers one question: **can a keyless run fill the fundamentals
columns without a per-symbol provider?** ``SecFundamentalsDepthSource`` wraps
the streamed quarterly ZIP index (``app.engine.bulk_financials``) and the
catalog mapping (``app.market.sec_fundamentals``) behind the depth protocol,
and ``build_depth_collector`` gates it (and the two per-symbol web rungs) so a
keyless-fast deployment can name its sources exactly.

The rules pinned here:

* **Construction never builds.** The four-quarter index costs minutes; it is
  built on the first ``fetch_many``, once, and reused.
* **A missing cache is no rows, not an exception.** No ZIP, no snapshot, no
  price reference — the source stays silent and the cycle keeps its other
  sources.
* **Absent, never zero.** A field with no evidence is not published; a price
  that is not a real quote is ignored rather than crashing the chunk.
* **Symbols are canonical at the edge.** ``aapl``/``BRK.B`` arrive as
  ``AAPL``/``BRK-B``, the spelling the snapshot's CIK map uses.
* **``short_ratio`` needs its divisor.** The FINRA source is wired with the
  Yahoo v7 batch average volume; without it the ratio is absent, never zero.

The fixture is the same two-issuer ZIP shape ``test_market_sec_fundamentals``
builds (Apple with a restatement). No test reaches the network: the HTTP seam
is the Phase-2 ``MockTransport``.
"""

from __future__ import annotations

import asyncio
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Sequence

import pytest

from app.engine.bulk_financials import current_quarter, parse_quarter, quarter_label

AAPL_CIK = "0000320193"
AAPL_K = "0000320193-25-000079"  # 10-K, filed 2025-10-31
AAPL_Q = "0000320193-26-000020"  # 10-Q, filed 2026-07-31

SUB_HEADER = ["adsh", "cik", "name", "form", "period", "fy", "fp", "filed"]
NUM_HEADER = [
    "adsh",
    "tag",
    "version",
    "ddate",
    "qtrs",
    "uom",
    "segments",
    "coreg",
    "value",
    "footnote",
]


def _sub(
    adsh: str,
    cik: int,
    name: str,
    form: str,
    period: str,
    fy: int,
    fp: str,
    filed: str,
) -> list[str]:
    return [adsh, str(cik), name, form, period, str(fy), fp, filed]


def _num(
    adsh: str, tag: str, ddate: str, qtrs: object, uom: str, value: object
) -> list[str]:
    return [
        adsh,
        tag,
        "us-gaap/2026",
        ddate,
        "" if qtrs is None else str(qtrs),
        uom,
        "",
        "",
        str(value),
        "",
    ]


def _quarter_members() -> tuple[str, str]:
    """``sub.txt``/``num.txt`` for one quarter: Apple's 10-K plus a restatement."""
    subs = [
        _sub(AAPL_K, 320193, "Apple Inc.", "10-K", "20250927", 2025, "FY", "20251031"),
        _sub(AAPL_Q, 320193, "Apple Inc.", "10-Q", "20260630", 2026, "Q3", "20260731"),
    ]
    nums = [
        _num(
            AAPL_K,
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "20250927",
            4,
            "USD",
            416_161_000_000,
        ),
        # The later-filed 10-Q restates the annual: 111.0bn wins over 112.01bn.
        _num(AAPL_Q, "NetIncomeLoss", "20250927", 4, "USD", 111_000_000_000),
        _num(AAPL_K, "EarningsPerShareDiluted", "20250927", 4, "USD", 7.46),
        _num(AAPL_K, "Assets", "20250927", 0, "USD", 364_980_000_000),
        _num(
            AAPL_K, "CommonStockSharesOutstanding", "20250927", 0, "shares", 14_800_000_000
        ),
    ]
    sub_text = "\n".join(["\t".join(SUB_HEADER)] + ["\t".join(r) for r in subs]) + "\n"
    num_text = "\n".join(["\t".join(NUM_HEADER)] + ["\t".join(r) for r in nums]) + "\n"
    return sub_text, num_text


def _write_quarter(root: Path, quarter: str) -> Path:
    """One cached dataset ZIP at ``<root>/sec-bulk/<quarter>.zip``."""
    sub_text, num_text = _quarter_members()
    path = root / "sec-bulk" / f"{quarter}.zip"
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("sub.txt", sub_text)
        archive.writestr("num.txt", num_text)
    return path


def _recent_labels(count: int = 4) -> list[str]:
    """The ``count`` newest dataset labels, current quarter first."""
    year, number = parse_quarter(current_quarter())
    index = year * 4 + (number - 1)
    labels = []
    for step in range(count):
        y, q = divmod(index - step, 4)
        labels.append(quarter_label(y, q + 1))
    return labels


def _module() -> Any:
    """The module under test; ``NotImplementedError`` while it does not exist."""
    try:
        import app.market.sec_fundamentals_depth as module
    except ImportError as exc:  # pragma: no cover - the RED phase
        raise NotImplementedError(
            "app/market/sec_fundamentals_depth.py is missing"
        ) from exc
    return module


def _settings(**overrides: Any) -> SimpleNamespace:
    values: dict[str, Any] = dict(
        depth_sec_snapshot=None,
        depth_sec_bulk=True,
        depth_finviz=True,
        depth_yahoo_full=True,
        depth_stockanalysis=True,
        depth_chart=False,
        depth_official=True,
        depth_finra=True,
        depth_news=False,
        depth_concurrency=1,
        depth_delay_s=0.0,
        depth_yahoo_concurrency=1,
        depth_yahoo_delay_s=0.0,
        depth_yahoo_symbol_timeout_s=30.0,
        depth_official_concurrency=2,
        depth_news_concurrency=8,
        depth_store_path=None,
        depth_refresh_s=86_400.0,
        depth_jitter_s=1_800.0,
        depth_source_timeout_s=180.0,
        depth_source_chunk_size=250,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def _names(collector: Any) -> list[str]:
    return [source.name for source in collector.sources]


# ---------------------------------------------------------------------------
# The source itself
# ---------------------------------------------------------------------------


def test_construction_builds_no_index(tmp_path: Path) -> None:
    module = _module()
    _write_quarter(tmp_path, "2026q3")
    source = module.SecFundamentalsDepthSource(
        cache_dir=tmp_path,
        quarters=("2026q3",),
        cik_by_symbol={"AAPL": AAPL_CIK},
    )
    assert source.fact_index is None, (
        "the four-quarter build costs minutes and must stay off the "
        "construction path"
    )


def test_a_missing_cache_contributes_no_rows(tmp_path: Path) -> None:
    module = _module()
    source = module.SecFundamentalsDepthSource(
        cache_dir=tmp_path / "no-such-cache",
        cik_by_symbol={"AAPL": AAPL_CIK},
    )
    assert asyncio.run(source.fetch_many(["AAPL"])) == {}


def test_the_default_quarters_are_discovered_from_the_cache(tmp_path: Path) -> None:
    module = _module()
    for label in _recent_labels(4):
        _write_quarter(tmp_path, label)
    source = module.SecFundamentalsDepthSource(
        cache_dir=tmp_path, cik_by_symbol={"AAPL": AAPL_CIK}
    )
    values = asyncio.run(source.fetch_many(["AAPL"]))
    assert values["AAPL"].fields["sales"] == pytest.approx(416_161_000_000)


def test_fetch_many_publishes_the_fundamentals_row(tmp_path: Path) -> None:
    module = _module()
    _write_quarter(tmp_path, "2026q3")
    source = module.SecFundamentalsDepthSource(
        cache_dir=tmp_path,
        quarters=("2026q3",),
        cik_by_symbol={"AAPL": AAPL_CIK},
    )
    values = asyncio.run(source.fetch_many(["AAPL", "ZZZZ"]))

    row = values["AAPL"]
    assert row.source == "sec_bulk"
    assert row.fields["sales"] == pytest.approx(416_161_000_000)
    assert row.fields["net_income"] == pytest.approx(111_000_000_000), (
        "the later-filed restatement wins"
    )
    assert row.fields["eps"] == pytest.approx(7.46)
    assert row.fields["roa"] == pytest.approx(111_000_000_000 / 364_980_000_000)
    assert row.fields["shares_outstanding"] == pytest.approx(14_800_000_000)
    assert "gross_margin" not in row.fields, "an absent field is not a zero"
    assert "market_cap" not in row.fields and "pe" not in row.fields, (
        "without a price reference the two price-dependent fields stay absent"
    )
    assert "ZZZZ" not in values, "a symbol the CIK map does not carry is silent"


def test_the_price_reference_fills_market_cap_and_pe(tmp_path: Path) -> None:
    module = _module()
    _write_quarter(tmp_path, "2026q3")
    for price in ({"AAPL": 250.0}, lambda: {"AAPL": 250.0}):
        source = module.SecFundamentalsDepthSource(
            cache_dir=tmp_path,
            quarters=("2026q3",),
            cik_by_symbol={"AAPL": AAPL_CIK},
            price=price,
        )
        values = asyncio.run(source.fetch_many(["AAPL"]))
        assert values["AAPL"].fields["market_cap"] == pytest.approx(
            250.0 * 14_800_000_000
        )
        assert values["AAPL"].fields["pe"] == pytest.approx(250.0 / 7.46)


def test_a_price_that_is_not_a_quote_is_ignored(tmp_path: Path) -> None:
    module = _module()
    _write_quarter(tmp_path, "2026q3")
    source = module.SecFundamentalsDepthSource(
        cache_dir=tmp_path,
        quarters=("2026q3",),
        cik_by_symbol={"AAPL": AAPL_CIK},
        price={"AAPL": "250"},  # a string is not a quote
    )
    values = asyncio.run(source.fetch_many(["AAPL"]))
    assert "market_cap" not in values["AAPL"].fields
    assert "pe" not in values["AAPL"].fields
    assert values["AAPL"].fields["sales"] == pytest.approx(416_161_000_000), (
        "one unusable quote must not sink the symbol's other fields"
    )


def test_symbols_are_canonicalized_before_the_lookup(tmp_path: Path) -> None:
    module = _module()
    _write_quarter(tmp_path, "2026q3")
    source = module.SecFundamentalsDepthSource(
        cache_dir=tmp_path,
        quarters=("2026q3",),
        # BRK-B deliberately maps to Apple's CIK: the row is the point, the
        # canonical spelling of the key is what is under test.
        cik_by_symbol={"AAPL": AAPL_CIK, "BRK-B": AAPL_CIK},
    )
    values = asyncio.run(source.fetch_many(["aapl", "BRK.B"]))
    assert set(values) == {"AAPL", "BRK-B"}
    assert values["BRK-B"].fields["sales"] == pytest.approx(416_161_000_000)


def test_the_index_is_built_once_on_the_first_fetch(tmp_path: Path, monkeypatch: Any) -> None:
    module = _module()
    _write_quarter(tmp_path, "2026q3")
    real = module.build_quarters
    calls: list[tuple[str, ...]] = []

    def counting(paths: Sequence[Any], *, ciks: Any = None) -> Any:
        calls.append(tuple(str(path) for path in paths))
        return real(paths, ciks=ciks)

    monkeypatch.setattr(module, "build_quarters", counting)
    source = module.SecFundamentalsDepthSource(
        cache_dir=tmp_path,
        quarters=("2026q3",),
        cik_by_symbol={"AAPL": AAPL_CIK},
    )
    assert calls == []

    asyncio.run(source.fetch_many(["AAPL"]))
    asyncio.run(source.fetch_many(["AAPL"]))

    assert len(calls) == 1, "the index is built once and reused"
    assert source.fact_index is not None


# ---------------------------------------------------------------------------
# The collector wiring
# ---------------------------------------------------------------------------


def test_build_depth_collector_gates_the_sec_bulk_source() -> None:
    from app.market.depth import build_depth_collector

    excluded = _names(build_depth_collector(_settings(depth_sec_bulk=False), ["AAPL"]))
    assert "sec_bulk" not in excluded

    _module()  # the default path needs the new module to exist
    included = _names(build_depth_collector(_settings(), ["AAPL"]))
    assert "sec_bulk" in included


def test_build_depth_collector_can_drop_the_keyed_web_sources() -> None:
    from app.market.depth import build_depth_collector

    keyless = _names(
        build_depth_collector(
            _settings(depth_yahoo_full=False, depth_stockanalysis=False), ["AAPL"]
        )
    )
    assert "yahoo_full" not in keyless
    assert "stockanalysis" not in keyless

    default = _names(build_depth_collector(_settings(), ["AAPL"]))
    assert "yahoo_full" in default
    assert "stockanalysis" in default


def test_build_depth_collector_wires_the_finra_average_volume_reference(
    monkeypatch: Any,
) -> None:
    from app.market import finra as finra_module
    from app.market.depth import build_depth_collector

    async def fetcher(*, settlement: str, limit: int, offset: int) -> list[dict[str, Any]]:
        return [
            {
                "symbolCode": "AAPL",
                "currentShortPositionQuantity": 4_000_000,
                "settlementDate": settlement,
            }
        ]

    class WiredFinra(finra_module.FinraShortSource):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(settlement_date="2026-10-15", fetcher=fetcher, **kwargs)

    monkeypatch.setattr(finra_module, "FinraShortSource", WiredFinra)
    module = _module()
    monkeypatch.setattr(
        module, "yahoo_average_volume", lambda symbols: {"AAPL": 2_000_000.0}
    )

    # depth_sec_bulk is off so this test fails on the wiring itself, not on a
    # module the RED phase has not written yet.
    collector = build_depth_collector(_settings(depth_sec_bulk=False), ["AAPL"])
    finra = next(source for source in collector.sources if source.name == "finra")
    values = asyncio.run(finra.fetch_many(["AAPL"]))

    assert values["AAPL"].fields["short_interest"] == 4_000_000
    assert values["AAPL"].fields["short_ratio"] == pytest.approx(2.0), (
        "short_interest / average_volume, with the divisor from the reference"
    )


def test_the_finra_ratio_stays_absent_without_a_volume_reference(
    monkeypatch: Any,
) -> None:
    from app.market import finra as finra_module
    from app.market.depth import build_depth_collector

    async def fetcher(*, settlement: str, limit: int, offset: int) -> list[dict[str, Any]]:
        return [
            {
                "symbolCode": "AAPL",
                "currentShortPositionQuantity": 4_000_000,
                "settlementDate": settlement,
            }
        ]

    class WiredFinra(finra_module.FinraShortSource):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(settlement_date="2026-10-15", fetcher=fetcher, **kwargs)

    monkeypatch.setattr(finra_module, "FinraShortSource", WiredFinra)
    module = _module()
    monkeypatch.setattr(module, "yahoo_average_volume", lambda symbols: {})

    collector = build_depth_collector(_settings(depth_sec_bulk=False), ["AAPL"])
    finra = next(source for source in collector.sources if source.name == "finra")
    values = asyncio.run(finra.fetch_many(["AAPL"]))

    assert values["AAPL"].fields["short_interest"] == 4_000_000
    assert "short_ratio" not in values["AAPL"].fields, "absent, never zero"


# ---------------------------------------------------------------------------
# The Yahoo v7 average-volume reference
# ---------------------------------------------------------------------------


def test_the_average_volume_reference_reads_the_yahoo_v7_batch(
    monkeypatch: Any,
) -> None:
    from tests._market_quote import install_seam, yahoo_row, yahoo_route
    from tests._phase2 import MockWeb

    module = _module()
    web = MockWeb()
    row = yahoo_row("AAPL")
    row["averageDailyVolume3Month"] = 1_234_567
    yahoo_route(web, {"AAPL": row})
    install_seam(monkeypatch, web)

    assert module.yahoo_average_volume(["AAPL"]) == {"AAPL": 1_234_567.0}


def test_the_average_volume_reference_is_absent_when_yahoo_cannot_answer(
    monkeypatch: Any,
) -> None:
    from tests._market_quote import install_seam, yahoo_route
    from tests._phase2 import MockWeb

    module = _module()
    web = MockWeb()
    yahoo_route(web, {}, status=500)
    install_seam(monkeypatch, web)

    assert module.yahoo_average_volume(["AAPL"]) == {}


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def test_settings_expose_the_keyless_depth_flags(monkeypatch: Any) -> None:
    from app.config import Settings

    settings = Settings()
    assert settings.depth_sec_bulk is True
    assert getattr(settings, "depth_yahoo_full", False) is True, (
        "depth_yahoo_full must default on"
    )
    assert getattr(settings, "depth_stockanalysis", False) is True, (
        "depth_stockanalysis must default on"
    )

    monkeypatch.setenv("MEE_DEPTH_YAHOO_FULL", "0")
    monkeypatch.setenv("MEE_DEPTH_STOCKANALYSIS", "0")
    off = Settings()
    assert off.depth_yahoo_full is False
    assert off.depth_stockanalysis is False
