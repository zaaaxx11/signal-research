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

Egress is a ladder, not a retry
-------------------------------
Finviz sits behind Cloudflare, and a ``403``/``429`` or a ``Just a moment…``
interstitial is a property of the **exit IP**, not of the symbol: the same page
answers ``200`` from another wire. :meth:`FinvizAdapter.fetch` therefore walks
``direct → r.jina.ai (free) → free proxies → paid proxies`` and keeps the first
body that is not a block. The reader comes before the proxies because it is
free, needs no key and returns the page's own HTML; a proxy request is spent
only after it. A direct refusal cools the direct rung for the rest of a bulk run
(``direct_cooldown_s``), so a 3,000-symbol fill does not spend one blocked
request per symbol. The verdict still reads the response status and content —
the proxy and the reader are transport, never a second source: the observations
keep ``source="finviz"`` and the exit is reported on the raw response as
``egress`` for the run log.

What climbs: ``401``/``403``/``429``, a ``200`` Cloudflare challenge, and (on
the reader rung) a markdown body — the reader ignored ``X-Return-Format: html``,
which carries no snapshot table and is never parsed as if it did.
What does **not**: ``404`` (the symbol is not there), ``5xx`` (the provider had
a bad minute and another exit would repeat it), and a ``200`` page that simply
lacks the snapshot table (``schema_drift`` — a shape change, not a wall). Those
are answers about the page, and the next exit would give the same one.

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
import time
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
from app.providers.proxy_pool import FREE, PAID, ProxyPool

HOST = "https://finviz.com"
STOCK_PATH = "/stock?t={symbol}"

#: The reader that renders the page from Jina's own egress when the direct wire
#: is refused. ``r.jina.ai`` fetches over plain HTTP (the host redirects
#: internally), so the path is spelled exactly as probed live 2026-10-25:
#: ``https://r.jina.ai/http://finviz.com/stock?t=AAPL`` → 200 with the page's
#: own 278 KB HTML when ``X-Return-Format: html`` is sent. Without that header
#: the reader answers markdown, which carries no snapshot table and is never
#: parsed as if it did.
JINA_HOST = "https://r.jina.ai"
JINA_SOURCE = "http://finviz.com"
JINA_RETURN_FORMAT = "html"
#: The rung's name in the egress order and on the raw response's ``egress``.
JINA = "jina"
#: The header the reader honours to return the page's HTML instead of markdown.
JINA_FORMAT_HEADERS = {
    "x-return-format": JINA_RETURN_FORMAT,
    "x-respond-with": JINA_RETURN_FORMAT,
}
#: A markdown body starts with these sections; it is an answer *about* the
#: transport (the header was ignored), not the page, and must not be parsed.
_JINA_MARKDOWN_MARKERS = ("markdown content:",)

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

#: Statuses that mean "this exit is refused", not "this page is wrong". They
#: climb to the next egress rung; a ``404`` or a ``5xx`` does not.
_BLOCKED_STATUSES = frozenset({401, 403, 429})

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


