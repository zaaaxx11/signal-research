"""ETF holdings from SEC Form N-PORT.

An ETF files N-PORT-P every quarter with its full portfolio: one ``invstOrSec``
row per position, carrying name, CUSIP, balance, USD value, percentage and
asset category. That is the official answer to "what does this fund hold?" — no
vendor top-10 list, no scraping.

``pctVal`` is copied as SEC reports it: **percent points** (SPY's 504 rows sum
to ≈100). Treating it as a fraction would turn 0.08% into 8%.

Three rules decide every judgement here, because each of them is a way an ETF
page can lie:

* **Unknown is ``None``, never zero.** EDGAR leaves ``<balance/>`` blank on some
  rows and prints ``N/A`` where a number belongs. A zero would claim the fund
  held nothing, so blank or unparseable text becomes ``None`` while a reported
  ``0`` stays ``0.0``.
* **A row without a name is not a holding.** The name is the only field the
  document shape requires, so a row that lost it is dropped rather than
  published as an unnamed position.
* **A failed body is one named error, never an empty portfolio.** A 500, an
  HTML error page or an unparseable document raises
  :class:`HoldingsUnavailable`; "SEC did not answer" must not look like "this
  fund holds nothing".

The parser is pure: no network, no clock, no cache. The collector is the only
part that touches EDGAR, and it takes its transport by injection so the whole
slice is testable without a socket.

Contract: ``briefs/market-engine/R5D-CONTRACT.md``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import zip_longest
import math
import os
from pathlib import Path
import tempfile
from typing import Any
import xml.etree.ElementTree as ET

from app.engine.fund_series import FundSeries, fund_series_path, load_fund_series
from app.engine.ingest import DEFAULT_USER_AGENT, fetch_submissions
from app.engine.sources.sec import normalize_cik
from app.providers import http as provider_http

#: The one form whose XML carries a full portfolio. ``NPORT-EX`` is the exhibit
#: (a list of the fund's other securities) and ``N-CSR`` is an annual report:
#: neither is the monthly portfolio this slice publishes.
NPORT_FORM = "NPORT-P"

#: EDGAR full-text search. Unlike ``submissions`` this index answers "which
#: filings mention this series id", which is the only official way to tell two
#: series of one trust apart. One page of 100 covers a series' whole quarterly
#: history, so the lookup never paginates.
EFTS_SEARCH_URL = "https://efts.sec.gov/LATEST/search-index"
EFTS_PAGE_SIZE = 100


#: EDGAR's archive path for one filing body. ``cik`` is the unpadded integer
#: spelling and ``accession`` carries no dashes — that is the published shape.
NPORT_ARCHIVE_URL = (
    "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{document}"
)


class HoldingsNotFound(LookupError):
    """The issuer has no N-PORT-P filing in its recent submissions."""


class HoldingsUnavailable(RuntimeError):
    """The N-PORT body could not be fetched or parsed."""


@dataclass(frozen=True, slots=True)
class EtfHolding:
    """One portfolio position exactly as the fund reported it."""
    name: str
    title: str | None
    cusip: str | None
    balance: float | None
    value_usd: float | None
    pct_val: float | None
    asset_category: str | None
    country: str | None
    currency: str | None

    def document(self) -> dict[str, object]:
        """Project the holding into its JSON shape.

        The nine contract fields and nothing else: a consumer that keys on this
        mapping must be able to rely on its exact vocabulary, and leaking an
        ``lei`` or a ``fair_val_level`` here would make every consumer parse
        around a field it never asked for.
        """

        return {
            "name": self.name,
            "title": self.title,
            "cusip": self.cusip,
            "balance": self.balance,
            "value_usd": self.value_usd,
            "pct_val": self.pct_val,
            "asset_category": self.asset_category,
            "country": self.country,
            "currency": self.currency,
        }


# ---------------------------------------------------------------------------
# XML traversal
# ---------------------------------------------------------------------------


def _local(tag: object) -> str:
    """The element's name without an XML namespace, or ``""``.

    The real body declares ``xmlns="http://www.sec.gov/edgar/nport"``, but a
    copy without it — or with a different URI — is still the same document.
    Matching on the local name is what keeps the walk from silently finding
    nothing because a namespace changed.
    """

    text = tag if isinstance(tag, str) else ""
    return text.rsplit("}", 1)[-1]


def _text(element: ET.Element | None) -> str | None:
    """The element's stripped text, or ``None`` when it is absent or blank."""

    if element is None or element.text is None:
        return None
    text = element.text.strip()
    return text or None


