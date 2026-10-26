"""SEC EDGAR as the identity source: three documents, one record shape.

EDGAR answers "who is this instrument?" from three public documents:

* ``https://www.sec.gov/files/company_tickers_exchange.json`` — the whole
  registrant index **with the venue SEC publishes**. Its body is a named-column
  table, not an object::

      {"fields": ["cik", "name", "ticker", "exchange"],
       "data": [[320193, "Apple Inc.", "AAPL", "Nasdaq"], ...]}

  This is the bulk identity authority: one request gives the universe and every
  venue, so resolving 10k symbols does not need 10k submissions calls. Columns
  are bound by the ``fields`` header, never by position — a reordered header
  still parses, because position is presentation, not data.
* ``https://www.sec.gov/files/company_tickers.json`` — the legacy registrant
  index, kept as the fallback. It is an **object keyed by index strings**, not
  an array::

      {"0": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA CORP"}, ...}

  It carries a ticker, a CIK and a title. It carries no venue, no sector, no
  country and no currency. Those fields stay ``None`` on the parsed record: an
  identity field that the document does not state is not filled from a guess.
* ``https://data.sec.gov/submissions/CIK##########.json`` — one registrant with
  its listed classes::

      {"cik": "0000320193", "name": "Apple Inc.",
       "tickers": ["AAPL"], "exchanges": ["Nasdaq"], ...}

  One CIK can own several tickers (Berkshire's ``BRK-A``/``BRK-B``), so this
  parser returns one record per ticker, pairing ``tickers[i]`` with
  ``exchanges[i]`` when that element exists.

Spelling
--------
EDGAR writes a share class with a hyphen (``BRK-B``); screens often send the
dot spelling (``BRK.B``). :func:`normalize_symbol` maps both to EDGAR's spelling
so one instrument cannot become two. A CIK is always printed as ten digits
because that is the path the submissions API publishes; :func:`normalize_cik`
accepts an integer, a padded string or a ``CIK…`` prefix and returns the padded
form.

Tolerance is deliberate but bounded
-----------------------------------
A malformed row is skipped: a blank ticker, a zero or non-numeric CIK, a missing
title. A ticker that appears twice with **different** CIKs is ambiguous and is
dropped entirely rather than letting JSON order pick an issuer. The parsers
never raise on a wrong JSON shape; they return ``()``. That is the honest
answer for "this body is not the document we expected", and it keeps the caller
from having to distinguish an exception dialect per vendor.

Licence
-------
EDGAR is a public, redistributable source under its fair-access policy: ten
requests per second, a declared user agent with contact details at fetch time.
The policy belongs to the source description, not to the parser; this module
performs no I/O at all.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.engine.model import LicenseClass, SecurityRecord, SourceSpec

#: Adapter key used in :class:`~app.engine.model.Provenance`.
SOURCE_NAME = "sec"

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
TICKERS_EXCHANGE_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"

#: SEC prints a CIK with ten digits in the submissions path.
CIK_WIDTH = 10

#: The index and the submissions document both cover SEC registrants, whose
#: tickers are US-listed equity and whose financial statements are USD.
ASSET_CLASS = "equity"
CURRENCY = "USD"

#: Static, non-secret description of the source (02 §3.4 L1, trust 1). The
#: rate limit is EDGAR's published fair-access ceiling of 10 requests/second.
SEC_SOURCE = SourceSpec(
    name=SOURCE_NAME,
    license_class=LicenseClass.PUBLIC,
    redistribution=True,
    attribution="U.S. Securities and Exchange Commission — EDGAR",
    terms_url="https://www.sec.gov/os/webmaster-faq#developers",
    features=frozenset({"identity", "reference"}),
    rate_limit=(10, 1.0),
)

#: Venue name as EDGAR prints it → ISO 10383 MIC. Only venues whose mapping is
#: unambiguous are listed; anything else is ``None``. A guessed MIC would make
#: a record look verified when it is not.
#:
#: ``TXSE`` (Texas Stock Exchange) is deliberately absent: the venue is too new
#: to have a verified MIC, so the record keeps the raw venue and ``mic=None``
#: rather than borrowing another exchange's code.
_EXCHANGE_MIC: dict[str, str] = {
    "NASDAQ": "XNAS",
    "NASDAQ GLOBAL SELECT": "XNAS",
    "NASDAQ GLOBAL SELECT MARKET": "XNAS",
    "NASDAQ GLOBAL MARKET": "XNAS",
    "NASDAQ CAPITAL MARKET": "XNAS",
    "NYSE": "XNYS",
    "NEW YORK STOCK EXCHANGE": "XNYS",
    "NYSE AMERICAN": "XASE",
    "NYSE MKT": "XASE",
    "AMEX": "XASE",
    "CBOE BZX": "BATS",
    "CBOE EDGX": "EDGX",
    "OTC": "OTCM",
    "CBOE": "XCBO",
}

#: US states plus the territories EDGAR can print in ``stateOrCountry``. A
#: submissions business address in one of these is the United States as a
#: country; the code alone names a subdivision, not a country.
_US_STATE_CODES: frozenset[str] = frozenset(
    {
        "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
        "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
        "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
        "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
        "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
        "DC", "PR", "VI", "GU", "AS", "MP",
    }
)


def normalize_cik(value: Any) -> str | None:
    """Return the ten-digit CIK, or ``None`` if the value is not one.

    Accepts ``320193``, ``"320193"``, ``"0000320193"`` and ``"CIK0000320193"``.
    Rejects booleans, floats, zero, negatives, non-digits and anything longer
    than ten digits: those are not identifiers, they are parse errors.
    """

    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        number = value
    elif isinstance(value, str):
        text = "".join(value.split()).upper()
        if text.startswith("CIK"):
            text = text[3:]
        if not text.isdigit():
            return None
        number = int(text)
    else:
        return None
    if number <= 0:
        return None
    digits = str(number)
    if len(digits) > CIK_WIDTH:
        return None
    return digits.zfill(CIK_WIDTH)


def normalize_symbol(value: Any) -> str | None:
    """Return EDGAR's symbol spelling, or ``None`` if the value is not a symbol.

    Upper-cased and trimmed; the dot spelling of a share class becomes EDGAR's
    hyphen (``BRK.B`` → ``BRK-B``). Internal whitespace means the value is a
    company name or a corrupted cell, not a ticker, so it is rejected.
    """

    if not isinstance(value, str):
        return None
    text = value.strip().upper()
    if not text or any(character.isspace() for character in text):
        return None
    text = text.replace(".", "-")
    if not any(character.isalnum() for character in text):
        return None
    return text


def exchange_mic(exchange: Any) -> str | None:
    """Map an EDGAR venue name to its MIC; unknown venues are ``None``."""

    if not isinstance(exchange, str):
        return None
    key = " ".join(exchange.split()).upper()
    return _EXCHANGE_MIC.get(key)


def submissions_url(cik: Any) -> str:
    """The submissions URL for one CIK, padded. Raises on a non-CIK."""

    normalized = normalize_cik(cik)
    if normalized is None:
        raise ValueError(f"submissions URL requires a valid cik, got {cik!r}")
    return SUBMISSIONS_URL.format(cik=normalized)


def _record(
    *,
    symbol: str,
    name: str,
    cik: str,
    exchange: str | None = None,
    industry: str | None = None,
    country: str | None = None,
) -> SecurityRecord:
    """Build the identity record every SEC document describes.

    The parsers share this constructor so a record from the bulk document, a
    record from the legacy index and a record from a submissions body cannot
    disagree about which fields are known. ``industry``/``country`` default to
    ``None`` because only a submissions profile states them.
    """

    return SecurityRecord(
        symbol=symbol,
        name=name,
        cik=cik,
        exchange=exchange,
        mic=exchange_mic(exchange),
        asset_class=ASSET_CLASS,
        currency=CURRENCY,
        country=country,
        sector=None,
        industry=industry,
        active=True,
    )


def _text(value: Any) -> str | None:
    """A trimmed non-blank string, or ``None`` — never ``str(value)``.

    Printing ``None``, a number or an empty string as identity text would make
    an absent field look stated, which is exactly the fabrication the records
    avoid.
    """

    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def parse_company_tickers(payload: Any) -> tuple[SecurityRecord, ...]:
    """Parse ``company_tickers.json`` into records sorted by symbol.

    Rows without a ticker, a valid CIK or a title are skipped. A ticker bound to
    two different CIKs is ambiguous and is dropped; a repeated ticker with the
    same CIK is one instrument and the first row's title wins.
    """

    if not isinstance(payload, Mapping):
        return ()

    by_symbol: dict[str, SecurityRecord] = {}
    ciks_by_symbol: dict[str, set[str]] = {}

    for entry in payload.values():
        if not isinstance(entry, Mapping):
            continue
        symbol = normalize_symbol(entry.get("ticker"))
        cik = normalize_cik(entry.get("cik_str"))
        title = entry.get("title")
        if symbol is None or cik is None or not isinstance(title, str):
            continue
        name = title.strip()
        if not name:
            continue
        ciks_by_symbol.setdefault(symbol, set()).add(cik)
        by_symbol.setdefault(symbol, _record(symbol=symbol, name=name, cik=cik))

    ambiguous = {
        symbol for symbol, ciks in ciks_by_symbol.items() if len(ciks) > 1
    }
    return tuple(
        by_symbol[symbol]
        for symbol in sorted(by_symbol)
        if symbol not in ambiguous
    )


def parse_submissions(payload: Any) -> tuple[SecurityRecord, ...]:
    """Parse one submissions document into one record per listed ticker.

    ``tickers`` and ``exchanges`` are parallel arrays; a missing or malformed
    exchange element leaves the venue unknown rather than borrowing a sibling's.
    The body is unusable without a CIK, a name and a list of tickers, and an
    empty body is an empty parse.

    The profile fields come from SEC's own wording: ``sicDescription`` is the
    industry (the bare ``sic`` code is a code, not a description), and the
    business address is the location. ``sector`` stays ``None`` because SIC is
    an industry taxonomy and inventing a sector mapping would be a fabricated
    classification.
    """

    if not isinstance(payload, Mapping):
        return ()
    cik = normalize_cik(payload.get("cik"))
    raw_name = payload.get("name")
    if cik is None or not isinstance(raw_name, str):
        return ()
    name = raw_name.strip()
    if not name:
        return ()
    raw_tickers = payload.get("tickers")
    if not isinstance(raw_tickers, list):
        return ()
    raw_exchanges = payload.get("exchanges")
    exchanges = raw_exchanges if isinstance(raw_exchanges, list) else []
    industry = _text(payload.get("sicDescription"))
    country = submissions_country(payload)

    records: list[SecurityRecord] = []
    seen: set[str] = set()
    for index, raw_ticker in enumerate(raw_tickers):
        symbol = normalize_symbol(raw_ticker)
        if symbol is None or symbol in seen:
            continue
        seen.add(symbol)
        exchange: str | None = None
        if index < len(exchanges):
            candidate = exchanges[index]
            if isinstance(candidate, str):
                exchange = candidate.strip() or None
        records.append(
            _record(
                symbol=symbol,
                name=name,
                cik=cik,
                exchange=exchange,
                industry=industry,
                country=country,
            )
        )

    return tuple(sorted(records, key=lambda record: record.symbol))


def parse_company_tickers_exchange(payload: Any) -> tuple[SecurityRecord, ...]:
    """Parse ``company_tickers_exchange.json`` into records sorted by symbol.

    The body is ``{"fields": [...], "data": [[...], ...]}``: a header and
    positional rows. The header, not the position, binds a value to a field —
    EDGAR may reorder its columns, and a row read by assumption would silently
    become another issuer.

    A row without a valid CIK, a normalisable ticker or a non-blank name is
    skipped: repairing a malformed identity would make the master assert
    something no document says. A ticker bound to two different CIKs is
    ambiguous and dropped entirely; a repeated ticker with the same CIK is one
    instrument and keeps its first row. The venue is the raw string SEC
    publishes (``None`` when the document has none); :func:`exchange_mic` is
    the only place a venue becomes a MIC. Any body that is not this table is an
    empty parse, not an exception.
    """

    if not isinstance(payload, Mapping):
        return ()
    fields = payload.get("fields")
    rows = payload.get("data")
    if not isinstance(fields, list) or not isinstance(rows, list):
        return ()

    columns: dict[str, int] = {}
    for position, field_name in enumerate(fields):
        if isinstance(field_name, str):
            key = field_name.strip().casefold()
            if key and key not in columns:
                columns[key] = position

    def cell(row: list[Any], column: str) -> Any:
        """The row's value for one named column, or ``None`` when absent."""

        position = columns.get(column)
        if position is None or position >= len(row):
            return None
        return row[position]

    by_symbol: dict[str, SecurityRecord] = {}
    ciks_by_symbol: dict[str, set[str]] = {}

    for row in rows:
        if not isinstance(row, list):
            continue
        symbol = normalize_symbol(cell(row, "ticker"))
        cik = normalize_cik(cell(row, "cik"))
        name = _text(cell(row, "name"))
        if symbol is None or cik is None or name is None:
            continue
        exchange = _text(cell(row, "exchange"))
        ciks_by_symbol.setdefault(symbol, set()).add(cik)
        by_symbol.setdefault(
            symbol,
            _record(symbol=symbol, name=name, cik=cik, exchange=exchange),
        )

    ambiguous = {
        symbol for symbol, ciks in ciks_by_symbol.items() if len(ciks) > 1
    }
    return tuple(
        by_symbol[symbol]
        for symbol in sorted(by_symbol)
        if symbol not in ambiguous
    )


