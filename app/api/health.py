"""``GET /health`` — the deployment's state, not the process's (plan §3).

The old route answered ``{"status": "ok", "version": ...}`` without looking at
anything, so a process serving a ten-hour-old depth store, a snapshot whose
digest no longer verified, or a full disk still reported healthy. Three rules
shape this module:

* **Answer for the deployment.** Five checks — the depth store, the engine
  snapshot, the two enrichment cursors, free space, the screen database — are
  what the API actually serves from. A check is ``ok``, ``past its budget`` or
  ``fatal``; only fatal data (a digest that does not verify, a store that cannot
  be read) turns the route into a ``503``. A stale-but-serving deployment is
  ``degraded`` with HTTP 200, because a load balancer must not remove a box that
  can still answer.
* **Read-only and network-free.** ``/health`` only stats files, parses them and
  counts rows; it never creates a database (SQLite is opened ``mode=ro``) and
  never calls a provider. The suite-wide socket guard in ``tests/conftest.py``
  is the proof. Upstream reachability is ``/health/deep``'s job, precisely
  because a load balancer must never wait for Yahoo.
* **Fail loud, one line per transition.** A verdict change is logged at
  WARNING/ERROR; the ten thousandth identical poll is not. Phase 2's alerter
  reads the document, not the journal.

Cost, measured on the production box: a warm call is a few milliseconds (five
stats, one SQLite count, two 15-byte files). The snapshot digest is the
exception — recomputing it over the 23 MB production file costs ~1.9 s and
~210 MB of transient memory because the contract hashes a canonical
re-serialisation of the dataset. It is therefore verified **once per file
identity** (path, device, inode, size, mtime) and memoised: both writers here
publish with ``os.replace``, so every real rewrite invalidates the memo. The
first call after a process start or a snapshot write pays that cost once, in a
worker thread; every later call — and every load-balancer poll — is stat-only.
"""

from __future__ import annotations

import asyncio
import gc
import json
import logging
import os
import shutil
import sqlite3
import sys
import threading
import time
import tracemalloc
from collections.abc import Awaitable, Callable, Mapping
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app import __version__
from app.config import Settings, get_settings
from app.engine.store import EngineStore, SnapshotError
from app.providers import http as provider_http

logger = logging.getLogger("mee.health")

router = APIRouter(tags=["health"])

STATUS_OK = "ok"
STATUS_DEGRADED = "degraded"
STATUS_FAILING = "failing"

#: ``degraded`` is a 200 on purpose: the deployment still serves, and a load
#: balancer that removed it would turn a stale store into an outage. Only data
#: that cannot be trusted is a 503.
_HTTP_STATUS: dict[str, int] = {
    STATUS_OK: 200,
    STATUS_DEGRADED: 200,
    STATUS_FAILING: 503,
}

#: One entry per snapshot/depth-store rewrite; a daily writer needs one or two.
#: The map is cleared wholesale when it grows past this, which only costs a
#: re-verification.
_VERDICT_CACHE_MAX = 32

_T = TypeVar("_T")


@dataclass(frozen=True, slots=True)
class Check:
    """One named check: its public document plus how bad a failure is.

    ``fatal`` is the difference between "the deployment is behind" and "the
    deployment is serving data it cannot prove". Only a fatal check answers 503.
    """

    name: str
    document: dict[str, object]
    fatal: bool = False

    @property
    def ok(self) -> bool:
        return bool(self.document["ok"])


# ---------------------------------------------------------------------------
# verdict memo
# ---------------------------------------------------------------------------

#: ``(kind, path, device, inode, size, mtime_ns)`` → the verdict for those bytes.
_VERDICTS: dict[tuple[object, ...], object] = {}
_VERDICT_LOCK = threading.Lock()

#: The last status this process logged, so a poll loop cannot flood journald.
_LAST_STATUS: str | None = None


def _clear_verification_cache() -> None:
    """Drop every memoised verdict (tests, and an operator's manual reset)."""

    _VERDICTS.clear()


def _identity(path: Path, stat: os.stat_result) -> tuple[object, ...]:
    """The tuple that changes whenever the bytes at ``path`` may have changed.

    ``EngineStore.save`` and ``DepthStore.write`` both write a temporary file and
    ``os.replace`` it, so a successful rewrite always changes the inode and
    ``st_mtime_ns`` even when the byte count is identical. An in-place editor
    that preserved both would be a deliberate forgery; the digest is still the
    only thing that could catch it, and re-hashing per poll is not affordable.
    """

    return (str(path), stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)


def prime_snapshot_verdict(
    path: Path | str, *, valid: bool = True, error: str | None = None
) -> None:
    """Record the verdict the startup load already earned.

    The engine loads this exact file before it serves anything, so the digest
    has been checked once by the same loader the probe uses. Priming moves the
    ~2 s cost off the first ``/health`` poll — usually the one a load balancer
    sees — without weakening the check: any later rewrite changes the file
    identity and forces a fresh verification.
    """

    target = Path(path)
    try:
        stat = target.stat()
    except OSError:
        return
    key = ("snapshot", *_identity(target, stat))
    with _VERDICT_LOCK:
        if len(_VERDICTS) >= _VERDICT_CACHE_MAX:
            _VERDICTS.clear()
        _VERDICTS[key] = (bool(valid), error)


def _memoised(
    kind: str, path: Path, stat: os.stat_result, compute: Callable[[], _T]
) -> _T:
    """``compute()`` once per file identity, then the remembered verdict.

    Verified under the lock on purpose: two concurrent cold probes would
    otherwise load the same 23 MB snapshot twice, and this box has 4 GB of RAM.
    A warm call never reaches the expensive branch.
    """

    key = (kind, *_identity(path, stat))
    with _VERDICT_LOCK:
        cached = _VERDICTS.get(key)
        if cached is not None:
            return cached  # type: ignore[return-value]
        verdict = compute()
        if len(_VERDICTS) >= _VERDICT_CACHE_MAX:
            _VERDICTS.clear()
        _VERDICTS[key] = verdict
        return verdict


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def _age_seconds(mtime: float, now: float) -> int:
    """Seconds between ``mtime`` and ``now``, clamped at zero.

    A file written by a box whose clock is a minute ahead must not publish a
    negative age.
    """

    return max(0, int(now - mtime))


def _scrub(message: str, path: Path) -> str:
    """Replace an absolute path with its basename.

    ``/health`` may be answered to an anonymous caller and the operator tools in
    ``app.engine.ops`` already refuse to publish a path; the basename still says
    which store failed.
    """

    return message.replace(str(path), path.name)