def _number(raw: str | None) -> float | None:
    """A reported amount, or ``None``.

    ``float`` is deliberately strict: ``"N/A"`` and ``"1,234"`` are text EDGAR
    prints where a number belongs, and turning either into a value would publish
    an amount nobody reported. A reported ``"0"`` is a number and stays ``0.0``.
    A non-finite reading (``inf``/``nan``) is rejected for the same reason a
    word is: it is not an amount a fund can hold.
    """

    if raw is None:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def _xml_text(xml_text: str, tag: str) -> str | None:
    """The text of the first element named ``tag``, or ``None``.

    ``seriesId`` and ``repPdDate`` live in ``genInfo``; matching on the local
    name keeps a namespaced document readable (the real body declares the
    nport namespace, a copy may omit it). A body that does not parse answers
    ``None`` rather than raising: :func:`parse_nport` already owns the "this is
    not an N-PORT" judgement, and the caller uses the miss only as a fallback
    for a date it can take from the index instead.
    """

    try:
        root = ET.fromstring(xml_text)
    except (ET.ParseError, ValueError):
        return None
    for element in root.iter():
        if _local(element.tag) == tag:
            return _text(element)
    return None


# ---------------------------------------------------------------------------
# §1 Parser
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SeriesFiling:
    """One N-PORT filing located through EDGAR full-text search."""

    filed: str
    report_date: str | None
    accession: str
    document: str


def parse_nport(xml_text: str) -> tuple[EtfHolding, ...]:
    """Read every ``invstOrSec`` row of one N-PORT body, in document order.

    A body that is not XML, or that has no portfolio rows, answers ``()``: an
    HTML error page served with a 200 is not an N-PORT, and a caller that has
    to catch a parser's exception dialect would end up treating it as one. That
    is also why the malformed answer is empty rather than a raised error — the
    collector is the layer that decides an empty parse is a failure.

    Fields are read from the row's **direct children** by local tag name. The
    real document nests a few groups (``identifiers``, ``securityLending``) that
    this slice does not publish, and a recursive search would let a nested
    ``<name>`` from one of them shadow the holding's own name.
    """

    try:
        root = ET.fromstring(xml_text)
    except (ET.ParseError, ValueError):
        return ()

    holdings: list[EtfHolding] = []
    for element in root.iter():
        if _local(element.tag) != "invstOrSec":
            continue
        fields = {_local(child.tag): child for child in element}
        name = _text(fields.get("name"))
        if name is None:
            # Without a name there is no position to publish; inventing one
            # (a CUSIP, "Unknown") would be a row the fund never filed.
            continue
        holdings.append(
            EtfHolding(
                name=name,
                title=_text(fields.get("title")),
                cusip=_text(fields.get("cusip")),
                balance=_number(_text(fields.get("balance"))),
                value_usd=_number(_text(fields.get("valUSD"))),
                pct_val=_number(_text(fields.get("pctVal"))),
                asset_category=_text(fields.get("assetCat")),
                country=_text(fields.get("invCountry")),
                currency=_text(fields.get("curCd")),
            )
        )
    return tuple(holdings)


# ---------------------------------------------------------------------------
# §2 Collector
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _NportFiling:
    """One ``filings.recent`` row this collector is willing to fetch."""

    accession: str
    filed: str
    report_date: str | None
    document: str