#: Labels for the wide-table cells the bulk scan does not carry. Values are
#: either numeric (percent points or counts), text labels, or deliberately
#: absent: a metric with two windows (``6.89% 17.91%``) is not silently reduced
#: to one number. ``parse_wide_metrics`` is display-only; it does not widen the
#: 22-cell fundamentals contract or any filter key.
WIDE_FIELD_LABELS: dict[str, tuple[str, ...]] = {
    "market_cap": ("Market Cap",),
    "enterprise_value": ("Enterprise Value",),
    "income": ("Income",),
    "sales": ("Sales",),
    "book_per_share": ("Book/sh",),
    "cash_per_share": ("Cash/sh",),
    "employees": ("Employees",),
    "ipo": ("IPO",),
    "index": ("Index",),
    "pe": ("P/E",),
    "forward_pe": ("Forward P/E",),
    "peg": ("PEG",),
    "ps": ("P/S",),
    "pb": ("P/B",),
    "pc": ("P/C",),
    "pfcf": ("P/FCF",),
    "ev_ebitda": ("EV/EBITDA",),
    "ev_sales": ("EV/Sales",),
    "quick_ratio": ("Quick Ratio",),
    "current_ratio": ("Current Ratio",),
    "debt_to_equity": ("Debt/Eq",),
    "lt_debt_to_equity": ("LT Debt/Eq",),
    "option_short": ("Option/Short",),
    "eps_ttm": ("EPS (ttm)",),
    "eps_next_y": ("EPS next Y",),
    "eps_next_q": ("EPS next Q",),
    "eps_this_y": ("EPS this Y",),
    "eps_next_5y": ("EPS next 5Y",),
    "eps_past_3_5y": ("EPS past 3/5Y",),
    "sales_past_3_5y": ("Sales past 3/5Y",),
    "eps_yoy_ttm": ("EPS Y/Y TTM",),
    "sales_yoy_ttm": ("Sales Y/Y TTM",),
    "eps_qoq": ("EPS Q/Q",),
    "sales_qoq": ("Sales Q/Q",),
    "insider_trans": ("Insider Trans",),
    "inst_trans": ("Inst Trans",),
    "insider_own": ("Insider Own",),
    "inst_own": ("Inst Own",),
    "roa": ("ROA",),
    "roe": ("ROE",),
    "roic": ("ROIC",),
    "gross_margin": ("Gross Margin",),
    "oper_margin": ("Oper. Margin",),
    "profit_margin": ("Profit Margin",),
    "sma20": ("SMA20",),
    "sma50": ("SMA50",),
    "sma200": ("SMA200",),
    "shares_outstanding": ("Shs Outstand",),
    "shares_float": ("Shs Float", "Float"),
    "week52_high": ("52W High",),
    "week52_low": ("52W Low",),
    "volatility": ("Volatility",),
    "atr_14": ("ATR (14)",),
    "rsi_14": ("RSI (14)",),
    "beta": ("Beta",),
    "rel_volume": ("Rel Volume",),
    "avg_volume": ("Avg Volume",),
    "volume": ("Volume",),
    "perf_week": ("Perf Week",),
    "perf_month": ("Perf Month",),
    "perf_quarter": ("Perf Quarter",),
    "perf_half_y": ("Perf Half Y",),
    "perf_ytd": ("Perf YTD",),
    "perf_year": ("Perf Year",),
    "perf_3y": ("Perf 3Y",),
    "perf_5y": ("Perf 5Y",),
    "perf_10y": ("Perf 10Y",),
    "prev_close": ("Prev Close",),
    "price": ("Price",),
    "change_pct": ("Change %",),
}