def _existing_ancestor(path: Path) -> Path | None:
    """``path`` or its nearest existing parent — the volume that would hold it.

    ``/health`` must not create the data directory just to measure it, so a
    deployment whose directory is missing (a fresh box, an unmounted volume)
    reports the filesystem that is actually there instead of raising.
    """

    candidate = path
    while True:
        try:
            if candidate.exists():
                return candidate
        except OSError:  # a permission wall on the way up is "no answer"
            return None
        parent = candidate.parent
        if parent == candidate:
            return None
        candidate = parent


def _settings_of(request: Request) -> Settings:
    """The settings this app was built from (never a fresh environment read)."""

    runtime = getattr(request.app.state, "runtime", None)
    settings = getattr(runtime, "settings", None)
    if isinstance(settings, Settings):
        return settings
    return get_settings()


# ---------------------------------------------------------------------------
# depth store
# ---------------------------------------------------------------------------


def _depth_document(path: Path) -> tuple[str, str | None, int | None]:
    """Read the depth store once: ``(status, error, symbols)``.

    Only the document *shape* is checked; whether the values are right is the
    collector's business. What matters here is whether the file the process
    would seed from is a depth document at all — an mtime-only check would call
    a truncated write, a stray JSON array or an object without ``symbols`` a
    fresh store, and ``/market/depth`` would then 503 while ``/health`` said ok.
    """

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return "unparseable", f"{type(exc).__name__}: {_scrub(str(exc), path)}", None
    if not text.strip():
        return "unparseable", "the store is empty; a truncated write has no recovery", None
    try:
        payload = json.loads(text)
    except ValueError as exc:
        return "unparseable", f"not valid JSON: {exc}", None
    if not isinstance(payload, Mapping):
        return (
            "unparseable",
            f"the top level is a JSON {type(payload).__name__}, not an object",
            None,
        )
    symbols = payload.get("symbols")
    if not isinstance(symbols, Mapping):
        return "unparseable", "there is no 'symbols' object: not a depth store", None
    if not symbols:
        return "empty", "the store holds no symbols", 0
    # A store whose every symbol maps to a scalar seeds nothing: ``DepthStore
    # .seed`` skips a row that is not a mapping, so the file would parse, look
    # fresh, and leave ``/market/depth`` empty. That is a shape failure, not a
    # stale one.
    rows = sum(1 for value in symbols.values() if isinstance(value, Mapping))
    if not rows:
        return "unparseable", "no symbol maps to a field object: not a depth store", 0
    return "ok", None, rows


def _collector_state(collector: Any, *, enabled: bool) -> str:
    """What the live collector contributes: ``running``/``empty``/``missing``/``disabled``.

    The file can be perfect while the in-memory store is empty — the collector
    seeds from that file at start, and a failed seed leaves ``/market/depth``
    answering 503. That is the state a file-only check would call healthy.
    """

    if collector is None:
        return "missing" if enabled else "disabled"
    return "running" if bool(getattr(collector, "ready", False)) else "empty"


def _collector_detail(collector: Any) -> dict[str, object] | None:
    """The live store's own numbers: merges since boot, and the last cycle.

    ``revision`` counts value *changes* merged since the process started —
    seeding from the file does not count, so a freshly restarted collector
    reports ``0`` until its first live cycle replaces something. It answers
    "has this process merged a live value yet", not "is the store fresh" (the
    store's file age answers that).
    ``last_cycle`` is the last cycle's own accounting (``requested`` /
    ``returned`` / ``errors`` / ``seconds``), so "the crawl returned half the
    universe" is visible without opening journalctl.
    """

    if collector is None:
        return None
    store = getattr(collector, "store", None)
    revision = getattr(store, "revision", None)
    detail: dict[str, object] = {"revision": revision if isinstance(revision, int) else None}

    cycle = getattr(collector, "last_cycle", None)
    if cycle is None:
        detail["last_cycle"] = None
        return detail
    started = getattr(cycle, "started_at", None)
    formatter = getattr(started, "isoformat", None)
    detail["last_cycle"] = {
        "requested": getattr(cycle, "requested", None),
        "returned": getattr(cycle, "returned", None),
        "errors": getattr(cycle, "errors", None),
        "seconds": getattr(cycle, "seconds", None),
        "started_at": formatter() if callable(formatter) else started,
    }
    return detail


def _check_depth_store(
    path: Path,
    *,
    budget_s: float,
    collector: Any,
    enabled: bool,
    now: float,
    observe_runtime: bool = True,
) -> Check:
    """Does a depth store exist, does it parse, and is it younger than the cadence?"""

    state = _collector_state(collector, enabled=enabled)
    if collector is None and not observe_runtime:
        # An out-of-process monitor (the alert CLI) cannot see the service's
        # collector. Calling that "missing" would page on every run of a
        # healthy deployment, so the state is reported as unobserved instead.
        state = "not_observable"
    document: dict[str, object] = {
        "ok": True,
        "status": "ok",
        "exists": False,
        "age_s": None,
        "budget_s": budget_s,
        "symbols": None,
        "collector": state,
        "revision": None,
        "last_cycle": None,
        "error": None,
    }
    detail = _collector_detail(collector)
    if detail is not None:
        document.update(detail)
    try:
        stat = path.stat()
    except OSError as exc:
        if collector is None and not enabled:
            # Nothing in this deployment claims a depth store. A bare checkout
            # must not fail a subsystem that is switched off.
            document["status"] = "disabled"
            return Check("depth_store", document)
        document["status"] = "missing"
        document["ok"] = False
        document["error"] = (
            f"the depth store {path.name} does not exist ({type(exc).__name__})"
        )
        return Check("depth_store", document, fatal=True)

    age = _age_seconds(stat.st_mtime, now)
    status, error, symbols = _memoised(
        "depth", path, stat, lambda: _depth_document(path)
    )
    errors: list[str] = [error] if error else []
    if status == "ok" and age > budget_s:
        status = "stale"
        errors.append(f"the store is {age}s old; the budget is {budget_s:.0f}s")
    if state == "empty":
        errors.append("the running collector holds no values, so /market/depth answers 503")
    elif state == "missing":
        errors.append("depth is enabled but no collector is attached")

    document.update(
        ok=not errors,
        status=status,
        exists=True,
        age_s=age,
        symbols=symbols,
        error="; ".join(errors) or None,
    )
    return Check("depth_store", document, fatal=status == "unparseable")


# ---------------------------------------------------------------------------
# engine snapshot
# ---------------------------------------------------------------------------


