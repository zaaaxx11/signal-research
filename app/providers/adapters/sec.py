"""SEC EDGAR — the official fundamentals rung (02 §3.4 L1, trust 1).

Two documents, two requests
---------------------------
``company_tickers.json`` maps a ticker to a CIK (one fetch, cached per instance),
and ``/api/xbrl/companyfacts/CIK##########.json`` carries every us-gaap fact the
issuer has filed. The CIK is zero-padded to ten digits because that is the path
the API publishes. EDGAR spells a share class with a hyphen (``BRK-B``), so the
dot spelling the screen uses is normalised before the lookup.

What the numbers mean
---------------------
Every cell is an arithmetic on the filings; nothing is copied from a vendor that
already did it.

* **TTM = last fiscal year + YTD this year − YTD the same period last year.**
  A 10-Q reports a six- or nine-month *year-to-date* figure, not a quarter, so
  summing "the last four quarters" is only possible for the concepts a company
  happens to tag quarterly (Apple does not tag a fourth quarter at all). The
  identity works for every duration a 10-Q can publish. When no 10-K exists yet
  (a recent listing) four consecutive quarters are summed; when neither exists
  the last fiscal year is the answer — a TTM that cannot be computed is not
  extrapolated.
* **EBITDA needs both legs**: ``OperatingIncomeLoss`` *and* a depreciation and
  amortisation tag. EBITDA is not a us-gaap concept, so half of it is not a
  smaller EBITDA, it is a different number wearing the same name.
* **FCF is CFO − capex**: ``NetCashProvidedByUsedInOperatingActivities`` minus
  ``PaymentsToAcquirePropertyPlantAndEquipment``. Not investing cash flow.
* **`total_debt` is exact debt only.** Long-term debt (plus its current portion,
  commercial paper and short-term borrowings when the filing breaks them out).
  ``Liabilities`` is not debt and is never used as a fallback; a company that
  does not tag debt gets a null.
* **`roe` = net income TTM / equity; `debt_to_equity` = Liabilities / equity**,
  both in **percent points** — the scale :mod:`app.fundamentals.scan` normalises
  TradingView's ratio to, so a column means the same thing whichever rung wrote
  it. Each ratio needs both sides on the same balance date (a 10-day window for
  a weekend/holiday offset), and a non-positive equity produces nothing: a
  negative book value makes the ratio meaningless and a zero one makes it
  infinite.

Null is a fact
--------------
A missing leg removes the cell instead of producing zero, half a calculation, or
a neighbouring concept. The rules above are enforced in :meth:`parse` and pinned
by ``tests/test_sec_adapter.py``.

`as_of` is the **period end** the value describes, not the fetch time and not the
filing date: ``2026-06-27`` is when the balance sheet is true, ``2026-07-31`` is
only when the world could read it.

US-listed equities only
-----------------------
``REGISTRY`` gates this rung with ``_us_equity_only``. A fund/ETF files N-CSR
rather than a 10-K, and a foreign listing has no CIK at all; the ladder must
**skip** the rung for those (zero requests) rather than receive ``not_found``,
which is terminal and would end the walk before the vendor rung that can answer
is ever asked.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Iterable, Mapping, Sequence

from app.contracts import Capability
from app.providers import http as provider_http
from app.providers.adapters._support import (
    HttpAdapter,
    as_float,
    envelope_verdict,
    json_of,
    observation,
    price_unit,
)
from app.providers.base import InstrumentRef, ProviderError, Verdict

TICKERS_HOST = "https://www.sec.gov"
TICKERS_PATH = "/files/company_tickers.json"
TICKERS_URL = f"{TICKERS_HOST}{TICKERS_PATH}"

FACTS_HOST = "https://data.sec.gov"
FACTS_PATH = "/api/xbrl/companyfacts/CIK{cik}.json"

#: SEC's fair-access policy: a User-Agent that identifies the client **and** a
#: contact. An anonymous or library UA is answered ``403``. Overridable so an
#: operator can put a real address here without a code change.
USER_AGENT_ENV = "MEE_SEC_USER_AGENT"
DEFAULT_USER_AGENT = "ztockz-market-engine/1.0 (market-engine@ztockz.local)"

#: Only the periodic reports are accounting facts. ``8-K`` carries exhibits and
#: pro-forma numbers, ``20-F``/``40-F`` are foreign private issuers with a
#: different taxonomy, and an ``S-1`` is a registration, not a fiscal period.
FORMS = ("10-K", "10-Q")

#: us-gaap facts are reported in the issuer's currency; the engine's statement
#: columns are USD, so a different unit is not a number we can store.
CURRENCY = "USD"

#: The two durations a TTM is built from, in days.
ANNUAL_DAYS = (300, 400)
QUARTER_DAYS = (70, 110)

#: A balance date and a period end from the same filing can differ by a weekend
#: or a holiday; a wider gap means two different filings and no ratio.
PAIR_TOLERANCE_DAYS = 10

#: Concept precedence. First tag that carries a usable fact wins; the order is
#: not alphabetical, it is "the most specific tag that means this line item".
REVENUE_CONCEPTS: tuple[str, ...] = (
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
    "SalesRevenueNet",
)
NET_INCOME_CONCEPTS: tuple[str, ...] = ("NetIncomeLoss", "ProfitLoss")
OPERATING_INCOME_CONCEPTS: tuple[str, ...] = ("OperatingIncomeLoss",)
DEPRECIATION_CONCEPTS: tuple[str, ...] = (
    "DepreciationDepletionAndAmortization",
    "DepreciationAndAmortization",
    "DepreciationAmortizationAndAccretionNet",
)
CFO_CONCEPTS: tuple[str, ...] = ("NetCashProvidedByUsedInOperatingActivities",)
CAPEX_CONCEPTS: tuple[str, ...] = (
    "PaymentsToAcquirePropertyPlantAndEquipment",
    "PaymentsToAcquireProductiveAssets",
)
CASH_CONCEPTS: tuple[str, ...] = ("CashAndCashEquivalentsAtCarryingValue",)
EQUITY_CONCEPTS: tuple[str, ...] = (
    "StockholdersEquity",
    "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
)
LIABILITY_CONCEPTS: tuple[str, ...] = ("Liabilities",)

#: ``LongTermDebtNoncurrent`` is the non-current piece; ``LongTermDebt`` is the
#: total including the current portion, so the two cannot be added together.
NONCURRENT_DEBT_CONCEPTS: tuple[str, ...] = ("LongTermDebtNoncurrent",)
TOTAL_LONG_TERM_DEBT_CONCEPTS: tuple[str, ...] = ("LongTermDebt",)
#: ``DebtCurrent`` already contains the current portion and commercial paper;
#: only when it is absent is the sum of these parts the current debt.
CURRENT_DEBT_CONCEPTS: tuple[str, ...] = ("DebtCurrent",)
CURRENT_DEBT_PARTS: tuple[str, ...] = (
    "LongTermDebtCurrent",
    "CommercialPaper",
    "ShortTermBorrowings",
)


@dataclass(frozen=True)
class _Fact:
    """One usable us-gaap fact: a period (or a point), a value and its filing."""

    start: date | None
    end: date
    value: float
    form: str
    filed: date

    @property
    def days(self) -> int | None:
        """Length of a duration fact; ``None`` for a balance-sheet point."""

        if self.start is None:
            return None
        return (self.end - self.start).days


@dataclass(frozen=True)
class _Amount:
    """A computed value and the date it is true for."""

    value: float
    end: date


# ---------------------------------------------------------------------------
# Reading the companyfacts document
# ---------------------------------------------------------------------------


def _as_date(value: Any) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def ticker_map(payload: Any) -> dict[str, int]:
    """``{"AAPL": 320193, ...}`` from ``company_tickers.json`` (``{}`` if broken).

    The document is an object keyed by an index string, not an array:

    ``{"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}, ...}``
    """

    if not isinstance(payload, Mapping):
        return {}
    out: dict[str, int] = {}
    for entry in payload.values():
        if not isinstance(entry, Mapping):
            continue
        ticker = str(entry.get("ticker") or "").strip().upper()
        raw_cik = entry.get("cik_str")
        if not ticker:
            continue
        try:
            cik = int(raw_cik)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        if cik > 0:
            out[ticker] = cik
    return out


def concept_facts(document: Mapping[str, Any], concept: str) -> list[_Fact]:
    """The 10-K/10-Q USD facts of one concept, one row per period.

    A restatement repeats ``(start, end)`` with a newer ``filed``; the newest
    filing is the answer, not the first one that happens to be in the array.
    """

    facts_block = document.get("facts")
    if not isinstance(facts_block, Mapping):
        return []
    gaap = facts_block.get("us-gaap")
    if not isinstance(gaap, Mapping):
        return []
    entry = gaap.get(concept)
    if not isinstance(entry, Mapping):
        return []
    units = entry.get("units")
    if not isinstance(units, Mapping):
        return []
    rows = units.get(CURRENCY)
    if not isinstance(rows, list):
        return []

    best: dict[tuple[date | None, date], _Fact] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        form = str(row.get("form") or "").strip().upper()
        if not form.startswith(FORMS):
            continue
        end = _as_date(row.get("end"))
        value = as_float(row.get("val"))
        if end is None or value is None or not math.isfinite(value):
            continue
        fact = _Fact(
            start=_as_date(row.get("start")),
            end=end,
            value=float(value),
            form=form,
            filed=_as_date(row.get("filed")) or date.min,
        )
        key = (fact.start, fact.end)
        current = best.get(key)
        if current is None or fact.filed >= current.filed:
            best[key] = fact
    return list(best.values())


def trailing_twelve_months(facts: Sequence[_Fact]) -> _Amount | None:
    """TTM of one duration concept (see the module docstring for the identity)."""

    annuals = sorted(
        (fact for fact in facts if fact.days is not None and ANNUAL_DAYS[0] <= fact.days <= ANNUAL_DAYS[1]),
        key=lambda fact: (fact.end, fact.filed),
    )
    annual = annuals[-1] if annuals else None

    if annual is not None:
        # The YTD that starts where the fiscal year ended is the current year to
        # date. The longest such window is the one the latest 10-Q reports.
        # Shorter than a full year and at least a quarter: a 6-week stub is a
        # transition period, not a year to date.
        year_to_date = sorted(
            (
                (fact, fact.days)
                for fact in facts
                if fact.days is not None
                and 60 <= fact.days < ANNUAL_DAYS[0]
                and fact.start is not None
                and abs((fact.start - annual.end).days) <= 7
            ),
            key=lambda item: (item[1], item[0].end),
        )
        for ytd, length in reversed(year_to_date):
            priors = [
                fact
                for fact in facts
                if fact.days is not None
                and abs(fact.days - length) <= 3
                and abs(abs((ytd.end - fact.end).days) - 365) <= PAIR_TOLERANCE_DAYS
            ]
            if priors:
                prior = max(priors, key=lambda fact: (fact.end, fact.filed))
                return _Amount(annual.value + ytd.value - prior.value, ytd.end)

    quarters = sorted(
        (
            fact
            for fact in facts
            if fact.days is not None and QUARTER_DAYS[0] <= fact.days <= QUARTER_DAYS[1]
        ),
        key=lambda fact: fact.end,
    )
    window: list[_Fact] = []
    for fact in quarters:
        if not window or 55 <= (fact.end - window[-1].end).days <= 110:
            window.append(fact)
        else:
            window = [fact]
    if len(window) >= 4:
        four = window[-4:]
        if len({fact.end for fact in four}) == 4:
            return _Amount(sum(fact.value for fact in four), four[-1].end)

    if annual is not None:
        return _Amount(annual.value, annual.end)
    return None


def latest_point(facts: Sequence[_Fact]) -> _Amount | None:
    """The most recent balance-sheet value (instant facts only)."""

    points = [fact for fact in facts if fact.days is None]
    if not points:
        return None
    newest = max(points, key=lambda fact: (fact.end, fact.filed))
    return _Amount(newest.value, newest.end)


# ---------------------------------------------------------------------------
# The adapter
# ---------------------------------------------------------------------------


class SecEdgarAdapter(HttpAdapter):
    """Official filings, trust 1; no credential, but a contactable User-Agent."""

    name = "sec_edgar"
    trust = 1
    capabilities = frozenset({Capability.FUNDAMENTALS})
    accept = "application/json"

    def __init__(self, *, timeout_s: float = 20.0, ticker_map: Mapping[str, int] | None = None) -> None:
        super().__init__(timeout_s=timeout_s)
        #: The ticker→CIK table is a 10k-row constant: one fetch per process.
        self._tickers: dict[str, int] | None = dict(ticker_map) if ticker_map else None

    # -- requests ----------------------------------------------------------

    def url(self, instrument: InstrumentRef) -> str:
        """The rung's entry URL; the companyfacts URL needs the CIK first."""

        return TICKERS_URL

    @staticmethod
    def facts_url(cik: int) -> str:
        return f"{FACTS_HOST}{FACTS_PATH.format(cik=f'{int(cik):010d}')}"

    def request_headers(self, instrument: InstrumentRef) -> dict[str, str]:
        headers = super().request_headers(instrument)
        headers["user-agent"] = (
            os.environ.get(USER_AGENT_ENV) or DEFAULT_USER_AGENT
        ).strip() or DEFAULT_USER_AGENT
        headers["accept-encoding"] = "gzip, deflate"
        return headers

    @staticmethod
    def _symbol(instrument: InstrumentRef) -> str:
        """EDGAR's spelling: upper case, and a share class is ``BRK-B``."""

        return str(instrument.symbol).strip().upper().replace(".", "-")

    async def fetch(
        self, capability: Capability, instrument: InstrumentRef, fields: set[str]
    ) -> dict[str, Any]:
        """Resolve the CIK, then read one companyfacts document."""

        if capability not in self.capabilities:
            raise ProviderError(
                Verdict(
                    ok=False,
                    kind="schema_drift",
                    detail=f"{self.name} does not implement {capability.value}",
                )
            )
        headers = self.request_headers(instrument)
        async with provider_http.build_client(
            timeout_s=self.timeout_s, headers=headers
        ) as client:
            symbol = self._symbol(instrument)
            if self._tickers is not None:
                cik, tickers_raw = self._tickers.get(symbol), None
            else:
                tickers_raw = await self._get(client, TICKERS_URL, stage="tickers")
                loaded = (
                    ticker_map(json_of(tickers_raw))
                    if int(tickers_raw.get("status") or 0) == 200
                    else {}
                )
                if not loaded:
                    # The mapping itself failed (authwall/blocked/404): that
                    # response *is* the answer, so the ladder can name the
                    # failure instead of blaming the symbol.
                    return tickers_raw
                self._tickers = loaded
                cik = loaded.get(symbol)
            if cik is None:
                raw = dict(tickers_raw or {})
                raw.update(
                    {
                        "status": int(raw.get("status") or 200),
                        "headers": raw.get("headers") or {"content-type": "application/json"},
                        "body": raw.get("body") or b"{}",
                        "url": raw.get("url") or TICKERS_URL,
                        "capability": Capability.FUNDAMENTALS.value,
                        "stage": "tickers",
                        "symbol_missing": True,
                    }
                )
                return raw
            return await self._get(client, self.facts_url(cik))

    async def _get(self, client: Any, url: str, *, stage: str = "facts") -> dict[str, Any]:
        response = await client.get(url)
        return {
            "status": int(response.status_code),
            "headers": {str(key).lower(): str(value) for key, value in response.headers.items()},
            "body": response.content,
            "url": url,
            "capability": Capability.FUNDAMENTALS.value,
            "stage": stage,
        }

    # -- verdict -----------------------------------------------------------

    def verdict(self, raw: Mapping[str, Any], instrument: InstrumentRef) -> Verdict:
        envelope = envelope_verdict(raw)
        if envelope is not None:
            return envelope
        if raw.get("symbol_missing"):
            return Verdict(
                False,
                "not_found",
                f"{self._symbol(instrument)} is not in company_tickers.json",
            )
        payload = json_of(raw)
        if not isinstance(payload, Mapping):
            return Verdict(False, "schema_drift", "body is not a JSON object")
        if str(raw.get("stage") or "facts") == "tickers":
            if not ticker_map(payload):
                return Verdict(
                    False,
                    "schema_drift",
                    "company_tickers.json is not the documented {index: {cik_str, ticker}} map",
                )
            return Verdict(True, "ok")

        facts_block = payload.get("facts")
        if not isinstance(facts_block, Mapping):
            return Verdict(False, "schema_drift", "companyfacts has no facts block")
        gaap = facts_block.get("us-gaap")
        if not isinstance(gaap, Mapping) or not gaap:
            return Verdict(False, "empty", "companyfacts carries no us-gaap facts")
        if not self.parse(raw, instrument):
            return Verdict(False, "empty", "no statement could be computed from the filings")
        return Verdict(True, "ok")

    # -- parse -------------------------------------------------------------

    def parse(
        self, raw: Mapping[str, Any], instrument: InstrumentRef
    ) -> Sequence[Mapping[str, Any]]:
        payload = json_of(raw)
        if not isinstance(payload, Mapping):
            return []
        document = _Statements(payload)
        unit = price_unit(CURRENCY)
        out: list[dict[str, Any]] = []

        def cell(field_name: str, amount: _Amount | None, *, cell_unit: str) -> None:
            if amount is None:
                return
            out.append(
                observation(
                    instrument,
                    field_name,
                    value_num=amount.value,
                    unit=cell_unit,
                    source=self.name,
                    trust=self.trust,
                    as_of=datetime(amount.end.year, amount.end.month, amount.end.day, tzinfo=UTC),
                )
            )

        revenue = document.duration(REVENUE_CONCEPTS)
        net_income = document.duration(NET_INCOME_CONCEPTS)
        operating_income = document.duration(OPERATING_INCOME_CONCEPTS)
        depreciation = document.duration(DEPRECIATION_CONCEPTS)
        cash_flow = document.duration(CFO_CONCEPTS)
        capex = document.duration(CAPEX_CONCEPTS)

        cell("revenue_ttm", revenue, cell_unit=unit)
        cell("net_income_ttm", net_income, cell_unit=unit)
        # EBITDA is not a us-gaap tag: both legs, or no cell.
        if operating_income is not None and depreciation is not None:
            cell(
                "ebitda_ttm",
                _Amount(operating_income.value + depreciation.value, operating_income.end),
                cell_unit=unit,
            )
        # Free cash flow is operating cash flow minus capital expenditure.
        if cash_flow is not None and capex is not None:
            cell(
                "fcf_ttm",
                _Amount(cash_flow.value - capex.value, cash_flow.end),
                cell_unit=unit,
            )

        equity = document.instant(EQUITY_CONCEPTS)
        cell("total_debt", document.total_debt(), cell_unit=unit)
        cell("cash_and_equivalents", document.instant(CASH_CONCEPTS), cell_unit=unit)

        if equity is not None and equity.value > 0:
            if net_income is not None and _same_balance_date(net_income.end, equity.end):
                cell(
                    "roe",
                    _Amount(net_income.value / equity.value * 100.0, equity.end),
                    cell_unit="pct",
                )
            liabilities = document.instant(LIABILITY_CONCEPTS)
            if liabilities is not None and _same_balance_date(liabilities.end, equity.end):
                cell(
                    "debt_to_equity",
                    _Amount(liabilities.value / equity.value * 100.0, equity.end),
                    cell_unit="ratio",
                )
        return out


def _same_balance_date(first: date, second: date) -> bool:
    return abs((first - second).days) <= PAIR_TOLERANCE_DAYS


class _Statements:
    """Lazy lookups over one companyfacts document (each concept read once)."""

    def __init__(self, document: Mapping[str, Any]) -> None:
        self._document = document
        self._cache: dict[str, Any] = {}

    def _facts(self, concept: str) -> list[_Fact]:
        if concept not in self._cache:
            self._cache[concept] = concept_facts(self._document, concept)
        return self._cache[concept]

    def duration(self, concepts: Iterable[str]) -> _Amount | None:
        """First concept with a computable TTM."""

        for concept in concepts:
            amount = trailing_twelve_months(self._facts(concept))
            if amount is not None:
                return amount
        return None

    def instant(self, concepts: Iterable[str]) -> _Amount | None:
        """First concept with a balance-sheet value."""

        for concept in concepts:
            amount = latest_point(self._facts(concept))
            if amount is not None:
                return amount
        return None

    def total_debt(self) -> _Amount | None:
        """Exact debt concepts only — never ``Liabilities``.

        ``LongTermDebtNoncurrent`` is the non-current piece and is combined with
        the current debt (``DebtCurrent`` when the filing publishes it, else the
        parts). When only ``LongTermDebt`` exists it is already the total of the
        long-term line including its current portion, so only genuinely separate
        short-term instruments are added. No long-term tag at all means the
        total is unknown: a figure that silently omits long-term debt is worse
        than an empty cell.
        """

        noncurrent = self.instant(NONCURRENT_DEBT_CONCEPTS)
        if noncurrent is not None:
            current = self.instant(CURRENT_DEBT_CONCEPTS)
            if current is None:
                parts = [
                    part
                    for part in (self.instant((name,)) for name in CURRENT_DEBT_PARTS)
                    if part is not None and _same_balance_date(part.end, noncurrent.end)
                ]
                if parts:
                    current = _Amount(sum(part.value for part in parts), noncurrent.end)
            elif not _same_balance_date(current.end, noncurrent.end):
                current = None
            return _Amount(
                noncurrent.value + (current.value if current is not None else 0.0),
                noncurrent.end,
            )

        total_long_term = self.instant(TOTAL_LONG_TERM_DEBT_CONCEPTS)
        if total_long_term is not None:
            extra = [
                part
                for part in (self.instant((name,)) for name in CURRENT_DEBT_PARTS[1:])
                if part is not None and _same_balance_date(part.end, total_long_term.end)
            ]
            return _Amount(
                total_long_term.value + sum(part.value for part in extra),
                total_long_term.end,
            )
        return None


__all__ = [
    "DEFAULT_USER_AGENT",
    "FORMS",
    "SecEdgarAdapter",
    "TICKERS_URL",
    "USER_AGENT_ENV",
    "concept_facts",
    "ticker_map",
    "trailing_twelve_months",
]
