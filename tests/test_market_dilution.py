"""The dilution series: what the share-count history across filings answers.

``sh_dilution`` ("Dilution Risk") is published **unavailable** because "the scan
carries shares outstanding today, not the series" — every consumer today sees one
count, and a dilution *risk* computed from one count would have to be invented.
This suite is the contract for the answer that would un-invent it: the
``dei:EntityCommonStockSharesOutstanding`` rows across one issuer's filings, read
from the cached ``companyfacts`` document the ingest pass already wrote (no
network here — the fixture is fabricated and the cache is a ``tmp_path``).

Every rule is about not inventing a number:

* a filing series is a **timeseries**, so the answer is first, last, the change
  from first to last, the filing count it came from, and the newest **filing
  date** — never the clock the question was asked at;
* a missing series is ``None`` **plus a reason**, never ``0.0``;
* one filed count is a level, not a trend: first and last are answered and the
  trend is refused;
* duplicate period ends collapse to the newest filing, so a restatement of one
  period is that period again and not a second observation;
* a negative, zero, non-numeric, undated or wrongly-united row is skipped — and
  counted, because "counted and skipped" is the honest shape of "5 rows, 3 of
  them garbage".

The module under test *derives* this from the SEC layer
(:func:`app.engine.ingest.load_cached_companyfacts`, whose cache-entry name is
asserted below). It is additive-only: nothing here flips the control, and
nothing here writes to the cache.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from app.engine.ingest import facts_cache_path
from app.market import dilution
from app.market.dilution import (
    REASON_ALL_UNUSABLE,
    REASON_NO_DOCUMENT,
    REASON_NO_TAG,
    REASON_ONE_POINT,
    DilutionSeries,
    ShareCountPoint,
    extract_share_series,
    shares_outstanding_series,
)
from app.screen.descriptive import _DILUTION_REASON

#: A fabricated issuer. The number is the ten-digit CIK spelling the cache
#: contract keys on; nothing here resolves or contacts any registrant.
CIK = "0000320193"

#: A clean dilution history: +10%, then +10% — first→last is +21.0%, exercised
#: as an exact number rather than an approximation.
FIRST_COUNT = 100_000_000_000.0
MIDDLE_COUNT = 110_000_000_000.0
LAST_COUNT = 121_000_000_000.0


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def share_row(
    end: str,
    value: Any,
    *,
    accn: str = "0000320193-24-000001",
    form: str = "10-K",
    filed: str = "2024-11-01",
    unit: str = "shares",
    **extra: Any,
) -> dict[str, Any]:
    """One ``dei`` row shaped like EDGAR's, minus every optional field.

    ``entityName``/``frame``-style metadata is passed through ``extra`` so a test
    can add or drop fields without rewriting the helper; the fields the series
    rule reads (``end``, ``val``, ``accn``, ``filed``) are explicit.
    """

    row: dict[str, Any] = {
        "end": end,
        "val": value,
        "accn": accn,
        "fy": 2024,
        "fp": "FY",
        "form": form,
        "filed": filed,
    }
    row.update(extra)
    return row


def companyfacts(rows: list[dict[str, Any]] | None, **payload: Any) -> dict[str, Any]:
    """A ``companyfacts`` body carrying a ``dei`` share-count concept.

    ``rows=None`` writes the concept with no rows at all, which is a different
    absence from "the tag is not in the document" — both are covered below.
    """

    document: dict[str, Any] = {
        "cik": int(CIK),
        "entityName": "TEST CO",
        "facts": {
            "dei": {
                "EntityCommonStockSharesOutstanding": {
                    "label": "Entity Common Stock, Shares Outstanding",
                    "units": {"shares": rows if rows is not None else []},
                }
            }
        },
    }
    document.update(payload)
    return document


def cache_facts(
    cache_dir: Path, document: dict[str, Any], *, cik: str = CIK
) -> Path:
    """Write ``document`` at the ingest cache's own entry name, return the path.

    The name is the loader's contract (``CIK##########-companyfacts.json``),
    asserted in :func:`test_the_cache_entry_name_is_the_loader_contract`; a
    module that read any other spelling would be a second cache mechanism.
    """

    path = cache_dir / f"CIK{cik}-companyfacts.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def series(cache_dir: Path, rows: list[dict[str, Any]]) -> DilutionSeries:
    """Cache one fabricated document and read the series back through the module."""

    cache_facts(cache_dir, companyfacts(rows))
    return shares_outstanding_series(cache_dir, CIK)


# ---------------------------------------------------------------------------
# The series
# ---------------------------------------------------------------------------


class TestSeries:
    def test_the_cache_entry_name_is_the_loader_contract(self, tmp_path: Path) -> None:
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        cache_facts(cache_dir, companyfacts([]))

        # The module must read exactly what app.engine.ingest writes — reusing
        # the loader is the whole point, so its path contract is asserted here
        # rather than re-spelled in the module.
        expected = facts_cache_path(cache_dir, CIK)
        assert expected.exists()
        assert sorted(path.name for path in cache_dir.iterdir()) == [expected.name]

    def test_a_payload_can_be_parsed_without_the_cache(
        self, tmp_path: Path
    ) -> None:
        document = companyfacts(
            [
                share_row("2022-09-30", FIRST_COUNT, filed="2022-11-01"),
                share_row("2024-09-27", LAST_COUNT),
            ]
        )

        parsed = extract_share_series(document)

        assert parsed.first == pytest.approx(FIRST_COUNT)
        assert parsed.last == pytest.approx(LAST_COUNT)
        assert parsed.count == 2
        # A pure parse never touches the disk, so an empty cache dir stays empty.
        assert not list(tmp_path.iterdir())

    def test_a_filed_series_answers_first_last_count_and_as_of(
        self, tmp_path: Path
    ) -> None:
        result = series(
            tmp_path,
            [
                share_row("2022-09-30", FIRST_COUNT, filed="2022-11-01"),
                share_row("2023-09-29", MIDDLE_COUNT, filed="2023-11-02"),
                share_row("2024-09-27", LAST_COUNT, filed="2024-11-01"),
            ],
        )

        assert result.first == pytest.approx(FIRST_COUNT)
        assert result.last == pytest.approx(LAST_COUNT)
        assert result.pct_change == pytest.approx(21.0)
        assert result.count == 3
        assert result.skipped == 0
        assert result.reason is None
        assert result.trend_reason is None

    def test_every_number_carries_the_filing_count_it_came_from(
        self, tmp_path: Path
    ) -> None:
        result = series(
            tmp_path,
            [
                share_row("2022-09-30", FIRST_COUNT, filed="2022-11-01"),
                share_row("2024-09-27", LAST_COUNT, filed="2024-11-01"),
            ],
        )

        # count is the filings behind first/last, not a schema's field count:
        # the points tuple and the count must always agree.
        assert result.count == len(result.points) == 2
        assert result.first == result.points[0].value
        assert result.last == result.points[-1].value
        assert all(isinstance(point, ShareCountPoint) for point in result.points)

    def test_the_series_is_ordered_oldest_to_newest_regardless_of_document_order(
        self, tmp_path: Path
    ) -> None:
        result = series(
            tmp_path,
            [
                share_row("2024-09-27", LAST_COUNT, filed="2024-11-01"),
                share_row("2023-09-29", MIDDLE_COUNT, filed="2023-11-02"),
                share_row("2022-09-30", FIRST_COUNT, filed="2022-11-01"),
            ],
        )

        ends = [point.period_end for point in result.points]
        assert ends == sorted(ends) == ["2022-09-30", "2023-09-29", "2024-09-27"]
        assert result.first == pytest.approx(FIRST_COUNT)
        assert result.last == pytest.approx(LAST_COUNT)

    def test_a_quoted_numeral_is_the_same_fact(self, tmp_path: Path) -> None:
        result = series(
            tmp_path,
            [
                share_row("2022-09-30", "100000000000", filed="2022-11-01"),
                share_row("2024-09-27", "121000000000", filed="2024-11-01"),
            ],
        )

        assert result.first == pytest.approx(FIRST_COUNT)
        assert result.last == pytest.approx(LAST_COUNT)
        assert result.skipped == 0

    def test_a_value_that_is_not_a_cik_is_refused_before_any_read(
        self, tmp_path: Path
    ) -> None:
        # A caller bug, not a data gap — the same refusal the loader raises.
        with pytest.raises(ValueError):
            shares_outstanding_series(tmp_path, "NOT-A-CIK")


# ---------------------------------------------------------------------------
# The trend
# ---------------------------------------------------------------------------


class TestTrend:
    def test_pct_change_is_first_to_last_not_min_to_max(
        self, tmp_path: Path
    ) -> None:
        # Up then down: the peak (+50%) is not the dilution; the count ended
        # 20% above where it started, and that — and only that — is the change.
        result = series(
            tmp_path,
            [
                share_row("2022-09-30", 100.0, filed="2022-11-01"),
                share_row("2023-09-29", 150.0, filed="2023-11-02"),
                share_row("2024-09-27", 120.0, filed="2024-11-01"),
            ],
        )

        assert result.first == pytest.approx(100.0)
        assert result.last == pytest.approx(120.0)
        assert result.pct_change == pytest.approx(20.0)

    def test_pct_change_for_a_rising_series(self, tmp_path: Path) -> None:
        result = series(
            tmp_path,
            [
                share_row("2022-09-30", 100.0, filed="2022-11-01"),
                share_row("2024-09-27", 132.0, filed="2024-11-01"),
            ],
        )

        assert result.pct_change == pytest.approx(32.0)

    def test_pct_change_for_a_falling_series_is_negative(
        self, tmp_path: Path
    ) -> None:
        result = series(
            tmp_path,
            [
                share_row("2022-09-30", 200.0, filed="2022-11-01"),
                share_row("2024-09-27", 150.0, filed="2024-11-01"),
            ],
        )

        assert result.pct_change == pytest.approx(-25.0)

    def test_as_of_is_the_newest_filing_date_never_the_clock(
        self, tmp_path: Path
    ) -> None:
        result = series(
            tmp_path,
            [
                share_row("2023-06-30", 100.0, filed="2023-08-04"),
                share_row("2024-06-28", 132.0, filed="2024-08-02"),
            ],
        )

        # The answer is dated by the filings, not by the request: a series is
        # only as fresh as its newest filing, whatever day it is read on.
        assert result.as_of == "2024-08-02"
        assert result.as_of != date.today().isoformat()

    def test_one_filed_count_is_a_level_and_refuses_the_trend(
        self, tmp_path: Path
    ) -> None:
        result = series(
            tmp_path,
            [share_row("2024-09-27", LAST_COUNT, filed="2024-11-01")],
        )

        # The existence questions are answerable; the change question is not.
        assert result.first == pytest.approx(LAST_COUNT)
        assert result.last == pytest.approx(LAST_COUNT)
        assert result.count == 1
        assert result.skipped == 0
        assert result.as_of == "2024-11-01"
        assert result.pct_change is None
        assert result.trend_reason == REASON_ONE_POINT
        assert result.reason is None, (
            "one observation is not a missing series: the refusal is the trend, "
            "not the series"
        )

    def test_a_missing_series_has_no_trend_either(self, tmp_path: Path) -> None:
        (tmp_path / "cache").mkdir()

        result = shares_outstanding_series(tmp_path / "cache", CIK)

        assert result.pct_change is None
        assert result.trend_reason is None
        assert result.reason == REASON_NO_DOCUMENT


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


class TestDedupe:
    def test_duplicate_period_ends_keep_the_newest_filing(
        self, tmp_path: Path
    ) -> None:
        # One period end filed twice: the restatement wins, and the pair is one
        # observation — the same rule as "a restatement is that quarter again".
        result = series(
            tmp_path,
            [
                share_row("2024-09-27", 120.0, accn="0000320193-24-000010",
                          filed="2024-11-01"),
                share_row("2024-09-27", 121.0, accn="0000320193-24-000017",
                          filed="2024-11-15"),
                share_row("2022-09-30", 100.0, filed="2022-11-01"),
            ],
        )

        assert result.count == 2
        assert result.last == pytest.approx(121.0)
        newest = result.points[-1]
        assert newest.period_end == "2024-09-27"
        assert newest.filed == "2024-11-15"
        assert newest.accession == "0000320193-24-000017"
        assert result.as_of == "2024-11-15"

    def test_a_duplicate_the_dates_cannot_separate_is_deterministic(
        self, tmp_path: Path
    ) -> None:
        # Same period, same filing date, two accessions: the tie-break must be
        # reproducible rather than "whichever row the JSON happened to carry
        # first", so two runs of one document always agree.
        result = series(
            tmp_path,
            [
                share_row("2024-09-27", 125.0, accn="0000320193-24-000010",
                          filed="2024-11-01"),
                share_row("2024-09-27", 121.0, accn="0000320193-24-000020",
                          filed="2024-11-01"),
            ],
        )

        assert result.count == 1
        assert result.last == pytest.approx(121.0)
        assert result.points[0].accession == "0000320193-24-000020"


# ---------------------------------------------------------------------------
# Skipped rows
# ---------------------------------------------------------------------------


class TestSkippedRows:
    def test_negative_and_garbage_rows_are_skipped_and_counted(
        self, tmp_path: Path
    ) -> None:
        result = series(
            tmp_path,
            [
                share_row("2022-09-30", 100.0, filed="2022-11-01"),
                share_row("2023-09-29", -50.0, filed="2023-11-02"),
                share_row("2024-03-29", 0.0, filed="2024-05-02"),
                share_row("2024-06-28", "not a number", filed="2024-08-02"),
                share_row("2024-09-27", 132.0, filed="2024-11-01"),
            ],
        )

        # The good rows answer; the bad ones are excluded from first/last but
        # counted, because a series of 5 rows with 3 usable filings must not
        # report itself as a series of 5.
        assert result.first == pytest.approx(100.0)
        assert result.last == pytest.approx(132.0)
        assert result.count == 2
        assert result.skipped == 3
        assert result.pct_change == pytest.approx(32.0)

    def test_a_zero_count_is_not_a_share_count(self, tmp_path: Path) -> None:
        result = series(
            tmp_path,
            [
                share_row("2022-09-30", 0.0, filed="2022-11-01"),
                share_row("2024-09-27", 132.0, filed="2024-11-01"),
            ],
        )

        assert result.first == pytest.approx(132.0)
        assert result.last == pytest.approx(132.0)
        assert result.skipped == 1

    def test_a_row_not_quoted_in_shares_is_not_a_share_count(
        self, tmp_path: Path
    ) -> None:
        cache_facts(tmp_path, companyfacts([]))
        document = companyfacts([])
        document["facts"]["dei"]["EntityCommonStockSharesOutstanding"][
            "units"
        ] = {"USD": [share_row("2024-09-27", 121.0, unit="USD")]}
        cache_facts(tmp_path, document)

        result = shares_outstanding_series(tmp_path, CIK)

        assert result.first is None
        assert result.last is None
        assert result.skipped == 1
        assert result.reason == REASON_ALL_UNUSABLE

    def test_a_row_without_an_end_date_is_unusable(self, tmp_path: Path) -> None:
        undated = share_row("", 121.0, filed="2024-11-01")

        result = series(
            tmp_path,
            [
                undated,
                share_row("2022-09-30", 100.0, filed="2022-11-01"),
            ],
        )

        assert result.count == 1
        assert result.skipped == 1


# ---------------------------------------------------------------------------
# Absence — None plus a reason, never 0.0
# ---------------------------------------------------------------------------


class TestAbsence:
    def test_no_cached_document_is_a_reason_never_a_zero(
        self, tmp_path: Path
    ) -> None:
        result = shares_outstanding_series(tmp_path, CIK)

        assert result.first is None
        assert result.last is None
        assert result.pct_change is None
        assert result.as_of is None
        assert result.count == 0
        assert result.points == ()
        assert result.reason == REASON_NO_DOCUMENT

    def test_a_document_without_the_share_count_tag_is_a_reason(
        self, tmp_path: Path
    ) -> None:
        document = companyfacts([])
        document["facts"]["dei"] = {
            "EntityPublicFloat": {
                "units": {"USD": [share_row("2024-06-28", 1.0)]}
            }
        }
        cache_facts(tmp_path, document)

        result = shares_outstanding_series(tmp_path, CIK)

        assert result.first is None
        assert result.last is None
        assert result.count == 0
        assert result.reason == REASON_NO_TAG

    def test_a_document_with_no_dei_namespace_is_a_reason(
        self, tmp_path: Path
    ) -> None:
        document: dict[str, Any] = {
            "cik": int(CIK),
            "entityName": "TEST CO",
            "facts": {"us-gaap": {"Revenues": {"units": {"USD": []}}}},
        }
        cache_facts(tmp_path, document)

        result = shares_outstanding_series(tmp_path, CIK)

        assert result.first is None
        assert result.count == 0
        assert result.reason == REASON_NO_TAG

    def test_a_document_for_another_issuer_is_a_miss_not_evidence(
        self, tmp_path: Path
    ) -> None:
        # The loader owns this rule (being on disk is not evidence); the series
        # layer inherits it — a foreign body must read as no document at all.
        cache_facts(
            tmp_path,
            companyfacts([share_row("2024-09-27", 121.0)]),
            cik="0000789019",
        )

        result = shares_outstanding_series(tmp_path, CIK)

        assert result.first is None
        assert result.count == 0
        assert result.reason == REASON_NO_DOCUMENT

    def test_every_row_garbage_is_skipped_counted_and_reasoned(
        self, tmp_path: Path
    ) -> None:
        result = series(
            tmp_path,
            [
                share_row("2022-09-30", -25.0, filed="2022-11-01"),
                share_row("2023-09-29", 0.0, filed="2023-11-02"),
                share_row("2024-03-29", None, filed="2024-05-02"),
                share_row("2024-06-28", "", filed="2024-08-02"),
                share_row("2024-09-27", float("inf"), filed="2024-11-01"),
            ],
        )

        assert result.first is None
        assert result.last is None
        assert result.pct_change is None
        assert result.count == 0
        assert result.skipped == 5
        assert result.reason == REASON_ALL_UNUSABLE

    def test_a_corrupt_cache_entry_is_a_miss_not_a_wrong_series(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / f"CIK{CIK}-companyfacts.json"
        path.write_text("{ half a document", encoding="utf-8")

        result = shares_outstanding_series(tmp_path, CIK)

        assert result.first is None
        assert result.reason == REASON_NO_DOCUMENT


# ---------------------------------------------------------------------------
# The refusal this module exists to answer
# ---------------------------------------------------------------------------


def test_the_module_docstring_quotes_the_published_refusal_verbatim() -> None:
    # The control is still unavailable; this round adds only the series. Keeping
    # the exact published sentence in the module's own documentation means the
    # code and the document cannot drift into describing different gaps — the
    # same discipline app/screen/descriptive.py applies to its own reasons.
    assert _DILUTION_REASON in (dilution.__doc__ or ""), (
        "the module must quote the screener's published refusal verbatim"
    )
