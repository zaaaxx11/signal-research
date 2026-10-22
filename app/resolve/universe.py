"""Universe expansion: ``MarketSpec`` → provable instruments, or a question.

Three answers, and no fourth:

* **resolved** — every row is in the reference master, in the documented stable
  order ``(venue, sector, mic, symbol)``;
* **ambiguous** — the input matches more than one instrument (two share
  classes, two venues sharing a name). The candidates are returned and
  ``instruments`` is **empty**: choosing one would be a coin flip wearing a
  confidence score (03 §3.1 A3);
* **narrowing** — the query names a scope but not a universe ("top crypto",
  "banks" with no venue). HTTP 200 with questions, never a 4xx.

What this module will not do is invent: an input that matches nothing becomes
``unresolved=[{input, reason:"not_found"}]`` and contributes **no** row. That is
the whole of gate 2 (02 §5 Fase 1).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from app.contracts import MarketKind, MarketSpec
from app.resolve.aliases import CONFIDENCE, match
from app.resolve.intent import parse
from app.resolve.reference import Instrument, Reference

#: Scope-expansion rows are inferred from a filter, not from a name, so they
#: never claim 1.0 (02 §3.3: "satu pengukuran bukan klaim universal").
SCOPE_CONFIDENCE = 0.92

#: A clarification shows at most three options (03 §3.4).
MAX_QUESTIONS_OPTIONS = 3

_NOT_FOUND = "not_found"
_AMBIGUOUS = "ambiguous"
_VENUE_UNKNOWN = "venue_unknown"


@dataclass(frozen=True)
class Resolution:
    """The result of expanding one ``MarketSpec``."""

    spec: MarketSpec
    instruments: tuple[Instrument, ...]
    unresolved: tuple[dict[str, Any], ...]
    narrowing: dict[str, Any]
    confidence: float
    basis: tuple[str, ...]
    label: str

    @property
    def resolved(self) -> bool:
        """True when at least one row can be proven."""

        return bool(self.instruments)

    @property
    def needs_narrowing(self) -> bool:
        return bool(self.narrowing.get("required"))

    def rows(self) -> list[dict[str, Any]]:
        """The ``instruments[]`` document of ``POST /markets/resolve``."""

        return [
            {
                "instrument_id": row.instrument_id,
                "symbol": row.symbol,
                "mic": row.mic,
                "name": row.name,
                "confidence": round(_row_confidence(row, self.confidence), 4),
            }
            for row in self.instruments
        ]


def resolve_market(reference: Reference, spec: MarketSpec) -> Resolution:
    """Expand ``spec`` against ``reference`` (deterministic, no I/O)."""

    kind = spec.kind
    if kind is MarketKind.WATCHLIST:
        return _resolve_inputs(reference, spec, spec.symbols or (), label="watchlist")
    if kind is MarketKind.EXCHANGE:
        return _resolve_exchange(reference, spec)
    if kind is MarketKind.INDEX:
        return _resolve_index(reference, spec)
    if kind is MarketKind.SCREEN:
        return _resolve_screen(reference, spec)
    return _resolve_text(reference, spec)


# --------------------------------------------------------------------------- #
# Text
# --------------------------------------------------------------------------- #


def _resolve_text(reference: Reference, spec: MarketSpec) -> Resolution:
    text = (spec.text or "").strip()
    intent = parse(text, reference)

    if intent.listing:
        return _resolve_inputs(reference, spec, intent.parts, label="list")

    # A ticker or an exact alias is the strongest evidence available.
    instrument_ids, how = match(reference, text)
    if len(instrument_ids) == 1:
        row = reference.instrument(instrument_ids[0])
        if row is not None:
            return _resolved(
                spec, (row,), confidence=CONFIDENCE.get(how, SCOPE_CONFIDENCE),
                label=f"{how}:{text}",
            )
    if len(instrument_ids) > 1:
        return _ambiguous(spec, text, instrument_ids)

    venue = _canonical_venue(reference, spec.venue or intent.venue)
    sector = spec.sector or intent.sector
    asset_class = intent.asset_class
    countries = tuple(spec.countries or intent.countries or ())

    # An asset class the master does not cover cannot be expanded at any size.
    if asset_class and not any(row.asset_class == asset_class for row in reference.active):
        return _narrowing(
            spec,
            reason="asset_class_not_covered",
            questions=[
                {
                    "id": "size",
                    "question": (
                        f"The reference master has no {asset_class} instruments. "
                        "How large should the universe be once a venue is wired?"
                    ),
                    "options": ["top 10", "top 25", "top 50"],
                },
                {
                    "id": "venue",
                    "question": "Which venue should supply them?",
                    "options": _venue_options(reference),
                },
            ],
            venue=venue,
            sector=sector,
        )

    if sector and not venue:
        mics = sorted({row.mic for row in reference.active if row.sector == sector})
        if len(mics) > 1:
            # Two venues carry the sector: the anchor is missing, not the sector.
            return _narrowing(
                spec,
                reason="venue_required",
                questions=[
                    {
                        "id": "venue",
                        "question": f"{sector!r} is listed on more than one venue. Which one?",
                        "options": mics[:MAX_QUESTIONS_OPTIONS],
                    }
                ],
                sector=sector,
            )
        if len(mics) == 1:
            venue = mics[0]

    if venue is None and sector is None:
        if asset_class:
            return _narrowing(
                spec,
                reason="venue_required",
                questions=[
                    {
                        "id": "venue",
                        "question": f"Which venue should the {asset_class} universe come from?",
                        "options": _venue_options(reference),
                    }
                ],
            )
        return _not_found(spec, text)

    selected = _select(
        reference, venue=venue, sector=sector, asset_class=asset_class, countries=countries
    )
    if not selected:
        if venue is not None and reference.venue(venue) is None:
            return _unresolved(spec, [(text, _VENUE_UNKNOWN)])
        return _not_found(spec, text)

    return _resolved(
        spec,
        selected,
        confidence=SCOPE_CONFIDENCE,
        label=_scope_label(venue, sector, asset_class),
        venue=venue,
        sector=sector,
        countries=countries,
        limit=spec.limit,
    )


# --------------------------------------------------------------------------- #
# Explicit universes
# --------------------------------------------------------------------------- #


def _resolve_inputs(
    reference: Reference, spec: MarketSpec, inputs: Iterable[str], *, label: str
) -> Resolution:
    """Resolve a list of tickers/names; one unresolved entry per bad input."""

    values = tuple(inputs)
    rows: list[Instrument] = []
    unresolved: list[dict[str, Any]] = []
    confidences: list[float] = []
    seen: set[str] = set()

    for raw in values:
        instrument_ids, how = match(reference, raw)
        if len(instrument_ids) == 1:
            row = reference.instrument(instrument_ids[0])
            if row is None:  # pragma: no cover - index and catalogue cannot disagree
                unresolved.append({"input": raw, "reason": _NOT_FOUND})
                continue
            if row.instrument_id in seen:
                continue
            seen.add(row.instrument_id)
            rows.append(row)
            confidences.append(CONFIDENCE.get(how, SCOPE_CONFIDENCE))
        elif len(instrument_ids) > 1:
            entry: dict[str, Any] = {
                "input": raw,
                "reason": _AMBIGUOUS,
                "candidates": list(instrument_ids[:MAX_QUESTIONS_OPTIONS]),
            }
            if len(instrument_ids) > MAX_QUESTIONS_OPTIONS:
                entry["candidate_count"] = len(instrument_ids)
            unresolved.append(entry)
        else:
            unresolved.append({"input": raw, "reason": _NOT_FOUND})

    rows.sort(key=_plan_key)
    confidence = min(confidences) if confidences else SCOPE_CONFIDENCE
    return Resolution(
        spec=spec,
        instruments=tuple(rows),
        unresolved=tuple(unresolved),
        narrowing=_no_narrowing(),
        confidence=confidence,
        basis=(
            f"{len(rows)} of {len(values)} inputs resolved against the reference master",
            f"{len(unresolved)} inputs could not be proven and produced no row",
        ),
        label=label,
    )


def _resolve_exchange(reference: Reference, spec: MarketSpec) -> Resolution:
    venue = _canonical_venue(reference, spec.venue)
    if venue is None:
        return _narrowing(
            spec,
            reason="venue_required",
            questions=[
                {
                    "id": "venue",
                    "question": "Which venue?",
                    "options": _venue_options(reference),
                }
            ],
        )
    selected = _select(
        reference,
        venue=venue,
        sector=spec.sector,
        countries=tuple(spec.countries or ()),
        limit=spec.limit,
    )
    if not selected:
        if reference.venue(venue) is None:
            return _unresolved(spec, [(venue, _VENUE_UNKNOWN)], venue=venue)
        return _not_found(spec, venue, venue=venue)
    return _resolved(
        spec,
        selected,
        confidence=SCOPE_CONFIDENCE,
        label=_scope_label(venue, spec.sector, None),
        venue=venue,
        sector=spec.sector,
        countries=tuple(spec.countries or ()),
        limit=spec.limit,
    )


def _resolve_index(reference: Reference, spec: MarketSpec) -> Resolution:
    """An index's constituents are not in this master: say so, do not guess."""

    name = (spec.index or "").strip()
    if not name:
        return _narrowing(
            spec,
            reason="index_required",
            questions=[{"id": "index", "question": "Which index?", "options": []}],
        )
    return _unresolved(
        spec,
        [(name, _NOT_FOUND)],
        venue=_canonical_venue(reference, spec.venue),
    )