def _snapshot_verdict(path: Path) -> tuple[bool, str | None]:
    """Run the store's own loader: version, digest and dataset shape.

    ``EngineStore.load()`` is the verifier on purpose — a second digest
    implementation would drift from the writer, and resealing a body that only
    *looks* right is exactly the bug that hid for days (plan §2 row 1). It is
    expensive (1.9 s / ~210 MB on the production file), which is why the caller
    memoises it on file identity rather than paying it per poll.
    """

    try:
        EngineStore(path).load()
    except SnapshotError as exc:
        return False, f"{exc.code}: {_scrub(str(exc), path)}"
    except Exception as exc:  # noqa: BLE001 - a probe reports, it never raises
        return False, f"{type(exc).__name__}: {_scrub(str(exc), path)}"
    return True, None


def _check_snapshot(
    path: Path | None, *, budget_s: float, has_dataset: bool, now: float
) -> Check:
    """Does the dataset the engine serves verify, and is it young enough?

    Three states are deliberately different:

    * *configured and invalid* — ``failing``: the bytes on disk are not the
      snapshot this build wrote, so no process may serve them.
    * *configured and stale* — ``degraded``: the digest proves the data is
      self-consistent, only old.
    * *not configured* — a bare checkout, or an engine that ingested over EDGAR.
      It is ``degraded`` when the engine holds no dataset at all (every
      ``/engine/*`` route would answer 503) and ``not_applicable`` when it does.
    """

    document: dict[str, object] = {
        "ok": True,
        "status": "ok",
        "exists": False,
        "checksum": "unverified",
        "age_s": None,
        "budget_s": budget_s,
        "error": None,
    }
    if path is None:
        if has_dataset:
            document["status"] = "not_applicable"
            return Check("snapshot", document)
        document["status"] = "not_configured"
        document["ok"] = False
        document["error"] = (
            "no snapshot is configured and the engine serves no dataset"
        )
        return Check("snapshot", document)

    try:
        stat = path.stat()
    except OSError as exc:
        document.update(
            status="missing",
            ok=False,
            checksum="missing",
            error=f"the snapshot {path.name} does not exist ({type(exc).__name__})",
        )
        return Check("snapshot", document, fatal=True)

    age = _age_seconds(stat.st_mtime, now)
    valid, error = _memoised(
        "snapshot", path, stat, lambda: _snapshot_verdict(path)
    )
    document.update(exists=True, age_s=age)
    if not valid:
        document.update(status="invalid", ok=False, checksum="invalid", error=error)
        return Check("snapshot", document, fatal=True)
    if age > budget_s:
        document.update(
            status="stale",
            ok=False,
            checksum="valid",
            error=f"the snapshot is {age}s old; the budget is {budget_s:.0f}s",
        )
        return Check("snapshot", document)
    document["checksum"] = "valid"
    return Check("snapshot", document)


# ---------------------------------------------------------------------------
# cursors
# ---------------------------------------------------------------------------


def _read_cursor(path: Path) -> tuple[int | None, str | None]:
    """One ``{"cursor": N}`` document: the value, or why it cannot be trusted.

    ``bool`` is an ``int`` subclass and ``783.5`` is not a symbol index, so only
    a real integer counts: a cursor the rotation cannot advance is a crawl that
    silently re-reads the same slice forever.
    """

    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, "the cursor file does not exist"
    except OSError as exc:
        return None, f"{type(exc).__name__}: {_scrub(str(exc), path)}"
    try:
        payload = json.loads(text)
    except ValueError:
        return None, "not valid JSON"
    if not isinstance(payload, Mapping):
        return None, f"the top level is a JSON {type(payload).__name__}, not an object"
    cursor = payload.get("cursor")
    if isinstance(cursor, bool) or not isinstance(cursor, int):
        return None, "the 'cursor' field is not an integer"
    return cursor, None


def _check_cursors(enrich_path: Path, news_path: Path) -> Check:
    """Both enrichment cursors parse and hold an integer.

    Age is deliberately not a budget here: the news cursor advances every five
    minutes and the Yahoo one daily, so a single threshold would either cry wolf
    on the daily leg or miss a stuck news feed. The depth store's mtime already
    answers "did the cycle run"; this check answers "is the rotation readable".
    """

    values: dict[str, int | None] = {}
    errors: list[str] = []
    for label, path in (("enrich", enrich_path), ("news", news_path)):
        value, error = _read_cursor(path)
        values[label] = value
        if error:
            errors.append(f"{label}: {error}")
    return Check(
        "cursors",
        {
            "ok": not errors,
            "status": "ok" if not errors else "unreadable",
            "enrich": values["enrich"],
            "news": values["news"],
            "error": "; ".join(errors) or None,
        },
    )


# ---------------------------------------------------------------------------
# disk
# ---------------------------------------------------------------------------


def _check_disk(data_dir: Path, *, min_free_gb: float) -> Check:
    """Free space on the volume that holds the data directory.

    Reported in GiB, rounded to two decimals: the unit an operator's ``df -h``
    shows, so the number can be compared without arithmetic.
    """

    target = _existing_ancestor(data_dir)
    if target is None:
        return Check(
            "disk",
            {
                "ok": False,
                "status": "unknown",
                "free_gb": None,
                "min_free_gb": min_free_gb,
                "error": f"{data_dir} and none of its parents exist",
            },
        )
    try:
        usage = shutil.disk_usage(target)
    except OSError as exc:
        return Check(
            "disk",
            {
                "ok": False,
                "status": "unknown",
                "free_gb": None,
                "min_free_gb": min_free_gb,
                "error": f"{type(exc).__name__}: {exc}",
            },
        )
    free_gb = round(usage.free / (1024**3), 2)
    ok = free_gb >= min_free_gb
    return Check(
        "disk",
        {
            "ok": ok,
            "status": "ok" if ok else "low",
            "free_gb": free_gb,
            "min_free_gb": min_free_gb,
            "error": None
            if ok
            else f"free {free_gb} GiB is below the {min_free_gb} GiB floor",
        },
    )


# ---------------------------------------------------------------------------
# screen database
# ---------------------------------------------------------------------------