def nport_archive_url(cik: str, accession: str, primary_document: str) -> str:
    """The archive URL for one N-PORT body.

    ``primaryDocument`` is served with a viewer prefix
    (``xslFormNPORT-P_X01/primary_doc.xml``) that points at an HTML rendering,
    not the XML; only the basename is the document the parser can read.
    """

    key = normalize_cik(cik)
    if key is None:
        raise ValueError(f"not a SEC CIK: {cik!r}")
    document = primary_document.rsplit("/", 1)[-1].strip()
    return NPORT_ARCHIVE_URL.format(
        cik=int(key),
        accession=accession.replace("-", ""),
        document=document,
    )


def nport_cache_path(cache_dir: Path, cik: str, accession: str) -> Path:
    """``<cache_dir>/CIK##########-nport-<accession_nodash>.xml``.

    The CIK keys the issuer and the accession keys the filing, so two quarters
    filed on the same day cannot overwrite each other. The suffix keeps the
    entry self-describing beside the submissions, companyfacts and Form 4
    caches that share the directory.
    """

    key = normalize_cik(cik)
    if key is None:
        raise ValueError(f"not a SEC CIK: {cik!r}")
    return Path(cache_dir) / f"CIK{key}-nport-{accession.replace('-', '')}.xml"


def _columns(recent: Mapping[str, Any]) -> dict[str, list[Any]]:
    """The column arrays of ``filings.recent``, one list per name.

    ``recent`` is column-oriented: parallel arrays, not one object per row. A
    column that is absent or not a sequence is an empty list, so a usable
    accession still pairs with the columns that are present instead of the
    whole document collapsing.
    """

    names = (
        "accessionNumber",
        "filingDate",
        "form",
        "primaryDocument",
        "reportDate",
    )
    columns: dict[str, list[Any]] = {}
    for name in names:
        values = recent.get(name)
        if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
            columns[name] = []
        else:
            columns[name] = list(values)
    return columns


def _newest_nport_filing(payload: object) -> _NportFiling | None:
    """The newest ``form == "NPORT-P"`` row of ``filings.recent``, or ``None``.

    "Newest" is ``(filingDate, accession)`` descending: the filing date is what
    a reader means by latest, and the accession breaks a tie between two
    amendments filed the same day (EDGAR numbers them in submission order).
    A row without an accession, a filing date or a primary document cannot be
    requested, so it is not a candidate.

    ``zip_longest`` rather than ``zip``: the ``reportDate`` column may be absent
    from a document, and a plain ``zip`` would then truncate every row to zero
    rather than leave one date unknown.
    """

    if not isinstance(payload, Mapping):
        return None
    filings = payload.get("filings")
    if not isinstance(filings, Mapping):
        return None
    recent = filings.get("recent")
    if not isinstance(recent, Mapping):
        return None
    columns = _columns(recent)

    candidates: list[_NportFiling] = []
    for accession, filed, form, document, report_date in zip_longest(
        columns["accessionNumber"],
        columns["filingDate"],
        columns["form"],
        columns["primaryDocument"],
        columns["reportDate"],
        fillvalue=None,
    ):
        if form != NPORT_FORM:
            continue
        if not isinstance(accession, str) or not accession.strip():
            continue
        if not isinstance(filed, str) or not filed.strip():
            continue
        if not isinstance(document, str) or not document.strip():
            continue
        candidates.append(
            _NportFiling(
                accession=accession.strip(),
                filed=filed.strip(),
                report_date=(
                    report_date.strip()
                    if isinstance(report_date, str) and report_date.strip()
                    else None
                ),
                document=document.strip(),
            )
        )
    if not candidates:
        return None
    candidates.sort(key=lambda row: (row.filed, row.accession), reverse=True)
    return candidates[0]


