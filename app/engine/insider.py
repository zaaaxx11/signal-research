"""SEC Form 4 insider transactions: the filing's own words, one row each.

Form 4 is the document an insider files after buying, selling or exercising a
security. It is public, small (a few KB of XML) and already carries the fields a
reader needs: who, what security, which transaction code, how many shares, at
what price and what they held afterwards.

This slice reads only the non-derivative table. Derivative transactions
(options, warrants) have a different shape and a different meaning; pretending
they are the same row would publish an exercise as a purchase. That omission is
a documented gap, not a silent zero.

Three rules decide every judgement here:

* **Unknown is ``None``, never zero.** EDGAR omits a price on a gift and leaves
  ``<value>`` blank on some rows. A zero would claim the shares changed hands for
  nothing, so a blank or unparseable amount becomes ``None`` while a reported
  ``0`` stays ``0.0``.
* **An incomplete row is dropped, not completed.** Without a security title, a
  transaction date and a transaction code there is no transaction to publish;
  inventing one field to keep the row would manufacture a trade.
* **One bad document never hides the others.** The collector reports a failed
  body in ``provider_errors`` and returns the rest, so a single 500 is visible
  without turning the filing history into an outage.

The parser is pure: no network, no clock, no cache. The collector is the only
part that touches EDGAR, and it takes its transport by injection so the whole
slice is testable without a socket.

Contract: ``briefs/market-engine/R5A-CONTRACT.md``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import os
from pathlib import Path
import tempfile
from typing import Any
import xml.etree.ElementTree as ET

from app.engine.ingest import DEFAULT_USER_AGENT, fetch_submissions
from app.engine.sources.sec import normalize_cik
from app.providers import http as provider_http

#: EDGAR's archive path for one filing body. ``cik`` is the unpadded integer
#: spelling and ``accession`` carries no dashes — that is the published shape.
INSIDER_ARCHIVE_URL = (
    "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{document}"
)

#: The document root a Form 4 body must carry. Anything else is a different
#: document that happened to answer 200.
_FORM4_ROOT = "ownershipDocument"

#: Role labels in the contract's join order, with the relationship flag that
#: turns each one on.
_ROLE_FLAGS: tuple[tuple[str, str], ...] = (
    ("isDirector", "Director"),
    ("isOfficer", "Officer"),
    ("isTenPercentOwner", "10% Owner"),
    ("isOther", "Other"),
)


class InsiderFetchError(RuntimeError):
    """A Form 4 body could not be read; the message names the reason."""


@dataclass(frozen=True, slots=True)
class InsiderTransaction:
    """One non-derivative Form 4 transaction, with its filing context."""

    accession: str
    filed: str
    period_of_report: str
    owner_name: str
    owner_cik: str | None
    role: str | None
    security: str
    transaction_date: str
    code: str
    shares: float | None
    price: float | None
    acquired_disposed: str | None
    shares_after: float | None
    url: str

    def document(self) -> dict[str, object]:
        """Project the transaction into its JSON shape."""

        return {
            "accession": self.accession,
            "filed": self.filed,
            "period_of_report": self.period_of_report,
            "owner_name": self.owner_name,
            "owner_cik": self.owner_cik,
            "role": self.role,
            "security": self.security,
            "transaction_date": self.transaction_date,
            "code": self.code,
            "shares": self.shares,
            "price": self.price,
            "acquired_disposed": self.acquired_disposed,
            "shares_after": self.shares_after,
            "url": self.url,
        }


@dataclass(frozen=True, slots=True)
class _Form4Filing:
    """One ``filings.recent`` row this collector is willing to fetch."""

    accession: str
    filed: str
    document: str


# ---------------------------------------------------------------------------
# XML traversal
# ---------------------------------------------------------------------------


def _local(tag: object) -> str:
    """The element's name without an XML namespace, or ``""``.

    SEC serves Form 4 without a namespace today, but a namespaced copy is still
    the same document; stripping the ``{uri}`` prefix keeps the walk from
    silently finding nothing.
    """

    text = tag if isinstance(tag, str) else ""
    return text.rsplit("}", 1)[-1]


def _child(element: ET.Element | None, name: str) -> ET.Element | None:
    """The first direct child called ``name``, or ``None``."""

    if element is None:
        return None
    for candidate in element:
        if _local(candidate.tag) == name:
            return candidate
    return None


def _descend(element: ET.Element | None, *names: str) -> ET.Element | None:
    """Follow a chain of direct children; ``None`` when any step is absent."""

    current = element
    for name in names:
        current = _child(current, name)
        if current is None:
            return None
    return current


def _text(element: ET.Element | None) -> str | None:
    """The element's stripped text, or ``None`` when it is absent or blank."""

    if element is None or element.text is None:
        return None
    text = element.text.strip()
    return text or None


