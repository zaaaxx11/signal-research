"""Finviz — the free US valuation page, scraped (trust 3).

``https://finviz.com/stock?t=SYMBOL`` answers one HTML page. The valuation block
is a snapshot table of ``label → value`` cells; this adapter reads that table and
maps twelve of its cells onto the canonical columns. ``/quote?t=`` is a ``301``
and this client never follows a redirect, so the stock path is the only one used.

Why the verdict is not ``envelope_verdict``
-------------------------------------------
:func:`app.providers.adapters._support.envelope_verdict` treats *any* HTML body
on a ``200`` as a bot wall. For Finviz, HTML **is** the success response, so this
adapter reads the statuses itself and then looks for the markers Cloudflare
actually serves (``Just a moment…``, ``cf-chl``, ``challenge-platform``). The
distinction matters: a challenge is ``blocked`` (walk on), a page without the
snapshot table is ``schema_drift`` (a shape change), and a table whose every
mapped cell is ``-`` is ``empty`` (the schema is right, the data is absent).

A missing value is no observation
---------------------------------
``-``, ``N/A``, an empty cell, a label that is absent and a cell that is not a
number all produce **zero** rows. A printed ``0.00`` is a real zero and is kept.
Nothing is defaulted, averaged or borrowed from a neighbouring label.

Units follow the store
----------------------
``debt_to_equity`` arrives as a ratio (``0.78``) where the store keeps percent
points (Yahoo publishes ``78.445`` for the same field, and
:mod:`app.fundamentals.scan` multiplies TradingView's ratio by 100 for exactly
this reason), so it is scaled once, here. ``dividend_yield`` is the percentage in
the parentheses of ``Dividend TTM`` (``1.06 (0.30%)`` → ``0.30``; the 1.06 is the
annual dividend per share, not a yield). ``payout_ratio`` and ``roe`` are already
percent on the page.

``P/FCF`` has no canonical column
--------------------------------
It is a valuation multiple the page publishes, so :func:`parse_metrics` parses it
and :data:`EXTRA_METRICS` names it. It is **not** emitted as an observation: an
``Observation.field`` outside the 92-column contract is a contract violation,
not a new column.

The short/ownership cells have their own reader
-----------------------------------------------
``Short Float``, ``Insider Own`` and ``Inst Own`` are not fundamentals cells in
the 92-column contract either, and they are not valuation multiples: they are
percentage points a selection filter reads straight off this page.
:func:`parse_facts` reads exactly those three labels into the
:mod:`app.screen.facts` vocabulary, with the same absent-cell rules as
:func:`parse_metrics` (``-``/``N/A``/no label → **no value**, never zero) and
**no** unit conversion — ``0.83%`` is stored as ``0.83``, because the store,
the filter and Finviz's own panel all mean percent. Labels the product cannot
filter (``Short Ratio``, ``Shs Float``) are deliberately not part of the
vocabulary.
"""

from __future__ import annotations

import math
import re
from datetime import datetime
from html.parser import HTMLParser
from typing import Any, Mapping, Sequence

from app.contracts import Capability
from app.providers import http as provider_http
from app.providers.adapters._support import (
    HttpAdapter,
    observation,
    price_unit,
    text_of,
    utc_now,
)
from app.providers.base import InstrumentRef, ProviderError, Verdict

HOST = "https://finviz.com"
STOCK_PATH = "/stock?t={symbol}"

#: The page is HTML; a JSON accept header gets a challenge more often.
ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"

#: ``column -> (label, ...)``. The first label present wins; the alternates are
#: the same cell in Finviz's other spellings, never a different metric.
VALUATION_FIELDS: dict[str, tuple[str, ...]] = {
    "pe_ttm": ("P/E",),
    "pe_forward": ("Forward P/E",),
    "peg": ("PEG",),
    "ps": ("P/S", "Price/Sales"),
    "pb": ("P/B", "Price/Book"),
    "ev_ebitda": ("EV/EBITDA",),
    "eps_ttm": ("EPS (ttm)", "EPS ttm"),
    "dividend_yield": ("Dividend TTM", "Dividend Yield"),
    "payout_ratio": ("Payout", "Payout Ratio"),
    "roe": ("ROE",),
    "debt_to_equity": ("Debt/Eq", "Total Debt/Eq"),
    "revenue_ttm": ("Sales", "Revenue"),
}

