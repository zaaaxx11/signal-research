"""Fill-only-empty conflation with trust ordering (02 §3.5, §1.6).

Two rules that look similar and are not:

* **Fill-only-empty** — a cell that already holds a value is not overwritten by
  a later arrival of the same standing. This is what stops phase P4 from
  spending a provider request to replace a number it already has, and what makes
  the first writer's attribution survive.
* **Trust ordering** — a *better* source wins even when it arrives last. Trust
  is the 1..4 scale of 02 §4.6 (1 official, 2 vendor, 3 aggregator, 4 scrape),
  stored per observation so conflation never has to guess from a provider name.

Equal trust does not overwrite: two vendors disagreeing is a dispute, and
picking the later one would silently turn "last writer" into "most credible".
The one exception is the verified tie-break — a verified observation beats an
unverified one at the same trust (02 §3.5), because a check ran.

An empty observation (``None``/``""``/whitespace) never writes a cell and never
creates a provenance record: it observed nothing, and provenance for nothing is
a lie about where a value came from.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from app.contracts import CANONICAL_COLUMNS_V1
from app.enrich.provenance import (
    make_entry,
    provenance,
    set_entry,
    status_for_verdict,
)
from app.enrich.quality import is_filled

_COLUMNS: frozenset[str] = frozenset(CANONICAL_COLUMNS_V1)


def _field(observation: Any, name: str, default: Any = None) -> Any:
    """Read one field from a mapping or an ``Observation`` model alike."""

    if isinstance(observation, Mapping):
        return observation.get(name, default)
    return getattr(observation, name, default)


def _value(observation: Any) -> Any:
    """The cell value an observation carries: number first, then text."""

    value = _field(observation, "value_num")
    if value is None:
        value = _field(observation, "value_text")
    return value


def entry_for(observation: Any, *, column: str | None = None) -> dict[str, Any]:
    """Build the provenance record for one observation."""

    source = _field(observation, "source")
    as_of = _field(observation, "as_of")
    discovery = _field(observation, "verdict", _field(observation, "status", "ok"))
    return make_entry(
        source=str(source) if source is not None else "",
        as_of=as_of,
        status=status_for_verdict(discovery),
        trust=_field(observation, "trust", 4),
        verified=bool(_field(observation, "verified", False)),
        source_id=_field(observation, "source_id"),
        confidence=_field(observation, "confidence"),
    )


def beats(candidate: Mapping[str, Any], incumbent: Mapping[str, Any]) -> bool:
    """Does ``candidate`` outrank ``incumbent``? Trust first, then verification."""

    candidate_trust = int(candidate["trust"])
    incumbent_trust = int(incumbent["trust"])
    if candidate_trust < incumbent_trust:
        return True
    if candidate_trust > incumbent_trust:
        return False
    return bool(candidate.get("verified")) and not bool(incumbent.get("verified"))


def write_if_empty(
    row: dict[str, Any], column: str, value: Any, observation: Any
) -> bool:
    """Write ``value`` into ``column`` when the rules allow it; return whether it did.

    Raises ``ValueError`` for a column outside the frozen 92: a typo must not
    create a 93rd key that the export contract cannot describe.
    """

    if column not in _COLUMNS:
        raise ValueError(
            f"column {column!r} is not one of the 92 canonical columns; "
            "conflation may not widen the export contract"
        )
    if not is_filled(value):
        return False

    record = provenance(row)
    incumbent = record.get(column)
    if is_filled(row.get(column)):
        # A filled cell with no record is not ours to overwrite: fill-only-empty
        # is the primary rule, and unknown trust is not "trust zero".
        if incumbent is None or not beats(entry_for(observation), incumbent):
            return False

    set_entry(row, column, entry_for(observation))
    row[column] = value
    return True


def merge(row: dict[str, Any], observations: Iterable[Any]) -> dict[str, Any]:
    """Apply observations in arrival order to ``row`` and return that same row."""

    for observation in observations:
        column = _field(observation, "field")
        if not column:
            continue
        write_if_empty(row, str(column), _value(observation), observation)
    return row


__all__ = ["beats", "entry_for", "merge", "write_if_empty"]
