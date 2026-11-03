"""Honest insider buy-vs-sell flow per symbol, aggregated from real SEC Form 4 rows.

Where the numbers come from
---------------------------
``GET /insider-flow/{symbol}`` reads the SAME collector ``GET /engine/insider``
reads: :func:`app.engine.insider.collect_insider`, which fetches EDGAR's own Form
4 XML (``filing`` + per-transaction ``code``, ``shares``, ``price``). Nothing
here is a provider's opinion about insiders; it is the filing's own arithmetic.

The withdrawn vendor field
--------------------------
This route exists because the OTHER insider surface — the Yahoo **net share
change** field on ``GET /market/insider`` — was removed. Its own basis for
removal is quoted verbatim below; do not revive it, and do not re-derive it:

    "a vendor field whose own numbers cannot be read as a flow (TSLA 76.0, META 45.9, GME 8.7 — a mixture of ownership levels and junk)"

That field's own numbers could not be read as a flow at all, so this route
reads one that can: Form 4 transaction codes, summed inside a named window.

Which transactions are a "buy" and which are a "sell"
-----------------------------------------------------
A Form 4 row carries a single-letter transaction code, exactly as reported:

* ``P`` — open-market purchase. This is the **buy** side of the flow, and the
  only thing counted as one.
* ``S`` — sale. The **sell** side.
* ``A`` award, ``M`` option exercise, ``G`` gift, ``C`` conversion,
  ``F`` tax withholding, ``D`` sale-to-issuer — none of these is an open-market
  purchase or sale. They count toward ``filings``/``filers`` and are counted in
  ``otherTransactions``, but they never touch ``bought_shares``/``sold_shares``.
  Folding a grant into "buying" would be inventing an intent the filing does not
  state, and would put the same phantom optimism on screen the vendor field did.

Codes compare by their first letter, so the SEC's own spellings
(``"P - Open market purchase"``) parse without a table of hidden codes.

Honesty rules, all load-bearing
-------------------------------
* **No price, no value.** A row with no price (or price ``0``) still counts in
  the share totals — its share count is a fact — and is excluded from that
  side's value total and counted in ``unpricedFilings``. A side where *nothing*
  was priced answers ``null``, never ``0.0``; a side with a mix sums its priced
  rows and says so in ``reason``.
* **An empty window is never ``0.0``.** Zero buys over a real window is a fact;
  zero buys over no data is not. A window with no surviving rows answers
  ``bought_shares: null`` with ``reason`` naming "no insider filings"; a window
  whose rows are all grants/exercises/gifts answers ``null`` with a reason
  naming "none of them open-market purchases or sales".
* **``as_of`` is the newest reportable date among the surviving rows**, never
  the clock: the flow is a statement about filings, so its date is the
  filings'. An empty window has no such date and says ``null``.
* **``net_shares`` is ``null`` when one side is silent.** Buys-minus-sells
  across a window where nobody sold nets an absent side against nothing.
* **The boundary day is inclusive.** ``days=30`` covers today and the 29 days
  before it, so ``from``/``to`` name the window exactly.
* **A failed read is reported, not swallowed.** The collector's own error list
  rides out in ``provider_errors``, the way ``/engine/insider`` reports it.

What the caller must supply
---------------------------
``days`` is one of :data:`SUPPORTED_WINDOWS`; anything else is the house
``{error, errorMessage}`` ``invalid_days`` envelope — a typo must never look
like an empty filing history. The symbol is upper-cased and bound per request,
and no aggregation is memoized across requests.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime, timedelta
from typing import Any

from app.auth import ApiError
from app.engine.insider import collect_insider

#: ``P`` is an open-market purchase; ``S`` is a sale. Everything else is neither.
PURCHASE_CODE = "P"
SALE_CODE = "S"

#: The windows a caller may ask for, and the default. Both are reporting
#: cadences an analyst already reasons in; an arbitrary ``days=45`` is not, so
#: it is refused rather than silently widened or narrowed.
SUPPORTED_WINDOWS: tuple[int, ...] = (30, 90)
DEFAULT_WINDOW = 30

#: How many Form 4 rows one request reads. A 90-day window over a heavily-traded
#: issuer holds more rows than this, so the read is bounded to the newest rows
#: and never described as a complete filing history.
COLLECT_LIMIT = 40

#: ``P``, ``p``, ``P - Open market purchase``: the code is its first letter run.
_CODE = re.compile(r"[A-Za-z]+")


def _field(row: Any, name: str) -> Any:
    """One collector field, from a mapping or an object.

    The collector yields :class:`app.engine.insider.InsiderTransaction` objects;
    a mapping-shaped row still reads, so a collector reformat degrades to "the
    fields I know" instead of a 500.
    """

    if isinstance(row, Mapping):
        return row.get(name)
    return getattr(row, name, None)


def _text_field(row: Any, name: str) -> str | None:
    """One collector field as non-blank text, or ``None``."""

    raw = _field(row, name)
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def _number_field(row: Any, name: str) -> float | None:
    """One collector field as a number, or ``None``. A blank/word is not a number,
    and ``0`` is a real number — a price of zero is a missing price, handled by
    the caller, not here."""

    raw = _field(row, name)
    if raw is None or isinstance(raw, bool):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _parse_date(raw: Any) -> date | None:
    """An ISO ``YYYY-MM-DD`` collector date, or ``None`` when it is not one.

    A row that fails to parse cannot be placed in a window and contributes
    nothing to any total.
    """

    if not isinstance(raw, str):
        return None
    token = raw.strip()
    if len(token) < 10:
        return None
    try:
        return date.fromisoformat(token[:10])
    except ValueError:
        return None


def transaction_code(row: Any) -> str | None:
    """``P`` or ``S`` — the row's open-market direction, else ``None``.

    The first letter run of the reported code is the code's meaning, so
    ``"P - Open market purchase"`` reads ``P`` and ``"S - Sale"`` reads ``S``,
    while ``M`` (exercise), ``A`` (award), ``G`` (gift), ``C`` (conversion),
    ``F`` (tax) and ``D`` alike return ``None`` — which is exactly the
    distinction the flow needs.
    """

    raw = _text_field(row, "code")
    if raw is None:
        return None
    match = _CODE.search(raw)
    if match is None:
        return None
    letter = match.group(0).upper()[0]
    return letter if letter in (PURCHASE_CODE, SALE_CODE) else None


def parse_days(raw: str | None) -> int:
    """``days`` as one of :data:`SUPPORTED_WINDOWS`, or ``422 invalid_days``.

    Taken as text and validated here, the way ``/engine/insider`` takes
    ``limit``: a framework-shaped 422 would bypass the house
    ``{error, errorMessage}`` envelope. Blank, non-numeric, zero, negative,
    fractional and unsupported windows are all the same refusal — a caller
    cannot slide a ``45`` through by assuming it means "a while".
    """

    token = "" if raw is None else str(raw).strip()
    try:
        value = int(token, 10)
    except (TypeError, ValueError):
        raise ApiError(
            status_code=422,
            error="invalid_days",
            message=(
                "days must be a whole number of days from "
                f"{SUPPORTED_WINDOWS[0]} to {SUPPORTED_WINDOWS[-1]}; got {raw!r}"
            ),
        ) from None
    if value not in SUPPORTED_WINDOWS:
        raise ApiError(
            status_code=422,
            error="invalid_days",
            message=(
                "days must be one of "
                f"{', '.join(str(window) for window in SUPPORTED_WINDOWS)}; got {raw!r}"
            ),
        )
    return value


def _filable_date(row: Any) -> date | None:
    """The date that places a row in the window.

    ``transaction_date`` is what the transaction was; ``filed`` is when it was
    reported. A window is about transactions, so a row goes in by
    ``transaction_date`` when it has one, and falls back to ``filed`` otherwise.
    """

    return _parse_date(_text_field(row, "transaction_date")) or _parse_date(
        _text_field(row, "filed")
    )


def _row_identity(row: Any) -> tuple[Any, ...]:
    """A row's dedup key.

    A Form 4 can carry several transactions, so the accession alone is NOT a
    unique row — deduping on it would silently drop the second half of a real
    filing. The key is the whole reported transaction instead, so a re-reported
    duplicate collapses while a genuine second transaction survives.
    """

    return (
        _text_field(row, "accession"),
        _text_field(row, "transaction_date"),
        _text_field(row, "code"),
        _number_field(row, "shares"),
        _number_field(row, "price"),
        _text_field(row, "owner_name"),
    )


async def collect_rows(
    symbol: str, cik: str, *, limit: int = COLLECT_LIMIT
) -> tuple[Any, Any]:
    """The collector's rows and its errors, awaited.

    :func:`app.engine.insider.collect_insider` is async and returns
    ``(items, errors)``. Both come back unchanged: the rows are the facts and
    the errors are the admissions, so a body that failed to read is reported in
    ``provider_errors`` rather than papered over with an empty window. The
    ``Any`` is deliberate — the collector's row type is its own business, and
    :func:`build_insider_flow` reads whatever fields it carries.
    """

    return await collect_insider(symbol, cik, limit=limit)


def build_insider_flow(
    rows: Iterable[Any],
    *,
    symbol: str,
    window_days: int,
) -> dict[str, Any]:
    """The flow document, pure: the same rows and window give the same document.

    ``window_days`` ends today, inclusive, and reaches back ``window_days - 1``
    further days — a 30-day window holds exactly 30 calendar days, today among
    them. ``reason`` is ``None`` only when both sides are fully priced; it never
    contradicts the numbers, it only explains the ones that are missing.
    """

    today = datetime.now(UTC).date()
    start = today - timedelta(days=window_days - 1)

    filings = 0
    other = 0
    unpriced = 0
    filers: set[str] = set()
    reportable: list[str] = []
    seen: dict[tuple[Any, ...], bool] = {}

    # Bought and sold sides, tracked separately: each side keeps its own priced
    # count so ``bought_value`` can say "nothing on this side was priced" with a
    # ``null`` instead of summing what it had and implying the rest.
    side: dict[str, dict[str, float]] = {
        "bought": {"shares": 0.0, "value": 0.0, "rows": 0.0, "priced": 0.0},
        "sold": {"shares": 0.0, "value": 0.0, "rows": 0.0, "priced": 0.0},
    }

    for row in rows:
        if row is None:
            continue
        identity = _row_identity(row)
        if identity in seen:
            continue
        seen[identity] = True

        when = _filable_date(row)
        if when is None or when < start or when > today:
            # Outside the window, or undatable: contributes nothing, and is not
            # counted as "no filings" — the caller asked about a window, not
            # about every filing ever.
            continue

        filings += 1
        filer = _text_field(row, "owner_name")
        if filer:
            filers.add(filer)
        filed = _text_field(row, "filed")
        if filed:
            reportable.append(filed[:10])
        other += 1  # provisional: an open-market row decrements this below

        code = transaction_code(row)
        if code is None:
            continue

        shares = _number_field(row, "shares")
        price = _number_field(row, "price")
        priced = price is not None and price > 0
        weighed = shares is not None and shares > 0
        if not weighed:
            # No share count means no share total to contribute. A priced row
            # with no shares is not a transaction this aggregation can state.
            continue

        other -= 1
        bucket = "bought" if code == PURCHASE_CODE else "sold"
        side[bucket]["rows"] += 1
        side[bucket]["shares"] += shares  # type: ignore[arg-type]
        if priced:
            side[bucket]["priced"] += 1
            side[bucket]["value"] += shares * price  # type: ignore[operator]
        else:
            unpriced += 1

    bought, sold = side["bought"], side["sold"]

    document: dict[str, Any] = {
        "symbol": symbol,
        "windowDays": window_days,
        "from": start.isoformat(),
        "to": today.isoformat(),
        "bought_shares": bought["shares"] if bought["rows"] else None,
        "sold_shares": sold["shares"] if sold["rows"] else None,
        # A value total sums the priced rows on its side only, and is ``null``
        # when NOTHING on that side was priced — a side of entirely unpriced
        # filings has no value, and ``0.0`` would be a fake zero. The unpriced
        # rows are counted in ``unpricedFilings`` and named in ``reason``, so a
        # value that covers fewer rows than the share count is explained by the
        # document itself rather than implied.
        "bought_value": bought["value"] if bought["priced"] else None,
        "sold_value": sold["value"] if sold["priced"] else None,
        "filings": filings,
        "filers": len(filers),
        "otherTransactions": other,
        "unpricedFilings": unpriced,
        "net_shares": bought["shares"] - sold["shares"]
        if bought["rows"] and sold["rows"]
        else None,
        "as_of": max(reportable) if reportable else None,
        "reason": None,
    }

    if filings == 0:
        document["reason"] = (
            f"no insider filings for {symbol} in the last {window_days} days"
        )
    elif not bought["rows"] and not sold["rows"]:
        document["reason"] = (
            f"{filings} insider filings for {symbol} in the last {window_days} "
            "days, none of them open-market purchases or sales"
        )
    elif unpriced:
        document["reason"] = (
            f"{unpriced} of {filings} filings carried no price, so the value "
            "totals cover only the priced rows"
        )
    return document


__all__ = [
    "COLLECT_LIMIT",
    "DEFAULT_WINDOW",
    "PURCHASE_CODE",
    "SALE_CODE",
    "SUPPORTED_WINDOWS",
    "build_insider_flow",
    "collect_rows",
    "parse_days",
    "transaction_code",
]