#: Parsed because the page publishes them, kept out of the observations because
#: the 92-column contract has no such field.
EXTRA_METRICS: dict[str, tuple[str, ...]] = {
    "p_fcf": ("P/FCF", "Price/Free Cash Flow"),
}

#: The short/ownership cells the selection filters read
#: (:mod:`app.screen.facts`). The labels are the live page's own (probed
#: 2026-10-25): ``Short Float 0.83%``, ``Insider Own 0.12%``,
#: ``Inst Own 68.93%``. Every value is already a percentage point — ``0.83``
#: is 0.83%, **not** a fraction — so the stored number is the printed number.
#: ``Short Ratio`` and ``Shs Float`` sit next to these cells and are
#: deliberately absent: no filter key reads them, and storing a cell nobody can
#: filter would widen the vocabulary without a product reason.
FACT_FIELDS: dict[str, tuple[str, ...]] = {
    "short_float": ("Short Float",),
    "insider_own": ("Insider Own",),
    "inst_own": ("Inst Own",),
}

_MONEY_FIELDS = frozenset({"eps_ttm", "revenue_ttm"})
_PERCENT_FIELDS = frozenset({"dividend_yield", "payout_ratio", "roe"})

#: ``debt_to_equity`` is the one cell whose provider unit is not the store unit.
DEBT_TO_EQUITY_SCALE = 100.0

#: Markers of a Cloudflare interstitial. Checked against the lower-cased body.
_CHALLENGE_MARKERS = (
    "just a moment",
    "attention required",
    "cf-chl",
    "challenge-platform",
    "cf-browser-verification",
    "enable javascript and cookies",
    "verify you are human",
)

#: ``148.75``, ``5.07T``, ``39,539,912``, ``-0.14%`` — one token, with an
#: optional magnitude suffix. Anything else (``Yes / Yes``, ``1.68% 1.82%``,
#: ``1.06 (0.30%)``) is not a number this adapter may read.
_NUMBER = re.compile(r"^([+-]?\d[\d,]*(?:\.\d+)?)\s*([KMBT])?%?$")
_SUFFIX = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}
_PAREN_PERCENT = re.compile(r"\(([+-]?\d[\d,]*(?:\.\d+)?)%\)")
_ONLY_PERCENT = re.compile(r"^([+-]?\d[\d,]*(?:\.\d+)?)%$")


