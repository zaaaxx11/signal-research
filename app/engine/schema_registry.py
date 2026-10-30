"""Registry of the SQLite tables: who writes them, who reads them, what dies with them.

Why this file exists
--------------------
The database carries fourteen tables. Some are load-bearing, some are written
and never read, and at least one was created by ``0001_init`` for a design that
was never built. Nothing in the tree said which was which, so every audit
re-opened the question with a fresh ``grep`` — and a ``grep`` lies here:
``quotes`` matches ``app/market/quote.py``, ``instruments`` matches a UI string,
and the provider breaker writes through raw ``sqlite3`` rather than through the
ORM. The question "can this table be dropped?" therefore had no answer that
survived a code review.

This module is that answer, made static. It declares one :class:`TableUsage` per
table with the SQL evidence that justifies the status, and
:func:`scan_table_usage` re-derives the same relation from the tree with the
``ast`` module so a test can compare the declaration against reality. The
registry is a *registry*, not a cleanup: nothing here drops a table, and a
status describes today's code rather than licensing a deletion.

The four statuses
-----------------
``written``
    At least one ``INSERT``/``UPDATE``/``DELETE``/``REPLACE`` in ``app/`` names
    the table (in ``FROM``/``INTO``/``UPDATE``/``JOIN`` position, not inside a
    comment).
``read-only``
    Something reads it and nothing in ``app/`` writes it: the rows arrive from
    outside the application (Alembic's bookkeeping, a script, another process),
    so the SQL in ``app/`` is a ``SELECT`` at most — for a table with an
    ``external_owner``, none at all.
``dead``
    No SQL in ``app/`` reads or writes it, and nothing outside ``app/`` owns it.
    A ``dead`` table is a leftover: it costs a row in every ``sqlite_master``
    scan and a question in every audit.
``reserved``
    Declared by a migration and deliberately kept for a feature that has not
    landed. No code touches it *yet*; dropping it would be a schema break for a
    consumer that is not in this repository. None of the fourteen tables is
    ``reserved`` today — the vocabulary keeps the case visible when it appears.

A table can also be **write-only** (``instruments``): the vocabulary has no slot
for "writes but never reads" because that is still ``written``; the note carries
the asymmetry, and :func:`scan_table_access` exposes the two directions
separately for anyone who needs the finer cut.

What the scanner can and cannot see
-----------------------------------
The scanner parses every ``.py`` under ``app/`` and looks at **string
constants**, not at raw text: a table name inside a triple-quoted ``text(...)``
module constant (``app/resolve/seed.py``) is found, a table name inside a
docstring is not (unless the docstring is itself a SQL statement), and a name
inside a comment never is. Statements are split on ``;`` and attributed per
reference keyword, so ``UPDATE jobs SET ... WHERE id IN (SELECT id FROM jobs
...)`` counts ``jobs`` once as a writer and once as a reader.

It deliberately does **not** see:

* SQL assembled by concatenation or ``%`` — ``"SELECT * FROM " + name`` leaves a
  constant with no table reference in it;
* an f-string placeholder — ``f"DELETE FROM {table}"`` parses as the fragments
  ``"DELETE FROM "`` and ``" WHERE ..."`` and names nothing. The one place this
  happens in this tree (``app/jobs/repository.py``, the retention purge) is
  covered by the *literal* statements in the same module, so no table's status
  depends on the invisible purge;
* a table name held in a variable and passed to ``text()``;
* schema probes — ``SELECT name FROM sqlite_master`` and ``PRAGMA table_info(x)``
  ask whether a table exists, not what it holds, so neither counts as a read.
  ``app/screen/facts.py`` and ``app/fundamentals/snapshot.py`` both guard an
  optional table this way, which is why the guard is not the evidence;
* SQL written by Alembic (``alembic_version``) or by a script under
  ``scripts/``. Those tables carry ``external_owner``, and the registry cites
  the file and line by hand;
* a statement whose first token is a SQL comment (``-- purge`` then
  ``DELETE FROM``) — the literal is read as prose. No such literal exists here.

The registry's evidence is manual precisely where the scanner is blind, and the
tests enforce both halves: every evidence line must exist and must name its own
table, and every table the scanner does see must carry the status the scanner
derives.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Iterator, Literal, Mapping

#: The statuses a table may carry. See the module docstring for the definitions.
Status = Literal["written", "read-only", "dead", "reserved"]

#: The same four names as data, so a test can assert the vocabulary is closed.
STATUSES: frozenset[str] = frozenset({"written", "read-only", "dead", "reserved"})

#: Statements that begin with one of these are SQL; anything else is prose or a
#: fragment (a concatenated prefix, an identifier, a docstring).
_READ_VERBS: frozenset[str] = frozenset({"SELECT", "WITH"})
_WRITE_VERBS: frozenset[str] = frozenset({"INSERT", "UPDATE", "DELETE", "REPLACE"})

#: ``FROM``/``INTO``/``UPDATE``/``JOIN`` followed by a table name. The optional
#: ``OR <conflict-clause>`` covers ``UPDATE OR IGNORE quotes``; the optional
#: ``schema.`` prefix covers ``main.jobs``.
_REFERENCE = re.compile(
    r"\b(FROM|INTO|UPDATE|JOIN)\s+"
    r"(?:OR\s+(?:ROLLBACK|ABORT|REPLACE|FAIL|IGNORE)\s+)?"
    r"(?:\w+\s*\.\s*)?\"?([A-Za-z_][A-Za-z0-9_]*)\"?",
    re.IGNORECASE,
)

#: A statement's first word, after leading whitespace and parentheses.
_STATEMENT_START = re.compile(r"^\(*\s*([A-Za-z]+)")

#: Words that follow a reference keyword but are not table names: the DDL and
#: clause keywords a naive ``UPDATE\s+(\w+)`` would happily call a table
#: (``ON CONFLICT DO UPDATE SET`` is the one that bites in this tree).
_KEYWORDS: frozenset[str] = frozenset(
    {
        "ABORT",
        "ALL",
        "AS",
        "BY",
        "CASCADE",
        "DISTINCT",
        "DO",
        "EXISTS",
        "FAIL",
        "FROM",
        "GROUP",
        "HAVING",
        "IF",
        "IGNORE",
        "INTO",
        "JOIN",
        "LIMIT",
        "NOT",
        "NULL",
        "ON",
        "ONLY",
        "OR",
        "ORDER",
        "REPLACE",
        "ROLLBACK",
        "SELECT",
        "SET",
        "TABLE",
        "UNION",
        "USING",
        "VALUES",
        "WHERE",
        "WITH",
    }
)

#: SQLite's own catalogues: asking them a question is a schema probe, not a read
#: of application data, so they never appear as tables in the scan.
_CATALOGUES: frozenset[str] = frozenset(
    {"sqlite_master", "sqlite_schema", "sqlite_temp_master"}
)


@dataclass(frozen=True, slots=True)
class Reference:
    """One table named by one SQL statement.

    ``keyword`` is the word that introduced the name (``FROM``, ``INTO``,
    ``UPDATE``, ``JOIN``) — kept because it is the whole justification for
    ``writes``, and a reviewer can check it against the statement.
    """

    table: str
    keyword: str
    writes: bool


@dataclass(frozen=True, slots=True)
class TableUsage:
    """What the application does with one table, and the lines that prove it."""

    name: str
    status: Status
    #: ``"path:line"`` entries relative to the repository root. Every line must
    #: exist and must name ``name`` itself; the tests enforce that, so a
    #: refactor that moves a statement cannot leave a dangling citation.
    evidence: tuple[str, ...]
    #: One sentence: what the table is for and, when it matters, why it is empty.
    note: str
    #: ``None`` when ``app/`` SQL proves the status. Otherwise the tool that owns
    #: the table and whose writes the scanner cannot see (``"alembic"``).
    external_owner: str | None = None


@dataclass(frozen=True, slots=True)
class TableAccess:
    """Which files touch one table, split by direction.

    A file may appear in both sets (one module usually holds the reader and the
    writer of the same table); the split is what makes "written but never read"
    visible without reading the SQL again.
    """

    readers: frozenset[str]
    writers: frozenset[str]

    @property
    def files(self) -> frozenset[str]:
        """Every file that mentions the table in SQL, in either direction."""

        return self.readers | self.writers


#: Reading order, grouped the way the document is: the tooling table, the job
#: pipeline (parents before children), identity, the breaker, then the screen and
#: fundamentals caches.
_REGISTRY: tuple[TableUsage, ...] = (
    TableUsage(
        name="alembic_version",
        status="read-only",
        external_owner="alembic",
        evidence=("scripts/fundamentals-snapshot.py:373",),
        note=(
            "Alembic's ledger of applied revisions: Alembic writes it during upgrade, the "
            "application never does, and scripts/fundamentals-snapshot.py reads it to refuse "
            "a snapshot against an old schema."
        ),
    ),
    TableUsage(
        name="jobs",
        status="written",
        evidence=(
            "app/jobs/repository.py:179",
            "app/jobs/repository.py:220",
            "app/jobs/repository.py:205",
        ),
        note=(
            "The request queue and its terminal state: POST /markets/enrich inserts a row, the "
            "worker claims and advances it, GET /requests/{id} reads it back; 0 rows only "
            "means this database has never served a request."
        ),
    ),
    TableUsage(
        name="job_batches",
        status="written",
        evidence=(
            "app/jobs/repository.py:713",
            "app/jobs/repository.py:753",
            "app/jobs/repository.py:782",
        ),
        note=(
            "Per-batch checkpoints so a retried job replays the batch it was in instead of "
            "restarting the whole universe."
        ),
    ),
    TableUsage(
        name="quotes",
        status="written",
        evidence=(
            "app/jobs/quotes.py:107",
            "app/jobs/quotes.py:145",
            "app/jobs/quotes.py:126",
        ),
        note=(
            "Single-use price quotes: the plan a client was sold is consumed once by POST "
            "/markets/enrich and linked to the job it paid for, which is what stops two "
            "jobs sharing one quote."
        ),
    ),
    TableUsage(
        name="job_artifacts",
        status="written",
        evidence=("app/jobs/repository.py:574", "app/jobs/repository.py:601"),
        note=(
            "The manifest of a finished job's export files (path, sha256, row count), keyed "
            "UNIQUE(job_id, kind) so a retry replaces a row instead of stacking one."
        ),
    ),
    TableUsage(
        name="verification_rejections",
        status="written",
        evidence=("app/jobs/repository.py:542", "app/jobs/repository.py:535"),
        note=(
            "Per-cell verifier rejections, deleted and re-inserted per job; written on every "
            "run and never read back by an endpoint today."
        ),
    ),
    TableUsage(
        name="observations",
        status="dead",
        evidence=("app/models.py:165",),
        note=(
            "Created by 0001_init for a durable observation ledger that was never built: its "
            "model is imported only by alembic/env.py for metadata, and the L0 cache that "
            "replaced it lives in process memory (app/providers/cache.py)."
        ),
    ),
    TableUsage(
        name="instruments",
        status="written",
        evidence=("app/resolve/seed.py:48",),
        note=(
            "The reference master copy (one row per MIC:symbol), upserted only when POST "
            "/markets/resolve seeds it; nothing in app/ SELECTs from it, so it is write-only "
            "and 0 rows means the seeder has never run here."
        ),
    ),
    TableUsage(
        name="instrument_aliases",
        status="written",
        evidence=("app/resolve/seed.py:72",),
        note=(
            "Normalised ticker and name aliases written by the same seeder for lookups that "
            "app/resolve/aliases.py still performs on the in-memory reference, which is why "
            "the table is write-only."
        ),
    ),
    TableUsage(
        name="provider_health",
        status="written",
        evidence=("app/providers/health.py:56", "app/providers/health.py:142"),
        note=(
            "Circuit-breaker state (one row per provider and capability) that must outlive the "
            "process: written through raw sqlite3 on every verdict and read back by "
            "GET /providers."
        ),
    ),
    TableUsage(
        name="screens",
        status="written",
        evidence=(
            "app/screen/repository.py:196",
            "app/screen/repository.py:265",
            "app/api/health.py:627",
        ),
        note=(
            "One row per verified screen snapshot, read by GET /screens, by the export bundle "
            "and by the health probe's row count."
        ),
    ),
    TableUsage(
        name="screen_rows",
        status="written",
        evidence=(
            "app/screen/repository.py:211",
            "app/screen/repository.py:271",
            "app/screen/repository.py:294",
        ),
        note=(
            "The ranked rows of a snapshot, written with their parent and removed by ON DELETE "
            "CASCADE when the snapshot is pruned."
        ),
    ),
    TableUsage(
        name="screen_facts",
        status="written",
        evidence=(
            "app/screen/facts.py:448",
            "app/screen/facts.py:491",
            "app/screen/facts.py:474",
        ),
        note=(
            "Finviz-only cells (short float, insider and institutional ownership) keyed by "
            "symbol with a TTL, filled by the facts collector and read once per selection."
        ),
    ),
    TableUsage(
        name="fundamentals_snapshots",
        status="written",
        evidence=(
            "app/fundamentals/snapshot.py:890",
            "app/fundamentals/snapshot.py:935",
            "app/fundamentals/snapshot.py:919",
        ),
        note=(
            "One full-width fundamentals snapshot per symbol with a TTL, and the only source "
            "of pe_forward, ps and payout_ratio for a selection."
        ),
    ),
)

#: The registry. Keyed by table name; every table the live schema has, and only
#: those (``tests/test_schema_registry.py`` diffs it against ``alembic upgrade
#: head`` so a migration without an entry fails). The mapping is read-only: a
#: route or a test cannot rewrite the audit trail at runtime.
TABLES: Mapping[str, TableUsage] = MappingProxyType({usage.name: usage for usage in _REGISTRY})


def _statements(sql: str) -> Iterator[str]:
    """Split one literal into statements; ``;`` inside a quoted value is rare enough to ignore."""

    for chunk in sql.split(";"):
        statement = chunk.strip()
        if statement:
            yield statement


def sql_references(sql: str) -> tuple[Reference, ...]:
    """Every table this scanner can see in one SQL string, in source order.

    A string that does not *start* with a SQL verb is not a statement: that is
    what keeps docstrings, log messages and concatenated prefixes out of the
    result. Within a statement, the keyword decides the direction — ``INTO`` and
    ``UPDATE`` write, ``FROM`` and ``JOIN`` read, except that ``DELETE FROM`` is
    the write it obviously is.
    """

    references: list[Reference] = []
    for statement in _statements(sql):
        start = _STATEMENT_START.match(statement)
        if start is None:
            continue
        verb = start.group(1).upper()
        if verb not in _READ_VERBS and verb not in _WRITE_VERBS:
            continue

        for match in _REFERENCE.finditer(statement):
            keyword = match.group(1).upper()
            table = match.group(2)
            if table.lower() in _CATALOGUES or table.upper() in _KEYWORDS:
                continue
            writes = keyword in {"INTO", "UPDATE"} or (verb == "DELETE" and keyword == "FROM")
            references.append(Reference(table=table, keyword=keyword, writes=writes))

    return tuple(references)


def _sources(app_root: Path) -> Iterator[Path]:
    """Every Python file under ``app/``, in a stable order (pycache excluded)."""

    for path in sorted(app_root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        yield path


def _relative(path: Path, root: Path) -> str:
    """A POSIX path relative to ``root`` when possible, so citations stay portable."""

    try:
        return path.relative_to(root).as_posix()
    except ValueError:  # root is a symlink or an unrelated directory
        return path.as_posix()


def scan_table_access(root: Path) -> dict[str, TableAccess]:
    """Map each table named by ``app/`` SQL to the files that read and write it.

    ``root`` is the repository root (``app/`` is looked for beneath it) or the
    ``app/`` directory itself. A file that will not parse is a broken tree, not
    a table to skip, so ``SyntaxError`` propagates: a scanner that silently
    skipped a module would report a live table as dead.
    """

    app_root = root / "app" if (root / "app").is_dir() else root
    readers: dict[str, set[str]] = {}
    writers: dict[str, set[str]] = {}

    for path in _sources(app_root):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        origin = _relative(path, root)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                continue
            for reference in sql_references(node.value):
                bucket = writers if reference.writes else readers
                bucket.setdefault(reference.table, set()).add(origin)

    return {
        table: TableAccess(
            readers=frozenset(readers.get(table, ())),
            writers=frozenset(writers.get(table, ())),
        )
        for table in sorted(set(readers) | set(writers))
    }


def scan_table_usage(root: Path) -> dict[str, set[str]]:
    """Map each table named by ``app/`` SQL to every file that touches it.

    The union of both directions: the contracted shape for the drift test, which
    only needs to know whether anyone mentions a table at all.
    """

    return {table: set(access.files) for table, access in scan_table_access(root).items()}


__all__ = [
    "STATUSES",
    "TABLES",
    "Reference",
    "Status",
    "TableAccess",
    "TableUsage",
    "scan_table_access",
    "scan_table_usage",
    "sql_references",
]
