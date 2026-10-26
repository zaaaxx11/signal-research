"""RED contract for R5C: the annual statements table and its route.

Every assertion below is a sentence from ``briefs/market-engine/R5C-CONTRACT.md``
§1–§4:

1. only ``10-K`` observations are annual candidates, a flow needs a 330..400 day
   duration and a balance instant needs only ``period_end``;
2. the period set is the union of the selected ``period_end`` values, newest
   first, and every line array is aligned to it;
3. a missing line/period is a visible ``not_reported`` null — never ``0`` — and
   a restatement wins inside its own period without moving ``as_of``;
4. ``fcf`` is derived from operating cash flow minus ``abs(capex)``, and is an
   ``absent`` null when either input is missing;
5. ``GET /engine/statements/{symbol}`` validates ``periods`` before lookup and
   answers the house ``{error, errorMessage}`` envelopes.

The observations are handwritten tuples with fixed dates, so no network, clock
or fixture is involved. The route tests drive the real ASGI app and patch both
collector seams the route could hold — ``app.api.engine`` and
``app.engine.statements`` — with one recording double, so a call that bypasses
the double is still visible in the shared call log.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, cast

import httpx2
import pytest

from app.engine.facts import FactObservation
from app.engine.ingest import EngineDataset, SecIngestError
from app.engine.model import Provenance, SecurityRecord
from app.engine.security import SecurityMaster
from app.engine.service import EngineService
from app.engine.statements import build_statements

#: One annual period: ``(period_end, start, fiscal_year, accession, filed)``.
Period = tuple[str, str, int, str, str]

#: One reported line: ``(statement, line, us-gaap tag, FY2025, FY2024, kind)``.
#: ``kind`` is ``flow`` (needs a 365-day start), ``flow_eps`` (a per-share flow)
#: or ``instant`` (a balance point with no start).
LineSpec = tuple[str, str, str, float, float, str]

#: Three consecutive annual periods, each exactly 365 days long.
FY2025: Period = (
    "2025-09-27",
    "2024-09-27",
    2025,
    "0000320193-25-000079",
    "2025-11-01",
)
FY2024: Period = (
    "2024-09-28",
    "2023-09-29",
    2024,
    "0000320193-24-000123",
    "2024-11-01",
)
FY2023: Period = (
    "2023-09-30",
    "2022-09-30",
    2023,
    "0000320193-23-000106",
    "2023-11-03",
)

#: The two columns the builder must produce from a full observation set.
PERIODS: tuple[Period, Period] = (FY2025, FY2024)

REVENUE_TAG = "RevenueFromContractWithCustomerExcludingAssessedTax"
CAPEX_TAG = "PaymentsToAcquirePropertyPlantAndEquipment"

#: Every reported contract line, with a synthetic value per period.
LINE_SPECS: tuple[LineSpec, ...] = (
    ("income", "revenue", REVENUE_TAG, 416_161_000_000.0, 391_035_000_000.0, "flow"),
    ("income", "gross_profit", "GrossProfit", 180_683_000_000.0, 170_782_000_000.0, "flow"),
    (
        "income",
        "operating_income",
        "OperatingIncomeLoss",
        123_216_000_000.0,
        114_301_000_000.0,
        "flow",
    ),
    ("income", "net_income", "NetIncomeLoss", 112_010_000_000.0, 101_956_000_000.0, "flow"),
    (
        "income",
        "eps_diluted",
        "EarningsPerShareDiluted",
        7.46,
        6.75,
        "flow_eps",
    ),
    ("balance", "assets", "Assets", 359_241_000_000.0, 344_085_000_000.0, "instant"),
    (
        "balance",
        "liabilities",
        "Liabilities",
        285_508_000_000.0,
        274_764_000_000.0,
        "instant",
    ),
    (
        "balance",
        "equity",
        "StockholdersEquity",
        73_733_000_000.0,
        69_321_000_000.0,
        "instant",
    ),
    (
        "balance",
        "cash_and_equivalents",
        "CashAndCashEquivalentsAtCarryingValue",
        28_162_000_000.0,
        26_509_000_000.0,
        "instant",
    ),
    (
        "balance",
        "total_debt",
        "LongTermDebt",
        96_494_000_000.0,
        101_326_000_000.0,
        "instant",
    ),
    (
        "cash_flow",
        "operating_cash_flow",
        "NetCashProvidedByUsedInOperatingActivities",
        118_254_000_000.0,
        110_543_000_000.0,
        "flow",
    ),
    ("cash_flow", "capex", CAPEX_TAG, -12_345_000_000.0, -10_959_000_000.0, "flow"),
)

INCOME_LINES = frozenset(
    {"revenue", "gross_profit", "operating_income", "net_income", "eps_diluted"}
)
BALANCE_LINES = frozenset(
    {"assets", "liabilities", "equity", "cash_and_equivalents", "total_debt"}
)
CASH_FLOW_LINES = frozenset({"operating_cash_flow", "capex", "fcf"})


# ---------------------------------------------------------------------------
# Observation factory
# ---------------------------------------------------------------------------


def expected_unit(line: str) -> str:
    """The contract unit of ``line``: per-share is ``unknown``, money is ``USD``."""

    return "unknown" if line == "eps_diluted" else "USD"


def _observation(
    *,
    tag: str,
    value: float,
    period_end: str,
    start: str | None,
    fiscal_year: int,
    accession: str,
    filed: str,
    unit: str = "USD",
    form: str = "10-K",
) -> FactObservation:
    """One synthetic filing row with the metadata a statement table reads."""

    return FactObservation(
        tag=tag,
        value=value,
        unit=unit,
        period_end=period_end,
        start=start,
        fiscal_year=fiscal_year,
        form=form,
        filed=filed,
        accession=accession,
    )


def period_observations(
    period: Period, *, skip: Sequence[str] = ()
) -> list[FactObservation]:
    """One annual 10-K row per contract line for ``period``, minus ``skip``."""

    period_end, start, fiscal_year, accession, filed = period
    rows: list[FactObservation] = []
    for _statement, line, tag, newest, prior, kind in LINE_SPECS:
        if line in skip:
            continue
        rows.append(
            _observation(
                tag=tag,
                value=newest if fiscal_year == FY2025[2] else prior,
                unit="USD/shares" if kind == "flow_eps" else "USD",
                period_end=period_end,
                start=None if kind == "instant" else start,
                fiscal_year=fiscal_year,
                accession=accession,
                filed=filed,
            )
        )
    return rows


def annual_observations(*, skip: Sequence[str] = ()) -> list[FactObservation]:
    """Both contract periods, every reported line, in document order."""

    rows: list[FactObservation] = []
    for period in PERIODS:
        rows.extend(period_observations(period, skip=skip))
    return rows


def _null_cell(period_end: str, unit: str) -> dict[str, Any]:
    """The exact null cell the contract requires for a missing line/period."""

    return {
        "value": None,
        "unit": unit,
        "provenance": {
            "source": "sec",
            "as_of": period_end,
            "status": "not_reported",
            "url": None,
            "inputs": [],
        },
    }


def _period_header(period: Period) -> dict[str, Any]:
    """The period object the document publishes: end, fiscal year, form."""

    return {"period_end": period[0], "fiscal_year": period[2], "form": "10-K"}


def build(
    observations: Sequence[FactObservation], *, periods: int = 4
) -> dict[str, Any]:
    """``build_statements`` cast to the JSON-shaped mapping these tests index."""

    return cast(dict[str, Any], build_statements(observations, periods=periods))


# ---------------------------------------------------------------------------
# §1 The document, alignment and provenance
# ---------------------------------------------------------------------------


def test_two_annual_periods_are_newest_first_with_every_line_aligned() -> None:
    """The table has two columns, FY2025 before FY2024, and every line is two long."""

    document = build(annual_observations())

    assert set(document) == {"as_of", "periods", "statements"}
    assert document["as_of"] == "2025-09-27"
    assert document["periods"] == [_period_header(FY2025), _period_header(FY2024)]

    statements = document["statements"]
    assert isinstance(statements, dict)
    assert set(statements) == {"income", "balance", "cash_flow"}
    assert set(statements["income"]) == INCOME_LINES
    assert set(statements["balance"]) == BALANCE_LINES
    assert set(statements["cash_flow"]) == CASH_FLOW_LINES

    for statement, line, *_rest in LINE_SPECS:
        cells = statements[statement][line]
        assert len(cells) == len(document["periods"]), (
            f"{statement}.{line} must be aligned to the period columns"
        )


def test_every_reported_cell_carries_value_unit_and_filing_provenance() -> None:
    """Each cell is the period's value, its contract unit, and tag + accession."""

    document = build(annual_observations())
    statements = document["statements"]

    for statement, line, tag, fy2025, fy2024, _kind in LINE_SPECS:
        for index, (period, value) in enumerate(zip(PERIODS, (fy2025, fy2024))):
            cell = statements[statement][line][index]
            assert cell == {
                "value": value,
                "unit": expected_unit(line),
                "provenance": {
                    "source": "sec",
                    "as_of": period[0],
                    "status": "ok",
                    "url": None,
                    "inputs": [tag, period[3]],
                },
            }, f"{statement}.{line}[{index}]"


