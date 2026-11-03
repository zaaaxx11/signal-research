"""TDD tests for ``GET /insider-flow/{symbol}`` — honest Form 4 buy-vs-sell flow.

Rules under test, decided here and documented in ``app/market/insider_flow.py``:

* ``P`` is an open-market **purchase** and counts as a buy. ``S`` is a **sale**.
* ``A`` (award), ``M`` (option exercise), ``G`` (gift), ``C`` (conversion), ``F``
  (tax withholding) and ``D`` are *not* open-market activity: they are never
  folded into ``bought_shares``/``sold_shares``, only reported as ``filings`` and
  ``otherTransactions``.
* A row with no usable price still counts in the share totals and is excluded
  from the value totals; it is counted in ``unpricedFilings``.
* ``as_of`` is the newest filing date inside the window — never the clock.
* An empty window is an explicit empty with a named ``reason`` — never a 0.0.
* The window's boundary day is inclusive: ``days=30`` covers today and the
  29 days before it.
* ``days`` must be one of the supported windows, else the house
  ``{error, errorMessage}`` ``invalid_days`` envelope (422).
* The symbol is bound per request: the collector sees one symbol per call and
  no answer ever leaks another request's rows.

Everything is fabricated in-process — ``collect_insider`` is monkeypatched with
rows built from the real ``InsiderTransaction`` shape and no request leaves the
process.
"""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.engine.insider import InsiderTransaction
import app.market.insider_flow as mod
from app.main import app

WITHDRAWN_FIELD_QUOTE = (
    '"a vendor field whose own numbers cannot be read as a flow '
    '(TSLA 76.0, META 45.9, GME 8.7 — a mixture of ownership levels and junk)"'
)


@pytest.fixture
def client():
    return TestClient(app)


class _FakeMaster:
    """The slice of ``SecurityMaster`` this route reads: one lookup."""

    def __init__(self, rows):
        self._rows = {k.upper(): v for k, v in rows.items()}

    def get(self, symbol):
        return self._rows.get(str(symbol).upper())


def _record(symbol, cik="0000320193"):
    return SimpleNamespace(symbol=symbol.upper(), cik=cik)


@pytest.fixture(autouse=True)
def fake_engine_state(monkeypatch):
    """A loaded identity master — so no request needs the runtime's dataset."""
    master = _FakeMaster(
        {
            "AAPL": _record("AAPL"),
            "MSFT": _record("MSFT"),
            "ZZZZ": SimpleNamespace(symbol="ZZZZ", cik=None),
        }
    )
    monkeypatch.setattr(
        app.state, "engine", SimpleNamespace(security_master=master), raising=False
    )
    yield


def _today() -> dt.date:
    return dt.datetime.now(dt.timezone.utc).date()


def _row(*, code, shares, price, accession, name="Alice Insiderson",
         days_ago=1, filed_days_ago=None, role="Officer"):
    """One parsed Form 4 transaction, in the collector's real shape.

    ``filed_days_ago`` overrides the filed date so ``as_of`` can be tested
    against a filing date that differs from the transaction date.
    """
    when = _today() - dt.timedelta(days=days_ago)
    filed = when if filed_days_ago is None else _today() - dt.timedelta(days=filed_days_ago)
    return InsiderTransaction(
        accession=accession,
        filed=filed.isoformat(),
        period_of_report=when.isoformat(),
        owner_name=name,
        owner_cik=None,
        role=role,
        security="Common Stock",
        transaction_date=when.isoformat(),
        code=code,
        shares=shares,
        price=price,
        acquired_disposed="A" if code.upper() == "P" else "D",
        shares_after=None,
        url=f"https://www.sec.gov/Archives/edgar/data/0/{accession}.xml",
    )


def _patch_collector(monkeypatch, rows):
    """Fabricated collector — the real one is async and returns ``(items, errors)``."""
    seen: list[str] = []

    async def fake_collect_insider(symbol, cik=None, **kwargs):
        seen.append(symbol)
        return list(rows), []

    monkeypatch.setattr(mod, "collect_insider", fake_collect_insider)
    return seen