def _check_screen_db(path: Path, *, timeout_s: float) -> Check:
    """Open the screen database read-only and count its screens.

    ``mode=ro`` is the read-only guarantee: a plain ``sqlite3.connect`` would
    *create* the file, and a health probe must never create or migrate a
    database. A missing table is ``degraded``, not fatal — the API serves jobs
    and quotes from the same file, so a database without ``screens`` still
    answers most routes and the migration is the operator's next step.
    """

    if not path.exists():
        return Check(
            "screen_db",
            {
                "ok": False,
                "status": "missing",
                "screens": None,
                "error": f"the screen database {path.name} does not exist",
            },
        )
    try:
        with closing(
            sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=timeout_s)
        ) as connection:
            row = connection.execute("SELECT COUNT(*) FROM screens").fetchone()
    except sqlite3.OperationalError as exc:
        message = str(exc)
        if "no such table" in message:
            return Check(
                "screen_db",
                {
                    "ok": False,
                    "status": "no_table",
                    "screens": None,
                    "error": "the screens table is missing",
                },
            )
        if "locked" in message or "busy" in message:
            return Check(
                "screen_db",
                {
                    "ok": False,
                    "status": "unreadable",
                    "screens": None,
                    "error": f"the database is locked: {message}",
                },
            )
        return Check(
            "screen_db",
            {
                "ok": False,
                "status": "unreadable",
                "screens": None,
                "error": _scrub(message, path),
            },
        )
    except sqlite3.DatabaseError as exc:
        return Check(
            "screen_db",
            {
                "ok": False,
                "status": "corrupt",
                "screens": None,
                "error": f"{type(exc).__name__}: {_scrub(str(exc), path)}",
            },
        )
    screens = int(row[0]) if row else 0
    return Check(
        "screen_db",
        {"ok": True, "status": "ok", "screens": screens, "error": None},
    )


# ---------------------------------------------------------------------------
# the web shell
# ---------------------------------------------------------------------------

#: The one instruction that fixes every state below. It is in the ``/health``
#: document and in the startup log because "the UI is missing" is not actionable
#: and "run this" is.
REBUILD_COMMAND = "cd web && npm run build"

#: True/False spellings of ``MEE_SERVE_UI``. Anything else — including an empty
#: string — is the default, "auto": report on whatever the checkout contains.
_UI_TRUE = frozenset({"1", "true", "yes", "on"})
_UI_FALSE = frozenset({"0", "false", "no", "off"})

#: Directory names under ``web/src`` that hold tests: they cannot reach the
#: bundle, so editing one does not make an artefact stale. A gate that fired on
#: the most common edit in the repository would be switched off within a week.
_TEST_DIRECTORIES = frozenset({"__tests__", "__mocks__", "__snapshots__"})


@dataclass(frozen=True, slots=True)
class WebUiState:
    """What this process knows about the shell it is — or is not — serving.

    ``mounted`` is the part no live filesystem check can reconstruct: whether
    ``create_app`` registered the ``/app`` mount when the process started. It is
    what turns "the file is there" into "the file was there when it mattered",
    which is the difference between a working deployment and one that answers
    404 for every UI URL until it is restarted.

    ``newest_source`` is measured once, at startup, so a load-balancer poll
    costs one ``stat`` of ``index.html`` and never a walk of the source tree.
    """

    dist_dir: Path
    source_dir: Path
    index: Path
    enabled: bool
    required: bool
    mounted: bool | None
    newest_source: Path | None = None
    newest_source_mtime: float | None = None
    #: ``web/dist/index.html`` — computed once, because building it on every
    #: poll means resolving this module's path on every poll.
    label: str = "dist/index.html"


def repository_root() -> Path:
    """The checkout (or install) this module was imported from.

    ``app/api/health.py`` → ``app/api`` → ``app`` → root. In the container the
    working tree shadows the installed copy, so this is ``/app`` and the shell
    resolves to ``/app/web/dist`` — the path the Dockerfile copies.

    Called when an app is built (once) and never on the poll path: ``resolve``
    walks the filesystem, and ``/health`` is answered every few seconds.
    """

    return Path(__file__).resolve().parents[2]


def _is_build_input(path: Path) -> bool:
    """Whether editing ``path`` could change the built document.

    Test files cannot: vitest reads them, ``vite build`` does not. Everything
    else under ``web/src`` can, even a comment.
    """

    if any(part in _TEST_DIRECTORIES for part in path.parts):
        return False
    return ".test." not in path.name and ".spec." not in path.name


def newest_build_input(web_dir: Path) -> tuple[Path, float] | None:
    """The most recently modified file that can change the built document.

    ``web/src`` plus the Vite entry ``web/index.html`` — the two inputs whose
    mtime ``vite build`` always advances past. Test files are excluded: they are
    not inputs, and a check that cannot tell the difference is a check everybody
    learns to ignore.
    """

    candidates: list[Path] = [web_dir / "index.html"]
    source = web_dir / "src"
    if source.is_dir():
        candidates.extend(
            path
            for path in source.rglob("*")
            if path.is_file() and _is_build_input(path)
        )

    newest: tuple[Path, float] | None = None
    for path in candidates:
        try:
            if not path.is_file():
                continue
            mtime = path.stat().st_mtime
        except OSError:  # a file that vanished mid-walk is not a verdict
            continue
        if newest is None or mtime > newest[1]:
            newest = (path, mtime)
    return newest


def build_is_stale(web_dir: Path | str) -> tuple[Path, float] | None:
    """The newest build input that is newer than ``dist/index.html``, or ``None``.

    ``None`` means one of two things and the caller knows which: the artefact is
    current, or there is no artefact at all (the health check owns that verdict).
    A rebuild writes the document last, so on a machine that just ran
    ``npm run build`` the comparison is always clean — the false positive here
    would be a gate that fails a perfect build.
    """

    root = Path(web_dir)
    try:
        built = (root / "dist" / "index.html").stat().st_mtime
    except OSError:
        return None

    newest = newest_build_input(root)
    if newest is None or newest[1] <= built:
        return None
    return newest


def resolve_web_ui(
    *,
    dist_dir: Path | str | None = None,
    source_dir: Path | str | None = None,
    serve_ui: str | None = None,
    mounted: bool | None = None,
) -> WebUiState:
    """Locate the shell and decide whether this deployment claims to serve it.

    Three intents, because "no ``dist``" means three different things:

    * ``MEE_SERVE_UI=1`` — **required**. The image bakes this in: it ships the
      artefact, so an artefact-less process is a broken image, not a choice.
    * ``MEE_SERVE_UI=0`` — **off**. An API-only deployment; the check is silent.
    * unset — **auto**. The tree is a claim only if it holds a ``web`` project
      (``package.json``) or an artefact; a bare checkout is ``disabled`` and an
      unbuilt one is ``absent`` (reported, not fatal).

    ``MEE_WEB_DIST`` / ``MEE_WEB_SRC`` exist so a test can point at a throwaway
    tree instead of fabricating one inside the repository.
    """

    root = repository_root()
    override = dist_dir if dist_dir is not None else os.environ.get("MEE_WEB_DIST")
    dist = Path(override).expanduser() if override else root / "web" / "dist"
    source_override = (
        source_dir if source_dir is not None else os.environ.get("MEE_WEB_SRC")
    )
    source = (
        Path(source_override).expanduser() if source_override else dist.parent / "src"
    )
    index = dist / "index.html"

    raw = serve_ui if serve_ui is not None else os.environ.get("MEE_SERVE_UI", "")
    switch = raw.strip().lower()
    if switch in _UI_TRUE:
        enabled, required = True, True
    elif switch in _UI_FALSE:
        enabled, required = False, False
    else:
        if switch:
            # A typo must not silently switch a safety check off, and it must
            # not switch it on either: the default is the middle, and the line
            # says so once at startup.
            logger.warning(
                "MEE_SERVE_UI=%r is not a boolean; treating it as unset (auto)", raw
            )
        enabled = index.is_file() or (dist.parent / "package.json").is_file()
        required = False

    newest = newest_build_input(dist.parent) if enabled else None
    return WebUiState(
        dist_dir=dist,
        source_dir=source,
        index=index,
        enabled=enabled,
        required=required,
        mounted=mounted,
        newest_source=newest[0] if newest else None,
        newest_source_mtime=newest[1] if newest else None,
        label=_index_label(index),
    )


