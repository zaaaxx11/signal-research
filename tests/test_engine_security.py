"""The SEC security-master slice: two parsers and one in-memory master.

The tests pin four rules that the slice exists to make structural:

1. **Identity is parsed from documents, not guessed.** ``company_tickers.json``
   carries a ticker, a CIK and a title; it does not carry a venue, a sector or a
   country, so the parsed record leaves those ``None``.
2. **Malformed input is dropped, never repaired into a plausible instrument.**
   A blank ticker, a zero CIK or a ticker bound to two CIKs has no identity.
3. **The master never silently overwrites evidence.** A second document may
   fill a field that is missing, but a disagreeing non-null value raises
   :class:`SecurityConflict` instead of winning by load order.
4. **A load can add a security, not retire one.** ``active`` is unchanged by a
   merge; retirement needs an explicit operation this slice does not have.

The fixtures are handwritten 3-6 record samples of the two live shapes (the
``company_tickers.json`` object keyed by ``"0".."N"`` and a submissions object
with ``tickers``/``exchanges`` lists). Nothing here opens a socket: the suite
guard in ``tests/conftest.py`` would fail the test if it did.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from app.engine.model import CellStatus, Provenance, SecurityRecord
from app.engine.security import (
    SecurityConflict,
    SecurityLookupError,
    SecurityMaster,
)
from app.engine.sources import sec
from app.engine.sources.sec import (
    SEC_SOURCE,
    TICKERS_URL,
    exchange_mic,
    normalize_cik,
    normalize_symbol,
    parse_company_tickers,
    parse_submissions,
    submissions_url,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _company_tickers() -> dict[str, object]:
    return json.loads(
        (FIXTURES / "engine_sec_company_tickers_sample.json").read_text("utf-8")
    )


def _submissions() -> dict[str, object]:
    return json.loads(
        (FIXTURES / "engine_sec_submissions_sample.json").read_text("utf-8")
    )


def _provenance(as_of: str = "2026-10-26", url: str = TICKERS_URL) -> Provenance:
    return Provenance(
        source="sec", as_of=as_of, status=CellStatus.OK, url=url
    )


# ---------------------------------------------------------------------------
# Source description and primitives
# ---------------------------------------------------------------------------


def test_sec_source_is_public_and_respects_the_fair_access_rate() -> None:
    """EDGAR is a public, redistributable source limited to 10 requests/second."""

    assert SEC_SOURCE.name == "sec"
    assert SEC_SOURCE.allows_public is True
    assert SEC_SOURCE.rate_limit == (10, 1.0)
    assert SEC_SOURCE.features == frozenset({"identity", "reference"})
    assert SEC_SOURCE.terms_url.startswith("https://www.sec.gov/")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (320193, "0000320193"),
        ("320193", "0000320193"),
        ("0000320193", "0000320193"),
        ("CIK0000320193", "0000320193"),
        ("cik 320193", "0000320193"),
        (0, None),
        (-1, None),
        (True, None),
        (320193.0, None),
        ("", None),
        ("   ", None),
        ("not-a-cik", None),
        ("12345678901", None),
        (None, None),
    ],
)
def test_normalize_cik_pads_to_ten_digits_and_rejects_non_identifiers(
    raw: object, expected: str | None
) -> None:
    """A CIK is a positive integer of at most ten digits, printed padded."""

    assert normalize_cik(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (" aapl ", "AAPL"),
        ("brk.b", "BRK-B"),
        ("BRK-B", "BRK-B"),
        ("", None),
        ("   ", None),
        ("HAS SPACE", None),
        ("-", None),
        (None, None),
        (123, None),
    ],
)
def test_normalize_symbol_uses_edgar_spelling_and_rejects_non_symbols(
    raw: object, expected: str | None
) -> None:
    """EDGAR spells a share class with a hyphen; the dot spelling is the same one."""

    assert normalize_symbol(raw) == expected


@pytest.mark.parametrize(
    ("exchange", "expected"),
    [
        ("Nasdaq", "XNAS"),
        ("NASDAQ Global Select Market", "XNAS"),
        ("Nasdaq Capital Market", "XNAS"),
        ("NYSE", "XNYS"),
        (" New York Stock Exchange ", "XNYS"),
        ("NYSE American", "XASE"),
        ("NYSE MKT", "XASE"),
        ("AMEX", "XASE"),
        ("Cboe BZX", "BATS"),
        ("Cboe EDGX", "EDGX"),
        ("OTC Markets", None),
        ("", None),
        (None, None),
    ],
)
def test_exchange_mic_maps_only_venues_it_can_name(
    exchange: object, expected: str | None
) -> None:
    """An unknown venue is ``None``; a guessed MIC would be a fabricated fact."""

    assert exchange_mic(exchange) == expected


def test_submissions_url_pads_the_cik_and_refuses_an_invalid_one() -> None:
    """The URL is a function of the padded CIK, never of user text."""

    assert (
        submissions_url("1067983")
        == "https://data.sec.gov/submissions/CIK0001067983.json"
    )
    with pytest.raises(ValueError, match="cik"):
        submissions_url("not-a-cik")


# ---------------------------------------------------------------------------
# Parsing company_tickers.json
# ---------------------------------------------------------------------------


def test_parse_company_tickers_reads_the_indexed_document() -> None:
    """The document is ``{"0": {cik_str, ticker, title}}``, not an array."""

    records = parse_company_tickers(_company_tickers())

    assert [record.symbol for record in records] == ["AAPL", "BRK-B", "MSFT", "NVDA"]
    apple = records[0]
    assert apple.cik == "0000320193"
    assert apple.name == "Apple Inc."
    assert apple.asset_class == "equity"
    assert apple.currency == "USD"
    assert apple.active is True
    # The index carries no venue and no classification, so none is invented.
    assert apple.exchange is None
    assert apple.mic is None
    assert apple.country is None
    assert apple.sector is None
    assert apple.industry is None


def test_parse_company_tickers_drops_entries_without_an_identity() -> None:
    """Junk rows are skipped, not repaired into a security with a blank name."""

    payload = {
        "0": "not an entry",
        "1": {"cik_str": 1, "ticker": "   ", "title": "Blank Ticker"},
        "2": {"cik_str": 0, "ticker": "ZERO", "title": "Zero Cik"},
        "3": {"cik_str": "not-a-number", "ticker": "BADCIK", "title": "Bad Cik"},
        "4": {"cik_str": 4, "ticker": "NONAME", "title": "  "},
        "5": {"cik_str": 5, "ticker": "HAS SPACE", "title": "Space"},
        "6": {"ticker": "NOCIK", "title": "No Cik"},
        "7": {"cik_str": 8, "ticker": "GOOD", "title": "Good Co"},
    }

    records = parse_company_tickers(payload)

    assert [(record.symbol, record.cik) for record in records] == [
        ("GOOD", "0000000008")
    ]


def test_parse_company_tickers_drops_a_ticker_bound_to_two_ciks() -> None:
    """An ambiguous ticker is no identity at all; the same CIK twice is one row."""

    payload = {
        "0": {"cik_str": 11, "ticker": "DUP", "title": "First"},
        "1": {"cik_str": 22, "ticker": "DUP", "title": "Second"},
        "2": {"cik_str": 33, "ticker": "ONE", "title": "One"},
        "3": {"cik_str": 33, "ticker": "ONE", "title": "One Again"},
    }

    records = parse_company_tickers(payload)

    assert [(record.symbol, record.cik) for record in records] == [
        ("ONE", "0000000033")
    ]


@pytest.mark.parametrize("payload", [None, [], "nope", 3])
def test_parse_company_tickers_returns_nothing_for_a_non_object(payload: object) -> None:
    """A body of the wrong JSON type is an empty parse, not an exception."""

    assert parse_company_tickers(payload) == ()


# ---------------------------------------------------------------------------
# Parsing a submissions document
# ---------------------------------------------------------------------------


def test_parse_submissions_pairs_each_ticker_with_its_exchange() -> None:
    """One CIK can own several share classes, each listed on its own venue."""

    records = parse_submissions(_submissions())

    assert [record.symbol for record in records] == ["BRK-A", "BRK-B"]
    class_a, class_b = records
    assert class_a.cik == "0001067983"
    assert class_b.cik == "0001067983"
    assert class_a.name == "Berkshire Hathaway Inc."
    assert class_a.exchange == "NYSE"
    assert class_a.mic == "XNYS"
    assert class_b.exchange == "NYSE"
    assert class_b.mic == "XNYS"
    assert class_a.country is None
    assert class_a.sector is None


def test_parse_submissions_leaves_a_missing_exchange_unknown() -> None:
    """``exchanges`` may be shorter than ``tickers``; the gap is not NASDAQ."""

    records = parse_submissions(
        {
            "cik": 123,
            "name": "Example Co",
            "tickers": ["EXA", "EXB"],
            "exchanges": ["Nasdaq"],
        }
    )

    by_symbol = {record.symbol: record for record in records}
    assert by_symbol["EXA"].exchange == "Nasdaq"
    assert by_symbol["EXA"].mic == "XNAS"
    assert by_symbol["EXB"].exchange is None
    assert by_symbol["EXB"].mic is None


def test_parse_submissions_keeps_the_first_of_a_duplicated_ticker() -> None:
    """A repeated ticker is one instrument; the first venue wins deterministically."""

    records = parse_submissions(
        {
            "cik": "0000000123",
            "name": "Example Co",
            "tickers": ["DUP", "DUP"],
            "exchanges": ["Nasdaq", "NYSE"],
        }
    )

    assert len(records) == 1
    assert records[0].symbol == "DUP"
    assert records[0].mic == "XNAS"


def test_parse_submissions_canonicalises_the_dot_spelling() -> None:
    """``BRK.B`` from a screen is ``BRK-B`` in EDGAR's own document."""

    records = parse_submissions(
        {
            "cik": "0001067983",
            "name": "Berkshire Hathaway Inc.",
            "tickers": ["BRK.B"],
            "exchanges": ["NYSE"],
        }
    )

    assert [record.symbol for record in records] == ["BRK-B"]


