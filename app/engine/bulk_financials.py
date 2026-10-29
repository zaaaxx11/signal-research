"""SEC quarterly financial statement data sets: one ZIP per quarter, streamed.

``companyfacts`` is one request per issuer: a 10k-symbol universe is ten
thousand requests and tens of gigabytes. EDGAR also publishes the **financial
statement data sets** — one ZIP per quarter holding every filer's statements as
TSV (``sub.txt`` for the filings, ``num.txt`` for the numbers, ``pre.txt`` for
the presentation map). One download covers the whole market, which is why this
module exists: fetch the quarter once, cache it, and index the facts by CIK.

What the value is
-----------------
* **Streamed, never resident.** ``num.txt`` is ~875 MB uncompressed
  (~3.9 M rows). Both parsers are generators over ``zipfile`` + ``csv``: one
  row is materialised at a time, and :meth:`FactIndex.build` keeps a row only
  when its CIK was asked for. The full-file mode (``ciks=None``) exists for
  fixtures and small extracts, not for production.
* **Header-driven columns.** The 2026q3 layout is not the 2019 readme's
  (``segments`` replaced ``dimh``/``iprx``); every column is resolved by name
  from the header row, so a reordered or extended file still parses.
* **Absent, never zero.** A blank, unparseable or non-finite ``value`` is a
  skipped row. A blank ``uom`` is kept as the empty string — rejecting a unit
  is the mapper's job (``app.market.sec_fundamentals``), not the parser's.
* **Dates are ISO.** ``ddate``/``period``/``filed`` arrive as ``YYYYMMDD`` and
  leave as ``YYYY-MM-DD``, because a cell's ``as_of`` is an ISO date.
* **The CIK is ten digits.** ``320193`` becomes ``0000320193``, the spelling
  the engine snapshot and the per-CIK caches use.

Which fact wins
---------------
This module does not choose between facts; it keeps every row and joins the
filing date from ``sub.txt`` onto each fact, so the mapper can apply the
engine's documented order (newest period end, then latest ``filed``, then
accession). A restated comparative therefore arrives with the metadata that
identifies it as the restatement.

One download per quarter
------------------------
:func:`download_dataset` writes the ZIP atomically into
``<cache>/sec-bulk/<quarter>.zip`` and answers a warm cache with **zero**
requests. The User-Agent carries contact information because EDGAR fair-access
asks for it, and the shared SEC rate limiter gates the request like every
other EDGAR call. :func:`save_snapshot` writes the indexed facts as a monthly
JSON document so a per-symbol lookup never re-reads the ZIP.

``pre.txt`` is deliberately not parsed: it maps an accession to its statement
and report layout, which only matters for statement *assembly*, not for fact
lookup. Contract: ``briefs/market-engine`` keyless plan, phase 3.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import io
import json
import math
import os
import re
import sys
import zipfile
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path
from typing import Any

from app.engine.paths import cache_dir as default_cache_dir
from app.engine.ratelimit import sec_limiter
from app.providers import http as provider_http

#: ``https://www.sec.gov/files/dera/data/financial-statement-data-sets/2026q3.zip``
DATASET_URL_TEMPLATE = (
    "https://www.sec.gov/files/dera/data/financial-statement-data-sets/{quarter}.zip"
)

#: EDGAR fair access asks automated clients to identify themselves with a
#: contact address; an anonymous UA is the fastest way to get throttled.
SEC_USER_AGENT = "Ztockz/0.1 (bulk-financials; contact: zaaaxx1432@gmail.com)"

#: The two members this module parses. ``pre.txt`` (the presentation map) is
#: not needed to look a fact up — see the module docstring.
SUB_FILENAME = "sub.txt"
NUM_FILENAME = "num.txt"
PRE_FILENAME = "pre.txt"

#: Cache subdirectory under ``MEE_ENGINE_CACHE_DIR``.
CACHE_SUBDIR = "sec-bulk"

#: ``2026q3`` — the only spelling EDGAR publishes.
_QUARTER_RE = re.compile(r"^(\d{4})q([1-4])$")

#: The first financial statement data set EDGAR published.
_FIRST_DATASET_YEAR = 2009


class BulkDownloadError(RuntimeError):
    """The quarterly dataset could not be downloaded or verified."""


# ---------------------------------------------------------------------------
# Quarters, URLs, cache locations
# ---------------------------------------------------------------------------


def parse_quarter(text: object) -> tuple[int, int]:
    """``"2026q3"`` → ``(2026, 3)``. Anything else is a ``ValueError``.

    The spelling is strict on purpose: a silently normalised ``"2026-Q3"``
    would build a URL nobody can check against EDGAR's directory.
    """

    if not isinstance(text, str):
        raise ValueError(f"quarter must be a string like '2026q3', got {text!r}")
    match = _QUARTER_RE.match(text.strip().lower())
    if match is None:
        raise ValueError(f"quarter must look like '2026q3', got {text!r}")
    return int(match.group(1)), int(match.group(2))


def quarter_label(year: int, quarter: int) -> str:
    """``(2026, 3)`` → ``"2026q3"``, validating both parts."""

    if isinstance(year, bool) or not isinstance(year, int):
        raise ValueError(f"year must be an int, got {year!r}")
    if isinstance(quarter, bool) or not isinstance(quarter, int):
        raise ValueError(f"quarter must be an int, got {quarter!r}")
    if not 1 <= quarter <= 4:
        raise ValueError(f"quarter must be 1-4, got {quarter}")
    if not _FIRST_DATASET_YEAR <= year <= 9999:
        raise ValueError(f"year must be {_FIRST_DATASET_YEAR}-9999, got {year}")
    return f"{year}q{quarter}"


def current_quarter(when: date | None = None) -> str:
    """The quarter containing ``when`` (default: today), as a dataset label."""

    if when is None:
        when = date.today()
    if not isinstance(when, date):
        raise ValueError(f"when must be a date, got {when!r}")
    return quarter_label(when.year, (when.month - 1) // 3 + 1)


def dataset_url(quarter: str) -> str:
    """The EDGAR URL for one quarter's dataset."""

    year, number = parse_quarter(quarter)
    return DATASET_URL_TEMPLATE.format(quarter=f"{year}q{number}")