def test_eps_diluted_is_unknown_and_money_is_usd() -> None:
    """The per-share bridge has no canonical unit; every money line is USD."""

    statements = build(annual_observations())["statements"]

    assert [cell["unit"] for cell in statements["income"]["eps_diluted"]] == [
        "unknown",
        "unknown",
    ]
    for statement, line, *_rest in LINE_SPECS:
        if line == "eps_diluted":
            continue
        assert [cell["unit"] for cell in statements[statement][line]] == ["USD", "USD"]


# ---------------------------------------------------------------------------
# §1 Period eligibility: forms, duration and startless balance points
# ---------------------------------------------------------------------------


def test_ten_q_rows_are_never_annual_candidates() -> None:
    """A quarterly period must not add a column or a value to the annual table."""

    rows = annual_observations()
    rows.extend(
        [
            _observation(
                tag=REVENUE_TAG,
                value=99_000_000_000.0,
                period_end="2025-06-28",
                start="2025-03-30",
                fiscal_year=2025,
                accession="0000320193-25-000050",
                filed="2025-11-02",
                form="10-Q",
            ),
            _observation(
                tag="Assets",
                value=999.0,
                period_end="2025-06-28",
                start=None,
                fiscal_year=2025,
                accession="0000320193-25-000050",
                filed="2025-11-02",
                form="10-Q",
            ),
        ]
    )

    document = build(rows)

    assert document["periods"] == [_period_header(FY2025), _period_header(FY2024)]
    assert document["statements"]["income"]["revenue"][0]["value"] == 416_161_000_000.0
    assert document["statements"]["balance"]["assets"][0]["value"] == 359_241_000_000.0


