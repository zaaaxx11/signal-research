"""The canonical row: identity, quote, SEC facts and audited derivations.

A row is the engine's unit of evidence: one instrument, every value still
attached to the observation that produced it. :func:`build_row` is the only
place the four layers are joined, and it joins them in one fixed order:

1. **identity** — the six catalog fields the security record can answer
   (``ticker``, ``company``, ``sector``, ``industry``, ``country``,
   ``exchange``);
2. **quote** — the five market numbers in :data:`QUOTE_FIELDS`;
3. **facts** — the SEC concepts in :data:`FACT_TO_METRIC`, copied untouched;
4. **derivations** — the multiples and totals that can be computed from what
   the first three layers already hold.

Later layers never overwrite earlier ones. A derived market capitalisation
cannot displace a quote's market capitalisation, and a fact cannot displace a
quote: observation beats inference, always. Derived values only fill a key that
is still empty, which is why :func:`_fill` exists instead of ``row[key] =``.

Identity is never invented
--------------------------
A field the record does not carry (``sector``, ``industry``, ``country``,
``exchange``) is **omitted**, not written as ``None`` and not filled from a
guess. The record's spelling is kept verbatim: a ``BRK.B`` ticker stays
``BRK.B`` because the identity slice, not this module, owns symbol
normalisation.

Provenance cannot exist without a date, and this module has no clock, so
``as_of`` is a required keyword: the date the caller observed the row. The
assembler will not borrow today's date for a value it did not fetch.

Quote
-----
:data:`QUOTE_FIELDS` is the whole observed vocabulary: ``price``,
``prev_close``, ``change``, ``volume`` and ``market_cap``. Their domains are
economic, not numerical:

* ``price``, ``prev_close`` and ``market_cap`` must be strictly positive — a
  zero price is a halted or absent quote, not a company worth nothing;
* ``volume`` must be non-negative, and a reported ``0`` is kept: a session with
  no trades is a measurement;
* ``change`` is signed and may be zero or negative; it is percentage points,
  matching the catalog's ``Unit.PERCENT``.

Anything that is not a finite number (``None``, ``NaN``, ``±inf``, a bool, or a
numeric string such as ``"262.82"``) is **omitted**. Nothing is parsed and
nothing falls back to zero: a missing price must stay missing, because a zero
is indistinguishable from a real one once it is on the row.

A quote's ``as_of`` dates the quote and wins over the keyword; text is
required, so a caller's numeric timestamp falls back rather than becoming a
provenance string. The quote may name its instrument (``symbol`` or
``ticker``); when that name is not the record's symbol the row would attach
another company's price to this identity, so it raises
:data:`UNKNOWN_SYMBOL`.

Four more keys are read but never published: ``eps_forward``, ``growth``,
``book_per_share`` and ``sales_per_share`` are the per-share and estimate
inputs a caller asserts for a derivation. They do not become cells because the
row's column vocabulary is the catalog and the assembler cannot say more about
them than "the quote said so" — which is exactly what the derived cell records.
Any other quote key is ignored, not copied.

Facts
-----
:data:`FACT_TO_METRIC` is deliberately short. A fact fills a column only when
the two names mean the same measurement:

============================  ==============  ==============================
fact key (``fact_summary``)   catalog key     why it is exact
============================  ==============  ==============================
``revenue``                   ``sales``       total revenue, absolute USD
``net_income``                ``net_income``  net income, absolute USD
``eps_diluted``               ``eps``         diluted earnings per share
``shares_outstanding``        ``shares_outstanding``  share count
============================  ==============  ==============================

The rejections matter as much as the mappings. ``equity`` does **not** fill
``book_per_share``: total stockholders' equity is a balance, and a per-share
book value would require dividing it by a share count — a derivation this
module does not claim and the catalog does not name. ``net_income`` is **not**
renamed ``income``: the catalog's column is ``net_income``, and a second
spelling would be a second vocabulary. ``assets``, ``liabilities``,
``cash_and_equivalents``, ``total_debt``, ``operating_cash_flow`` and ``capex``
have no catalog column at all; two of them (the cash-flow pair) are read only
to derive ``fcf``. A fact under a catalog key that is not in the mapping — a
stray ``price`` or ``pe`` — is ignored, so a facts mapping can never smuggle a
number into a field it does not own.

A fact cell is copied by identity: its value, unit, ``as_of``, tag and
accession stay exactly as the filing produced them. The assembler re-dates
nothing. Per-share amounts keep ``Unit.UNKNOWN``, the verdict ``facts``
documented for XBRL's ``USD/shares`` (the engine has no per-share unit);
relabelling them ``USD`` would be a claim the evidence does not support. A
null cell is no evidence and is skipped.

Units: a mismatch raises
------------------------
Every fact the assembler reads declares the units that can fill its target:
``USD`` for money, ``SHARES`` for a share count, and ``USD`` *or* ``UNKNOWN``
for ``eps_diluted`` (the per-share bridge above). A non-null cell in any other
unit raises :class:`RowBuildError` with code :data:`UNIT_MISMATCH`; nothing is
converted, and nothing is silently dropped.

This module raises where :mod:`app.engine.reconcile` degrades, and the
difference is the kind of problem. Reconciliation resolves *disagreement*
between sources for one metric and must keep the row alive; here the mapping
from fact key to column is a fixed table, and ``facts`` can only emit dollars,
shares or ``UNKNOWN``. A mismatch therefore means the table and the evidence
were wired together wrongly — a bug, not a provider quirk. Dropping the column
would turn that bug into a permanent, unexplained hole. ``build_row`` assembles
one instrument; the caller that loops over a universe owns the decision to skip
a symbol.

Derivations
-----------
The formulas live in :mod:`app.engine.derived`; this module only chooses inputs
and attests them. Each derivation is attempted only when every input exists:

======================================  =====================================
cell                                    inputs
======================================  =====================================
``market_cap``                          row ``price``, fact ``shares_outstanding``
``pe``                                  row ``price``, row ``eps`` (TTM)
``forward_pe``                          row ``price``, quote ``eps_forward``
``peg``                                 row ``forward_pe``, quote ``growth``
``pb``                                  row ``price``, quote ``book_per_share``
``ps``                                  row ``price``, quote ``sales_per_share``
``fcf``                                 fact ``operating_cash_flow``, fact ``capex``
``earnings_yield``                      row ``pe``
======================================  =====================================

A derivation that cannot produce a finite number is **omitted**, never written
as a null placeholder and never as ``0``. The recipe for that absence (a
missing input, a non-positive denominator, an overflow) stays inside the
:class:`~app.engine.derived.DerivedValue`; the row simply has no key, which is
the same thing it says about a quote the caller did not supply.

A derived cell is dated by the number it divided, not by the build: the
price-based multiples carry the quote's ``as_of``, ``fcf`` carries the
operating-cash-flow fact's period end, and ``earnings_yield`` carries the
``pe`` cell's date. Nothing here invents a timestamp, and
:data:`INPUT_SOURCES` records which key or mapping every semantic input name in
a derived cell's provenance came from.

``fcf`` and ``earnings_yield`` are the two keys outside the catalog. The
catalog has no free-cash-flow column (only the ``pfcf`` multiple) and no
earnings-yield column, but both are arithmetic over evidence this row already
holds, so they are published under their own names and listed in
:data:`ROW_EXTENSION_KEYS`. A presenter that only knows the 116 columns simply
does not show them.

Purity
------
No network, no database, no clock, no file. The inputs are never mutated and
the returned cells are frozen; two calls with equal inputs return equal rows.
"""

