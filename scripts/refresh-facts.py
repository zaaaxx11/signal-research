#!/usr/bin/env python3
"""Refresh the engine snapshot's per-issuer *facts* from EDGAR's companyfacts.

Why this script exists
----------------------
A snapshot holds two very different bodies of evidence, and they cost very
different amounts to refresh:

* **identity** — who is listed, under which name, venue and CIK. EDGAR serves
  it as *two* bulk documents, so a full identity refresh is a couple of
  requests (``python -m app.engine.ops refresh-identity``).
* **facts** — every us-gaap fact each issuer has tagged. EDGAR serves it as one
  ``companyfacts`` document **per issuer**, so the 4,197 symbols this
  deployment serves are 4,197 documents and **~7 GB** of download (measured: 904
  documents cached at 2.15 MB each). A day-old identity read is still cheap;
  there is no cheap version of this pass, only a cached one.

The existing CLIs cannot run this pass as a resumable job: ``bootstrap`` wants
every symbol on ``argv`` (4,197 tokens) and the identity refresh explicitly does
*not* touch facts. This script is the runner neither of them is — it reads the
symbol list straight out of a snapshot, crawls the documents through the shared
per-CIK cache, gates on disk headroom, writes a **new** snapshot atomically, and
loads it back before it reports success.

Rules this runner makes structural
----------------------------------
* **Never the live snapshot.** ``--source`` is opened read-only and ``--output``
  is a separate file (default: a timestamped name under ``backups/``), so a
  crawl can never corrupt what the service is currently serving.
* **A disk gate, not a disk surprise.** ``--min-free-gb`` (default 4.0) is
  checked before the first request and again on every ``--chunk-size`` symbols
  (default 25 — also the size of one ``load_sec_dataset`` call). On a breach the
  run stops with a clear message on stderr and writes **no snapshot at all**: a
  half-written snapshot is worse than none, because a reader cannot tell it
  apart from a complete one.
* **Chunked so the gate can fire.** ``load_sec_dataset`` is one pass over a
  symbol list, so there is no seam inside it to check the disk; this runner
  therefore drives it in fixed-size chunks and rebuilds one dataset from the
  union of the chunk results. Each chunk's identity read is the same two bulk
  documents and every issuer document is read through the shared cache, so
  chunking costs a re-read of the index — never a re-download of a fact body.
* **Verified before reported.** The new file is loaded back and its fact and
  security counts compared with what was written; a write that cannot be read
  is a non-zero exit code, never a success line.
* **Enrichment is carried, never re-fetched.** The per-issuer ``submissions``
  fetch that supplies ``country``/``sector``/``industry`` can be skipped or fail
  without a hard error, so a finished crawl can silently blank fields a prior
  snapshot already paid for. ``--carry-enrichment-from`` fills **only** the
  blanks in those three fields from the same symbol in an older snapshot,
  compares the fact body before and after, and writes nothing if the carry
  perturbed facts. Omitting the flag changes nothing.

Exit codes: ``0`` written and verified; ``2`` the run could not start (source
unreadable, no symbols, no disk headroom) or the write did not verify; ``3`` the
disk gate tripped mid-crawl (no snapshot was written).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import time
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # runnable as a file, not only as a module
    sys.path.insert(0, str(ROOT))

from app.engine import paths  # noqa: E402
from app.engine.ingest import EngineDataset, load_sec_dataset  # noqa: E402
from app.engine.model import Cell, Provenance  # noqa: E402
from app.engine.security import (  # noqa: E402
    SecurityConflict,
    SecurityLookupError,
    SecurityMaster,
)
from app.engine.store import EngineStore, SnapshotError  # noqa: E402

#: The service's ``EnvironmentFile``. Read before the crawl so that
#: ``paths.cache_dir()`` and the SEC rate limit resolve exactly as the service
#: resolves them; an already-set variable wins, so one run can point at a
#: different cache without editing the file.
ENV_FILE = Path(os.environ.get("MEE_ENV_FILE", "/home/ubuntu/ztockz-data/engine.env"))

#: The snapshot this deployment serves: the default symbol source, read-only.
DEFAULT_SOURCE = "/home/ubuntu/ztockz-data/engine-snapshot.json"

#: One chunk is one ``load_sec_dataset`` pass *and* the disk-gate cadence, so the
#: two numbers are deliberately the same.
DEFAULT_CHUNK_SIZE = 25

#: How often (in completed symbols) the crawl prints a progress line to stderr.
PROGRESS_EVERY = 100

#: The only fields ``--carry-enrichment-from`` fills, and only when the fresh
#: record left them blank. This is identity enrichment carried from an older
#: snapshot, never a data merge: the three fields are the ones a per-issuer
#: ``submissions`` fetch supplies.
CARRY_FIELDS = ("country", "sector", "industry")


class RefreshError(RuntimeError):
    """The refresh cannot start, or cannot be trusted to have finished."""


class DiskGateError(RefreshError):
    """Free disk fell below the configured floor; nothing was written."""


def load_env_file(path: Path) -> int:
    """Put the service's environment file into ``os.environ`` (``KEY=VALUE``).

    The systemd unit starts uvicorn with this file, so a runner that skipped it
    would resolve a different cache directory and a different SEC rate than
    production. An existing variable wins, so an operator can override one value
    for a run without editing the file.
    """

    applied = 0
    if not path.exists():
        return 0
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip()
            applied += 1
    return applied


def free_gb(mount: str = "/") -> float:
    """Free space at ``mount`` in GiB — the number the disk gate compares."""

    return shutil.disk_usage(mount).free / (1024 ** 3)


def utc_stamp() -> str:
    """``YYYYMMDDTHHMMSSZ`` — the compact UTC stamp used in snapshot names."""

    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def chunks(items: Sequence[str], size: int) -> Iterator[list[str]]:
    """Successive slices of ``items``, at most ``size`` long each."""

    step = max(1, int(size))
    for start in range(0, len(items), step):
        yield list(items[start : start + step])


def symbols_from_snapshot(source: Path) -> list[str]:
    """The symbols the snapshot holds facts for, in the file's own order.

    The snapshot's fact keys *are* the universe being refreshed: re-reading
    exactly what we already serve keeps a rerun bounded to the same set instead
    of the whole registrant index.
    """

    dataset = EngineStore(source).load()
    symbols = [str(symbol) for symbol in dataset.facts.keys()]
    if not symbols:
        raise RefreshError(f"{source} holds no facts symbols; nothing to refresh")
    return symbols


def symbols_from_file(path: Path) -> list[str]:
    """One symbol per line; blank lines and ``#`` comments are ignored."""

    symbols: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        token = raw.split("#", 1)[0].strip()
        if token:
            symbols.append(token)
    if not symbols:
        raise RefreshError(f"{path} holds no symbols")
    return symbols