def test_a_ninety_day_flow_is_ignored_and_a_startless_balance_is_kept() -> None:
    """A quarter-long flow cannot fill an annual cell; a balance point needs no start."""

    quarter_start = "2025-06-29"  # exactly 90 days before FY2025's end
    rows = [
        _observation(
            tag=REVENUE_TAG,
            value=416_161_000_000.0,
            period_end=FY2025[0],
            start=FY2025[1],
            fiscal_year=2025,
            accession=FY2025[3],
            filed="2024-11-01",
        ),
        _observation(
            tag=REVENUE_TAG,
            value=999_000_000_000.0,
            period_end=FY2025[0],
            start=quarter_start,
            fiscal_year=2025,
            accession="0000320193-25-000099",
            filed="2025-11-02",
        ),
        _observation(
            tag="GrossProfit",
            value=777_000_000_000.0,
            period_end=FY2025[0],
            start=quarter_start,
            fiscal_year=2025,
            accession="0000320193-25-000099",
            filed="2025-11-02",
        ),
        _observation(
            tag="Assets",
            value=359_241_000_000.0,
            period_end=FY2025[0],
            start=None,
            fiscal_year=2025,
            accession=FY2025[3],
            filed=FY2025[4],
        ),
    ]

    document = build(rows)
    income = document["statements"]["income"]

    assert document["periods"] == [_period_header(FY2025)]
    assert income["revenue"][0]["value"] == 416_161_000_000.0, (
        "a 90-day flow filed later must not win the annual cell"
    )
    assert income["gross_profit"] == [_null_cell("2025-09-27", "USD")], (
        "a quarter-long gross profit is not an annual gross profit"
    )
    assert document["statements"]["balance"]["assets"][0]["value"] == 359_241_000_000.0


