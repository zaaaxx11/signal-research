"""Universe-scale pagination contract (R3 §3): 250 identities, no network.

``app.engine.screener.apply`` already pages a row sequence; the screen tests
exercise it over a handful of rows. This file pins the same behaviour at the
size R3 ingests, where the interesting arithmetic is a short last page, a
disjoint page pair and an offset past the end. The master is synthetic on
purpose: the test proves the offset/total contract, not a fixture.

Everything here is expected to pass against the current code — ``apply`` needs
no R3 change. If one of these fails, pagination behaviour moved.
"""

from __future__ import annotations

from app.engine.dataset import DatasetRows
from app.engine.ingest import EngineDataset
from app.engine.model import Cell, Provenance, SecurityRecord
from app.engine.screener import ScreenResult, apply
from app.engine.security import SecurityMaster

FETCHED_AT = "2026-10-26T12:00:00+00:00"
INDEX_AS_OF = "2026-10-25"
UNIVERSE_SIZE = 250
PAGE_SIZE = 20
LAST_PAGE_SIZE = UNIVERSE_SIZE - (UNIVERSE_SIZE // PAGE_SIZE) * PAGE_SIZE
SYMBOLS: tuple[str, ...] = tuple(f"S{index:03d}" for index in range(UNIVERSE_SIZE))


def _master() -> SecurityMaster:
    """A master of :data:`UNIVERSE_SIZE` synthetic identities, one document."""

    master = SecurityMaster()
    records = tuple(
        SecurityRecord(
            symbol=symbol,
            name=f"Synthetic {symbol}",
            cik=f"{index + 1:010d}",
            exchange="Nasdaq",
            mic="XNAS",
            asset_class="equity",
            currency="USD",
            country="United States",
            sector=None,
            industry=None,
        )
        for index, symbol in enumerate(SYMBOLS)
    )
    master.load(
        records,
        Provenance(source="sec", as_of=INDEX_AS_OF),
    )
    return master


def _rows() -> tuple[dict[str, Cell], ...]:
    """The dataset's rows exactly as :class:`DatasetRows` emits them."""

    dataset = EngineDataset(
        fetched_at=FETCHED_AT,
        security_master=_master(),
        facts={},
        errors={},
    )
    return DatasetRows(dataset).rows()


def _tickers(result: ScreenResult) -> list[str]:
    """The ticker column of one screen page, in row order."""

    return [str(getattr(row["ticker"], "value")) for row in result.rows]


def test_dataset_rows_returns_the_whole_universe_in_symbol_order() -> None:
    """250 identities, one row each, ordered as the master iterates them."""

    rows = _rows()

    assert len(rows) == UNIVERSE_SIZE
    assert [str(row["ticker"].value) for row in rows] == list(SYMBOLS)


def test_first_page_reports_the_real_total_and_returns_the_first_page() -> None:
    """``total`` is the match count, never the page length."""

    result = apply(_rows(), {"limit": PAGE_SIZE, "offset": 0})

    assert result.total == UNIVERSE_SIZE
    assert result.returned == PAGE_SIZE
    assert (result.limit, result.offset) == (PAGE_SIZE, 0)
    assert _tickers(result) == list(SYMBOLS[:PAGE_SIZE])


def test_last_page_is_short_and_holds_the_final_symbols() -> None:
    """Offset 240 of 250 returns ten rows, not a padded page of twenty."""

    result = apply(_rows(), {"limit": PAGE_SIZE, "offset": 240})

    assert LAST_PAGE_SIZE == 10
    assert result.total == UNIVERSE_SIZE
    assert result.returned == LAST_PAGE_SIZE
    assert _tickers(result) == list(SYMBOLS[240:])


def test_page_one_and_page_thirteen_are_disjoint() -> None:
    """No ticker may appear on two pages, or a reader double-counts it."""

    rows = _rows()
    first = apply(rows, {"limit": PAGE_SIZE, "offset": 0})
    thirteenth = apply(rows, {"limit": PAGE_SIZE, "offset": 240})

    assert set(_tickers(first)).isdisjoint(_tickers(thirteenth))


def test_concatenating_every_page_reproduces_the_universe_exactly_once() -> None:
    """Every page in order, concatenated, is the master's order with no gaps."""

    rows = _rows()
    collected: list[str] = []
    offset = 0
    while offset < UNIVERSE_SIZE:
        page = apply(rows, {"limit": PAGE_SIZE, "offset": offset})
        assert page.total == UNIVERSE_SIZE
        assert page.offset == offset
        collected.extend(_tickers(page))
        offset += PAGE_SIZE

    assert len(collected) == UNIVERSE_SIZE
    assert collected == list(SYMBOLS)


def test_a_page_past_the_end_returns_no_rows_and_the_real_total() -> None:
    """An offset past the last row is an empty page, not an error or a zero total."""

    result = apply(_rows(), {"limit": PAGE_SIZE, "offset": 400})

    assert result.total == UNIVERSE_SIZE
    assert result.returned == 0
    assert result.rows == ()