@pytest.mark.parametrize(
    "payload",
    [
        {"cik": "0000320193", "tickers": ["AAPL"]},
        {"cik": "bad-cik", "name": "Apple Inc.", "tickers": ["AAPL"]},
        {"cik": "0000320193", "name": "Apple Inc.", "tickers": "AAPL"},
        {"cik": "0000320193", "name": "Apple Inc.", "tickers": []},
        {"name": "Apple Inc.", "tickers": ["AAPL"]},
        None,
    ],
)
def test_parse_submissions_requires_a_cik_a_name_and_a_ticker_list(
    payload: object,
) -> None:
    """Without a CIK, a name and a list of tickers there is no usable identity."""

    assert parse_submissions(payload) == ()


# ---------------------------------------------------------------------------
# The in-memory master
# ---------------------------------------------------------------------------


def test_master_loads_company_tickers_and_looks_up_symbols() -> None:
    """Lookup is case-insensitive and accepts EDGAR's dot spelling."""

    master = SecurityMaster.from_company_tickers(
        _company_tickers(), as_of="2026-10-26"
    )

    assert len(master) == 4
    assert master.symbols() == ("AAPL", "BRK-B", "MSFT", "NVDA")
    assert master.get("aapl") == master.require("AAPL")
    assert master.get("brk.b").symbol == "BRK-B"  # type: ignore[union-attr]
    assert master.get("NOPE") is None
    assert master.get(123) is None  # type: ignore[arg-type]
    assert "nvda" in master
    assert "NOPE" not in master
    assert 123 not in master
    assert [record.symbol for record in master] == list(master.symbols())
    assert master.records() == tuple(master)