# --------------------------------------------------------------- 30d window

def test_30d_window_aggregates_purchases_and_sales(client, monkeypatch):
    seen = _patch_collector(
        monkeypatch,
        [
            _row(code="P", shares=100.0, price=10.0, accession="A1", days_ago=1),
            _row(code="P", shares=50.0, price=20.0, accession="A2", name="Bob", days_ago=2),
            _row(code="S", shares=200.0, price=5.0, accession="A3", days_ago=3),
            _row(code="S", shares=100.0, price=6.0, accession="A4", name="Carol", days_ago=3),
        ],
    )
    r = client.get("/insider-flow/AAPL?days=30")
    assert r.status_code == 200
    b = r.json()
    assert b["symbol"] == "AAPL"
    assert b["windowDays"] == 30
    assert b["bought_shares"] == pytest.approx(150.0)
    assert b["sold_shares"] == pytest.approx(300.0)
    assert b["bought_value"] == pytest.approx(100.0 * 10.0 + 50.0 * 20.0)
    assert b["sold_value"] == pytest.approx(200.0 * 5.0 + 100.0 * 6.0)
    assert b["filings"] == 4
    assert b["filers"] == 3
    assert b["provider_errors"] == []
    assert b["reason"] is None
    assert seen == ["AAPL"]


def test_30d_window_drops_filings_older_than_the_window(client, monkeypatch):
    _patch_collector(
        monkeypatch,
        [
            _row(code="P", shares=10.0, price=1.0, accession="IN", days_ago=2),
            _row(code="P", shares=999.0, price=9.0, accession="OUT", days_ago=40),
        ],
    )
    b = client.get("/insider-flow/AAPL?days=30").json()
    assert b["bought_shares"] == pytest.approx(10.0)
    assert b["bought_value"] == pytest.approx(10.0)
    assert b["filings"] == 1


def test_90d_window_is_available(client, monkeypatch):
    _patch_collector(
        monkeypatch,
        [_row(code="S", shares=7.0, price=2.0, accession="F1", days_ago=80)],
    )
    r = client.get("/insider-flow/AAPL?days=90")
    assert r.status_code == 200
    b = r.json()
    assert b["windowDays"] == 90
    assert b["sold_shares"] == pytest.approx(7.0)
    assert b["sold_value"] == pytest.approx(14.0)


def test_default_window_is_thirty_days(client, monkeypatch):
    _patch_collector(
        monkeypatch,
        [_row(code="P", shares=3.0, price=2.0, accession="D1", days_ago=5)],
    )
    b = client.get("/insider-flow/AAPL").json()
    assert b["windowDays"] == 30
    assert b["bought_shares"] == pytest.approx(3.0)


# --------------------------------------------------------------- P vs S

def test_grants_and_exercises_are_not_buys(client, monkeypatch):
    _patch_collector(
        monkeypatch,
        [
            _row(code="P", shares=100.0, price=5.0, accession="BUY1"),
            _row(code="A", shares=1000.0, price=0.0, accession="GRANT"),
            _row(code="M", shares=500.0, price=0.0, accession="EXERCISE"),
            _row(code="G", shares=42.0, price=0.0, accession="GIFT"),
            _row(code="C", shares=7.0, price=0.0, accession="CONV"),
            _row(code="F", shares=3.0, price=0.0, accession="TAX"),
            _row(code="D", shares=2.0, price=0.0, accession="SALE_D"),
            _row(code="S", shares=400.0, price=5.0, accession="SELL1"),
        ],
    )
    b = client.get("/insider-flow/AAPL?days=30").json()
    assert b["bought_shares"] == pytest.approx(100.0)
    assert b["bought_value"] == pytest.approx(500.0)
    assert b["sold_shares"] == pytest.approx(400.0)
    assert b["sold_value"] == pytest.approx(2000.0)
    assert b["filings"] == 8
    assert b["otherTransactions"] == 6


