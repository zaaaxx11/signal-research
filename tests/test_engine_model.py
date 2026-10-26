from __future__ import annotations

import pytest

from app.engine.model import (
    Cell,
    CellStatus,
    LicenseClass,
    MetricSpec,
    Provenance,
    SecurityRecord,
    SourceSpec,
    Unit,
)


def test_license_public_requires_redistribution_for_public_use() -> None:
    public = SourceSpec(
        name="sec",
        license_class=LicenseClass.PUBLIC,
        redistribution=True,
        attribution=None,
        terms_url="https://www.sec.gov/",
        features=frozenset({"reference", "fundamentals"}),
        rate_limit=(10, 1.0),
    )
    internal = SourceSpec(
        name="finviz",
        license_class=LicenseClass.INTERNAL,
        redistribution=False,
        attribution=None,
        terms_url="https://finviz.com/",
        features=frozenset({"screen"}),
        rate_limit=(1, 5.0),
    )

    assert public.allows_public is True
    assert internal.allows_public is False


def test_source_spec_rejects_an_empty_name() -> None:
    with pytest.raises(ValueError, match="name"):
        SourceSpec(
            name=" ",
            license_class=LicenseClass.INTERNAL,
            redistribution=False,
            attribution=None,
            terms_url="https://example.test/",
            features=frozenset(),
            rate_limit=(1, 1.0),
        )


def test_a_non_null_cell_requires_provenance() -> None:
    with pytest.raises(ValueError, match="provenance"):
        Cell(value=1.0, unit=Unit.USD)


def test_null_cell_requires_a_non_ok_status() -> None:
    with pytest.raises(ValueError, match="null"):
        Cell(
            value=None,
            unit=Unit.USD,
            provenance=Provenance(
                source="sec",
                as_of="2026-10-26",
                status=CellStatus.OK,
            ),
        )


def test_absent_cell_keeps_null_and_a_reason() -> None:
    cell = Cell(
        value=None,
        unit=Unit.USD,
        provenance=Provenance(
            source="sec",
            as_of="2026-10-26",
            status=CellStatus.ABSENT,
        ),
    )

    assert cell.value is None
    assert cell.provenance is not None
    assert cell.provenance.status is CellStatus.ABSENT


def test_provenance_rejects_blank_source_and_as_of() -> None:
    with pytest.raises(ValueError, match="source"):
        Provenance(source="", as_of="2026-10-26", status=CellStatus.OK)
    with pytest.raises(ValueError, match="as_of"):
        Provenance(source="sec", as_of="", status=CellStatus.OK)


def test_metric_spec_is_frozen_and_normalises_key_and_views() -> None:
    metric = MetricSpec(
        key="market_cap",
        label="Market Cap",
        unit=Unit.USD,
        source_keys=("marketCap",),
        views=("overview", "valuation"),
        derivation=None,
    )

    assert metric.key == "market_cap"
    assert metric.views == ("overview", "valuation")
    with pytest.raises(AttributeError):
        metric.key = "other"  # type: ignore[misc]


def test_metric_spec_rejects_duplicate_views_and_blank_key() -> None:
    with pytest.raises(ValueError, match="views"):
        MetricSpec(
            key="pe",
            label="P/E",
            unit=Unit.RATIO,
            source_keys=("pe",),
            views=("overview", "overview"),
            derivation=None,
        )
    with pytest.raises(ValueError, match="key"):
        MetricSpec(
            key=" ",
            label="P/E",
            unit=Unit.RATIO,
            source_keys=("pe",),
            views=("overview",),
            derivation=None,
        )


def test_security_record_normalises_the_symbol_and_rejects_blank_name() -> None:
    record = SecurityRecord(
        symbol=" aapl ",
        name="Apple Inc.",
        cik="0000320193",
        exchange="NASDAQ",
        mic="XNAS",
        asset_class="stock",
        currency="usd",
        country="USA",
        sector="Technology",
        industry="Consumer Electronics",
        active=True,
    )

    assert record.symbol == "AAPL"
    assert record.currency == "USD"
    assert record.cik == "0000320193"

    with pytest.raises(ValueError, match="name"):
        SecurityRecord(
            symbol="AAPL",
            name=" ",
            cik=None,
            exchange=None,
            mic=None,
            asset_class="stock",
            currency="USD",
            country=None,
            sector=None,
            industry=None,
            active=True,
        )