# ---------------------------------------------------------------------------
# §1 Restatements and deterministic ties
# ---------------------------------------------------------------------------


def test_a_restatement_wins_inside_its_period_without_moving_as_of() -> None:
    """The later filing replaces the value; the column stays the period end."""

    rows = [
        _observation(
            tag=REVENUE_TAG,
            value=400_000_000_000.0,
            period_end=FY2025[0],
            start=FY2025[1],
            fiscal_year=2025,
            accession="0000320193-24-000123",
            filed="2024-11-01",
        ),
        _observation(
            tag=REVENUE_TAG,
            value=416_161_000_000.0,
            period_end=FY2025[0],
            start=FY2025[1],
            fiscal_year=2025,
            accession="0000320193-25-000099",
            filed="2025-12-01",
        ),
    ]

    document = build(rows)
    cell = document["statements"]["income"]["revenue"][0]

    assert document["as_of"] == "2025-09-27"
    assert cell["value"] == 416_161_000_000.0
    assert cell["provenance"]["as_of"] == "2025-09-27"
    assert cell["provenance"]["inputs"] == [
        REVENUE_TAG,
        "0000320193-25-000099",
    ]


def test_equal_filed_dates_break_on_the_newer_accession() -> None:
    """``filed`` then ``accession`` decides, independent of document order."""

    rows = [
        _observation(
            tag=REVENUE_TAG,
            value=222.0,
            period_end=FY2025[0],
            start=FY2025[1],
            fiscal_year=2025,
            accession="0000320193-25-000002",
            filed="2025-12-01",
        ),
        _observation(
            tag=REVENUE_TAG,
            value=111.0,
            period_end=FY2025[0],
            start=FY2025[1],
            fiscal_year=2025,
            accession="0000320193-25-000001",
            filed="2025-12-01",
        ),
    ]

    cell = build(rows)["statements"]["income"]["revenue"][0]

    assert cell["value"] == 222.0
    assert cell["provenance"]["inputs"] == [
        REVENUE_TAG,
        "0000320193-25-000002",
    ]


# ---------------------------------------------------------------------------
# §1 Missing lines and periods stay visible
# ---------------------------------------------------------------------------


def test_missing_line_and_period_are_null_cells_never_zero() -> None:
    """The union of periods appears once, and an absent value is null, not 0."""

    rows = [
        _observation(
            tag=REVENUE_TAG,
            value=416_161_000_000.0,
            period_end=FY2025[0],
            start=FY2025[1],
            fiscal_year=2025,
            accession=FY2025[3],
            filed=FY2025[4],
        ),
        _observation(
            tag="Assets",
            value=344_085_000_000.0,
            period_end=FY2024[0],
            start=None,
            fiscal_year=2024,
            accession=FY2024[3],
            filed=FY2024[4],
        ),
    ]

    document = build(rows)
    income = document["statements"]["income"]
    balance = document["statements"]["balance"]

    assert [period["period_end"] for period in document["periods"]] == [
        "2025-09-27",
        "2024-09-28",
    ]
    assert income["revenue"][0]["value"] == 416_161_000_000.0
    assert income["revenue"][1] == _null_cell("2024-09-28", "USD")
    assert balance["assets"][0] == _null_cell("2025-09-27", "USD")
    assert balance["assets"][1]["value"] == 344_085_000_000.0
    assert income["gross_profit"] == [
        _null_cell("2025-09-27", "USD"),
        _null_cell("2024-09-28", "USD"),
    ], "a line with no rows is still two visible absences"

    for statement, line, *_rest in LINE_SPECS:
        for cell in document["statements"][statement][line]:
            assert cell["value"] is None or cell["value"] != 0, (
                f"{statement}.{line} must never invent a zero"
            )


