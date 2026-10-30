"""The depth store, read once per file version (the screener's depth columns).

The depth collector (``app.market.depth``) publishes one field-level JSON
document — ``{"as_of": ..., "symbols": {"AAPL": {"pe": 12.3, ...}, ...}}`` —
roughly every 13 minutes, atomically, for ~4,200 symbols (~9 MB). ``/screens``
may overlay those columns onto a selection universe, so this module owns the one
read of that file. Four rules, in the order they cost money:

**Read once per file version.** A parsed snapshot is cached against
``(path, mtime_ns, size)``. An unchanged file is never parsed twice — the parse
is ~0.2 s and ~50 MB of Python objects, so a per-row or per-request parse would
be a latency incident, not an inefficiency — and a rewritten file is read again
on the next call. ``ttl_s`` is an optional second gate: with ``ttl_s > 0`` a
snapshot younger than the window is served without even a ``stat``.

**At most one snapshot per process.** The cache is a single slot keyed by path,
not a dictionary of paths. The document is 9 MB on disk and larger in memory, so
a caller that invents paths must not be able to grow the process by a parsed copy
apiece; a second path simply replaces the first.

**A failure is an empty picture, never an exception.** ``path=None``, a missing
file, an unreadable path, invalid JSON, a payload that is not a JSON object, and
a symbol whose value is not an object all answer ``as_of=None, symbols={}``.
Depth is an enrichment of a display: one bad write may not turn ``GET /screens``
into a 500. The reason is one log line, not a stack trace up the request.

**A broken file is cached like a good one.** The empty answer is stored against
the same fingerprint, so a malformed store is parsed once, not once per scan,
and the next rewrite invalidates it by changing ``(mtime_ns, size)``.

Staleness is not this module's decision: it reports ``as_of`` (the collector's
stamp) and ``loaded_at`` (this process's read clock) and lets the caller decide
what a 13-minute-old picture is worth. Nothing is written back — the depth store
has exactly one writer.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

logger = logging.getLogger("mee.screen.depth")

#: The collector's own default store (``app.market.depth.DEFAULT_STORE_PATH``).
#: Spelled as a literal so importing this reader does not drag in the collector's
#: provider adapters; ``tests/test_screen_depth_source.py`` pins the equality, so
#: the two constants cannot drift apart silently.
DEFAULT_STORE_PATH = Path("data/depth.json")

#: The one empty symbol table, shared by every empty snapshot. Wrapped so a caller
#: cannot mutate the answer the next caller will receive.
_EMPTY_SYMBOLS: Mapping[str, Mapping[str, Any]] = MappingProxyType({})


@dataclass(frozen=True, slots=True)
class DepthSnapshot:
    """One parsed depth store: the collector's stamp, the rows, the read clock.

    ``symbols`` is keyed exactly as the store spells it (``BRK-B``, not
    ``BRK.B``); ticker normalisation belongs to the overlay, which also knows
    which way the provider row spells it. Values are the store's own cells —
    ``None`` included — and the mapping is read-only.
    """

    as_of: str | None = None
    symbols: Mapping[str, Mapping[str, Any]] = _EMPTY_SYMBOLS
    loaded_at: float = 0.0

    @property
    def count(self) -> int:
        """How many symbols the store carries (``0`` for an empty picture)."""

        return len(self.symbols)


def empty_snapshot(*, now: float = 0.0) -> DepthSnapshot:
    """The answer for "no depth store": no stamp, no symbols, no rows."""

    return DepthSnapshot(as_of=None, symbols=_EMPTY_SYMBOLS, loaded_at=now)


@dataclass(frozen=True, slots=True)
class _CacheEntry:
    """The last parsed store: which file, which version of it, what it parsed to."""

    key: str
    mtime_ns: int
    size: int
    snapshot: DepthSnapshot


#: One slot, not a dict: see "at most one snapshot per process" above.
_CACHE: _CacheEntry | None = None


def reset_cache() -> None:
    """Forget the cached snapshot (tests, or an operator forcing a re-read).

    Not needed on a store rewrite — the fingerprint check does that — but a
    process that wants to re-read the *same* version (e.g. after moving the file
    aside and back) needs a way to say so.
    """

    global _CACHE
    _CACHE = None


def load_depth(
    path: str | Path | None,
    *,
    clock: Callable[[], float] | None = None,
    ttl_s: float = 0.0,
) -> DepthSnapshot:
    """Read the depth store at ``path``; never raises, never parses twice.

    ``clock`` is the read clock (``time.time`` by default, epoch seconds) and is
    what ``loaded_at`` records. ``ttl_s`` is 0 by default, which means "trust
    only the fingerprint": the file is stat-ed on every call and re-read only
    when ``(mtime_ns, size)`` moved. A positive ``ttl_s`` additionally serves a
    younger snapshot without touching the disk.
    """

    global _CACHE

    now = (clock or time.time)()
    if path is None:
        return empty_snapshot(now=now)

    try:
        target = Path(path).expanduser()
        key = str(target.resolve())
    except (OSError, RuntimeError, ValueError) as exc:  # pragma: no cover - exotic path
        logger.warning("depth store path %r cannot be resolved: %s", path, exc)
        return empty_snapshot(now=now)

    cached = _CACHE
    if cached is not None and cached.key == key:
        if ttl_s > 0 and (now - cached.snapshot.loaded_at) < ttl_s:
            return cached.snapshot

    try:
        stat = target.stat()
    except OSError as exc:
        # A box without the depth collector is the normal case, not a fault.
        logger.debug("depth store %s is not readable: %s", target, exc)
        return empty_snapshot(now=now)

    fingerprint = (stat.st_mtime_ns, stat.st_size)
    if (
        cached is not None
        and cached.key == key
        and (cached.mtime_ns, cached.size) == fingerprint
    ):
        return cached.snapshot

    try:
        snapshot = _read(target, now=now)
    except Exception:  # pragma: no cover - the store is untrusted input
        # Last resort for the one hard rule: a broken store may not raise into a
        # selection. The expected failures are handled, with a message, in
        # ``_read``; this only catches what a hand-edited file can still surprise
        # the shaper with.
        logger.warning("depth store %s could not be parsed", target, exc_info=True)
        snapshot = empty_snapshot(now=now)

    _CACHE = _CacheEntry(
        key=key,
        mtime_ns=stat.st_mtime_ns,
        size=stat.st_size,
        snapshot=snapshot,
    )
    return snapshot


def _read(path: Path, *, now: float) -> DepthSnapshot:
    """Read and shape one version of the store. Empty on any read/parse failure."""

    try:
        raw = path.read_bytes()
    except OSError as exc:
        logger.warning("depth store %s is unreadable: %s", path, exc)
        return empty_snapshot(now=now)

    try:
        document = json.loads(raw)
    except (ValueError, RecursionError) as exc:
        # JSONDecodeError and UnicodeDecodeError are both ValueErrors; a deeply
        # nested document is a RecursionError. All three mean "no depth today".
        logger.warning("depth store %s is not valid JSON: %s", path, exc)
        return empty_snapshot(now=now)

    snapshot = _shape(document, now=now)
    if snapshot.as_of is None:
        logger.info("depth store %s read: %d symbols, no as_of", path, snapshot.count)
    else:
        logger.info(
            "depth store %s read: %d symbols as_of=%s", path, snapshot.count, snapshot.as_of
        )
    return snapshot


def _shape(document: Any, *, now: float) -> DepthSnapshot:
    """Turn a parsed payload into a snapshot, dropping what is not a symbol row."""

    if not isinstance(document, dict):
        logger.warning(
            "depth store payload is %s, not a JSON object", type(document).__name__
        )
        return empty_snapshot(now=now)

    raw_symbols = document.get("symbols")
    symbols: dict[str, Mapping[str, Any]] = {}
    if isinstance(raw_symbols, dict):
        for symbol, fields in raw_symbols.items():
            if not isinstance(symbol, str) or not symbol.strip():
                continue
            if not isinstance(fields, dict):
                # A scalar or a list under a ticker was never a row; keeping it
                # would hand the overlay a value it cannot merge per field.
                continue
            symbols[symbol] = fields
    elif raw_symbols is not None:
        logger.warning(
            "depth store symbols is %s, not an object", type(raw_symbols).__name__
        )

    as_of = document.get("as_of")
    stamp = as_of.strip() if isinstance(as_of, str) and as_of.strip() else None
    return DepthSnapshot(
        as_of=stamp,
        symbols=MappingProxyType(symbols) if symbols else _EMPTY_SYMBOLS,
        loaded_at=now,
    )


def current_depth(settings: Any | None = None) -> DepthSnapshot:
    """The depth store this deployment serves, or an empty snapshot.

    One call per scan: the path comes from settings (``MEE_SCREEN_DEPTH`` gates
    it, ``MEE_SCREEN_DEPTH_STORE`` names the file), the parse is mtime-cached,
    and every failure mode answers an empty snapshot so a screener request can
    never fail because enrichment is missing.
    """

    return load_depth(resolve_store_path(settings))


def resolve_store_path(settings: Any | None = None) -> Path | None:
    """The store this process should read, or ``None`` when depth is switched off.

    Two rules, in order:

    1. ``screen_depth`` false → ``None``. The screener must not touch the file at
       all in that case, and ``load_depth(None)`` is the empty snapshot, so the
       caller cannot accidentally read depth it was told to ignore.
    2. ``screen_depth_store_path`` when set, else the collector's own
       ``depth_store_path``. The fallback is the point: a deployment already sets
       ``MEE_DEPTH_STORE_PATH`` (``data/depth.json`` when it sets nothing), and a
       second variable that must repeat it would be a way to read a different
       file than the one being written.

    A relative value stays relative to the process working directory, exactly as
    the collector resolves its own ``store_path`` — remapping it through
    ``app.engine.paths.data_dir()`` here would make the reader and the writer
    disagree whenever ``MEE_DATA_DIR`` is set. ``~`` is expanded because a read
    side path is typed by an operator.

    ``settings`` may also be a path, for a caller that has already resolved it.
    """

    if isinstance(settings, (str, Path)):
        return Path(settings).expanduser()

    if settings is None:
        from app.config import get_settings

        settings = get_settings()

    if not getattr(settings, "screen_depth", True):
        return None

    override = getattr(settings, "screen_depth_store_path", None)
    if isinstance(override, Path):
        return override.expanduser()
    if isinstance(override, str) and override.strip():
        return Path(override.strip()).expanduser()

    configured = getattr(settings, "depth_store_path", None)
    if isinstance(configured, Path):
        return configured.expanduser()
    if isinstance(configured, str) and configured.strip():
        return Path(configured.strip()).expanduser()

    return DEFAULT_STORE_PATH


__all__ = [
    "DEFAULT_STORE_PATH",
    "DepthSnapshot",
    "empty_snapshot",
    "load_depth",
    "reset_cache",
    "resolve_store_path",
]