from __future__ import annotations

import math
import numbers
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType
from typing import Any

from app.engine import derived
from app.engine.catalog import CATALOG_BY_KEY
from app.engine.derived import DerivedValue
from app.engine.model import Cell, CellStatus, Provenance, SecurityRecord, Unit
from app.engine.sources.sec import normalize_symbol

#: A payload named an instrument this row's identity is not.
UNKNOWN_SYMBOL = "unknown_symbol"
#: A non-null fact carried a unit its target field cannot accept.
UNIT_MISMATCH = "unit_mismatch"

#: The closed error vocabulary of :class:`RowBuildError`.
ERROR_CODES: frozenset[str] = frozenset({UNKNOWN_SYMBOL, UNIT_MISMATCH})

#: Catalog key → :class:`SecurityRecord` attribute. Insertion order is the
#: row's presentation order for identity.
IDENTITY_FIELDS: Mapping[str, str] = MappingProxyType(
    {
        "ticker": "symbol",
        "company": "name",
        "sector": "sector",
        "industry": "industry",
        "country": "country",
        "exchange": "exchange",
    }
)

#: The quote's observed vocabulary, in presentation order.
QUOTE_FIELDS: tuple[str, ...] = (
    "price",
    "prev_close",
    "change",
    "volume",
    "market_cap",
)