def _index_label(index: Path) -> str:
    """``web/dist/index.html`` — a path an anonymous caller may be told about.

    ``/health`` can be served to anyone who can reach the port, so the document
    never carries an absolute path from this box; the startup log has the real
    one. A path outside the repository (``MEE_WEB_DIST``) is reduced to its own
    ``dist/index.html`` for the same reason.
    """

    try:
        return str(index.relative_to(repository_root()))
    except ValueError:
        return f"{index.parent.name}/{index.name}"


def web_ui_check(state: WebUiState) -> Check:
    """Does ``/app`` answer, and does it answer with bytes this source describes?

    Four verdicts, and the severity is the deployment's own claim, not a guess:

    * ``disabled`` — nothing claims a UI here (``MEE_SERVE_UI=0``).
    * ``absent`` / ``missing`` — no artefact. ``degraded`` in auto mode because
      an API-only process must stay startable; **``failing``** when the process
      was told it serves a UI, because then the artefact is the product and an
      orchestrator must refuse the rollout.
    * ``not_mounted`` — the artefact exists *now* but did not when the process
      started, so every ``/app`` URL answers 404 until a restart. This is the
      one state a filesystem-only check cannot see.
    * ``stale`` — the artefact is older than the newest source file. Serving,
      but no longer the source it claims to be; ``degraded``, and the message
      carries the rebuild command.
    """

    document: dict[str, object] = {
        "ok": True,
        "status": "ok",
        "required": state.required,
        "mounted": state.mounted,
        "exists": False,
        "age_s": None,
        "stale_s": None,
        "newest_source": None,
        "error": None,
    }
    label = state.label

    if not state.enabled:
        document["status"] = "disabled"
        return Check("web_ui", document)

    try:
        stat = state.index.stat()
    except OSError:
        document.update(
            ok=False,
            status="missing" if state.required else "absent",
            error=(
                f"{label} is missing, so GET /app answers 404. "
                f"Rebuild it with `{REBUILD_COMMAND}`"
            ),
        )
        return Check("web_ui", document, fatal=state.required)

    document.update(exists=True, age_s=_age_seconds(stat.st_mtime, time.time()))

    if state.mounted is False:
        document.update(
            ok=False,
            status="not_mounted",
            error=(
                f"{label} appeared after this process started, so the /app mount "
                "was never registered and every /app URL answers 404; restart the "
                f"process to pick it up (after `{REBUILD_COMMAND}`)"
            ),
        )
        return Check("web_ui", document, fatal=state.required)

    if (
        state.newest_source_mtime is not None
        and state.newest_source_mtime > stat.st_mtime
    ):
        stale_s = int(state.newest_source_mtime - stat.st_mtime)
        name = state.newest_source.name if state.newest_source else "the sources"
        document.update(
            ok=False,
            status="stale",
            stale_s=stale_s,
            newest_source=name,
            error=(
                f"{label} is {stale_s}s older than {name}, so /app serves bytes the "
                f"source no longer describes; rebuild it with `{REBUILD_COMMAND}`"
            ),
        )
        return Check("web_ui", document)

    return Check("web_ui", document)



# ---------------------------------------------------------------------------
# the document
# ---------------------------------------------------------------------------


def _status_of(
    checks: list[Check], startup_error: str | None, shell: Check | None = None
) -> str:
    """``failing`` only for untrusted data; otherwise any complaint is ``degraded``.

    ``shell`` is the web-UI verdict. It is a sibling of the five data checks
    rather than a sixth entry — the data contract names exactly those five, and
    the UI is the delivery surface, not a data subsystem — but it counts the
    same way: a fatal shell verdict makes the whole deployment ``failing``.
    """

    considered = checks + ([shell] if shell is not None else [])
    if any(check.fatal for check in considered):
        return STATUS_FAILING
    if startup_error is not None or any(not check.ok for check in considered):
        return STATUS_DEGRADED
    return STATUS_OK



def _guarded(name: str, build: Callable[[], Check]) -> Check:
    """Run one check; an unexpected failure is a reported check, never a 500.

    A health probe is read by a load balancer: an unparseable file, a hostile
    document or a future bug in one check must degrade *that* check and leave
    the rest of the report readable. Crashing the route would turn a diagnostic
    into an outage.
    """

    try:
        return build()
    except Exception as exc:  # noqa: BLE001 - the failure *is* the check's answer
        logger.exception("health check %s crashed", name)
        return Check(
            name,
            {
                "ok": False,
                "status": "error",
                "error": f"{type(exc).__name__}: {exc}",
            },
        )


def _log_transition(status: str, checks: list[Check], startup_error: str | None) -> None:
    """One line when the verdict changes; silence while it stays the same.

    A load balancer polls every few seconds. Logging each poll would bury the
    one line that matters, so the journal gets a message per *transition* — the
    same anti-flood rule the Phase 2 alerter uses.
    """

    global _LAST_STATUS
    if status == _LAST_STATUS:
        return
    _LAST_STATUS = status
    detail = "; ".join(
        f"{check.name}={check.document['status']}"
        + (f" ({check.document['error']})" if check.document.get("error") else "")
        for check in checks
        if not check.ok
    )
    if startup_error:
        detail = f"{detail}; startup_error={startup_error}" if detail else f"startup_error={startup_error}"
    level = (
        logging.ERROR
        if status == STATUS_FAILING
        else logging.WARNING
        if status == STATUS_DEGRADED
        else logging.INFO
    )
    logger.log(level, "health %s%s", status, f": {detail}" if detail else "")


