"""Deterministic reconciliation of one metric observed by several sources.

Rule 1 of the engine is that a value is never detached from its origin; rule 2
is that the engine never hides a disagreement. This module is where both rules
are enforced for the cross-source case:

* A caller supplies every observation as ``(source, Cell)`` and an ordered
  ``priority`` of source names. The first ranked source that actually carries a
  value wins — not the first source that answered.
* The winner is returned untouched. Its :class:`~app.engine.model.Provenance`
  is the evidence for the number, so it is never rewritten, averaged or
  re-dated here.
* Disagreement is reported, never resolved silently: ``conflict`` is true when
  the non-null values differ (numerically beyond ``tolerance``, or at all for
  text), while the chosen value is still returned.
* ``delta`` is the spread of absolute magnitudes, so a caller can rank
  contested cells by materiality without re-reading every source.

Absence is a first-class outcome. Missing values are dropped before ranking and
never replaced by ``0``; when nothing is left the result is a null cell with
:attr:`~app.engine.model.CellStatus.ABSENT`. A unit clash is a data problem, not
a caller problem, so it degrades one cell to ``UNIT_MISMATCH`` instead of
raising: a screening row must not fail because one adapter printed percentages
where another printed ratios. Only a malformed *request* (no ranking at all, or
a blank source name inside it) raises :class:`ReconcileError`.

``priority`` is a preference order, not a licence gate. If no ranked source
carries a value but an unranked one does, the first non-null observation in
supplied order is returned so evidence is not discarded; the caller still sees
``sources`` and the exact provenance and can apply the licence check itself.
"""

from __future__ import annotations

import math
import numbers
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.engine.model import Cell, CellStatus, Provenance, Unit


class ReconcileError(ValueError):
    """A malformed reconciliation request.

    Never raised for the data in the observations: unit clashes and total
    absence are reported through the returned :class:`ReconcileResult`.
    """


@dataclass(frozen=True)
class ReconcileResult:
    """One reconciled cell plus the evidence needed to audit the choice."""

    cell: Cell
    agreement: bool
    conflict: bool
    sources: tuple[str, ...]
    delta: float | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "sources", tuple(self.sources))


def choose(
    metric: str,
    observations: Sequence[tuple[str, Cell]],
    *,
    priority: Sequence[str],
    tolerance: float = 1e-9,
) -> ReconcileResult:
    """Reconcile ``observations`` into one cell under ``priority``.

    ``metric`` is the catalog key the observations belong to; it appears in
    error messages only. ``tolerance`` is an absolute difference in the metric's
    own unit. ``sources`` in the result lists every source consulted, in the
    order supplied, deduplicated by name.
    """

    ranked = _ranked_names(priority, metric)
    pairs = tuple(observations)
    sources = _source_names(pairs)
    valued = tuple((name, cell) for name, cell in pairs if cell.value is not None)

    if not valued:
        return ReconcileResult(
            cell=_null_cell(pairs, CellStatus.ABSENT, _first_unit(pairs)),
            agreement=False,
            conflict=False,
            sources=sources,
            delta=None,
        )

    if len({_unit_key(cell.unit) for _, cell in valued}) > 1:
        return ReconcileResult(
            cell=_null_cell(pairs, CellStatus.UNIT_MISMATCH, Unit.UNKNOWN),
            agreement=False,
            conflict=True,
            sources=sources,
            delta=None,
        )

    values = [cell.value for _, cell in valued]
    agreed = _values_agree(values, tolerance)
    return ReconcileResult(
        cell=_chosen(valued, ranked),
        agreement=agreed,
        conflict=not agreed,
        sources=sources,
        delta=_delta(values),
    )


def _ranked_names(priority: Sequence[str], metric: str) -> tuple[str, ...]:
    """Validate the request and return the priority list as clean labels."""

    if isinstance(priority, (str, bytes)):
        raise ReconcileError(
            f"priority for metric {metric!r} must be a sequence of source names, "
            "not a single string"
        )
    names = tuple(priority)
    if not names:
        raise ReconcileError(f"priority for metric {metric!r} must not be empty")
    ranked: list[str] = []
    for name in names:
        label = _label(name)
        if not label:
            raise ReconcileError(
                f"priority for metric {metric!r} contains a blank source name"
            )
        ranked.append(label)
    return tuple(ranked)