def dataset_cache_path(cache_dir: Path | str | None, quarter: str) -> Path:
    """``<cache>/sec-bulk/<quarter>.zip``; ``cache_dir=None`` uses the engine cache."""

    year, number = parse_quarter(quarter)
    root = Path(cache_dir) if cache_dir is not None else default_cache_dir()
    return root / CACHE_SUBDIR / f"{year}q{number}.zip"


def snapshot_path(cache_dir: Path | str | None, quarter: str) -> Path:
    """``<cache>/sec-bulk/<quarter>-facts.json`` — the monthly indexed document."""

    year, number = parse_quarter(quarter)
    root = Path(cache_dir) if cache_dir is not None else default_cache_dir()
    return root / CACHE_SUBDIR / f"{year}q{number}-facts.json"


def normalize_cik(value: object) -> str:
    """``320193``/``"CIK0000320193"`` → ``"0000320193"``; anything else raises.

    The ten-digit zero-padded spelling is the engine's CIK key: the snapshot,
    the per-CIK ``companyfacts`` cache and this index all use it, so a lookup
    never has to guess which spelling a caller brought.
    """

    if isinstance(value, bool):
        raise ValueError(f"not a CIK: {value!r}")
    if isinstance(value, int):
        text = str(value)
    elif isinstance(value, str):
        text = value.strip()
        if text[:3].upper() == "CIK":
            text = text[3:].strip()
    else:
        raise ValueError(f"not a CIK: {value!r}")
    if not text or not text.isdigit():
        raise ValueError(f"not a CIK: {value!r}")
    if len(text) > 10:
        raise ValueError(f"CIK is at most ten digits: {value!r}")
    return text.zfill(10)