def _resolve_screen(reference: Reference, spec: MarketSpec) -> Resolution:
    venue = _canonical_venue(reference, spec.venue)
    sector = spec.sector
    countries = tuple(spec.countries or ())
    if not (venue or sector or countries):
        return _narrowing(
            spec,
            reason="filters_required",
            questions=[
                {
                    "id": "venue",
                    "question": "A screen needs at least a venue or a sector. Which venue?",
                    "options": _venue_options(reference),
                }
            ],
        )
    selected = _select(
        reference, venue=venue, sector=sector, countries=countries, limit=spec.limit
    )
    if not selected:
        return _not_found(spec, sector or venue or "screen", venue=venue, sector=sector)
    return _resolved(
        spec,
        selected,
        confidence=SCOPE_CONFIDENCE,
        label=_scope_label(venue, sector, None),
        venue=venue,
        sector=sector,
        countries=countries,
        limit=spec.limit,
    )


# --------------------------------------------------------------------------- #
# Selection and result construction
# --------------------------------------------------------------------------- #


def _plan_key(row: Instrument) -> tuple[str, str, str, str]:
    """The documented stable order: ``(venue, sector, mic, symbol)``.

    ``venue`` is represented by the MIC: in the frozen contract ``MarketSpec.
    venue`` *is* a MIC, so carrying a second venue identifier would only give
    two names for one thing.
    """

    return (row.mic, row.sector or "", row.mic, row.symbol)


