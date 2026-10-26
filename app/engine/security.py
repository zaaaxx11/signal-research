"""An in-memory security master: identity with an audit trail, no database.

The master answers three questions without touching disk or network:

* *what is this symbol?* — :meth:`SecurityMaster.get` /
  :meth:`SecurityMaster.require`, case-insensitive and tolerant of the dot
  spelling of a share class (``BRK.B`` is ``BRK-B``);
* *which instruments share this issuer?* — :meth:`SecurityMaster.by_cik`, which
  returns every class of one CIK (Berkshire's ``BRK-A``/``BRK-B``);
* *who says so?* — :meth:`SecurityMaster.provenance`, the documents that
  asserted the symbol, oldest first.

Merge policy
------------
The master is deliberately **not** a dictionary that lets the last load win.
When a second document names a symbol already present:

* a field the master does not know yet (``exchange``, ``mic``, ``country``,
  ``sector``, ``industry``, or a missing CIK) is filled from the new record;
* a field both records state must agree. If it does not, the whole load raises
  :class:`SecurityConflict` naming the symbol and every disagreeing field. Load
  order is not evidence, so it cannot silently overwrite a CIK or a venue.
* display names compare case- and whitespace-insensitively with trailing
  periods ignored: EDGAR's index prints ``APPLE INC.`` while a submissions body
  prints ``Apple Inc.``, and that is one name, not a conflict. The existing
  spelling is kept so repeated loads cannot flip the label back and forth.
* ``active`` is **not** merged. A load can add a security or enrich one; it
  cannot retire one. Retirement needs an explicit operation that this slice
  does not provide, which keeps a refreshed index from silently delisting a
  symbol.

Provenance is per symbol, not per field. That is a deliberate simplification:
identity is asserted by a document as a whole, and the master keeps the list of
documents rather than pretending to know which row of which filing supplied
each attribute. Every load appends its provenance once; re-loading the exact
same document is idempotent.

The slice is intentionally small. It holds no bars, no fundamentals and no
prices, and it performs no I/O: fetching a document is a caller's job, which is
what keeps this module testable with handwritten fixtures.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import replace

from app.engine.model import Provenance, SecurityRecord
from app.engine.sources.sec import (
    SOURCE_NAME,
    TICKERS_URL,
    normalize_cik,
    normalize_symbol,
    parse_company_tickers,
    parse_submissions,
    submissions_url,
)

__all__ = ["SecurityConflict", "SecurityLookupError", "SecurityMaster"]


class SecurityLookupError(LookupError):
    """A symbol was required from the master but is not known."""


class SecurityConflict(ValueError):
    """Two documents disagree about a field of the same symbol."""


#: Attributes a later document may fill when the master does not know them, and
#: which must agree when both documents state them.
_MERGEABLE_TEXT_FIELDS: tuple[str, ...] = (
    "exchange",
    "mic",
    "country",
    "sector",
    "industry",
)


def _name_key(name: str) -> str:
    """Comparison key for a display name.

    Case, runs of whitespace and a trailing period are presentation, not
    identity: ``APPLE INC.`` and ``Apple Inc`` are the same issuer. A genuine
    rename still differs here and is reported as a conflict.
    """

    return " ".join(name.split()).casefold().rstrip(".")


def _same_text(left: str, right: str) -> bool:
    """Case- and whitespace-insensitive equality for an attributive field."""

    return " ".join(left.split()).casefold() == " ".join(right.split()).casefold()


def _merge(existing: SecurityRecord, incoming: SecurityRecord) -> SecurityRecord:
    """Return the enriched record, or raise if the two documents disagree.

    Only missing fields are written; an existing value is never replaced. Every
    disagreement is collected so one error names all of them instead of forcing
    the caller to fix conflicts one reload at a time.
    """

    updates: dict[str, object] = {}
    conflicts: list[str] = []

    if existing.cik is not None and incoming.cik is not None:
        if existing.cik != incoming.cik:
            conflicts.append(f"cik ({existing.cik} vs {incoming.cik})")
    elif incoming.cik is not None:
        updates["cik"] = incoming.cik

    if _name_key(existing.name) != _name_key(incoming.name):
        conflicts.append(f"name ({existing.name!r} vs {incoming.name!r})")

    for field_name in _MERGEABLE_TEXT_FIELDS:
        current = getattr(existing, field_name)
        proposed = getattr(incoming, field_name)
        if current is None:
            if proposed is not None:
                updates[field_name] = proposed
        elif proposed is not None and not _same_text(current, proposed):
            conflicts.append(f"{field_name} ({current!r} vs {proposed!r})")

    for field_name in ("asset_class", "currency"):
        current = getattr(existing, field_name)
        proposed = getattr(incoming, field_name)
        if not _same_text(current, proposed):
            conflicts.append(f"{field_name} ({current!r} vs {proposed!r})")

    if conflicts:
        raise SecurityConflict(f"{existing.symbol}: " + "; ".join(conflicts))
    if updates:
        return replace(existing, **updates)  # type: ignore[arg-type]
    return existing


class SecurityMaster:
    """An in-memory, symbol-keyed registry of :class:`SecurityRecord` values."""

    def __init__(self) -> None:
        self._records: dict[str, SecurityRecord] = {}
        self._provenance: dict[str, list[Provenance]] = {}
        self._cik_index: dict[str, tuple[str, ...]] | None = None

    # -- construction ------------------------------------------------------

    @classmethod
    def from_company_tickers(
        cls,
        payload: object,
        *,
        as_of: str,
        source: str = SOURCE_NAME,
    ) -> "SecurityMaster":
        """Build a master from one ``company_tickers.json`` body.

        ``as_of`` is the observation date of the document, not the date of the
        call: a cached body is still evidence from the day it was fetched.
        """

        master = cls()
        master.load(
            parse_company_tickers(payload),
            Provenance(source=source, as_of=as_of, url=TICKERS_URL),
        )
        return master

    def add_submissions(
        self,
        payload: object,
        *,
        as_of: str,
        source: str = SOURCE_NAME,
    ) -> int:
        """Load one submissions body; return the number of new symbols.

        A body without a usable CIK, name or ticker list loads nothing and
        returns ``0``. Each record states the submissions URL it came from, so
        the provenance of an enriched symbol points at the exact document.
        """

        records = parse_submissions(payload)
        if not records:
            return 0
        cik = records[0].cik
        if cik is None:  # defensive: parse_submissions never emits a null CIK
            return 0
        return self.load(
            records,
            Provenance(source=source, as_of=as_of, url=submissions_url(cik)),
        )

    # -- mutation ----------------------------------------------------------

    def add(self, record: SecurityRecord, provenance: Provenance) -> bool:
        """Add or enrich one record; return ``True`` when the symbol is new.

        Raises :class:`SecurityConflict` when the record disagrees with what the
        master already knows, and :class:`TypeError` when the arguments are not
        the engine's value objects.
        """

        if not isinstance(record, SecurityRecord):
            raise TypeError(
                "SecurityMaster holds SecurityRecord values, got "
                f"{type(record).__name__}"
            )
        if not isinstance(provenance, Provenance):
            raise TypeError(
                f"a load needs a Provenance, got {type(provenance).__name__}"
            )

        symbol = normalize_symbol(record.symbol)
        if symbol is None:
            raise ValueError(f"not a security symbol: {record.symbol!r}")
        cik = record.cik
        if cik is not None:
            cik = normalize_cik(cik)
            if cik is None:
                raise ValueError(f"{symbol}: not a CIK: {record.cik!r}")
        if symbol != record.symbol or cik != record.cik:
            # A caller may hand-build a record with the screen spelling or an
            # unpadded CIK. Store one canonical identity so lookup cannot miss.
            record = replace(record, symbol=symbol, cik=cik)

        existing = self._records.get(symbol)
        if existing is None:
            self._records[symbol] = record
            is_new = True
        else:
            self._records[symbol] = _merge(existing, record)
            is_new = False

        history = self._provenance.setdefault(symbol, [])
        if provenance not in history:
            history.append(provenance)
        self._cik_index = None
        return is_new

    def load(self, records: Iterable[SecurityRecord], provenance: Provenance) -> int:
        """Load many records from one document; return the number of new symbols.

        The provenance and every record are validated before the master changes,
        and a conflict half-way through restores the pre-load state. A batch is
        one document: a master left half-enriched by a rejected document would
        be an identity assembled from an error path.

        The provenance is validated even for an empty iterable, because a caller
        that passed the wrong type has a bug whether or not the body was empty.
        """

        if not isinstance(provenance, Provenance):
            raise TypeError(
                f"a load needs a Provenance, got {type(provenance).__name__}"
            )
        items = tuple(records)
        for record in items:
            if not isinstance(record, SecurityRecord):
                raise TypeError(
                    "SecurityMaster holds SecurityRecord values, got "
                    f"{type(record).__name__}"
                )

        records_before = dict(self._records)
        provenance_before = {
            symbol: list(history) for symbol, history in self._provenance.items()
        }
        try:
            added = 0
            for record in items:
                if self.add(record, provenance):
                    added += 1
        except Exception:
            self._records = records_before
            self._provenance = provenance_before
            self._cik_index = None
            raise
        return added

    # -- reads -------------------------------------------------------------

    def get(self, symbol: object) -> SecurityRecord | None:
        """The record for a symbol, or ``None``. Lookup is case-insensitive."""

        key = normalize_symbol(symbol)
        if key is None:
            return None
        return self._records.get(key)

    def require(self, symbol: object) -> SecurityRecord:
        """The record for a symbol; raise :class:`SecurityLookupError` if absent."""

        record = self.get(symbol)
        if record is None:
            raise SecurityLookupError(f"unknown security {symbol!r}")
        return record

    def by_cik(self, cik: object) -> tuple[SecurityRecord, ...]:
        """Every record for one CIK, sorted by symbol; ``()`` when unknown.

        Several share classes legitimately share a CIK, so this is a tuple and
        not a single record. A non-CIK argument is an empty answer, not a crash.
        """

        key = normalize_cik(cik)
        if key is None:
            return ()
        return tuple(
            self._records[symbol]
            for symbol in self._cik_index_map().get(key, ())
        )

    def symbols(self) -> tuple[str, ...]:
        """Every symbol in ascending order."""

        return tuple(sorted(self._records))

    def ciks(self) -> tuple[str, ...]:
        """Every known CIK in ascending order."""

        return tuple(sorted(self._cik_index_map()))

    def records(self) -> tuple[SecurityRecord, ...]:
        """Every record, in symbol order (the master's iteration order)."""

        return tuple(self._records[symbol] for symbol in sorted(self._records))

    def provenance(self, symbol: object) -> tuple[Provenance, ...]:
        """The documents that asserted this symbol, oldest first.

        Raises :class:`SecurityLookupError` for an unknown symbol instead of
        returning ``()``: no evidence and no instrument are different answers.
        """

        key = normalize_symbol(symbol)
        if key is None or key not in self._records:
            raise SecurityLookupError(f"unknown security {symbol!r}")
        return tuple(self._provenance.get(key, ()))

    def _cik_index_map(self) -> dict[str, tuple[str, ...]]:
        """Build the CIK index lazily; every mutation invalidates it."""

        if self._cik_index is None:
            buckets: dict[str, list[str]] = {}
            for symbol in sorted(self._records):
                cik = self._records[symbol].cik
                if cik is not None:
                    buckets.setdefault(cik, []).append(symbol)
            self._cik_index = {
                cik: tuple(symbols) for cik, symbols in buckets.items()
            }
        return self._cik_index

    # -- protocol ----------------------------------------------------------

    def __len__(self) -> int:
        return len(self._records)

    def __iter__(self) -> Iterator[SecurityRecord]:
        return iter(self.records())

    def __contains__(self, symbol: object) -> bool:
        return self.get(symbol) is not None

    def __repr__(self) -> str:
        return f"SecurityMaster(symbols={len(self._records)})"
