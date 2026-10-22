"""The single vocabulary of the Market Enrichment Engine.

Architecture rule (02 §3.1): this module imports **no** other ``app.*`` module.
It is the only place where the 92-column table, the request contract, the
capability list and the observation envelope are declared.

PHASE 0 / RED STATE
-------------------
This file is a deliberate skeleton. The constants are empty and the models
declare no fields yet: ``tests/test_contracts.py`` is the executable spec that
must turn green. Do not "helpfully" fill in plausible values here — every
column name, group count, core set and enum member is asserted against
``tests/spec/columns_v1.json`` (extracted from 02-architecture §3.8.1).
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict

#: Ordered, frozen v1 export contract — 92 names, 10 groups (02 §3.8.1).
CANONICAL_COLUMNS_V1: tuple[str, ...] = ()

#: Rows missing any of these are rejected before export (02 §3.8.1).
CORE_COLUMNS: frozenset[str] = frozenset()

#: Allowed values of the ``data_quality`` column (enum, never free text).
DATA_QUALITY_VALUES: tuple[str, ...] = ()


class Capability(str, Enum):
    """Provider capability (02 §3.4). RED: the eight members are not declared."""


class MarketRequest(BaseModel):
    """Frozen client request for resolve/estimate/enrich (02 §3.2).

    Must be ``extra="forbid"`` and ``frozen=True``: an unknown knob is a client
    bug, not a feature toggle, and a quoted plan may never be mutated.
    RED: fields are intentionally not declared yet.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)


class MarketPlan(BaseModel):
    """Deterministic, quotable execution plan (02 §3.2 / §3.3).

    RED: fields are intentionally not declared yet.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)


class QuotePlanV1(BaseModel):
    """The plan frozen by a single-use quote (02 §4.3).

    ``plan_hash`` is the sha256 of this document, so two jobs with the same
    plan share a hash and data drift can be told apart from code drift.
    RED: fields are intentionally not declared yet.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)


class Observation(BaseModel):
    """One provider observation for one (instrument, field) cell (02 §4.6).

    An observation without ``as_of`` is not allowed to exist: ``as_of`` is the
    time of the **data**, never the time of the fetch.
    RED: fields are intentionally not declared yet.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