def test_lowercase_codes_and_wordy_codes_are_read(client, monkeypatch):
    _patch_collector(
        monkeypatch,
        [
            _row(code="p", shares=4.0, price=3.0, accession="l1"),
            _row(code="s", shares=6.0, price=3.0, accession="l2"),
            _row(code="P - Open Market Purchase", shares=2.0, price=3.0, accession="n1"),
        ],
    )
    b = client.get("/insider-flow/AAPL?days=30").json()
    assert b["bought_shares"] == pytest.approx(6.0)
    assert b["sold_shares"] == pytest.approx(6.0)


def test_net_shares_is_buys_less_sells_and_rejects_an_empty_side(client, monkeypatch):
    _patch_collector(
        monkeypatch,
        [
            _row(code="P", shares=100.0, price=1.0, accession="P1"),
            _row(code="S", shares=30.0, price=1.0, accession="S1"),
        ],
    )
    b = client.get("/insider-flow/AAPL?days=30").json()
    assert b["net_shares"] == pytest.approx(70.0)


def test_net_shares_is_null_when_one_side_is_silent(client, monkeypatch):
    _patch_collector(
        monkeypatch, [_row(code="P", shares=100.0, price=1.0, accession="P1")]
    )
    b = client.get("/insider-flow/AAPL?days=30").json()
    assert b["bought_shares"] == pytest.approx(100.0)
    assert b["sold_shares"] is None
    assert b["net_shares"] is None


# --------------------------------------------------------------- empty window

def test_no_filings_at_all_is_a_named_empty_never_zero(client, monkeypatch):
    _patch_collector(monkeypatch, [])
    r = client.get("/insider-flow/AAPL?days=30")
    assert r.status_code == 200
    b = r.json()
    assert b["bought_shares"] is None
    assert b["sold_shares"] is None
    assert b["bought_value"] is None
    assert b["sold_value"] is None
    assert b["net_shares"] is None
    assert b["as_of"] is None
    assert b["filings"] == 0
    assert b["filers"] == 0
    assert isinstance(b["reason"], str) and "no insider filings" in b["reason"].lower()


def test_no_open_market_flow_is_a_named_empty_never_zero(client, monkeypatch):
    _patch_collector(monkeypatch, [_row(code="M", shares=500.0, price=0.0, accession="X1")])
    b = client.get("/insider-flow/AAPL?days=30").json()
    assert b["bought_shares"] is None
    assert b["sold_shares"] is None
    assert b["net_shares"] is None
    assert b["filings"] == 1
    assert "open-market" in b["reason"].lower()


def test_no_cik_answers_an_empty_two_hundred(client, monkeypatch):
    _patch_collector(monkeypatch, [])
    r = client.get("/insider-flow/zzzz?days=30")
    assert r.status_code == 200
    b = r.json()
    assert b["filings"] == 0
    assert b["bought_shares"] is None
    assert "cik" in b["reason"].lower()


def test_no_master_is_a_five_oh_three(client, monkeypatch):
    monkeypatch.setattr(app.state, "engine", None, raising=False)
    r = client.get("/insider-flow/AAPL?days=30")
    assert r.status_code == 503
    assert r.json()["error"] == "screen_unavailable"


def test_unknown_symbol_is_a_four_oh_four(client, monkeypatch):
    _patch_collector(monkeypatch, [])
    r = client.get("/insider-flow/tsm?days=30")
    assert r.status_code == 404
    assert r.json()["error"] == "security_not_found"


# --------------------------------------------------------------- junk days

@pytest.mark.parametrize("bad", ["x", "-5", "0", "1.5", "", "30;", "45", "99999999999999999999"])
def test_junk_days_return_the_house_invalid_envelope(client, monkeypatch, bad):
    _patch_collector(monkeypatch, [])
    r = client.get(f"/insider-flow/AAPL?days={bad}")
    assert r.status_code == 422
    b = r.json()
    assert b["error"] == "invalid_days"
    assert isinstance(b["errorMessage"], str) and b["errorMessage"]


# --------------------------------------------------------------- dedup

