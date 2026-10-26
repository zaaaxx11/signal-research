"""Licence gate for the engine's source mesh.

A source that answers 200 is not automatically a source that may be shown to
the public. The gate is deliberately separate from the HTTP adapters: an
adapter can be perfectly healthy and still be ``INTERNAL`` because its terms do
not permit redistribution.

Two modes:

* ``public_mode=True`` — only ``PUBLIC`` sources with ``redistribution=True``
  may answer. There is no silent fallback to an internal source; a deployment
  with no licensed source gets an explicit ``license_unavailable`` error.
* ``public_mode=False`` — development/private research may use internal
  sources. The response must still carry the source's licence class, so an
  operator can see why it is not public.

The module is intentionally tiny: policy belongs in one place, not in every
route. The API/composition root decides which sources to offer; this object
decides whether they are admissible.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from app.engine.model import LicenseClass, SourceSpec


class LicenseError(RuntimeError):
    """A source set cannot satisfy the requested licence mode."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class LicensePolicy:
    """Whether the process is serving a public product or a private one."""

    public_mode: bool = False

    def allows(self, spec: SourceSpec) -> bool:
        """True when ``spec`` may answer under this policy."""

        if not self.public_mode:
            return True
        return spec.allows_public

    def require_public(self, spec: SourceSpec) -> SourceSpec:
        """Return ``spec`` or raise if it may not be used in public mode."""

        if self.allows(spec):
            return spec
        raise LicenseError(
            "license_blocked",
            f"source {spec.name!r} is {spec.license_class.value} and "
            f"redistribution={spec.redistribution}; it cannot answer a public request",
        )

    def require_any_public(self, specs: Sequence[SourceSpec]) -> tuple[SourceSpec, ...]:
        """Return the admissible sources, or raise when none remain.

        Private mode is intentionally permissive (all sources are admissible),
        but public mode never falls back to an internal source. The error names
        what was offered so an operator can fix the composition root rather
        than guessing why a page is empty.
        """

        allowed = tuple(public_sources(specs, public_mode=self.public_mode))
        if allowed:
            return allowed
        offered = ", ".join(spec.name for spec in specs) or "(none)"
        raise LicenseError(
            "license_unavailable",
            f"no source may answer in public mode; offered: {offered}",
        )


def public_sources(
    specs: Iterable[SourceSpec], *, public_mode: bool
) -> tuple[SourceSpec, ...]:
    """Filter a source list without changing its order."""

    policy = LicensePolicy(public_mode=public_mode)
    return tuple(spec for spec in specs if policy.allows(spec))


__all__ = ["LicenseError", "LicensePolicy", "public_sources"]