class _SnapshotParser(HTMLParser):
    """Collect the ``(label, value)`` pairs of Finviz's snapshot cells.

    The label sits in ``div.snapshot-td-label`` and the value in the following
    ``div.snapshot-td-content``; both may wrap their text in ``<a>``, ``<b>``,
    ``<span>`` or ``<small>``, so only ``div`` depth is tracked. A malformed page
    (a challenge, a truncated body) simply yields fewer pairs.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[tuple[str, str]] = []
        self._mode: str | None = None
        self._depth = 0
        self._buffer: list[str] = []
        self._label: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "div":
            return
        if self._mode is not None:
            self._depth += 1
            return
        classes = (dict(attrs).get("class") or "").split()
        if "snapshot-td-label" in classes:
            self._mode, self._depth, self._buffer = "label", 1, []
        elif "snapshot-td-content" in classes:
            self._mode, self._depth, self._buffer = "value", 1, []

    def handle_endtag(self, tag: str) -> None:
        if tag != "div" or self._mode is None:
            return
        self._depth -= 1
        if self._depth > 0:
            return
        text = re.sub(r"\s+", " ", "".join(self._buffer)).strip()
        if self._mode == "label":
            self._label = text
        elif self._label is not None:
            self.rows.append((self._label, text))
            self._label = None
        self._mode, self._buffer = None, []

    def handle_data(self, data: str) -> None:
        if self._mode is not None:
            self._buffer.append(data)


def snapshot_rows(body: str) -> dict[str, str]:
    """``{label: value text}`` from one stock page; the first occurrence wins."""

    parser = _SnapshotParser()
    try:
        parser.feed(body or "")
        parser.close()
    except Exception:  # noqa: BLE001 - a broken page is "no rows", not a crash
        return {}
    out: dict[str, str] = {}
    for label, value in parser.rows:
        if label and label not in out:
            out[label] = value
    return out


def _number(text: str) -> float | None:
    """One provider number, or ``None`` — never ``0`` for an unreadable cell."""

    cleaned = (text or "").strip()
    if not cleaned:
        return None
    match = _NUMBER.match(cleaned)
    if match is None:
        return None
    try:
        value = float(match.group(1).replace(",", ""))
    except ValueError:  # pragma: no cover - the regex already pinned the shape
        return None
    suffix = (match.group(2) or "").upper()
    if suffix:
        value *= _SUFFIX[suffix]
    return value if math.isfinite(value) else None


def _dividend_yield(text: str) -> float | None:
    """The yield of ``Dividend TTM``: the parenthesised percentage.

    ``1.06 (0.30%)`` is a 1.06 annual dividend and a 0.30% yield. Without the
    parentheses only a bare percentage is a yield — a bare ``1.06`` is the
    amount, and reading it as a yield would publish 106%.
    """

    cleaned = (text or "").strip()
    if not cleaned:
        return None
    match = _PAREN_PERCENT.search(cleaned)
    if match is not None:
        return _number(f"{match.group(1)}%")
    match = _ONLY_PERCENT.match(cleaned)
    if match is not None:
        return _number(f"{match.group(1)}%")
    return None


def _first(rows: Mapping[str, str], labels: Sequence[str]) -> str | None:
    for label in labels:
        if label in rows:
            return rows[label]
    return None


def parse_metrics(body: str) -> dict[str, float]:
    """Every valuation metric the page publishes, keyed by canonical name.

    Canonical columns plus :data:`EXTRA_METRICS` (``p_fcf``). A cell that is
    absent, dashed or not numeric is simply not in the result.
    """

    rows = snapshot_rows(body)
    out: dict[str, float] = {}
    for field_name, labels in {**VALUATION_FIELDS, **EXTRA_METRICS}.items():
        text = _first(rows, labels)
        if text is None:
            continue
        if field_name == "dividend_yield":
            value = _dividend_yield(text)
        else:
            value = _number(text)
        if value is None:
            continue
        if field_name == "debt_to_equity":
            value *= DEBT_TO_EQUITY_SCALE
        out[field_name] = value
    return out


def parse_facts(body: str) -> dict[str, float]:
    """The short/ownership cells of the page, keyed by canonical name.

    ``Short Float``, ``Insider Own`` and ``Inst Own`` are published on the same
    snapshot table as the valuation cells and read with the same rules: a cell
    that is absent, dashed (``-``), ``N/A`` or not numeric is simply **not in
    the result** — no zero, no default, no borrowed neighbour. The values are
    percentage points exactly as printed (``0.83%`` → ``0.83``); nothing is
    scaled here, because the store and the filter both mean percent.
    """

    rows = snapshot_rows(body)
    out: dict[str, float] = {}
    for field_name, labels in FACT_FIELDS.items():
        cell = _first(rows, labels)
        if cell is None:
            continue
        value = _number(cell)
        if value is None:
            continue
        out[field_name] = value
    return out


class FinvizAdapter(HttpAdapter):
    """Scraped US quote page: trust 3 (aggregator), delayed, no credential."""

    name = "finviz"
    trust = 3
    capabilities = frozenset({Capability.FUNDAMENTALS})
    accept = ACCEPT

    @staticmethod
    def _symbol(instrument: InstrumentRef) -> str:
        """Finviz spells a share class with a hyphen, like Yahoo: ``BRK-B``."""

        return str(instrument.symbol).strip().upper().replace(".", "-")

    def url(self, instrument: InstrumentRef) -> str:
        return f"{HOST}{STOCK_PATH.format(symbol=self._symbol(instrument))}"

    async def fetch(
        self, capability: Capability, instrument: InstrumentRef, fields: set[str]
    ) -> dict[str, Any]:
        if capability not in self.capabilities:
            raise ProviderError(
                Verdict(
                    ok=False,
                    kind="schema_drift",
                    detail=f"{self.name} does not implement {capability.value}",
                )
            )
        headers = self.request_headers(instrument)
        async with provider_http.build_client(
            timeout_s=self.timeout_s, headers=headers
        ) as client:
            response = await client.get(self.url(instrument))
            body = response.content
        return {
            "status": int(response.status_code),
            "headers": {str(key).lower(): str(value) for key, value in response.headers.items()},
            "body": body,
            "url": self.url(instrument),
            "capability": Capability.FUNDAMENTALS.value,
        }

    # -- verdict -----------------------------------------------------------

    def verdict(self, raw: Mapping[str, Any], instrument: InstrumentRef) -> Verdict:
        status = int(raw.get("status") or 0)
        if status in (401, 403):
            return Verdict(False, "authwall", f"status {status}")
        if status == 404:
            return Verdict(False, "not_found", "status 404")
        if status == 429:
            return Verdict(False, "throttled", "status 429")
        if 300 <= status < 400:
            return Verdict(False, "blocked", f"redirect {status} (not followed)")
        if 500 <= status < 600:
            return Verdict(False, "5xx", f"status {status}")
        if status != 200:
            return Verdict(False, "schema_drift", f"status {status}")

        body = text_of(raw)
        if not body.strip():
            return Verdict(False, "empty", "empty body")
        lowered = body.lower()
        hit = next((marker for marker in _CHALLENGE_MARKERS if marker in lowered), None)
        if hit is not None:
            return Verdict(False, "blocked", f"Cloudflare challenge page ({hit})")

        rows = snapshot_rows(body)
        if not rows:
            return Verdict(
                False, "schema_drift", "200 HTML without a snapshot table"
            )
        metrics = parse_metrics(body)
        if not any(field_name in metrics for field_name in VALUATION_FIELDS):
            return Verdict(False, "empty", "snapshot table carries no valuation value")
        return Verdict(True, "ok")

    # -- parse -------------------------------------------------------------

    def parse(
        self, raw: Mapping[str, Any], instrument: InstrumentRef
    ) -> Sequence[Mapping[str, Any]]:
        metrics = parse_metrics(text_of(raw))
        if not metrics:
            return []
        # A scraped page publishes no data timestamp for the valuation block; the
        # observation time is the only clock it can prove.
        stamp: datetime = utc_now()
        unit = price_unit(instrument.currency)
        out: list[dict[str, Any]] = []
        for field_name in VALUATION_FIELDS:
            value = metrics.get(field_name)
            if value is None:
                continue
            if field_name in _MONEY_FIELDS:
                cell_unit = unit
            elif field_name in _PERCENT_FIELDS:
                cell_unit = "pct"
            else:
                cell_unit = "ratio"
            out.append(
                observation(
                    instrument,
                    field_name,
                    value_num=value,
                    unit=cell_unit,
                    source=self.name,
                    trust=self.trust,
                    as_of=stamp,
                )
            )
        return out


__all__ = [
    "EXTRA_METRICS",
    "FACT_FIELDS",
    "FinvizAdapter",
    "HOST",
    "VALUATION_FIELDS",
    "parse_facts",
    "parse_metrics",
    "snapshot_rows",
]