def _headers() -> dict[str, str]:
    """Per-request headers; sent on the request so an injected client keeps them.

    EDGAR answers ``403`` to an anonymous client, so the User-Agent always
    carries the contact address from :data:`~app.engine.ingest.DEFAULT_USER_AGENT`
    — even when the caller supplied its own transport.
    """

    return {"user-agent": DEFAULT_USER_AGENT, "accept": "application/xml"}


async def _fetch_body(url: str, *, client: Any) -> str:
    """GET one N-PORT body; every transport answer becomes one error type.

    ``client=None`` builds the provider client **as a module attribute** so the
    suite can install a ``MockTransport`` at that seam. A non-200 answer is a
    failure, not a body: ``follow_redirects=False`` means a 3xx is a refusal,
    and an error page would otherwise parse to ``()`` and look like an empty
    portfolio.
    """

    headers = _headers()
    try:
        if client is None:
            async with provider_http.build_client(headers=headers) as owned:
                response = await owned.get(url, headers=headers)
        else:
            response = await client.get(url, headers=headers)
    except Exception as exc:  # noqa: BLE001 - one domain error for the boundary
        raise HoldingsUnavailable(f"{type(exc).__name__}: {exc}") from exc
    if response.status_code != 200:
        raise HoldingsUnavailable(
            f"HTTPStatusError: HTTP {response.status_code} on {url}"
        )
    return response.text


def _read_cache(path: Path | None) -> str | None:
    """The cached body, or ``None`` when there is nothing usable on disk.

    A blank file is a miss rather than an empty portfolio: an interrupted write
    leaves a zero-byte entry, and serving it would report a real filing as
    holding nothing.
    """

    if path is None:
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    return text if text.strip() else None


def _write_cache(path: Path, text: str) -> None:
    """Write the body atomically, so a crash cannot leave half a document."""

    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    )
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(text)
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _sort_key(holding: EtfHolding) -> tuple[bool, float, bool, float]:
    """Descending order by ``(pct_val, value_usd)``, unknown percentages last.

    The leading booleans put a reported value ahead of a missing one; the
    fallback ``0.0`` only ever orders rows inside the missing group, so it can
    never rank an unknown position above a reported one. ``sorted`` is stable,
    so two rows with identical keys keep document order.
    """

    return (
        holding.pct_val is not None,
        holding.pct_val if holding.pct_val is not None else 0.0,
        holding.value_usd is not None,
        holding.value_usd if holding.value_usd is not None else 0.0,
    )


def _efts_hits(payload: object) -> list[object]:
    """The ``hits.hits`` list of an EFTS response, or ``[]``.

    EFTS wraps its rows twice (``hits.hits``); anything else — an error object,
    a bare list — is not a result set this lookup can read, and answering "no
    rows" would turn a malformed answer into a "fund has no filings" verdict.
    The caller distinguishes the two, so a wrong shape is a failure.
    """

    if not isinstance(payload, Mapping):
        return []
    hits = payload.get("hits")
    if not isinstance(hits, Mapping):
        return []
    rows = hits.get("hits")
    if isinstance(rows, (str, bytes)) or not isinstance(rows, Sequence):
        return []
    return list(rows)


def _contains_cik(values: object, cik: str) -> bool:
    """True when the hit's ``ciks`` names the padded ten-digit ``cik``."""

    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        return False
    return any(normalize_cik(value) == cik for value in values)


def _contains_nport_form(values: object) -> bool:
    """True when the hit's ``root_forms`` includes ``NPORT-P``.

    ``NPORT-EX`` is an exhibit and ``N-CSR`` an annual report: either may
    mention the series, and neither carries the monthly portfolio.
    """

    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        return False
    return any(
        isinstance(value, str) and value.strip().upper() == NPORT_FORM
        for value in values
    )