def _proc_status(path: Path = Path("/proc/self/status")) -> dict[str, int]:
    """``VmRSS``/``VmSize``/``Threads`` from ``/proc/self/status``, in MB.

    Read from ``/proc`` rather than from ``resource``: the deployment's questions
    are "how much is resident" and "how many threads", and ``ru_maxrss`` answers
    neither (it is the high-water mark of the whole process group).
    """

    wanted = {"VmRSS": "rss_mb", "VmSize": "vm_mb", "VmSwap": "swap_mb"}
    found: dict[str, int] = {}
    try:
        text = path.read_text(encoding="ascii")
    except OSError:
        return found
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        if key in wanted:
            value = rest.strip().split()
            if value and value[0].isdigit():
                found[wanted[key]] = int(value[0]) // 1024
        elif key == "Threads" and rest.strip().isdigit():
            found["threads"] = int(rest.strip())
    return found


def _cgroup_memory(
    cgroup_file: Path = Path("/proc/self/cgroup"),
    *,
    root: Path = Path("/sys/fs/cgroup"),
) -> dict[str, int]:
    """The service cgroup's own accounting: current, high, max, peak (MB).

    The unit sets ``MemoryHigh``/``MemoryMax``, so the cgroup's numbers — not the
    host's — are what decide whether this process gets reclaimed or killed. A
    path that cannot be read answers ``{}``: a diagnostic never raises.
    """

    try:
        text = cgroup_file.read_text(encoding="ascii")
    except OSError:
        return {}
    rel = ""
    for line in text.splitlines():
        parts = line.split(":", 2)
        if len(parts) == 3 and parts[2].strip():
            rel = parts[2].strip()
    if not rel:
        return {}
    here = root / rel.lstrip("/")
    found: dict[str, int] = {}
    for name, key in (
        ("memory.current", "current_mb"),
        ("memory.high", "high_mb"),
        ("memory.max", "max_mb"),
        ("memory.peak", "peak_mb"),
    ):
        try:
            raw = (here / name).read_text(encoding="ascii").strip()
        except OSError:
            continue
        if raw.isdigit():
            found[key] = int(raw) // 1024 // 1024
    return found


def _store_census(state: Any) -> dict[str, object]:
    """What the live objects cost, as counted sizes rather than guesses.

    Only the stores this process genuinely holds: the engine dataset (identity +
    facts), the depth collector's sources, and — for the SEC bulk source — its
    built fact index, which is the one structure big enough to matter.
    """

    census: dict[str, object] = {}
    engine = getattr(state, "engine", None)
    dataset = getattr(engine, "dataset", None)
    if dataset is not None:
        facts = getattr(dataset, "facts", {}) or {}
        census["engine_dataset"] = {
            "facts_symbols": len(facts),
            "cells": sum(len(row) for row in facts.values()),
        }
    collector = getattr(state, "depth", None)
    sources = getattr(collector, "sources", None) or ()
    entries: list[dict[str, object]] = []
    for source in sources:
        index = getattr(source, "fact_index", None)
        entry: dict[str, object] = {"name": getattr(source, "name", type(source).__name__)}
        if index is not None:
            by_cik = getattr(index, "by_cik", {}) or {}
            entry["fact_index"] = {
                "issuers": len(by_cik),
                "facts": sum(len(facts) for facts in by_cik.values()),
                "submissions": len(getattr(index, "submissions", {}) or {}),
            }
        error = getattr(source, "build_error", None)
        if error:
            entry["build_error"] = error
        entries.append(entry)
    if entries:
        census["depth_sources"] = entries
    return census


def process_memory_report(state: Any = None, *, objects: bool = False) -> dict[str, object]:
    """One read-only answer to "where did the RAM go?" for this process.

    ``/health/memory`` renders it. The document is deliberately about *this*
    process: the resident set, the cgroup that reclaims it, the live object
    count, the stores it holds, and — when ``MEE_TRACE_MEMORY=1`` turned on
    tracing at boot — the top allocation sites by line.

    ``objects=True`` adds a heap census by type. It walks every object the
    collector tracks, which is why it is opt-in per request rather than always
    on: an ops endpoint that costs 200 ms of a serving worker on every poll is
    itself a memory problem.
    """

    report: dict[str, object] = {
        "status": STATUS_OK,
        "checked_at": datetime.now(UTC).isoformat(),
        "pid": os.getpid(),
        "python": sys.version.split()[0],
    }
    report.update(_proc_status())
    report["cgroup"] = _cgroup_memory()
    collected, uncollected, threshold = gc.get_count()
    report["gc"] = {
        "counts": [collected, uncollected, threshold],
        "threshold": list(gc.get_threshold()),
        "tracked_objects": len(gc.get_objects()) if objects else None,
        "frozen": getattr(gc, "get_freeze_count", lambda: None)(),
    }
    if objects:
        by_type: dict[str, int] = {}
        for item in gc.get_objects():
            name = type(item).__name__
            by_type[name] = by_type.get(name, 0) + 1
        report["top_types_by_count"] = sorted(
            by_type.items(), key=lambda pair: -pair[1]
        )[:15]
    if state is not None:
        report["stores"] = _store_census(state)
    if tracemalloc.is_tracing():
        snapshot = tracemalloc.take_snapshot()
        report["tracemalloc_top"] = [
            f"{stat.traceback[0].filename}:{stat.traceback[0].lineno} "
            f"{stat.size / 1024 / 1024:.1f} MB ({stat.count} blocks)"
            for stat in snapshot.statistics("lineno")[:10]
        ]
    else:
        report["tracemalloc"] = "off (boot with MEE_TRACE_MEMORY=1 to trace)"
    return report


