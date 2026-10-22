"""The phase table: enrichment declared as data, not as ``if`` in a runner (02 §3.5).

Eight phases, P0..P7. Each one owns a disjoint slice of the frozen 92-column
contract, so "which phase fills this cell" has exactly one answer — the property
that makes provenance, conflation and a per-phase budget report possible at all.

The partition is not decoration: it is asserted by ``tests/test_phases.py``
(18 + 15 + 12 + 9 + 22 + 10 + 6 + 0 = 92, no overlap) and used by the
orchestrator to attribute a filled cell to the phase that owns it.

Two phases are fatal:

* ``reference`` (P0) — a row without identity is not a row, it is noise;
* ``export`` (P7) — a job whose table never reached disk has no deliverable.

Everything in between degrades to empty cells. A failed quote must not throw
away a reference row: empty is a fact, a missing row is data loss.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.contracts import CANONICAL_COLUMNS_V1, Capability, columns_in_group

_COLUMNS: frozenset[str] = frozenset(CANONICAL_COLUMNS_V1)


@dataclass(frozen=True, slots=True)
class Phase:
    """One enrichment phase: a name, a capability, its columns, its fail policy.

    ``capability is None`` means the phase derives its cells from data an earlier
    phase already fetched (performance from EOD bars, quality from the row,
    export from the table) and therefore spends no provider request.

    A non-canonical column is a programming error and raises ``ValueError`` at
    construction time — a phase table that can name a 93rd column would let the
    vocabulary drift one phase at a time.
    """

    name: str
    capability: Capability | None
    columns: tuple[str, ...] = ()
    fatal: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("Phase.name must be a non-empty string")

        capability = self.capability
        if isinstance(capability, str) and not isinstance(capability, Capability):
            try:
                capability = Capability(capability)
            except ValueError as exc:
                raise ValueError(f"phase {self.name!r}: unknown capability {capability!r}") from exc
            object.__setattr__(self, "capability", capability)

        columns = tuple(self.columns)
        unknown = sorted(set(columns) - _COLUMNS)
        if unknown:
            raise ValueError(
                f"phase {self.name!r} declares non-canonical column(s): "
                + ", ".join(unknown)
                + "; the export contract is v1 (92 columns)"
            )
        if len(set(columns)) != len(columns):
            raise ValueError(f"phase {self.name!r} declares the same column twice")
        object.__setattr__(self, "columns", columns)

    def owns(self, column: str) -> bool:
        """True when this phase is the one responsible for ``column``."""

        return column in self.columns


#: P0..P7 of 02 §3.5, in execution order.
DEFAULT_PHASES: tuple[Phase, ...] = (
    Phase(
        name="reference",
        capability=Capability.REFERENCE,
        columns=columns_in_group("Identity") + columns_in_group("Classification"),
        fatal=True,
    ),
    Phase(
        name="quote",
        capability=Capability.QUOTE,
        columns=columns_in_group("Quote") + columns_in_group("Market/session"),
    ),
    Phase(
        name="eod",
        capability=Capability.EOD_BARS,
        columns=columns_in_group("EOD & technical"),
    ),
    Phase(
        name="performance",
        capability=None,
        columns=columns_in_group("Performance"),
    ),
    Phase(
        name="fundamentals",
        capability=Capability.FUNDAMENTALS,
        columns=columns_in_group("Fundamentals") + columns_in_group("Statements"),
    ),
    Phase(
        name="options",
        capability=Capability.OPTIONS,
        columns=columns_in_group("Options"),
    ),
    Phase(
        name="verify",
        capability=None,
        columns=columns_in_group("Quality"),
    ),
    Phase(name="export", capability=None, columns=(), fatal=True),
)

#: ``column -> phase name``. Built from :data:`DEFAULT_PHASES`, so it cannot drift.
COLUMN_PHASE: dict[str, str] = {
    column: phase.name for phase in DEFAULT_PHASES for column in phase.columns
}


def _assert_partition() -> None:
    """Fail at import if the phase table stops covering the contract exactly."""

    covered = [column for phase in DEFAULT_PHASES for column in phase.columns]
    if len(covered) != len(set(covered)) or set(covered) != set(CANONICAL_COLUMNS_V1):
        raise RuntimeError(
            "DEFAULT_PHASES must partition CANONICAL_COLUMNS_V1: "
            f"{len(covered)} entries, {len(set(covered))} unique, "
            f"{len(CANONICAL_COLUMNS_V1)} canonical"
        )


_assert_partition()


def phase_named(name: str) -> Phase:
    """Return the default phase called ``name`` (``KeyError`` when absent)."""

    for phase in DEFAULT_PHASES:
        if phase.name == name:
            return phase
    raise KeyError(f"unknown phase {name!r}; DEFAULT_PHASES are {[p.name for p in DEFAULT_PHASES]}")


def phase_of_column(column: str) -> str | None:
    """The phase that owns ``column``, or ``None`` for a non-canonical name."""

    return COLUMN_PHASE.get(column)


def fatal_phases() -> frozenset[str]:
    """Names of the phases whose failure fails the job (02 §3.5)."""

    return frozenset(phase.name for phase in DEFAULT_PHASES if phase.fatal)


__all__ = [
    "COLUMN_PHASE",
    "DEFAULT_PHASES",
    "Phase",
    "fatal_phases",
    "phase_named",
    "phase_of_column",
]