def _series_filings(payload: object, *, cik: str) -> list[SeriesFiling]:
    """Every EFTS hit for ``cik`` that is an NPORT-P, newest-date data intact.

    ``_id`` is ``<accession>:<document>`` and is split at the **first** colon:
    the document name itself can contain one, while the accession cannot.
    """

    filings: list[SeriesFiling] = []
    for hit in _efts_hits(payload):
        if not isinstance(hit, Mapping):
            continue
        source = hit.get("_source")
        if not isinstance(source, Mapping):
            continue
        if not _contains_cik(source.get("ciks"), cik):
            continue
        if not _contains_nport_form(source.get("root_forms")):
            continue
        identifier = hit.get("_id")
        if not isinstance(identifier, str):
            continue
        accession, _, document = identifier.partition(":")
        accession = accession.strip()
        document = document.strip()
        filed = source.get("file_date")
        filed = filed.strip() if isinstance(filed, str) else ""
        if not accession or not document or not filed:
            continue
        report_date = source.get("period_ending")
        filings.append(
            SeriesFiling(
                filed=filed,
                report_date=(
                    report_date.strip()
                    if isinstance(report_date, str) and report_date.strip()
                    else None
                ),
                accession=accession,
                document=document,
            )
        )
    return filings


async def find_series_filing(
    series_id: str, cik: str, *, client: Any = None
) -> SeriesFiling | None:
    """Locate the newest N-PORT filing that belongs to one series.

    A trust CIK files one NPORT-P per series, so the submissions index cannot
    answer "which filing is IVV's": its newest row may be another ETF. EDGAR's
    full-text search can, because it indexes the XML body — asking for the
    series id returns exactly the filings whose document contains it.

    The result is filtered twice more, because full-text search is not a
    guarantee: the hit must name the padded CIK (another trust may mention the
    series) and its ``root_forms`` must be ``NPORT-P``. "Newest" is
    ``(file_date, accession)`` descending — the filing date is what a reader
    means, and the accession breaks a tie between two same-day filings.

    No qualifying hit is ``None`` (a normal "this fund has no filing", which the
    collector turns into :class:`HoldingsNotFound`); a transport or JSON failure
    is :class:`HoldingsUnavailable`, never an empty result set.
    """

    if not isinstance(series_id, str) or not series_id.strip():
        raise ValueError("series_id must be a non-blank SEC series id")
    key = normalize_cik(cik)
    if key is None:
        raise HoldingsUnavailable(f"ValueError: not a SEC CIK: {cik!r}")

    headers = {"user-agent": DEFAULT_USER_AGENT, "accept": "application/json"}
    params = {
        "q": series_id.strip(),
        "forms": NPORT_FORM,
        "from": "0",
        "size": str(EFTS_PAGE_SIZE),
    }
    try:
        if client is None:
            async with provider_http.build_client(headers=headers) as owned:
                response = await owned.get(
                    EFTS_SEARCH_URL, params=params, headers=headers
                )
        else:
            response = await client.get(
                EFTS_SEARCH_URL, params=params, headers=headers
            )
    except Exception as exc:  # noqa: BLE001 - one domain error for the boundary
        raise HoldingsUnavailable(
            f"EFTS search failed: {type(exc).__name__}: {exc}"
        ) from exc
    if response.status_code != 200:
        raise HoldingsUnavailable(
            f"HTTPStatusError: HTTP {response.status_code} on {EFTS_SEARCH_URL}"
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise HoldingsUnavailable(
            f"EFTS search did not answer JSON: {type(exc).__name__}: {exc}"
        ) from exc

    # EDGAR's search can answer 200 with an error document when its OpenSearch
    # backend is unhealthy.  That is an outage, not "this fund never filed":
    # turning it into zero hits would publish holdings_not_found for a fund
    # that files every quarter.  Only a real hits array may reach the filter.
    if isinstance(payload, Mapping) and (
        payload.get("errorType") or payload.get("errorMessage")
    ):
        raise HoldingsUnavailable(
            "EFTS search failed: "
            f"{payload.get('errorType')}: {payload.get('errorMessage')}"
        )
    if not isinstance(payload, Mapping) or not isinstance(payload.get("hits"), Mapping):
        raise HoldingsUnavailable("EFTS search answered without a hits object")

    filings = _series_filings(payload, cik=key)
    if not filings:
        return None
    filings.sort(key=lambda filing: (filing.filed, filing.accession), reverse=True)
    return filings[0]


def _series_for(symbol: str, cache_dir: Path | None) -> FundSeries | None:
    """The fund-map row for ``symbol``, or ``None`` to take the fallback path.

    The map is consulted only when there is a cache directory to read it from;
    ``cache_dir=None`` keeps the startup behaviour (one request per document,
    no disk at all). An absent or unreadable map yields ``{}``, which is the
    same answer as "symbol not listed" — both mean the CIK-wide submissions
    path, never a guessed series.
    """

    if cache_dir is None:
        return None
    ticker = symbol.strip().upper() if isinstance(symbol, str) else ""
    if not ticker:
        return None
    return load_fund_series(fund_series_path(cache_dir)).get(ticker)


async def _read_or_fetch_body(
    url: str, path: Path | None, *, client: Any
) -> tuple[str, bool]:
    """The body from disk when the cache is warm, else exactly one request.

    The boolean says whether a request paid for it, so a warm entry is never
    rewritten and a body that fails to parse is never cached at all.
    """

    cached = _read_cache(path)
    if cached is not None:
        return cached, False
    return await _fetch_body(url, client=client), True


def _document(
    symbol: str,
    holdings: Sequence[EtfHolding],
    *,
    as_of: str | None,
    filed: str,
    accession: str,
    limit: int,
) -> dict[str, object]:
    """Project the portfolio into the frozen seven-key document shape.

    ``limit`` bounds the page, not the analysis: the sort runs over the whole
    portfolio so the page really is the largest positions, and ``count`` is the
    number of rows returned.
    """

    ordered = sorted(holdings, key=_sort_key, reverse=True)
    limit_value = limit if isinstance(limit, int) and limit > 0 else 0
    page = ordered[:limit_value]
    return {
        "symbol": symbol,
        "as_of": as_of,
        "filed": filed,
        "accession": accession,
        "count": len(page),
        "holdings": [holding.document() for holding in page],
        "provider_errors": [],
    }


async def _collect_series_holdings(
    symbol: str,
    series: FundSeries,
    *,
    client: Any,
    cache_dir: Path | None,
    limit: int,
) -> dict[str, object]:
    """Collect one series' portfolio: EFTS locates it, the XML proves it.

    The submissions index is deliberately not read here. It is keyed by CIK, so
    its newest NPORT-P may belong to a sibling series — the exact bug this path
    exists to prevent. EFTS is the index lookup (it names the accession, the
    document and the dates); the downloaded XML is the evidence, and its
    ``seriesId`` must agree with the map before a single row is published.

    The EFTS dates win over the body's own ``repPdDate``: the index is the
    filing's metadata, and a body whose period disagrees is a mis-filed
    document, not a second opinion.
    """

    filing = await find_series_filing(series.series_id, series.cik, client=client)
    if filing is None:
        raise HoldingsNotFound(
            f"series {series.series_id} (CIK{series.cik}) has no NPORT-P filing "
            "in EDGAR full-text search"
        )

    url = nport_archive_url(series.cik, filing.accession, filing.document)
    path = (
        nport_cache_path(cache_dir, series.cik, filing.accession)
        if cache_dir is not None
        else None
    )
    text, fetched = await _read_or_fetch_body(url, path, client=client)

    holdings = parse_nport(text)
    if not holdings:
        raise HoldingsUnavailable(
            f"ParseError: {url} carried no invstOrSec rows"
        )
    filed_series = _xml_text(text, "seriesId")
    if filed_series != series.series_id:
        # Full-text search is an index, not a promise: stop rather than publish
        # another ETF's portfolio under this ticker.
        raise HoldingsUnavailable(
            "series mismatch: expected "
            f"{series.series_id}, got {filed_series or 'no seriesId'} ({url})"
        )
    if fetched and path is not None:
        _write_cache(path, text)

    return _document(
        symbol,
        holdings,
        as_of=filing.report_date
        or _xml_text(text, "repPdDate")
        or filing.filed,
        filed=filing.filed,
        accession=filing.accession,
        limit=limit,
    )


async def collect_holdings(
    symbol: str,
    cik: str,
    *,
    client: Any = None,
    cache_dir: Path | None = None,
    limit: int = 50,
) -> dict[str, object]:
    """Read the latest N-PORT-P portfolio for one fund.

    Two paths, because one CIK is not one fund:

    * **Series path** — when ``cache_dir`` holds SEC's fund ticker map and the
      symbol is in it, the series id is authoritative. EDGAR full-text search
      locates the newest NPORT-P that carries that series id, and the XML must
      confirm the same ``seriesId`` before anything is published. The
      submissions index is never read on this path: it is keyed by CIK, and a
      trust's newest filing may belong to another of its ETFs.
    * **Fallback path** — no map, or a symbol absent from it (SPY is a UIT, and
      UITs are not in the mutual-fund map). The issuer's submissions document
      is the index: it lists every filing with its form, dates and primary
      document, so one request replaces a crawl of the registrant's history.
      Only the newest ``NPORT-P`` is read — an older quarter is a different
      portfolio.

    On both paths the body is read from ``cache_dir`` first and fetched once on
    a miss; the cache is what makes a repeated call free. A body is cached
    **after** it parses to at least one row (and, on the series path, after its
    series id matches), so an error page or a sibling fund's document can never
    become evidence the next call trusts.

    ``limit`` bounds the page, not the analysis: the sort runs over the whole
    portfolio so the page really is the largest positions, and ``count`` is the
    number of rows returned.

    Raises :class:`HoldingsNotFound` when neither index has an NPORT-P row and
    :class:`HoldingsUnavailable` for every fetch, parse or series-mismatch
    failure. No partial or invented rows are ever returned.
    """

    key = normalize_cik(cik)
    if key is None:
        raise HoldingsUnavailable(f"ValueError: not a SEC CIK: {cik!r}")

    series = _series_for(symbol, cache_dir)
    if series is not None:
        return await _collect_series_holdings(
            symbol, series, client=client, cache_dir=cache_dir, limit=limit
        )

    try:
        submissions = await fetch_submissions(
            key, client=client, cache_dir=cache_dir
        )
    except Exception as exc:  # noqa: BLE001 - an index failure is one domain error
        raise HoldingsUnavailable(f"{type(exc).__name__}: {exc}") from exc

    filing = _newest_nport_filing(submissions)
    if filing is None:
        raise HoldingsNotFound(
            f"CIK{key} has no NPORT-P filing in its recent submissions"
        )

    url = nport_archive_url(key, filing.accession, filing.document)
    path = (
        nport_cache_path(cache_dir, key, filing.accession)
        if cache_dir is not None
        else None
    )
    text, fetched = await _read_or_fetch_body(url, path, client=client)

    holdings = parse_nport(text)
    if not holdings:
        raise HoldingsUnavailable(
            f"ParseError: {url} carried no invstOrSec rows"
        )
    if fetched and path is not None:
        _write_cache(path, text)

    return _document(
        symbol,
        holdings,
        as_of=filing.report_date,
        filed=filing.filed,
        accession=filing.accession,
        limit=limit,
    )


__all__ = [
    "EFTS_PAGE_SIZE",
    "EFTS_SEARCH_URL",
    "EtfHolding",
    "HoldingsNotFound",
    "HoldingsUnavailable",
    "NPORT_ARCHIVE_URL",
    "NPORT_FORM",
    "SeriesFiling",
    "collect_holdings",
    "find_series_filing",
    "nport_archive_url",
    "nport_cache_path",
    "parse_nport",
]