# ---------------------------------------------------------------------------
# Row-level parsing
# ---------------------------------------------------------------------------


def _iso_date(text: str) -> str:
    """``"20260630"`` → ``"2026-06-30"``; already-ISO or blank text passes through."""

    raw = (text or "").strip()
    if len(raw) == 8 and raw.isdigit():
        year, month, day = int(raw[:4]), int(raw[4:6]), int(raw[6:])
        if year and 1 <= month <= 12 and 1 <= day <= 31:
            return f"{year:04d}-{month:02d}-{day:02d}"
    return raw


def _to_float(text: str) -> float | None:
    """``"1,234.5"`` → ``1234.5``; blank, unparseable or non-finite → ``None``.

    SEC quotes values with four decimals and no separators, but the dataset
    has carried comma-grouped numbers in older quarters, and a blank cell is
    common. A row that does not parse is skipped — never coerced to ``0.0``.
    """

    raw = (text or "").strip().replace(",", "")
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def _to_int(text: str) -> int | None:
    """``"4"`` → ``4``; blank or non-integer → ``None`` (``qtrs`` may be absent)."""

    raw = (text or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _stream_rows(zip_path: Path | str, member: str) -> Iterator[dict[str, str]]:
    """Yield one ``{column: value}`` dict per data row of a TSV inside the ZIP.

    The header row names the columns, so the 2026q3 layout, the 2019 layout
    and a reordered file all parse the same way. A row whose width does not
    match the header is not a row of this dataset and is skipped. The archive
    stays open for the life of the generator; nothing is read into memory
    beyond one line.
    """

    with zipfile.ZipFile(zip_path) as archive:
        try:
            handle = archive.open(member)
        except KeyError:
            return
        with handle, io.TextIOWrapper(handle, encoding="utf-8", newline="") as text:
            # QUOTE_NONE: these are tab-separated values, not CSV — a quote in
            # a company name is a character, not a delimiter.
            reader = csv.reader(text, delimiter="\t", quoting=csv.QUOTE_NONE)
            try:
                header = next(reader)
            except StopIteration:
                return
            columns = {name.strip(): position for position, name in enumerate(header)}
            width = len(header)
            for row in reader:
                if len(row) != width:
                    continue
                yield {name: row[position] for name, position in columns.items()}


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Submission:
    """One row of ``sub.txt``: the filing (accession) side of the dataset.

    ``period`` and ``filed`` are ISO dates; ``fy``/``fp`` are the filer's own
    fiscal labels, kept verbatim because a fiscal calendar is the issuer's
    business, not the parser's.
    """

    adsh: str
    cik: str
    name: str
    form: str
    period: str
    fy: int | None
    fp: str
    filed: str
    sic: str


@dataclass(frozen=True, slots=True)
class Fact:
    """One row of ``num.txt``: one tagged number inside one accession.

    ``cik`` and ``filed`` are **joined** from ``sub.txt`` by
    :meth:`FactIndex.build`; a row from the raw :func:`iter_facts` stream
    carries them empty, because ``num.txt`` does not name the filer. ``qtrs``
    is the duration in quarters (``0`` instant, ``1`` quarter, ``4`` annual);
    ``segments`` is non-empty for dimensional (segment/axis) data, which is
    not the consolidated fact.
    """

    adsh: str
    tag: str
    version: str
    ddate: str
    qtrs: int | None
    uom: str
    value: float
    cik: str = ""
    segments: str = ""
    coreg: str = ""
    filed: str = ""


def _fact_sort_key(fact: Fact) -> tuple[str, str, int, str, str, float]:
    """Deterministic order: tag, period, duration, unit, accession, value."""

    return (
        fact.tag,
        fact.ddate,
        fact.qtrs if fact.qtrs is not None else -1,
        fact.uom,
        fact.adsh,
        fact.value,
    )


# ---------------------------------------------------------------------------
# The index
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FactIndex:
    """Facts indexed by CIK, built by streaming the quarterly ZIP.

    ``submissions`` maps accession → :class:`Submission`; ``by_cik`` maps the
    ten-digit CIK → that issuer's facts in :func:`_fact_sort_key` order. The
    index is a frozen value: two builds of one ZIP compare equal, and the
    snapshot round-trip is exact.
    """

    submissions: Mapping[str, Submission] = field(default_factory=dict)
    by_cik: Mapping[str, tuple[Fact, ...]] = field(default_factory=dict)

    @classmethod
    def build(cls, zip_path: Path | str, *, ciks: Iterable[str] | None = None) -> FactIndex:
        """Stream ``sub.txt`` then ``num.txt`` into a CIK-keyed index.

        Two passes, because the join runs that way: ``sub.txt`` is small
        (~1.9 MB) and gives accession → CIK plus the filing date, and
        ``num.txt`` is then streamed once with a row kept only when its
        accession belongs to a wanted CIK. ``ciks=None`` keeps every filer —
        the fixture and small-extract mode, not the 30k-filer production
        mode.
        """

        wanted = None if ciks is None else {normalize_cik(cik) for cik in ciks}

        submissions: dict[str, Submission] = {}
        accession_cik: dict[str, str] = {}
        for submission in iter_submissions(zip_path):
            if wanted is not None and submission.cik not in wanted:
                continue
            submissions[submission.adsh] = submission
            accession_cik[submission.adsh] = submission.cik

        buckets: dict[str, list[Fact]] = {}
        for fact in iter_facts(zip_path):
            cik = accession_cik.get(fact.adsh)
            if cik is None:
                continue
            buckets.setdefault(cik, []).append(
                replace(fact, cik=cik, filed=submissions[fact.adsh].filed)
            )

        by_cik = {
            cik: tuple(sorted(facts, key=_fact_sort_key))
            for cik, facts in sorted(buckets.items())
        }
        return cls(submissions=submissions, by_cik=by_cik)

    def facts_for(self, cik: str) -> tuple[Fact, ...]:
        """One issuer's facts; an unknown or malformed CIK is empty, not an error."""

        try:
            key = normalize_cik(cik)
        except ValueError:
            return ()
        return self.by_cik.get(key, ())

    def merge(self, other: "FactIndex") -> "FactIndex":
        """Union two quarterly indexes into one fact list per CIK.

        The TTM arithmetic in ``app.market.sec_fundamentals`` needs the annual
        row from the quarter that carried the 10-K *and* the year-to-date pair
        from the quarter that carried the 10-Q — two different datasets for a
        mid-year filer. Facts are re-sorted by the same
        :func:`_fact_sort_key` a single build uses, so a merged index is
        indistinguishable from one built over both ZIPs at once; submissions
        are unioned with ``other`` winning an accession collision, because the
        later quarter's filing date is the one that matters.
        """

        submissions: dict[str, Submission] = dict(self.submissions)
        submissions.update(other.submissions)

        merged: dict[str, list[Fact]] = {}
        for cik, facts in self.by_cik.items():
            merged.setdefault(cik, []).extend(facts)
        for cik, facts in other.by_cik.items():
            merged.setdefault(cik, []).extend(facts)

        by_cik = {
            cik: tuple(sorted(facts, key=_fact_sort_key))
            for cik, facts in sorted(merged.items())
        }
        return FactIndex(submissions=submissions, by_cik=by_cik)

    def submissions_for(self, cik: str) -> tuple[Submission, ...]:
        """One issuer's filings, in ``sub.txt`` order."""

        try:
            key = normalize_cik(cik)
        except ValueError:
            return ()
        return tuple(
            submission
            for submission in self.submissions.values()
            if submission.cik == key
        )

    def ciks(self) -> tuple[str, ...]:
        """Every indexed CIK, sorted."""

        return tuple(self.by_cik)

    def issuer_name(self, cik: str) -> str:
        """The name from the issuer's most recently filed submission."""

        submissions = self.submissions_for(cik)
        if not submissions:
            return ""
        return max(submissions, key=lambda item: (item.filed, item.adsh)).name

    # -- the monthly snapshot document -------------------------------------

    def to_document(self) -> dict[str, Any]:
        """Project the index into a JSON-serialisable document.

        The shape is ``{"ciks": {cik: {"name", "submissions", "facts"}}}``:
        one entry per issuer, so a per-symbol lookup reads one key instead of
        scanning the ZIP again.
        """

        return {
            "ciks": {
                cik: {
                    "name": self.issuer_name(cik),
                    "submissions": [
                        {
                            "adsh": item.adsh,
                            "cik": item.cik,
                            "name": item.name,
                            "form": item.form,
                            "period": item.period,
                            "fy": item.fy,
                            "fp": item.fp,
                            "filed": item.filed,
                            "sic": item.sic,
                        }
                        for item in self.submissions_for(cik)
                    ],
                    "facts": [
                        {
                            "adsh": item.adsh,
                            "tag": item.tag,
                            "version": item.version,
                            "ddate": item.ddate,
                            "qtrs": item.qtrs,
                            "uom": item.uom,
                            "value": item.value,
                            "segments": item.segments,
                            "coreg": item.coreg,
                            "filed": item.filed,
                        }
                        for item in facts
                    ],
                }
                for cik, facts in self.by_cik.items()
            }
        }

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> FactIndex:
        """Rebuild an index from :meth:`to_document`; junk becomes an empty index.

        A snapshot is a cache, not an authority: a truncated or foreign
        document must degrade to "no facts", never raise inside a lookup.
        """

        if not isinstance(document, Mapping):
            return cls()
        raw = document.get("ciks")
        if not isinstance(raw, Mapping):
            return cls()

        submissions: dict[str, Submission] = {}
        by_cik: dict[str, tuple[Fact, ...]] = {}
        for cik, payload in raw.items():
            try:
                key = normalize_cik(cik)
            except ValueError:
                continue
            if not isinstance(payload, Mapping):
                continue
            for item in payload.get("submissions") or ():
                submission = _submission_from(item)
                if submission is not None:
                    submissions[submission.adsh] = submission
            facts = []
            for item in payload.get("facts") or ():
                fact = _fact_from(item, key)
                if fact is not None:
                    facts.append(fact)
            by_cik[key] = tuple(sorted(facts, key=_fact_sort_key))
        return cls(submissions=submissions, by_cik=by_cik)


def _submission_from(item: object) -> Submission | None:
    if not isinstance(item, Mapping):
        return None
    adsh = str(item.get("adsh") or "").strip()
    try:
        cik = normalize_cik(item.get("cik"))
    except ValueError:
        return None
    if not adsh:
        return None
    return Submission(
        adsh=adsh,
        cik=cik,
        name=str(item.get("name") or ""),
        form=str(item.get("form") or ""),
        period=str(item.get("period") or ""),
        fy=item.get("fy") if isinstance(item.get("fy"), int) else None,
        fp=str(item.get("fp") or ""),
        filed=str(item.get("filed") or ""),
        sic=str(item.get("sic") or ""),
    )


def _fact_from(item: object, cik: str) -> Fact | None:
    if not isinstance(item, Mapping):
        return None
    adsh = str(item.get("adsh") or "").strip()
    tag = str(item.get("tag") or "").strip()
    value = item.get("value")
    if not adsh or not tag or not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    qtrs = item.get("qtrs")
    return Fact(
        adsh=adsh,
        tag=tag,
        version=str(item.get("version") or ""),
        ddate=str(item.get("ddate") or ""),
        qtrs=qtrs if isinstance(qtrs, int) and not isinstance(qtrs, bool) else None,
        uom=str(item.get("uom") or ""),
        value=float(value),
        cik=cik,
        segments=str(item.get("segments") or ""),
        coreg=str(item.get("coreg") or ""),
        filed=str(item.get("filed") or ""),
    )


# ---------------------------------------------------------------------------
# Streaming parsers
# ---------------------------------------------------------------------------


def iter_submissions(zip_path: Path | str) -> Iterator[Submission]:
    """Stream ``sub.txt``: one :class:`Submission` per filing.

    A row without an accession or a CIK is not a filing and is skipped; so is
    a row whose width does not match the header. The CIK is normalised and the
    dates are ISO by the time the caller sees them.
    """

    for row in _stream_rows(zip_path, SUB_FILENAME):
        adsh = row.get("adsh", "").strip()
        if not adsh:
            continue
        try:
            cik = normalize_cik(row.get("cik", ""))
        except ValueError:
            continue
        yield Submission(
            adsh=adsh,
            cik=cik,
            name=row.get("name", "").strip(),
            form=row.get("form", "").strip(),
            period=_iso_date(row.get("period", "")),
            fy=_to_int(row.get("fy", "")),
            fp=row.get("fp", "").strip(),
            filed=_iso_date(row.get("filed", "")),
            sic=row.get("sic", "").strip(),
        )


def iter_facts(zip_path: Path | str) -> Iterator[Fact]:
    """Stream ``num.txt``: one :class:`Fact` per usable row.

    A row without an accession, a tag, a date or a finite value is skipped —
    an absent number is not a zero. ``uom`` is kept verbatim (possibly empty)
    and ``cik``/``filed`` are left empty: the join is :meth:`FactIndex.build`'s
    job, because ``num.txt`` does not name the filer.
    """

    for row in _stream_rows(zip_path, NUM_FILENAME):
        adsh = row.get("adsh", "").strip()
        tag = row.get("tag", "").strip()
        ddate = _iso_date(row.get("ddate", ""))
        value = _to_float(row.get("value", ""))
        if not adsh or not tag or not ddate or value is None:
            continue
        yield Fact(
            adsh=adsh,
            tag=tag,
            version=row.get("version", "").strip(),
            ddate=ddate,
            qtrs=_to_int(row.get("qtrs", "")),
            uom=row.get("uom", "").strip(),
            value=value,
            segments=row.get("segments", "").strip(),
            coreg=row.get("coreg", "").strip(),
        )


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------


def save_snapshot(index: FactIndex, path: Path | str) -> None:
    """Write the index as a JSON document, atomically."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_text(json.dumps(index.to_document()), encoding="utf-8")
    os.replace(temporary, target)


def load_snapshot(path: Path | str) -> FactIndex:
    """Read a snapshot document written by :func:`save_snapshot`."""

    return FactIndex.from_document(
        json.loads(Path(path).read_text(encoding="utf-8"))
    )


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------


async def _fetch_to_cache(client: Any, url: str, path: Path, headers: dict[str, str]) -> Path:
    """Stream ``url`` into ``path`` through a temporary file, then verify it.

    The body is written to ``<path>.tmp`` and renamed only after it is proven
    to be a ZIP holding ``sub.txt`` and ``num.txt``: a truncated or HTML error
    page must never occupy the cache slot a good download would fill.
    """

    limiter = sec_limiter()
    if limiter is not None:
        await limiter.acquire()

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        async with client.stream("GET", url, headers=headers) as response:
            if response.status_code != 200:
                raise BulkDownloadError(
                    f"SEC dataset {url} answered HTTP {response.status_code}"
                )
            with open(temporary, "wb") as handle:
                async for chunk in response.aiter_bytes():
                    handle.write(chunk)
        if not zipfile.is_zipfile(temporary):
            raise BulkDownloadError(f"SEC dataset {url} is not a ZIP archive")
        with zipfile.ZipFile(temporary) as archive:
            members = set(archive.namelist())
        if not {SUB_FILENAME, NUM_FILENAME} <= members:
            raise BulkDownloadError(
                f"SEC dataset {url} is missing {SUB_FILENAME}/{NUM_FILENAME}"
            )
        os.replace(temporary, path)
        return path
    except BulkDownloadError:
        temporary.unlink(missing_ok=True)
        raise
    except Exception as exc:  # noqa: BLE001 - a transport failure is one error type
        temporary.unlink(missing_ok=True)
        raise BulkDownloadError(
            f"SEC dataset {url} could not be downloaded: {exc}"
        ) from exc


async def download_dataset(
    quarter: str,
    *,
    cache_dir: Path | str | None = None,
    client: Any = None,
    url: str | None = None,
) -> Path:
    """Fetch one quarter's dataset ZIP, or answer from the cache.

    A warm cache costs zero requests — the whole point of the quarterly
    cadence. ``client`` is any httpx-style async client (the tests inject a
    mock transport); when it is ``None`` the module builds one through the
    provider seam, so the suite can patch ``provider_http.build_client``.
    """

    parse_quarter(quarter)  # validate before touching the network or the disk
    path = dataset_cache_path(cache_dir, quarter)
    if path.exists():
        return path

    target = url or dataset_url(quarter)
    headers = {"User-Agent": SEC_USER_AGENT, "Accept": "application/zip,*/*"}
    if client is not None:
        return await _fetch_to_cache(client, target, path, headers)
    async with provider_http.build_client() as transport:
        return await _fetch_to_cache(transport, target, path, headers)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.engine.bulk_financials",
        description=(
            "Download one quarter of the SEC financial statement data set into "
            "the engine cache. A warm cache answers with zero requests."
        ),
    )
    parser.add_argument(
        "--quarter",
        default=None,
        help="dataset quarter, e.g. 2026q3 (default: the current quarter)",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=None,
        help="cache root (default: MEE_ENGINE_CACHE_DIR or <data>/cache)",
    )
    parser.add_argument(
        "--url",
        default=None,
        help="override the dataset URL (a mirror, or a local file:// source)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Download the quarter and report the cache entry as JSON on stdout.

    Exit ``1`` with ``{"error", "errorMessage"}`` on stderr when the download
    fails; a bad ``--quarter`` is a usage error and raises, like argparse.
    """

    args = _build_parser().parse_args(argv)
    quarter = args.quarter or current_quarter()
    path = dataset_cache_path(args.cache_dir, quarter)
    cached = path.exists()

    try:
        path = asyncio.run(
            download_dataset(quarter, cache_dir=args.cache_dir, url=args.url)
        )
    except BulkDownloadError as exc:
        print(json.dumps({"error": "download", "errorMessage": str(exc)}), file=sys.stderr)
        return 1

    print(
        json.dumps(
            {
                "quarter": quarter,
                "path": str(path),
                "bytes": path.stat().st_size,
                "cached": cached,
            },
            indent=2,
        )
    )
    return 0


def build_quarters(
    paths: Iterable[Path | str], *, ciks: Iterable[str] | None = None
) -> FactIndex:
    """Build one index over several quarterly ZIPs, merged.

    A mid-year filer's annual row lives in the quarter that carried its 10-K
    while its year-to-date pair lives in the quarter that carried its 10-Q;
    the TTM arithmetic needs both in one fact list. An empty ``paths`` answers
    an empty index rather than raising, so a caller with no cached quarter yet
    still gets a usable object.
    """

    index: FactIndex | None = None
    for path in paths:
        built = FactIndex.build(path, ciks=ciks)
        index = built if index is None else index.merge(built)
    return index if index is not None else FactIndex()


__all__ = [
    "BulkDownloadError",
    "CACHE_SUBDIR",
    "DATASET_URL_TEMPLATE",
    "Fact",
    "FactIndex",
    "NUM_FILENAME",
    "PRE_FILENAME",
    "SEC_USER_AGENT",
    "SUB_FILENAME",
    "Submission",
    "build_quarters",
    "current_quarter",
    "dataset_cache_path",
    "dataset_url",
    "download_dataset",
    "iter_facts",
    "iter_submissions",
    "load_snapshot",
    "main",
    "normalize_cik",
    "parse_quarter",
    "quarter_label",
    "save_snapshot",
    "snapshot_path",
]


if __name__ == "__main__":  # pragma: no cover - the module's own entry point
    raise SystemExit(main())