#: ``fact_summary`` key → catalog key, only where the two are the same
#: measurement. See the module docstring for the documented rejections.
FACT_TO_METRIC: Mapping[str, str] = MappingProxyType(
    {
        "revenue": "sales",
        "net_income": "net_income",
        "eps_diluted": "eps",
        "shares_outstanding": "shares_outstanding",
    }
)

#: Semantic input name (as :mod:`app.engine.derived` names it) → where this
#: module reads it. Every name in a derived cell's provenance appears here.
INPUT_SOURCES: Mapping[str, str] = MappingProxyType(
    {
        "price": "row:price",
        "shares": "row:shares_outstanding",
        "eps_ttm": "row:eps",
        "eps_forward": "quote:eps_forward",
        "forward_pe": "row:forward_pe",
        "growth_pct": "quote:growth",
        "book_per_share": "quote:book_per_share",
        "sales_per_share": "quote:sales_per_share",
        "operating_cash_flow": "facts:operating_cash_flow",
        "capex": "facts:capex",
        "pe": "row:pe",
    }
)

#: Derived keys the 116-column catalog does not name, with the unit each is
#: expressed in. Everything else the assembler emits is a catalog key.
ROW_EXTENSION_KEYS: frozenset[str] = frozenset({"fcf", "earnings_yield"})

#: Units a quote field must be positive in (``change`` is signed, ``volume`` is
#: non-negative; see the module docstring).
_POSITIVE_QUOTE_FIELDS: frozenset[str] = frozenset(
    {"price", "prev_close", "market_cap"}
)

#: Every fact key this module reads → the units that may fill it. The list is
#: wider than :data:`FACT_TO_METRIC` because ``fcf`` reads two cash-flow facts
#: that have no catalog column of their own.
_READ_FACT_UNITS: Mapping[str, frozenset[Unit]] = MappingProxyType(
    {
        "revenue": frozenset({Unit.USD}),
        "net_income": frozenset({Unit.USD}),
        # Per-share amounts arrive as Unit.UNKNOWN from ``facts``; the bridge
        # is documented in the module docstring and is the only one here.
        "eps_diluted": frozenset({Unit.USD, Unit.UNKNOWN}),
        "shares_outstanding": frozenset({Unit.SHARES}),
        "operating_cash_flow": frozenset({Unit.USD}),
        "capex": frozenset({Unit.USD}),
    }
)

_EXTENSION_UNITS: Mapping[str, Unit] = MappingProxyType(
    {
        "fcf": Unit.USD,
        "earnings_yield": Unit.PERCENT,
    }
)