def _label(source: object) -> str:
    return source.strip() if isinstance(source, str) else str(source).strip()


def _source_names(observations: Sequence[tuple[str, Cell]]) -> tuple[str, ...]:
    """Every source consulted, first spelling wins, order preserved."""

    seen: set[str] = set()
    ordered: list[str] = []
    for name, _cell in observations:
        label = _label(name)
        key = label.casefold()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(label)
    return tuple(ordered)


def _chosen(
    valued: Sequence[tuple[str, Cell]], ranked: Sequence[str]
) -> Cell:
    """First ranked source with a value; otherwise the first value supplied."""

    first_by_source: dict[str, Cell] = {}
    for name, cell in valued:
        first_by_source.setdefault(_label(name).casefold(), cell)
    for name in ranked:
        cell = first_by_source.get(name.casefold())
        if cell is not None:
            return cell
    return valued[0][1]


def _unit_key(unit: Any) -> str:
    """Comparable identity for a unit, tolerating a raw string from an adapter."""

    if isinstance(unit, Unit):
        return unit.value
    try:
        return Unit(unit).value
    except (TypeError, ValueError):
        return f"raw:{unit!r}"


def _first_unit(observations: Sequence[tuple[str, Cell]]) -> Unit:
    return observations[0][1].unit if observations else Unit.UNKNOWN


def _null_cell(
    observations: Sequence[tuple[str, Cell]], status: CellStatus, unit: Unit
) -> Cell:
    """A null cell that keeps the first origin but reports why it is empty.

    ``as_of`` is copied, never invented: an empty observation list becomes the
    explicit ``none``/``unknown`` pair rather than a plausible-looking date.
    """

    origin = next(
        (cell.provenance for _name, cell in observations if cell.provenance is not None),
        None,
    )
    if origin is None:
        provenance = Provenance(source="none", as_of="unknown", status=status)
    else:
        provenance = Provenance(
            source=origin.source,
            as_of=origin.as_of,
            status=status,
            url=origin.url,
            inputs=origin.inputs,
        )
    return Cell(value=None, unit=unit, provenance=provenance)


def _is_numeric(value: Any) -> bool:
    """Numbers compare by tolerance; ``bool`` is a flag, not a quantity."""

    if isinstance(value, bool):
        return False
    if isinstance(value, Decimal):
        return True
    return isinstance(value, numbers.Real)


def _values_agree(values: Sequence[Any], tolerance: float) -> bool:
    """True only when every non-null value equals the first one."""

    first = values[0]
    first_is_numeric = _is_numeric(first)
    for other in values[1:]:
        if _is_numeric(other) is not first_is_numeric:
            return False
        if first_is_numeric:
            if not _close(first, other, tolerance):
                return False
        elif other != first:
            return False
    return True


def _close(left: Any, right: Any, tolerance: float) -> bool:
    """Absolute comparison that survives mixed numeric types."""

    try:
        return bool(abs(left - right) <= tolerance)
    except (TypeError, ArithmeticError):
        try:
            return bool(abs(float(left) - float(right)) <= tolerance)
        except (TypeError, ValueError, ArithmeticError, OverflowError):
            return False


def _delta(values: Sequence[Any]) -> float | None:
    """Spread of absolute magnitudes, or ``None`` when the values are not numeric.

    ``-5`` and ``3`` are two points apart; ``-2`` and ``2`` are zero apart but
    still a conflict, which is why the spread never substitutes for
    ``conflict``. Non-finite magnitudes (``nan``, ``inf``) have no spread and
    produce ``None`` rather than a plausible-looking number.
    """

    if not values or not all(_is_numeric(value) for value in values):
        return None
    magnitudes: list[float] = []
    for value in values:
        try:
            magnitude = float(abs(value))
        except (TypeError, ValueError, ArithmeticError, OverflowError):
            return None
        if not math.isfinite(magnitude):
            return None
        magnitudes.append(magnitude)
    return max(magnitudes) - min(magnitudes)


__all__ = ["ReconcileError", "ReconcileResult", "choose"]