def test_a_repeated_transaction_row_is_counted_once(client, monkeypatch):
    seen = _patch_collector(
        monkeypatch,
        [
            _row(code="P", shares=100.0, price=10.0, accession="DUP", name="Dana"),
            _row(code="P", shares=100.0, price=10.0, accession="DUP", name="Dana"),
        ],
    )
    b = client.get("/insider-flow/AAPL?days=30").json()
    assert b["filings"] == 1
    assert b["bought_shares"] == pytest.approx(100.0)
    assert b["bought_value"] == pytest.approx(1000.0)
    assert b["filers"] == 1


def test_two_transactions_from_one_filing_are_both_counted(client, monkeypatch):
    """One Form 4 body legitimately holds several transaction rows."""
    _patch_collector(
        monkeypatch,
        [
            _row(code="S", shares=40.0, price=10.0, accession="TWO", name="Dana"),
            _row(code="P", shares=40.0, price=10.0, accession="TWO", name="Dana"),
        ],
    )
    b = client.get("/insider-flow/AAPL?days=30").json()
    assert b["filings"] == 2
    assert b["bought_shares"] == pytest.approx(40.0)
    assert b["sold_shares"] == pytest.approx(40.0)


# --------------------------------------------------------------- pricing

def test_missing_price_counts_shares_but_not_value(client, monkeypatch):
    _patch_collector(
        monkeypatch,
        [
            _row(code="P", shares=100.0, price=10.0, accession="PRICED"),
            _row(code="P", shares=250.0, price=None, accession="NOPRICE"),
        ],
    )
    b = client.get("/insider-flow/AAPL?days=30").json()
    assert b["bought_shares"] == pytest.approx(350.0)
    assert b["bought_value"] == pytest.approx(1000.0)
    assert b["unpricedFilings"] == 1
    # The value describes only the priced row, and the document says so rather
    # than letting the reader assume it covers the whole share count.
    assert "priced rows" in b["reason"].lower()


def test_zero_price_is_not_a_price(client, monkeypatch):
    _patch_collector(
        monkeypatch, [_row(code="P", shares=10.0, price=0.0, accession="FREE")]
    )
    b = client.get("/insider-flow/AAPL?days=30").json()
    assert b["bought_shares"] == pytest.approx(10.0)
    assert b["bought_value"] is None
    assert b["unpricedFilings"] == 1


def test_unpriced_only_window_keeps_shares_and_says_why(client, monkeypatch):
    _patch_collector(
        monkeypatch, [_row(code="S", shares=10.0, price=None, accession="NOPRICE2")]
    )
    b = client.get("/insider-flow/AAPL?days=30").json()
    assert b["sold_shares"] == pytest.approx(10.0)
    assert b["sold_value"] is None
    assert "priced" in b["reason"].lower()


# --------------------------------------------------------------- as_of

def test_as_of_is_the_newest_filing_date_never_the_clock(client, monkeypatch):
    new = (_today() - dt.timedelta(days=4)).isoformat()
    old = (_today() - dt.timedelta(days=20)).isoformat()
    _patch_collector(
        monkeypatch,
        [
            _row(code="P", shares=1.0, price=1.0, accession="O1", filed_days_ago=20),
            _row(code="S", shares=2.0, price=1.0, accession="N1", filed_days_ago=4),
        ],
    )
    b = client.get("/insider-flow/AAPL?days=30").json()
    assert b["as_of"] == new
    assert b["as_of"] != old
    assert b["as_of"] != _today().isoformat()


def test_as_of_ignores_out_of_window_filings(client, monkeypatch):
    _patch_collector(
        monkeypatch,
        [
            _row(code="P", shares=1.0, price=1.0, accession="N2", days_ago=10),
            _row(code="P", shares=1.0, price=1.0, accession="O2", days_ago=400),
        ],
    )
    b = client.get("/insider-flow/AAPL?days=30").json()
    assert b["as_of"] == (_today() - dt.timedelta(days=10)).isoformat()


# --------------------------------------------------------------- boundary