def carry_enrichment(master: SecurityMaster, prior: SecurityMaster) -> list[str]:
    """Fill blank ``country``/``sector``/``industry`` from an older identity.

    The facts crawl derives identity from EDGAR's two bulk documents **plus** one
    ``submissions`` fetch per issuer; a submissions fetch that is skipped or that
    fails leaves that offer's ``country``/``sector``/``industry`` empty and does
    **not** raise — so a finished crawl can look clean while quietly dropping
    enrichment a previous snapshot already paid for (measured on the 2026-11-07
    swap: 34 symbols lost 65 field values). This carries those values forward.

    Only the three fields in :data:`CARRY_FIELDS` are considered, only when the
    fresh record left them blank, and only from the **same** symbol in ``prior``.
    An existing value is never overwritten and ``facts`` is never touched — this
    is identity enrichment, not a data merge. Returns the enriched symbols, in
    ``master`` (ascending) order.
    """

    carried: list[str] = []
    for symbol in master.symbols():
        fresh = master.get(symbol)
        ancient = prior.get(symbol)
        if fresh is None or ancient is None:
            continue
        updates = {
            field: getattr(ancient, field)
            for field in CARRY_FIELDS
            if getattr(fresh, field) is None and getattr(ancient, field) is not None
        }
        if not updates:
            continue
        try:
            history = prior.provenance(symbol)
        except SecurityLookupError:  # defensive: ancient exists, so this cannot fire
            continue
        if not history:
            continue
        # Only blank fields were proposed, so the merge cannot conflict; keep the
        # prior document's provenance so the sourced value stays attributable.
        master.add(replace(fresh, **updates), history[0])
        carried.append(symbol)
    return carried