def _value(element: ET.Element | None, *names: str) -> str | None:
    """The text of ``<value>`` below ``names``; ``None`` when absent or blank."""

    return _text(_descend(element, *names, "value"))


def _number(raw: str | None) -> float | None:
    """A reported amount, or ``None``.

    ``float`` is deliberately strict: ``"N/A"`` and ``"1,234"`` are text EDGAR
    sometimes prints where a number belongs, and turning either into a value
    would publish an amount nobody reported. ``"0"`` is a number and stays one.
    """

    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _flag(element: ET.Element | None, name: str) -> bool:
    """Whether a relationship flag element says ``true``."""

    text = _text(_child(element, name))
    return text is not None and text.casefold() in {"true", "1"}


def _role(element: ET.Element | None) -> str | None:
    """The contract's role label for one ``reportingOwnerRelationship``.

    Several flags may be true at once (a director who is also an officer and a
    10% owner), so the labels join in a fixed order instead of the first one
    winning. An officer with no title keeps the bare ``Officer`` label rather
    than inventing a title.
    """

    labels: list[str] = []
    for flag, label in _ROLE_FLAGS:
        if not _flag(element, flag):
            continue
        if flag == "isOfficer":
            title = _text(_child(element, "officerTitle"))
            labels.append(f"{label}: {title}" if title else label)
        else:
            labels.append(label)
    return "; ".join(labels) if labels else None


# ---------------------------------------------------------------------------
# §1 Parser
# ---------------------------------------------------------------------------


def parse_form4(
    xml_text: str, *, accession: str, filed: str, url: str
) -> tuple[InsiderTransaction, ...]:
    """Read every non-derivative transaction in one Form 4 body.

    ``accession``, ``filed`` and ``url`` are the filing's identity, not the
    document's content; they are echoed into every row so a transaction can be
    traced back to the exact body it was read from. The document itself carries
    no accession number, which is why the caller supplies it.

    A body that is not XML, or whose root is not ``ownershipDocument``, answers
    ``()``: an HTML error page served with a 200 is not a Form 4, and a caller
    that has to catch a parser's exception dialect would end up treating it as
    one. Rows are returned in document order — the order the filer wrote.
    """

    try:
        root = ET.fromstring(xml_text)
    except (ET.ParseError, ValueError):
        return ()
    if _local(root.tag) != _FORM4_ROOT:
        return ()

    owner = _child(root, "reportingOwner")
    owner_id = _child(owner, "reportingOwnerId")
    period_of_report = _text(_child(root, "periodOfReport")) or ""
    owner_name = _text(_child(owner_id, "rptOwnerName")) or ""
    owner_cik = _text(_child(owner_id, "rptOwnerCik"))
    role = _role(_child(owner, "reportingOwnerRelationship"))

    table = _child(root, "nonDerivativeTable")
    if table is None:
        return ()

    transactions: list[InsiderTransaction] = []
    for row in table:
        if _local(row.tag) != "nonDerivativeTransaction":
            continue
        security = _value(row, "securityTitle")
        transaction_date = _value(row, "transactionDate")
        code = _text(_descend(row, "transactionCoding", "transactionCode"))
        if security is None or transaction_date is None or code is None:
            # Incomplete is not zero-filled: without what, when and which code
            # there is no transaction to report.
            continue
        transactions.append(
            InsiderTransaction(
                accession=accession,
                filed=filed,
                period_of_report=period_of_report,
                owner_name=owner_name,
                owner_cik=owner_cik,
                role=role,
                security=security,
                transaction_date=transaction_date,
                code=code,
                shares=_number(_value(row, "transactionAmounts", "transactionShares")),
                price=_number(
                    _value(row, "transactionAmounts", "transactionPricePerShare")
                ),
                acquired_disposed=_value(
                    row, "transactionAmounts", "transactionAcquiredDisposedCode"
                ),
                shares_after=_number(
                    _value(
                        row,
                        "postTransactionAmounts",
                        "sharesOwnedFollowingTransaction",
                    )
                ),
                url=url,
            )
        )
    return tuple(transactions)