@dataclass(frozen=True)
class RowBuildError(ValueError):
    """A row could not be assembled from the evidence it was handed.

    ``code`` is one of :data:`ERROR_CODES`. The exception is a ``ValueError``
    (it is a bad input, not a bug in the interpreter) with a frozen ``code``
    and ``message``, and ``str(error)`` is the message so a log line needs no
    unpacking.
    """

    code: str
    message: str

    def __post_init__(self) -> None:
        ValueError.__init__(self, self.message)


def build_row(
    security: SecurityRecord,
    facts: Mapping[str, Cell],
    quote: Mapping[str, object] | None = None,
    *,
    as_of: str,
) -> dict[str, Cell]:
    """Assemble the canonical row for one instrument.

    ``facts`` is a :func:`~app.engine.facts.fact_summary` mapping (fact key →
    :class:`Cell`); ``quote`` is a mapping of market numbers with an optional
    ``as_of``. ``as_of`` is the caller's observation date and is required: the
    assembler has no clock and will not invent one.

    Returns a ``dict[str, Cell]`` in layer order (identity, quote, facts,
    derivations). Raises :class:`RowBuildError` with code :data:`UNIT_MISMATCH`
    when a fact carries the wrong unit for its target, and with
    :data:`UNKNOWN_SYMBOL` when the quote names a different instrument;
    ``TypeError`` when an argument is not the declared type; ``ValueError``
    when ``as_of`` is blank.
    """

    if not isinstance(security, SecurityRecord):
        raise TypeError(
            f"build_row needs a SecurityRecord, got {type(security).__name__}"
        )
    if not isinstance(facts, Mapping):
        raise TypeError(
            "facts must be a mapping of fact key to Cell, got "
            f"{type(facts).__name__}"
        )
    quote_map: Mapping[str, object] = {} if quote is None else quote
    if not isinstance(quote_map, Mapping):
        raise TypeError(
            "quote must be a mapping of field to number, got "
            f"{type(quote_map).__name__}"
        )

    build_date = _observation_date(as_of)
    quote_date = _quote_date(quote_map, build_date)
    _guard_quote_symbol(security, quote_map)

    row: dict[str, Cell] = {}

    for key, attribute in IDENTITY_FIELDS.items():
        value = getattr(security, attribute)
        if isinstance(value, str) and value.strip():
            row[key] = _observed(key, value.strip(), "identity", build_date)

    for key in QUOTE_FIELDS:
        number = _quote_number(quote_map, key)
        if number is not None:
            row[key] = _observed(key, number, "quote", quote_date)

    fact_cells = _read_facts(facts)
    for fact_key, target in FACT_TO_METRIC.items():
        cell = fact_cells.get(fact_key)
        if cell is not None:
            row[target] = cell

    _add_derivations(row, fact_cells, quote_map, quote_date)
    return row


# ---------------------------------------------------------------------------
# Dates and the quote's identity guard
# ---------------------------------------------------------------------------


def _observation_date(value: Any) -> str:
    """The caller's non-blank observation date, or :class:`ValueError`."""

    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            "as_of must be a non-blank observation date; the assembler does "
            "not have a clock"
        )
    return value.strip()


def _quote_date(quote: Mapping[str, object], fallback: str) -> str:
    """The quote's own ``as_of`` when it is text, else the build date."""

    value = quote.get("as_of")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return fallback


def _guard_quote_symbol(security: SecurityRecord, quote: Mapping[str, object]) -> None:
    """Refuse a quote that names an instrument this row is not.

    Both ``symbol`` and ``ticker`` are accepted as the quote's identity, in any
    spelling :func:`~app.engine.sources.sec.normalize_symbol` understands. A
    payload that names a different instrument would attach another company's
    price to this identity, which is the one mistake a row must not make
    silently.
    """

    for field in ("symbol", "ticker"):
        if field not in quote:
            continue
        named = quote[field]
        if named is None:
            continue
        if normalize_symbol(named) != normalize_symbol(security.symbol):
            raise RowBuildError(
                UNKNOWN_SYMBOL,
                f"quote names {named!r} but the row is {security.symbol!r}",
            )