def build_health_report(
    settings: Settings,
    *,
    collector: Any = None,
    engine_dataset: Any = None,
    startup_error: str | None = None,
    now: float | None = None,
    observe_runtime: bool = True,
    web_ui: WebUiState | None = None,
) -> dict[str, object]:
    """Build the ``/health`` document from the filesystem and the live objects.

    Synchronous and free of the event loop: the route runs it in a worker
    thread, because a cold digest verification is a second of CPU and a locked
    SQLite database is a wait. ``now`` is injectable so a test can age a file
    without touching the clock.

    ``web_ui`` carries ``mounted`` — the startup verdict only ``create_app``
    knows. A caller that omits it (the alert CLI) still gets the filesystem
    verdict, with ``mounted: null`` meaning "not observed from out here".
    """


    started = time.perf_counter()
    clock = time.time() if now is None else now

    depth_path = Path(settings.health_depth_store_path or settings.depth_store_path)
    data_dir = Path(settings.health_data_dir or depth_path.parent)
    # ``create_app`` expands ``MEE_ENGINE_SNAPSHOT`` before loading it, so the
    # probe expands the same way; the depth store path is left literal because
    # the collector writes it literally and a probe must read what it wrote.
    snapshot_path = (
        Path(settings.health_snapshot_path).expanduser()
        if settings.health_snapshot_path
        else None
    )
    screen_db = Path(settings.health_screen_db_path or settings.db_path)
    depth_budget = float(settings.depth_refresh_s) + float(
        settings.health_depth_budget_slack_s
    )

    checks = [
        _guarded(
            "depth_store",
            lambda: _check_depth_store(
                depth_path,
                budget_s=depth_budget,
                collector=collector,
                enabled=bool(getattr(settings, "depth", False)),
                now=clock,
                observe_runtime=observe_runtime,
            ),
        ),
        _guarded(
            "snapshot",
            lambda: _check_snapshot(
                snapshot_path,
                budget_s=float(settings.health_snapshot_budget_s),
                has_dataset=engine_dataset is not None,
                now=clock,
            ),
        ),
        _guarded(
            "cursors",
            lambda: _check_cursors(
                depth_path.with_suffix(".enrich-cursor.json"),
                depth_path.with_suffix(".news-cursor.json"),
            ),
        ),
        _guarded(
            "disk",
            lambda: _check_disk(data_dir, min_free_gb=float(settings.health_min_free_gb)),
        ),
        _guarded(
            "screen_db",
            lambda: _check_screen_db(
                screen_db, timeout_s=float(settings.health_screen_db_timeout_s)
            ),
        ),
    ]

    shell_state = web_ui if isinstance(web_ui, WebUiState) else resolve_web_ui()
    shell = _guarded("web_ui", lambda: web_ui_check(shell_state))

    status = _status_of(checks, startup_error, shell)
    _log_transition(status, [*checks, shell], startup_error)
    return {
        "status": status,
        "version": __version__,
        "checked_at": datetime.fromtimestamp(clock, tz=UTC).isoformat(),
        "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 1),
        "startup_error": startup_error,
        "checks": {check.name: check.document for check in checks},
        # Sibling, not a sixth ``checks`` entry: the five data checks are a
        # published contract, and the shell is a delivery surface. It still
        # decides the verdict above — an image without its UI is ``failing``.
        "ui": shell.document,
    }


# ---------------------------------------------------------------------------
# deep probes
# ---------------------------------------------------------------------------


#: A ticker never needs percent-encoding. Building the probe URL by hand keeps
#: this module free of the legacy transport tree (``urllib`` is banned here by
#: the import-direction rule) and is stricter than quoting: anything that is
#: not a plain symbol is refused instead of encoded into a URL.
_SYMBOL_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-")


def _safe_symbol(raw: str) -> str:
    """``raw`` as a plain ticker, or ``ValueError`` naming what was wrong."""

    symbol = str(raw or "").strip().upper()
    if not symbol or len(symbol) > 12 or not set(symbol) <= _SYMBOL_CHARS:
        raise ValueError(f"health probe symbol {raw!r} is not a plain ticker")
    return symbol


async def _probe_cnbc(timeout_s: float, *, symbol: str) -> str:
    """One CNBC quote row: the endpoint that fed the 2026-10-26 parser bug.

    One symbol and no retry. This probe answers "can we reach CNBC and does it
    still answer ``FormattedQuoteResult``", not "what is AAPL worth".
    """

    from app.market.bulk import CNBC_HOST, CNBC_QUOTE_PATH, CNBC_USER_AGENT

    url = (
        f"{CNBC_HOST}{CNBC_QUOTE_PATH}"
        f"?symbols={_safe_symbol(symbol)}&requestMethod=itv&noform=1&partnerId=2"
        "&fund=1&exthrs=1&output=json&events=1"
    )
    async with provider_http.build_client(
        timeout_s=timeout_s, headers={"user-agent": CNBC_USER_AGENT}
    ) as client:
        response = await client.get(url)
    if response.status_code != 200:
        raise RuntimeError(f"cnbc answered {response.status_code}")
    payload = response.json()
    rows = None
    if isinstance(payload, Mapping):
        result = payload.get("FormattedQuoteResult")
        if isinstance(result, Mapping):
            rows = result.get("FormattedQuote")
    if not isinstance(rows, list) or not rows:
        raise ValueError("cnbc answered without FormattedQuote rows")
    return f"{symbol}: {len(rows)} row(s)"


async def _probe_yahoo_crumb(timeout_s: float) -> str:
    """The cookie → crumb handshake, which is what a throttled egress breaks.

    Trading a cookie for a crumb is the one request the quote path cannot retry
    cheaply, so a 429 or a challenge page here is the earliest warning that
    Yahoo has started refusing this IP (plan §2 row 4).
    """

    # The protocol's own constants, headers and token shape: a second regex, a
    # second host or a missing browser UA would drift from the source that
    # actually uses them — and a bare UA is itself a 429 on this endpoint.
    from app.market.sources import (
        BROWSER_USER_AGENT,
        YAHOO_COOKIE_URL,
        YAHOO_CRUMB_PATH,
        YAHOO_HOST,
        _CRUMB_RE,
    )

    async with provider_http.build_client(
        timeout_s=timeout_s, headers={"user-agent": BROWSER_USER_AGENT}
    ) as client:
        # The warm-up answers 404 on purpose; the cookie it sets is the point.
        # A transport failure here propagates and is this probe's result.
        await client.get(YAHOO_COOKIE_URL)
        response = await client.get(f"{YAHOO_HOST}{YAHOO_CRUMB_PATH}")
    if response.status_code != 200:
        raise RuntimeError(f"yahoo answered {response.status_code}")
    token = str(getattr(response, "text", "") or "").strip()
    if not _CRUMB_RE.match(token):
        raise ValueError(f"yahoo answered {token[:24]!r}, not a crumb")
    return f"crumb accepted ({len(token)} chars)"


async def _probe_sec(timeout_s: float, *, cik: str) -> str:
    """One EDGAR document, with the contactable agent EDGAR requires.

    SEC publishes its rate limit per agent, so the probe identifies the same way
    the ingestion path does. One companyconcept document is ~100 KB and proves
    the host, the route and the identity in one request.
    """

    from app.engine.ingest import DEFAULT_USER_AGENT

    url = (
        f"https://data.sec.gov/api/xbrl/companyconcept/CIK{cik}"
        "/us-gaap/Assets.json"
    )
    async with provider_http.build_client(
        timeout_s=timeout_s, headers={"user-agent": DEFAULT_USER_AGENT}
    ) as client:
        response = await client.get(url)
    if response.status_code != 200:
        raise RuntimeError(f"sec answered {response.status_code}")
    payload = response.json()
    if not isinstance(payload, Mapping) or not payload.get("units"):
        raise ValueError("sec answered without a companyconcept document")
    return f"companyconcept Assets for {cik}"


