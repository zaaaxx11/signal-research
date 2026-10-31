"""Provider venue spelling → ISO 10383 MIC: one table for the depth store.

The depth store's ``exchange`` cell has two writers and, before this module,
three vocabularies:

* **SEC/EDGAR** prints a venue *name* (``Nasdaq``, ``NYSE``, ``CBOE``, ``TXSE``),
  measured over 10,433 snapshot records as
  ``{Nasdaq 4,357 · NYSE 3,270 · OTC 2,579 · null 171 · CBOE 44 · TXSE 12}``;
* **Yahoo** prints its own product code in ``fullExchangeName``
  (``NasdaqGM``/``NasdaqCM``/``NasdaqGS``) — a Nasdaq market *tier*, not a venue;
* the **screener** prints a MIC, because :func:`app.screen.universe.mic_for`
  normalises every row before it is stored.

A join between the two stores therefore agreed on 0 rows by string equality and
on 4,008 once the vocabulary was normalised (research D2). This module is the
one place the translation is stated, and :func:`normalize_venue` is the single
function applied at the store's write boundary.

Rules
-----
* **Known spelling → its MIC, and the original is kept.** The caller stores the
  MIC in ``exchange`` and the spelling in :data:`EXCHANGE_SOURCE_FIELD`. That
  second cell is *store metadata*: the catalogue is frozen at 116 columns, so
  provenance is a store column and never a catalogue column, and the depth
  overlay drops it before a row reaches a product surface.
* **Idempotent.** A MIC is already normalised. ``normalize_venue`` applied to its
  own output changes nothing, which is what makes a re-run safe.
* **Unknown ⇒ preserved, never guessed.** A code this table does not know is
  returned exactly as printed. A guessed MIC would make a record look verified
  when it is not — the rule ``app/engine/sources/sec.py`` already keeps.
* **``TXSE`` is a documented pass-through, not a mapping.** The Texas Stock
  Exchange is too new to have a verified MIC (the SEC source states it and
  stores ``mic=None`` for its 12 records), so there is nothing to map it *to*.
  Relabelling its 9 depth rows as ``XASE`` would agree with today's screener —
  and hide the very disagreement the research measured instead of recording it.

Scope
-----
Only venues the product serves are mapped. ``NYSE Arca`` (``ARCX``),
``CBOE BZX`` (``BATS``) and every foreign code are deliberately absent: they are
different venues, not spellings of these four, and a pass-through keeps them
visible instead of folding them into a code that means something else.
"""

from __future__ import annotations

from typing import Any

#: Store metadata: the spelling the provider printed, beside the MIC that
#: replaced it. **Not** a catalogue column (``app/market/depth_fields.py`` owns
#: the 116 frozen names) and never copied onto a screen row.
EXCHANGE_SOURCE_FIELD = "exchange_source"

#: Provider spelling → MIC. Keys are compared upper-cased with whitespace
#: collapsed, so ``"  nyse american "`` finds ``NYSE AMERICAN``.
#:
#: The EDGAR rows mirror ``app/engine/sources/sec.py::_EXCHANGE_MIC`` — that
#: table maps a *record's* venue to its MIC at parse time, this one maps a
#: *store cell*; the overlapping keys are asserted equal in
#: ``tests/test_market_venue.py`` so the two copies cannot drift.
VENUE_MIC: dict[str, str] = {
    # -- EDGAR / SEC identity snapshot ---------------------------------------
    "NASDAQ": "XNAS",
    "NASDAQ GLOBAL SELECT": "XNAS",
    "NASDAQ GLOBAL SELECT MARKET": "XNAS",
    "NASDAQ GLOBAL MARKET": "XNAS",
    "NASDAQ CAPITAL MARKET": "XNAS",
    "NYSE": "XNYS",
    "NEW YORK STOCK EXCHANGE": "XNYS",
    "NYSE AMERICAN": "XASE",
    "NYSE MKT": "XASE",
    "AMEX": "XASE",
    "CBOE": "XCBO",
    "OTC": "OTCM",
    # -- Yahoo's fullExchangeName product codes ------------------------------
    "NASDAQGS": "XNAS",
    "NASDAQGM": "XNAS",
    "NASDAQCM": "XNAS",
    # -- already MICs: identity rows, so the function is idempotent ----------
    "XNAS": "XNAS",
    "XNYS": "XNYS",
    "XASE": "XASE",
    "XCBO": "XCBO",
    "OTCM": "OTCM",
}

#: Venues that keep their own name because no MIC is verified for them. A venue
#: belongs here only with a written reason; the vocabulary invariant
#: (``tests/test_screen_universe.py``) treats this set as the allow-list.
PASSTHROUGH_VENUES: frozenset[str] = frozenset({"TXSE"})


def normalize_venue(value: Any) -> tuple[str, str | None]:
    """Return ``(MIC or the original spelling, the original spelling or None)``.

    The second element is what goes in :data:`EXCHANGE_SOURCE_FIELD`; it is the
    spelling the provider printed, trimmed and with internal whitespace
    collapsed (``"NYSE\\tAmerican"`` → ``"NYSE American"``), because that is the
    value a human has to be able to trace back.

    A non-text or blank value is not a venue: ``("", None)`` — the caller must
    publish nothing rather than a number or an empty string.
    """

    if not isinstance(value, str):
        return "", None
    text = " ".join(value.split())
    if not text:
        return "", None
    return VENUE_MIC.get(text.upper(), text), text


def venue_mic(value: Any) -> str | None:
    """The MIC for a known spelling, or ``None`` for an unknown one.

    ``None`` is an answer, not a failure: callers combine it with
    :data:`PASSTHROUGH_VENUES` to decide whether a venue is in the vocabulary.
    """

    if not isinstance(value, str):
        return None
    return VENUE_MIC.get(" ".join(value.split()).upper())


__all__ = [
    "EXCHANGE_SOURCE_FIELD",
    "PASSTHROUGH_VENUES",
    "VENUE_MIC",
    "normalize_venue",
    "venue_mic",
]
