"""Strict unit/scale parsing. "N/A" -> None, never 0 (02 §3.7).

Three questions, three answers — and they are deliberately separate, because
the bug class this module prevents (04 §1 D4/T4) is a sentinel or a mis-scaled
value becoming a number that *looks* plausible:

``parse_number(raw, unit=...)``
    Does this provider scalar hold a number at all? ``"N/A"``, ``"-"``, ``""``,
    ``None``, ``"nan"`` and free text are ``None``. ``0`` is only ever produced
    from a real zero. ``"1,234.5"`` is ``1234.5``; ``"1 234"`` is not guessed.

``to_percent(raw, field=..., unit=None)``
    What percent does the value mean? A dividend yield of ``0.0031`` is
    ``0.31``%, an IV of ``0.25`` is ``25``%. A *declared* unit always wins;
    inference is the fallback and only within the documented bands (``|v| < 1``
    for a yield, ``|v| <= 5`` for IV — the 04 §1 D4 measurement).

``canonical(raw, field=..., unit=None)``
    What do we store? Dividend yield in percent points, IV as a fraction, so a
    quote of ``25`` (%) and ``0.25`` (fraction) land on the same stored value.

``verify/`` is import-pure (``tests/_import_rules.py``): standard library only.
"""

from __future__ import annotations

import math
import re
from typing import Any

#: Strings that mean "the provider has no number". None of them may become 0.
SENTINELS: frozenset[str] = frozenset(
    {"n/a", "n.a.", "n.a", "na", "-", "--", "", "nan", "none", "null", "tbd", "not available"}
)

#: A strict decimal body: optional sign, optional whole thousands separators,
#: optional decimal part. A space or a second dot is not a number we invent a
#: reading for.
_NUMBER = re.compile(r"^[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?$")

#: Fields whose canonical storage is a ratio but whose display unit is percent.
_FRACTION_FIELDS: frozenset[str] = frozenset({"dividend_yield"})

#: Fields whose display unit is percent and whose storage unit is a fraction.
_FRACTION_STORED_FIELDS: frozenset[str] = frozenset({"opt_iv_atm"})

#: ``|v| <= 5`` is a fraction for IV; above it the provider meant percent
#: (04 §1 D4: 6.0 is 6%, 600 is 600% and therefore outside the band).
IV_FRACTION_CEILING = 5.0

_PERCENT_UNITS: frozenset[str] = frozenset({"percent", "pct", "%", "percentage"})
_FRACTION_UNITS: frozenset[str] = frozenset({"fraction", "ratio", "decimal", "unit"})


def _declared_unit(unit: str | None) -> str | None:
    """Normalise a declared unit to ``"percent"``/``"fraction"``/``None``."""

    if unit is None:
        return None
    text = str(unit).strip().lower()
    if text in _PERCENT_UNITS:
        return "percent"
    if text in _FRACTION_UNITS:
        return "fraction"
    return None


def parse_number(raw: object, *, unit: str) -> float | None:
    """Parse a provider scalar; sentinels and free text are ``None``, never 0.

    ``unit`` documents the caller's expectation (``number``, ``percent``,
    ``fraction``). A trailing ``%`` is stripped without rescaling: the number
    keeps its magnitude and the *unit* carries the meaning.
    """

    del unit  # a declaration for the reader; the numeric body is unit-agnostic

    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        value = float(raw)
        return value if math.isfinite(value) else None
    if not isinstance(raw, str):
        return None

    text = raw.strip()
    if not text or text.lower() in SENTINELS:
        return None
    if text.endswith("%"):
        text = text[:-1].strip()
        if not text or text.lower() in SENTINELS:
            return None
    if not _NUMBER.match(text):
        # "abc", "n.a.", "not available", "TBD", "1.2.3", "1 234" all land here:
        # a parser that guesses is a parser that fabricates.
        return None
    try:
        value = float(text.replace(",", ""))
    except ValueError:  # pragma: no cover - the regex already proved it numeric
        return None
    return value if math.isfinite(value) else None


def to_percent(raw: object, *, field: str, unit: str | None = None) -> float | None:
    """Return the display percent for ``raw`` (``0.0031`` -> ``0.31``)."""

    value = parse_number(raw, unit=unit or "number")
    if value is None:
        return None
    declared = _declared_unit(unit)

    if field in _FRACTION_STORED_FIELDS:
        if declared == "percent":
            return value
        if declared == "fraction":
            return value * 100.0
        return value * 100.0 if abs(value) <= IV_FRACTION_CEILING else value

    if field in _FRACTION_FIELDS:
        if declared == "percent":
            return value
        if declared == "fraction":
            return value * 100.0
        return value * 100.0 if abs(value) < 1.0 else value

    return value


def canonical(raw: object, *, field: str, unit: str | None = None) -> float | None:
    """Return the storage unit: yield in percent points, IV as a fraction."""

    value = parse_number(raw, unit=unit or "number")
    if value is None:
        return None
    declared = _declared_unit(unit)

    if field in _FRACTION_STORED_FIELDS:
        if declared == "fraction":
            return value
        if declared == "percent":
            return value / 100.0
        return value if abs(value) <= IV_FRACTION_CEILING else value / 100.0

    if field in _FRACTION_FIELDS:
        if declared == "percent":
            return value
        if declared == "fraction":
            return value * 100.0
        return value * 100.0 if abs(value) < 1.0 else value

    return value


def is_number(value: Any) -> bool:
    """True when ``value`` parses as a finite number (strictly, never coercing)."""

    return parse_number(value, unit="number") is not None


__all__ = [
    "IV_FRACTION_CEILING",
    "SENTINELS",
    "canonical",
    "is_number",
    "parse_number",
    "to_percent",
]