def _select(
    reference: Reference,
    *,
    venue: str | None,
    sector: str | None = None,
    asset_class: str | None = None,
    countries: tuple[str, ...] = (),
    limit: int | None = None,
) -> tuple[Instrument, ...]:
    rows = [
        row
        for row in reference.active
        if (venue is None or row.mic == venue)
        and (sector is None or row.sector == sector)
        and (asset_class is None or row.asset_class == asset_class)
        and (not countries or (row.country or "") in countries)
    ]
    rows.sort(key=_plan_key)
    if limit is not None:
        rows = rows[:limit]
    return tuple(rows)


def _resolved(
    spec: MarketSpec,
    rows: Iterable[Instrument],
    *,
    confidence: float,
    label: str,
    venue: str | None = None,
    sector: str | None = None,
    countries: tuple[str, ...] = (),
    limit: int | None = None,
) -> Resolution:
    selected = list(rows)
    selected.sort(key=_plan_key)
    if limit is not None:
        selected = selected[:limit]
    return Resolution(
        spec=_echo(spec, venue=venue, sector=sector, countries=countries),
        instruments=tuple(selected),
        unresolved=(),
        narrowing=_no_narrowing(),
        confidence=confidence,
        basis=(
            f"{len(selected)} instruments read from the reference master",
            f"stable order (venue, sector, mic, symbol); label {label!r}",
        ),
        label=label,
    )