# ---------------------------------------------------------------------------
# §2 Collector
# ---------------------------------------------------------------------------


def form4_archive_url(cik: str, accession: str, primary_document: str) -> str:
    """The archive URL for one filing body.

    ``primaryDocument`` is served with a viewer prefix (``xslF345X06/form4.xml``)
    that points at an HTML rendering, not the XML; only the basename is the
    document the parser can read.
    """

    key = normalize_cik(cik)
    if key is None:
        raise ValueError(f"not a SEC CIK: {cik!r}")
    document = primary_document.rsplit("/", 1)[-1].strip()
    return INSIDER_ARCHIVE_URL.format(
        cik=int(key),
        accession=accession.replace("-", ""),
        document=document,
    )


def form4_cache_path(cache_dir: Path, cik: str, accession: str) -> Path:
    """``<cache_dir>/CIK##########-form4-<accession_nodash>.xml``.

    The CIK keys the issuer and the accession keys the filing, so two filings
    filed the same day cannot overwrite each other. The suffix keeps the entry
    self-describing beside the submissions and companyfacts caches.
    """

    key = normalize_cik(cik)
    if key is None:
        raise ValueError(f"not a SEC CIK: {cik!r}")
    return (
        Path(cache_dir)
        / f"CIK{key}-form4-{accession.replace('-', '')}.xml"
    )


def _recent_form4_filings(payload: object, *, limit: int) -> tuple[_Form4Filing, ...]:
    """The newest ``form == "4"`` rows of ``filings.recent``, at most ``limit``.

    ``recent`` is column-oriented: four parallel arrays, not four keys per row.
    A row is usable only when the accession, the filing date and the primary
    document are all text, because each of them is part of the request URL.
    Selection is by filing date descending and stable, so two filings of one day
    keep EDGAR's order.
    """

    if not isinstance(payload, Mapping):
        return ()
    filings = payload.get("filings")
    if not isinstance(filings, Mapping):
        return ()
    recent = filings.get("recent")
    if not isinstance(recent, Mapping):
        return ()

    def column(name: str) -> list[Any]:
        values = recent.get(name)
        if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
            return []
        return list(values)

    rows: list[_Form4Filing] = []
    for accession, filed, form, document in zip(
        column("accessionNumber"),
        column("filingDate"),
        column("form"),
        column("primaryDocument"),
        strict=False,
    ):
        if form != "4":
            continue
        if not all(isinstance(value, str) and value.strip() for value in (
            accession,
            filed,
            document,
        )):
            continue
        rows.append(
            _Form4Filing(
                accession=accession.strip(),
                filed=filed.strip(),
                document=document.strip(),
            )
        )
    rows.sort(key=lambda row: row.filed, reverse=True)
    return tuple(rows[: max(0, limit)])


