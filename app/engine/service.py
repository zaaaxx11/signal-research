"""Read-only projection of the engine's foundational slices.

This service is deliberately small: it exposes the frozen catalog, the licence
policy and the in-memory security master without pretending that an ingestion
pipeline exists yet. The API can therefore ship a stable ``/engine`` surface
while the adapters and database land in later slices.

``source_keys`` are intentionally omitted from :meth:`catalog` and
:meth:`metric`: they are adapter vocabulary, not presentation fields. The
catalog endpoint is safe to serve before the licence gate is wired into the
screen routes because it contains no provider values.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from app.engine.catalog import CATALOG, CATALOG_VERSION, catalog_hash
from app.engine.license import LicensePolicy
from app.engine.model import MetricSpec, SecurityRecord
from app.engine.security import SecurityMaster


@dataclass(frozen=True, slots=True)
class EngineService:
    """Collaborators for the read-only engine endpoints."""

    policy: LicensePolicy = LicensePolicy()
    security_master: SecurityMaster | None = None

    def health(self) -> dict[str, Any]:
        return {
            "engine": "v2",
            "catalog_version": CATALOG_VERSION,
            "catalog_hash": catalog_hash(),
            "metrics": len(CATALOG),
            "public_mode": self.policy.public_mode,
            "security_master": self.security_master is not None,
        }

    def catalog(self) -> dict[str, Any]:
        return {
            "catalog_version": CATALOG_VERSION,
            "count": len(CATALOG),
            "metrics": [self._metric_document(metric) for metric in CATALOG],
        }

    def metric(self, key: str) -> dict[str, Any] | None:
        for metric in CATALOG:
            if metric.key == key:
                return self._metric_document(metric)
        return None

    def security(self, symbol: str) -> dict[str, Any] | None:
        if self.security_master is None:
            return None
        record = self.security_master.get(symbol)
        return self._security_document(record) if record is not None else None

    @staticmethod
    def _metric_document(metric: MetricSpec) -> dict[str, Any]:
        return {
            "key": metric.key,
            "label": metric.label,
            "unit": metric.unit.value,
            "views": list(metric.views),
            "derivation": metric.derivation,
        }

    @staticmethod
    def _security_document(record: SecurityRecord) -> dict[str, Any]:
        return asdict(record)


__all__ = ["EngineService"]