# ---------------------------------------------------------------------------
# Numbers
# ---------------------------------------------------------------------------


def _finite(value: Any) -> float | None:
    """``value`` as a finite float, or ``None`` when it is not a measurement.

    ``bool`` is rejected (a flag is not a quantity), a string is not parsed
    (formatting is not arithmetic) and ``Decimal`` is narrowed at the boundary,
    matching :mod:`app.engine.derived`.
    """

    if value is None or isinstance(value, bool):
        return None
    if not isinstance(value, (numbers.Real, Decimal)):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _quote_number(quote: Mapping[str, object], key: str) -> float | None:
    """One observed quote number, or ``None`` when it is absent or unusable."""

    number = _finite(quote.get(key))
    if number is None:
        return None
    if key in _POSITIVE_QUOTE_FIELDS and number <= 0.0:
        return None
    if key == "volume" and number < 0.0:
        return None
    return number


def _row_number(row: Mapping[str, Cell], key: str) -> float | None:
    """One already-built cell's value as a finite float, or ``None``."""

    cell = row.get(key)
    return None if cell is None else _finite(cell.value)


def _cell_number(cell: Cell | None) -> float | None:
    """A fact cell's value as a finite float, or ``None``."""

    return None if cell is None else _finite(cell.value)


def _cell_as_of(cell: Cell | None) -> str | None:
    """The date a cell was observed on, or ``None`` when it carries none."""

    if cell is None or cell.provenance is None:
        return None
    return cell.provenance.as_of


# ---------------------------------------------------------------------------
# Facts
# ---------------------------------------------------------------------------


def _read_facts(facts: Mapping[str, Cell]) -> dict[str, Cell]:
    """The fact cells this module may read, unit-checked and still untouched.

    Only :data:`_READ_FACT_UNITS` keys are inspected; anything else in the
    mapping is ignored rather than copied. A null cell is skipped (no evidence,
    no mismatch), a non-``Cell`` or non-finite value is a caller bug and raises
    ``TypeError``, and a value in a unit the target cannot honour raises
    :data:`UNIT_MISMATCH` before any row exists.
    """

    cells: dict[str, Cell] = {}
    for fact_key, accepted in _READ_FACT_UNITS.items():
        cell = facts.get(fact_key)
        if cell is None:
            continue
        if not isinstance(cell, Cell):
            raise TypeError(
                f"facts[{fact_key!r}] must be a Cell, got {type(cell).__name__}"
            )
        if cell.value is None:
            continue
        if _finite(cell.value) is None:
            raise TypeError(
                f"facts[{fact_key!r}] must carry a finite number, got "
                f"{cell.value!r}"
            )
        if cell.unit not in accepted:
            raise RowBuildError(
                UNIT_MISMATCH, _unit_mismatch_message(fact_key, cell.unit, accepted)
            )
        cells[fact_key] = cell
    return cells


def _unit_mismatch_message(
    fact_key: str, unit: Unit, accepted: frozenset[Unit]
) -> str:
    """A message that names the fact, its target, and both units."""

    target = FACT_TO_METRIC.get(fact_key)
    where = f"cannot fill {target!r}" if target is not None else "cannot be read"
    expected = " or ".join(sorted(candidate.value for candidate in accepted))
    return f"fact {fact_key!r} {where}: expected {expected}, got {unit.value}"


# ---------------------------------------------------------------------------
# Cells
# ---------------------------------------------------------------------------


def _unit_for(key: str) -> Unit:
    """The canonical unit of a row key, catalog first, extension second."""

    spec = CATALOG_BY_KEY.get(key)
    if spec is not None:
        return spec.unit
    unit = _EXTENSION_UNITS.get(key)
    if unit is None:
        raise KeyError(f"no canonical unit for row key {key!r}")
    return unit