def submissions_country(payload: Any) -> str | None:
    """The issuer's country from a submissions body, or ``None``.

    SEC states the business address, not a country field. A two-letter US
    state/territory code means the United States — taking the subdivision name
    (``California``) as a country would be wrong, and taking the code itself
    would be an abbreviation in a country field. Any other location is only
    named by SEC's own description (``P7``/``Netherlands``); a code with no
    description is unknown, never the code printed as a country. The company
    name is never consulted: a name is not a location.
    """

    if not isinstance(payload, Mapping):
        return None
    addresses = payload.get("addresses")
    if not isinstance(addresses, Mapping):
        return None
    business = addresses.get("business")
    if not isinstance(business, Mapping):
        return None

    code = _text(business.get("stateOrCountry"))
    if code is None:
        return None
    if code.upper() in _US_STATE_CODES:
        return "United States"
    return _text(business.get("stateOrCountryDescription"))


__all__ = [
    "CIK_WIDTH",
    "SEC_SOURCE",
    "SOURCE_NAME",
    "SUBMISSIONS_URL",
    "TICKERS_EXCHANGE_URL",
    "TICKERS_URL",
    "exchange_mic",
    "normalize_cik",
    "normalize_symbol",
    "parse_company_tickers",
    "parse_company_tickers_exchange",
    "parse_submissions",
    "submissions_country",
    "submissions_url",
]