def test_master_require_raises_for_an_unknown_symbol() -> None:
    """``require`` differs from ``get``: a missing instrument is an error."""

    master = SecurityMaster()

    with pytest.raises(SecurityLookupError) as info:
        master.require("ghost")
    assert "ghost" in str(info.value)


def test_master_adds_a_second_share_class_and_indexes_both_by_cik() -> None:
    """Berkshire's two classes share one CIK; the index returns both."""

    master = SecurityMaster.from_company_tickers(
        _company_tickers(), as_of="2026-10-26"
    )

    added = master.add_submissions(_submissions(), as_of="2026-10-27")

    assert added == 1
    assert len(master) == 5
    assert [record.symbol for record in master.by_cik("1067983")] == [
        "BRK-A",
        "BRK-B",
    ]
    assert master.by_cik("CIK0001067983") == master.by_cik("1067983")
    assert [record.symbol for record in master.by_cik("320193")] == ["AAPL"]
    assert master.by_cik("not-a-cik") == ()
    assert master.by_cik("9999999999") == ()
    assert master.ciks() == (
        "0000320193",
        "0000789019",
        "0001045810",
        "0001067983",
    )


def test_master_enriches_a_known_symbol_without_rewriting_its_name() -> None:
    """A later document fills the missing venue; the display name is preserved."""

    master = SecurityMaster.from_company_tickers(
        _company_tickers(), as_of="2026-10-26"
    )
    before = master.require("BRK-B")
    assert before.exchange is None and before.mic is None

    master.add_submissions(_submissions(), as_of="2026-10-27")

    after = master.require("BRK-B")
    assert after.exchange == "NYSE"
    assert after.mic == "XNYS"
    assert after.cik == before.cik
    assert after.name == "BERKSHIRE HATHAWAY INC"
    assert after.active is True


