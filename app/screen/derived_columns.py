"""Ten native scan metrics exposed as screener columns, under stable names.

Why this module exists
----------------------
The bulk scan already answers ``EMA13``, ``EMA21``, ``EMA50``, ``EMA100``,
``EMA200``, ``SMA13``, ``SMA21``, ``SMA100``, ``Mom`` and ``Perf.W`` for every
row. None of them was addressable: the catalogue's 116 keys are the shipped
Finviz surface and the trend block carries only ``SMA20``/``SMA50``/``SMA200``.
This registry is the missing name → row-field table, and it is deliberately
**additive**: no catalogue key is renamed, no view is edited, and the frozen
116-key catalogue is untouched.

The name ``derived_columns`` follows the interface's own vocabulary (the picker
shows these under **Computed** because the client cannot spell them as a view
column). It does **not** mean algebra: unlike :mod:`app.screen.derived`, nothing
here is computed from other cells. Every value is a verbatim copy of a provider
cell, mapped by :data:`app.screen.universe.PARITY_FIELDS`; computing an EMA on
this side would be a second methodology nobody could reconcile with the
scanner's own.

Units and labels
----------------
``unit`` is the one thing a renderer needs and the reason it is declared here
rather than inferred from the key:

* ``price`` — the eight moving averages and ``momentum`` are **price levels /
  price differences**, so a cell is printed as a price (two decimals), never as
  a percent. TradingView's ``Mom`` is a difference, not a rate: live on
  2026-11-02, AAPL ``Mom`` = ``-0.88`` while ``close - close[10 sessions ago]``
  = ``342.06 - 342.94`` = ``-0.88``. The label says so
  ("Momentum (price change, 10d)") instead of letting a reader assume percent.
* ``percent`` — ``perf_wtd`` is ``Perf.W``, the provider's week-to-date
  performance in percent points (``-0.54`` is -0.54%). The row field is the
  existing ``ret_1w``, because that is the column the parity scan already fills
  and the ``perf_1w`` filter already reads; one cell, one owner.

What is deliberately absent
---------------------------
* **MTD.** The provider has no month-to-date field. ``Perf.M``, ``Perf.MTD``,
  ``Perf.Mo`` and ``Perf.Month`` answered 0% coverage in the probe, and
  ``Perf.1M`` is a **trailing** one-month return — a different question from
  "since the first trading day of this month". Labelling ``Perf.1M`` as MTD
  would be a false statement about the window, so no ``perf_mtd`` key exists
  until a provider field really measures it.
* **Greeks, expiry and DTM.** Delta/gamma/theta/vega, expiry and days-to-maturity
  are properties of one option contract, not of an issuer row. A bulk equity
  scan has one row per company; a single number in an ``iv`` or ``dtm`` cell
  would silently choose a strike and an expiry nobody asked for. Those live on
  the per-symbol options surface, not here.
"""

from __future__ import annotations

import math
from types import MappingProxyType
from typing import Any, Mapping

#: The two units a computed column can have. A renderer prints ``price`` as a
#: price and ``percent`` as a signed percentage; neither is ever inferred.
PRICE = "price"
PERCENT = "percent"
UNITS: frozenset[str] = frozenset({PRICE, PERCENT})


def _column(field: str, unit: str, label: str) -> Mapping[str, str]:
    return MappingProxyType({"field": field, "unit": unit, "label": label})


#: Public key → row field, unit and human label. **Order is the picker's order**
#: (shortest window first, then momentum and week-to-date), and the TypeScript
#: mirror in ``web/src/grid/computedColumns.ts`` is asserted equal to it.
COMPUTED_COLUMNS: Mapping[str, Mapping[str, str]] = MappingProxyType(
    {
        # -- exponential moving averages, price units
        "ema_13": _column("ema_13", PRICE, "EMA 13"),
        "ema_21": _column("ema_21", PRICE, "EMA 21"),
        "ema_50": _column("ema_50", PRICE, "EMA 50"),
        "ema_100": _column("ema_100", PRICE, "EMA 100"),
        "ema_200": _column("ema_200", PRICE, "EMA 200"),
        # -- simple moving averages, price units
        "sma_13": _column("sma_13", PRICE, "SMA 13"),
        "sma_21": _column("sma_21", PRICE, "SMA 21"),
        "sma_100": _column("sma_100", PRICE, "SMA 100"),
        # -- TradingView ``Mom`` (close minus close 10 sessions back), a price
        #    difference; the field carries the window so no caller has to guess.
        "momentum": _column("momentum_10d", PRICE, "Momentum (price change, 10d)"),
        # -- TradingView ``Perf.W``, week-to-date, percent points. The row field
        #    is the parity scan's existing ``ret_1w``.
        "perf_wtd": _column("ret_1w", PERCENT, "Performance (week to date)"),
    }
)

#: The keys, in registry order — what ``columns`` validates against and what the
#: picker lists.
COMPUTED_KEYS: tuple[str, ...] = tuple(COMPUTED_COLUMNS)


def is_computed(name: object) -> bool:
    """Is ``name`` one of the ten public keys?"""

    return isinstance(name, str) and name in COMPUTED_COLUMNS


def field_for(name: object) -> str | None:
    """The row field ``name`` reads, or ``None`` when it is not a computed key."""

    if not isinstance(name, str):
        return None
    entry = COMPUTED_COLUMNS.get(name)
    return entry["field"] if entry is not None else None


def value_for(row: Mapping[str, Any], name: str) -> float | None:
    """One computed cell as a finite number, or ``None``.

    A missing cell, a string, a bool, ``NaN`` and ``inf`` are all *unknown*:
    the API publishes numbers or null, never a coerced zero. A real ``0.0``
    (a price exactly on its moving average) stays ``0.0``.
    """

    field = field_for(name)
    if field is None:
        return None
    value = row.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value):
        return None
    return float(value)


def _validate() -> None:
    """Fail at import on a malformed registry (no runtime surprises)."""

    fields: set[str] = set()
    for key, entry in COMPUTED_COLUMNS.items():
        if set(entry) != {"field", "unit", "label"}:
            raise RuntimeError(f"computed column {key!r} must declare field/unit/label")
        if not key.strip() or not entry["field"].strip() or not entry["label"].strip():
            raise RuntimeError(f"computed column {key!r} has a blank field, unit or label")
        if entry["unit"] not in UNITS:
            raise RuntimeError(f"computed column {key!r} has unit {entry['unit']!r}")
        if entry["field"] in fields:
            raise RuntimeError(f"computed column {key!r} reuses row field {entry['field']!r}")
        fields.add(entry["field"])


_validate()


__all__ = [
    "COMPUTED_COLUMNS",
    "COMPUTED_KEYS",
    "PERCENT",
    "PRICE",
    "UNITS",
    "field_for",
    "is_computed",
    "value_for",
]
