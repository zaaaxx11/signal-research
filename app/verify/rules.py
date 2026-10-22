"""V1..V16 as a table of pure rules: ``(row, context) -> rejections`` (02 §3.7).

The verifier is the engine's self-validating property. Instead of comparing a
number with itself, every rule compares a value with a quantity the row already
carries — the price move, the share count, the parity identity, the session
clock — so a wrong cell cannot hide behind a green check.

Design rules this module enforces rather than documents:

* **A table, not a chain of ``if``s.** :data:`RULES` is the registry: ``id``,
  the canonical ``columns`` it may reject, and a ``severity``. The severity is
  the export policy (``critical`` blocks the file, ``major`` degrades it) and it
  lives beside the rule, never in a caller's list.
* **Context is injected.** ``context`` carries ``now``, ``asset_class``,
  ``venue``, ``market_state``, ``session_date``, ``session_close``,
  ``max_staleness_s``, ``allow_delayed``, ``multi_class``, ``quote_currency``,
  ``risk_free_rate``, ``candidates`` and ``index``. No rule reads the wall clock
  or the network, so a fixture is a complete description of the decision.
* **Failure nulls, never clamps.** A value that fails becomes ``None`` (or the
  documented series/flag effect: bars dropped, option block cleared, quote
  marked stale, cell marked disputed). Writing a plausible-looking repair is a
  contract violation, not a rescue.
* **Apply is pure.** ``apply`` returns a new row and leaves the caller's row
  untouched, so the worker can keep the pre-verification row for the audit.

``verify/`` is import-pure (``tests/_import_rules.py``): it may import its own
modules only, never ``app.contracts`` or ``app.enrich``. The option/return
column lists below are therefore literals, and the 92-column contract is
injected by the caller.
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Mapping, Sequence

from app.verify.units import parse_number

#: Rule set names this module can evaluate. An unknown name is a bug, not a
#: quiet fallback to "no rules".
RULE_SETS: frozenset[str] = frozenset({"v1"})

#: 02 §3.7 tolerances, named so a test can point at the number it pins.
V1_TOLERANCE_PP = 0.02
V4_TOLERANCE = 0.02
V4_MULTI_CLASS_TOLERANCE = 0.05
V5_TOLERANCE = 0.01
V6_TOLERANCE_PP = 0.05
V9_EQUITY_BAND = 0.003
V9_INDEX_BAND = 0.0015
V12_TOLERANCE = 0.005
V13_IN_SESSION_S = 60
V13_OFF_SESSION_GRACE_S = 3600
V14_TOLERANCE = 0.002
V14_IDX_TOLERANCE = 0.0075
V14_INDEX_TOLERANCE = 0.0025
V15_MAX_VOLUME = 1.0e12
V16_SKEW_S = 300

#: Columns V9 clears when the chain is suspected. Canonical Options group,
#: written out because this module may not import ``app.contracts``.
OPTION_COLUMNS: tuple[str, ...] = (
    "opt_expiry",
    "opt_dte",
    "opt_strike_atm",
    "opt_iv_atm",
    "opt_delta_atm",
    "opt_gamma_atm",
    "opt_theta_atm",
    "opt_vega_atm",
    "opt_open_interest_atm",
    "opt_volume_atm",
)

#: Columns V12 clears when the split is inconsistent with the quoted series.
RETURN_COLUMNS: tuple[str, ...] = (
    "ret_1d",
    "ret_1w",
    "ret_1m",
    "ret_3m",
    "ret_6m",
    "ret_ytd",
    "ret_1y",
    "ret_3y",
    "ret_5y",
)

#: Price cells V11 empties on a currency mismatch. No silent conversion: an
#: unconverted EUR price in a USD column is a lie with extra steps.
PRICE_COLUMNS: tuple[str, ...] = (
    "last_price",
    "prev_close",
    "change_abs",
    "change_pct",
    "bid",
    "ask",
    "day_open",
    "day_high",
    "day_low",
    "turnover_value",
)

#: The greeks V7 empties with the IV (02 §3.7: "greeks follow the IV out").
GREEK_COLUMNS: tuple[str, ...] = (
    "opt_delta_atm",
    "opt_gamma_atm",
    "opt_theta_atm",
    "opt_vega_atm",
)

#: Non-canonical helper inputs of the parity identity. They are carried on the
#: row by the options phase, so V9 must empty them with the rest of the block.
_PARITY_PRICE_COLUMNS: tuple[str, ...] = ("opt_call_price", "opt_put_price")


@dataclass(frozen=True, slots=True)
class Rule:
    """One verification rule: identity, owned columns, export severity."""

    id: str
    columns: tuple[str, ...]
    severity: str
    title: str


#: V1..V16 in declaration order. This tuple *is* the registry the tests read.
RULES: tuple[Rule, ...] = (
    Rule("V1", ("change_pct",), "major", "change_pct matches the price move"),
    Rule("V2", ("day_high", "day_low", "day_open", "bars_count", "first_bar_date", "last_bar_date"), "major", "OHLC ordering"),
    Rule("V3", ("last_bar_date", "first_bar_date", "bars_count"), "major", "bar dates strictly ascending"),
    Rule("V4", ("market_cap", "shares_outstanding"), "major", "market cap equals price x shares"),
    Rule("V5", ("pe_ttm", "eps_ttm"), "major", "PE equals price / EPS"),
    Rule("V6", ("dividend_yield",), "major", "dividend yield unit and consistency"),
    Rule("V7", ("opt_iv_atm",) + GREEK_COLUMNS, "critical", "IV fraction band"),
    Rule(
        "V8",
        ("opt_delta_atm", "opt_gamma_atm", "opt_vega_atm", "opt_theta_atm"),
        "major",
        "greek bands",
    ),
    Rule("V9", OPTION_COLUMNS, "critical", "put-call parity"),
    Rule("V10", ("index_memberships",), "major", "index weight sum"),
    Rule("V11", PRICE_COLUMNS, "critical", "quote currency matches the instrument"),
    Rule("V12", RETURN_COLUMNS, "major", "split consistency"),
    Rule("V13", ("quote_as_of",), "major", "quote freshness against the session"),
    Rule("V14", ("last_price", "bid", "ask"), "major", "provider divergence"),
    Rule("V15", ("day_volume", "last_price"), "critical", "volume band and positive price"),
    Rule("V16", ("quote_as_of", "as_of_achieved"), "critical", "no future as_of"),
)

_BY_ID: dict[str, Rule] = {rule.id: rule for rule in RULES}

if len(RULES) != 16 or len(_BY_ID) != 16:  # pragma: no cover - table typo guard
    raise RuntimeError("verify.rules.RULES must declare V1..V16 exactly once")


# ---------------------------------------------------------------------------
# Small typed readers
# ---------------------------------------------------------------------------


def _filled(value: Any) -> bool:
    """``None`` and blank strings are empty; ``0`` and ``False`` are values."""

    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def _number(value: Any) -> float | None:
    """Read a cell as a number; sentinels stay absent (never 0)."""

    return parse_number(value, unit="number")


def _moment(value: Any) -> datetime | None:
    """Parse an aware ISO timestamp (or a bare date at midnight UTC)."""

    if isinstance(value, datetime):
        return value if value.tzinfo is not None else None
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        text = text + "T00:00:00+00:00"
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else None


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if value is None:
        return ""
    return str(value)


def _envelope(
    rule: str,
    column: str,
    reason: str,
    value: Any,
    row: Mapping[str, Any],
    message: str = "",
) -> dict[str, Any]:
    """One rejection: the frozen envelope of 02 §3.7/§4.7."""

    detail = f"{reason}: {message}" if message else reason
    return {
        "rule": rule,
        "column": column,
        "reason": reason,
        "value_text": _text(value) or reason,
        "instrument_id": row.get("instrument_id"),
        "detail": detail,
    }


def _relative_error(actual: float, expected: float) -> float:
    if expected == 0:
        return abs(actual - expected)
    return abs(actual - expected) / abs(expected)


# ---------------------------------------------------------------------------
# V1..V16
# ---------------------------------------------------------------------------


def _v1_change_pct(row: dict[str, Any], context: Mapping[str, Any], out: list[dict[str, Any]]) -> None:
    """``change_pct`` is derived, so a quoted 0 beside a real move is a lie."""

    quoted = _number(row.get("change_pct"))
    if quoted is None:
        return
    last = _number(row.get("last_price"))
    prev = _number(row.get("prev_close"))
    if last is None or prev is None or prev == 0:
        # The inputs cannot derive it; inventing a comparison is worse than
        # saying nothing (02 §3.7: the property must be self-validating).
        return
    derived = (last / prev - 1.0) * 100.0
    if abs(quoted - derived) > V1_TOLERANCE_PP:
        out.append(
            _envelope(
                "V1",
                "change_pct",
                "change_pct_mismatch",
                row.get("change_pct"),
                row,
                f"quoted {quoted:g} pp vs derived {derived:.6f} pp",
            )
        )
        row["change_pct"] = None


def _recount_bars(row: dict[str, Any], bars: Sequence[Mapping[str, Any]]) -> None:
    """Rewrite ``bars_count``/``first_bar_date``/``last_bar_date`` from the kept bars."""

    row["bars"] = [dict(bar) for bar in bars]
    row["bars_count"] = len(bars)
    dates = [str(bar.get("date")) for bar in bars if _filled(bar.get("date"))]
    row["first_bar_date"] = dates[0] if dates else None
    row["last_bar_date"] = dates[-1] if dates else None


def _v2_ohlc_order(row: dict[str, Any], context: Mapping[str, Any], out: list[dict[str, Any]]) -> None:
    """Drop every bar whose own open/high/low/close do not nest correctly."""

    bars = row.get("bars")
    if not isinstance(bars, (list, tuple)) or not bars:
        return
    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for bar in bars:
        if not isinstance(bar, Mapping):
            dropped.append({"date": None, "raw": repr(bar)})
            continue
        open_, high = _number(bar.get("open")), _number(bar.get("high"))
        low, close = _number(bar.get("low")), _number(bar.get("close"))
        if open_ is None or high is None or low is None or close is None:
            dropped.append(dict(bar))
            continue
        if low <= min(open_, close) and max(open_, close) <= high:
            kept.append(dict(bar))
        else:
            dropped.append(dict(bar))
    if not dropped:
        return
    worst = dropped[0]
    out.append(
        _envelope(
            "V2",
            "day_high",
            "ohlc_order",
            (
                f"bar {worst.get('date')} open={worst.get('open')} high={worst.get('high')} "
                f"low={worst.get('low')} close={worst.get('close')}"
            ),
            row,
            f"{len(dropped)} bar(s) dropped",
        )
    )
    _recount_bars(row, kept)


def _v3_bar_dates(row: dict[str, Any], context: Mapping[str, Any], out: list[dict[str, Any]]) -> None:
    """Keep one bar per session, in strictly ascending date order."""

    bars = row.get("bars")
    if not isinstance(bars, (list, tuple)) or not bars:
        return
    seen: set[str] = set()
    kept: list[dict[str, Any]] = []
    duplicates: list[Any] = []
    for bar in bars:
        if not isinstance(bar, Mapping):
            continue
        date = str(bar.get("date") or "")
        if not date or date in seen or (kept and date <= str(kept[-1].get("date"))):
            duplicates.append(bar.get("date"))
            continue
        seen.add(date)
        kept.append(dict(bar))
    if not duplicates:
        return
    out.append(
        _envelope(
            "V3",
            "last_bar_date",
            "bar_dates_not_strict",
            f"duplicate/out-of-order session {duplicates[0]}",
            row,
            f"{len(duplicates)} bar(s) dropped",
        )
    )
    _recount_bars(row, kept)


def _v4_market_cap(row: dict[str, Any], context: Mapping[str, Any], out: list[dict[str, Any]]) -> None:
    """``market_cap ≈ price x shares`` within 2% (5% for a multi-class issuer)."""

    cap = _number(row.get("market_cap"))
    price = _number(row.get("last_price"))
    shares = _number(row.get("shares_outstanding"))
    if cap is None or price is None or shares is None:
        return
    if price <= 0 or shares <= 0:
        return
    expected = price * shares
    tolerance = V4_MULTI_CLASS_TOLERANCE if context.get("multi_class") else V4_TOLERANCE
    if _relative_error(cap, expected) > tolerance:
        out.append(
            _envelope(
                "V4",
                "market_cap",
                "market_cap_mismatch",
                row.get("market_cap"),
                row,
                f"{cap:g} vs {expected:g} (> {tolerance:.0%})",
            )
        )
        # The shares are the input, the cap is the derived claim: keep the
        # former, reject the latter (02 §3.7 V4).
        row["market_cap"] = None


def _v5_pe(row: dict[str, Any], context: Mapping[str, Any], out: list[dict[str, Any]]) -> None:
    """``pe_ttm ≈ price / eps_ttm`` within 1%, only when EPS is positive."""

    pe = _number(row.get("pe_ttm"))
    price = _number(row.get("last_price"))
    eps = _number(row.get("eps_ttm"))
    if pe is None or price is None or eps is None or eps <= 0 or price <= 0:
        return
    expected = price / eps
    if _relative_error(pe, expected) > V5_TOLERANCE:
        out.append(
            _envelope(
                "V5",
                "pe_ttm",
                "pe_mismatch",
                row.get("pe_ttm"),
                row,
                f"{pe:g} vs {expected:g}",
            )
        )
        row["pe_ttm"] = None


def _v6_dividend_yield(row: dict[str, Any], context: Mapping[str, Any], out: list[dict[str, Any]]) -> None:
    """A percent between 0 and 100 that also agrees with ``dps/price``."""

    quoted = _number(row.get("dividend_yield"))
    if quoted is None:
        return
    if quoted < 0 or quoted > 100:
        out.append(
            _envelope(
                "V6",
                "dividend_yield",
                "dividend_yield_unit",
                row.get("dividend_yield"),
                row,
                f"{quoted:g} is outside [0, 100] percent",
            )
        )
        row["dividend_yield"] = None
        return

    per_share = _number(row.get("dividend_per_share"))
    price = _number(row.get("last_price"))
    if per_share is None or price is None or price <= 0:
        return
    expected = per_share / price * 100.0
    if abs(quoted - expected) > V6_TOLERANCE_PP:
        out.append(
            _envelope(
                "V6",
                "dividend_yield",
                "dividend_yield_inconsistent",
                row.get("dividend_yield"),
                row,
                f"quoted {quoted:g}% vs dps/price {expected:.6f}%",
            )
        )
        row["dividend_yield"] = None


def _v7_iv(row: dict[str, Any], context: Mapping[str, Any], out: list[dict[str, Any]]) -> None:
    """Normalise percent to fraction, then require ``0 < iv <= 5``."""

    raw = _number(row.get("opt_iv_atm"))
    if raw is None:
        return
    fraction = raw if abs(raw) <= 5.0 else raw / 100.0
    if not (0.0 < fraction <= 5.0):
        out.append(
            _envelope(
                "V7",
                "opt_iv_atm",
                "iv_unit",
                row.get("opt_iv_atm"),
                row,
                f"{raw:g} normalises to {fraction:g}, outside (0, 5]",
            )
        )
        row["opt_iv_atm"] = None
        for column in GREEK_COLUMNS:
            # The greeks are derivatives of the IV; keeping them beside a
            # rejected IV would be an invented value (02 §3.7 V7).
            row[column] = None
        return
    row["opt_iv_atm"] = fraction


def _v8_greeks(row: dict[str, Any], context: Mapping[str, Any], out: list[dict[str, Any]]) -> None:
    """Numeric sanity on the greek block: bands, not suggestions."""

    checks: tuple[tuple[str, Callable[[float], bool]], ...] = (
        # The ATM column carries the call convention (0 < delta < 1). A put
        # delta in (-1, 0) is the same magnitude with a minus sign, so a
        # negative value here is a sign error we cannot resolve from the row.
        ("opt_delta_atm", lambda value: 0.0 < value < 1.0),
        ("opt_gamma_atm", lambda value: value >= 0.0),
        ("opt_vega_atm", lambda value: value >= 0.0),
    )
    for column, valid in checks:
        value = _number(row.get(column))
        if value is None:
            continue
        if not valid(value):
            out.append(
                _envelope("V8", column, "greek_sign", row.get(column), row, f"{value:g} out of band")
            )
            row[column] = None


def _v9_parity(row: dict[str, Any], context: Mapping[str, Any], out: list[dict[str, Any]]) -> None:
    """Put-call parity: ``|C - P - (S - K e^{-rT})|`` inside the asset band."""

    call = _number(row.get("opt_call_price"))
    put = _number(row.get("opt_put_price"))
    spot = _number(row.get("last_price"))
    strike = _number(row.get("opt_strike_atm"))
    if call is None or put is None or spot is None or strike is None or spot <= 0:
        return
    dte = _number(row.get("opt_dte")) or 0.0
    rate = _number(context.get("risk_free_rate")) or 0.0
    years = dte / 365.0
    carry = spot - strike * math.exp(-rate * years)
    band = (V9_INDEX_BAND if str(context.get("asset_class") or "").lower() == "index" else V9_EQUITY_BAND) * spot
    error = abs(call - put - carry)
    if error <= band:
        return
    out.append(
        _envelope(
            "V9",
            "opt_iv_atm",
            "parity_violation",
            row.get("opt_iv_atm") if _filled(row.get("opt_iv_atm")) else f"C={call:g} P={put:g} S={spot:g}",
            row,
            f"parity error {error:g} > {band:g}",
        )
    )
    for column in OPTION_COLUMNS + _PARITY_PRICE_COLUMNS:
        # An arbitrage-sized gap invalidates the chain, not just one cell: the
        # inputs of the broken identity cannot stay beside the verdict.
        row[column] = None


def _v10_index_weights(row: dict[str, Any], context: Mapping[str, Any], out: list[dict[str, Any]]) -> None:
    """``Σw ∈ [99, 101]`` and ``member_count == published ± 1`` when known."""

    index = context.get("index")
    if not isinstance(index, Mapping):
        return
    weights = index.get("weights")
    if not weights:
        return
    try:
        total = sum(float(weight) for weight in weights)
    except (TypeError, ValueError):  # pragma: no cover - a malformed context is a bug
        return
    broken = not (99.0 <= total <= 101.0)
    published = index.get("published_count")
    members = index.get("member_count")
    if isinstance(published, int) and isinstance(members, int) and abs(members - published) > 1:
        broken = True
    if not broken:
        return
    out.append(
        _envelope(
            "V10",
            "index_memberships",
            "index_weight_sum",
            row.get("index_memberships") if _filled(row.get("index_memberships")) else weights,
            row,
            f"weight sum {total:g}, members {members}/{published}",
        )
    )
    # Mark partial; never truncate the membership list (02 §3.7 V10).
    row["index_memberships_status"] = "partial"


def _v11_currency(row: dict[str, Any], context: Mapping[str, Any], out: list[dict[str, Any]]) -> None:
    """A quote in another currency empties the price columns; no conversion."""

    quote_currency = row.get("quote_currency")
    expected = context.get("quote_currency")
    if not _filled(quote_currency) or not _filled(expected):
        return
    if str(quote_currency).strip().upper() == str(expected).strip().upper():
        return
    out.append(
        _envelope(
            "V11",
            "last_price",
            "currency_mismatch",
            row.get("last_price"),
            row,
            f"quote {quote_currency} != instrument {expected}",
        )
    )
    for column in PRICE_COLUMNS:
        row[column] = None


def _v12_split(row: dict[str, Any], context: Mapping[str, Any], out: list[dict[str, Any]]) -> None:
    """``close_ratio ≈ 1/split_ratio``; a broken series loses its returns."""

    ratio = _number(row.get("split_ratio"))
    last = _number(row.get("last_price"))
    prev = _number(row.get("prev_close"))
    if ratio is None or ratio <= 0 or last is None or prev is None or prev <= 0:
        return
    expected = 1.0 / ratio
    actual = last / prev
    if _relative_error(actual, expected) <= V12_TOLERANCE:
        return
    out.append(
        _envelope(
            "V12",
            "ret_1d",
            "split_inconsistent",
            row.get("ret_1d") if _filled(row.get("ret_1d")) else f"close_ratio {actual:g} split {ratio:g}",
            row,
            f"close_ratio {actual:g} vs 1/split {expected:g}",
        )
    )
    row["adjustment_status"] = "unadjusted"
    for column in RETURN_COLUMNS:
        row[column] = None


def _v13_staleness(row: dict[str, Any], context: Mapping[str, Any], out: list[dict[str, Any]]) -> None:
    """Freshness against the session clock (or the client's own tolerance)."""

    quote = _moment(row.get("quote_as_of"))
    now = _moment(context.get("now"))
    if quote is None or now is None:
        return
    limit = context.get("max_staleness_s")
    age = (now - quote).total_seconds()
    if limit is not None:
        stale = age > float(limit)
    elif str(context.get("market_state") or "").lower() == "open":
        stale = age > V13_IN_SESSION_S
    else:
        close = _moment(context.get("session_close"))
        if close is not None:
            # Off session, a quote is fresh until an hour after the close: the
            # last print of the day is not "19 hours stale" at 20:30.
            stale = now > close + timedelta(seconds=V13_OFF_SESSION_GRACE_S)
        else:
            stale = age > V13_IN_SESSION_S
    if not stale:
        return
    out.append(
        _envelope(
            "V13",
            "quote_as_of",
            "stale_quote",
            row.get("quote_as_of"),
            row,
            f"age {age:.0f}s",
        )
    )
    # The price is real; only its freshness is in doubt. Keep both value and
    # timestamp and label the quote (02 §3.7 V13).
    row["quote_status"] = "stale"


def _v14_divergence(row: dict[str, Any], context: Mapping[str, Any], out: list[dict[str, Any]]) -> None:
    """Two providers disagreeing: highest trust wins, the cell is disputed."""

    candidates = context.get("candidates")
    if not isinstance(candidates, Mapping):
        return
    tolerance = _v14_tolerance(context)
    for column in ("last_price", "bid", "ask"):
        entries = candidates.get(column)
        if not isinstance(entries, (list, tuple)) or len(entries) < 2:
            continue
        values: list[tuple[float, int, str]] = []
        for entry in entries:
            if not isinstance(entry, Mapping):
                continue
            value = _number(entry.get("value"))
            if value is None:
                continue
            trust = entry.get("trust")
            trust = int(trust) if isinstance(trust, int) and not isinstance(trust, bool) else 4
            values.append((value, trust, str(entry.get("source") or "")))
        if len(values) < 2:
            continue
        best_value, best_trust, best_source = sorted(values, key=lambda item: (item[1], item[2]))[0]
        worst = 0.0
        for value, _trust, _source in values:
            if value == best_value:
                continue
            worst = max(worst, _relative_error(value, best_value))
        if worst <= tolerance:
            continue
        out.append(
            _envelope(
                "V14",
                column,
                "provider_divergence",
                best_value,
                row,
                f"{best_source} (trust {best_trust}) vs {worst:.4%} divergence",
            )
        )
        # The most trusted value is the one that survives; the others live on
        # in the sidecar, not in the table.
        row[column] = best_value
        row["disputed"] = True


def _v14_tolerance(context: Mapping[str, Any]) -> float:
    venue = str(context.get("venue") or "").strip().upper()
    if venue == "XIDX":
        return V14_IDX_TOLERANCE
    if str(context.get("asset_class") or "").strip().lower() == "index":
        return V14_INDEX_TOLERANCE
    return V14_TOLERANCE


def _v15_bands(row: dict[str, Any], context: Mapping[str, Any], out: list[dict[str, Any]]) -> None:
    """``volume ∈ [0, 1e12]`` and ``price > 0``; zero volume is a fact."""

    price = _number(row.get("last_price"))
    if price is not None and price <= 0:
        out.append(
            _envelope("V15", "last_price", "nonpositive", row.get("last_price"), row, f"{price:g} <= 0")
        )
        row["last_price"] = None

    volume = _number(row.get("day_volume"))
    if volume is None:
        return
    if volume < 0:
        out.append(
            _envelope("V15", "day_volume", "nonpositive", row.get("day_volume"), row, f"{volume:g} < 0")
        )
        row["day_volume"] = None
    elif volume > V15_MAX_VOLUME:
        out.append(
            _envelope(
                "V15",
                "day_volume",
                "unit_anomaly",
                row.get("day_volume"),
                row,
                f"{volume:g} > {V15_MAX_VOLUME:g}",
            )
        )
        row["day_volume"] = None


def _v16_future(row: dict[str, Any], context: Mapping[str, Any], out: list[dict[str, Any]]) -> None:
    """Reject an ``as_of`` more than five minutes ahead of the injected clock."""

    quote = _moment(row.get("quote_as_of"))
    now = _moment(context.get("now"))
    if quote is None or now is None:
        return
    if (quote - now).total_seconds() <= V16_SKEW_S:
        return
    out.append(
        _envelope(
            "V16",
            "quote_as_of",
            "future_as_of",
            row.get("quote_as_of"),
            row,
            f"{(quote - now).total_seconds():.0f}s ahead of now",
        )
    )
    row["quote_as_of"] = None


_EVALUATORS: dict[str, Callable[[dict[str, Any], Mapping[str, Any], list[dict[str, Any]]], None]] = {
    "V1": _v1_change_pct,
    "V2": _v2_ohlc_order,
    "V3": _v3_bar_dates,
    "V4": _v4_market_cap,
    "V5": _v5_pe,
    "V6": _v6_dividend_yield,
    "V7": _v7_iv,
    "V8": _v8_greeks,
    "V9": _v9_parity,
    "V10": _v10_index_weights,
    "V11": _v11_currency,
    "V12": _v12_split,
    "V13": _v13_staleness,
    "V14": _v14_divergence,
    "V15": _v15_bands,
    "V16": _v16_future,
}


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


def severity_of(rule: str) -> str:
    """``critical``/``major`` for a rule id; an unknown id is a ``ValueError``."""

    record = _BY_ID.get(str(rule))
    if record is None:
        raise ValueError(f"unknown verification rule {rule!r}; expected V1..V16")
    return record.severity


def critical_rules() -> frozenset[str]:
    """The rule ids whose rejection blocks the export (04 §2)."""

    return frozenset(rule.id for rule in RULES if rule.severity == "critical")


def columns_of(rule: str) -> tuple[str, ...]:
    record = _BY_ID.get(str(rule))
    if record is None:
        raise ValueError(f"unknown verification rule {rule!r}; expected V1..V16")
    return record.columns


def _run(
    row: Mapping[str, Any],
    *,
    context: Mapping[str, Any],
    rule_set: str,
    keep_effects: bool,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if str(rule_set) not in RULE_SETS:
        raise ValueError(f"unknown rule_set {rule_set!r}; this build knows {sorted(RULE_SETS)}")
    if not isinstance(context, Mapping):  # pragma: no cover - programmer error
        raise TypeError("rules require an explicit context mapping (02 §3.7)")

    working = copy.deepcopy(dict(row))
    rejections: list[dict[str, Any]] = []
    for rule in RULES:
        _EVALUATORS[rule.id](working, context, rejections)
    if keep_effects:
        return working, rejections
    return dict(row), rejections


def evaluate(
    row: Mapping[str, Any],
    *,
    context: Mapping[str, Any],
    rule_set: str = "v1",
) -> list[dict[str, Any]]:
    """Report what ``apply`` would reject, without repairing the row."""

    _untouched, rejections = _run(row, context=context, rule_set=rule_set, keep_effects=False)
    return rejections


def apply(
    row: Mapping[str, Any],
    *,
    context: Mapping[str, Any],
    rule_set: str = "v1",
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return ``(row_with_documented_effects, rejections)``; input untouched."""

    return _run(row, context=context, rule_set=rule_set, keep_effects=True)


__all__ = [
    "GREEK_COLUMNS",
    "OPTION_COLUMNS",
    "PRICE_COLUMNS",
    "RETURN_COLUMNS",
    "RULE_SETS",
    "RULES",
    "Rule",
    "apply",
    "columns_of",
    "critical_rules",
    "evaluate",
    "severity_of",
]