def test_master_records_every_document_that_asserted_a_symbol() -> None:
    """Provenance is per symbol and accumulates: identity has an audit trail."""

    master = SecurityMaster.from_company_tickers(
        _company_tickers(), as_of="2026-10-26"
    )
    master.add_submissions(_submissions(), as_of="2026-10-27")

    apple = master.provenance("AAPL")
    assert len(apple) == 1
    assert apple[0].source == "sec"
    assert apple[0].as_of == "2026-10-26"
    assert apple[0].status is CellStatus.OK
    assert apple[0].url == TICKERS_URL

    berkshire = master.provenance("BRK-B")
    assert len(berkshire) == 2
    assert berkshire[-1].as_of == "2026-10-27"
    assert berkshire[-1].url is not None
    assert berkshire[-1].url.endswith("/CIK0001067983.json")

    with pytest.raises(SecurityLookupError):
        master.provenance("NOPE")


def test_master_load_is_idempotent_for_the_same_document() -> None:
    """Re-loading one document neither duplicates symbols nor duplicates evidence."""

    master = SecurityMaster()
    provenance = _provenance()
    records = parse_company_tickers(_company_tickers())

    assert master.load(records, provenance) == 4
    assert master.load(records, provenance) == 0
    assert len(master) == 4
    assert len(master.provenance("AAPL")) == 1


def test_master_add_returns_whether_the_symbol_is_new() -> None:
    """``add`` reports insertion, so a caller can distinguish add from enrich."""

    master = SecurityMaster()
    record = SecurityRecord(
        symbol="XYZ",
        name="Example Co",
        cik=None,
        exchange=None,
        mic=None,
        asset_class="equity",
        currency="USD",
        country=None,
        sector=None,
        industry=None,
    )
    provenance = _provenance()

    assert master.add(record, provenance) is True
    assert master.add(record, provenance) is False
    assert len(master.provenance("XYZ")) == 1


