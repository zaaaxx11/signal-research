"""P11F-B — Finviz is the free US valuation rung: scrape, map, never invent.

Finviz answers ``https://finviz.com/stock?t=SYMBOL`` with one HTML page whose
snapshot table is a list of ``label → value`` cells (``P/E``, ``Forward P/E``,
``PEG``, ``P/S``, ``P/B``, ``P/FCF``, ``EV/EBITDA``, ``EPS (ttm)``, ``ROE``,
``Debt/Eq``…). It is a **scrape**, so the adapter carries trust 3, is declared
US-listed only, and never pretends the page is JSON:

* ``200`` + HTML is the *expected* answer, not ``blocked`` — the shared
  :func:`app.providers.adapters._support.envelope_verdict` would call every
  successful Finviz response a bot wall, so this adapter reads the statuses
  itself and then looks for the challenge markers Cloudflare actually serves
  ("Just a moment…", ``cf-chl``, ``challenge-platform``). A challenge page is
  ``blocked``; a page with no snapshot table is ``schema_drift``; a table whose
  every mapped value is ``-`` is ``empty``.
* **A missing value is no observation.** ``-``, ``N/A``, an empty cell and a
  label that is simply absent all produce **zero** rows. A P/E of ``0.00`` in
  the page is a real zero and is kept; a dash is not a zero.
* **Units follow the store, not the provider.** ``debt_to_equity`` arrives as a
  ratio (``0.78``) where the store keeps percent points (Yahoo publishes
  ``78.445`` for the same thing, and :mod:`app.fundamentals.scan` multiplies
  TradingView's ratio by 100 for exactly this reason), so it is multiplied once,
  here. ``dividend_yield`` is the parenthesised yield of ``Dividend TTM``
  (``1.06 (0.30%)`` → ``0.30``); ``payout_ratio`` and ``roe`` are already
  percent.
* ``P/FCF`` is a valuation multiple with **no canonical column**. It is parsed
  and exposed through :func:`app.providers.adapters.finviz.parse_metrics`, and
  deliberately **not** emitted as an observation: an ``Observation.field`` that
  is not one of the 92 columns is a contract violation, not a new column.

The fixture is a verbatim trim of the live page captured 2026-10-25 (Apple), so
the expected numbers below are the provider's own, not a guess.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx2
import pytest

from app.contracts import CANONICAL_COLUMNS_V1, Capability
from app.providers.base import InstrumentRef

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "finviz_stock_aapl.html"

INSTRUMENT = InstrumentRef.from_symbol("AAPL", venue="XNAS")

#: The 12 valuation cells this rung promises, with the provider's own numbers.
EXPECTED: dict[str, float] = {
    "pe_ttm": 39.86,
    "pe_forward": 36.24,
    "peg": 2.88,
    "ps": 10.87,
    "pb": 47.25,
    "ev_ebitda": 30.34,
    "eps_ttm": 8.72,
    "dividend_yield": 0.30,
    "payout_ratio": 13.66,
    "roe": 148.75,
    "debt_to_equity": 78.0,  # Finviz prints 0.78; the store keeps percent points
    "revenue_ttm": 466_820_000_000.0,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def module() -> Any:
    import importlib

    return importlib.import_module("app.providers.adapters.finviz")


def adapter() -> Any:
    return module().FinvizAdapter()


def html() -> str:
    return FIXTURE.read_text(encoding="utf-8")


def raw_html(markup: str, *, status: int = 200) -> dict[str, Any]:
    """A ``RawResponse`` shaped like ``HttpAdapter.fetch`` builds one."""

    return {
        "status": status,
        "headers": {"content-type": "text/html; charset=utf-8"},
        "body": markup.encode("utf-8"),
        "url": "https://finviz.com/stock?t=AAPL",
        "capability": "fundamentals",
    }


def page(*rows: tuple[str, str]) -> str:
    """A Finviz-shaped page: one ``tr`` per label with the real class names."""

    cells = "\n".join(
        '<tr class="table-dark-row">\n'
        '<td class="snapshot-td2" align="left"><div class="snapshot-td-label">'
        f"{label}</div></td>"
        '<td class="snapshot-td2" align="left"><div class="snapshot-td-content">'
        f"<b>{value}</b></div></td>\n</tr>"
        for label, value in rows
    )
    return (
        "<!DOCTYPE html><html><body><div class=\"content\">"
        '<table class="js-snapshot-table snapshot-table2">'
        f"{cells}</table></div></body></html>"
    )


def parsed(markup: str | None = None) -> dict[str, Any]:
    instance = adapter()
    raw = raw_html(html() if markup is None else markup)
    out: dict[str, Any] = {}
    for item in instance.parse(raw, INSTRUMENT):
        value = item.get("value_num")
        if value is None:
            value = item.get("value_text")
        out[str(item["field"])] = value
    return out


# ---------------------------------------------------------------------------
# Capability and wiring
# ---------------------------------------------------------------------------


def test_adapter_declares_fundamentals_as_a_trust_3_scrape() -> None:
    instance = adapter()
    assert instance.name == "finviz"
    assert Capability.FUNDAMENTALS in instance.capabilities, sorted(
        capability.value for capability in instance.capabilities
    )
    assert instance.trust == 3, (
        "a scraped quote page is an aggregator (02 §4.6 trust 3), not a vendor: "
        f"{instance.trust}"
    )


def test_registry_declares_finviz_free_delayed_and_us_only() -> None:
    from app.providers.adapters import ADAPTER_NAMES, build_adapters
    from app.providers.registry import ladder_for, provider_named

    spec = provider_named("finviz")
    assert spec is not None, "REGISTRY does not declare finviz"
    assert Capability.FUNDAMENTALS in spec.capabilities
    assert spec.cost_tier == "T0" and spec.trust == 3 and spec.delay == "delayed"
    assert spec.auth is None, "the quote page needs no key; it needs a polite UA"
    assert spec.supports("equity", "XNAS") is True
    assert spec.supports("equity", "XTSE") is False, (
        "Finviz lists US issuers only; a global symbol must be skipped, not sent "
        "to a page that will answer with someone else's company"
    )

    rungs = [entry.name for entry in ladder_for(Capability.FUNDAMENTALS)]
    assert "finviz" in rungs, rungs
    assert rungs.index("sec_edgar") < rungs.index("yahoo") < rungs.index("finviz"), (
        "official before unofficial, and a scrape after the vendor that owns the "
        f"same field: {rungs}"
    )

    assert "finviz" in ADAPTER_NAMES, sorted(ADAPTER_NAMES)
    assert "finviz" in build_adapters()


def test_fetch_reads_the_stock_page_once(monkeypatch: Any) -> None:
    from tests._phase2 import MockWeb, install_web

    web = MockWeb()
    web.route("finviz.com", lambda _request: httpx2.Response(200, text=html()))
    install_web(monkeypatch, web)

    instance = adapter()
    raw = asyncio.run(
        instance.fetch(Capability.FUNDAMENTALS, INSTRUMENT, set(CANONICAL_COLUMNS_V1))
    )

    assert raw.get("capability") == "fundamentals"
    assert int(raw.get("status") or 0) == 200
    assert len(web.requests) == 1, [record.url for record in web.requests]
    url = web.requests[0].url
    assert "/stock?t=AAPL" in url, (
        "``/quote?t=`` answers 301 (and this client never follows a redirect); "
        f"the quote page is ``/stock?t=``: {url}"
    )
    assert "user-agent" in web.requests[0].headers


def test_a_class_share_symbol_is_sent_with_a_hyphen() -> None:
    instance = adapter()
    url = instance.url(InstrumentRef.from_symbol("BRK.B", venue="XNYS"))
    assert url.endswith("/stock?t=BRK-B"), (
        "Finviz spells the share class with a hyphen, like Yahoo; BRK.B is the "
        f"screen/export spelling and returns no page: {url}"
    )


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def test_parse_fills_every_valuation_column() -> None:
    values = parsed()

    missing = [column for column in EXPECTED if column not in values]
    assert not missing, (
        f"the stock page left these cells empty: {missing}; it produced {sorted(values)}"
    )
    assert set(values) <= set(CANONICAL_COLUMNS_V1), (
        f"the parser emitted a non-canonical field: {sorted(set(values) - set(CANONICAL_COLUMNS_V1))}"
    )
    for column, expected in EXPECTED.items():
        assert values[column] == pytest.approx(expected), (
            f"{column}: adapter produced {values[column]!r}, the page says {expected!r}"
        )


def test_parse_metrics_exposes_p_fcf_without_inventing_a_column() -> None:
    metrics = module().parse_metrics(html())

    assert metrics["p_fcf"] == pytest.approx(37.13), (
        "P/FCF is printed on the page and parsed; it just has no canonical "
        f"column: {metrics.get('p_fcf')!r}"
    )
    assert "p_fcf" not in parsed(), (
        "an Observation.field must be one of the 92 columns; P/FCF cannot be "
        "emitted as one"
    )


def test_observations_carry_finviz_trust_3_and_are_time_stamped() -> None:
    observations = adapter().parse(raw_html(html()), INSTRUMENT)
    assert observations

    for item in observations:
        assert item["source"] == "finviz", item["source"]
        assert item["trust"] == 3, item["trust"]
        assert item["verdict"] == "ok"
        assert item["instrument_id"] == "XNAS:AAPL"
        assert item["as_of"], "an observation without as_of is not allowed to exist"

    by_field = {str(item["field"]): item for item in observations}
    assert by_field["eps_ttm"]["unit"] == "price:USD", by_field["eps_ttm"]["unit"]
    assert by_field["revenue_ttm"]["unit"] == "price:USD"
    assert by_field["roe"]["unit"] == "pct"
    assert by_field["dividend_yield"]["unit"] == "pct"
    assert by_field["payout_ratio"]["unit"] == "pct"
    assert by_field["pe_ttm"]["unit"] == "ratio"
    assert by_field["debt_to_equity"]["unit"] == "ratio"


def test_a_missing_value_is_no_observation_never_a_zero() -> None:
    values = parsed(
        page(
            ("P/E", "-"),
            ("Forward P/E", "N/A"),
            ("PEG", ""),
            ("P/S", "10.87"),
            ("P/B", "0.00"),
        )
    )

    for column in ("pe_ttm", "pe_forward", "peg"):
        assert column not in values, (
            f"{column}: a dash is 'the provider did not publish it', not a value "
            f"of {values.get(column)!r}"
        )
    assert values["ps"] == pytest.approx(10.87)
    assert values["pb"] == pytest.approx(0.0), "a printed 0.00 is a real zero"


def test_suffixes_and_thousands_separators_are_magnitudes_not_strings() -> None:
    values = parsed(
        page(
            ("Sales", "466.82B"),
            ("Market Cap", "5.07T"),
            ("Avg Volume", "44.06M"),
            ("Volume", "39,539,912"),
            ("ROE", "148.75%"),
        )
    )
    assert values["revenue_ttm"] == pytest.approx(466_820_000_000.0)
    assert values["roe"] == pytest.approx(148.75)


def test_the_dividend_yield_is_the_percentage_in_parentheses() -> None:
    values = parsed(
        page(("Dividend TTM", "1.06 (0.30%)"), ("Payout", "13.66%"))
    )
    assert values["dividend_yield"] == pytest.approx(0.30), (
        "``1.06 (0.30%)`` is a 1.06 annual dividend and a 0.30% yield; storing "
        f"1.06 would be a yield of 106%: {values.get('dividend_yield')!r}"
    )
    assert values["payout_ratio"] == pytest.approx(13.66)


def test_debt_to_equity_is_scaled_once_into_percent_points() -> None:
    values = parsed(page(("Debt/Eq", "0.78")))
    assert values["debt_to_equity"] == pytest.approx(78.0), (
        "the store keeps percent points (Yahoo: 78.445); Finviz prints the "
        f"ratio 0.78, so it is multiplied by 100 exactly once: {values.get('debt_to_equity')!r}"
    )


def test_a_value_that_is_not_a_number_is_skipped() -> None:
    values = parsed(page(("P/E", "Yes / Yes"), ("PEG", "n/a"), ("P/B", "47.25")))
    assert "pe_ttm" not in values and "peg" not in values
    assert values["pb"] == pytest.approx(47.25)


def test_a_page_without_the_snapshot_table_parses_to_nothing() -> None:
    assert adapter().parse(raw_html("<html><body><p>nothing here</p></body></html>"), INSTRUMENT) == []


# ---------------------------------------------------------------------------
# Verdicts
# ---------------------------------------------------------------------------


def test_a_cloudflare_challenge_page_is_blocked() -> None:
    instance = adapter()
    challenge = (
        "<!DOCTYPE html><html><head><title>Just a moment...</title></head>"
        "<body><div id=\"cf-challenge-running\">Enable JavaScript and cookies to "
        "continue</div><script src=\"/cdn-cgi/challenge-platform/h/b/orchestrate\">"
        "</script></body></html>"
    )
    verdict = instance.verdict(raw_html(challenge), INSTRUMENT)
    assert verdict.ok is False and verdict.kind == "blocked", (
        "a Cloudflare interstitial is a bot wall, not a page with no P/E: "
        f"{verdict}"
    )


def test_statuses_map_to_the_shared_verdict_kinds() -> None:
    instance = adapter()
    assert instance.verdict(raw_html("<html></html>", status=403), INSTRUMENT).kind == "authwall"
    assert instance.verdict(raw_html("<html></html>", status=401), INSTRUMENT).kind == "authwall"
    assert instance.verdict(raw_html("<html></html>", status=404), INSTRUMENT).kind == "not_found"
    assert instance.verdict(raw_html("<html></html>", status=429), INSTRUMENT).kind == "throttled"
    assert instance.verdict(raw_html("<html></html>", status=503), INSTRUMENT).kind == "5xx"
    assert instance.verdict(raw_html("<html></html>", status=301), INSTRUMENT).kind == "blocked"


def test_a_200_html_page_is_ok_when_it_carries_the_table() -> None:
    verdict = adapter().verdict(raw_html(html()), INSTRUMENT)
    assert verdict.ok is True and verdict.kind == "ok", (
        "the shared envelope verdict calls every HTML body 'blocked'; a Finviz "
        f"success is an HTML body: {verdict}"
    )


def test_html_without_the_table_is_schema_drift_and_all_dashes_is_empty() -> None:
    instance = adapter()

    drift = instance.verdict(raw_html("<html><body><h1>AAPL</h1></body></html>"), INSTRUMENT)
    assert drift.ok is False and drift.kind == "schema_drift", drift

    empty = instance.verdict(raw_html(page(("P/E", "-"), ("PEG", "-"))), INSTRUMENT)
    assert empty.ok is False and empty.kind == "empty", (
        "the table is there and every mapped cell is a dash: there is no "
        f"observation to make, which is 'empty' rather than 'ok': {empty}"
    )