def _ambiguous(spec: MarketSpec, text: str, instrument_ids: Iterable[str]) -> Resolution:
    candidates = sorted(instrument_ids)
    shown = candidates[:MAX_QUESTIONS_OPTIONS]
    entry: dict[str, Any] = {"input": text, "reason": _AMBIGUOUS, "candidates": shown}
    if len(candidates) > len(shown):
        # 03 §3.4: at most three options; the rest is a count, not a list.
        entry["candidate_count"] = len(candidates)
    return Resolution(
        spec=_echo(spec),
        instruments=(),
        unresolved=(entry,),
        narrowing=_no_narrowing(),
        confidence=0.0,
        basis=(
            f"{len(candidates)} instruments match {text!r}; none was chosen",
            "two identities behind one name is a question for the caller, not a tie to break",
        ),
        label=f"ambiguous:{text}",
    )


def _not_found(
    spec: MarketSpec,
    text: str,
    *,
    venue: str | None = None,
    sector: str | None = None,
) -> Resolution:
    return _unresolved(spec, [(text, _NOT_FOUND)], venue=venue, sector=sector)


def _unresolved(
    spec: MarketSpec,
    entries: Iterable[tuple[str, str]],
    *,
    venue: str | None = None,
    sector: str | None = None,
) -> Resolution:
    unresolved = tuple({"input": raw, "reason": reason} for raw, reason in entries)
    return Resolution(
        spec=_echo(spec, venue=venue, sector=sector),
        instruments=(),
        unresolved=unresolved,
        narrowing=_no_narrowing(),
        confidence=0.0,
        basis=("no instrument in the reference master matches the input",),
        label="unresolved",
    )


def _narrowing(
    spec: MarketSpec,
    *,
    reason: str,
    questions: list[dict[str, Any]],
    venue: str | None = None,
    sector: str | None = None,
) -> Resolution:
    return Resolution(
        spec=_echo(spec, venue=venue, sector=sector),
        instruments=(),
        unresolved=(),
        narrowing={"required": True, "reason": reason, "questions": questions},
        confidence=0.0,
        basis=(
            "the query names a scope but not a universe; a plan would be an invention",
            f"narrowing reason: {reason}",
        ),
        label=f"narrowing:{reason}",
    )


def _no_narrowing() -> dict[str, Any]:
    return {"required": False, "questions": []}


def _echo(
    spec: MarketSpec,
    *,
    venue: str | None = None,
    sector: str | None = None,
    countries: tuple[str, ...] = (),
) -> MarketSpec:
    """Echo the caller's spec with the anchors the resolver proved.

    Explicit anchors from the caller always win: the resolver proposes an
    interpretation, it does not overrule a stated venue.
    """

    update: dict[str, Any] = {}
    if venue and not spec.venue:
        update["venue"] = venue
    if sector and not spec.sector:
        update["sector"] = sector
    if countries and not spec.countries:
        update["countries"] = list(countries)
    return spec.model_copy(update=update) if update else spec


def _canonical_venue(reference: Reference, value: str | None) -> str | None:
    """Map ``XIDX``, ``Indonesia``, ``BEI`` or ``Bursa Efek Indonesia`` to a MIC."""

    if not value:
        return None
    wanted = value.strip()
    if reference.venue(wanted.upper()) is not None:
        return wanted.upper()

    key = " ".join(wanted.casefold().split())
    for venue in reference.venues:
        if venue.name.casefold() == key:
            return venue.mic
    for entry in reference.jurisdictions.values():
        mic = str(entry.get("venue") or "").upper()
        aliases = {str(alias).casefold() for alias in entry.get("aliases") or ()}
        if mic and key in aliases:
            return mic
    return wanted.upper()


def _venue_options(reference: Reference) -> list[str]:
    return sorted(venue.mic for venue in reference.venues)[:MAX_QUESTIONS_OPTIONS]


def _scope_label(venue: str | None, sector: str | None, asset_class: str | None) -> str:
    return " / ".join(
        part for part in (asset_class or "equity", sector or "all sectors", venue or "any venue")
    )


def _row_confidence(row: Instrument, fallback: float) -> float:
    """A row-level confidence that never exceeds the resolution's own evidence."""

    base = fallback if fallback > 0 else SCOPE_CONFIDENCE
    if row.status != "active":
        return round(min(base, 0.5), 4)
    return round(min(max(base, 0.05), 1.0), 4)


__all__ = ["MAX_QUESTIONS_OPTIONS", "Resolution", "resolve_market"]
