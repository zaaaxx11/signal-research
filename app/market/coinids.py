"""The coinids manifest: read, normalise and validate.

The manifest answers a question the universe-driven scan cannot: *fetch exactly
these instruments*, including symbols the scan's tabs do not carry. It is
server-side configuration the owner edits by hand, so this module's job is to
make the document's rules explicit and to refuse a document that breaks them
instead of quietly serving half of it.

Schema (version 1)
------------------

::

    {
      "version": 1,
      "updated_at": "2026-11-02T00:00:00Z",
      "instruments": [
        {"id": "id1", "symbol": "AAPL", "asset": "stock"},
        {"id": "id3", "symbol": "AAPL", "asset": "option",
         "expiry": "2026-01-16", "strike": 200, "right": "C"}
      ]
    }

Rules, each one a refusal rather than a guess:

* **``symbol`` is the identity, ``id`` is a human label.** The label is
  optional, and when present it names exactly one row. Two rows with the same
  identity — symbol, asset, expiry, strike and right — are one instrument
  duplicated and are refused, because "which of the two rows is AAPL?" has no
  answer.
* **``asset`` defaults to ``stock``.** The vocabulary is closed
  (``stock``/``option``): an instrument family this build cannot price is
  refused by name rather than published as a permanently empty row.
* **An option row is a contract.** ``expiry`` (ISO date), a positive ``strike``
  and a ``right`` (``C``/``P``, also accepted as ``call``/``put``) are required
  together, because half a contract cannot be looked up. The reverse is also a
  refusal: option fields on a stock row mean the ``asset`` key was forgotten,
  so the row is named instead of being silently treated as a stock.
* **Symbols and rights are normalised** — ``aapl`` is ``AAPL``, ``c`` is ``C``
  and ``200`` is ``200.0`` — so two spellings of one instrument cannot both
  survive. Everything else is copied verbatim.
* **Unknown top-level keys are ignored.** The schema is versioned, and a field
  this build does not know yet must not make the manifest unreadable.

The legacy flat form
--------------------

The owner's document is ``{"id1": "AAPL", "id2": "AMZN"}``: a label-to-symbol
map. It is **read** and normalised into the versioned shape in memory; the file
on disk is never rewritten by this module (there is no write endpoint in this
phase, and the manifest is the owner's file).

Location
--------

``/home/ubuntu/ztockz-data/coinid.json``, overridden by ``MEE_COINIDS_PATH``
(the process's ``MEE_`` convention). **A missing file is an empty manifest, not
an error**: a fresh deployment has no manifest yet, and "no instruments" is an
answer. A file that exists but cannot be parsed or validated *is* an error —
silently answering an empty table for a broken manifest would look exactly like
an owner who removed every instrument.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Mapping

#: The environment variable that overrides the manifest location.
ENV_MANIFEST_PATH = "MEE_COINIDS_PATH"

#: Where the owner's manifest lives. Absolute on purpose: the file is data, not
#: a package resource, and a deployment that moves it sets the env override.
DEFAULT_MANIFEST_PATH = Path("/home/ubuntu/ztockz-data/coinid.json")

#: The schema version this build writes into an in-memory normalisation. A
#: document may declare a newer one; every rule here is additive, so it is read
#: with the v1 rules and an unknown field is ignored.
VERSION = 1

ASSET_STOCK = "stock"
ASSET_OPTION = "option"
#: The closed instrument-family vocabulary.
ASSETS: tuple[str, ...] = (ASSET_STOCK, ASSET_OPTION)

#: The three cells that make an option row a contract.
OPTION_FIELDS: tuple[str, ...] = ("expiry", "strike", "right")

#: ``right`` spellings, normalised to the one-letter form the contract uses.
RIGHTS: dict[str, str] = {"C": "C", "CALL": "C", "P": "P", "PUT": "P"}


class ManifestError(ValueError):
    """The manifest exists but cannot be read, parsed or validated.

    Carries the API's error ``code`` so the route can answer the house envelope
    without inventing a second vocabulary.
    """

    def __init__(self, message: str, *, code: str = "invalid_manifest") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class Instrument:
    """One manifest entry, normalised.

    ``id`` is the owner's label (``None`` when the row has none), ``symbol`` is
    the identity, and the three contract terms are set only for ``option`` rows.
    """

    symbol: str
    asset: str = ASSET_STOCK
    id: str | None = None
    expiry: str | None = None
    strike: float | None = None
    right: str | None = None

    @property
    def identity(self) -> tuple[str, str, str | None, float | None, str | None]:
        """What makes two rows the same instrument."""

        return (self.symbol, self.asset, self.expiry, self.strike, self.right)

    @property
    def label(self) -> str:
        """The name to show for this row: its id when it has one, else symbol."""

        return self.id or self.symbol

    @property
    def contract(self) -> dict[str, Any] | None:
        """The declared contract terms, or ``None`` for a non-option row.

        These are the manifest's own declaration (identity), **not** an
        observation from an option chain — the distinction the API's
        ``unavailable`` block keeps: ``contract.expiry`` is what the owner
        asked for, ``expiry`` as a signal is ``null`` until a chain exists.
        """

        if self.asset != ASSET_OPTION:
            return None
        return {"expiry": self.expiry, "strike": self.strike, "right": self.right}


@dataclass(frozen=True, slots=True)
class Manifest:
    """One loaded manifest: its file, its stamp, and its rows in file order."""

    path: Path | None
    present: bool
    version: int
    updated_at: str | None
    instruments: tuple[Instrument, ...]
    legacy: bool

    def __len__(self) -> int:
        return len(self.instruments)

    @property
    def by_id(self) -> dict[str, Instrument]:
        """The labelled rows, keyed by ``id`` (labels are unique by validation)."""

        return {row.id: row for row in self.instruments if row.id is not None}


# ---------------------------------------------------------------------------
# Cells
# ---------------------------------------------------------------------------


def _symbol(value: Any, *, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"{where}: symbol must be a non-empty string, got {value!r}")
    return value.strip().upper()


def _identifier(value: Any, *, where: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"{where}: id must be a non-empty string, got {value!r}")
    return value.strip()


def _expiry(value: Any, *, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"{where}: option expiry must be an ISO date, got {value!r}")
    try:
        parsed = date.fromisoformat(value.strip())
    except ValueError as exc:
        raise ManifestError(
            f"{where}: option expiry {value!r} is not an ISO date (YYYY-MM-DD)"
        ) from exc
    return parsed.isoformat()


def _strike(value: Any, *, where: str) -> float:
    number: float | None
    if isinstance(value, bool) or value is None:
        number = None
    elif isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            number = None
    else:
        number = None
    if number is None or not math.isfinite(number) or number <= 0:
        raise ManifestError(f"{where}: option strike must be a positive number, got {value!r}")
    return number


def _right(value: Any, *, where: str) -> str:
    if not isinstance(value, str):
        raise ManifestError(f"{where}: option right must be C or P, got {value!r}")
    normalised = RIGHTS.get(value.strip().upper())
    if normalised is None:
        raise ManifestError(f"{where}: option right must be C or P, got {value!r}")
    return normalised


def _asset(value: Any, *, where: str) -> str:
    if value is None:
        return ASSET_STOCK
    if not isinstance(value, str):
        raise ManifestError(f"{where}: asset must be a string, got {value!r}")
    normalised = value.strip().lower() or ASSET_STOCK
    if normalised not in ASSETS:
        raise ManifestError(
            f"{where}: unknown asset {value!r}; expected one of {', '.join(ASSETS)}"
        )
    return normalised


# ---------------------------------------------------------------------------
# One row
# ---------------------------------------------------------------------------


def _instrument(raw: Any, *, where: str) -> Instrument:
    """One raw entry → one normalised instrument, or a refusal naming the row."""

    if not isinstance(raw, Mapping):
        raise ManifestError(f"{where}: instrument must be a JSON object, got {raw!r}")

    symbol = _symbol(raw.get("symbol"), where=where)
    asset = _asset(raw.get("asset"), where=f"{where} ({symbol})")
    identifier = _identifier(raw.get("id"), where=f"{where} ({symbol})")
    carried = [name for name in OPTION_FIELDS if raw.get(name) is not None]

    if asset == ASSET_OPTION:
        missing = [name for name in OPTION_FIELDS if raw.get(name) is None]
        if missing:
            raise ManifestError(
                f"{where}: option {symbol} requires "
                f"{', '.join(OPTION_FIELDS)}; missing {', '.join(missing)}"
            )
        return Instrument(
            symbol=symbol,
            asset=asset,
            id=identifier,
            expiry=_expiry(raw.get("expiry"), where=f"{where} ({symbol})"),
            strike=_strike(raw.get("strike"), where=f"{where} ({symbol})"),
            right=_right(raw.get("right"), where=f"{where} ({symbol})"),
        )

    if carried:
        raise ManifestError(
            f"{where}: {symbol} carries option field(s) {', '.join(carried)} "
            f"but asset is {asset!r}; set \"asset\": \"option\" or remove them"
        )
    return Instrument(symbol=symbol, asset=asset, id=identifier)


# ---------------------------------------------------------------------------
# The document
# ---------------------------------------------------------------------------


def _version(document: Mapping[str, Any]) -> int:
    value = document.get("version", VERSION)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ManifestError(f"manifest version must be a positive integer, got {value!r}")
    return value


def _updated_at(document: Mapping[str, Any]) -> str | None:
    value = document.get("updated_at")
    if value is None:
        return None
    if not isinstance(value, str):
        raise ManifestError(f"manifest updated_at must be a string, got {value!r}")
    return value.strip() or None


def _legacy_rows(document: Mapping[str, Any]) -> list[Instrument]:
    """``{"id1": "AAPL"}`` → one stock instrument per entry, in file order."""

    rows: list[Instrument] = []
    for key, value in document.items():
        where = f"instrument {key!r}"
        if not isinstance(key, str) or not key.strip():
            raise ManifestError(f"{where}: the id of a legacy entry must be a string")
        if not isinstance(value, str) or not value.strip():
            raise ManifestError(
                f"{where}: the legacy form maps each id to one symbol string, "
                f"got {value!r}; use the versioned form for anything else"
            )
        rows.append(
            Instrument(id=key.strip(), symbol=_symbol(value, where=where), asset=ASSET_STOCK)
        )
    return rows


def _ensure_unique(instruments: tuple[Instrument, ...]) -> None:
    """Refuse a duplicate instrument or a duplicate label, naming both rows."""

    seen: dict[tuple[str, str, str | None, float | None, str | None], Instrument] = {}
    labelled: dict[str, Instrument] = {}
    for row in instruments:
        previous = seen.get(row.identity)
        if previous is not None:
            raise ManifestError(
                "duplicate instrument: "
                f"{row.label} ({row.symbol} {row.asset}) is already declared as "
                f"{previous.label}; one symbol is one instrument"
            )
        seen[row.identity] = row
        if row.id is not None:
            clash = labelled.get(row.id)
            if clash is not None:
                raise ManifestError(
                    f"duplicate id {row.id!r}: it labels both {clash.symbol} and "
                    f"{row.symbol}; an id must name exactly one row"
                )
            labelled[row.id] = row


def parse_manifest(document: Any, *, path: Path | None = None) -> Manifest:
    """Normalise and validate one decoded manifest document.

    The versioned form is recognised by any of ``instruments``/``version``/
    ``updated_at``; anything else is the legacy label-to-symbol map. Raises
    :class:`ManifestError` with a sentence that names the offending row.
    """

    if not isinstance(document, Mapping):
        raise ManifestError(f"manifest must be a JSON object, got {type(document).__name__}")

    versioned = any(key in document for key in ("instruments", "version", "updated_at"))
    if versioned:
        version = _version(document)
        updated_at = _updated_at(document)
        raw_rows = document.get("instruments")
        if not isinstance(raw_rows, list):
            raise ManifestError(
                "a versioned manifest must carry an \"instruments\" list; "
                f"got {type(raw_rows).__name__}"
            )
        instruments = tuple(
            _instrument(raw, where=f"instruments[{index}]")
            for index, raw in enumerate(raw_rows)
        )
        legacy = False
    else:
        version = VERSION
        updated_at = None
        instruments = tuple(_legacy_rows(document))
        legacy = True

    _ensure_unique(instruments)
    return Manifest(
        path=path,
        present=True,
        version=version,
        updated_at=updated_at,
        instruments=instruments,
        legacy=legacy,
    )


# ---------------------------------------------------------------------------
# The file
# ---------------------------------------------------------------------------


def resolve_manifest_path(path: str | Path | None = None) -> Path:
    """The manifest location: the explicit argument, the env override, or the default."""

    if path is not None:
        return Path(path).expanduser()
    override = os.environ.get(ENV_MANIFEST_PATH, "").strip()
    return Path(override).expanduser() if override else DEFAULT_MANIFEST_PATH


def load_manifest(path: str | Path | None = None) -> Manifest:
    """Read and validate the manifest; a missing file is an empty manifest.

    Never returns ``None`` and never mutates the file. An unreadable or invalid
    document raises :class:`ManifestError`, because an empty table for a broken
    manifest is indistinguishable from an owner who declared no instruments.
    """

    target = resolve_manifest_path(path)
    try:
        raw = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return Manifest(
            path=target,
            present=False,
            version=VERSION,
            updated_at=None,
            instruments=(),
            legacy=False,
        )
    except OSError as exc:
        raise ManifestError(f"manifest at {target} is unreadable: {exc}") from exc

    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ManifestError(f"manifest at {target} is not valid JSON: {exc}") from exc

    return parse_manifest(document, path=target)


__all__ = [
    "ASSETS",
    "ASSET_OPTION",
    "ASSET_STOCK",
    "DEFAULT_MANIFEST_PATH",
    "ENV_MANIFEST_PATH",
    "Instrument",
    "Manifest",
    "ManifestError",
    "OPTION_FIELDS",
    "RIGHTS",
    "VERSION",
    "load_manifest",
    "parse_manifest",
    "resolve_manifest_path",
]