def _headers() -> dict[str, str]:
    """Per-request headers; sent on the request so an injected client keeps them.

    EDGAR answers ``403`` to an anonymous client, so the User-Agent always
    carries the contact address from :data:`~app.engine.ingest.DEFAULT_USER_AGENT`
    — even when the caller supplied its own transport.
    """

    return {"user-agent": DEFAULT_USER_AGENT, "accept": "application/xml"}


async def _fetch_body(url: str, *, client: Any) -> str:
    """GET one Form 4 body; every transport answer becomes one error type.

    ``client=None`` builds the provider client **as a module attribute** so the
    suite can install a ``MockTransport`` at that seam. A non-200 answer is a
    failure, not a body: ``follow_redirects=False`` means a 3xx is a refusal and
    an error page would otherwise parse to ``()`` and look like an empty filing.
    """

    headers = _headers()
    try:
        if client is None:
            async with provider_http.build_client(headers=headers) as owned:
                response = await owned.get(url, headers=headers)
        else:
            response = await client.get(url, headers=headers)
    except Exception as exc:  # noqa: BLE001 - one domain error for the boundary
        raise InsiderFetchError(f"{type(exc).__name__}: {exc}") from exc
    if response.status_code != 200:
        raise InsiderFetchError(f"HTTP {response.status_code}")
    return response.text


def _read_cache(path: Path | None) -> str | None:
    """The cached body, or ``None`` when there is nothing usable on disk.

    A blank file is a miss rather than an empty filing: an interrupted write
    leaves a zero-byte entry, and serving it would report a real filing as
    having no transactions.
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


async def collect_insider(
    symbol: str,
    cik: str,
    *,
    client: Any = None,
    cache_dir: Path | None = None,
    limit: int = 5,
) -> tuple[tuple[InsiderTransaction, ...], tuple[str, ...]]:
    """Read the newest Form 4 filings for one issuer.

    The issuer's submissions document is the index: it lists every filing with
    its form and primary document, which is why one request replaces a crawl of
    the issuer's filing history. ``limit`` bounds the number of *filings*, not
    transactions — a filing may report several rows, and cutting them would
    publish a partial trade.

    A body is read from ``cache_dir`` first and fetched once on a miss; the
    cache is what makes a repeated call free. A filing that fails (transport,
    non-200, unparseable) appends one short message and the other filings still
    return: an outage of one document is reported, never papered over with a
    synthesized row.

    Returns ``(transactions, errors)`` sorted by ``(filed, transaction_date)``
    descending, so the newest filing and the newest trade inside it come first.
    """

    key = normalize_cik(cik)
    if key is None:
        return (), (f"{symbol}: not a SEC CIK: {cik!r}",)

    try:
        submissions = await fetch_submissions(
            key, client=client, cache_dir=cache_dir
        )
    except Exception as exc:  # noqa: BLE001 - an index failure is one message
        return (), (f"CIK{key}: {type(exc).__name__}: {exc}",)

    transactions: list[InsiderTransaction] = []
    errors: list[str] = []
    for filing in _recent_form4_filings(submissions, limit=limit):
        url = form4_archive_url(key, filing.accession, filing.document)
        path = (
            form4_cache_path(cache_dir, key, filing.accession)
            if cache_dir is not None
            else None
        )
        text = _read_cache(path)
        if text is None:
            try:
                text = await _fetch_body(url, client=client)
            except InsiderFetchError as exc:
                errors.append(f"{filing.accession}: {exc}")
                continue
            if path is not None:
                _write_cache(path, text)

        rows = parse_form4(
            text, accession=filing.accession, filed=filing.filed, url=url
        )
        if not rows:
            errors.append(f"{filing.accession}: no non-derivative transaction")
            continue
        transactions.extend(rows)

    transactions.sort(
        key=lambda item: (item.filed, item.transaction_date), reverse=True
    )
    return tuple(transactions), tuple(errors)


__all__ = [
    "INSIDER_ARCHIVE_URL",
    "InsiderFetchError",
    "InsiderTransaction",
    "collect_insider",
    "form4_archive_url",
    "form4_cache_path",
    "parse_form4",
]