def carry_snapshot_enrichment(
    prior_path: Path,
    fresh_path: Path,
    output: Path,
) -> dict[str, object]:
    """Carry enrichment between two snapshots on disk; return the JSON report.

    Loads ``prior_path`` (a snapshot that still holds the enrichment) and
    ``fresh_path`` (the new crawl, possibly with blank fields), fills the blank
    ``country``/``sector``/``industry`` on the fresh master, proves ``facts`` did
    not change, writes a **new** snapshot to ``output`` (never in place) and
    loads it back before reporting. This is the exact move :func:`crawl` makes
    mid-run, exposed so a swap can be repaired without a 41-minute crawl.
    """

    prior = EngineStore(prior_path).load()
    fresh = EngineStore(fresh_path).load()

    # Count before the carry mutates the fresh master in place.
    country_before = sum(1 for r in fresh.security_master.records() if r.country)
    industry_before = sum(1 for r in fresh.security_master.records() if r.industry)
    facts_before = dict(fresh.facts)

    carried = carry_enrichment(fresh.security_master, prior.security_master)

    dataset = EngineDataset(
        fetched_at=fresh.fetched_at,
        security_master=fresh.security_master,
        facts=fresh.facts,
        errors=fresh.errors,
    )
    if dict(dataset.facts) != facts_before:
        raise RefreshError(
            "carry changed the fact body; refusing to write the snapshot"
        )

    EngineStore(output).save(dataset)
    verified = EngineStore(output).load()

    return {
        "output": str(output),
        "symbols_carried": len(carried),
        "sample": carried[:10],
        "facts_identical": dict(verified.facts) == facts_before,
        "facts_symbols": len(verified.facts),
        "errors": len(verified.errors),
        "country_before": country_before,
        "country_after": sum(
            1 for r in verified.security_master.records() if r.country
        ),
        "industry_before": industry_before,
        "industry_after": sum(
            1 for r in verified.security_master.records() if r.industry
        ),
        "verified": verified.fetched_at == fresh.fetched_at
        and len(verified.security_master.symbols())
        == len(fresh.security_master.symbols()),
    }


def absorb(
    master: SecurityMaster,
    donor: SecurityMaster,
    provenance: Provenance,
    *,
    full: bool,
    conflicts: list[str],
) -> None:
    """Fold one chunk's identity into the accumulator.

    Every ``load_sec_dataset`` pass rebuilds identity from the same bulk
    document, so the first chunk carries the whole universe and the rest only
    add the per-issuer enrichment (``country``/``sector``/``industry``) their own
    symbols paid a submissions fetch for — which is why later chunks are filtered
    instead of re-merged in full. A disagreement between two identity reads is
    recorded and skipped rather than aborting a multi-hour crawl over one row.
    """

    for record in donor.records():
        if not full and not (record.country or record.sector or record.industry):
            continue
        try:
            master.add(record, provenance)
        except SecurityConflict as exc:
            conflicts.append(str(exc))