def _observed(key: str, value: Any, source: str, as_of: str) -> Cell:
    """An observed cell: catalog unit, one source, one date, no derivation."""

    return Cell(
        value=value,
        unit=_unit_for(key),
        provenance=Provenance(source=source, as_of=as_of, status=CellStatus.OK),
    )


def _derived_cell(value: DerivedValue, as_of: str) -> Cell | None:
    """A successful derivation as a ``DERIVED`` cell; ``None`` when absent.

    An empty derivation is omitted rather than published as a null placeholder.
    A value carrying :data:`~app.engine.derived.ASSUMED_ZERO` is also withheld:
    a :class:`Cell` has no slot for the caveat, and a number whose optional
    input was defaulted to zero is exactly the plausible-looking value this
    engine refuses to publish silently.
    """

    if value.value is None or value.assumed:
        return None
    return Cell(
        value=value.value,
        unit=_unit_for(value.key),
        provenance=Provenance(
            source="derived",
            as_of=as_of,
            status=CellStatus.DERIVED,
            inputs=value.inputs,
        ),
    )


def _fill(row: dict[str, Cell], key: str, cell: Cell | None) -> None:
    """Add a derived cell only into a gap; an observation always wins."""

    if cell is None or key in row:
        return
    row[key] = cell


# ---------------------------------------------------------------------------
# Derivations
# ---------------------------------------------------------------------------


def _add_derivations(
    row: dict[str, Cell],
    fact_cells: Mapping[str, Cell],
    quote: Mapping[str, object],
    quote_date: str,
) -> None:
    """Attempt every derivation whose inputs are present; fill only gaps.

    The inputs are read from the cells the row already holds (so a derived
    number is computed from evidence, not from a second copy of the inputs) and
    from the four quote extras documented in :data:`INPUT_SOURCES`.
    """

    price = _row_number(row, "price")
    shares = _row_number(row, "shares_outstanding")
    eps = _row_number(row, "eps")
    eps_forward = _finite(quote.get("eps_forward"))
    growth = _finite(quote.get("growth"))
    book_per_share = _finite(quote.get("book_per_share"))
    sales_per_share = _finite(quote.get("sales_per_share"))

    _fill(
        row,
        "market_cap",
        _derived_cell(derived.market_cap(price, shares), quote_date),
    )
    _fill(row, "pe", _derived_cell(derived.pe_ratio(price, eps), quote_date))
    _fill(
        row,
        "forward_pe",
        _derived_cell(derived.forward_pe(price, eps_forward), quote_date),
    )

    forward_as_of = _cell_as_of(row.get("forward_pe"))
    if forward_as_of is not None:
        _fill(
            row,
            "peg",
            _derived_cell(
                derived.peg(_row_number(row, "forward_pe"), growth),
                forward_as_of,
            ),
        )

    _fill(
        row,
        "pb",
        _derived_cell(derived.price_to_book(price, book_per_share), quote_date),
    )
    _fill(
        row,
        "ps",
        _derived_cell(derived.price_to_sales(price, sales_per_share), quote_date),
    )

    cash_flow = fact_cells.get("operating_cash_flow")
    capex = fact_cells.get("capex")
    cash_flow_date = _cell_as_of(cash_flow)
    if cash_flow_date is not None:
        _fill(
            row,
            "fcf",
            _derived_cell(
                derived.fcf(_cell_number(cash_flow), _cell_number(capex)),
                cash_flow_date,
            ),
        )

    pe_date = _cell_as_of(row.get("pe"))
    if pe_date is not None:
        _fill(
            row,
            "earnings_yield",
            _derived_cell(derived.earnings_yield(_row_number(row, "pe")), pe_date),
        )


__all__ = [
    "ERROR_CODES",
    "FACT_TO_METRIC",
    "IDENTITY_FIELDS",
    "INPUT_SOURCES",
    "QUOTE_FIELDS",
    "ROW_EXTENSION_KEYS",
    "RowBuildError",
    "UNIT_MISMATCH",
    "UNKNOWN_SYMBOL",
    "build_row",
]