def test_master_refuses_to_overwrite_a_conflicting_cik() -> None:
    """A ticker cannot silently move to another issuer; load order is not truth."""

    master = SecurityMaster()
    original = SecurityRecord(
        symbol="AAPL",
        name="Apple Inc.",
        cik="0000320193",
        exchange=None,
        mic=None,
        asset_class="equity",
        currency="USD",
        country=None,
        sector=None,
        industry=None,
    )
    master.add(original, _provenance())

    impostor = replace(original, cik="0000000001")

    with pytest.raises(SecurityConflict, match="AAPL") as info:
        master.add(impostor, _provenance())
    assert "cik" in str(info.value)
    assert master.require("AAPL").cik == "0000320193"
    assert len(master) == 1


def test_master_refuses_to_overwrite_a_conflicting_venue() -> None:
    """Two venues for one ticker is an ambiguity the caller must resolve."""

    master = SecurityMaster()
    listed = SecurityRecord(
        symbol="XYZ",
        name="Example Co",
        cik="0000000042",
        exchange="NYSE",
        mic="XNYS",
        asset_class="equity",
        currency="USD",
        country=None,
        sector=None,
        industry=None,
    )
    master.add(listed, _provenance())

    with pytest.raises(SecurityConflict, match="XYZ") as info:
        master.add(
            replace(listed, exchange="Nasdaq", mic="XNAS"), _provenance()
        )
    assert "mic" in str(info.value)
    assert master.require("XYZ").mic == "XNYS"


def test_master_accepts_a_name_that_differs_only_in_display_case() -> None:
    """EDGAR's index is upper case and submissions is mixed case: same name."""

    master = SecurityMaster()
    indexed = SecurityRecord(
        symbol="AAPL",
        name="APPLE INC.",
        cik="0000320193",
        exchange=None,
        mic=None,
        asset_class="equity",
        currency="USD",
        country=None,
        sector=None,
        industry=None,
    )
    master.add(indexed, _provenance())

    master.add(
        replace(indexed, name="Apple Inc.", cik=None),
        _provenance(as_of="2026-10-27"),
    )

    assert master.require("AAPL").name == "APPLE INC."
    assert master.require("AAPL").cik == "0000320193"


def test_master_never_retires_a_security_by_loading_it_as_inactive() -> None:
    """A load can add or enrich; retirement needs an explicit operation."""

    master = SecurityMaster()
    active = SecurityRecord(
        symbol="XYZ",
        name="Example Co",
        cik=None,
        exchange=None,
        mic=None,
        asset_class="equity",
        currency="USD",
        country=None,
        sector=None,
        industry=None,
        active=True,
    )
    master.add(active, _provenance())

    master.add(replace(active, active=False), _provenance(as_of="2026-10-27"))

    assert master.require("XYZ").active is True


def test_master_stores_one_canonical_identity_for_a_caller_built_record() -> None:
    """A hand-built record with the dot spelling and a bare CIK is canonicalised."""

    master = SecurityMaster()
    record = SecurityRecord(
        symbol="brk.b",
        name="Berkshire Hathaway Inc.",
        cik="1067983",
        exchange="NYSE",
        mic="XNYS",
        asset_class="equity",
        currency="USD",
        country=None,
        sector=None,
        industry=None,
    )

    assert master.add(record, _provenance()) is True

    stored = master.require("BRK-B")
    assert stored.symbol == "BRK-B"
    assert stored.cik == "0001067983"
    assert master.require("BRK.B") == stored
    assert [item.symbol for item in master.by_cik("1067983")] == ["BRK-B"]


def test_master_load_is_all_or_nothing_when_a_later_record_conflicts() -> None:
    """A rejected document cannot leave the master half-enriched."""

    master = SecurityMaster()
    apple = SecurityRecord(
        symbol="AAPL",
        name="Apple Inc.",
        cik="0000320193",
        exchange=None,
        mic=None,
        asset_class="equity",
        currency="USD",
        country=None,
        sector=None,
        industry=None,
    )
    original_provenance = _provenance()
    master.add(apple, original_provenance)

    microsoft = replace(apple, symbol="MSFT", name="Microsoft Corp", cik="0000789019")
    impostor = replace(apple, cik="0000000001")

    with pytest.raises(SecurityConflict, match="AAPL"):
        master.load([microsoft, impostor], _provenance(as_of="2026-10-27"))

    assert len(master) == 1
    assert master.get("MSFT") is None
    assert master.provenance("AAPL") == (original_provenance,)


