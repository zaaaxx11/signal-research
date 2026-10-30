"""Regenerate the pinned upstream fixtures in this directory.

Not collected by pytest (no ``test_`` prefix); run it by hand after changing a
row:

    .venv/bin/python tests/fixtures/upstream/build_fixtures.py

The XLSX files are committed as bytes because the point of a *pinned* fixture is
that the test reads exactly what the incident produced, not whatever the local
openpyxl version would synthesise today. The row content is repeated below so
the reader can diff a binary file against its intent.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import openpyxl

HERE = Path(__file__).resolve().parent

#: The real SSGA holdings layout: a fund preamble, a blank row, then a header
#: whose cells are the column names (never fixed offsets).
PREAMBLE: list[list[Any]] = [
    ["Fund Name:", "State Street SPDR S&P 500 ETF Trust"],
    ["Ticker Symbol:", "SPY"],
    ["Holdings:", "As of 27-Oct-2026"],
    [],
]
HEADER = [
    "Name",
    "Ticker",
    "Identifier",
    "SEDOL",
    "Weight",
    "Sector",
    "Shares Held",
    "Local Currency",
]
DATA: list[list[Any]] = [
    ["NVIDIA CORP", "NVDA", "67066G104", "2379504", 7.924666, "-", 295426533, "USD"],
    ["APPLE INC", "AAPL", "037833100", "2046251", 7.611391, "-", 178161925, "USD"],
    ["MICROSOFT CORP", "MSFT", "594918104", "2588173", 5.927782, "-", 90642802, "USD"],
]


def write_xlsx(path: Path, rows: list[list[Any]]) -> None:
    workbook = openpyxl.Workbook()
    sheet = workbook.worksheets[0]
    sheet.title = "holdings"
    for row in rows:
        sheet.append(list(row))
    buffer = io.BytesIO()
    workbook.save(buffer)
    path.write_bytes(buffer.getvalue())


def build_ssga() -> None:
    baseline: list[list[Any]] = PREAMBLE + [HEADER] + DATA
    write_xlsx(HERE / "ssga_spy_baseline.xlsx", baseline)
    # The deformation: SSGA appended a position column to the *data block* only,
    # so every data cell sits one column to the right of the header cell that
    # names it. Header untouched — exactly what a column insert without a header
    # rewrite looks like.
    shifted: list[list[Any]] = PREAMBLE + [HEADER] + [
        [position, *row] for position, row in enumerate(DATA, start=1)
    ]
    write_xlsx(HERE / "ssga_spy_shifted.xlsx", shifted)


# ---------------------------------------------------------------------------
# SEC financial statement data sets: the 2026q3 column layout, four rows
# ---------------------------------------------------------------------------

SUB_COLUMNS = (
    "adsh cik name sic countryba stprba cityba zipba bas1 bas2 baph countryma "
    "stprma cityma zipma mas1 mas2 countryinc stprinc ein former changed afs wksi "
    "fye form period fy fp filed accepted prevrpt detail instance nciks aciks"
).split()
NUM_COLUMNS = "adsh tag version ddate qtrs uom segments coreg value footnote".split()


def sub_row(
    adsh: str, cik: str, name: str, form: str, period: str, fy: str, fp: str, filed: str
) -> str:
    row = {column: "" for column in SUB_COLUMNS}
    row.update(
        adsh=adsh,
        cik=cik,
        name=name,
        sic="3571",
        form=form,
        period=period,
        fy=fy,
        fp=fp,
        filed=filed,
    )
    return "\t".join(row[column] for column in SUB_COLUMNS)


def num_row(adsh: str, tag: str, ddate: str, qtrs: str, uom: str, value: str) -> str:
    row = {column: "" for column in NUM_COLUMNS}
    row.update(adsh=adsh, tag=tag, version="us-gaap/2026", ddate=ddate, qtrs=qtrs, uom=uom, value=value)
    return "\t".join(row[column] for column in NUM_COLUMNS)


AAPL_CIK = "0000320193"
#: 2026q1 carried Apple's 2025 10-K; 2026q3 carried its fiscal-Q3 10-Q.
AAPL_10K = "0000320193-25-000079"
AAPL_10Q = "0000320193-26-000020"


def write_quarter(directory: Path, sub_rows: list[str], num_rows: list[str]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "sub.txt").write_text(
        "\t".join(SUB_COLUMNS) + "\n" + "\n".join(sub_rows) + "\n", encoding="utf-8"
    )
    (directory / "num.txt").write_text(
        "\t".join(NUM_COLUMNS) + "\n" + "\n".join(num_rows) + "\n", encoding="utf-8"
    )


def build_sec() -> None:
    root = HERE / "sec_bulk"
    write_quarter(
        root / "2026q1",
        [sub_row(AAPL_10K, AAPL_CIK, "Apple Inc.", "10-K", "20250927", "2025", "FY", "20251031")],
        [
            num_row(
                AAPL_10K,
                "RevenueFromContractWithCustomerExcludingAssessedTax",
                "20250927",
                "4",
                "USD",
                "416161000000",
            )
        ],
    )
    write_quarter(
        root / "2026q3",
        [sub_row(AAPL_10Q, AAPL_CIK, "Apple Inc.", "10-Q", "20260630", "2026", "Q3", "20260731")],
        [
            num_row(
                AAPL_10Q,
                "RevenueFromContractWithCustomerExcludingAssessedTax",
                "20260627",
                "1",
                "USD",
                "94000000000",
            )
        ],
    )
    # 2026q2 is deliberately absent: that is the incident this fixture pins.


def build_yahoo() -> None:
    # Recorded verbatim from the throttled endpoint: the body is prose, served
    # with HTTP 200, and the naive parser stored it as a crumb token.
    (HERE / "yahoo_getcrumb_too_many_requests.txt").write_text(
        "Too Many Requests\n", encoding="utf-8"
    )


if __name__ == "__main__":
    build_ssga()
    build_sec()
    build_yahoo()
    print("fixtures written to", HERE)