def parse_wide_metrics(body: str) -> dict[str, float | str]:
    """Read the display-only cells from a stock page's snapshot table.

    The page is a human table, so two labels can be present twice (``EPS next
    Y`` appears in both the estimate and revision blocks). The first readable
    value wins, exactly like :func:`parse_metrics`; only the cells that are
    genuinely textual (``IPO``, ``Index``, ``Option/Short`` and the two
    ``3/5Y`` cells that print two windows) keep their text. Nothing is guessed,
    and a cell that is missing or malformed is absent from the result.
    """

    rows = snapshot_rows(body)
    out: dict[str, float | str] = {}
    text_fields = frozenset(
        {"ipo", "index", "option_short", "eps_past_3_5y", "sales_past_3_5y"}
    )
    for field_name, labels in WIDE_FIELD_LABELS.items():
        text = _first(rows, labels)
        if text is None:
            continue
        if field_name in text_fields:
            if text and text != "-":
                out[field_name] = text
            continue
        # 52W High ``348.36 -0.16%`` and Volatility ``1.77% 1.80%`` are
        # one cell with two facts. The displayed column is the first fact
        # (price / first window); never concatenate them into a fake number.
        first = re.match(r"^([+-]?\d[\d,]*(?:\.\d+)?(?:\s*[KMBT])?%?)", text or "")
        value = _number(first.group(1)) if first else _number(text)
        if value is not None:
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

    def __init__(
        self,
        *,
        timeout_s: float = 20.0,
        proxy_pool: ProxyPool | None = None,
        proxy_attempts: int = 3,
        direct_cooldown_s: float = 600.0,
    ) -> None:
        super().__init__(timeout_s=timeout_s)
        self.proxy_pool = proxy_pool
        self.proxy_attempts = max(1, int(proxy_attempts))
        self.direct_cooldown_s = max(0.0, float(direct_cooldown_s))
        #: A direct 403/429 is a property of this egress, not this symbol: once
        #: Cloudflare refuses the direct wire, a bulk fill should start on the
        #: reader/proxy rungs instead of spending one blocked request per symbol.
        self._direct_blocked_until = 0.0

    @staticmethod
    def _symbol(instrument: InstrumentRef) -> str:
        """Finviz spells a share class with a hyphen, like Yahoo: ``BRK-B``."""

        return str(instrument.symbol).strip().upper().replace(".", "-")

    def url(self, instrument: InstrumentRef) -> str:
        return f"{HOST}{STOCK_PATH.format(symbol=self._symbol(instrument))}"

    def jina_url(self, instrument: InstrumentRef) -> str:
        """The same stock page, fetched through the reader's own egress."""

        return f"{JINA_HOST}/{JINA_SOURCE}{STOCK_PATH.format(symbol=self._symbol(instrument))}"

    # -- egress ------------------------------------------------------------

    def _egress_candidates(self) -> list[tuple[str, str | None]]:
        """``(rung, proxy)`` in order: direct → Jina (free) → free → paid.

        Jina's reader is *free* (no key, its own egress) and answers the page's
        own HTML, so it is the first fallback after the direct wire — before
        spending a proxy request. A proxy rung is skipped when the pool has
        none; the list is never empty because the reader needs no pool.
        """

        candidates: list[tuple[str, str | None]] = []
        if time.monotonic() >= self._direct_blocked_until:
            candidates.append(("direct", None))
        candidates.append((JINA, None))
        pool = self.proxy_pool
        if pool is not None:
            seen: set[str] = set()
            for _ in range(self.proxy_attempts):
                proxy = pool.acquire(FREE)
                if proxy and proxy not in seen:
                    seen.add(proxy)
                    candidates.append((FREE, proxy))
            if pool.available(PAID):
                proxy = pool.acquire(PAID)
                if proxy and proxy not in seen:
                    candidates.append((PAID, proxy))
        return candidates

    @staticmethod
    def _challenged(raw: Mapping[str, Any]) -> bool:
        """True when this body is a refusal of the *exit*, worth another wire.

        A challenge page can arrive as ``200`` with an interstitial body, and a
        ``401``/``403``/``429`` is a refusal whatever the body says. A ``200``
        page without a snapshot table is **not** a challenge: that is a shape
        change (``schema_drift``), and every exit would return the same one.
        """

        status = int(raw.get("status") or 0)
        if status in _BLOCKED_STATUSES:
            return True
        if status != 200:
            return False
        lowered = text_of(raw).lower()
        return any(marker in lowered for marker in _CHALLENGE_MARKERS)

    def _usable(self, raw: Mapping[str, Any], rung: str) -> bool:
        """A body that may be returned as the page: not a block, not markdown."""

        if self._challenged(raw):
            return False
        if rung == JINA and self._markdown(raw):
            return False
        return True

    def _raw(
        self, response: Any, url: str, instrument: InstrumentRef, proxy: str | None
    ) -> dict[str, Any]:
        """One response as the raw document the ladder reads, with its exit."""

        return {
            "status": int(response.status_code),
            "headers": {str(key).lower(): str(value) for key, value in response.headers.items()},
            "body": response.content,
            "url": url,
            "capability": Capability.FUNDAMENTALS.value,
            "egress": proxy or "direct",
        }

    @staticmethod
    def _markdown(raw: Mapping[str, Any]) -> bool:
        """True when the reader answered markdown instead of the page's HTML."""

        lowered = text_of(raw).lower()
        return any(marker in lowered for marker in _JINA_MARKDOWN_MARKERS)

    async def _attempt(
        self, rung: str, proxy: str | None, instrument: InstrumentRef, headers: dict[str, str]
    ) -> dict[str, Any] | None:
        """One rung: the reader (no proxy, HTML format) or one proxied GET."""

        if rung == JINA:
            return await self._fetch_jina(instrument, headers)
        url = self.url(instrument)
        try:
            async with provider_http.build_client(
                timeout_s=self.timeout_s, headers=headers, proxy=proxy
            ) as client:
                response = await client.get(url)
                return self._raw(response, url, instrument, proxy)
        except Exception as exc:  # noqa: BLE001 - one bad exit must not stop the walk
            if self.proxy_pool is not None and proxy is not None:
                self.proxy_pool.report_failure(proxy, FREE)
            return {
                "status": -1,
                "headers": {},
                "body": f"{type(exc).__name__}: {exc}".encode("utf-8"),
                "url": url,
                "capability": Capability.FUNDAMENTALS.value,
                "egress": proxy or "direct",
            }

    async def _fetch_jina(
        self, instrument: InstrumentRef, headers: Mapping[str, str]
    ) -> dict[str, Any] | None:
        """The page through ``r.jina.ai`` — transport, never a new source.

        The reader does the fetching on its own egress, so this request carries
        no proxy (a proxy here would only add a second failure point). ``None``
        means the reader itself was unreachable; a reachable reader's body is
        returned and judged by the same verdict, with the source still
        ``finviz`` because the bytes are Finviz's page.
        """

        jina_headers = dict(headers)
        jina_headers.update(JINA_FORMAT_HEADERS)
        jina_headers["accept"] = ACCEPT
        url = self.jina_url(instrument)
        try:
            async with provider_http.build_client(
                timeout_s=self.timeout_s, headers=jina_headers
            ) as client:
                response = await client.get(url)
                return self._raw(response, url, instrument, "jina")
        except Exception:  # noqa: BLE001 - the reader is a fallback, not a promise
            return None

    async def fetch(
        self, capability: Capability, instrument: InstrumentRef, fields: set[str]
    ) -> dict[str, Any]:
        """One page over the first exit that is not refused; the last refusal else."""

        if capability not in self.capabilities:
            raise ProviderError(
                Verdict(
                    ok=False,
                    kind="schema_drift",
                    detail=f"{self.name} does not implement {capability.value}",
                )
            )
        headers = self.request_headers(instrument)
        last: dict[str, Any] | None = None
        for rung, proxy in self._egress_candidates():
            candidate = await self._attempt(rung, proxy, instrument, headers)
            if candidate is None:
                # The reader itself was unreachable; the wire below it already
                # left its refusal in ``last``.
                continue
            if self._usable(candidate, rung):
                if self.proxy_pool is not None and proxy is not None:
                    self.proxy_pool.report_success(proxy, FREE)
                return candidate

            last = candidate
            if rung == "direct":
                self._direct_blocked_until = time.monotonic() + self.direct_cooldown_s
            elif proxy is not None and self.proxy_pool is not None:
                self.proxy_pool.report_failure(proxy, FREE)

        return last or {
            "status": -1,
            "headers": {},
            "body": b"no egress candidate",
            "url": self.url(instrument),
            "capability": Capability.FUNDAMENTALS.value,
            "egress": "direct",
        }

    # -- verdict -----------------------------------------------------------

    def verdict(self, raw: Mapping[str, Any], instrument: InstrumentRef) -> Verdict:
        status = int(raw.get("status") or 0)
        if status <= 0:
            detail = text_of(raw).strip() or "no response"
            return Verdict(False, "error", f"no response: {detail}")
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
        if self._markdown(raw):
            # The reader ignored ``X-Return-Format: html``. Markdown carries no
            # snapshot table, so this is a transport shape change, not a page.
            return Verdict(
                False, "schema_drift", "r.jina.ai answered markdown, not the page HTML"
            )

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
    "JINA",
    "JINA_HOST",
    "JINA_SOURCE",
    "VALUATION_FIELDS",
    "parse_facts",
    "parse_metrics",
    "snapshot_rows",
]
