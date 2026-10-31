"""One venue vocabulary for the depth store: provider spelling → MIC.

Measured inputs (2026-10-31, read-only, no write to the production store):

* ``ztockz.db`` ``screen_rows`` (1,900 rows) carries **17** codes and all of
  them are already MICs — ``XNAS 887 · XNYS 515 · XASE 128 · SSE 87 · SIX 45 ·
  LSE 45 · KRX 36 · HKEX 33 · TWSE 18 · TSE 18 · XETR 15 · TSX 15 · TADAWUL 15
  · SZSE 15 · EURONEXT 15 · XCBO 10 · BCS 3``. The snaphot is written after
  :func:`app.screen.universe.mic_for`, so the store must preserve every one of
  them verbatim — thirteen of them are foreign codes with no US MIC.
* ``/home/ubuntu/ztockz-data/depth.json`` (4,217 rows) carries **six**:
  ``Nasdaq 2,550 · NYSE 1,652 · TXSE 9 · NasdaqGM 4 · NasdaqCM 1 · NasdaqGS 1``.
  Two writers, three vocabularies: SEC/EDGAR prints a venue name, Yahoo prints
  its own product code (``NasdaqGM`` is a market tier, not a venue), and the
  screener prints a MIC.
* The EDGAR snapshot (10,433 records) prints
  ``{Nasdaq 4,357 · NYSE 3,270 · OTC 2,579 · null 171 · CBOE 44 · TXSE 12}`` —
  it never prints ``AMEX`` or ``NYSE American`` at all, which is why the 164
  ``NYSE``-vs-``XASE`` rows exist and are *not* a defect this table can fix.

The rules pinned here:

* a known provider spelling becomes its MIC, and the original is kept as
  ``exchange_source`` metadata — a store column, never a catalogue column;
* a MIC is already normalised, so the function is **idempotent**;
* an **unknown** code is preserved verbatim: never dropped, never guessed;
* ``TXSE`` is a documented pass-through. The venue is too new to have a
  verified MIC (``app/engine/sources/sec.py`` states it and stores
  ``mic=None`` for its 12 records), so mapping it to ``XASE`` would assert an
  identity no source states — and would erase the 9-row disagreement the
  research measured instead of surfacing it.
"""

from __future__ import annotations

import pytest

from app.market.venue import (
    EXCHANGE_SOURCE_FIELD,
    PASSTHROUGH_VENUES,
    VENUE_MIC,
    normalize_venue,
    venue_mic,
)
from app.screen.universe import MIC_BY_EXCHANGE

#: ``screen_rows`` on 2026-10-31 — 17 codes, 1,900 rows. Every one is a MIC and
#: must survive normalisation unchanged (13 of them are not US venues at all).
SCREEN_ROW_CODES: dict[str, int] = {
    "XNAS": 887,
    "XNYS": 515,
    "XASE": 128,
    "SSE": 87,
    "SIX": 45,
    "LSE": 45,
    "KRX": 36,
    "HKEX": 33,
    "TWSE": 18,
    "TSE": 18,
    "XETR": 15,
    "TSX": 15,
    "TADAWUL": 15,
    "SZSE": 15,
    "EURONEXT": 15,
    "XCBO": 10,
    "BCS": 3,
}

#: ``depth.json`` on 2026-10-31 — 4,217 rows, six spellings.
DEPTH_CODES: dict[str, int] = {
    "Nasdaq": 2_550,
    "NYSE": 1_652,
    "TXSE": 9,
    "NasdaqGM": 4,
    "NasdaqCM": 1,
    "NasdaqGS": 1,
}

#: The provider spellings the mapping must know, and the venue each names.
#: ``AMEX``/``NYSE American`` are included although EDGAR never prints them:
#: they are Yahoo/venue spellings of XASE and the app already treats them as
#: such (``app/engine/sources/sec.py`` maps both).
PROVIDER_SPELLINGS: dict[str, str] = {
    "Nasdaq": "XNAS",
    "NASDAQ": "XNAS",
    "NasdaqGS": "XNAS",
    "NasdaqGM": "XNAS",
    "NasdaqCM": "XNAS",
    "NASDAQ Global Select": "XNAS",
    "Nasdaq Capital Market": "XNAS",
    "NYSE": "XNYS",
    "New York Stock Exchange": "XNYS",
    "AMEX": "XASE",
    "NYSE American": "XASE",
    "NYSE MKT": "XASE",
    "CBOE": "XCBO",
    "OTC": "OTCM",
}