def test_window_boundary_day_is_inclusive(client, monkeypatch):
    _patch_collector(
        monkeypatch,
        [
            _row(code="P", shares=5.0, price=2.0, accession="EDGE", days_ago=29),
            _row(code="P", shares=999.0, price=2.0, accession="OUT", days_ago=30),
        ],
    )
    b = client.get("/insider-flow/AAPL?days=30").json()
    assert b["bought_shares"] == pytest.approx(5.0)
    assert b["filings"] == 1


def test_ninety_day_boundary_is_inclusive(client, monkeypatch):
    _patch_collector(
        monkeypatch,
        [
            _row(code="S", shares=5.0, price=2.0, accession="EDGE90", days_ago=89),
            _row(code="S", shares=999.0, price=2.0, accession="OUT90", days_ago=90),
        ],
    )
    b = client.get("/insider-flow/AAPL?days=90").json()
    assert b["sold_shares"] == pytest.approx(5.0)
    assert b["filings"] == 1


# --------------------------------------------------------------- symbol bound

def test_symbol_is_bound_per_request(client, monkeypatch):
    master = _FakeMaster({"AAAA": _record("AAAA"), "BBBB": _record("BBBB")})
    monkeypatch.setattr(
        app.state, "engine", SimpleNamespace(security_master=master), raising=False
    )
    seen = _patch_collector(monkeypatch, [])
    client.get("/insider-flow/aaaa?days=30")
    client.get("/insider-flow/bbbb?days=30")
    assert seen == ["AAAA", "BBBB"]


def test_symbol_is_uppercased_in_the_payload(client, monkeypatch):
    _patch_collector(
        monkeypatch,
        [_row(code="P", shares=1.0, price=1.0, accession="X1")],
    )
    b = client.get("/insider-flow/msft?days=30").json()
    assert b["symbol"] == "MSFT"


def test_one_request_does_not_leak_another_requests_rows(client, monkeypatch):
    """Stale rows are never carried between requests."""
    store: dict[str, list] = {}

    async def fake_collect_insider(symbol, cik=None, **kwargs):
        return store.get(symbol, []), []

    monkeypatch.setattr(mod, "collect_insider", fake_collect_insider)
    store["AAPL"] = [_row(code="P", shares=9.0, price=3.0, accession="ONE")]
    first = client.get("/insider-flow/AAPL?days=30").json()
    store["AAPL"] = []
    second = client.get("/insider-flow/AAPL?days=30").json()
    assert first["bought_shares"] == pytest.approx(9.0)
    assert second["filings"] == 0
    assert second["reason"]


# --------------------------------------------------------------- provenance

def test_module_quotes_the_withdrawn_vendor_field_in_its_docstring():
    assert WITHDRAWN_FIELD_QUOTE in (mod.__doc__ or "")


def test_module_never_revives_the_withdrawn_vendor_field():
    """No spellings of the withdrawn vendor field survive in this module's code."""
    import pathlib

    path = pathlib.Path(mod.__file__)
    source = path.read_text() if path is not None else ""
    # The withdrawn field was the vendor's net-share-change number. ``net_shares``
    # is this route's OWN derived quantity (buys less sells from Form 4 rows), so
    # only the vendor's spellings are banned, and only in code — not in the
    # docstring that names it in order to refuse it.
    code = source.split('"""')[2]  # the module body, past the docstring
    for banned in ("netShare", "net_share_change", "netShareChange", "signals"):
        assert banned not in code
    assert "netShareChange" not in source


def test_collector_failure_is_reported_not_swallowed(monkeypatch):
    async def failing_collector(symbol, cik=None, **kwargs):
        return [], ["EDGAR said 500"]

    monkeypatch.setattr(mod, "collect_insider", failing_collector)
    client = TestClient(app)
    r = client.get("/insider-flow/AAPL?days=30")
    assert r.status_code == 200
    b = r.json()
    assert b["provider_errors"] == ["EDGAR said 500"]
    assert b["filings"] == 0
    assert b["reason"]
