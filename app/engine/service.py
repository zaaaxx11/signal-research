"""Read-only projection of the engine's foundational slices.

This service is deliberately small: it exposes the frozen catalog, the licence
policy, the in-memory security master, an optional screen row provider and the
point-in-time document for one instrument. The API can therefore ship a stable
``/engine`` surface while ingestion and persistence land in later slices; a
deployment with no dataset answers ``503`` rather than inventing rows.

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

    def peers(self, symbol: str, *, limit: int = 10) -> dict[str, Any] | None:
        """Who else is in this symbol's SEC industry group, up to ``limit``.

        The group is computed from the identity the engine already owns — the
        SIC ``industry`` on :class:`~app.engine.model.SecurityRecord` — so the
        answer needs no peer list from a vendor and no market capitalisation
        the universe does not have. Case and surrounding whitespace are folded
        away before comparing: ``" electronic computers "`` and
        ``"ELECTRONIC COMPUTERS"`` are one industry, not three.

        A missing industry is an *honest empty group*, not a 503 and not every
        other company whose SIC is unknown: "we do not know what this company
        does" must not silently become "these companies are alike". The same
        refusal applies to a null ``mic`` when ordering: a venue nobody stated
        is not a venue shared with another null.

        The order is deterministic and explains itself — same MIC as the
        subject first (the group reads like a venue page), then symbol
        ascending — because a market-cap rank would be an invented number.
        ``limit`` is applied *after* ordering, so a page always starts with the
        same head of the same list; ``count`` therefore reports the page, not
        the group.

        ``as_of`` is the newest document that asserted the **subject**, never a
        peer's newer document and never the load clock: the peer set cannot
        make the subject's identity fresher than its own filing. Only a master
        with no provenance at all falls back to the dataset's ``fetched_at``,
        and a bare in-memory master answers ``None`` rather than a made-up
        date. ``None`` as a return value answers "this symbol is not in the
        master"; a deployment with no master raises
        :class:`EngineUnavailable` first, because "nothing was ingested" and
        "no such company" must not look the same.
        """

        master = self.security_master
        if master is None:
            raise EngineUnavailable(
                "screen_unavailable",
                "the engine has no identity master loaded yet; ingest a dataset first",
            )

        subject = master.get(symbol)
        if subject is None:
            return None

        industry_key = self._industry_key(subject.industry)
        members: list[SecurityRecord] = (
            []
            if industry_key is None
            else [
                record
                for record in master.records()
                if record.symbol != subject.symbol
                and self._industry_key(record.industry) == industry_key
            ]
        )
        same_venue = subject.mic is not None
        members.sort(
            key=lambda record: (
                0 if same_venue and record.mic == subject.mic else 1,
                record.symbol,
            )
        )
        # A non-positive limit is an empty page, not a Python negative slice
        # (which would silently return every peer but the last few). The route
        # rejects 0/51/'x' before reaching here; this only keeps a direct
        # service call truthful.
        page = members[:limit] if limit > 0 else []

        return {
            "symbol": subject.symbol,
            "industry": subject.industry,
            "as_of": self._identity_as_of(subject.symbol),
            "count": len(page),
            "peers": [self._peer_document(record) for record in page],
        }

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

    def fundamentals(self, symbol: str) -> dict[str, Any] | None:
        """One instrument's point-in-time document, or ``None`` when unknown.

        The document is a projection of a single assembled row — every cell the
        dataset holds for the symbol plus every cell the row assembler derived —
        so a caller can see the filing behind each number instead of a
        table of pre-chewed ratios. ``as_of`` is the **newest provenance date in
        the row** (max, never the wall clock): a document is as current as its
        most recently observed cell, and borrowing today's date would claim a
        freshness no filing supports.

        ``None`` answers "this symbol is not in the loaded identity master";
        a deployment with no row provider raises :class:`EngineUnavailable` with
        the house ``screen_unavailable`` code, because "nothing was ingested" and
        "no such company" must not look the same. The lookup is deliberately
        one row: :meth:`DatasetRows.row` builds the symbol asked for, not the
        ten-thousand-row universe a screen needs.

        No provider name is added outside the ``source`` already inside each
        cell's provenance: the response states evidence, not who sold it.
        """

        provider = self.rows
        if provider is None:
            raise EngineUnavailable(
                "screen_unavailable",
                "the engine has no row provider loaded yet; ingest a dataset first",
            )
        lookup = getattr(provider, "row", None)
        if not callable(lookup):
            raise EngineUnavailable(
                "screen_unavailable",
                "the loaded row provider cannot answer a single symbol",
            )

        row = lookup(symbol)
        if row is None:
            return None
        if not isinstance(row, Mapping):
            raise TypeError(
                "a row provider must return a mapping of key to Cell, got "
                f"{type(row).__name__}"
            )

        symbol_key = self._row_symbol(row, symbol)
        # Start from the raw fact vocabulary when the dataset is available: the
        # row carries only cells that have a catalog column, but this endpoint's
        # job is the filing, not the screen. Row cells win on a shared key
        # because they are the assembled/derived projection of those facts.
        metrics: dict[str, Any] = {}
        if self.dataset is not None:
            for key, cell in self.dataset.facts.get(symbol_key, {}).items():
                metrics[key] = self._cell_document(cell)
        metrics.update(
            {key: self._cell_document(value) for key, value in row.items()}
        )

        return {
            "symbol": symbol_key,
            "as_of": self._row_as_of(row),
            "metrics": metrics,
        }

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
    def _row_symbol(row: Mapping[str, object], requested: str) -> str:
        """The row's own ticker when it has one, else the requested spelling.

        The identity layer owns symbol spelling (``BRK.B`` stays ``BRK.B``), so
        the document echoes the row instead of normalising the caller's path
        segment into a second vocabulary.
        """

        cell = row.get("ticker")
        value = cell.value if isinstance(cell, Cell) else None
        if isinstance(value, str) and value.strip():
            return value.strip()
        return requested.strip().upper()

    @staticmethod
    def _row_as_of(row: Mapping[str, object]) -> str | None:
        """The newest **observation** date in ``row``, or ``None``.

        Identity cells carry the load timestamp because that is when the
        identity document was read; letting that date win would label a
        fundamentals document with today even when its newest filing is months
        old. Observed and derived cells answer the question this document is
        about, so they are preferred; a fact-less row falls back to the
        identity date because the load is the only observation it has.

        ``max`` over ISO-8601 strings is chronological because the engine only
        ever emits prefix-ordered dates (a date-only value sorts before the same
        day's timestamp and after the previous day's). No date is ever invented.
        """

        dated = [
            (cell.provenance.as_of, cell.provenance.source)
            for cell in row.values()
            if isinstance(cell, Cell)
            and cell.provenance is not None
            and cell.provenance.as_of
        ]
        observed = [date for date, source in dated if source != "identity"]
        if observed:
            return max(observed)
        return max((date for date, _source in dated), default=None)

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

    def _identity_as_of(self, symbol: str) -> str | None:
        """The newest document date that asserted ``symbol``, or ``None``.

        Provenance is per symbol and ordered oldest first, so ``max`` over the
        documents is the freshest evidence the master holds for *this* record.
        A master loaded without a dataset has no load clock to borrow, and
        borrowing one would label a filing with the day of the request; the
        dataset's ``fetched_at`` is therefore only a fallback for a master that
        kept no provenance at all. Nothing else is ever invented.
        """

        master = self.security_master
        if master is not None:
            documents = master.provenance(symbol)
            if documents:
                return max(document.as_of for document in documents)
        dataset = self.dataset
        return None if dataset is None else dataset.fetched_at

    @staticmethod
    def _industry_key(industry: str | None) -> str | None:
        """The grouping key for an SIC industry label.

        Case and runs of whitespace are presentation, not a different
        industry, so they are folded away before comparing. A missing (or
        blank) industry answers ``None`` — never a shared key — because "we do
        not know this company's industry" is not evidence that two companies
        are alike.
        """

        if industry is None:
            return None
        return " ".join(industry.split()).casefold() or None

    @staticmethod
    def _peer_document(record: SecurityRecord) -> dict[str, Any]:
        """Exactly the six identity fields a peer page may state.

        No provider label, no market cap and no composite score: the engine
        holds no caps for the universe, and a rank it cannot cite would be
        presented as authority it does not have. ``industry`` is the peer's own
        record rather than the subject's spelling; both fold to the same group
        key, and the row stays a statement about that company.
        """

        return {
            "symbol": record.symbol,
            "name": record.name,
            "exchange": record.exchange,
            "mic": record.mic,
            "industry": record.industry,
            "country": record.country,
        }

    @staticmethod
    def _security_document(record: SecurityRecord) -> dict[str, Any]:
        return asdict(record)


__all__ = ["EngineService"]
