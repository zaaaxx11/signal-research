"""P11F-B — SEC EDGAR is the official fundamentals rung, and it is trust 1.

``REGISTRY`` has declared ``sec_edgar`` as a ``T0``/trust 1 ``FUNDAMENTALS`` rung
since Phase 2, but no adapter existed, so the ladder reported it ``skipped``.
This suite is the contract that rung has to meet.

Two requests, and only two
--------------------------
Ticker → CIK comes from ``https://www.sec.gov/files/company_tickers.json``
(one fetch, cached per process) and the numbers come from
``https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json`` (one fetch per
symbol). The CIK is zero-padded to ten digits because that is the path the API
publishes; an unpadded CIK is a 404, not a different document.

What is computed, and from what
-------------------------------
Every column is a function of the filings alone; nothing is copied from a
vendor that already did the arithmetic:

* ``revenue_ttm`` / ``net_income_ttm`` — trailing twelve months, computed as
  ``last fiscal year + YTD this year − YTD the same period last year``. That
  identity is the only TTM that works for a 10-Q, which reports a six- or
  nine-month **year-to-date** figure rather than a quarter. When no 10-K exists
  yet, four consecutive quarters are summed; when neither exists, the last
  fiscal year is the answer (a TTM nobody can compute is not invented).
* ``ebitda_ttm`` — **only** when ``OperatingIncomeLoss`` and a depreciation and
  amortisation concept are both present: EBITDA is not a us-gaap tag, so a
  missing leg is a missing cell, never an operating income presented as EBITDA.
* ``fcf_ttm`` — **only** ``NetCashProvidedByUsedInOperatingActivities`` minus
  ``PaymentsToAcquirePropertyPlantAndEquipment``. Capital expenditure is a
  cash-flow line, not "investing activities".
* ``total_debt`` — only exact debt concepts (long-term debt, its current
  portion, commercial paper, short-term borrowings). ``Liabilities`` is not
  debt; a company that does not tag debt gets a null, not total liabilities.
* ``cash_and_equivalents`` — ``CashAndCashEquivalentsAtCarryingValue`` only.
* ``roe`` — ``net_income_ttm / StockholdersEquity``; ``debt_to_equity`` —
  ``Liabilities / StockholdersEquity``. Both are **percent points**, the scale
  :mod:`app.fundamentals.scan` already normalises TradingView's ratio to, and
  both need the same balance date on each side.

Null is a fact
--------------
Every rule above has a test that removes one leg and asserts the cell is
**absent** rather than zero, half-computed, or borrowed from another concept.
The fixture is a **trim** of the live ``companyfacts`` document for Apple
(CIK 320193, captured 2026-10-25): only the concepts this adapter reads, only
``10-K``/``10-Q`` rows from 2024 onward. The expected TTM values are the ones
that trim produces, and they are cross-checked against Finviz's independently
computed TTM Sales (466.82B) and Income (128.93B) on the same day.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Mapping

import httpx2
import pytest

from app.contracts import CANONICAL_COLUMNS_V1, Capability
from app.providers.base import InstrumentRef

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "sec_companyfacts_aapl.json"

INSTRUMENT = InstrumentRef.from_symbol("AAPL", venue="XNAS")

#: AAPL's fiscal 2026 Q3 balance date — the ``as_of`` every TTM cell carries.
LATEST_PERIOD_END = "2026-06-27T00:00:00+00:00"

#: ``company_tickers.json`` answers an object keyed by index, not an array.
TICKERS = {
    "0": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA CORP"},
    "1": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "2": {"cik_str": 1067983, "ticker": "BRK-B", "title": "BERKSHIRE HATHAWAY INC"},
}

#: The columns this rung promises. The rest of the 22 fundamentals cells are a
#: price/vendor question (market cap, P/E, shares) and stay the Yahoo rung's job.
STATEMENT_COLUMNS = (
    "revenue_ttm",
    "ebitda_ttm",
    "net_income_ttm",
    "fcf_ttm",
    "total_debt",
    "cash_and_equivalents",
    "roe",
    "debt_to_equity",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def adapter() -> Any:
    """A fresh adapter per test: the ticker cache must not leak between tests."""

    from app.providers.adapters.sec import SecEdgarAdapter

    return SecEdgarAdapter()


def facts() -> dict[str, Any]:
    """A fresh deep copy of the trimmed live ``companyfacts`` document."""

    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def raw_of(document: Any, *, status: int = 200, capability: str = "fundamentals") -> dict[str, Any]:
    """A ``RawResponse`` shaped exactly like ``HttpAdapter.fetch`` builds one."""

    return {
        "status": status,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(document).encode("utf-8"),
        "url": "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json",
        "capability": capability,
    }


def html_raw(markup: str, *, status: int = 200) -> dict[str, Any]:
    return {
        "status": status,
        "headers": {"content-type": "text/html; charset=utf-8"},
        "body": markup.encode("utf-8"),
        "url": "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json",
        "capability": "fundamentals",
    }


def values(document: Any) -> dict[str, Any]:
    """``{field: number-or-text}`` from one parse of a companyfacts body."""

    instance = adapter()
    raw = raw_of(document)
    return _values(instance.parse(raw, INSTRUMENT))


def _values(observations: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for item in observations:
        name = str(item.get("field") or "")
        value = item.get("value_num")
        if value is None:
            value = item.get("value_text")
        out[name] = value
    return out


def documents_without(*concepts: str) -> dict[str, Any]:
    document = facts()
    for concept in concepts:
        document["facts"]["us-gaap"].pop(concept, None)
    return document


def units_of(document: Mapping[str, Any], concept: str, unit: str = "USD") -> list[dict[str, Any]]:
    return document["facts"]["us-gaap"][concept]["units"][unit]


# ---------------------------------------------------------------------------
# Capability and wiring
# ---------------------------------------------------------------------------


def test_adapter_declares_fundamentals_and_trust_1() -> None:
    instance = adapter()
    assert instance.name == "sec_edgar"
    assert Capability.FUNDAMENTALS in instance.capabilities, (
        "REGISTRY lists sec_edgar as an L1 FUNDAMENTALS rung; an adapter that does "
        f"not declare it only moves the rung from 'no adapter' to 'does not "
        f"implement': {sorted(c.value for c in instance.capabilities)}"
    )
    assert instance.trust == 1, (
        "SEC is the official source (02 §4.6 trust 1); advertising it as an "
        f"aggregator would make the provenance column lie: {instance.trust}"
    )


def test_registry_puts_sec_edgar_first_for_fundamentals_and_wires_the_adapter() -> None:
    from app.providers.adapters import ADAPTER_NAMES, build_adapters
    from app.providers.registry import ladder_for, provider_named

    rungs = [entry.name for entry in ladder_for(Capability.FUNDAMENTALS)]
    assert rungs[0] == "sec_edgar", (
        "02 §3.4 orders official before unofficial at the same price: " f"{rungs}"
    )

    spec = provider_named("sec_edgar")
    assert spec is not None
    assert spec.cost_tier == "T0" and spec.trust == 1, (spec.cost_tier, spec.trust)
    assert spec.auth is None, "EDGAR needs no credential; only a contactable User-Agent"

    assert spec.supports("equity", "XNAS") is True
    assert spec.supports("equity", "XTSE") is False, (
        "a foreign listing has no CIK; answering not_found is terminal, so the "
        "rung would end the walk before Yahoo is asked"
    )
    assert spec.supports("etf", "XNAS") is False, (
        "a fund files N-CSR, not a 10-K: it must be skipped, not failed"
    )

    assert "sec_edgar" in ADAPTER_NAMES, sorted(ADAPTER_NAMES)
    adapters = build_adapters()
    assert "sec_edgar" in adapters and adapters["sec_edgar"].name == "sec_edgar"


def test_fetch_refuses_a_capability_it_does_not_declare() -> None:
    from app.providers.base import ProviderError

    with pytest.raises(ProviderError):
        asyncio.run(adapter().fetch(Capability.QUOTE, INSTRUMENT, {"last_price"}))


def test_a_global_symbol_skips_the_sec_rung_and_reaches_the_vendor(monkeypatch: Any) -> None:
    """The gate is what keeps L1 from ending the walk for a foreign listing."""

    from app.providers.adapters import build_adapters
    from app.providers.ladder import Ladder
    from app.providers.registry import REGISTRY
    from tests import _wiring as W
    from tests._phase2 import Instrument, MockWeb, install_web

    web = MockWeb()
    web.route("yahoo", lambda _request: httpx2.Response(200, json=W.quote_summary()))
    install_web(monkeypatch, web)

    ladder = Ladder(registry=REGISTRY, adapters=build_adapters())
    result = asyncio.run(
        ladder.fetch(
            Capability.FUNDAMENTALS,
            Instrument("7203.T", "7203.T", mic="XTSE"),
            {"revenue_ttm"},
        )
    )

    assert result.rung == "yahoo", (
        "a Tokyo listing has no CIK: the SEC rung must be skipped so the vendor "
        f"rung can answer, got rung={result.rung!r} verdict={result.verdict}"
    )
    assert not [record for record in web.requests if "sec.gov" in record.host], (
        "a skipped rung sends zero requests: "
        f"{[record.url for record in web.requests if 'sec.gov' in record.host]}"
    )
    assert result.observations, "the vendor rung answered with no observation"


# ---------------------------------------------------------------------------
# The two requests
# ---------------------------------------------------------------------------


def _install(monkeypatch: Any, *, tickers: Any = None, facts_body: Any = None) -> Any:
    from app.providers import http as provider_http
    from tests._phase2 import MockWeb, install_web

    web = MockWeb()
    web.route(
        "www.sec.gov",
        lambda _request: httpx2.Response(
            200, json=TICKERS if tickers is None else tickers
        ),
    )
    web.route(
        "data.sec.gov",
        lambda _request: httpx2.Response(
            200, json=facts() if facts_body is None else facts_body
        ),
    )
    install_web(monkeypatch, web)
    # The fake transport ignores the headers handed to ``build_client``, so the
    # clauses are captured here: the User-Agent is part of SEC's protocol.
    capture: dict[str, Any] = {}

    def build_client(**kwargs: Any) -> Any:
        capture.update(kwargs)
        return web.build_client(**kwargs)

    monkeypatch.setattr(provider_http, "build_client", build_client)
    web.captured = capture  # type: ignore[attr-defined]
    return web


def test_fetch_resolves_the_ticker_and_asks_for_the_padded_cik(monkeypatch: Any) -> None:
    web = _install(monkeypatch)
    instance = adapter()

    raw = asyncio.run(
        instance.fetch(Capability.FUNDAMENTALS, INSTRUMENT, set(CANONICAL_COLUMNS_V1))
    )

    assert raw.get("capability") == "fundamentals", raw.get("capability")
    assert int(raw.get("status") or 0) == 200

    ticker_calls = [record for record in web.requests if "company_tickers.json" in record.url]
    facts_calls = [record for record in web.requests if "/api/xbrl/companyfacts/" in record.url]
    assert len(ticker_calls) == 1, [r.url for r in ticker_calls]
    assert len(facts_calls) == 1, [r.url for r in facts_calls]
    assert ticker_calls[0].host == "www.sec.gov", ticker_calls[0].host
    assert facts_calls[0].host == "data.sec.gov", facts_calls[0].host
    assert facts_calls[0].url.endswith("/api/xbrl/companyfacts/CIK0000320193.json"), (
        "the companyfacts path needs the ten-digit CIK; 320193 is a 404: "
        f"{facts_calls[0].url}"
    )

    user_agent = str((web.captured.get("headers") or {}).get("user-agent") or "")
    assert "@" in user_agent, (
        "SEC answers 403 to an anonymous client: the User-Agent must carry a "
        f"contactable identity, got {user_agent!r}"
    )


def test_the_ticker_map_is_fetched_once_per_adapter(monkeypatch: Any) -> None:
    web = _install(monkeypatch)
    instance = adapter()

    for _ in range(2):
        asyncio.run(
            instance.fetch(Capability.FUNDAMENTALS, INSTRUMENT, set(CANONICAL_COLUMNS_V1))
        )

    ticker_calls = [record for record in web.requests if "company_tickers.json" in record.url]
    assert len(ticker_calls) == 1, (
        "company_tickers.json is a 10k-row document; re-reading it per symbol "
        f"spends a request on a constant: {[r.url for r in ticker_calls]}"
    )


def test_a_class_share_ticker_is_looked_up_with_a_hyphen(monkeypatch: Any) -> None:
    web = _install(monkeypatch)
    instance = adapter()

    asyncio.run(
        instance.fetch(
            Capability.FUNDAMENTALS,
            InstrumentRef.from_symbol("BRK.B", venue="XNYS"),
            set(CANONICAL_COLUMNS_V1),
        )
    )

    facts_calls = [record for record in web.requests if "/api/xbrl/companyfacts/" in record.url]
    assert len(facts_calls) == 1, [r.url for r in facts_calls]
    assert "CIK0001067983" in facts_calls[0].url, (
        "EDGAR spells the share class with a hyphen (BRK-B); BRK.B is a Yahoo "
        f"spelling and resolves to nothing: {facts_calls[0].url}"
    )


def test_an_unknown_ticker_is_not_found_and_spends_no_facts_request(monkeypatch: Any) -> None:
    web = _install(monkeypatch)
    instance = adapter()
    target = InstrumentRef.from_symbol("ZZZZ", venue="XNAS")

    raw = asyncio.run(instance.fetch(Capability.FUNDAMENTALS, target, {"revenue_ttm"}))
    verdict = instance.verdict(raw, target)

    assert verdict.ok is False and verdict.kind == "not_found", verdict
    assert not [r for r in web.requests if "/api/xbrl/companyfacts/" in r.url], (
        "an unknown ticker must not buy a second request"
    )
    assert instance.parse(raw, target) == []


# ---------------------------------------------------------------------------
# Statements
# ---------------------------------------------------------------------------


def test_parse_fills_every_statement_column_from_the_filings() -> None:
    parsed = values(facts())

    missing = [column for column in STATEMENT_COLUMNS if column not in parsed]
    assert not missing, (
        f"the companyfacts body left these cells empty: {missing}; it produced "
        f"{sorted(parsed)}"
    )
    assert set(parsed) <= set(CANONICAL_COLUMNS_V1), (
        f"the parser emitted a non-canonical field: {sorted(set(parsed) - set(CANONICAL_COLUMNS_V1))}"
    )

    # FY2025 + 9M FY2026 - 9M FY2025. The same identity, applied to the two
    # concepts Finviz also publishes, reproduces Finviz's TTM numbers exactly
    # (Sales 466.82B, Income 128.93B) — two independent implementations of one
    # arithmetic, not a copy of a vendor's answer.
    assert parsed["revenue_ttm"] == pytest.approx(466_823_000_000), parsed["revenue_ttm"]
    assert parsed["net_income_ttm"] == pytest.approx(128_930_000_000), parsed["net_income_ttm"]

    # EBITDA = operating income TTM + D&A TTM (154.859B + 13.100B).
    assert parsed["ebitda_ttm"] == pytest.approx(167_959_000_000), parsed["ebitda_ttm"]

    # FCF = CFO TTM (146.724B) - capex TTM (10.041B).
    assert parsed["fcf_ttm"] == pytest.approx(136_683_000_000), parsed["fcf_ttm"]

    # Exact debt concepts only: 71.340B non-current + 11.007B current + 1.997B
    # commercial paper. Liabilities (275.746B) is not this number.
    assert parsed["total_debt"] == pytest.approx(84_344_000_000), parsed["total_debt"]
    assert parsed["total_debt"] != pytest.approx(275_746_000_000)
    assert parsed["cash_and_equivalents"] == pytest.approx(39_544_000_000)

    assert parsed["roe"] == pytest.approx(128_930_000_000 / 107_520_000_000 * 100.0)
    assert parsed["debt_to_equity"] == pytest.approx(275_746_000_000 / 107_520_000_000 * 100.0)


def test_observations_carry_sec_edgar_trust_1_and_the_period_end() -> None:
    observations = adapter().parse(raw_of(facts()), INSTRUMENT)
    assert observations, "the fixture produced no observation at all"

    by_field = {str(item["field"]): item for item in observations}
    assert set(by_field) == set(STATEMENT_COLUMNS), sorted(by_field)

    for name, item in by_field.items():
        assert item["source"] == "sec_edgar", (name, item["source"])
        assert item["trust"] == 1, (name, item["trust"])
        assert item["verdict"] == "ok"
        assert item["instrument_id"] == "XNAS:AAPL"
        assert str(item["as_of"]) == LATEST_PERIOD_END, (
            f"{name}: as_of must be the period the data describes, got {item['as_of']}"
        )

    assert by_field["revenue_ttm"]["unit"] == "price:USD", by_field["revenue_ttm"]["unit"]
    assert by_field["roe"]["unit"] == "pct", by_field["roe"]["unit"]
    assert by_field["debt_to_equity"]["unit"] == "ratio", by_field["debt_to_equity"]["unit"]


def test_ttm_uses_fiscal_year_plus_ytd_minus_prior_ytd_not_a_single_quarter() -> None:
    """A 10-Q carries a six- or nine-month YTD; a quarter is not a year."""

    document = facts()
    parsed = values(document)

    annual = 416_161_000_000  # FY2025, 10-K
    ytd = 364_357_000_000  # 9M FY2026, 10-Q
    prior_ytd = 313_695_000_000  # 9M FY2025, repeated in the FY2026 10-Q
    assert parsed["revenue_ttm"] == pytest.approx(annual + ytd - prior_ytd)

    latest_quarter = 109_417_000_000
    assert parsed["revenue_ttm"] != pytest.approx(annual + latest_quarter - 94_036_000_000), (
        "FY + one quarter - that quarter a year ago is not a trailing twelve "
        "months: it drops the two quarters between the fiscal year end and the "
        "latest one"
    )


def test_ebitda_needs_operating_income_and_depreciation_together() -> None:
    assert "ebitda_ttm" not in values(
        documents_without("OperatingIncomeLoss")
    ), "operating income is missing: EBITDA cannot be produced from D&A alone"

    assert "ebitda_ttm" not in values(
        documents_without("DepreciationDepletionAndAmortization")
    ), "D&A is missing: operating income is not EBITDA"

    only_operating = documents_without(
        "DepreciationDepletionAndAmortization",
        "DepreciationAndAmortization",
        "DepreciationAmortizationAndAccretionNet",
    )
    parsed = values(only_operating)
    assert "ebitda_ttm" not in parsed
    assert parsed.get("revenue_ttm") is not None, (
        "dropping the D&A tag must not take the other statements with it"
    )


def test_free_cash_flow_is_cfo_minus_capex_and_nothing_else() -> None:
    assert "fcf_ttm" not in values(
        documents_without("NetCashProvidedByUsedInOperatingActivities")
    ), "capex alone is not free cash flow"

    no_capex = documents_without(
        "PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"
    )
    assert "fcf_ttm" not in values(no_capex), (
        "without a capex tag the difference is operating cash flow, not FCF — "
        "publishing it as FCF overstates the number by the capex"
    )

    document = documents_without("PaymentsToAcquirePropertyPlantAndEquipment")
    assert "fcf_ttm" not in values(document)


def test_total_debt_uses_exact_debt_concepts_and_never_liabilities() -> None:
    without_debt = documents_without(
        "LongTermDebtNoncurrent",
        "LongTermDebtCurrent",
        "LongTermDebt",
        "CommercialPaper",
        "DebtCurrent",
    )
    parsed = values(without_debt)
    assert "total_debt" not in parsed, (
        "Liabilities is in the document, so a fallback would silently fill "
        f"total_debt with {parsed.get('total_debt')!r}; debt is a narrower fact"
    )
    assert parsed["debt_to_equity"] == pytest.approx(275_746_000_000 / 107_520_000_000 * 100.0), (
        "the balance-sheet ratio does not depend on the debt tags"
    )


def test_debt_to_equity_is_liabilities_over_equity_not_debt_over_equity() -> None:
    parsed = values(facts())
    expected = 275_746_000_000 / 107_520_000_000 * 100.0
    assert parsed["debt_to_equity"] == pytest.approx(expected)
    assert parsed["debt_to_equity"] != pytest.approx(84_344_000_000 / 107_520_000_000 * 100.0), (
        "the narrow debt sum is a different ratio; this column is the "
        "balance-sheet one"
    )


def test_ratios_are_percent_points_like_the_store_already_uses() -> None:
    parsed = values(facts())
    assert parsed["roe"] == pytest.approx(119.91257440476191), (
        "roe is net income / equity in percent points (Yahoo and the TradingView "
        f"scan both publish percent here), got {parsed['roe']!r}"
    )
    assert parsed["debt_to_equity"] == pytest.approx(256.46019345238096), (
        "debt_to_equity is a percent, not a fraction: the scan multiplies its "
        f"0.784 ratio by 100 for the same reason, got {parsed['debt_to_equity']!r}"
    )


def test_a_ratio_disappears_when_one_side_is_missing() -> None:
    no_equity = values(documents_without("StockholdersEquity"))
    assert "roe" not in no_equity, "net income alone is not a return on equity"
    assert "debt_to_equity" not in no_equity

    no_liabilities = values(documents_without("Liabilities"))
    assert "debt_to_equity" not in no_liabilities
    assert no_liabilities.get("roe") is not None, (
        "ROE does not need the liabilities side; dropping it must not empty ROE"
    )


def test_a_non_positive_equity_is_never_a_ratio() -> None:
    document = facts()
    for row in units_of(document, "StockholdersEquity"):
        if row["end"] == "2026-06-27":
            row["val"] = -5_000_000_000

    parsed = values(document)
    assert "roe" not in parsed and "debt_to_equity" not in parsed, (
        "a negative book value makes the ratio meaningless and a zero one makes "
        f"it infinite: {parsed.get('roe')!r} / {parsed.get('debt_to_equity')!r}"
    )


def test_other_forms_and_other_units_are_ignored() -> None:
    document = facts()
    equity = units_of(document, "StockholdersEquity")
    equity.append({"end": "2026-07-31", "val": 1, "accn": "x", "fy": 2026, "fp": "Q4",
                   "form": "8-K", "filed": "2026-08-01"})
    equity.append({"end": "2026-08-31", "val": 1, "accn": "y", "fy": 2026, "fp": "FY",
                   "form": "20-F", "filed": "2026-09-01"})
    document["facts"]["us-gaap"]["StockholdersEquity"]["units"]["EUR"] = [
        {"end": "2026-08-31", "val": 1, "accn": "z", "fy": 2026, "fp": "FY",
         "form": "10-Q", "filed": "2026-09-02"}
    ]

    parsed = values(document)
    assert parsed["roe"] == pytest.approx(119.91257440476191), (
        "an 8-K/20-F row or a EUR unit was read as the balance sheet: "
        f"{parsed.get('roe')!r}"
    )
    assert parsed["debt_to_equity"] == pytest.approx(256.46019345238096)


def test_the_latest_filed_restatement_wins() -> None:
    document = facts()
    units_of(document, "CashAndCashEquivalentsAtCarryingValue").append(
        {"end": "2026-06-27", "val": 40_000_000_000, "accn": "restated", "fy": 2026,
         "fp": "Q3", "form": "10-Q", "filed": "2026-08-15"}
    )
    assert values(document)["cash_and_equivalents"] == pytest.approx(40_000_000_000)


# ---------------------------------------------------------------------------
# Verdicts
# ---------------------------------------------------------------------------


def test_verdict_reads_the_body_not_only_the_status() -> None:
    instance = adapter()
    target = INSTRUMENT

    blocked = instance.verdict(
        html_raw("<!DOCTYPE html><html><body>Request Rate Threshold Exceeded</body></html>"),
        target,
    )
    assert blocked.ok is False and blocked.kind == "blocked", blocked

    missing = instance.verdict(html_raw("{}", status=404), target)
    assert missing.kind == "not_found", missing

    throttled = instance.verdict(html_raw("{}", status=429), target)
    assert throttled.kind == "throttled", throttled

    drift = instance.verdict(raw_of({"cik": 1, "entityName": "X"}), target)
    assert drift.ok is False and drift.kind == "schema_drift", drift

    empty = instance.verdict(
        raw_of({"cik": 1, "entityName": "X", "facts": {"us-gaap": {}}}), target
    )
    assert empty.ok is False and empty.kind == "empty", empty

    ok = instance.verdict(raw_of(facts()), target)
    assert ok.ok is True and ok.kind == "ok", ok


def test_a_malformed_body_parses_to_nothing_instead_of_raising() -> None:
    instance = adapter()
    assert instance.parse(raw_of({"facts": "nope"}), INSTRUMENT) == []
    assert instance.parse(raw_of({"facts": {"us-gaap": {"Revenues": 3}}}), INSTRUMENT) == []
    assert instance.parse(html_raw("<html></html>"), INSTRUMENT) == []


def test_a_payload_with_no_usable_fact_is_empty_not_ok() -> None:
    instance = adapter()
    document = facts()
    document["facts"]["us-gaap"] = {
        "Revenues": {
            "units": {
                "EUR": [{"start": "2024-01-01", "end": "2024-12-31", "val": 1,
                         "form": "10-K", "filed": "2025-02-01"}]
            }
        }
    }
    verdict = instance.verdict(raw_of(document), INSTRUMENT)
    assert verdict.ok is False and verdict.kind == "empty", verdict