def test_master_rejects_objects_that_are_not_security_records() -> None:
    """The master holds records, not dictionaries a caller hoped were records."""

    master = SecurityMaster()

    with pytest.raises(TypeError, match="SecurityRecord"):
        master.load([{"symbol": "AAPL"}], _provenance())  # type: ignore[list-item]

    with pytest.raises(TypeError, match="Provenance"):
        master.load([], object())  # type: ignore[arg-type]


def test_master_is_independent_of_the_document_it_was_built_from() -> None:
    """Mutating the input payload after a load cannot change the master."""

    payload = _company_tickers()
    master = SecurityMaster.from_company_tickers(payload, as_of="2026-10-26")

    payload["0"]["cik_str"] = 1  # type: ignore[index]
    payload["4"] = {"cik_str": 9, "ticker": "LATE", "title": "Late"}

    assert master.require("AAPL").cik == "0000320193"
    assert len(master) == 4
    assert master.get("LATE") is None


def test_add_submissions_on_a_broken_document_loads_nothing() -> None:
    """A malformed submissions body is a zero-result load, not a crash."""

    master = SecurityMaster()

    assert master.add_submissions({"cik": "bad", "name": "X"}, as_of="2026-10-26") == 0
    assert len(master) == 0


def test_sec_module_exposes_the_two_document_urls() -> None:
    """The slice names its sources so a fetch layer cannot invent a URL."""

    assert sec.TICKERS_URL == TICKERS_URL
    assert TICKERS_URL == "https://www.sec.gov/files/company_tickers.json"
    assert sec.SUBMISSIONS_URL == "https://data.sec.gov/submissions/CIK{cik}.json"


# ---------------------------------------------------------------------------
# R3 §1.3: the submissions profile — SIC industry and business country
# ---------------------------------------------------------------------------

#: Distinguishes "the key is absent" from "the key is present and null".
_MISSING: object = object()


def _profile_body(
    *,
    cik: str = "0000320193",
    name: str = "Apple Inc.",
    ticker: str = "AAPL",
    sic_description: object = "Electronic Computers",
    business: object = _MISSING,
) -> dict[str, object]:
    """A submissions body with the SIC field and an ``addresses.business``.

    The SEC states industry as ``sicDescription`` and country as the business
    address; ``_MISSING`` leaves a key out entirely so a test can tell an
    absent field from a present but blank one.
    """

    body: dict[str, object] = {
        "cik": cik,
        "name": name,
        "tickers": [ticker],
        "exchanges": ["Nasdaq"],
        "sic": "3571",
    }
    if sic_description is not _MISSING:
        body["sicDescription"] = sic_description
    if business is not _MISSING:
        body["addresses"] = {"business": business}
    return body


def test_parse_submissions_reads_the_sic_industry_and_a_us_business_country() -> None:
    """A US state code is ``United States``; the SIC description is industry."""

    records = parse_submissions(
        _profile_body(
            sic_description="  Electronic Computers  ",
            business={
                "stateOrCountry": "CA",
                "stateOrCountryDescription": "California",
            },
        )
    )

    assert len(records) == 1
    apple = records[0]
    assert apple.industry == "Electronic Computers"
    assert apple.country == "United States"
    assert apple.sector is None
    # The venue is still read exactly as before.
    assert apple.exchange == "Nasdaq"
    assert apple.mic == "XNAS"


def test_parse_submissions_uses_a_foreign_location_description_as_the_country() -> None:
    """``P7``/``Netherlands`` is the country; the code alone is not a name."""

    records = parse_submissions(
        _profile_body(
            cik="0000937966",
            name="ASML Holding N.V.",
            ticker="ASML",
            business={
                "stateOrCountry": "P7",
                "stateOrCountryDescription": "Netherlands",
            },
        )
    )

    assert [record.symbol for record in records] == ["ASML"]
    assert records[0].country == "Netherlands"
    assert records[0].industry == "Electronic Computers"
    assert records[0].sector is None