def test_no_observations_invents_no_periods() -> None:
    """An empty input is an empty table with empty arrays, not four blank years."""

    document = build(())

    assert document["as_of"] is None
    assert document["periods"] == []
    assert set(document["statements"]) == {"income", "balance", "cash_flow"}
    for line in INCOME_LINES:
        assert document["statements"]["income"][line] == []
    for line in BALANCE_LINES:
        assert document["statements"]["balance"][line] == []
    for line in CASH_FLOW_LINES:
        assert document["statements"]["cash_flow"][line] == []


def test_periods_one_truncates_to_the_newest_column() -> None:
    """``periods=1`` keeps FY2025 and truncates every line with it."""

    rows: list[FactObservation] = []
    for period in (FY2025, FY2024, FY2023):
        rows.append(
            _observation(
                tag=REVENUE_TAG,
                value=416_161_000_000.0
                if period is FY2025
                else 391_035_000_000.0,
                period_end=period[0],
                start=period[1],
                fiscal_year=period[2],
                accession=period[3],
                filed=period[4],
            )
        )

    document = build(rows, periods=1)

    assert document["as_of"] == "2025-09-27"
    assert document["periods"] == [_period_header(FY2025)]
    assert document["statements"]["income"]["revenue"] == [
        {
            "value": 416_161_000_000.0,
            "unit": "USD",
            "provenance": {
                "source": "sec",
                "as_of": "2025-09-27",
                "status": "ok",
                "url": None,
                "inputs": [REVENUE_TAG, FY2025[3]],
            },
        }
    ]
    assert document["statements"]["income"]["gross_profit"] == [
        _null_cell("2025-09-27", "USD")
    ]


# ---------------------------------------------------------------------------
# §1 Derived free cash flow
# ---------------------------------------------------------------------------


def test_fcf_is_operating_cash_flow_minus_absolute_capex() -> None:
    """Both reported inputs produce one derived value per period."""

    document = build(annual_observations())
    cells = document["statements"]["cash_flow"]["fcf"]

    assert [cell["value"] for cell in cells] == [
        105_909_000_000.0,
        99_584_000_000.0,
    ]
    for cell, period in zip(cells, PERIODS):
        assert cell["unit"] == "USD"
        provenance = cell["provenance"]
        assert provenance["source"] == "derived"
        assert provenance["status"] == "derived"
        assert provenance["as_of"] == period[0]


def test_fcf_without_capex_is_an_absent_null() -> None:
    """A missing capex makes that period's FCF absent, never negative CFO."""

    rows = [
        row
        for row in annual_observations()
        if row.tag != CAPEX_TAG or row.period_end == FY2025[0]
    ]

    cells = build(rows)["statements"]["cash_flow"]["fcf"]

    assert cells[0]["value"] == 105_909_000_000.0
    assert cells[1]["value"] is None
    assert cells[1]["unit"] == "USD"
    assert cells[1]["provenance"]["status"] == "absent"
    assert cells[1]["provenance"]["as_of"] == "2024-09-28"
    assert cells[1]["provenance"]["inputs"] == []


# ---------------------------------------------------------------------------
# §1 Determinism
# ---------------------------------------------------------------------------


def test_repeated_builds_produce_equal_documents() -> None:
    """Two builds of one observation sequence are byte-for-byte equal dicts."""

    observations = tuple(annual_observations())

    first = build(observations)
    second = build(observations)

    assert first == second


# ---------------------------------------------------------------------------
# §3 Route — identity fixture, collector double and ASGI client
# ---------------------------------------------------------------------------


AAPL_CIK = "0000320193"


