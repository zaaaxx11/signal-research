"""Provider vocabulary: capability, spec, verdict, instrument, errors (02 §3.4).

``Capability`` is re-exported from :mod:`app.contracts` — one vocabulary, not
two. Adapters never register themselves; only ``registry.py`` declares order.

Three names matter for the whole provider layer:

* :class:`ProviderSpec` — a static, **non-secret** description of a rung.
  ``auth`` is the *name* of an environment variable, never a value.
* :class:`Verdict` — what the *body* said, never a restatement of the status
  code. ``ok=False`` plus a ``kind`` is how "200 but not a quote" is expressed.
* :class:`InstrumentRef` — the duck-typed target a ladder walks for. The tests
  pass their own object with the same five attributes, so nothing here may
  require a subclass.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from app.contracts import Capability

__all__ = [
    "Capability",
    "InstrumentRef",
    "ProviderError",
    "ProviderSpec",
    "Verdict",
]

#: Verdict kinds where a **different** rung can plausibly answer (02 §3.4 table).
RETRYABLE_KINDS: frozenset[str] = frozenset(
    {"blocked", "throttled", "stale", "5xx", "timeout", "circuit_open", "error"}
)
#: Verdict kinds that end the walk: another provider would repeat the answer.
TERMINAL_KINDS: frozenset[str] = frozenset(
    {"ok", "empty", "not_found", "schema_drift", "authwall"}
)

#: Suffix → (MIC, currency) for the symbols a client may send without a venue.
_VENUE_SUFFIX: dict[str, tuple[str, str]] = {
    ".JK": ("XIDX", "IDR"),
    ".L": ("XLON", "GBP"),
    ".TO": ("XTSE", "CAD"),
    ".AS": ("XAMS", "EUR"),
    ".DE": ("XETR", "EUR"),
    ".PA": ("XPAR", "EUR"),
}


@dataclass(frozen=True)
class InstrumentRef:
    """One instrument a ladder can walk for (duck-typed by the tests)."""

    instrument_id: str
    symbol: str
    mic: str = "XNAS"
    asset_class: str = "equity"
    currency: str = "USD"

    @classmethod
    def from_symbol(
        cls,
        symbol: str,
        *,
        venue: str | None = None,
        asset_class: str = "equity",
        currency: str | None = None,
    ) -> "InstrumentRef":
        """Build a reference from a bare symbol, inferring venue only when forced.

        A MIC-shaped ``venue`` (four letters, as ``MarketSpec.venue`` carries for
        an exchange request) wins; otherwise the suffix table decides, and an
        unsuffixed symbol is a US listing. Inference is declared here so the
        cache key and the adapter URL agree on one identity.
        """

        clean = symbol.strip()
        upper = clean.upper()
        if venue and len(venue) == 4 and venue.isalpha() and venue.isupper():
            mic, default_currency = venue, "USD"
        else:
            suffix = f".{upper.rsplit('.', 1)[-1]}" if "." in upper else ""
            mic, default_currency = _VENUE_SUFFIX.get(suffix, ("XNAS", "USD"))
        resolved_currency = currency or default_currency
        return cls(
            instrument_id=f"{mic}:{upper}",
            symbol=upper,
            mic=mic,
            asset_class=asset_class,
            currency=resolved_currency,
        )


@dataclass(frozen=True)
class ProviderSpec:
    """Static, non-secret description of a provider rung."""

    name: str
    capabilities: frozenset[Capability]
    cost_tier: str
    trust: int
    delay: str
    auth: str | None
    rate_limit: tuple[int, float]
    supports: Callable[[str, str], bool]
    schema_version: str


@dataclass(frozen=True)
class Verdict:
    """Content-based verdict, never a status-code restatement."""

    ok: bool
    kind: str
    detail: str | None = None

    @property
    def retryable(self) -> bool:
        """True when another rung is worth asking (02 §3.4 / §3.9)."""

        return (not self.ok) and self.kind in RETRYABLE_KINDS

    @property
    def terminal(self) -> bool:
        """True when the walk must stop: a retry cannot change the answer."""

        return self.kind in TERMINAL_KINDS


class ProviderError(Exception):
    """A transport/parse failure carrying the verdict the ladder must record.

    Adapters raise this instead of returning a fabricated ``raw`` payload: an
    exception with ``retryable=False`` stops the walk exactly like a
    ``schema_drift`` verdict, and the attempt record shows which rung said so.
    """

    def __init__(self, verdict: Verdict) -> None:
        super().__init__(verdict.detail or verdict.kind)
        self.verdict = verdict