@pytest.mark.parametrize("sic_description", [_MISSING, None, "", "   "])
def test_a_sic_code_without_a_description_is_not_an_industry(
    sic_description: object,
) -> None:
    """The description is the industry; the bare ``sic`` code is never printed."""

    described = parse_submissions(_profile_body())[0]
    undescribed = parse_submissions(_profile_body(sic_description=sic_description))[0]

    assert described.industry == "Electronic Computers"
    assert undescribed.industry is None
    assert undescribed.sector is None


@pytest.mark.parametrize(
    "business",
    [
        _MISSING,
        None,
        {},
        {"stateOrCountry": "   ", "stateOrCountryDescription": "   "},
        {"city": "Cupertino"},
        "CA",
    ],
)
def test_an_unusable_location_is_not_a_country(business: object) -> None:
    """No business address means no country; the industry is unaffected."""

    records = parse_submissions(_profile_body(business=business))

    assert records[0].country is None
    assert records[0].industry == "Electronic Computers"
    assert records[0].sector is None


@pytest.mark.parametrize(
    "code", ["CA", "NY", "TX", "FL", "DC", "PR", "VI", "GU", "AS", "MP"]
)
def test_submissions_country_maps_a_us_state_or_territory_to_the_united_states(
    code: str,
) -> None:
    """The 50 states plus DC/PR/VI/GU/AS/MP are one country, not a place name."""

    payload = {
        "cik": "0000320193",
        "addresses": {
            "business": {
                "stateOrCountry": code,
                "stateOrCountryDescription": "Somewhere",
            }
        },
    }

    assert sec.submissions_country(payload) == "United States"


@pytest.mark.parametrize(
    ("business", "expected"),
    [
        (
            {"stateOrCountry": "P7", "stateOrCountryDescription": "Netherlands"},
            "Netherlands",
        ),
        (
            {
                "stateOrCountry": "A6",
                "stateOrCountryDescription": "  Ontario  ",
            },
            "Ontario",
        ),
    ],
)
def test_submissions_country_uses_the_description_of_a_non_us_location(
    business: dict[str, str], expected: str
) -> None:
    """Only the document's own description names a foreign country."""

    assert sec.submissions_country({"addresses": {"business": business}}) == expected


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        "nope",
        3,
        {},
        {"addresses": []},
        {"addresses": {"business": "CA"}},
        {"addresses": {"business": {"stateOrCountry": "P7"}}},
        {
            "addresses": {
                "business": {
                    "stateOrCountry": "P7",
                    "stateOrCountryDescription": "  ",
                }
            }
        },
        {
            "addresses": {
                "business": {
                    "stateOrCountry": "",
                    "stateOrCountryDescription": "Netherlands",
                }
            }
        },
    ],
)
def test_submissions_country_returns_none_without_a_usable_location(
    payload: object,
) -> None:
    """A foreign code with no description is unknown, never the code itself."""

    assert sec.submissions_country(payload) is None


def test_add_submissions_fills_industry_and_country_on_an_indexed_record() -> None:
    """The master enriches an index record instead of adding a second one."""

    master = SecurityMaster.from_company_tickers(_company_tickers(), as_of="2026-10-26")
    before = master.require("AAPL")
    assert before.industry is None
    assert before.country is None

    added = master.add_submissions(
        _profile_body(
            business={
                "stateOrCountry": "CA",
                "stateOrCountryDescription": "California",
            }
        ),
        as_of="2026-10-27",
    )

    assert added == 0, "AAPL is already indexed; this load only enriches it"
    apple = master.require("AAPL")
    assert apple.industry == "Electronic Computers"
    assert apple.country == "United States"
    assert apple.exchange == "Nasdaq"
    assert apple.mic == "XNAS"
    assert apple.sector is None
    assert len(master.provenance("AAPL")) == 2