def _record(symbol: str, name: str, cik: str | None) -> SecurityRecord:
    """One identity row with the fixture's fixed venue, class and currency."""

    return SecurityRecord(
        symbol=symbol,
        name=name,
        cik=cik,
        exchange="Nasdaq",
        mic="XNAS",
        asset_class="equity",
        currency="USD",
        country="United States",
        sector=None,
        industry="Electronic Computers",
    )


def _master() -> SecurityMaster:
    """AAPL with a CIK and PRIV without one, loaded as one identity document."""

    master = SecurityMaster()
    master.load(
        [
            _record("AAPL", "Apple Inc.", AAPL_CIK),
            _record("PRIV", "Private Holdings Inc.", None),
        ],
        Provenance(source="sec", as_of="2026-10-20"),
    )
    return master


def _service() -> EngineService:
    """The composition a deployment uses after ingestion: identity only."""

    dataset = EngineDataset(
        fetched_at="2026-10-26T12:00:00+00:00",
        security_master=_master(),
        facts={},
        errors={},
    )
    return EngineService.from_dataset(dataset)


def collected_document() -> dict[str, Any]:
    """The document a collector would hand the route: one column, three groups."""

    return {
        "symbol": "AAPL",
        "as_of": "2025-09-27",
        "periods": [_period_header(FY2025)],
        "statements": {
            "income": {
                "revenue": [
                    {
                        "value": 416_161_000_000.0,
                        "unit": "USD",
                        "provenance": {
                            "source": "sec",
                            "as_of": "2025-09-27",
                            "status": "ok",
                            "url": None,
                            "inputs": [REVENUE_TAG, FY2025[3]],
                        },
                    }
                ]
            },
            "balance": {
                "assets": [
                    {
                        "value": 359_241_000_000.0,
                        "unit": "USD",
                        "provenance": {
                            "source": "sec",
                            "as_of": "2025-09-27",
                            "status": "ok",
                            "url": None,
                            "inputs": ["Assets", FY2025[3]],
                        },
                    }
                ]
            },
            "cash_flow": {
                "fcf": [
                    {
                        "value": 105_909_000_000.0,
                        "unit": "USD",
                        "provenance": {
                            "source": "derived",
                            "as_of": "2025-09-27",
                            "status": "derived",
                            "url": None,
                            "inputs": [],
                        },
                    }
                ]
            },
        },
    }


class FakeCollector:
    """A collector double that records the route's call and answers a document."""

    def __init__(
        self,
        *,
        document: dict[str, Any] | None = None,
        error: BaseException | None = None,
    ) -> None:
        self.document = document if document is not None else collected_document()
        self.error = error
        self.calls: list[dict[str, object]] = []

    async def __call__(
        self,
        symbol: str,
        cik: str,
        *,
        client: Any = None,
        cache_dir: Path | None = None,
        periods: int = 4,
    ) -> dict[str, object]:
        """Record one call, then answer the canned document or raise ``error``."""

        self.calls.append(
            {
                "symbol": symbol,
                "cik": cik,
                "periods": periods,
                "cache_dir": cache_dir,
                "client": client,
            }
        )
        if self.error is not None:
            raise self.error
        return self.document


def install_collector(
    monkeypatch: pytest.MonkeyPatch, fake: FakeCollector
) -> None:
    """Patch both collector seams the route could hold with one shared double.

    The route may import ``collect_statements`` into ``app.api.engine`` or call
    it through ``app.engine.statements``; patching both means the double is
    reached either way and its single call log proves exactly one collection.
    """

    monkeypatch.setattr("app.engine.statements.collect_statements", fake)
    monkeypatch.setattr("app.api.engine.collect_statements", fake, raising=False)


@asynccontextmanager
async def route_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    service: EngineService | None = None,
) -> AsyncIterator[httpx2.AsyncClient]:
    """An ASGI client over a fresh app whose engine service is ``service``."""

    monkeypatch.setenv("MEE_DB_PATH", str(tmp_path / "mee.db"))
    monkeypatch.setenv("MEE_EXPORT_DIR", str(tmp_path / "exports"))

    from app.main import create_app

    application = create_app()
    if service is not None:
        application.state.engine = service
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=application), base_url="http://mee.test"
    ) as client:
        yield client


