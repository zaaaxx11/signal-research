"""T1 — the ticker-page statement table, built from cached SEC XBRL facts.

The table is a data structure first and a route second, so most of these tests
call :func:`app.engine.statement_table.build_statement_table` directly. The
route tests then prove the one composition that matters: a symbol the identity
master knows, a ``companyfacts`` document already on disk, and no network.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterable, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from datetime import date, timedelta
from typing import Any

import httpx2
import pytest

from app.engine.facts import FactObservation
from app.engine.ingest import EngineDataset
from app.engine.model import Provenance
from app.engine.security import SecurityMaster
from app.engine.service import EngineService
from app.engine.statement_table import build_statement_table, statement_document

AAPL_CIK = "0000320193"

#: Ten fiscal years, newest last. FY ends on the last Saturday of September,
#: which is why the end dates are not 1 January.
ANNUAL_ENDS = [f"{year}-09-27" for year in range(2016, 2026)]

#: Eight reported quarters: 2023 Q1–Q3, 2024 Q1–Q3, 2025 Q1–Q2. A fiscal Q4 is
#: never a 10-Q, so a run of eight cannot be nine.
QUARTER_ENDS = [
    "2023-03-25",
    "2023-06-24",
    "2023-09-23",
    "2024-03-30",
    "2024-06-29",
    "2024-09-28",
    "2025-03-29",
    "2025-06-28",
]


def obs(
    tag: str,
    value: float,
    *,
    end: str,
    start: str | None = None,
    form: str = "10-K",
    filed: str = "2026-01-15",
    accn: str = "0000320193-26-000001",
    unit: str = "USD",
    fy: int | None = None,
    fp: str | None = None,
) -> FactObservation:
    """One XBRL observation, in the shape :func:`parse_companyfacts` produces."""

    return FactObservation(
        tag=tag,
        value=value,
        unit=unit,
        period_end=end,
        start=start,
        form=form,
        filed=filed,
        accession=accn,
        fiscal_year=fy,
        fiscal_period=fp,
    )


def _year_start(end: str) -> str:
    return f"{int(end[:4]) - 1}{end[4:]}"


def annual_series(
    tag: str,
    base: float,
    step: float,
    *,
    unit: str = "USD",
    missing: Iterable[str] = (),
) -> list[FactObservation]:
    """One flow line as ten consecutive fiscal years (330–400 day rows)."""

    rows: list[FactObservation] = []
    for index, end in enumerate(ANNUAL_ENDS):
        if end in missing:
            continue
        year = int(end[:4])
        rows.append(
            obs(
                tag,
                base + step * index,
                end=end,
                start=_year_start(end),
                form="10-K",
                filed=f"{year + 1}-01-15",
                accn=f"0000320193-{str(year + 1)[2:]}-000001",
                unit=unit,
                fy=year,
                fp="FY",
            )
        )
    return rows


def instant_series(
    tag: str, base: float, step: float, *, unit: str = "USD"
) -> list[FactObservation]:
    """One balance line: an instant at every fiscal year end."""

    rows: list[FactObservation] = []
    for index, end in enumerate(ANNUAL_ENDS):
        year = int(end[:4])
        rows.append(
            obs(
                tag,
                base + step * index,
                end=end,
                form="10-K",
                filed=f"{year + 1}-01-15",
                accn=f"0000320193-{str(year + 1)[2:]}-000001",
                unit=unit,
                fy=year,
                fp="FY",
            )
        )
    return rows


def quarters(
    tag: str, values: Sequence[float], *, unit: str = "USD", form: str = "10-Q"
) -> list[FactObservation]:
    """One line as eight three-month rows, one accession per quarter."""

    rows: list[FactObservation] = []
    for index, (end, value) in enumerate(zip(QUARTER_ENDS, values, strict=True)):
        year = int(end[:4])
        fiscal_quarter = {3: "Q1", 6: "Q2", 9: "Q3"}[int(end[5:7])]
        rows.append(
            obs(
                tag,
                value,
                end=end,
                start=(date.fromisoformat(end) - timedelta(days=91)).isoformat(),
                form=form,
                filed=f"{year}-05-0{index % 9 + 1}",
                accn=f"0000320193-{year}-q{index}",
                unit=unit,
                fy=year,
                fp=fiscal_quarter,
            )
        )
    return rows


def fixture(
    *,
    missing_gross_profit: bool = False,
    missing_capex: bool = False,
    quarterly_revenue: Sequence[float] = (300.0, 310.0, 320.0, 330.0, 340.0, 350.0, 360.0, 370.0),
) -> list[FactObservation]:
    """A complete ten-year issuer, plus eight quarters of the flow lines."""

    revenue = annual_series(
        "RevenueFromContractWithCustomerExcludingAssessedTax", 1000.0, 100.0
    )
    rows: list[FactObservation] = [
        *revenue,
        *annual_series("OperatingIncomeLoss", 200.0, 20.0),
        *annual_series("NetIncomeLoss", 100.0, 10.0),
        *annual_series("EarningsPerShareDiluted", 1.0, 0.1, unit="USD/shares"),
        *quarters(
            "RevenueFromContractWithCustomerExcludingAssessedTax", quarterly_revenue
        ),
        *quarters("OperatingIncomeLoss", [60.0] * 8),
        *quarters("NetIncomeLoss", [30.0] * 8),
        *quarters("EarningsPerShareDiluted", [0.3] * 8, unit="USD/shares"),
        *instant_series("Assets", 5000.0, 100.0),
        *instant_series("Liabilities", 3000.0, 60.0),
        *instant_series("StockholdersEquity", 2000.0, 40.0),
        *instant_series("AssetsCurrent", 1500.0, 30.0),
        *instant_series("LiabilitiesCurrent", 800.0, 10.0),
        *instant_series("CashAndCashEquivalentsAtCarryingValue", 500.0, 20.0),
        *instant_series("LongTermDebt", 900.0, 5.0),
        *instant_series("CommonStockSharesOutstanding", 100.0, 1.0, unit="shares"),
        *annual_series("NetCashProvidedByUsedInOperatingActivities", 400.0, 30.0),
        *annual_series("NetCashProvidedByUsedInInvestingActivities", -150.0, -5.0),
        *annual_series("NetCashProvidedByUsedInFinancingActivities", -100.0, -2.0),
    ]
    if not missing_gross_profit:
        rows.extend(annual_series("GrossProfit", 600.0, 60.0))
    if not missing_capex:
        rows.extend(annual_series("PaymentsToAcquirePropertyPlantAndEquipment", 80.0, 5.0))
    return rows


def rows_of(document: dict[str, Any], statement: str) -> dict[str, dict[str, Any]]:
    return {row["key"]: row for row in document["statements"][statement]}


# ---------------------------------------------------------------------------
# Periods and alignment
# ---------------------------------------------------------------------------


def test_ten_annual_periods_are_newest_first_and_every_row_is_aligned() -> None:
    document = build_statement_table(fixture())

    annual = [period for period in document["periods"] if period["kind"] == "annual"]
    assert len(annual) == 10
    assert [period["period"] for period in annual] == [
        str(year) for year in range(2025, 2015, -1)
    ]
    assert document["as_of"] == ANNUAL_ENDS[-1]

    labels = {period["period"] for period in document["periods"]}
    for statement in ("income", "balance", "cash_flow"):
        for row in document["statements"][statement]:
            assert list(row["values"]) == [p["period"] for p in document["periods"]], (
                f"{statement}.{row['key']} is not aligned to the period columns"
            )
            assert set(row["values"]) == labels


def test_eight_quarterly_periods_are_labelled_from_their_own_filing() -> None:
    document = build_statement_table(fixture())

    quarterly = [period for period in document["periods"] if period["kind"] == "quarterly"]
    assert len(quarterly) == 8
    assert [period["period"] for period in quarterly] == [
        "2025Q2",
        "2025Q1",
        "2024Q3",
        "2024Q2",
        "2024Q1",
        "2023Q3",
        "2023Q2",
        "2023Q1",
    ]


def test_a_missing_tag_is_null_in_every_period_never_zero() -> None:
    document = build_statement_table(fixture(missing_gross_profit=True))

    gross = rows_of(document, "income")["gross_profit"]
    assert gross["values"]
    assert set(gross["values"].values()) == {None}
    assert gross["ttm"] is None and gross["yoy_pct"] is None


def test_a_10q_row_never_lands_in_an_annual_column() -> None:
    """The annual column is a 10-K period; a quarter ending there is not it."""

    rows = fixture()
    # A stray 10-Q whose period end collides with the newest fiscal year end.
    rows.append(
        obs(
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            42.0,
            end=ANNUAL_ENDS[-1],
            start="2025-06-29",
            form="10-Q",
            filed="2025-11-01",
            accn="0000320193-25-999999",
            fy=2026,
            fp="Q1",
        )
    )
    document = build_statement_table(rows)
    revenue = rows_of(document, "income")["revenue"]
    assert revenue["values"]["2025"] == 1900.0


# ---------------------------------------------------------------------------
# Growth and TTM
# ---------------------------------------------------------------------------


def test_yoy_is_the_newest_annual_against_the_year_before_it() -> None:
    document = build_statement_table(fixture())
    revenue = rows_of(document, "income")["revenue"]

    assert revenue["values"]["2025"] == 1900.0
    assert revenue["values"]["2024"] == 1800.0
    assert revenue["yoy_pct"] == pytest.approx((1900.0 - 1800.0) / 1800.0 * 100.0)


def test_ttm_rolls_the_annual_forward_with_the_year_to_date_pair() -> None:
    """TTM = latest annual + this year's YTD − last year's same YTD.

    The fixture's newest 10-Q is Q2 2025 (six months to 2025-06-28) and the
    newest annual is FY2024. Four quarters cannot be summed — a fiscal Q4 is
    never filed as a 10-Q — so the roll-forward is the only honest TTM.
    """

    rows = [
        *annual_series("RevenueFromContractWithCustomerExcludingAssessedTax", 1000.0, 0.0)[:9],
        # The newest annual is FY2024, revenue 1000.
        obs(
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            300.0,
            end="2025-03-29",
            start="2024-09-29",
            form="10-Q",
            filed="2025-05-01",
            accn="0000320193-25-q1",
            fy=2025,
            fp="Q1",
        ),
        obs(
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            620.0,
            end="2025-06-28",
            start="2024-09-29",
            form="10-Q",
            filed="2025-08-01",
            accn="0000320193-25-q2",
            fy=2025,
            fp="Q2",
        ),
        # The prior-year six months, from the Q2 2024 10-Q.
        obs(
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            560.0,
            end="2024-06-29",
            start="2023-09-30",
            form="10-Q",
            filed="2024-08-01",
            accn="0000320193-24-q2",
            fy=2024,
            fp="Q2",
        ),
    ]
    document = build_statement_table(rows)
    revenue = rows_of(document, "income")["revenue"]

    assert revenue["ttm"] == pytest.approx(1000.0 + 620.0 - 560.0)


def test_ttm_is_the_annual_when_the_newest_document_is_the_annual() -> None:
    document = build_statement_table(fixture())
    revenue = rows_of(document, "income")["revenue"]
    # FY2025 closed after the newest quarter, so the annual is the TTM.
    assert revenue["ttm"] == 1900.0


def test_a_balance_line_has_no_ttm() -> None:
    document = build_statement_table(fixture())
    assets = rows_of(document, "balance")["total_assets"]
    assert assets["ttm"] is None


def test_a_missing_prior_year_leaves_yoy_null() -> None:
    document = build_statement_table(
        annual_series(
            "NetIncomeLoss",
            100.0,
            10.0,
            missing={ANNUAL_ENDS[-2]},
        )
    )
    net = rows_of(document, "income")["net_income"]
    assert net["yoy_pct"] is None


# ---------------------------------------------------------------------------
# Derived free cash flow
# ---------------------------------------------------------------------------


def test_free_cash_flow_is_operating_cash_flow_minus_absolute_capex() -> None:
    document = build_statement_table(fixture())
    cash = rows_of(document, "cash_flow")
    operating = cash["operating_cash_flow"]["values"]["2025"]
    capex = cash["capex"]["values"]["2025"]

    assert operating == pytest.approx(400.0 + 30.0 * 9)
    assert cash["free_cash_flow"]["values"]["2025"] == pytest.approx(
        operating - abs(capex)
    )


def test_free_cash_flow_is_null_without_capex() -> None:
    document = build_statement_table(fixture(missing_capex=True))
    free = rows_of(document, "cash_flow")["free_cash_flow"]
    assert set(free["values"].values()) == {None}
    assert free["ttm"] is None


# ---------------------------------------------------------------------------
# Restatement
# ---------------------------------------------------------------------------


def test_a_restatement_wins_inside_its_period_without_moving_the_column() -> None:
    rows = [
        *annual_series("NetIncomeLoss", 100.0, 10.0),
        obs(
            "NetIncomeLoss",
            999.0,
            end=ANNUAL_ENDS[-1],
            start=_year_start(ANNUAL_ENDS[-1]),
            form="10-K/A",
            filed="2026-06-01",
            accn="0000320193-26-000099",
            fy=2025,
            fp="FY",
        ),
    ]
    document = build_statement_table(rows)
    net = rows_of(document, "income")["net_income"]

    assert net["values"]["2025"] == 999.0
    assert document["as_of"] == ANNUAL_ENDS[-1]


# ---------------------------------------------------------------------------
# The payload wrapper
# ---------------------------------------------------------------------------


def payload(observations: Iterable[FactObservation]) -> dict[str, Any]:
    """One ``companyfacts`` document from observations, for the parser."""

    facts: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for observation in observations:
        entry: dict[str, Any] = {
            "end": observation.period_end,
            "val": observation.value,
            "accn": observation.accession,
            "fy": observation.fiscal_year,
            "fp": observation.fiscal_period,
            "form": observation.form,
            "filed": observation.filed,
        }
        if observation.start:
            entry["start"] = observation.start
        facts.setdefault(observation.tag, {}).setdefault(observation.unit, []).append(entry)
    return {
        "cik": AAPL_CIK,
        "entityName": "Apple Inc.",
        "facts": {"us-gaap": {tag: {"units": units} for tag, units in facts.items()}},
    }


def test_statement_document_parses_a_companyfacts_payload() -> None:
    document = statement_document("AAPL", payload(fixture()))

    assert document is not None
    assert document["symbol"] == "AAPL"
    assert document["source"] == "sec-xbrl"
    assert len([p for p in document["periods"] if p["kind"] == "annual"]) == 10


def test_statement_document_with_no_usable_facts_is_none() -> None:
    assert statement_document("AAPL", {"facts": {"us-gaap": {}}}) is None
    assert statement_document("AAPL", {"nothing": True}) is None


# ---------------------------------------------------------------------------
# The route
# ---------------------------------------------------------------------------


def _master() -> SecurityMaster:
    master = SecurityMaster()
    master.load(
        [
            _record("AAPL", "Apple Inc.", AAPL_CIK),
            _record("PRIV", "Private Holdings Inc.", None),
        ],
        Provenance(source="sec", as_of="2026-10-20"),
    )
    return master


def _record(symbol: str, name: str, cik: str | None) -> Any:
    from app.engine.model import SecurityRecord

    return SecurityRecord(
        symbol=symbol,
        name=name,
        exchange="XNAS",
        mic="XNAS",
        cik=cik,
        asset_class="stock",
        currency="USD",
        sector=None,
        industry=None,
        country="United States",
    )


def _service() -> EngineService:
    dataset = EngineDataset(
        fetched_at="2026-10-26T12:00:00+00:00",
        security_master=_master(),
        facts={},
        errors={},
    )
    return EngineService.from_dataset(dataset)


@asynccontextmanager
async def route_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    service: EngineService | None = None,
) -> AsyncIterator[httpx2.AsyncClient]:
    monkeypatch.setenv("MEE_DB_PATH", str(tmp_path / "mee.db"))
    monkeypatch.setenv("MEE_EXPORT_DIR", str(tmp_path / "exports"))
    monkeypatch.setenv("MEE_ENGINE_CACHE_DIR", str(tmp_path / "sec-cache"))

    from app.main import create_app

    application = create_app()
    if service is not None:
        application.state.engine = service
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=application), base_url="http://mee.test"
    ) as client:
        yield client


def write_cache(tmp_path: Path, document: dict[str, Any]) -> None:
    cache = tmp_path / "sec-cache"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / f"CIK{AAPL_CIK}-companyfacts.json").write_text(json.dumps(document))


async def test_statements_route_serves_the_cached_issuer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_cache(tmp_path, payload(fixture()))

    async with route_client(tmp_path, monkeypatch, _service()) as client:
        response = await client.get("/engine/statements/AAPL")

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"symbol", "source", "as_of", "periods", "statements"}
    assert body["source"] == "sec-xbrl"
    assert body["symbol"] == "AAPL"
    assert len(body["statements"]["income"]) == 5
    assert len(body["statements"]["balance"]) == 8
    assert len(body["statements"]["cash_flow"]) == 5


async def test_statements_route_unknown_symbol_is_404_no_statements(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_cache(tmp_path, payload(fixture()))

    async with route_client(tmp_path, monkeypatch, _service()) as client:
        response = await client.get("/engine/statements/ZZZZ")

    assert response.status_code == 404, response.text
    assert response.json()["error"] == "no_statements"


async def test_statements_route_without_cached_facts_is_404_no_statements(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with route_client(tmp_path, monkeypatch, _service()) as client:
        response = await client.get("/engine/statements/AAPL")

    assert response.status_code == 404, response.text
    assert response.json()["error"] == "no_statements"


async def test_statements_route_without_a_dataset_is_503(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with route_client(tmp_path, monkeypatch, None) as client:
        response = await client.get("/engine/statements/AAPL")

    assert response.status_code == 503, response.text
    assert response.json()["error"] == "screen_unavailable"


async def test_statements_route_symbol_without_a_cik_is_404_no_statements(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with route_client(tmp_path, monkeypatch, _service()) as client:
        response = await client.get("/engine/statements/PRIV")

    assert response.status_code == 404, response.text
    assert response.json()["error"] == "no_statements"
