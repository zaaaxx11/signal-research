"""Read-only projection of the engine's foundational slices.

This service is deliberately small: it exposes the frozen catalog, the licence
policy, the in-memory security master and an optional screen row provider. The
API can therefore ship a stable ``/engine`` surface while ingestion and
persistence land in later slices; a deployment with no dataset answers ``503``
rather than inventing rows.

``source_keys`` are intentionally omitted from :meth:`catalog` and
:meth:`metric`: they are adapter vocabulary, not presentation fields. The
catalog endpoint is safe to serve before the licence gate is wired into the
screen routes because it contains no provider values.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from typing import Any, Protocol

from app.engine.catalog import CATALOG, CATALOG_VERSION, catalog_hash
from app.engine.dataset import DatasetRows
from app.engine.ingest import EngineDataset
from app.engine.license import LicensePolicy
from app.engine.model import Cell, MetricSpec, SecurityRecord
from app.engine.quotes import QUOTE_FIELDS, QuoteSnapshot
from app.engine.screener import ScreenQuery, ScreenResult, apply, parse_query
from app.engine.security import SecurityMaster
from app.engine.sources.quote import quote_source_available


class RowProvider(Protocol):
    """The row source the screen reads.

    A provider is deliberately synchronous: it answers from an already-loaded
    dataset or cache. Fetching is the ingestion slice's job, not the screen's;
    keeping that boundary explicit is what lets the screen be tested without a
    socket.
    """

    def rows(self) -> Sequence[Mapping[str, object]]: ...


class EngineUnavailable(RuntimeError):
    """The requested engine capability has no data provider yet."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class EngineService:
    """Collaborators for the read-only engine endpoints."""

    policy: LicensePolicy = LicensePolicy()
    security_master: SecurityMaster | None = None
    rows: RowProvider | None = None
    dataset: EngineDataset | None = None

    @classmethod
    def from_dataset(
        cls,
        dataset: EngineDataset,
        *,
        quotes: Mapping[str, Mapping[str, object]] | None = None,
        policy: LicensePolicy | None = None,
    ) -> EngineService:
        """Build the service around one already-ingested dataset.

        This is the composition a CLI or a lifespan hook uses after ingestion:
        the identity master serves ``/engine/security`` and the same dataset
        backs ``/engine/screen`` through :class:`DatasetRows`.
        """

        return cls(
            policy=LicensePolicy() if policy is None else policy,
            security_master=dataset.security_master,
            rows=DatasetRows(dataset, quotes or {}),
            dataset=dataset,
        )

    def with_quotes(
        self, quotes: Mapping[str, Mapping[str, object]]
    ) -> EngineService:
        """Return a service whose screen rows carry a refreshed quote map.

        Only a service built from an :class:`EngineDataset` can do this: the
        row provider needs the identity/facts snapshot as well as the new
        prices. A service built from an arbitrary test provider is returned
        unchanged, so this cannot turn a fake row source into a dataset.
        """

        if self.dataset is None:
            return self
        existing: Mapping[str, Mapping[str, object]] = {}
        if isinstance(self.rows, DatasetRows):
            existing = self.rows.quotes
        merged = dict(existing)
        merged.update(quotes)
        return replace(self, rows=DatasetRows(self.dataset, merged))

    def quotes_admitted(self) -> bool:
        """May this service poll a quote rung at all?

        Every observed-quote source is ``INTERNAL`` (see
        :mod:`app.engine.sources.quote`), so a public deployment answers
        ``False``. The route uses this as a gate *before* a fetch: a caller
        cannot license a source by asking for a refresh, and a public response
        must not carry a price it is not allowed to redistribute.
        """

        return quote_source_available(public_mode=self.policy.public_mode)

    def candidate_symbols(self) -> tuple[str, ...]:
        """Every symbol a quote-based screen could need: the whole dataset.

        A filter on ``price`` decides *membership*, so the page cannot be known
        before the values exist. The refresh target is therefore the loaded
        identity master, not the page — and an empty tuple when no dataset was
        ingested, which is a different answer from "there are no quotes".
        """

        if self.dataset is None:
            return ()
        return self.dataset.security_master.symbols()

    def uses_quote_fields(self, query: object) -> bool:
        """Does ``query`` filter or sort on an observed quote field?

        Accepts the wire shape or an already-parsed
        :class:`~app.engine.screener.ScreenQuery`. The answer decides both the
        refresh scope (candidates vs page) and whether the response must admit
        that it read a snapshot; it is computed from the *validated* query so
        ``market_cap`` — a derived price × shares value — does not count as an
        observed print.
        """

        parsed = parse_query(query)
        if parsed.sort is not None and parsed.sort.field in QUOTE_FIELDS:
            return True
        return any(spec.field in QUOTE_FIELDS for spec in parsed.filters)

    def health(self) -> dict[str, Any]:
        """The deployment's read-only self-description.

        ``dataset`` is the one key that answers "what is backing the rows": a
        process can be healthy with no dataset at all (the engine starts empty
        on purpose), so this is ``None`` rather than a block of zeroes, and the
        error count is reported next to the symbol count instead of being
        swallowed — a dataset that loaded four identities and failed two facts
        fetches is not the same as a clean one.
        """

        dataset = self.dataset
        return {
            "engine": "v2",
            "catalog_version": CATALOG_VERSION,
            "catalog_hash": catalog_hash(),
            "metrics": len(CATALOG),
            "public_mode": self.policy.public_mode,
            "security_master": self.security_master is not None,
            "rows": self.rows is not None,
            "dataset": (
                None
                if dataset is None
                else {
                    "fetched_at": dataset.fetched_at,
                    "symbols": len(dataset.security_master),
                    "errors": len(dataset.errors),
                }
            ),
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

    def screen(self, payload: ScreenQuery | Mapping[str, object]) -> ScreenResult:
        """Apply one screen query to the configured row provider.

        ``payload`` is the wire document or an already-validated
        :class:`~app.engine.screener.ScreenQuery`; the route parses once, so a
        refresh can re-run the same validated query instead of re-reading (and
        re-checking) the caller's body.

        The query is validated before the provider check on purpose: a malformed
        filter is a client error even when the deployment has no data yet, and
        returning 503 for a typo would hide the real bug.
        """

        query = parse_query(payload)
        if self.rows is None:
            # Validate the request against the catalog vocabulary before
            # reporting the missing provider: a typo is a 422 even when the
            # deployment has no data loaded yet.
            apply((), query)
            raise EngineUnavailable(
                "screen_unavailable",
                "the engine has no row provider loaded yet; ingest a dataset first",
            )
        return apply(self.rows.rows(), query)

    @staticmethod
    def screen_document(
        result: ScreenResult, *, quote_snapshot: QuoteSnapshot | None = None
    ) -> dict[str, Any]:
        """Project a :class:`ScreenResult` into a JSON-ready document.

        ``quote_snapshot`` is the freshness sidecar for the prices attached to
        the page; it is omitted when the caller has no refresh story to tell,
        so a direct unit-test call keeps the historical four-key envelope.
        """

        document: dict[str, Any] = {
            "total": result.total,
            "returned": result.returned,
            "limit": result.limit,
            "offset": result.offset,
            "applied_filters": list(result.applied_filters),
            "rows": [
                {field: EngineService._cell_document(value) for field, value in row.items()}
                for row in result.rows
            ],
        }
        if quote_snapshot is not None:
            document["quote_snapshot"] = quote_snapshot.document()
        return document

    @staticmethod
    def _cell_document(value: object) -> object:
        if not isinstance(value, Cell):
            return value
        provenance = value.provenance
        return {
            "value": value.value,
            "unit": value.unit.value,
            "provenance": (
                None
                if provenance is None
                else {
                    "source": provenance.source,
                    "as_of": provenance.as_of,
                    "status": provenance.status.value,
                    "url": provenance.url,
                    "inputs": list(provenance.inputs),
                }
            ),
        }

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
