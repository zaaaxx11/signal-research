"""Pin the *failure mode* of the three upstream formats that already bit us.

Every incident in the plan's §7 table had the same anatomy: the third party
changed the shape of a payload, the parser kept returning something plausible,
and the damage showed up days later as "all prices are None" or "every batch
401'd". A test that only asserts "an exception happened" would not have caught
any of them, so each test here asserts **what the error says** — the type, and
the words that name the defect.

The fixtures live in ``tests/fixtures/upstream/`` and are pinned bytes
(``build_fixtures.py`` regenerates them and repeats the rows in source so a
binary can be reviewed). Nothing here touches the network: the Yahoo seams are
fed a fake client, and ``tests/conftest.py`` fails the whole suite if anything
tries to connect.

Status of each contract against today's parsers
-----------------------------------------------
* SSGA shifted columns — **not enforced yet.** ``parse_ssga_holdings`` returns
  ``set()`` (measured: 0 of 3 constituents) after an ``INFO`` line. The source
  layer notices an *empty* result, but nothing notices a *wrong non-empty* one,
  and no caller is told which column moved. Now ``HoldingsFormatError`` names
  the index and the column.
* SEC bulk missing quarter — **enforced.** ``build_quarters`` raises
  ``FileNotFoundError`` naming the ZIP, and the discovery path records the
  absent quarter in ``missing_quarters`` plus a WARNING.
* Yahoo ``getcrumb`` — **enforced.** A prose body raises ``CrumbRefused`` (a
  ``QuoteSourceError``) naming the refusal, so the literal never reaches a
  query string and the caller can tell "throttled" from "no cookie".
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Any

import pytest

from app.engine.bulk_financials import build_quarters
from app.market import official, sources, yahoo_batch, yahoo_depth
from app.market.official import parse_ssga_holdings
from app.market.sec_fundamentals_depth import SecFundamentalsDepthSource

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "upstream"

SSGA_BASELINE = (FIXTURES / "ssga_spy_baseline.xlsx").read_bytes()
SSGA_SHIFTED = (FIXTURES / "ssga_spy_shifted.xlsx").read_bytes()
CRUMB_THROTTLED = (FIXTURES / "yahoo_getcrumb_too_many_requests.txt").read_text(
    encoding="utf-8"
)

AAPL_CIK = "0000320193"


def _quarter_archive(tmp_path: Path, quarter: str) -> Path:
    """Zip one pinned quarter directory into ``<tmp>/cache/sec-bulk/<q>.zip``.

    The cache path is the loader's own (``dataset_cache_path``), so the test
    exercises the real layout instead of a shortcut.
    """

    source_dir = FIXTURES / "sec_bulk" / quarter
    target_dir = tmp_path / "cache" / "sec-bulk"
    target_dir.mkdir(parents=True, exist_ok=True)
    archive = target_dir / f"{quarter}.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for member in sorted(source_dir.glob("*.txt")):
            bundle.write(member, member.name)
    return archive


# ---------------------------------------------------------------------------
# 1. SSGA holdings XLSX with one column of drift
# ---------------------------------------------------------------------------


def test_the_baseline_workbook_parses_to_its_three_constituents() -> None:
    """Control: the fixture is well formed, so the shift is the only variable."""

    assert parse_ssga_holdings(SSGA_BASELINE, "SPY") == {"NVDA", "AAPL", "MSFT"}


def test_a_shifted_workbook_raises_a_named_format_error() -> None:
    """A drifted column must be an error that names the defect, not empty data."""

    error = getattr(official, "HoldingsFormatError", None)
    assert error is not None, (
        "app.market.official must define HoldingsFormatError (a ValueError "
        "subclass) so a caller can tell 'this file is malformed' from 'this "
        "index has no members'"
    )

    with pytest.raises(error) as caught:
        parse_ssga_holdings(SSGA_SHIFTED, "SPY")

    message = str(caught.value).lower()
    assert "column" in message, f"the error must say which column moved: {message!r}"
    assert "spy" in message, f"the error must name the index: {message!r}"


def test_a_shifted_workbook_never_invents_constituents() -> None:
    """Whatever the parser does with drift, it may not publish bogus tickers.

    Today it returns ``set()`` — wrong, but not *plausible*, because every cell
    must match a ticker shape. The day a column of short upper-case codes
    (currencies, exchange codes) lands under the header's ``Ticker`` cell, the
    parse would return a believable membership set; this assertion fails then.
    """

    baseline = parse_ssga_holdings(SSGA_BASELINE, "SPY")
    try:
        shifted = parse_ssga_holdings(SSGA_SHIFTED, "SPY")
    except Exception:  # the named error the previous test demands
        return

    assert shifted <= baseline, (
        "a shifted workbook produced tickers the real file does not contain: "
        f"{sorted(shifted - baseline)}"
    )


# ---------------------------------------------------------------------------
# 2. SEC bulk quarter directory missing one quarter
# ---------------------------------------------------------------------------


def test_two_present_quarters_still_build_an_index(tmp_path: Path) -> None:
    """Control: the pinned quarters parse, so a missing one is the only variable."""

    first = _quarter_archive(tmp_path, "2026q1")
    third = _quarter_archive(tmp_path, "2026q3")

    index = build_quarters([first, third])

    assert index.ciks() == (AAPL_CIK,)
    assert len(index.facts_for(AAPL_CIK)) == 2


def test_a_missing_quarter_file_raises_and_names_the_quarter(tmp_path: Path) -> None:
    """Asked for a quarter that is not on disk, the loader refuses to guess.

    ``build_quarters`` must not return an index built from "the quarters that
    happened to exist": the TTM arithmetic silently degrades to "no growth"
    when a quarter is missing, which is indistinguishable from a flat company.
    """

    first = _quarter_archive(tmp_path, "2026q1")
    third = _quarter_archive(tmp_path, "2026q3")
    missing = tmp_path / "cache" / "sec-bulk" / "2026q2.zip"

    with pytest.raises(FileNotFoundError) as caught:
        build_quarters([first, missing, third])

    message = str(caught.value)
    assert "2026q2" in message, f"the error must name the missing quarter: {message!r}"


async def test_the_source_reports_the_quarter_it_could_not_find(tmp_path: Path) -> None:
    """The depth rung must name the hole, not quietly serve fewer quarters."""

    _quarter_archive(tmp_path, "2026q1")
    _quarter_archive(tmp_path, "2026q3")

    source = SecFundamentalsDepthSource(
        cache_dir=tmp_path / "cache",
        quarters=("2026q1", "2026q2", "2026q3"),
        cik_by_symbol={"AAPL": AAPL_CIK},
    )
    await source.fetch_many(["AAPL"])

    # It built something — which is exactly why silence is dangerous: the rung
    # looks healthy while one of its three quarters was never read.
    assert source.fact_index is not None

    reported = " ".join(
        str(value)
        for value in (source.build_error, getattr(source, "missing_quarters", ""))
        if value
    )
    assert "2026q2" in reported, (
        "the source found 2026q1 and 2026q3 but never said 2026q2 was missing: "
        f"build_error={source.build_error!r}"
    )


# ---------------------------------------------------------------------------
# 3. Yahoo getcrumb answering with the literal "Too Many Requests"
# ---------------------------------------------------------------------------


class _Response:
    def __init__(self, text: str, status: int = 200) -> None:
        self.text = text
        self.status_code = status
        self.content = text.encode("utf-8")
        self.headers: dict[str, str] = {}

    def json(self) -> Any:  # the throttle body is not JSON
        raise ValueError("not JSON")


class _CrumbClient:
    """Answers the cookie warm-up and the crumb exchange with the throttle body."""

    def __init__(self, body: str, status: int = 200) -> None:
        self.body = body
        self.status = status
        self.cookies: dict[str, str] = {}
        self.urls: list[str] = []

    async def get(self, url: str, *args: Any, **kwargs: Any) -> _Response:
        self.urls.append(url)
        return _Response(self.body, self.status)


def _crumb_seams() -> list[tuple[str, Any]]:
    """``(label, callable(client) -> token)`` for every crumb parser in app/."""

    batch = yahoo_batch.YahooBatchQuoteSource()
    quote = sources.YahooQuoteSource()
    depth = yahoo_depth.YahooDepthSource()

    return [
        ("yahoo_batch._authorize", batch._authorize),
        ("sources._crumb", quote._crumb),
        ("yahoo_depth._crumb_token", depth._crumb_token),
    ]


async def test_the_throttle_body_is_never_returned_as_a_token() -> None:
    """The 2026-10 incident: ``Too Many Requests`` stored as the crumb.

    Every seam must reject a prose body, whatever its HTTP status says, so the
    literal can never reach a query string (every batch then 401'd).
    """

    for label, seam in _crumb_seams():
        client = _CrumbClient(CRUMB_THROTTLED)

        try:
            token = await seam(client)
        except sources.CrumbRefused:
            # The refusal is now a named error; what still matters is that the
            # prose never travelled as a credential.
            token = ""

        assert not token, f"{label} returned a token from a throttle body: {token!r}"
        assert all(CRUMB_THROTTLED.strip() not in url for url in client.urls), label


async def test_a_throttled_crumb_is_a_named_error_that_quotes_the_body() -> None:
    """A refusal must be an error class the caller can branch on, with the why."""

    module_calls = [
        (yahoo_batch, "yahoo_batch.YahooBatchQuoteSource._authorize"),
        (sources, "sources.YahooQuoteSource._crumb"),
        (yahoo_depth, "yahoo_depth.YahooDepthSource._crumb_token"),
    ]
    for module, label in module_calls:
        error = getattr(module, "CrumbRefused", None)
        assert error is not None, (
            f"{module.__name__} must define CrumbRefused (a QuoteSourceError "
            "subclass) so a throttled crumb is not just an empty string"
        )
        assert issubclass(error, sources.QuoteSourceError), (
            f"{module.__name__}.CrumbRefused must stay in the source-error family "
            "so existing callers keep catching it"
        )

    for (label, seam), (module, _) in zip(_crumb_seams(), module_calls):
        error = getattr(module, "CrumbRefused")
        with pytest.raises(error) as caught:
            await seam(_CrumbClient(CRUMB_THROTTLED))

        message = str(caught.value).lower()
        assert "crumb" in message, f"{label}: {message!r}"
        assert "too many requests" in message or "throttl" in message, (
            f"{label} must say what the endpoint answered: {message!r}"
        )