def _probe_callables(
    settings: Settings,
) -> tuple[tuple[str, Callable[[float], Awaitable[str]]], ...]:
    """The probe set and its names, resolved from settings at call time.

    Each entry is looked up through the module global, so a test can replace
    ``_probe_cnbc`` and the route honours it without a test-only branch here.
    """

    symbol = settings.health_deep_symbol
    cik = settings.health_deep_cik
    return (
        ("cnbc", lambda timeout_s: _probe_cnbc(timeout_s, symbol=symbol)),
        ("yahoo_crumb", _probe_yahoo_crumb),
        ("sec", lambda timeout_s: _probe_sec(timeout_s, cik=cik)),
    )


async def _run_probe(
    name: str, probe: Callable[[float], Awaitable[str]], timeout_s: float
) -> dict[str, object]:
    """One probe under its own deadline; a failure is a document, never a raise."""

    started = time.perf_counter()

    def document(ok: bool, detail: str | None, error: str | None) -> dict[str, object]:
        return {
            "ok": ok,
            "ms": max(0, int(round((time.perf_counter() - started) * 1000.0))),
            "detail": detail,
            "error": error,
        }

    try:
        async with asyncio.timeout(timeout_s):
            detail = await probe(timeout_s)
    except TimeoutError:
        return document(False, None, f"TimeoutError: no answer within {timeout_s:g}s")
    except Exception as exc:  # noqa: BLE001 - the failure *is* the probe's result
        logger.warning("health probe %s failed: %s: %s", name, type(exc).__name__, exc)
        return document(False, None, f"{type(exc).__name__}: {exc}")
    return document(True, detail, None)


def _deadline_document(started: float, budget_s: float) -> dict[str, object]:
    """The result for a probe the route budget cut off before it answered."""

    return {
        "ok": False,
        "ms": max(0, int(round((time.perf_counter() - started) * 1000.0))),
        "detail": None,
        "error": f"deadline_exceeded: no answer within the {budget_s:g}s route budget",
    }


async def deep_health_report(settings: Settings) -> dict[str, object]:
    """Probe three upstreams inside one route budget.

    ``asyncio.wait`` (not ``gather``) with the route deadline is what makes the
    budget a hard bound *and* keeps the per-probe results: a probe still running
    when the deadline fires is cancelled and reported as ``deadline_exceeded``,
    so one upstream that accepts a connection and then says nothing cannot hold
    an operator's terminal open, and cannot turn the route into a 500 either.
    """

    started = time.perf_counter()
    timeout_s = float(settings.health_probe_timeout_s)
    budget_s = float(settings.health_deep_budget_s)

    tasks = {
        name: asyncio.create_task(_run_probe(name, probe, timeout_s), name=f"health-{name}")
        for name, probe in _probe_callables(settings)
    }
    done, pending = await asyncio.wait(tasks.values(), timeout=budget_s)
    for task in pending:
        task.cancel()
    if pending:
        # Reap the cancellations: a bare ``cancel`` leaves the loop warning
        # about a task nobody awaited.
        await asyncio.gather(*pending, return_exceptions=True)

    probes: dict[str, dict[str, object]] = {}
    for name, task in tasks.items():
        if not task.done() or task.cancelled():
            probes[name] = _deadline_document(started, budget_s)
            continue
        error = task.exception()
        if error is not None:  # defensive: _run_probe already catches
            probes[name] = {
                "ok": False,
                "ms": int(round((time.perf_counter() - started) * 1000.0)),
                "detail": None,
                "error": f"{type(error).__name__}: {error}",
            }
            continue
        probes[name] = task.result()

    failed = [name for name, probe in probes.items() if not probe["ok"]]
    return {
        "status": STATUS_OK if not failed else STATUS_DEGRADED,
        "budget_s": budget_s,
        "checked_at": datetime.now(UTC).isoformat(),
        "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 1),
        "probes": probes,
    }


# ---------------------------------------------------------------------------
# routes
# ---------------------------------------------------------------------------


@router.get("/health")
async def health(request: Request) -> JSONResponse:
    """Readiness: does every subsystem still hold data this build can trust?"""

    settings = _settings_of(request)
    state = request.app.state
    report = await asyncio.to_thread(
        build_health_report,
        settings,
        collector=getattr(state, "depth", None),
        engine_dataset=getattr(getattr(state, "engine", None), "dataset", None),
        startup_error=getattr(state, "engine_startup_error", None),
        # The startup verdict, not a fresh one: only this object knows whether
        # the /app mount was registered, and that is what makes a post-boot
        # ``npm run build`` visible as a 404 rather than as a healthy shell.
        web_ui=getattr(state, "web_ui", None),
    )
    return JSONResponse(
        status_code=_HTTP_STATUS[str(report["status"])], content=report
    )


@router.get("/health/memory")
async def health_memory(request: Request) -> JSONResponse:
    """Where this process's RAM goes: RSS, cgroup accounting, store census.

    An ops surface, never product chrome — the interface does not call it. It
    exists because "6.8 GB and we do not know why" is answered by numbers, not by
    a guess: the engine snapshot, the depth store and the SEC bulk index each
    report their own size here, and ``gc`` reports the live object counts.

    Read-only and bounded: one ``/proc`` read, one cgroup read, one ``gc`` count,
    and the sizes of objects the app already holds. ``?objects=1`` adds a heap
    census by type, which walks every tracked object — useful once, not per poll.
    ``tracemalloc`` is opt-in at boot (``MEE_TRACE_MEMORY=1``) because tracing
    every allocation costs CPU on a serving worker; when it is on, the document
    carries the top allocation sites as well.
    """

    objects = request.query_params.get("objects") in {"1", "true", "yes"}
    report = await asyncio.to_thread(process_memory_report, request.app.state, objects=objects)
    return JSONResponse(status_code=200, content=report)


@router.get("/health/deep")
async def health_deep(request: Request) -> JSONResponse:
    """Upstream reachability. Bounded to ``health_deep_budget_s``; never a 503.

    A dead CNBC, a throttled crumb or an EDGAR outage is a fact about the
    internet, not about this deployment: the route reports it and answers 200 so
    a checker reads the document instead of a status code.
    """

    report = await deep_health_report(_settings_of(request))
    return JSONResponse(status_code=200, content=report)


__all__ = [
    "REBUILD_COMMAND",
    "WebUiState",
    "build_health_report",
    "build_is_stale",
    "deep_health_report",
    "newest_build_input",
    "process_memory_report",
    "resolve_web_ui",
    "router",
    "web_ui_check",
]