def test_every_screen_row_code_is_a_mic_and_survives_untouched() -> None:
    for code, rows in SCREEN_ROW_CODES.items():
        normalized, source = normalize_venue(code)
        assert normalized == code, f"{code} ({rows} rows) must not be rewritten"
        assert source == code


def test_every_depth_spelling_maps_or_is_an_explicit_passthrough() -> None:
    for spelling, rows in DEPTH_CODES.items():
        normalized, source = normalize_venue(spelling)
        assert source == spelling, "the original spelling is always kept"
        if spelling == "TXSE":
            assert normalized == "TXSE", (
                "TXSE has no verified MIC; relabelling it XASE would hide the "
                "nine-row disagreement instead of recording it"
            )
            assert normalized in PASSTHROUGH_VENUES
        else:
            assert normalized in MIC_BY_EXCHANGE.values(), (
                f"{spelling} ({rows} rows) must become a screener venue code"
            )


@pytest.mark.parametrize(("spelling", "mic"), sorted(PROVIDER_SPELLINGS.items()))
def test_a_provider_spelling_becomes_its_mic(spelling: str, mic: str) -> None:
    assert normalize_venue(spelling) == (mic, spelling)
    assert venue_mic(spelling) == mic


def test_whitespace_and_case_are_not_a_venue() -> None:
    assert normalize_venue("  nasdaq  ") == ("XNAS", "nasdaq")
    assert normalize_venue("NYSE\tAmerican") == ("XASE", "NYSE American")


def test_normalisation_is_idempotent_over_every_observed_code() -> None:
    observed = [*SCREEN_ROW_CODES, *DEPTH_CODES, *PROVIDER_SPELLINGS, *VENUE_MIC]
    for value in observed:
        once, _ = normalize_venue(value)
        twice, _ = normalize_venue(once)
        assert twice == once, f"normalise(normalise({value!r})) != normalise({value!r})"


def test_an_unknown_code_is_preserved_never_dropped_and_never_guessed() -> None:
    for unknown in ("BATS", "XTXE", "IEX", "MUN", "TRADEGATE"):
        normalized, source = normalize_venue(unknown)
        assert normalized == unknown, "an unknown venue is kept exactly as printed"
        assert source == unknown
        assert venue_mic(unknown) is None, "an unknown venue has no MIC, not a guess"


def test_a_missing_venue_is_empty_not_a_string() -> None:
    for missing in (None, "", "   "):
        assert normalize_venue(missing) == ("", None)


def test_the_table_answers_only_mics_or_documented_passthroughs() -> None:
    allowed = set(MIC_BY_EXCHANGE.values()) | {"OTCM"}
    for spelling, mic in VENUE_MIC.items():
        assert mic in allowed or mic == spelling, (
            f"{spelling} → {mic} is outside the venue vocabulary"
        )
    assert "TXSE" not in VENUE_MIC, "a passthrough is not a mapping"
    assert PASSTHROUGH_VENUES == frozenset({"TXSE"})


def test_the_depth_store_vocabulary_fits_the_screener_vocabulary() -> None:
    """The join the research measured: six depth spellings → three codes.

    All six observed spellings, mapped, in one table — the executable form of
    the mapping document. Five become a MIC the screener already speaks, and
    ``TXSE`` stays itself (no verified MIC exists for it).
    """

    mapped = {value: normalize_venue(value)[0] for value in DEPTH_CODES}
    assert mapped == {
        "Nasdaq": "XNAS",
        "NYSE": "XNYS",
        "TXSE": "TXSE",
        "NasdaqGM": "XNAS",
        "NasdaqCM": "XNAS",
        "NasdaqGS": "XNAS",
    }
    assert set(mapped.values()) <= set(MIC_BY_EXCHANGE.values()) | PASSTHROUGH_VENUES


def test_the_source_field_is_metadata_not_a_catalogue_column() -> None:
    from app.market.depth_fields import CATALOG_FIELDS

    assert EXCHANGE_SOURCE_FIELD == "exchange_source"
    assert EXCHANGE_SOURCE_FIELD not in CATALOG_FIELDS, (
        "the catalog is frozen at 116 columns; provenance is a store cell"
    )