def ten_digit_cik(value: object) -> str:
    """The ten-digit spelling of a CIK, whichever form the route passed."""

    digits = "".join(character for character in str(value) if character.isdigit())
    return digits.zfill(10)


# ---------------------------------------------------------------------------
# §3 Route — 200 documents
# ---------------------------------------------------------------------------


async def test_statements_route_returns_the_collected_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """200 is the collector's document with the four contract keys and nothing else."""

    fake = FakeCollector()
    install_collector(monkeypatch, fake)

    async with route_client(tmp_path, monkeypatch, _service()) as client:
        response = await client.get("/engine/statements/AAPL?periods=4")

    assert response.status_code == 200, response.text
    body = response.json()

    assert set(body) == {"symbol", "as_of", "periods", "statements"}
    assert body == collected_document()
    assert len(fake.calls) == 1, "the route collects exactly once per request"
    call = fake.calls[0]
    assert call["symbol"] == "AAPL"
    assert ten_digit_cik(call["cik"]) == AAPL_CIK
    assert call["periods"] == 4


async def test_statements_route_symbol_without_a_cik_is_an_empty_200(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A listed symbol with no CIK has no statements, and says why."""

    fake = FakeCollector()
    install_collector(monkeypatch, fake)

    async with route_client(tmp_path, monkeypatch, _service()) as client:
        response = await client.get("/engine/statements/PRIV?periods=4")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body == {
        "symbol": "PRIV",
        "as_of": None,
        "periods": [],
        "statements": {},
        "provider_errors": ["no CIK"],
    }
    assert fake.calls == [], "there is no CIK to collect against"


# ---------------------------------------------------------------------------
# §3 Route — refusals
# ---------------------------------------------------------------------------


async def test_statements_route_unknown_symbol_is_404(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A symbol outside the loaded master is ``security_not_found``."""

    fake = FakeCollector()
    install_collector(monkeypatch, fake)

    async with route_client(tmp_path, monkeypatch, _service()) as client:
        response = await client.get("/engine/statements/ZZZZ?periods=4")

    assert response.status_code == 404, response.text
    assert response.json().get("error") == "security_not_found"
    assert fake.calls == []


async def test_statements_route_without_a_dataset_is_503(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing ingested is ``screen_unavailable``, not an empty statement table."""

    fake = FakeCollector()
    install_collector(monkeypatch, fake)

    async with route_client(tmp_path, monkeypatch, None) as client:
        response = await client.get("/engine/statements/AAPL?periods=4")

    assert response.status_code == 503, response.text
    assert response.json().get("error") == "screen_unavailable"
    assert fake.calls == []


async def test_statements_route_collector_failure_is_503(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed SEC fetch is ``statements_unavailable``, not an empty table."""

    fake = FakeCollector(
        error=SecIngestError("CIK0000320193 did not answer a companyfacts document")
    )
    install_collector(monkeypatch, fake)

    async with route_client(tmp_path, monkeypatch, _service()) as client:
        response = await client.get("/engine/statements/AAPL?periods=4")

    assert response.status_code == 503, response.text
    assert response.json().get("error") == "statements_unavailable"
    assert len(fake.calls) == 1


@pytest.mark.parametrize(
    ("symbol", "periods"),
    [("AAPL", "0"), ("AAPL", "11"), ("AAPL", "x"), ("ZZZZ", "0")],
    ids=["zero", "eleven", "text", "before_lookup"],
)
async def test_statements_route_rejects_an_invalid_periods_with_422(
    symbol: str,
    periods: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``periods`` is an integer 1..10, validated before the identity lookup."""

    fake = FakeCollector()
    install_collector(monkeypatch, fake)

    async with route_client(tmp_path, monkeypatch, _service()) as client:
        response = await client.get(f"/engine/statements/{symbol}?periods={periods}")

    assert response.status_code == 422, response.text
    assert response.json().get("error") == "invalid_limit"
    assert fake.calls == [], "a rejected query never reaches the collector"