def report(
    *,
    output: Path,
    symbols_requested: int,
    facts_symbols: int,
    errors: int,
    cells: int,
    cache_dir: Path,
    free_before: float,
    free_after: float,
    elapsed_s: float,
    verified: bool,
    enrichment: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """The one JSON document this runner prints (stdout), in a fixed shape.

    ``enrichment`` is supplied only when ``--carry-enrichment-from`` ran, so a
    run without the flag prints exactly the document it always did.
    """

    document: dict[str, object] = {
        "output": str(output),
        "symbols_requested": symbols_requested,
        "facts_symbols": facts_symbols,
        "errors": errors,
        "cells": cells,
        "cache_dir": str(cache_dir),
        "free_gb_before": round(free_before, 3),
        "free_gb_after": round(free_after, 3),
        "elapsed_s": round(elapsed_s, 1),
        "verified": verified,
    }
    if enrichment is not None:
        document["enrichment_carried"] = enrichment["carried"]
        document["enrichment_sample"] = [str(s) for s in enrichment["sample"]]  # type: ignore[union-attr]
    return document


def emit(document: dict[str, object]) -> None:
    """Print the single JSON document to stdout and nothing else."""

    print(json.dumps(document, indent=2, sort_keys=True))


def crawl(
    *,
    symbols: list[str],
    output: Path,
    cache_dir: Path,
    min_free_gb: float,
    chunk_size: int,
    carry_from: Path | None = None,
) -> int:
    """Drive the crawl in chunks, gate on disk, then save and verify.

    When ``carry_from`` names an older snapshot, blank ``country``/``sector``/
    ``industry`` are filled from it after the crawl and before the write, the
    fact body is proven unchanged, and the carry is reported. ``None`` keeps the
    original behaviour exactly.
    """

    started = time.monotonic()
    total = len(symbols)
    free_before = free_gb()

    # Pre-start gate: refuse to begin a ~7 GB download with no headroom, and do
    # not create the output file at all.
    if free_before < min_free_gb:
        print(
            f"disk gate: {free_before:.2f} GiB free at / is below the "
            f"{min_free_gb:.2f} GiB floor; refusing to start (no snapshot written)",
            file=sys.stderr,
            flush=True,
        )
        emit(
            report(
                output=output,
                symbols_requested=total,
                facts_symbols=0,
                errors=0,
                cells=0,
                cache_dir=cache_dir,
                free_before=free_before,
                free_after=free_gb(),
                elapsed_s=time.monotonic() - started,
                verified=False,
            )
        )
        return 2

    print(
        f"refresh-facts: {total} symbols, chunk={chunk_size}, "
        f"cache={cache_dir}, free={free_before:.2f} GiB -> {output}",
        file=sys.stderr,
        flush=True,
    )

    master = SecurityMaster()
    provenance = Provenance(
        source="facts-refresh", as_of=datetime.now(UTC).date().isoformat()
    )
    facts: dict[str, Mapping[str, Cell]] = {}
    errors: dict[str, str] = {}
    conflicts: list[str] = []
    fetched_at = ""
    done = 0

    try:
        for index, chunk in enumerate(chunks(symbols, chunk_size)):
            # The disk gate runs on the chunk cadence, i.e. every chunk_size
            # symbols, before the chunk that would push the disk lower.
            current_free = free_gb()
            if current_free < min_free_gb:
                raise DiskGateError(
                    f"{current_free:.2f} GiB free at / is below the "
                    f"{min_free_gb:.2f} GiB floor after {done} of {total} symbols"
                )

            dataset = asyncio.run(load_sec_dataset(chunk, cache_dir=cache_dir))
            facts.update(dataset.facts)
            errors.update(dataset.errors)
            absorb(
                master,
                dataset.security_master,
                provenance,
                full=(index == 0),
                conflicts=conflicts,
            )
            fetched_at = dataset.fetched_at or fetched_at
            done = min(total, done + len(chunk))
            del dataset

            if done % PROGRESS_EVERY == 0 or done == total:
                print(
                    f"[{done}/{total}] last={chunk[-1]} facts={len(facts)} "
                    f"errors={len(errors)} elapsed={time.monotonic() - started:.0f}s",
                    file=sys.stderr,
                    flush=True,
                )
    except DiskGateError as exc:
        free_after = free_gb()
        print(f"disk gate tripped mid-crawl: {exc}; no snapshot written", file=sys.stderr, flush=True)
        emit(
            report(
                output=output,
                symbols_requested=total,
                facts_symbols=0,
                errors=0,
                cells=0,
                cache_dir=cache_dir,
                free_before=free_before,
                free_after=free_after,
                elapsed_s=time.monotonic() - started,
                verified=False,
            )
        )
        return 3

    if conflicts:
        print(
            f"identity: {len(conflicts)} record(s) skipped on disagreement "
            f"(first: {conflicts[0]})",
            file=sys.stderr,
            flush=True,
        )

    cells = sum(len(cells_for_symbol) for cells_for_symbol in facts.values())
    dataset = EngineDataset(
        fetched_at=fetched_at
        or datetime.now(UTC).replace(microsecond=0).isoformat(),
        security_master=master,
        facts=facts,
        errors=errors,
    )

    carried: list[str] = []

    def emit_report(verified: bool) -> None:
        emit(
            report(
                output=output,
                symbols_requested=total,
                facts_symbols=len(facts),
                errors=len(errors),
                cells=cells,
                cache_dir=cache_dir,
                free_before=free_before,
                free_after=free_gb(),
                elapsed_s=time.monotonic() - started,
                verified=verified,
                enrichment=(
                    None
                    if carry_from is None
                    else {"carried": len(carried), "sample": carried[:10]}
                ),
            )
        )

    # Carry enrichment before the write. A submissions fetch per issuer can be
    # skipped or fail without raising, so a clean-looking crawl can still blank
    # country/sector/industry; fill only the blanks from the prior snapshot and
    # prove the fact body did not move. A carry that perturbs facts is a bug, not
    # data, and nothing is written.
    if carry_from is not None:
        try:
            prior = EngineStore(carry_from).load()
            facts_before = dict(dataset.facts)
            carried = carry_enrichment(master, prior.security_master)
        except (SnapshotError, OSError, ValueError) as exc:
            print(
                f"carry-enrichment from {carry_from} failed: "
                f"{type(exc).__name__}: {exc}; no snapshot written",
                file=sys.stderr,
                flush=True,
            )
            emit_report(False)
            return 2
        if dict(dataset.facts) != facts_before:
            print(
                "carry-enrichment changed facts; refusing to write "
                "(no snapshot written)",
                file=sys.stderr,
                flush=True,
            )
            emit_report(False)
            return 2

    # Publish to a NEW file, then read it back. A save that fails (disk full
    # mid-write) leaves the target untouched because EngineStore writes
    # atomically; a load that fails is a non-zero exit, never a success line.
    verified = False
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        EngineStore(output).save(dataset)
        loaded = EngineStore(output).load()
        verified = (
            len(loaded.facts) == len(facts)
            and len(loaded.security_master.symbols()) == len(master.symbols())
        )
    except (SnapshotError, OSError, ValueError) as exc:
        print(f"snapshot write/verify failed: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)

    emit_report(verified)
    return 0 if verified else 2


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="refresh-facts",
        description=(
            "Refresh the engine snapshot's per-issuer facts from EDGAR's "
            "companyfacts documents (one document per issuer, ~7 GB for 4,197 "
            "symbols), writing a NEW snapshot and loading it back to verify."
        ),
    )
    parser.add_argument(
        "--source",
        default=DEFAULT_SOURCE,
        help=(
            "snapshot whose facts symbols are the crawl list "
            f"(default: {DEFAULT_SOURCE})"
        ),
    )
    parser.add_argument(
        "--symbols-file",
        default=None,
        help="text file with one symbol per line ('#' comments allowed); replaces --source",
    )
    parser.add_argument(
        "--output",
        default=None,
        help=(
            "snapshot to write; default is a timestamped name under "
            "<cache>/../backups/ (never the live --source)"
        ),
    )
    parser.add_argument(
        "--cache-dir",
        default=None,
        help="per-CIK document cache (default: MEE_ENGINE_CACHE_DIR or <data>/cache)",
    )
    parser.add_argument(
        "--min-free-gb",
        type=float,
        default=4.0,
        metavar="GB",
        help="stop when free space at / falls below this (default 4.0); no snapshot is written",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="refresh at most N symbols (0 = all); a quick pilot keeps the same code path",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=DEFAULT_CHUNK_SIZE,
        help=(
            "symbols per load_sec_dataset pass; this is also the disk-gate cadence "
            f"(default {DEFAULT_CHUNK_SIZE})"
        ),
    )
    parser.add_argument(
        "--carry-enrichment-from",
        default=None,
        metavar="SNAPSHOT",
        help=(
            "after the crawl and before the write, fill country/sector/industry "
            "that the new snapshot left BLANK from this older snapshot. The "
            "per-issuer submissions pass can be skipped or fail without a hard "
            "error, which silently blanks these fields; carrying them from the "
            "last good snapshot keeps identity enrichment without a second fetch. "
            "Only missing values are filled, never an existing one, and the fact "
            "body is compared before/after — if it changed, nothing is written."
        ),
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)

    applied = load_env_file(ENV_FILE)
    cache_dir = Path(args.cache_dir).expanduser() if args.cache_dir else paths.cache_dir()
    if args.output:
        output = Path(args.output).expanduser()
    else:
        output = cache_dir.parent / "backups" / f"engine-snapshot-{utc_stamp()}-facts.json"

    try:
        if args.symbols_file:
            symbols = symbols_from_file(Path(args.symbols_file))
        else:
            symbols = symbols_from_snapshot(Path(args.source))
    except (RefreshError, SnapshotError, OSError, ValueError) as exc:
        print(f"cannot read the symbol list: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return 2

    if args.limit and args.limit > 0:
        symbols = symbols[: args.limit]
    if not symbols:
        print("no symbols to refresh", file=sys.stderr, flush=True)
        return 2
    if args.chunk_size < 1:
        print("--chunk-size must be at least 1", file=sys.stderr, flush=True)
        return 2

    print(f"env: {applied} key(s) from {ENV_FILE}", file=sys.stderr, flush=True)

    carry_from = (
        Path(args.carry_enrichment_from).expanduser()
        if args.carry_enrichment_from
        else None
    )

    return crawl(
        symbols=symbols,
        output=output,
        cache_dir=cache_dir,
        min_free_gb=float(args.min_free_gb),
        chunk_size=int(args.chunk_size),
        carry_from=carry_from,
    )


if __name__ == "__main__":  # pragma: no cover - entrypoint
    raise SystemExit(main())
